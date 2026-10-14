"""Command-line arguments and text/JSON presentation."""

import argparse
import json
import math
import multiprocessing
import sys
import time

from . import __version__
from .models import MODES, Diagnostic, Request, Result, backend_version
from .runner import run

EXIT_HELP = (
    "Exit codes: 0 success; 1 KCL diagnostic, lint finding, sketch constraint problem "
    "or --check formatting difference; 2 invalid input, timeout or tool failure; "
    "130 interrupted. Any warning fails the check. No CAD geometry is validated."
)


class ArgumentError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError(message)


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number of seconds")
    return seconds


def _parser() -> Parser:
    parser = Parser(
        prog="kcl-check",
        description=(
            "Local checks for Zoo / KittyCAD KCL.\n"
            "No Zoo cloud execution. CAD geometry is not validated."
        ),
        epilog="Use kcl-check COMMAND --help for command options.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"kcl-check {__version__}; zoo-kcl {backend_version() or 'not installed'}; "
            f"Python {sys.version.split()[0]}"
        ),
        help="show CLI, zoo-kcl and Python versions",
    )
    commands = parser.add_subparsers(dest="command", title="Commands", metavar="COMMAND")
    descriptions = {
        "format": (
            "Format KCL source",
            "Format without writing by default. Use --write to update files or --check "
            "to detect differences. A folder needs --write, --check or --json. All files "
            "must parse before any write; each replacement is atomic, the batch is not.",
        ),
        "lint": (
            "Check syntax and style",
            "Check KCL syntax and all style findings, without fixes or execution. "
            "Recursively check .kcl files in a folder; symbolic links are excluded.",
        ),
        "execute": (
            "Run KCL with a local mock engine",
            "Run local mock execution to check names, arguments, types, assertions and "
            "unit warnings. This is not real CAD construction. Sketch constraints are "
            "reported separately by the sketches command. A folder uses default_file "
            "from project.toml or main.kcl.",
        ),
        "sketches": (
            "Report sketch constraint status",
            "Run mock execution once and analyze sketch constraints. Under-constrained, "
            "over-constrained and erroneous sketches fail the check. An incomplete "
            "report fails too; a complete empty report succeeds. A folder uses "
            "default_file from project.toml or main.kcl.",
        ),
    }
    for name, (summary, description) in descriptions.items():
        command = commands.add_parser(name, help=summary, description=description, epilog=EXIT_HELP)
        command.add_argument("path", metavar="PATH", help="a .kcl file or project folder")
        command.add_argument(
            "--json", action="store_true", help="print one machine-readable JSON object"
        )
        command.add_argument(
            "--timeout",
            type=_seconds,
            default=60.0,
            metavar="SECONDS",
            help="hard deadline for the whole command (default: 60 seconds)",
        )
        if name == "format":
            group = command.add_mutually_exclusive_group()
            group.add_argument("-w", "--write", action="store_true", help="write formatted source")
            group.add_argument(
                "--check", action="store_true", help="fail on formatting differences"
            )
    return parser


def _render_text(result: Result, request: Request | None = None) -> None:
    if request and request.command == "format" and not request.write and not request.check:
        for entry in result.formatting or []:
            if entry["formatted_code"] is not None:
                sys.stdout.write(entry["formatted_code"])
    else:
        for entry in result.formatting or []:
            status = (
                "written"
                if entry["written"]
                else "needs formatting"
                if entry["changed"]
                else "unchanged"
            )
            print(f"{entry['file']}: {status}")
        if result.sketches is not None:
            report = result.sketches
            print(
                f"Sketch report: {'complete' if report['complete'] else 'incomplete'}, "
                f"{report['total']} sketches"
            )
            for entry in report["entries"]:
                print(
                    f"{entry['name'] or '<unnamed>'}[{entry['instance_index']}]: {entry['status']} "
                    f"(free={entry['free_count']}, conflicts={entry['conflict_count']}, "
                    f"total={entry['total_count']})"
                )
        if not result.diagnostics and result.formatting is None and result.sketches is None:
            print("OK: local checks passed; CAD geometry is not validated.")
    for diagnostic in result.diagnostics:
        if diagnostic.rendered:
            print(
                diagnostic.rendered,
                file=sys.stderr,
                end="" if diagnostic.rendered.endswith("\n") else "\n",
            )
        else:
            location = f"{diagnostic.file}: " if diagnostic.file else ""
            if diagnostic.range:
                start = diagnostic.range["start"]
                location += f"{start['line']}:{start['column']}: "
            rule = f" [{diagnostic.code}]" if diagnostic.code else ""
            print(f"{location}{diagnostic.severity}{rule}: {diagnostic.message}", file=sys.stderr)


def _output(result: Result, json_mode: bool, request: Request | None = None) -> int:
    if json_mode:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
    else:
        _render_text(result, request)
    return result.exit_code


def _main(arguments: list[str]) -> int:
    started = time.monotonic()
    parser = _parser()
    if not arguments:
        parser.print_help()
        return 0
    options = arguments[: arguments.index("--")] if "--" in arguments else arguments
    json_mode = "--json" in options
    command = arguments[0] if arguments[0] in MODES else None
    try:
        namespace = parser.parse_args(arguments)
        if namespace.command is None:
            raise ArgumentError("a command is required")
    except ArgumentError as error:
        result = Result(command)
        result.add(Diagnostic("error", "input", str(error), exit_code=2))
        return _output(result, json_mode)
    except SystemExit as exit_request:
        # argparse's help/version handlers intentionally print human-readable text.
        return int(exit_request.code or 0)
    request = Request(
        namespace.command,
        namespace.path,
        write=getattr(namespace, "write", False),
        check=getattr(namespace, "check", False),
        json=namespace.json,
    )
    result = run(request, namespace.timeout, deadline=started + namespace.timeout)
    return _output(result, namespace.json, request)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(arguments)
    except KeyboardInterrupt:
        command = arguments[0] if arguments and arguments[0] in MODES else None
        result = Result(command)
        result.add(Diagnostic("fatal", "tool", "Interrupted by user", exit_code=130))
        options = arguments[: arguments.index("--")] if "--" in arguments else arguments
        return _output(result, "--json" in options)
