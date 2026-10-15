"""Exercise the public CLI with the pinned, real native KCL backend."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = '@settings(kclVersion = "3.0")\n'
MODES = {
    "format": "local_format",
    "lint": "local_lint",
    "execute": "local_mock_execution",
    "sketches": "local_sketch_constraints",
}


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("ZOO_API_TOKEN", None)
    environment.pop("KITTYCAD_API_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "zoo_kcl_check_cli", *map(str, args)],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=25,
        check=False,
    )


def run_json(command: str, path: Path, expected: int = 0, *args: str) -> dict:
    process = run_cli(command, str(path), "--json", *args)
    assert process.returncode == expected, (process.stdout, process.stderr)
    result = json.loads(process.stdout)
    assert isinstance(result, dict)
    assert result["schema_version"] == 1
    assert result["command"] == command
    assert result["mode"] == MODES[command]
    assert result["ok"] is (expected == 0)
    assert result["exit_code"] == expected
    assert result["geometry_validated"] is False
    assert result["backend"] == {"name": "zoo-kcl", "version": "0.3.191"}
    assert all(Path(target).is_absolute() for target in result["targets"])
    for diagnostic in result["diagnostics"]:
        assert diagnostic["severity"] in {"info", "warning", "error", "fatal"}
        assert diagnostic["origin"] in {"lint", "execution", "solver", "input", "tool"}
        assert diagnostic["message"]
        assert {"code", "file", "range", "rendered"}.issubset(diagnostic)
    return result


def write_source(path: Path, source: str = SETTINGS + "x = 1mm + 2mm\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fixture_sources_are_read_only():
    before = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in FIXTURES.rglob("*.kcl")
    }
    yield
    assert {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) for path in before
    } == before


def test_execute_valid_program():
    result = run_json("execute", FIXTURES / "valid.kcl")
    assert result["diagnostics"] == []
    assert result["sketches"] is None
    assert result["formatting"] is None


def test_execute_syntax_error():
    result = run_json("execute", FIXTURES / "syntax_error.kcl", 1)
    assert any(d["severity"] in {"error", "fatal"} for d in result["diagnostics"])


def test_execute_unknown_name():
    result = run_json("execute", FIXTURES / "unknown_name.kcl", 1)
    assert "noSuchFunction" in str(result["diagnostics"])


def test_execute_argument_type():
    result = run_json("execute", FIXTURES / "argument_type.kcl", 1)
    assert "Point2d" in str(result["diagnostics"])


def test_execute_assert_failure():
    result = run_json("execute", FIXTURES / "assert_failure.kcl", 1)
    assert "assert" in str(result["diagnostics"]).lower()


def test_execute_units_warning():
    result = run_json("execute", FIXTURES / "units_warning.kcl", 1)
    warnings = [d for d in result["diagnostics"] if d["severity"] == "warning"]
    assert warnings
    assert "units" in warnings[0]["message"].lower()
    assert warnings[0]["rendered"]


def test_execute_imports():
    result = run_json("execute", FIXTURES / "imports_valid")
    assert result["targets"] == [str((FIXTURES / "imports_valid/main.kcl").resolve())]
    assert result["diagnostics"] == []


def test_execute_import_error():
    result = run_json("execute", FIXTURES / "imports_error", 1)
    assert "broken.kcl" in str(result["diagnostics"])
    assert "noSuchFunction" in str(result["diagnostics"])
    entrypoint = str((FIXTURES / "imports_error/main.kcl").resolve())
    # An execution error's precise location must not be fabricated from the entrypoint.
    assert all(d["file"] != entrypoint for d in result["diagnostics"])
    assert any(d["rendered"] for d in result["diagnostics"])


def test_execute_cad_operations():
    result = run_json("execute", FIXTURES / "cad_operations.kcl")
    assert result["geometry_validated"] is False
    assert result["sketches"] is None


def test_sketch_fully_constrained():
    result = run_json("sketches", FIXTURES / "fully_constrained.kcl")
    report = result["sketches"]
    assert report["complete"] is True
    assert report["total"] == 1
    assert len(report["entries"]) == 1
    entry = report["entries"][0]
    assert entry["status"] == "fully_constrained"
    assert entry["free_count"] == 0
    assert entry["conflict_count"] == 0
    assert entry["total_count"] > 0
    assert result["diagnostics"] == []


def test_sketch_under_constrained():
    result = run_json("sketches", FIXTURES / "under_constrained.kcl", 1)
    report = result["sketches"]
    assert report["complete"] is True
    assert report["total"] == 1
    assert report["entries"][0]["status"] == "under_constrained"
    assert report["entries"][0]["free_count"] > 0


def test_sketch_over_constrained():
    result = run_json("sketches", FIXTURES / "over_constrained.kcl", 1)
    report = result["sketches"]
    assert report["total"] == 1
    assert report["entries"][0]["status"] == "over_constrained"
    assert report["entries"][0]["conflict_count"] > 0
    assert any(d["severity"] == "warning" for d in result["diagnostics"])


def test_sketch_duplicate_names():
    result = run_json("sketches", FIXTURES / "duplicate_names.kcl")
    entries = result["sketches"]["entries"]
    assert result["sketches"]["total"] == 2
    assert len(entries) == 2
    assert entries[0]["name"] == entries[1]["name"]
    assert {entry["instance_index"] for entry in entries} == {0, 1}


def test_sketch_no_sketches():
    result = run_json("sketches", FIXTURES / "no_sketches.kcl")
    assert result["sketches"] == {"complete": True, "total": 0, "entries": []}


def test_sketch_partial_report_preserved():
    result = run_json("sketches", FIXTURES / "partial_sketch_report.kcl", 1)
    assert result["sketches"]["complete"] is False
    assert result["sketches"]["total"] == 1
    assert result["sketches"]["entries"][0]["status"] == "fully_constrained"
    assert "noSuchFunction" in str(result["diagnostics"])


def test_execution_warning_before_error_is_preserved():
    result = run_json("execute", FIXTURES / "warning_then_error.kcl", 1)
    assert any(d["severity"] == "warning" for d in result["diagnostics"])
    assert any(d["severity"] in {"error", "fatal"} for d in result["diagnostics"])


def test_lint_syntax_error_is_source_error():
    result = run_json("lint", FIXTURES / "syntax_error.kcl", 1)
    diagnostic = result["diagnostics"][0]
    assert diagnostic["severity"] == "error"
    assert diagnostic["origin"] == "lint"
    assert "Syntax error" in diagnostic["rendered"]
    assert "x = [" in diagnostic["rendered"]


def test_lint_does_not_execute_unknown_function():
    result = run_json("lint", FIXTURES / "unknown_name.kcl")
    assert result["diagnostics"] == []


def test_lint_unicode_byte_offsets(tmp_path):
    path = write_source(
        tmp_path / "папка с пробелами" / "эскиз.kcl",
        SETTINGS + "// Привет ☃\nBad_имя = 1mm\n",
    )
    result = run_json("lint", path, 1)
    assert len(result["diagnostics"]) == 1
    diagnostic = result["diagnostics"][0]
    assert diagnostic["origin"] == "lint"
    assert diagnostic["code"] == "Z0001"
    assert diagnostic["file"] == str(path.resolve())
    assert diagnostic["range"] == {
        "start": {"line": 3, "column": 1},
        "end": {"line": 3, "column": 8},
    }


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_relative_unicode_path_from_another_working_directory(command, tmp_path):
    source = write_source(tmp_path / "папка с пробелами" / "эскиз.kcl")
    before = source.read_bytes()
    process = run_cli(command, str(source.relative_to(tmp_path)), "--json", cwd=tmp_path)
    assert process.returncode == 0, (process.stdout, process.stderr)
    result = json.loads(process.stdout)
    assert result["targets"] == [str(source.resolve())]
    assert result["geometry_validated"] is False
    assert source.read_bytes() == before


def test_lint_collects_all_files_after_diagnostics(tmp_path):
    first = write_source(tmp_path / "a.kcl", SETTINGS + "Bad_name = 1mm\n")
    second = write_source(tmp_path / "nested" / "b.kcl", SETTINGS + "Also_bad = 2mm\n")
    result = run_json("lint", tmp_path, 1)
    assert result["targets"] == [str(first.resolve()), str(second.resolve())]
    assert {d["file"] for d in result["diagnostics"]} == {
        str(first.resolve()),
        str(second.resolve()),
    }


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_command_help(command):
    result = run_cli(command, "--help")
    assert result.returncode == 0
    assert "--json" in result.stdout
    assert "--timeout" in result.stdout
    assert "60" in result.stdout
    if command == "execute":
        assert "mock" in result.stdout.lower()
    if command == "sketches":
        assert "under" in result.stdout.lower()


def test_top_level_help_and_version():
    for arguments in [(), ("--help",)]:
        result = run_cli(*arguments)
        assert result.returncode == 0
        assert all(command in result.stdout for command in MODES)
        assert "geometry" in result.stdout.lower()
    result = run_cli("--version")
    assert result.returncode == 0
    assert "0.3.191" in result.stdout
    assert "python" in result.stdout.lower()


def test_installed_console_entrypoint_matches_python_module():
    entrypoint = Path(sys.executable).with_name("kcl-check")
    if not entrypoint.is_file():
        pytest.skip("console entrypoint requires an installed package")
    process = subprocess.run(
        [str(entrypoint), "execute", str(FIXTURES / "valid.kcl"), "--json"],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == run_json("execute", FIXTURES / "valid.kcl")


@pytest.mark.parametrize(
    "arguments, command",
    [
        (("does-not-exist", "--json"), None),
        (("--json",), None),
        (("execute", "--json"), "execute"),
        (("lint", "missing.kcl", "--unknown", "--json"), "lint"),
        (("format", "missing.kcl", "--write", "--check", "--json"), "format"),
    ],
)
def test_argument_errors_return_single_json(arguments, command):
    process = run_cli(*arguments)
    assert process.returncode == 2
    result = json.loads(process.stdout)
    assert result["command"] == command
    assert result["mode"] == (MODES[command] if command else None)
    assert result["exit_code"] == 2
    assert result["ok"] is False
    assert result["geometry_validated"] is False
    assert result["diagnostics"]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_invalid_timeout_is_input_error(value):
    run_json("execute", FIXTURES / "valid.kcl", 2, "--timeout", value)


def test_tiny_command_timeout_is_json_error():
    result = run_json("execute", FIXTURES / "valid.kcl", 2, "--timeout", "0.000001")
    assert (
        "timeout" in str(result["diagnostics"]).lower()
        or "timed out" in str(result["diagnostics"]).lower()
    )


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_missing_file_is_input_error(command, tmp_path):
    run_json(command, tmp_path / "missing.kcl", 2)


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_bad_extension_is_input_error(command, tmp_path):
    path = write_source(tmp_path / "source.txt")
    run_json(command, path, 2)


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_invalid_utf8_is_input_error(command, tmp_path):
    path = tmp_path / "broken.kcl"
    path.write_bytes(b"\xff\xfe")
    run_json(command, path, 2)
    assert path.read_bytes() == b"\xff\xfe"


@pytest.mark.parametrize("command", ["format", "lint"])
def test_empty_directory_is_input_error(command, tmp_path):
    run_json(command, tmp_path, 2)


@pytest.mark.parametrize("command", ["execute", "sketches"])
def test_project_default_file(command, tmp_path):
    entrypoint = write_source(tmp_path / "models" / "assembly.kcl")
    write_source(tmp_path / "main.kcl", SETTINGS + "x = noSuchFunction(1_)\n")
    (tmp_path / "project.toml").write_text(
        'default_file = "models/assembly.kcl"\n', encoding="utf-8"
    )
    result = run_json(command, tmp_path)
    assert result["targets"] == [str(entrypoint.resolve())]


@pytest.mark.parametrize(
    "configuration",
    ['default_file = "missing.kcl"\n', "default_file = 42\n", "[broken\n"],
)
@pytest.mark.parametrize("command", ["execute", "sketches"])
def test_invalid_project_config_does_not_fall_back(command, configuration, tmp_path):
    write_source(tmp_path / "main.kcl")
    (tmp_path / "project.toml").write_text(configuration, encoding="utf-8")
    run_json(command, tmp_path, 2)


@pytest.mark.parametrize("command", ["execute", "sketches"])
def test_explicit_file_ignores_project_entrypoint(command, tmp_path):
    entrypoint = write_source(tmp_path / "main.kcl")
    (tmp_path / "project.toml").write_text('default_file = "absent.kcl"\n', encoding="utf-8")
    run_json(command, entrypoint)


@pytest.mark.parametrize("command", ["format", "lint"])
def test_recursive_traversal_excludes_generated_directories(command, tmp_path):
    first = write_source(tmp_path / "a.kcl")
    second = write_source(tmp_path / "nested" / "b.kcl")
    excluded = [
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
    ]
    for directory in excluded:
        write_source(tmp_path / directory / "broken.kcl", SETTINGS + "x = [\n")
    result = run_json(command, tmp_path)
    assert result["targets"] == [str(first.resolve()), str(second.resolve())]


def make_symlink(path: Path, target: Path, *, directory: bool = False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"OS cannot create test symlink: {error}")


@pytest.mark.parametrize("command", ["format", "lint"])
def test_recursive_traversal_skips_all_symlinks(command, tmp_path):
    project = tmp_path / "project"
    actual = write_source(project / "actual.kcl")
    external = write_source(tmp_path / "external" / "broken.kcl", SETTINGS + "x = [\n")
    make_symlink(project / "linked.kcl", external)
    make_symlink(project / "linked-dir", external.parent, directory=True)
    result = run_json(command, project)
    assert result["targets"] == [str(actual.resolve())]


@pytest.mark.parametrize("command", ["format", "lint", "execute", "sketches"])
def test_explicit_symlink_is_unsupported_input(command, tmp_path):
    target = write_source(tmp_path / "actual.kcl")
    link = tmp_path / "linked.kcl"
    make_symlink(link, target)
    run_json(command, link, 2)
    assert link.is_symlink()


def test_format_without_write_returns_code_and_preserves_source(tmp_path):
    path = write_source(tmp_path / "input.kcl", '@settings(kclVersion="3.0")\nx=1mm+2mm\n')
    before = path.read_bytes()
    result = run_json("format", path)
    formatting = result["formatting"]
    assert len(formatting) == 1
    assert formatting[0]["file"] == str(path.resolve())
    assert formatting[0]["changed"] is True
    assert formatting[0]["written"] is False
    assert "x = 1mm + 2mm" in formatting[0]["formatted_code"]
    assert path.read_bytes() == before
    text = run_cli("format", str(path))
    assert text.returncode == 0
    assert text.stdout == formatting[0]["formatted_code"]
    assert path.read_bytes() == before


def test_format_check_write_and_idempotence(tmp_path):
    path = write_source(tmp_path / "input.kcl", '@settings(kclVersion="3.0")\nx=1mm+2mm\n')
    path.chmod(0o640)
    before = path.read_bytes()
    check = run_json("format", path, 1, "--check")
    assert check["formatting"][0]["changed"] is True
    assert check["formatting"][0]["formatted_code"] is None
    assert path.read_bytes() == before
    written = run_json("format", path, 0, "--write")
    assert written["formatting"][0]["written"] is True
    assert written["formatting"][0]["formatted_code"] is None
    assert path.stat().st_mode & 0o777 == 0o640
    assert path.read_bytes() != before
    mtime = path.stat().st_mtime_ns
    check = run_json("format", path, 0, "--check")
    assert check["formatting"][0]["changed"] is False
    repeated = run_json("format", path, 0, "--write")
    assert repeated["formatting"][0]["written"] is False
    assert path.stat().st_mtime_ns == mtime


def test_format_directory_text_requires_check_or_write(tmp_path):
    write_source(tmp_path / "main.kcl")
    process = run_cli("format", str(tmp_path))
    assert process.returncode == 2
    run_json("format", tmp_path)


def test_format_parse_failure_prevents_all_writes(tmp_path):
    first = write_source(tmp_path / "a.kcl", '@settings(kclVersion="3.0")\nx=1mm+2mm\n')
    broken = write_source(tmp_path / "z.kcl", SETTINGS + "x = [\n")
    before = {path: path.read_bytes() for path in (first, broken)}
    result = run_json("format", tmp_path, 1, "--write")
    assert result["diagnostics"]
    assert all(not entry["written"] for entry in result["formatting"])
    assert {path: path.read_bytes() for path in before} == before
