import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from zoo_kcl_check_cli import runner
from zoo_kcl_check_cli.models import Request, Result


def sleepy_worker(request, connection):
    Path(request.path).write_text(str(os.getpid()))
    time.sleep(30)


def crashing_worker(request, connection):
    os._exit(23)


def empty_worker(request, connection):
    connection.close()


def large_worker(request, connection):
    result = Result(request.command)
    result.formatting = [
        {"file": request.path, "changed": True, "written": False, "formatted_code": "x" * 8_000_000}
    ]
    connection.send(("result", result))
    connection.close()


def result_then_crash_worker(request, connection):
    connection.send(("result", Result(request.command)))
    os._exit(7)


def unfinished_result_worker(request, connection):
    connection.send(("result", Result(request.command)))
    time.sleep(30)


def write_then_hang_worker(request, connection):
    result = Result(request.command)
    result.formatting = [
        {"file": request.path, "changed": True, "written": False, "formatted_code": None}
    ]
    result.pending_write = (request.path, "formatted\n")
    connection.send(("progress", result))
    Path(request.path).write_text("formatted\n")
    time.sleep(30)


def raw_output_worker(request, connection):
    from zoo_kcl_check_cli import core

    def noisy(request, emit=None):
        os.write(1, b"native output\n")
        print("python output", flush=True)
        assert "ZOO_API_TOKEN" not in os.environ
        assert "KITTYCAD_API_TOKEN" not in os.environ
        return Result(request.command)

    core.run_request = noisy
    runner._worker(request, connection)


def _assert_stopped(pid_file):
    assert pid_file.exists(), "worker never reached the test scenario"
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert all(child.pid != pid for child in multiprocessing.active_children())


def test_timeout(tmp_path):
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    result = runner.run(Request("lint", str(pid_file)), 1, worker_target=sleepy_worker)
    assert result.exit_code == 2
    assert "timed out" in result.diagnostics[-1].message
    assert time.monotonic() - started < 4
    _assert_stopped(pid_file)


@pytest.mark.parametrize("worker", [crashing_worker, empty_worker, result_then_crash_worker])
def test_worker_crash(worker):
    result = runner.run(Request("lint", "unused"), 5, worker_target=worker)
    assert result.exit_code == 2
    assert result.diagnostics[-1].origin == "tool"
    assert not result.to_dict()["ok"]


def test_large_worker_result():
    result = runner.run(Request("format", "unused"), 10, worker_target=large_worker)
    assert result.exit_code == 0
    assert len(result.formatting[0]["formatted_code"]) == 8_000_000


def test_worker_hangs_after_result():
    result = runner.run(Request("lint", "unused"), 1, worker_target=unfinished_result_worker)
    assert result.exit_code == 2
    assert "timed out" in result.diagnostics[-1].message


def test_interrupt_cleanup(tmp_path):
    pid_file = tmp_path / "pid"

    def interrupt_when_ready():
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGINT)

    interrupter = threading.Thread(target=interrupt_when_ready)
    interrupter.start()
    try:
        result = runner.run(Request("lint", str(pid_file)), 10, worker_target=sleepy_worker)
    finally:
        interrupter.join(timeout=5)
    assert result.exit_code == 130
    assert result.to_dict()["ok"] is False
    _assert_stopped(pid_file)


def test_timeout_reports_committed_write(tmp_path):
    file = tmp_path / "changed.kcl"
    file.write_text("original")
    result = runner.run(
        Request("format", str(file), write=True), 1, worker_target=write_then_hang_worker
    )
    assert result.exit_code == 2
    assert file.read_text() == "formatted\n"
    assert result.formatting[0]["written"] is True
    assert result.pending_write is None
    assert "pending_write" not in result.to_dict()


def test_expired_deadline_does_not_spawn(monkeypatch):
    def unexpected_start(self):
        raise AssertionError("must not spawn after deadline")

    monkeypatch.setattr(multiprocessing.context.SpawnProcess, "start", unexpected_start)
    result = runner.run(Request("lint", "unused"), deadline=time.monotonic() - 1)
    assert result.exit_code == 2
    assert "timed out" in result.diagnostics[-1].message


def test_spawn_failure_is_tool_error(monkeypatch):
    def denied(self):
        raise OSError("cannot spawn")

    monkeypatch.setattr(multiprocessing.context.SpawnProcess, "start", denied)
    result = runner.run(Request("lint", "unused"))
    assert result.exit_code == 2
    assert "cannot spawn" in result.diagnostics[-1].message


def test_worker_redirects_native_output_and_removes_tokens(monkeypatch, capfd):
    monkeypatch.setenv("ZOO_API_TOKEN", "test-only")
    monkeypatch.setenv("KITTYCAD_API_TOKEN", "test-only")
    result = runner.run(Request("lint", "unused"), worker_target=raw_output_worker)
    assert result.exit_code == 0
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "native output" in captured.err
    assert "python output" in captured.err
    assert os.environ["ZOO_API_TOKEN"] == "test-only"


def test_interrupt_during_cleanup_is_deferred(monkeypatch, tmp_path):
    original_stop = runner._stop
    interrupted = False

    def stop_and_interrupt(process):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            os.kill(os.getpid(), signal.SIGINT)
        original_stop(process)

    monkeypatch.setattr(runner, "_stop", stop_and_interrupt)
    pid_file = tmp_path / "pid"
    result = runner.run(Request("lint", str(pid_file)), 1, worker_target=sleepy_worker)
    assert result.exit_code == 130
    _assert_stopped(pid_file)


def test_cli_handles_interrupt_with_json(monkeypatch, capsys):
    from zoo_kcl_check_cli import cli

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupt)
    assert cli.main(["lint", "unused.kcl", "--json"]) == 130
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 130
    assert data["command"] == "lint"
    assert not data["ok"]
