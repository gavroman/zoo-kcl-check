"""The frozen executable must carry its Python and native backend runtimes."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def binary():
    path = ROOT / "dist" / ("kcl-check.exe" if sys.platform == "win32" else "kcl-check")
    if not path.is_file():
        pytest.skip("build the executable with packaging/kcl-check.spec first")
    return path


@pytest.fixture
def isolated_binary_environment(tmp_path):
    fixture_copy = tmp_path / "проект с пробелами"
    shutil.copytree(FIXTURES, fixture_copy)
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ZOO_API_TOKEN", "KITTYCAD_API_TOKEN"):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONSAFEPATH"] = "1"
    if sys.platform != "win32":
        environment["PATH"] = "/usr/bin:/bin"
    before = {path: path.read_bytes() for path in fixture_copy.rglob("*.kcl")}
    yield tmp_path, fixture_copy, environment
    assert {path: path.read_bytes() for path in before} == before


def run_binary(binary, isolated_binary_environment, *arguments):
    outside_repository, _, environment = isolated_binary_environment
    return subprocess.run(
        [str(binary), *map(str, arguments)],
        cwd=outside_repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )


def test_binary_help_and_version(binary, isolated_binary_environment):
    for arguments in [(), ("--help",), ("--version",)]:
        process = run_binary(binary, isolated_binary_environment, *arguments)
        assert process.returncode == 0, (process.stdout, process.stderr)
        assert process.stdout
    version = run_binary(binary, isolated_binary_environment, "--version")
    assert "0.3.191" in version.stdout
    assert "python" in version.stdout.lower()


@pytest.mark.parametrize(
    "command, fixture, exit_code",
    [
        ("format", "valid.kcl", 0),
        ("format", "syntax_error.kcl", 1),
        ("lint", "valid.kcl", 0),
        ("lint", "syntax_error.kcl", 1),
        ("execute", "valid.kcl", 0),
        ("execute", "unknown_name.kcl", 1),
        ("execute", "units_warning.kcl", 1),
        ("execute", "imports_valid", 0),
        ("execute", "imports_error", 1),
        ("execute", "cad_operations.kcl", 0),
        ("sketches", "fully_constrained.kcl", 0),
        ("sketches", "under_constrained.kcl", 1),
        ("sketches", "over_constrained.kcl", 1),
        ("sketches", "duplicate_names.kcl", 0),
        ("sketches", "no_sketches.kcl", 0),
        ("sketches", "partial_sketch_report.kcl", 1),
    ],
)
def test_binary_commands_with_isolated_python_environment(
    binary, isolated_binary_environment, command, fixture, exit_code
):
    _, copied_fixtures, _ = isolated_binary_environment
    process = run_binary(
        binary, isolated_binary_environment, command, copied_fixtures / fixture, "--json"
    )
    assert process.returncode == exit_code, (process.stdout, process.stderr)
    result = json.loads(process.stdout)
    assert result["command"] == command
    assert result["exit_code"] == exit_code
    assert result["ok"] is (exit_code == 0)
    assert result["geometry_validated"] is False
    assert result["backend"] == {"name": "zoo-kcl", "version": "0.3.191"}


def test_binary_unknown_command_json(binary, isolated_binary_environment):
    process = run_binary(binary, isolated_binary_environment, "unknown", "--json")
    assert process.returncode == 2
    result = json.loads(process.stdout)
    assert result["command"] is None
    assert result["mode"] is None
    assert result["ok"] is False
    assert result["exit_code"] == 2


def test_binary_with_os_network_forbidden(binary, isolated_binary_environment):
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        pytest.skip("binary offline acceptance requires macOS sandbox-exec")
    outside_repository, copied_fixtures, environment = isolated_binary_environment
    profile = "(version 1)(allow default)(deny network*)"
    # Test the actual OS policy, then run the frozen worker under that same policy.
    probe = subprocess.run(
        [
            str(sandbox),
            "-p",
            profile,
            sys.executable,
            "-c",
            "import socket\n"
            "try:\n"
            "    socket.socket().bind(('127.0.0.1', 0))\n"
            "except PermissionError:\n"
            "    print('NETWORK_BLOCKED')\n"
            "else:\n"
            "    raise SystemExit('sandbox did not forbid network sockets')\n",
        ],
        cwd=outside_repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if "sandbox_apply: Operation not permitted" in probe.stderr:
        pytest.skip(
            "outer execution sandbox forbids macOS sandbox_apply; "
            "rerun binary offline acceptance outside that outer sandbox"
        )
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    assert probe.stdout.strip() == "NETWORK_BLOCKED", (probe.stdout, probe.stderr)
    for command, fixture, expected in [
        ("format", "valid.kcl", 0),
        ("format", "syntax_error.kcl", 1),
        ("lint", "valid.kcl", 0),
        ("lint", "syntax_error.kcl", 1),
        ("execute", "imports_valid", 0),
        ("execute", "imports_error", 1),
        ("sketches", "fully_constrained.kcl", 0),
        ("sketches", "under_constrained.kcl", 1),
    ]:
        process = subprocess.run(
            [
                str(sandbox),
                "-p",
                profile,
                str(binary),
                command,
                str(copied_fixtures / fixture),
                "--json",
            ],
            cwd=outside_repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        assert process.returncode == expected, (process.stdout, process.stderr)
        result = json.loads(process.stdout)
        assert result["exit_code"] == expected
        assert result["geometry_validated"] is False
