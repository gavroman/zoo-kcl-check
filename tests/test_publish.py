"""Publishing must stop on failures and upload only the newly built artifacts."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publish", Path(__file__).resolve().parents[1] / "scripts/publish.py"
)
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


@pytest.fixture
def release(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "zoo-kcl-check-cli"\nversion = "0.1.2"\n'
    )
    module = tmp_path / "src/zoo_kcl_check_cli/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text('__version__ = "0.1.2"\n')
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/bin/uv")
    return tmp_path


def test_version_mismatch_stops_before_running_commands(release, monkeypatch):
    (release / "src/zoo_kcl_check_cli/__init__.py").write_text('__version__ = "0.1.1"\n')
    calls = []
    monkeypatch.setattr(publish, "run", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="must match"):
        publish.main(["--bump", "patch"])
    assert calls == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_only_fresh_artifacts_can_be_uploaded(release, monkeypatch, dry_run):
    dist = release / "dist"
    dist.mkdir()
    (dist / "old.whl").write_text("old artifact")
    calls = []
    originals = {
        path: path.read_bytes()
        for path in [release / "pyproject.toml", release / "src/zoo_kcl_check_cli/__init__.py"]
    }

    def run(*args):
        calls.append(args)
        assert publish.release_info(release) == ("zoo-kcl-check-cli", "0.1.3")
        if "build" in args:
            output = Path(args[-1])
            (output / "zoo_kcl_check_cli-0.1.3-py3-none-any.whl").touch()
            (output / "zoo_kcl_check_cli-0.1.3.tar.gz").touch()

    monkeypatch.setattr(publish, "run", run)
    assert publish.main(["--bump", "patch", *(["--dry-run"] if dry_run else [])]) == 0
    uploads = [args for args in calls if args[:2] == ("uv", "publish")]
    if dry_run:
        assert uploads == []
        assert all(path.read_bytes() == content for path, content in originals.items())
    else:
        assert len(uploads) == 1
        artifacts = [Path(path) for path in uploads[0][2:]]
        assert len(artifacts) == 2
        assert all(path.is_file() and path.parent.parent == dist for path in artifacts)
        assert all(path.name.startswith("zoo_kcl_check_cli-0.1.3") for path in artifacts)


def test_failed_check_prevents_build_and_upload(release, monkeypatch):
    calls = []

    def fail(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(publish, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        publish.main(["--bump", "patch"])
    assert len(calls) == 1
    assert not (release / "dist").exists()


@pytest.mark.parametrize("arguments", [[], ["--dry-run"], ["--bump", "invalid"]])
def test_bump_is_required_before_any_changes(release, arguments):
    with pytest.raises(SystemExit) as error:
        publish.main(arguments)
    assert error.value.code == 2
    assert publish.release_info(release) == ("zoo-kcl-check-cli", "0.1.2")


@pytest.mark.parametrize(
    "part, expected", [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]
)
def test_version_bump_resets_lower_components(part, expected):
    assert publish.bumped_version("1.2.3", part) == expected


def test_dry_run_failure_restores_version_files(release, monkeypatch):
    originals = {
        path: path.read_bytes()
        for path in [release / "pyproject.toml", release / "src/zoo_kcl_check_cli/__init__.py"]
    }

    def fail(*args):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(publish, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        publish.main(["--bump", "minor", "--dry-run"])
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_version_update_preserves_unrelated_content(release):
    project = release / "pyproject.toml"
    project.write_text(project.read_text() + '\n[tool.example]\nversion = "0.1.2"\n')
    publish.update_versions(release, "0.1.2", "0.2.0")
    assert publish.release_info(release) == ("zoo-kcl-check-cli", "0.2.0")
    assert '[tool.example]\nversion = "0.1.2"' in project.read_text()
