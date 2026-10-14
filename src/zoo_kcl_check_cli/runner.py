"""Spawned native worker with a deadline independent of the Rust runtime."""

import multiprocessing
import os
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .models import Diagnostic, Request, Result


def _worker(request: Request, connection) -> None:
    result = Result(request.command)

    def progress(value: Result) -> None:
        nonlocal result
        result = value
        connection.send(("progress", value))

    try:
        # The parent owns Ctrl-C and cleans up the native worker.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for name in ("ZOO_API_TOKEN", "KITTYCAD_API_TOKEN"):
            os.environ.pop(name, None)
        # dup2 also redirects Rust/C output, which Python redirect_stdout cannot do.
        sys.stdout.flush()
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        from .core import run_request

        result = run_request(request, emit=progress)
        connection.send(("result", result))
    except BaseException as error:
        result.add(
            Diagnostic(
                "fatal",
                "tool",
                f"Worker failure ({type(error).__name__}): {error}",
                exit_code=2,
            )
        )
        try:
            connection.send(("result", result))
        except (OSError, EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


def _receive(connection, messages: queue.Queue) -> None:
    # Reading in a thread lets the deadline remain effective even for a partial
    # pipe frame. The parent drains results before joining the sending process.
    try:
        while True:
            message = connection.recv()
            messages.put(message)
            if isinstance(message, tuple) and message[0] == "result":
                return
    except (EOFError, OSError) as error:
        messages.put(("closed", str(error)))
    except BaseException as error:
        messages.put(("invalid", f"Invalid worker response: {error}"))


def _stop(process) -> None:
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=0.5)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.5)


def _reconcile_write(result: Result) -> None:
    if result.pending_write is None:
        return
    file, formatted = result.pending_write
    try:
        if Path(file).read_bytes() == formatted.encode("utf-8"):
            for entry in result.formatting or []:
                if entry["file"] == file:
                    entry["written"] = True
    except OSError:
        pass
    result.pending_write = None


def run(
    request: Request,
    timeout: float = 60,
    *,
    deadline: float | None = None,
    worker_target: Callable = _worker,
) -> Result:
    """Return the latest complete progress snapshot even on timeout or interruption."""
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    result = Result(request.command)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker_target, args=(request, sender), daemon=True)
    messages: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_receive, args=(receiver, messages), daemon=True)
    final = False
    failure: Diagnostic | None = None
    interrupted = False
    try:
        if time.monotonic() >= deadline:
            failure = Diagnostic(
                "fatal", "tool", f"Command timed out after {timeout:g}s", exit_code=2
            )
        else:
            process.start()
            sender.close()
            reader.start()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = Diagnostic(
                        "fatal", "tool", f"Command timed out after {timeout:g}s", exit_code=2
                    )
                    break
                if final:
                    process.join(timeout=min(remaining, 0.05))
                    if not process.is_alive():
                        if process.exitcode != 0:
                            failure = Diagnostic(
                                "fatal",
                                "tool",
                                f"Worker exited unexpectedly (exit code {process.exitcode})",
                                exit_code=2,
                            )
                        break
                    continue
                try:
                    kind, value = messages.get(timeout=min(remaining, 0.05))
                except queue.Empty:
                    continue
                if kind in ("progress", "result") and isinstance(value, Result):
                    result = value
                    final = kind == "result"
                else:
                    failure = Diagnostic(
                        "fatal",
                        "tool",
                        f"Worker exited without a result (exit code {process.exitcode})"
                        if kind == "closed"
                        else "Invalid worker response",
                        exit_code=2,
                    )
                    break
    except KeyboardInterrupt:
        interrupted = True
        failure = Diagnostic("fatal", "tool", "Interrupted by user", exit_code=130)
    except Exception as error:
        failure = Diagnostic("fatal", "tool", f"Cannot run worker: {error}", exit_code=2)
    finally:

        def defer_interrupt(signum, frame):
            nonlocal interrupted
            interrupted = True

        previous_handler = None
        try:
            previous_handler = signal.signal(signal.SIGINT, defer_interrupt)
        except ValueError:
            pass  # Library callers can run from a thread; signals go to the main thread.
        try:
            try:
                _stop(process)
            except KeyboardInterrupt:
                interrupted = True
                _stop(process)
            sender.close()
            receiver.close()
            if reader.ident is not None:
                reader.join(timeout=0.2)
            # A completed replacement may have happened just before interruption.
            _reconcile_write(result)
            if process.pid is not None:
                process.close()
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
    if failure is not None:
        result.add(failure)
    if interrupted:
        if failure is None or failure.exit_code != 130:
            result.add(Diagnostic("fatal", "tool", "Interrupted by user", exit_code=130))
        result.exit_code = 130
    return result
