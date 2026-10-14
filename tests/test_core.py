"""Filesystem guarantees independent of native backend behavior."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path

import pytest

from zoo_kcl_check_cli import core
from zoo_kcl_check_cli.models import Diagnostic, Request


class Backend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def format(self, code: str, file: str) -> tuple[str | None, list[Diagnostic]]:
        self.calls.append(("format", file))
        if code == "broken":
            return None, [Diagnostic("error", "execution", "Syntax error", file=file)]
        return code.replace("old", "new"), []

    def lint(self, code: str, file: str) -> list[Diagnostic]:
        self.calls.append(("lint", file))
        if code == "bad":
            return [Diagnostic("warning", "lint", "Style finding", file=file)]
        return []

    def execute(self, path: str, include_sketches: bool = False) -> tuple[list, dict | None]:
        self.calls.append(("execute", path, include_sketches))
        return [], {"complete": True, "total": 0, "entries": []} if include_sketches else None


def write(path: Path, content: str = "old") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def request(command: str, path: Path, **kwargs: bool) -> Request:
    return Request(command=command, path=str(path), **kwargs)


def test_recursive_targets_stable_exclusions_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "project"
    first = write(root / "a.kcl", "valid")
    last = write(root / "папка с пробелами" / "я.kcl", "valid")
    write(root / "not-kcl.txt", "bad")
    for excluded in core.EXCLUDED_DIRECTORIES:
        write(root / excluded / "excluded.kcl", "bad")
    outside = write(tmp_path / "outside" / "outside.kcl", "bad")
    (root / "linked-file.kcl").symlink_to(outside)
    (root / "linked-folder").symlink_to(outside.parent, target_is_directory=True)
    (root / "broken-link.kcl").symlink_to(tmp_path / "missing.kcl")
    backend = Backend()

    result = core.run_request(request("lint", root), backend=backend)

    assert result.exit_code == 0
    assert result.targets == [str(first), str(last)]
    assert backend.calls == [("lint", str(first)), ("lint", str(last))]


@pytest.mark.parametrize("kind", ["file", "directory", "broken"])
def test_explicit_symlinks_are_input_errors(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "target"
    if kind == "file":
        write(target)
    elif kind == "directory":
        target.mkdir()
    link = tmp_path / "linked.kcl"
    link.symlink_to(target, target_is_directory=kind == "directory")
    backend = Backend()

    result = core.run_request(request("lint", link), backend=backend)

    assert result.exit_code == 2
    assert result.diagnostics[0].origin == "input"
    assert not backend.calls


def test_symlink_ancestor_is_allowed(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    source = write(actual / "main.kcl", "valid")
    linked = tmp_path / "parent-link"
    linked.symlink_to(actual, target_is_directory=True)

    result = core.run_request(request("lint", linked / source.name), backend=Backend())

    assert result.exit_code == 0
    assert result.targets == [str(linked / source.name)]


@pytest.mark.parametrize("kind", ["missing", "extension", "empty-directory"])
def test_invalid_inputs(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "missing.kcl"
    if kind == "extension":
        path = write(tmp_path / "file.txt")
    elif kind == "empty-directory":
        path = tmp_path
    backend = Backend()

    result = core.run_request(request("lint", path), backend=backend)

    assert result.exit_code == 2
    assert not backend.calls


def test_lint_collects_findings_and_read_failures(tmp_path: Path) -> None:
    invalid = tmp_path / "a.kcl"
    invalid.write_bytes(b"\xff")
    bad = write(tmp_path / "b.kcl", "bad")
    valid = write(tmp_path / "c.kcl", "valid")
    backend = Backend()

    result = core.run_request(request("lint", tmp_path), backend=backend)

    assert result.exit_code == 2
    assert [diagnostic.origin for diagnostic in result.diagnostics] == ["input", "lint"]
    assert backend.calls == [("lint", str(bad)), ("lint", str(valid))]
    assert invalid.read_bytes() == b"\xff"
    assert bad.read_text() == "bad"


def test_plain_directory_format_requires_mode(tmp_path: Path) -> None:
    write(tmp_path / "main.kcl")
    backend = Backend()

    result = core.run_request(request("format", tmp_path), backend=backend)

    assert result.exit_code == 2
    assert not backend.calls


def test_plain_and_json_format_return_code_without_writing(tmp_path: Path) -> None:
    source = write(tmp_path / "main.kcl")
    original_stat = source.stat()

    plain = core.run_request(request("format", source), backend=Backend())
    directory = core.run_request(request("format", tmp_path, json=True), backend=Backend())

    for result in (plain, directory):
        assert result.exit_code == 0
        assert result.formatting == [
            {"file": str(source), "changed": True, "written": False, "formatted_code": "new"}
        ]
    assert source.read_text() == "old"
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_format_check_reports_changes_without_writing(tmp_path: Path) -> None:
    changed = write(tmp_path / "a.kcl")
    unchanged = write(tmp_path / "b.kcl", "new")

    result = core.run_request(request("format", tmp_path, check=True), backend=Backend())

    assert result.exit_code == 1
    assert [record["changed"] for record in result.formatting] == [True, False]
    assert all(record["formatted_code"] is None for record in result.formatting)
    assert changed.read_text() == "old"
    assert unchanged.read_text() == "new"


@pytest.mark.parametrize("failure", ["parse", "read"])
def test_prepare_failure_prevents_entire_write_batch(tmp_path: Path, failure: str) -> None:
    first = write(tmp_path / "a.kcl")
    bad = write(tmp_path / "b.kcl", "broken")
    if failure == "read":
        bad.write_bytes(b"\xff")
    last = write(tmp_path / "c.kcl")
    backend = Backend()

    result = core.run_request(request("format", tmp_path, write=True), backend=backend)

    assert result.exit_code == (1 if failure == "parse" else 2)
    assert first.read_text() == "old"
    assert last.read_text() == "old"
    assert not any(record["written"] for record in result.formatting)
    assert backend.calls[-1] == ("format", str(last))


def test_atomic_write_preserves_permissions_and_unchanged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = write(tmp_path / "changed.kcl")
    unchanged = write(tmp_path / "unchanged.kcl", "new")
    changed.chmod(0o640)
    old_changed = changed.stat()
    old_unchanged = unchanged.stat()
    replacements: list[tuple[Path, Path]] = []
    replace = os.replace

    def track_replace(source: str, destination: Path) -> None:
        replacements.append((Path(source), destination))
        replace(source, destination)

    monkeypatch.setattr(core.os, "replace", track_replace)
    result = core.run_request(request("format", tmp_path, write=True), backend=Backend())

    assert result.exit_code == 0
    assert changed.read_text() == "new"
    assert stat.S_IMODE(changed.stat().st_mode) == stat.S_IMODE(old_changed.st_mode)
    assert unchanged.stat().st_ino == old_unchanged.st_ino
    assert unchanged.stat().st_mtime_ns == old_unchanged.st_mtime_ns
    assert len(replacements) == 1
    assert replacements[0][0].parent == changed.parent
    assert replacements[0][1] == changed
    assert [record["written"] for record in result.formatting] == [True, False]
    assert not list(tmp_path.glob("*.tmp"))

    second = core.run_request(request("format", tmp_path, write=True), backend=Backend())
    assert second.exit_code == 0
    assert not any(record["changed"] for record in second.formatting)
    assert len(replacements) == 1


def test_write_failure_reports_committed_files_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = write(tmp_path / "a.kcl")
    failed = write(tmp_path / "b.kcl")
    last = write(tmp_path / "c.kcl")
    replace = os.replace

    def fail_second(source: str, destination: Path) -> None:
        if destination == failed:
            raise PermissionError("simulated write failure")
        replace(source, destination)

    monkeypatch.setattr(core.os, "replace", fail_second)
    result = core.run_request(request("format", tmp_path, write=True), backend=Backend())

    assert result.exit_code == 2
    assert first.read_text() == "new"
    assert failed.read_text() == last.read_text() == "old"
    assert [record["written"] for record in result.formatting] == [True, False, False]
    assert result.diagnostics[-1].origin == "tool"
    assert result.diagnostics[-1].file == str(failed)
    assert result.pending_write is None
    assert not list(tmp_path.glob("*.tmp"))


def test_progress_before_and_after_atomic_write(tmp_path: Path) -> None:
    source = write(tmp_path / "main.kcl")
    snapshots: list[tuple] = []

    def capture(result) -> None:
        snapshots.append((copy.deepcopy(result), source.read_text()))

    result = core.run_request(
        request("format", source, write=True), emit=capture, backend=Backend()
    )

    assert result.exit_code == 0
    assert snapshots[0][0].targets == [str(source)]
    pending = [item for item in snapshots if item[0].pending_write is not None]
    assert len(pending) == 1
    assert pending[0][0].pending_write == (str(source), "new")
    assert pending[0][1] == "old"
    final, contents = snapshots[-1]
    assert contents == "new"
    assert final.formatting[0]["written"] is True
    assert final.pending_write is None


@pytest.mark.parametrize("config", [None, "", '[other]\ndefault_file = "other.kcl"\n'])
def test_project_default_main_entrypoint(tmp_path: Path, config: str | None) -> None:
    source = write(tmp_path / "main.kcl", "valid")
    if config is not None:
        write(tmp_path / "project.toml", config)
    backend = Backend()

    result = core.run_request(request("execute", tmp_path), backend=backend)

    assert result.exit_code == 0
    assert backend.calls == [("execute", str(source), False)]


@pytest.mark.parametrize("command", ["execute", "sketches"])
def test_project_top_level_default_file_and_absolute_backend_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    root = tmp_path / "проект с пробелами"
    source = write(root / "nested" / "сборка.kcl", "valid")
    write(root / "project.toml", 'default_file = "nested/сборка.kcl"\n')
    write(root / "main.kcl", "invalid")
    monkeypatch.chdir(tmp_path)
    backend = Backend()

    result = core.run_request(request(command, Path(root.name)), backend=backend)

    assert result.exit_code == 0
    assert result.targets == [str(source)]
    assert backend.calls == [("execute", str(source), command == "sketches")]
    assert (result.sketches is not None) == (command == "sketches")


@pytest.mark.parametrize(
    "config",
    [
        'default_file = "absent.kcl"',
        'default_file = "wrong.txt"',
        "default_file = 123",
        "default_file = []",
        'default_file = ""',
        'default_file = "   "',
        "default_file = [",
    ],
)
def test_invalid_project_configuration_does_not_fall_back(tmp_path: Path, config: str) -> None:
    write(tmp_path / "main.kcl", "valid")
    write(tmp_path / "wrong.txt", "valid")
    write(tmp_path / "project.toml", config)
    backend = Backend()

    result = core.run_request(request("execute", tmp_path), backend=backend)

    assert result.exit_code == 2
    assert not backend.calls


@pytest.mark.parametrize("file", ["project.toml", "main.kcl"])
def test_project_invalid_utf8_is_input_error(tmp_path: Path, file: str) -> None:
    write(tmp_path / "main.kcl", "valid")
    (tmp_path / file).write_bytes(b"\xff")
    backend = Backend()

    result = core.run_request(request("execute", tmp_path), backend=backend)

    assert result.exit_code == 2
    assert not backend.calls


@pytest.mark.parametrize("linked", ["project.toml", "main.kcl"])
def test_project_symlink_inputs_are_rejected(tmp_path: Path, linked: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = write(tmp_path / "outside", "" if linked == "project.toml" else "valid")
    (root / linked).symlink_to(outside)
    backend = Backend()

    result = core.run_request(request("execute", root), backend=backend)

    assert result.exit_code == 2
    assert not backend.calls
