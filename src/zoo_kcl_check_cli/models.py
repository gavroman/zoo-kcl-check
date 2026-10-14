"""Picklable requests and results; no native backend objects cross the pipe."""

from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version

MODES = {
    "format": "local_format",
    "lint": "local_lint",
    "execute": "local_mock_execution",
    "sketches": "local_sketch_constraints",
}


def backend_version() -> str | None:
    try:
        return version("zoo-kcl")
    except PackageNotFoundError:
        return None


@dataclass
class Request:
    command: str
    path: str
    write: bool = False
    check: bool = False
    json: bool = False


@dataclass
class Diagnostic:
    severity: str
    origin: str
    message: str
    code: str | None = None
    file: str | None = None
    range: dict | None = None
    rendered: str | None = None
    exit_code: int = 1

    def to_dict(self) -> dict:
        result = asdict(self)
        del result["exit_code"]
        return result


@dataclass
class Result:
    command: str | None
    targets: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    sketches: dict | None = None
    formatting: list[dict] | None = None
    exit_code: int = 0
    # Worker progress used to reconcile a timeout during an atomic file replacement.
    pending_write: tuple[str, str] | None = field(default=None, repr=False)

    def add(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)
        if self.exit_code != 130:
            self.exit_code = max(self.exit_code, diagnostic.exit_code)

    def extend(self, diagnostics: list[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.add(diagnostic)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "command": self.command,
            "ok": self.exit_code == 0,
            "exit_code": self.exit_code,
            "mode": MODES.get(self.command),
            "geometry_validated": False,
            "backend": {"name": "zoo-kcl", "version": backend_version()},
            "targets": self.targets,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "sketches": self.sketches,
            "formatting": self.formatting,
        }
