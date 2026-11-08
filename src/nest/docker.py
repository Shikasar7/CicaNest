"""The QQ containers (the Docker setup, deploy/mac/docker-compose.yml: a NapCat per account, or SnowLuma):
their state, and starting, stopping and restarting them through the `docker` command line.

Every function blocks (it runs `docker`): call them from a worker thread. Tests replace them, like the
process helpers in nest.system.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nest.system import ProcessError

# Where Docker Desktop, OrbStack and Homebrew put the command line. A launchd service starts with a bare
# PATH (/usr/bin:/bin:/usr/sbin:/sbin), so `which` alone would miss them.
KNOWN_PLACES = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "~/.orbstack/bin/docker",
    "/Applications/OrbStack.app/Contents/MacOS/xbin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)
# The state, the published ports (HostConfig's: they are known while the container is stopped too), the
# environment, for ACCOUNT: the QQ number a NapCat container logs in (deploy/mac/docker-compose.yml), and
# whether a 回退密码 is set (never its value; the others, WEBUI_TOKEN…, are never kept), and the labels,
# for the docker compose service a container belongs to.
INSPECT_FORMAT = (
    "{{json .State}}\t{{json .HostConfig.PortBindings}}\t{{json .Config.Env}}\t{{json .Config.Labels}}"
)
PASSWORD_VARIABLES = ("NAPCAT_QUICK_PASSWORD_MD5", "NAPCAT_QUICK_PASSWORD")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
INSPECT_TIMEOUT = 10.0
ACTION_TIMEOUT = 90.0  # `docker stop` waits up to 10 s for QQ to exit, then the start
# Where NapCat saves the login QR code while QQ waits for a scan (a new one about every 2 minutes).
QR_PATH = "/app/napcat/cache/qrcode.png"
QR_LIMIT = 256 * 1024  # bytes; NapCat's is well under 1 KB
QR_TIMEOUT = 10.0
LOG_LINES = 400  # of a container's output, read for why its QQ did not log in
CREATE_TIMEOUT = 600.0  # docker run, the first time with a 2 GB image download


@dataclass(frozen=True)
class Container:
    found: bool  # the container exists
    running: bool = False
    status: str = ""  # Docker's word: running / exited / restarting / paused / created / dead
    started_at: str = ""  # ISO time, when running
    health: str = ""  # healthy / unhealthy / starting, when the image has a health check
    problem: str = ""  # why nothing could be learned (no docker, Docker not running), in Chinese
    ports: tuple[tuple[str, int], ...] = ()  # published ports: ("6099/tcp", 6098) = container's 6099 on 6098
    account: str = ""  # the QQ number in its ACCOUNT variable (NapCat), when set
    quick_password: bool = False  # NapCat has a 回退密码 (NAPCAT_QUICK_PASSWORD[_MD5]) to log in with
    compose: Compose | None = None  # made by docker compose (the first three accounts), else None


@dataclass(frozen=True)
class Compose:
    """Where docker compose made a container from (its com.docker.compose.* labels)."""

    project: str
    service: str
    working_dir: str
    config_files: tuple[str, ...]


def valid_name(name: str) -> bool:
    return NAME.fullmatch(name) is not None


def cli() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for place in KNOWN_PLACES:
        path = Path(place).expanduser()
        if path.is_file():
            return str(path)
    return None


def _problem(stderr: str) -> str:
    text = stderr.strip()
    lowered = text.lower()
    if "cannot connect to the docker daemon" in lowered or "error during connect" in lowered:
        return "Docker 没有在运行：请先打开 OrbStack 或 Docker Desktop"
    if "permission denied" in lowered:
        return "没有权限使用 Docker"
    return f"docker 出错：{text.splitlines()[0] if text else '没有说明原因'}"


def container(name: str) -> Container:
    """What Docker knows about container `name`."""
    exe = cli()
    if exe is None:
        return Container(False, problem="找不到 docker 命令：请先安装 OrbStack 或 Docker Desktop")
    try:
        done = subprocess.run(
            [exe, "inspect", "--type", "container", "--format", INSPECT_FORMAT, name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSPECT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Container(False, problem="Docker 没有响应（超过 10 秒）")
    except OSError as exc:
        return Container(False, problem=f"运行 docker 失败：{exc.strerror or exc}")
    if done.returncode != 0:
        if "no such" in done.stderr.lower():
            return Container(False)
        return Container(False, problem=_problem(done.stderr))
    state_text, _, rest = done.stdout.strip().partition("\t")
    ports_text, _, rest = rest.partition("\t")
    env_text, _, labels_text = rest.partition("\t")
    try:
        state = json.loads(state_text)
        bindings = json.loads(ports_text) if ports_text else None
        env = json.loads(env_text) if env_text else None
        labels = json.loads(labels_text) if labels_text else None
    except ValueError:
        return Container(False, problem="看不懂 docker 的回答")
    if not isinstance(state, dict):
        return Container(False, problem="看不懂 docker 的回答")
    health = state.get("Health") or {}
    return Container(
        True,
        running=bool(state.get("Running")),
        status=str(state.get("Status") or ""),
        started_at=str(state.get("StartedAt") or ""),
        health=str(health.get("Status") or "") if isinstance(health, dict) else "",
        ports=_published(bindings),
        account=_variable(env, "ACCOUNT") if _variable(env, "ACCOUNT").isdigit() else "",
        quick_password=any(_variable(env, name) for name in PASSWORD_VARIABLES),
        compose=_compose(labels),
    )


def _variable(env: object, name: str) -> str:
    """The value of `name` in the container's environment (stripped), else ""."""
    for item in env if isinstance(env, list) else []:
        if isinstance(item, str) and item.startswith(f"{name}="):
            return item.removeprefix(f"{name}=").strip()
    return ""


