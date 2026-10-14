"""Failure and normalization tests; real KCL programs live in CLI tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zoo_kcl_check_cli.backend import KclBackend


class KclError(Exception):
    def __init__(self, message, retryable=False, report=None):
        super().__init__(message, retryable)
        self.sketch_constraint_report = report


class PanicException(BaseException):
    pass


def issue(message="units warning", severity="warning"):
    return SimpleNamespace(
        message=lambda: message,
        is_warning=lambda: severity == "warning",
        is_err=lambda: severity == "error",
        is_fatal=lambda: severity == "fatal",
    )


def sketch(name="shape", instance_index=0, free=0, conflict=0, total=3):
    return SimpleNamespace(
        name=name,
        instance_index=instance_index,
        free_count=free,
        conflict_count=conflict,
        total_count=total,
    )


def report(complete=True, **values):
    fields = dict(
        fully_constrained=[],
        under_constrained=[],
        over_constrained=[],
        errors=[],
        warnings=[],
        execution_errors=[],
        execution_fatals=[],
        is_complete=complete,
        kcl_error=None,
    )
    fields.update(values)
    total = sum(
        len(fields[category])
        for category in ("fully_constrained", "under_constrained", "over_constrained", "errors")
    )
    return SimpleNamespace(**fields, total_sketches=lambda: total)


def module(outcome=None, error=None):
    async def mock_execute(path):
        calls.append(path)
        if error is not None:
            raise error
        return outcome

    calls = []
    return SimpleNamespace(
        KclError=KclError,
        mock_execute=mock_execute,
        format=lambda code: code,
        lint=lambda code: [],
        calls=calls,
    )


def outcome(issues=(), constraint_report=None):
    return SimpleNamespace(
        issues=lambda: issues,
        report=lambda value: f"rendered {value.message()}",
        sketch_constraint_report=Mock(return_value=constraint_report or report()),
    )


def test_execute_does_not_analyze_sketches():
    result = outcome([issue()])
    backend_module = module(result)
    diagnostics, sketches = KclBackend(backend_module).execute("/project/main.kcl")
    assert sketches is None
    assert backend_module.calls == ["/project/main.kcl"]
    result.sketch_constraint_report.assert_not_called()
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "warning"
    assert diagnostics[0].message == "units warning"
    assert diagnostics[0].rendered == "rendered units warning"
    assert diagnostics[0].file is None
    assert diagnostics[0].range is None
    assert diagnostics[0].exit_code == 1


@pytest.mark.parametrize("severity", ["warning", "error", "fatal", "info"])
def test_execution_issue_severity(severity):
    diagnostics, _ = KclBackend(module(outcome([issue(severity=severity)]))).execute("main.kcl")
    assert diagnostics[0].severity == severity
    assert diagnostics[0].origin == "execution"
    assert diagnostics[0].exit_code == 1


@pytest.mark.parametrize("include_sketches", [False, True])
def test_kcl_error_preserves_warnings_and_partial_report(include_sketches):
    partial = report(
        complete=False,
        fully_constrained=[sketch()],
        warnings=["full warning\nwith source and filename"],
        kcl_error=SimpleNamespace(text="full error\nwith imported source"),
    )
    diagnostics, sketches = KclBackend(
        module(error=KclError(partial.kcl_error.text, False, partial))
    ).execute("/project/main.kcl", include_sketches)
    errors = [value for value in diagnostics if value.origin == "execution"]
    assert [value.severity for value in errors] == ["error", "warning"]
    assert errors[0].message == "full error\nwith imported source"
    assert errors[0].rendered == errors[0].message
    assert errors[1].rendered == partial.warnings[0]
    assert all(value.file is None and value.range is None for value in errors)
    if include_sketches:
        assert sketches["complete"] is False
        assert sketches["total"] == 1
        assert sketches["entries"][0]["status"] == "fully_constrained"
        assert any(value.origin == "solver" for value in diagnostics)
    else:
        assert sketches is None
        assert all(value.origin != "solver" for value in diagnostics)


def test_report_preserves_execution_errors_and_fatals():
    partial = report(
        complete=False,
        execution_errors=["first error"],
        execution_fatals=["fatal report"],
    )
    diagnostics, _ = KclBackend(module(error=KclError("terminal", report=partial))).execute(
        "main.kcl"
    )
    assert [value.severity for value in diagnostics] == ["error", "error", "fatal"]
    assert diagnostics[-1].rendered == "fatal report"


def test_successful_report_does_not_repeat_execution_diagnostics():
    constraints = report(warnings=["rendered units warning"])
    result = outcome([issue()], constraints)
    backend_module = module(result)
    diagnostics, sketches = KclBackend(backend_module).execute("main.kcl", True)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "units warning"
    assert sketches == {"complete": True, "total": 0, "entries": []}
    assert backend_module.calls == ["main.kcl"]
    result.sketch_constraint_report.assert_called_once_with()


def test_sketch_status_and_duplicate_instances():
    constraints = report(
        fully_constrained=[sketch("shape", 0)],
        under_constrained=[sketch("shape", 1, free=2)],
        over_constrained=[sketch("conflict", conflict=1)],
        errors=[sketch("failed")],
    )
    diagnostics, sketches = KclBackend(module(outcome(constraint_report=constraints))).execute(
        "main.kcl", True
    )
    assert sketches["complete"] is True
    assert sketches["total"] == 4
    assert [entry["status"] for entry in sketches["entries"]] == [
        "fully_constrained",
        "under_constrained",
        "over_constrained",
        "error",
    ]
    assert [entry["instance_index"] for entry in sketches["entries"][:2]] == [0, 1]
    assert sketches["entries"][1]["free_count"] == 2
    assert sketches["entries"][2]["conflict_count"] == 1
    assert len(diagnostics) == 3
    assert all(value.origin == "solver" and value.exit_code == 1 for value in diagnostics)


def test_incomplete_sketch_report():
    diagnostics, sketches = KclBackend(
        module(outcome(constraint_report=report(complete=False)))
    ).execute("main.kcl", True)
    assert sketches == {"complete": False, "total": 0, "entries": []}
    assert len(diagnostics) == 1
    assert diagnostics[0].exit_code == 1
    assert diagnostics[0].origin == "solver"


@pytest.mark.parametrize("operation", ["format", "lint", "execute"])
@pytest.mark.parametrize("error", [PanicException("native panic"), RuntimeError("unexpected")])
def test_native_panic_and_internal_error(operation, error):
    backend_module = module(error=error)

    def fail(code):
        raise error

    backend_module.format = fail
    backend_module.lint = fail
    backend = KclBackend(backend_module)
    if operation == "format":
        formatted, diagnostics = backend.format("source", "file.kcl")
        assert formatted is None
    elif operation == "lint":
        diagnostics = backend.lint("source", "file.kcl")
    else:
        diagnostics, sketches = backend.execute("file.kcl")
        assert sketches is None
    assert diagnostics[0].exit_code == 2
    assert diagnostics[0].origin == "tool"
    assert diagnostics[0].severity == "fatal"
    assert str(error) in diagnostics[0].message


def test_report_panic_preserves_execution_issues():
    result = outcome([issue()])
    result.sketch_constraint_report.side_effect = PanicException("solver panic")
    diagnostics, sketches = KclBackend(module(result)).execute("main.kcl", True)
    assert sketches is None
    assert [value.exit_code for value in diagnostics] == [1, 2]


@pytest.mark.parametrize("operation", ["format", "lint"])
def test_parse_error_preserves_full_rendering(operation):
    backend_module = module()

    def fail(code):
        raise KclError("syntax report\nwith source", False)

    setattr(backend_module, operation, fail)
    backend = KclBackend(backend_module)
    if operation == "format":
        _, diagnostics = backend.format("x = [", "file.kcl")
    else:
        diagnostics = backend.lint("x = [", "file.kcl")
    assert diagnostics[0].message == "syntax report\nwith source"
    assert diagnostics[0].rendered == diagnostics[0].message
    assert diagnostics[0].exit_code == 1
    assert diagnostics[0].file == "file.kcl"
    assert diagnostics[0].range is None


def test_lint_unicode_ranges_and_overridden_findings():
    code = "// привет 😀\nBAD_NAME = 1mm\n"
    start = len("// привет 😀\n".encode())
    backend_module = module()
    backend_module.lint = lambda source: [
        SimpleNamespace(
            finding=SimpleNamespace(code="identifier-case", title="Camel case"),
            description="Use camelCase",
            pos=(start, start + len("BAD_NAME")),
            overridden=True,
        )
    ]
    diagnostics = KclBackend(backend_module).lint(code, "/project/модель.kcl")
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "identifier-case"
    assert diagnostics[0].file == "/project/модель.kcl"
    assert diagnostics[0].range == {
        "start": {"line": 2, "column": 1},
        "end": {"line": 2, "column": 9},
    }
    assert diagnostics[0].exit_code == 1


def test_lint_unicode_columns_within_line():
    code = 'message = "русский 😀"; BAD_NAME = 1mm\n'
    prefix = code[: code.index("BAD_NAME")]
    start = len(prefix.encode())
    backend_module = module()
    backend_module.lint = lambda source: [
        SimpleNamespace(
            finding=SimpleNamespace(code="case", title="Camel case"),
            description="style",
            pos=(start, start + 8),
        )
    ]
    diagnostics = KclBackend(backend_module).lint(code, "main.kcl")
    assert diagnostics[0].range["start"] == {"line": 1, "column": len(prefix) + 1}
    assert diagnostics[0].range["end"] == {"line": 1, "column": len(prefix) + 9}


@pytest.mark.parametrize("offsets", [(0, 100), (5, 1), (1, 2)])
def test_invalid_backend_offsets_are_tool_errors(offsets):
    backend_module = module()
    backend_module.lint = lambda source: [
        SimpleNamespace(
            finding=SimpleNamespace(code="case", title="Camel case"),
            description="style",
            pos=offsets,
        )
    ]
    diagnostics = KclBackend(backend_module).lint("я = 1\n", "main.kcl")
    assert diagnostics[0].exit_code == 2
    assert diagnostics[0].range is None


def test_backend_rejects_unexpected_version_before_native_import(monkeypatch):
    monkeypatch.setattr("zoo_kcl_check_cli.backend.metadata.version", lambda name: "0.3.192")
    native_import = Mock(side_effect=AssertionError("must not import mismatched backend"))
    monkeypatch.setattr("zoo_kcl_check_cli.backend.import_module", native_import)
    with pytest.raises(RuntimeError, match="Expected zoo-kcl==0.3.191"):
        KclBackend()
    native_import.assert_not_called()


def test_backend_imports_native_module_only_on_construction(monkeypatch):
    backend_module = module()
    monkeypatch.setattr("zoo_kcl_check_cli.backend.metadata.version", lambda name: "0.3.191")
    native_import = Mock(return_value=backend_module)
    monkeypatch.setattr("zoo_kcl_check_cli.backend.import_module", native_import)
    assert KclBackend().format("x = 1mm\n", "main.kcl") == ("x = 1mm\n", [])
    native_import.assert_called_once_with("kcl")


def test_forbidden_cloud_api():
    class RestrictedModule:
        KclError = KclError

        @staticmethod
        def format(code):
            return code

        @staticmethod
        def lint(code):
            return []

        @staticmethod
        async def mock_execute(path):
            return outcome()

        def __getattr__(self, name):
            raise AssertionError(f"Forbidden KCL API accessed: {name}")

    backend = KclBackend(RestrictedModule())
    assert backend.format("x = 1mm\n", "main.kcl") == ("x = 1mm\n", [])
    assert backend.lint("x = 1mm\n", "main.kcl") == []
    assert backend.execute("main.kcl") == ([], None)
    diagnostics, sketches = backend.execute("main.kcl", True)
    assert diagnostics == []
    assert sketches == {"complete": True, "total": 0, "entries": []}
