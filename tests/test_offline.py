"""Prove local KCL operations work under an OS-level network prohibition."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = "(version 1)(allow default)(deny network*)"


@pytest.fixture(scope="module")
def os_network_sandbox():
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        pytest.skip("OS-level offline acceptance uses macOS /usr/bin/sandbox-exec")
    # Verify the real kernel restriction before accepting an offline smoke test.
    probe = subprocess.run(
        [
            str(sandbox),
            "-p",
            PROFILE,
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
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if "sandbox_apply: Operation not permitted" in probe.stderr:
        pytest.skip(
            "outer execution sandbox forbids macOS sandbox_apply; "
            "rerun offline acceptance outside that outer sandbox"
        )
    assert probe.returncode == 0, (probe.stdout, probe.stderr)
    assert probe.stdout.strip() == "NETWORK_BLOCKED", (probe.stdout, probe.stderr)
    return [str(sandbox), "-p", PROFILE]


@pytest.mark.parametrize(
    "command, fixture, exit_code",
    [
        ("format", "valid.kcl", 0),
        ("format", "syntax_error.kcl", 1),
        ("lint", "valid.kcl", 0),
        ("lint", "syntax_error.kcl", 1),
        ("execute", "imports_valid", 0),
        ("execute", "imports_error", 1),
        ("execute", "cad_operations.kcl", 0),
        ("execute", "units_warning.kcl", 1),
        ("sketches", "fully_constrained.kcl", 0),
        ("sketches", "under_constrained.kcl", 1),
        ("sketches", "over_constrained.kcl", 1),
        ("sketches", "duplicate_names.kcl", 0),
        ("sketches", "no_sketches.kcl", 0),
        ("sketches", "partial_sketch_report.kcl", 1),
    ],
)
def test_all_commands_with_network_forbidden(os_network_sandbox, command, fixture, exit_code):
    environment = os.environ.copy()
    environment.pop("ZOO_API_TOKEN", None)
    environment.pop("KITTYCAD_API_TOKEN", None)
    sources = {path: path.read_bytes() for path in FIXTURES.rglob("*.kcl")}
    process = subprocess.run(
        [
            *os_network_sandbox,
            sys.executable,
            "-m",
            "zoo_kcl_check_cli",
            command,
            str(FIXTURES / fixture),
            "--json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert process.returncode == exit_code, (process.stdout, process.stderr)
    result = json.loads(process.stdout)
    assert result["exit_code"] == exit_code
    assert result["ok"] is (exit_code == 0)
    assert result["geometry_validated"] is False
    assert result["backend"]["version"] == "0.3.191"
    assert {path: path.read_bytes() for path in sources} == sources