def _compose(labels: object) -> Compose | None:
    if not isinstance(labels, dict):
        return None
    parts = [
        labels.get(f"com.docker.compose.{key}")
        for key in ("project", "service", "project.working_dir", "project.config_files")
    ]
    if not all(isinstance(part, str) and part for part in parts):
        return None
    project, service, working_dir, files = (str(part) for part in parts)
    return Compose(project, service, working_dir, tuple(f for f in files.split(",") if f))


def _published(bindings: object) -> tuple[tuple[str, int], ...]:
    """("6099/tcp", 6098) pairs from HostConfig.PortBindings, the first host port of each."""
    if not isinstance(bindings, dict):
        return ()
    found = []
    for inside, hosts in sorted(bindings.items()):
        for host in hosts if isinstance(hosts, list) else []:
            port = str((host or {}).get("HostPort") or "")
            if port.isdigit():
                found.append((str(inside), int(port)))
                break
    return tuple(found)


def act(name: str, action: str) -> None:
    """`docker start|stop|restart <name>`; ProcessError with one Chinese sentence when it fails."""
    if action not in ("start", "stop", "restart"):
        raise ValueError(action)
    exe = cli()
    if exe is None:
        raise ProcessError("找不到 docker 命令：请先安装 OrbStack 或 Docker Desktop")
    try:
        done = subprocess.run(
            [exe, action, name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ACTION_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProcessError(f"docker {action} 超过 {ACTION_TIMEOUT:.0f} 秒还没有完成") from None
    except OSError as exc:
        raise ProcessError(f"运行 docker 失败：{exc.strerror or exc}") from None
    if done.returncode != 0:
        if "no such" in done.stderr.lower():
            raise ProcessError(f"Docker 里没有叫 {name} 的容器：先在 deploy/mac 里 docker compose up -d")
        raise ProcessError(_problem(done.stderr))


def login_qr(name: str) -> tuple[bytes, int] | None:
    """The login QR code NapCat saved in container `name` (PNG bytes) and its age in seconds; None when there
    is none (QQ has not asked for a scan since the container started, or it is not NapCat). ProcessError with
    one Chinese sentence when docker fails."""
    exe = cli()
    if exe is None:
        raise ProcessError("找不到 docker 命令：请先安装 OrbStack 或 Docker Desktop")
    script = f'f={QR_PATH}; [ -f "$f" ] || exit 3; echo $(( $(date +%s) - $(stat -c %Y "$f") )); cat "$f"'
    try:
        done = subprocess.run(
            [exe, "exec", name, "sh", "-c", script], capture_output=True, timeout=QR_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        raise ProcessError(f"Docker 没有响应（超过 {QR_TIMEOUT:.0f} 秒）") from None
    except OSError as exc:
        raise ProcessError(f"运行 docker 失败：{exc.strerror or exc}") from None
    if done.returncode == 3:
        return None
    if done.returncode != 0:
        raise ProcessError(_problem(done.stderr.decode("utf-8", "replace")))
    age, _, image = done.stdout.partition(b"\n")
    if not age.strip().lstrip(b"-").isdigit() or not image.startswith(b"\x89PNG") or len(image) > QR_LIMIT:
        raise ProcessError("容器里的二维码图片看不懂")
    return image, max(0, int(age))


def login_log(name: str) -> str:
    """The end of container `name`'s output (NapCat's log, both streams), for why QQ did not log in.
    ProcessError with one Chinese sentence when docker fails."""
    exe = cli()
    if exe is None:
        raise ProcessError("找不到 docker 命令：请先安装 OrbStack 或 Docker Desktop")
    try:
        done = subprocess.run(
            [exe, "logs", "--tail", str(LOG_LINES), name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=QR_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProcessError(f"Docker 没有响应（超过 {QR_TIMEOUT:.0f} 秒）") from None
    except OSError as exc:
        raise ProcessError(f"运行 docker 失败：{exc.strerror or exc}") from None
    if done.returncode != 0:
        raise ProcessError(_problem(done.stdout))
    return done.stdout


def names() -> list[str]:
    """Every container's name, running or not. ProcessError when docker fails."""
    return [
        line for line in _run(["ps", "-a", "--format", "{{.Names}}"], INSPECT_TIMEOUT).splitlines() if line
    ]


def create(args: list[str]) -> None:
    """`docker run …` (nest.napcat.run_args): may download the image first, so it gets longer."""
    _run(args, CREATE_TIMEOUT)


def remove(name: str) -> None:
    """Stop and delete the container (its data folders, mounted from the Mac, stay)."""
    _run(["rm", "-f", name], ACTION_TIMEOUT)


def compose_up(compose: Compose) -> None:
    """`docker compose up -d` for that one service: recreates its container when its settings changed (a
    回退密码 in the compose project's .env), with the same name, host name and MAC address."""
    files = [arg for path in compose.config_files for arg in ("-f", path)]
    project = ["-p", compose.project, "--project-directory", compose.working_dir, *files]
    _run(["compose", *project, "up", "-d", "--no-deps", compose.service], CREATE_TIMEOUT)


def _run(args: list[str], timeout: float) -> str:
    exe = cli()
    if exe is None:
        raise ProcessError("找不到 docker 命令：请先安装 OrbStack 或 Docker Desktop")
    try:
        done = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ProcessError(f"docker {args[0]} 超过 {timeout:.0f} 秒还没有完成") from None
    except OSError as exc:
        raise ProcessError(f"运行 docker 失败：{exc.strerror or exc}") from None
    if done.returncode != 0:
        raise ProcessError(_problem(done.stderr))
    return done.stdout
