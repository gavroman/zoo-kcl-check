"""The worker's adapter to the pinned, local-only Zoo KCL APIs."""

from __future__ import annotations

import asyncio
from importlib import import_module, metadata
from typing import Any

from .models import Diagnostic

EXPECTED_BACKEND_VERSION = "0.3.191"


def _exception_text(error: BaseException) -> str:
    # Rich native KclError.args contains (rendered_report, retryable). str(error)
    # would print a tuple, escaping the report's newlines.
    if error.args and isinstance(error.args[0], str):
        return error.args[0]
    return str(error) or type(error).__name__


def _byte_position(encoded: bytes, offset: int) -> dict[str, int]:
    """Translate a UTF-8 byte offset to a one-based Unicode position."""
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError("KCL lint returned a non-integer source offset")
    if not 0 <= offset <= len(encoded):
        raise ValueError("KCL lint returned a source offset outside the input")
    prefix = encoded[:offset].decode("utf-8")
    return {"line": prefix.count("\n") + 1, "column": len(prefix.rsplit("\n", 1)[-1]) + 1}


def _lint_range(encoded: bytes, offsets: tuple[int, int]) -> dict[str, Any]:
    start, end = offsets
    if start > end:
        raise ValueError("KCL lint returned a reversed source range")
    return {"start": _byte_position(encoded, start), "end": _byte_position(encoded, end)}


class KclBackend:
    """Normalize native results without using cloud execution or geometry APIs.

    Construct this adapter only in a worker. Importing this Python module does
    not load the native library. A supplied module is for infrastructure tests.
    """

    def __init__(self, module: Any = None) -> None:
        if module is None:
            actual = metadata.version("zoo-kcl")
            if actual != EXPECTED_BACKEND_VERSION:
                raise RuntimeError(f"Expected zoo-kcl=={EXPECTED_BACKEND_VERSION}, found {actual}")
            module = import_module("kcl")
        self._module = module

    def _failure(self, error: BaseException, origin: str, file: str | None = None) -> Diagnostic:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise error
        text = _exception_text(error)
        if isinstance(error, self._module.KclError):
            return Diagnostic(
                severity="error",
                origin=origin,
                message=text,
                file=file,
                rendered=text,
            )
        return Diagnostic(
            severity="fatal",
            origin="tool",
            message=f"KCL backend failed ({type(error).__name__}): {text}",
            file=file,
            exit_code=2,
        )

    def format(self, code: str, file: str) -> tuple[str | None, list[Diagnostic]]:
        try:
            return self._module.format(code), []
        except BaseException as error:
            return None, [self._failure(error, "execution", file)]

    def lint(self, code: str, file: str) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        try:
            # Preserve structured syntax errors: the full linter raises a generic
            # Exception for invalid input instead of a KclError.
            self._module.parse_code(code)
            encoded = code.encode("utf-8")
            # Run all lint families while leaving automatic fixes disabled.
            for discovered in self._module.lint_and_fix_families(code, []).unfixed_lints:
                diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        origin="lint",
                        code=discovered.finding.code,
                        message=f"{discovered.finding.title}: {discovered.description}",
                        file=file,
                        range=_lint_range(encoded, discovered.pos),
                    )
                )
        except BaseException as error:
            diagnostics.append(self._failure(error, "lint", file))
        return diagnostics

    @staticmethod
    def _outcome_issues(outcome: Any, diagnostics: list[Diagnostic]) -> None:
        for issue in outcome.issues():
            if issue.is_fatal():
                severity = "fatal"
            elif issue.is_err():
                severity = "error"
            elif issue.is_warning():
                severity = "warning"
            else:
                severity = "info"
            diagnostics.append(
                Diagnostic(
                    severity=severity,
                    origin="execution",
                    message=issue.message(),
                    rendered=outcome.report(issue),
                )
            )

    @staticmethod
    def _report_issues(report: Any, diagnostics: list[Diagnostic]) -> None:
        # The report repeats outcome diagnostics. Its rendered strings are also
        # the only way to retain execution warnings preceding a KclError.
        seen = {
            (item.severity, item.rendered)
            for item in diagnostics
            if item.origin == "execution" and item.rendered is not None
        }
        for attribute, severity in (
            ("warnings", "warning"),
            ("execution_errors", "error"),
            ("execution_fatals", "fatal"),
        ):
            for rendered in getattr(report, attribute):
                if (severity, rendered) not in seen:
                    diagnostics.append(
                        Diagnostic(
                            severity=severity,
                            origin="execution",
                            message=rendered,
                            rendered=rendered,
                        )
                    )
        if report.kcl_error is not None:
            rendered = report.kcl_error.text
            if ("error", rendered) not in seen:
                diagnostics.append(
                    Diagnostic(
                        severity="error",
                        origin="execution",
                        message=rendered,
                        rendered=rendered,
                    )
                )

    @staticmethod
    def _sketches(report: Any, diagnostics: list[Diagnostic]) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for attribute, status in (
            ("fully_constrained", "fully_constrained"),
            ("under_constrained", "under_constrained"),
            ("over_constrained", "over_constrained"),
            ("errors", "error"),
        ):
            for sketch in getattr(report, attribute):
                entry = {
                    "name": sketch.name,
                    "instance_index": sketch.instance_index,
                    "status": status,
                    "free_count": sketch.free_count,
                    "conflict_count": sketch.conflict_count,
                    "total_count": sketch.total_count,
                }
                entries.append(entry)
                if status != "fully_constrained":
                    diagnostics.append(
                        Diagnostic(
                            severity="error",
                            origin="solver",
                            message=(
                                f"Sketch {sketch.name!r} (instance {sketch.instance_index}) "
                                f"is {status}: {sketch.free_count} free, "
                                f"{sketch.conflict_count} conflicting, "
                                f"{sketch.total_count} total segments."
                            ),
                        )
                    )
        if not report.is_complete:
            diagnostics.append(
                Diagnostic(
                    severity="error",
                    origin="solver",
                    message="Sketch constraint report is incomplete.",
                )
            )
        return {
            "complete": report.is_complete,
            "total": report.total_sketches(),
            "entries": entries,
        }

    def execute(
        self, path: str, include_sketches: bool = False
    ) -> tuple[list[Diagnostic], dict[str, Any] | None]:
        diagnostics: list[Diagnostic] = []
        sketches = None

        async def run() -> Any:
            return await self._module.mock_execute(path)

        try:
            outcome = asyncio.run(run())
            self._outcome_issues(outcome, diagnostics)
            if include_sketches:
                report = outcome.sketch_constraint_report()
                self._report_issues(report, diagnostics)
                sketches = self._sketches(report, diagnostics)
        except BaseException as error:
            diagnostics.append(self._failure(error, "execution"))
            if isinstance(error, self._module.KclError):
                try:
                    report = error.sketch_constraint_report
                    if report is not None:
                        self._report_issues(report, diagnostics)
                        if include_sketches:
                            sketches = self._sketches(report, diagnostics)
                except BaseException as report_error:
                    diagnostics.append(self._failure(report_error, "execution"))
        return diagnostics, sketches
