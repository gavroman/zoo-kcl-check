"""Filesystem operations for KCL checks, executed entirely in the worker."""

from __future__ import annotations

import os
import stat
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Diagnostic, Request, Result

if TYPE_CHECKING:
    from .backend import KclBackend


EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
    }
)


@dataclass
class _PreparedFormat:
    path: Path
    code: str
    permissions: int
    record: dict


def _input_error(result: Result, message: str, file: Path | None = None) -> None:
    result.add(
        Diagnostic(
            severity="error",
            origin="input",
            message=message,
            file=str(file) if file is not None else None,
            exit_code=2,
        )
    )


def _validate_target(path: Path, result: Result) -> bool:
    try:
        if path.is_symlink():
            _input_error(result, "Symbolic links are not supported inputs.", path)
            return False
        info = path.stat()
    except (OSError, ValueError) as error:
        _input_error(result, f"Cannot access input: {error}", path)
        return False
    if not stat.S_ISREG(info.st_mode):
        _input_error(result, "Expected a regular KCL file.", path)
        return False
    if path.suffix != ".kcl":
        _input_error(result, "Input file must have the .kcl extension.", path)
        return False
    return True


def _collect_targets(root: Path, result: Result) -> list[Path]:
    targets: list[Path] = []

    def traversal_error(error: OSError) -> None:
        file = Path(error.filename) if error.filename is not None else root
        _input_error(result, f"Cannot read directory: {error}", file)

    for directory, names, files in os.walk(
        root, topdown=True, onerror=traversal_error, followlinks=False
    ):
        current = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name not in EXCLUDED_DIRECTORIES and not (current / name).is_symlink()
        )
        for name in sorted(files):
            path = current / name
            if path.suffix != ".kcl" or path.is_symlink():
                continue
            if _validate_target(path, result):
                targets.append(path)
    targets.sort(key=str)
    if not targets and result.exit_code != 2:
        _input_error(result, "Directory contains no KCL files.", root)
    return targets


def _project_entrypoint(root: Path, result: Result) -> Path | None:
    config = root / "project.toml"
    try:
        if config.is_symlink():
            _input_error(result, "Project configuration must not be a symbolic link.", config)
            return None
        if config.exists():
            with config.open("rb") as source:
                settings = tomllib.load(source)
            if "default_file" in settings:
                entry = settings["default_file"]
                if not isinstance(entry, str) or not entry.strip():
                    _input_error(result, "default_file must be a nonempty string.", config)
                    return None
            else:
                entry = "main.kcl"
        else:
            entry = "main.kcl"
        target = Path(os.path.abspath(root / entry))
    except (OSError, ValueError, UnicodeError) as error:
        _input_error(result, f"Cannot read project configuration: {error}", config)
        return None
    return target if _validate_target(target, result) else None


def _resolve_targets(request: Request, result: Result) -> list[Path]:
    try:
        path = Path(os.path.abspath(request.path))
        if path.is_symlink():
            _input_error(result, "Symbolic links are not supported inputs.", path)
            return []
        info = path.stat()
    except (OSError, ValueError) as error:
        _input_error(result, f"Cannot access input: {error}")
        return []
    if stat.S_ISDIR(info.st_mode):
        if request.command in {"execute", "sketches"}:
            entrypoint = _project_entrypoint(path, result)
            return [entrypoint] if entrypoint is not None else []
        if request.command == "format" and not (request.write or request.check or request.json):
            _input_error(
                result,
                "Formatting a directory requires --write, --check, or --json.",
                path,
            )
            return []
        return _collect_targets(path, result)
    return [path] if _validate_target(path, result) else []


def _read_target(path: Path, result: Result) -> tuple[str, int] | None:
    if not _validate_target(path, result):
        return None
    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            code = source.read()
            permissions = stat.S_IMODE(os.fstat(source.fileno()).st_mode)
        return code, permissions
    except (OSError, UnicodeError) as error:
        _input_error(result, f"Cannot read UTF-8 input: {error}", path)
        return None


def _atomic_write(prepared: _PreparedFormat) -> None:
    """Replace one file atomically, retaining its original permission bits."""
    path = prepared.path
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(prepared.code)
            stream.flush()
            os.chmod(temporary, prepared.permissions)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def run_request(
    request: Request,
    emit: Callable[[Result], None] | None = None,
    backend: KclBackend | None = None,
) -> Result:
    """Resolve input, collect results, and publish cumulative worker progress."""
    result = Result(command=request.command)

    def progress() -> None:
        if emit is not None:
            emit(result)

    targets = _resolve_targets(request, result)
    result.targets = [str(path) for path in targets]
    if request.command == "format":
        result.formatting = []
    progress()
    if not targets:
        return result

    if backend is None:
        from .backend import KclBackend

        backend = KclBackend()

    if request.command in {"execute", "sketches"}:
        if _read_target(targets[0], result) is not None:
            diagnostics, sketches = backend.execute(
                str(targets[0]), include_sketches=request.command == "sketches"
            )
            result.extend(diagnostics)
            result.sketches = sketches
        progress()
        return result

    prepared: list[_PreparedFormat] = []
    for path in targets:
        source = _read_target(path, result)
        if source is None:
            progress()
            continue
        code, permissions = source
        if request.command == "lint":
            result.extend(backend.lint(code, str(path)))
        else:
            formatted, diagnostics = backend.format(code, str(path))
            result.extend(diagnostics)
            if formatted is not None:
                record = {
                    "file": str(path),
                    "changed": formatted != code,
                    "written": False,
                    "formatted_code": (
                        formatted if not request.write and not request.check else None
                    ),
                }
                assert result.formatting is not None
                result.formatting.append(record)
                prepared.append(_PreparedFormat(path, formatted, permissions, record))
                if request.check and record["changed"]:
                    result.exit_code = max(result.exit_code, 1)
        progress()

    if request.command == "format" and request.write and result.exit_code == 0:
        for item in prepared:
            if not item.record["changed"]:
                continue
            result.pending_write = (str(item.path), item.code)
            progress()
            try:
                _atomic_write(item)
                item.record["written"] = True
            except OSError as error:
                result.add(
                    Diagnostic(
                        severity="error",
                        origin="tool",
                        message=f"Cannot write formatted file: {error}",
                        file=str(item.path),
                        exit_code=2,
                    )
                )
                result.pending_write = None
                progress()
                break
            result.pending_write = None
            progress()
    return result
