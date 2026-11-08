"""The local programs nest starts for the user: SnowLuma (the QQ ↔ OneBot bridge) and the QQ client.

Every process operation lives here so tests can replace these functions and never touch a real
process. All of them block (process table walks, waits): call them from a worker thread.

SnowLuma hooks QQ clients that are already logged in; restarting it never logs QQ out, so stopping it
ends SnowLuma's own process tree and leaves every QQ.exe running.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

import psutil

# No default folder: QQ usually runs in Docker now. A SnowLuma folder can still be set on the 系统 page.
DEFAULT_SNOWLUMA_DIR = ""
DEFAULT_QQ_EXECUTABLE = r"C:\Program Files\Tencent\QQNT\QQ.exe"
WEBUI_HOST, WEBUI_PORT = "127.0.0.1", 5099
WEBUI_URL = f"http://{WEBUI_HOST}:{WEBUI_PORT}"
SNOWLUMA_ENV = {
    "SNOWLUMA_ACCEPT_EULA": "1",
    "SNOWLUMA_ACCEPT_PRIVACY": "1",
    "SNOWLUMA_HOOK_AUTOLOAD": "1",
    "SNOWLUMA_WEBUI_HOST": WEBUI_HOST,
}
NODE, SCRIPT, QQ = "node.exe", "index.mjs", "qq.exe"
START_GRACE = 1.0  # a SnowLuma that exits within this long did not start (bad config, port taken)
STOP_TIMEOUT = 5.0
PORT_RELEASE_TIMEOUT = 2.0


class ProcessError(Exception):
    """A start or stop that did not work; the message is one plain Chinese sentence for the console."""


# -- paths ------------------------------------------------------------------------------------------


def is_snowluma_dir(path: str | Path) -> bool:
    if not str(path):
        return False
    folder = Path(path)
    return (folder / NODE).is_file() and (folder / SCRIPT).is_file()


def is_qq_executable(path: str | Path) -> bool:
    if not str(path):
        return False
    exe = Path(path)
    return exe.suffix.lower() == ".exe" and exe.is_file()


def default_snowluma_dir() -> str:
    return DEFAULT_SNOWLUMA_DIR if is_snowluma_dir(DEFAULT_SNOWLUMA_DIR) else ""


def default_qq_executable() -> str:
    return DEFAULT_QQ_EXECUTABLE if is_qq_executable(DEFAULT_QQ_EXECUTABLE) else ""


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# -- finding processes ------------------------------------------------------------------------------


def runs_snowluma(folder: str | Path, cmdline: Sequence[str], cwd: str | None, exe: str | None) -> bool:
    """Whether a node.exe with this command line runs `<folder>\\index.mjs`.

    A relative script (`node.exe index.mjs`, as SnowLuma's launcher runs it) is resolved against the
    process's working directory, or the folder of its node.exe when that cannot be read.
    """
    script = Path(folder) / SCRIPT
    base = cwd or (str(Path(exe).parent) if exe else None)
    for arg in cmdline[1:]:
        arg = arg.strip().strip('"')
        if Path(arg).name.lower() != SCRIPT:
            continue
        path = Path(arg)
        if not path.is_absolute():
            if base is None:
                continue
            path = Path(base) / path
        if _same_path(path, script):
            return True
    return False


def _named(name: str) -> list[psutil.Process]:
    # Names only for the whole table. Anything more per process is slow on Windows: ppid() alone takes
    # a full process snapshot each time (seconds for ~700 processes), so it is asked only of a few.
    return [p for p in psutil.process_iter(["name"], ad_value=None) if (p.info["name"] or "").lower() == name]


def _parent(proc: psutil.Process) -> int | None:
    try:
        return proc.ppid()
    except psutil.Error:
        return None


def snowluma_pids(folder: str | Path) -> list[int]:
    """The SnowLuma processes running from `folder` (only the top of each tree)."""
    if not str(folder):
        return []
    matches = []
    for proc in _named(NODE):
        try:
            cmdline = proc.cmdline()
        except (psutil.Error, OSError):
            continue
        exe = cwd = None
        with contextlib.suppress(psutil.Error, OSError):
            exe = proc.exe()
        with contextlib.suppress(psutil.Error, OSError):
            cwd = proc.cwd()
        if runs_snowluma(folder, cmdline, cwd, exe):
            matches.append(proc)
    pids = {proc.pid for proc in matches}
    return sorted(proc.pid for proc in matches if _parent(proc) not in pids)


def webui_listening(timeout: float = 0.3) -> bool:
    """Something accepts connections on SnowLuma's WebUI port: running, even when its process cannot be
    identified (another user's or an elevated process), so nest never starts a second one."""
    try:
        with socket.create_connection((WEBUI_HOST, WEBUI_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def qq_clients() -> int:
    """Running QQ clients. Each runs many QQ.exe processes; all but its main one are Chromium helpers
    started with --type=. One whose command line cannot be read counts when its parent is not a QQ.exe."""
    procs = _named(QQ)
    pids = {proc.pid for proc in procs}
    count = 0
    for proc in procs:
        try:
            helper = any(arg.startswith("--type=") for arg in proc.cmdline())
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError):
            helper = _parent(proc) in pids
        count += not helper
    return count


def memory_mb() -> float:
    """This process's memory (the working set on Windows)."""
    return round(psutil.Process().memory_info().rss / 2**20, 1)


# -- starting and stopping --------------------------------------------------------------------------


def _child_env(extra: dict[str, str], hide: Iterable[str]) -> dict[str, str]:
    """nest's environment without its own settings and secrets (keys may be environment variables)."""
    hidden = {name.upper() for name in hide if name}
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("NEST_") and name.upper() not in hidden
    }
    return env | extra


def _spawn(args: list[str], *, cwd: Path, env: dict[str, str], windowless: bool) -> subprocess.Popen:
    """Start a program that outlives nest: detached, its own process group, no inherited handles."""
    kwargs: dict[str, Any] = {}
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if windowless:
            flags |= subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    spawn: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        **kwargs,
    }
    if os.name == "nt":
        # Leave a job object nest may have been started in (one that ends its processes when the
        # launcher exits); not every job allows that, so fall back to starting inside it.
        try:
            return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **spawn)
        except PermissionError:
            pass
    return subprocess.Popen(args, creationflags=flags, **spawn)


