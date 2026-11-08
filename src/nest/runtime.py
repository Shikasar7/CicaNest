"""Process bookkeeping: data/runtime.json (port, pid, token), the one-instance lock, process liveness."""

from __future__ import annotations

import contextlib
import json
import os
import signal
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

import psutil


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    pid: int
    host: str
    port: int
    token: str
    started_at: float
    version: str

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self)), encoding="utf-8")
        os.replace(temp, path)  # atomic: readers never see a half-written file

    @classmethod
    def read(cls, path: Path) -> RuntimeInfo | None:
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def remove_if_owned(path: Path, pid: int) -> None:
        current = RuntimeInfo.read(path)
        if current is not None and current.pid == pid:
            path.unlink(missing_ok=True)


class InstanceLock:
    """Exclusive OS lock on <data>/nest.lock, held for the life of the process: one nest per data directory.

    Two processes on the same accounts would both answer every message once a bot speaks for real.
    The OS drops the lock when the process dies, so a crash never leaves it stuck.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 (kept open while the lock is held)
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        with contextlib.suppress(OSError):
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        self._file.close()
        self._file = None

    def held_elsewhere(self) -> bool:
        if self.acquire():
            self.release()
            return False
        return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, just not ours to query
        try:
            code = wintypes.DWORD()
            still_active = 259
            return (
                bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE  # ended, not yet reaped by its parent
    except psutil.Error:
        return False


def terminate(pid: int) -> None:
    """Forcefully end a process (TerminateProcess on Windows, SIGKILL elsewhere: a hung nest may never act
    on SIGTERM)."""
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
