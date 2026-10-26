"""Running external programs for zedd.

Commands are always argument vectors executed without a shell.  Output is
streamed line by line to a callback (forwarded to clients as progress) and
to the transaction's log file.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO

from .errors import Cancelled
import contextlib

LineCallback = Callable[[str], None]


@dataclass
class Result:
    argv: list[str]
    returncode: int
    stdout: str
    stderr_tail: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CancelToken:
    """Set by zedd when a client disconnects or asks to cancel a plan."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


# Environment for child processes: fixed PATH, no inherited client data.
def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("ZED_CHILD_PATH", "/run/current-system/sw/bin:/run/wrappers/bin"),
        "LANG": "C.UTF-8",
        "HOME": "/root",
        "NIX_CONFIG": "experimental-features = nix-command flakes",
    }
    for k in ("ZED_ROOT", "NIX_REMOTE", "NIX_STATE_DIR", "NIX_STORE_DIR"):
        if k in os.environ:
            env[k] = os.environ[k]
    if extra:
        env.update(extra)
    return env


class Runner:
    """Executes commands.  Tests substitute a fake with the same interface."""

    def run(
        self,
        argv: Sequence[str],
        *,
        on_line: LineCallback | None = None,
        log: IO[str] | None = None,
        cancel: CancelToken | None = None,
        timeout: float | None = None,
        capture_stdout: bool = True,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Result:
        argv = [str(a) for a in argv]
        if log:
            log.write(f"$ {' '.join(argv)}\n")
            log.flush()
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=child_env(env),
            start_new_session=True,
            text=True,
            errors="replace",
        )
        sel = selectors.DefaultSelector()
        if proc.stdout:
            sel.register(proc.stdout, selectors.EVENT_READ, "out")
        if proc.stderr is None:  # pragma: no cover - stderr is always a pipe
            raise RuntimeError("stderr pipe missing")
        sel.register(proc.stderr, selectors.EVENT_READ, "err")
        out: list[str] = []
        tail: list[str] = []
        deadline = time.monotonic() + timeout if timeout else None
        open_streams = len(sel.get_map())
        try:
            while open_streams:
                if cancel and cancel.cancelled:
                    _terminate(proc)
                    raise Cancelled("cancelled before activation")
                if deadline and time.monotonic() > deadline:
                    _terminate(proc)
                    raise TimeoutError(f"{argv[0]} did not finish within {timeout:.0f}s")
                for key, _ in sel.select(timeout=0.5):
                    line = key.fileobj.readline()
                    if not line:
                        sel.unregister(key.fileobj)
                        open_streams -= 1
                        continue
                    if key.data == "out":
                        out.append(line)
                        continue
                    line = line.rstrip("\n")
                    tail.append(line)
                    del tail[:-200]
                    if log:
                        log.write(line + "\n")
                    if on_line:
                        on_line(line)
            rc = proc.wait()
        finally:
            sel.close()
            if proc.poll() is None:
                _terminate(proc)
        if log:
            log.write(f"[exit {rc}]\n")
            log.flush()
        return Result(argv=argv, returncode=rc, stdout="".join(out), stderr_tail=tail)


def _terminate(proc: subprocess.Popen) -> None:
    # The child runs in its own session; signal the whole group so that
    # builders started by nix are stopped too.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