def start_snowluma(folder: str | Path, *, hide: Collection[str] = ()) -> int:
    """Start SnowLuma from `folder` (detached, no window) and return its pid. `hide`: environment
    variable names it must not inherit (nest's secret names)."""
    folder = Path(folder)
    try:
        proc = _spawn(
            [str(folder / NODE), SCRIPT], cwd=folder, env=_child_env(SNOWLUMA_ENV, hide), windowless=True
        )
    except OSError as exc:
        raise ProcessError(f"SnowLuma 没能启动：{exc.strerror or type(exc).__name__}") from None
    try:
        code = proc.wait(START_GRACE)
    except subprocess.TimeoutExpired:
        return proc.pid
    raise ProcessError(f"SnowLuma 启动后马上退出了（退出码 {code}），请查看 {folder / 'logs'} 里的日志")


def tree(root: Any) -> list[Any]:
    """`root` and its descendants, children before parents, without any QQ.exe and what runs under it:
    a QQ client started by SnowLuma keeps running (and stays logged in)."""
    found: list[Any] = []

    def walk(proc: Any) -> None:
        try:
            children = proc.children()
        except psutil.Error:
            children = []
        for child in children:
            try:
                if child.name().lower() == QQ:
                    continue
            except psutil.Error:
                continue
            walk(child)
        found.append(proc)

    walk(root)
    return found


def stop_snowluma(pids: Iterable[int]) -> bool:
    """End each SnowLuma process tree (never QQ). False when none of them was running any more."""
    victims: list[psutil.Process] = []
    for pid in pids:
        try:
            victims.extend(tree(psutil.Process(pid)))
        except psutil.NoSuchProcess:
            continue
    if not victims:
        return False
    for proc in victims:  # children first: a parent must not get the chance to start new ones
        with contextlib.suppress(psutil.Error):
            proc.kill()
    _, alive = psutil.wait_procs(victims, timeout=STOP_TIMEOUT)
    if alive:
        raise ProcessError(f"SnowLuma 没能停止（进程 {alive[0].pid} 结束不了），请在任务管理器里结束它")
    deadline = time.monotonic() + PORT_RELEASE_TIMEOUT
    while webui_listening(0.1) and time.monotonic() < deadline:  # a restart binds the same port
        time.sleep(0.1)
    return True


def open_qq(executable: str | Path, *, hide: Collection[str] = ()) -> None:
    """Start a QQ client (detached); the user logs in to it."""
    exe = Path(executable)
    try:
        _spawn([str(exe)], cwd=exe.parent, env=_child_env({}, hide), windowless=False)
    except OSError as exc:
        raise ProcessError(f"QQ 没能打开：{exc.strerror or type(exc).__name__}") from None
