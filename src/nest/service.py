"""nest as a macOS background service (a launchd agent): started when the user logs in, restarted when it
crashes, left alone when it is stopped on purpose (`nest stop` ends it with exit code 0, and the agent only
restarts after a failure).

A per-user agent rather than a system daemon: Docker (SnowLuma's QQ) also runs in the logged-in session, so
a Mac serving nest is set to log in automatically after a restart.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

LABEL = "com.nest.server"
THROTTLE_SECONDS = 10  # launchd waits at least this long between restarts

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=30, check=False)


def supported() -> bool:
    return sys.platform == "darwin"


def plist_path(home_dir: Path | None = None) -> Path:
    return (home_dir or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def domain() -> str:
    return f"gui/{os.getuid()}"  # type: ignore[attr-defined,unused-ignore]  # POSIX only


def agent(nest_home: Path, python: str) -> dict[str, object]:
    """The launchd job: `python -m nest serve --quiet` in `nest_home`, its output next to nest's own log."""
    logs = nest_home / "data" / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "nest", "serve", "--quiet"],
        "WorkingDirectory": str(nest_home),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": THROTTLE_SECONDS,
        "ProcessType": "Standard",
        "StandardOutPath": str(logs / "launchd.log"),
        "StandardErrorPath": str(logs / "launchd.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            # launchd's PATH lacks these: the docker command line (and its helpers) lives there.
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str


def install(
    nest_home: Path, *, python: str = sys.executable, run: Runner = _run, home_dir: Path | None = None
) -> Result:
    """Write the agent and load it (replacing an older one). nest starts right away."""
    if not supported():
        return Result(False, "后台服务只在 macOS 上可用；Windows 请用桌面应用或 启动.bat")
    path = plist_path(home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    (nest_home / "data" / "logs").mkdir(parents=True, exist_ok=True)
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"])  # an older version, if loaded; failing is fine
    path.write_bytes(plistlib.dumps(agent(nest_home, python)))
    done = run(["launchctl", "bootstrap", domain(), str(path)])
    if done.returncode != 0:
        return Result(False, f"launchctl 没能加载 nest 服务：{(done.stderr or done.stdout).strip()}")
    return Result(True, f"已安装：登录这台 Mac 后 nest 会自动启动，崩溃后自动重启。配置在 {path}")


def uninstall(*, run: Runner = _run, home_dir: Path | None = None) -> Result:
    """Unload the agent (stopping nest) and remove it."""
    if not supported():
        return Result(False, "后台服务只在 macOS 上可用")
    path = plist_path(home_dir)
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"])
    if path.exists():
        path.unlink()
        return Result(True, "已卸载：nest 已停止，以后不会自动启动")
    return Result(True, "没有安装过 nest 服务")


def installed(home_dir: Path | None = None) -> bool:
    return supported() and plist_path(home_dir).exists()


def kickstart(*, run: Runner = _run) -> bool:
    """Start the installed agent's job now (it is not running after `nest stop`)."""
    return run(["launchctl", "kickstart", f"{domain()}/{LABEL}"]).returncode == 0
