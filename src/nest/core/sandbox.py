"""她的容器 (伙伴模式): commands a companion runs go into a Docker container of hers, never onto
this machine itself.

One long-lived container per companion (nest-ws-<her folder>), made from the local image nest-workspace
(built once from Docker Hub's official python slim image, see DOCKERFILE). Only her workspace folder is
mounted (at /work); not root; no added capabilities; its own system read-only (scratch space small and in
memory); limited CPU, memory and processes; a command that pushes her folder past the workspace's limit is
stopped. Each command has a
time limit (TIMEOUT, at most MAX_TIMEOUT) enforced inside the container by `timeout`, and its output is cut
to OUTPUT_CHARS. A container is started when she runs something and stopped after IDLE_SECONDS without a
command, so a small machine does not carry it all day.

联网: online (the default, per companion), her container is on an internal network of her
own (nest-net-<her folder>) — no way out of it but the proxy container EGRESS (nest.core.egress_proxy),
which refuses this machine and the local network and logs every host under her name, known by the network
the connection arrived on (NETWORK_MAP). Offline, it has no network at all. What she installs with pip goes
into /work/.local (PYTHONUSERBASE), so it stays when the container is made again (a switch of the network
does that).

插件的容器: an MCP server a plugin brings runs in a container of its own per companion
(nest-mcp-<her folder>-<plugin id>), never in hers: it sees only its own plugin folder (read-only, at
/plugins/<id>) and a folder of its own for what it installs (at /data, kept under plugin-homes/<her
folder>/<id>), not her workspace and not the other plugins. Its secrets are handed to it alone, so neither
her commands nor another plugin can read them. It goes online or not with her switch, through the same
proxy, logged under her name.

nest reaches them with `docker exec`, not over the network. Tests hand in their own `docker` (an async
function taking the arguments and a time limit, returning (exit code, output)).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from nest import docker
from nest.core.workspace import Workspace, folder_name

log = logging.getLogger(__name__)

# The tag says which Dockerfile it was built from: a new one is built when it changes (2: Node and a few
# command-line tools).
IMAGE = "nest-workspace:2"
BASE = "python:3.12-slim"
NODE = "node:22-bookworm-slim"  # Docker Hub's official Node image: only its node and npm are copied over
DOCKERFILE = f"""FROM {NODE} AS node
FROM {BASE}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update \
 && apt-get install -y --no-install-recommends git curl jq ca-certificates unzip \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 she
USER she
WORKDIR /work
ENV PATH=/home/she/.local/bin:$PATH PYTHONDONTWRITEBYTECODE=1
"""
TIMEOUT = 120
MAX_TIMEOUT = 600
OUTPUT_CHARS = 12_000
IDLE_SECONDS = 600.0
CPUS = "2"
MEMORY = "1g"  # OrbStack gives its Linux about 4 GB, shared with the QQ containers
PROCESSES = "256"
BUILD_TIMEOUT = 600.0
DOCKER_TIMEOUT = 60.0
COMMAND_MAX = 4000
VERSION = "6"  # of how her container is made: an older one is made again (4: proxy without a password,
# a writable system; 5: before the image had Node)
PLUGIN_VERSION = "3"  # of how a plugin's container is made (1: proxy without a password, writable;
# 2: before the image had Node)
PLUGIN_CPUS = "1"
PLUGIN_MEMORY = "512m"
PLUGIN_PROCESSES = "128"
# Her container's own system is read-only: what she writes goes into /work (counted against the
# workspace's limit) or into these, in memory and small.
SCRATCH = ("/tmp:size=256m", "/home/she:size=256m,uid=1000,gid=1000", "/var/tmp:size=64m")
WATCH_SECONDS = 5.0  # how often her folder is measured while a command of hers runs
# Each companion has an internal network of her own (her container and her plugins' containers on it,
# the proxy joined to each): companions cannot reach each other's containers, and the proxy tells who
# connects by the network it was reached on, not by what the program says.
NETWORK_PREFIX = "nest-net-"
NETWORK_MAP = "networks.json"  # in the proxy's log folder: its address on each network -> whose
CONTAINER_MAP = "containers.json"  # there too: each container's address on those networks -> its name
EGRESS = "nest-egress"
EGRESS_SCRIPT = Path(__file__).with_name("egress_proxy.py")
EGRESS_PORT = 3128

# (arguments, time limit[, what to feed it]) -> (exit code, output)
Docker = Callable[..., Awaitable[tuple[int, str]]]
# (docker arguments, environment to add) -> a process with stdin and stdout pipes (an MCP server's)
Spawner = Callable[[list[str], dict[str, str]], Awaitable[asyncio.subprocess.Process]]
LINE_LIMIT = 8 * 1024 * 1024


async def spawn_docker(args: list[str], env: dict[str, str]) -> asyncio.subprocess.Process:
    """`docker <args>` left running, talking over pipes. Values in `env` reach the container only as the
    `--env NAME` arguments name them (never on the command line)."""
    exe = docker.cli()
    if exe is None:
        raise SandboxError("这台电脑上没有 Docker")
    try:
        return await asyncio.create_subprocess_exec(
            exe,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **env},
            limit=LINE_LIMIT,
        )
    except OSError as exc:
        raise SandboxError(f"运行 docker 失败：{exc.strerror or exc}") from None


class SandboxError(Exception):
    """Why the command could not be run, in one Chinese sentence."""


async def run_docker(args: list[str], seconds: float, stdin: bytes | None = None) -> tuple[int, str]:
    """`docker <args>`: (exit code, stdout and stderr together). SandboxError when docker cannot be run."""
    exe = docker.cli()
    if exe is None:
        raise SandboxError("这台电脑上没有 Docker，跑不了命令")
    try:
        process = await asyncio.create_subprocess_exec(
            exe,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise SandboxError(f"运行 docker 失败：{exc.strerror or exc}") from None
    try:
        out, _ = await asyncio.wait_for(process.communicate(stdin), seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        raise SandboxError(f"docker {args[0]} 超过 {seconds:.0f} 秒没有完成") from None
    return process.returncode or 0, out.decode("utf-8", errors="replace")


def _last_line(out: str) -> str:
    lines = out.strip().splitlines()
    return lines[-1][:300] if lines else "没有说明原因"


def container_name(bot_id: str) -> str:
    return f"nest-ws-{folder_name(bot_id)}"[:120]


def plugin_container_name(bot_id: str, plugin_id: str) -> str:
    """nest-mcp-<her folder>-<plugin id>: a plugin's container for one companion."""
    name = f"nest-mcp-{folder_name(bot_id)}-{re.sub(r'[^a-z0-9.-]', '-', plugin_id.lower())}"
    return name if len(name) <= 120 else name[:107] + "-" + hashlib.sha1(name.encode()).hexdigest()[:12]


def network_name(bot_id: str) -> str:
    """nest-net-<her folder>: her internal network."""
    return NETWORK_PREFIX + folder_name(bot_id)


def proxy_url(bot_id: str) -> str:
    """The proxy, with her folder name as the user: the label her connections are logged under. The
    password means nothing, but without one Python (urllib, pip) sends no user to the proxy at all."""
    return f"http://{folder_name(bot_id)}:nest@{EGRESS}:{EGRESS_PORT}"


def _container_args(
    name: str,
    bot_id: str,
    *,
    online: bool,
    env: dict[str, str],
    labels: dict[str, str],
    mounts: list[str],
    workdir: str,
    limits: tuple[str, str, str] = (CPUS, MEMORY, PROCESSES),
    scratch: tuple[str, ...] = SCRATCH,
) -> list[str]:
    """`docker run` for a container of hers: only what `mounts` names, out only through the proxy (or not
    at all), not root, limited."""
    proxy = proxy_url(bot_id)
    env = {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": "",
        **env,
    }
    cpus, memory, processes = limits
    return [
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        network_name(bot_id) if online else "none",
        *(arg for key, value in env.items() for arg in ("--env", f"{key}={value}")),
        "--cpus",
        cpus,
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--pids-limit",
        processes,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "1000:1000",
        "--read-only",
        *(arg for place in scratch for arg in ("--tmpfs", place)),
        *(arg for key, value in labels.items() for arg in ("--label", f"{key}={value}")),
        *(arg for mount in mounts for arg in ("--volume", mount)),
        "--workdir",
        workdir,
        IMAGE,
        "sleep",
        "infinity",
    ]


def run_args(bot_id: str, folder: Path, *, online: bool) -> list[str]:
    """`docker run` for her container: only her folder."""
    return _container_args(
        container_name(bot_id),
        bot_id,
        online=online,
        env={
            "PYTHONUSERBASE": "/work/.local",
            "npm_config_prefix": "/work/.local",
            "PATH": "/work/.local/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        labels={"nest.workspace": VERSION, "nest.online": str(int(online))},
        mounts=[f"{folder}:/work"],
        workdir="/work",
    )


def plugin_stamp(folder: Path) -> str:
    """Which install of a plugin folder this is: an upgrade or a reinstall puts a new folder in its place,
    and a container still mounting the old one is made again."""
    return f"{folder.stat().st_ino}.{(folder / 'plugin.json').stat().st_mtime_ns}"


def plugin_run_args(
    bot_id: str, plugin_id: str, folder: Path, home: Path, *, online: bool, stamp: str
) -> list[str]:
    """`docker run` for a plugin's container of hers: its own folder (read-only) and its own home, nothing
    of hers, nothing of the other plugins'."""
    return _container_args(
        plugin_container_name(bot_id, plugin_id),
        bot_id,
        online=online,
        env={
            "HOME": "/data",
            "PYTHONUSERBASE": "/data/.local",
            "npm_config_prefix": "/data/.local",
            "PATH": "/data/.local/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        labels={
            "nest.plugin": PLUGIN_VERSION,
            "nest.online": str(int(online)),
            "nest.stamp": stamp,
        },
        mounts=[f"{folder}:/plugins/{plugin_id}:ro", f"{home}:/data"],
        workdir=f"/plugins/{plugin_id}",
        limits=(PLUGIN_CPUS, PLUGIN_MEMORY, PLUGIN_PROCESSES),
        scratch=("/tmp:size=128m",),
    )


# A plugin, for the methods that work in a plugin's container: (its id, its installed folder).
Plugin = tuple[str, Path]


class Sandbox:
    def __init__(
        self,
        workspace: Workspace,
        *,
        docker_call: Docker | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = IDLE_SECONDS,
        egress_log: Path | None = None,
        online: Callable[[str], Awaitable[bool]] | None = None,
        plugin_homes: Path | None = None,
        spawner: Spawner | None = None,
    ) -> None:
        self.workspace = workspace
        # Where plugins' containers keep what they install: <her folder>/<plugin id>/, mounted at /data.
        self.plugin_homes = plugin_homes or workspace.root.parent / "plugin-homes"
        self._spawner = spawner or spawn_docker
        self.egress_log = egress_log  # the folder the proxy writes its log into
        self._online = online
        self._docker = docker_call or run_docker
        self._clock = clock
        self._idle_seconds = idle_seconds
        self._used: dict[str, float] = {}  # container name -> when it last ran something
        self._plugin_boxes: dict[str, tuple[str, str]] = {}  # a plugin's container name -> (bot, plugin)
        self._image_ready = False
        self._joined: set[str] = set()  # networks the proxy is on (since this proxy container was made)
        self._containers: dict[str, dict[str, str]] = {}  # network -> container address -> name (the log)
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap(), name="sandbox-idle")

    async def stop(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        for name in list(self._used):
            with contextlib.suppress(SandboxError):
                await self._docker(["stop", "--time", "2", name], DOCKER_TIMEOUT)

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(60)
            with contextlib.suppress(Exception):
                await self.stop_idle()

    async def stop_idle(self) -> int:
        now, stopped = self._clock(), 0
        for name, at in list(self._used.items()):
            if now - at >= self._idle_seconds:
                self._used.pop(name, None)
                code, _ = await self._docker(["stop", "--time", "2", name], DOCKER_TIMEOUT)
                stopped += code == 0
                log.info("容器 %s 闲置，停了", name)
        return stopped

    async def _image(self) -> None:
        if self._image_ready:
            return
        code, _ = await self._docker(["image", "inspect", IMAGE], DOCKER_TIMEOUT)
        if code != 0:
            log.info("第一次用工作区容器：用 %s 构建 %s", BASE, IMAGE)
            code, out = await self._docker(["build", "--tag", IMAGE, "-"], BUILD_TIMEOUT, DOCKERFILE.encode())
            if code != 0:
                raise SandboxError(f"工作区容器的镜像没建成：{_last_line(out)}")
        self._image_ready = True

    async def is_online(self, bot_id: str) -> bool:
        return True if self._online is None else await self._online(bot_id)

    async def _inspect(self, name: str, labels: tuple[str, ...]) -> list[str] | None:
        """[running, label…] of a container; None when there is none."""
        shown = " ".join(["{{.State.Running}}", *(f'{{{{index .Config.Labels "{k}"}}}}' for k in labels)])
        code, out = await self._docker(
            ["inspect", "--type", "container", "--format", shown, name], DOCKER_TIMEOUT
        )
        if code != 0:
            return None
        found = out.strip().split(" ")
        return found + [""] * (len(labels) + 1 - len(found))

    async def _network(self, bot_id: str) -> None:
        """Her internal network there, with the proxy on it and written down as hers."""
        network = network_name(bot_id)
        if network in self._joined:
            return
        code, _ = await self._docker(["network", "inspect", network], DOCKER_TIMEOUT)
        if code != 0:
            code, out = await self._docker(["network", "create", "--internal", network], DOCKER_TIMEOUT)
            if code != 0 and "already exists" not in out:
                raise SandboxError(f"联网用的内部网络没建成：{_last_line(out)}")
        code, out = await self._docker(["network", "connect", network, EGRESS], DOCKER_TIMEOUT)
        if code != 0 and "already exists" not in out:
            raise SandboxError(f"联网代理没接上她的内部网络：{_last_line(out)}")
        await self._write_map()
        self._joined.add(network)

    async def _write_map(self) -> None:
        """For the proxy: its address on each companion's network, and whose network that is."""
        folder = self.egress_log
        if folder is None:
            return
        code, out = await self._docker(
            ["inspect", "--format", "{{json .NetworkSettings.Networks}}", EGRESS], DOCKER_TIMEOUT
        )
        try:
            networks = json.loads(out) if code == 0 else {}
        except ValueError:
            networks = {}
        found = {
            str(info.get("IPAddress")): name[len(NETWORK_PREFIX) :]
            for name, info in (networks if isinstance(networks, dict) else {}).items()
            if name.startswith(NETWORK_PREFIX) and isinstance(info, dict) and info.get("IPAddress")
        }

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / NETWORK_MAP
            temporary = target.with_name(target.name + ".new")
            temporary.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)

        await asyncio.to_thread(write)

    async def _write_containers(self, bot_id: str) -> None:
        """For the proxy's log: which container on her network has which address now (her own, her
        plugins'), so a connection is logged with where it came from. Shown only: it decides nothing."""
        folder = self.egress_log
        if folder is None:
            return
        code, out = await self._docker(
            ["network", "inspect", "--format", "{{json .Containers}}", network_name(bot_id)], DOCKER_TIMEOUT
        )
        try:
            members = json.loads(out) if code == 0 else {}
        except ValueError:
            members = {}
        here = {
            str(info.get("IPv4Address") or "").partition("/")[0]: str(info.get("Name") or "")
            for info in (members.values() if isinstance(members, dict) else [])
            if isinstance(info, dict) and info.get("IPv4Address") and info.get("Name") != EGRESS
        }
        self._containers[network_name(bot_id)] = here

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            found = {ip: name for places in self._containers.values() for ip, name in places.items()}
            target = folder / CONTAINER_MAP
            temporary = target.with_name(target.name + ".new")
            temporary.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)

        await asyncio.to_thread(write)

    async def _egress(self) -> None:
        """The proxy container, made again when its script changed."""
        script = await asyncio.to_thread(EGRESS_SCRIPT.read_bytes)
        version = hashlib.sha256(script).hexdigest()[:12]
        state = await self._inspect(EGRESS, ("nest.egress",))
        if state is not None and state[1] == version:
            if state[0] != "true":
                code, out = await self._docker(["start", EGRESS], DOCKER_TIMEOUT)
                if code != 0:
                    raise SandboxError(f"联网代理没能启动：{_last_line(out)}")
            return
        if state is not None:
            await self._docker(["rm", "--force", EGRESS], DOCKER_TIMEOUT)
        self._joined.clear()  # a new proxy container is on no companion's network yet
        mounts = ["--volume", f"{EGRESS_SCRIPT.resolve()}:/egress.py:ro"]
        if self.egress_log is not None:
            await asyncio.to_thread(self.egress_log.mkdir, parents=True, exist_ok=True)
            mounts += ["--volume", f"{self.egress_log.resolve()}:/log"]
        code, out = await self._docker(
            [
                "run",
                "--detach",
                "--name",
                EGRESS,
                "--restart",
                "unless-stopped",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "64",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "1000:1000",
                "--read-only",
                "--label",
                f"nest.egress={version}",
                *mounts,
                IMAGE,
                "python3",
                "-u",
                "/egress.py",
            ],
            DOCKER_TIMEOUT,
        )
        if code != 0:
            raise SandboxError(f"联网代理没能启动：{_last_line(out)}")
        log.info("联网代理 %s 启动了（%s）", EGRESS, version)

    async def _ensure(
        self,
        bot_id: str,
        name: str,
        wanted: dict[str, str],
        online: bool,
        make: Callable[[], Awaitable[list[str]]],
    ) -> None:
        """The container `name` running, made with `make()` when there is none or its labels are not
        `wanted` (made the old way, the network switch moved, a plugin installed again)."""
        state = await self._inspect(name, tuple(wanted))
        if state is not None and state[1:] != list(wanted.values()):
            await self._docker(["rm", "--force", name], DOCKER_TIMEOUT)
            state = None
        if online:
            await self._image()
            await self._egress()
            await self._network(bot_id)
        if state is not None and state[0] == "true":
            return
        if state is not None:
            code, out = await self._docker(["start", name], DOCKER_TIMEOUT)
        else:
            await self._image()
            code, out = await self._docker(await make(), DOCKER_TIMEOUT)
        if code != 0:
            raise SandboxError(f"容器没能启动：{_last_line(out)}")
        log.info("容器 %s 启动了（%s）", name, "联网" if online else "断网")
        if online:
            await self._write_containers(bot_id)

    async def _container(self, bot_id: str) -> str:
        """Her container (her files are in /work, so making it again loses nothing)."""
        name = container_name(bot_id)
        online = await self.is_online(bot_id)

        async def make() -> list[str]:
            folder = await asyncio.to_thread(self.workspace.folder, bot_id)
            return run_args(bot_id, folder.resolve(), online=online)

        wanted = {"nest.workspace": VERSION, "nest.online": str(int(online))}
        await self._ensure(bot_id, name, wanted, online, make)
        return name

    async def _plugin_container(self, bot_id: str, plugin: Plugin) -> str:
        """A plugin's container of hers (what it installed is in its home, so making it again loses
        nothing)."""
        plugin_id, folder = plugin
        name = plugin_container_name(bot_id, plugin_id)
        online = await self.is_online(bot_id)
        try:
            folder = await asyncio.to_thread(folder.resolve)
            stamp = await asyncio.to_thread(plugin_stamp, folder)
        except OSError:
            raise SandboxError("插件的文件夹不在了") from None
        home = self.plugin_homes / folder_name(bot_id) / plugin_id

        async def make() -> list[str]:
            await asyncio.to_thread(home.mkdir, parents=True, exist_ok=True)
            resolved = await asyncio.to_thread(home.resolve)
            return plugin_run_args(bot_id, plugin_id, folder, resolved, online=online, stamp=stamp)

        wanted = {"nest.plugin": PLUGIN_VERSION, "nest.online": str(int(online)), "nest.stamp": stamp}
        await self._ensure(bot_id, name, wanted, online, make)
        self._plugin_boxes[name] = (bot_id, plugin_id)
        return name

    async def _box(self, bot_id: str, plugin: Plugin | None) -> str:
        async with self._lock:
            if plugin is None:
                name = await self._container(bot_id)
            else:
                name = await self._plugin_container(bot_id, plugin)
            self._used[name] = self._clock()
        return name

    async def spawn(
        self,
        bot_id: str,
        command: list[str],
        *,
        workdir: str = "/work",
        env: dict[str, str] | None = None,
        plugin: Plugin | None = None,
    ) -> asyncio.subprocess.Process:
        """A long-running process (an MCP server) talking over stdin and stdout: in her container, or with
        `plugin` in that plugin's container of hers. `env` reaches that container only."""
        given = env or {}
        name = await self._box(bot_id, plugin)
        names = [arg for key in given for arg in ("--env", key)]
        return await self._spawner(["exec", "-i", "--workdir", workdir, *names, name, *command], given)

    def touch(self, bot_id: str, plugin_id: str | None = None) -> None:
        """Her container (or a plugin's container of hers) is in use: not idle."""
        name = container_name(bot_id) if plugin_id is None else plugin_container_name(bot_id, plugin_id)
        if name in self._used:
            self._used[name] = self._clock()

    async def _remove(self, name: str) -> None:
        self._used.pop(name, None)
        self._plugin_boxes.pop(name, None)
        with contextlib.suppress(SandboxError):
            await self._docker(["rm", "--force", name], DOCKER_TIMEOUT)

    async def forget(self, bot_id: str) -> None:
        """Her containers go (the network switch moved): hers and her plugins'. Her files and what the
        plugins installed stay; the next use makes them again."""
        theirs = [n for n, (bot, _) in self._plugin_boxes.items() if bot == bot_id]
        for name in [container_name(bot_id), *theirs]:
            await self._remove(name)

    async def _size(self, bot_id: str) -> int:
        return await asyncio.to_thread(self.workspace.used, bot_id)

    async def _watch(
        self, bot_id: str, name: str, running: asyncio.Future[tuple[int, str]], ceiling: int
    ) -> bool:
        """While her command runs, her folder is measured now and then; past `ceiling` (the limit, or what
        it held already when that was more), her container goes, which ends the command. Whether it had
        to."""
        while True:
            done, _ = await asyncio.wait({running}, timeout=WATCH_SECONDS)
            if done:
                return False
            if await self._size(bot_id) > ceiling:
                log.warning("%s 的工作区超过上限，停掉她的容器", bot_id)
                with contextlib.suppress(SandboxError):
                    await self._remove(name)
                with contextlib.suppress(Exception):
                    await running
                return True

    async def forget_plugin(self, plugin_id: str) -> None:
        """A plugin's containers go (switched, upgraded, uninstalled, a secret changed)."""
        for name in [n for n, (_, pid) in self._plugin_boxes.items() if pid == plugin_id]:
            await self._remove(name)

    async def run(
        self, bot_id: str, command: str, seconds: int = TIMEOUT, *, plugin: Plugin | None = None
    ) -> str:
        """Run `command` (sh) in her container, in /work (with `plugin`: in that plugin's container of hers,
        in its folder). What to tell her: exit code and output."""
        command = command.strip()
        if not command:
            return "命令是空的"
        if len(command) > COMMAND_MAX:
            return f"命令太长了（最多 {COMMAND_MAX} 字）"
        seconds = max(1, min(int(seconds or TIMEOUT), MAX_TIMEOUT))
        workdir = "/work" if plugin is None else f"/plugins/{plugin[0]}"
        # Over the limit already, a command may still run (to delete things), but not make it worse.
        ceiling = max(self.workspace.limit, await self._size(bot_id)) if plugin is None else 0
        try:
            name = await self._box(bot_id, plugin)
            inside = ["timeout", "--kill-after=5", str(seconds), "sh", "-c", command]
            running = asyncio.ensure_future(
                self._docker(["exec", "--workdir", workdir, name, *inside], seconds + 30)
            )
            if plugin is None and await self._watch(bot_id, name, running, ceiling):
                limit = f"{self.workspace.limit / 1024**3:.0f} GB"
                return f"这条命令让工作区超过上限（{limit}），被停掉了。先删掉些不用的东西再继续。"
            code, out = await running
        except SandboxError as exc:
            return f"没跑成：{exc}"
        self._used[name] = self._clock()
        if len(out) > OUTPUT_CHARS:
            out = out[: OUTPUT_CHARS // 2] + "\n……（中间略）……\n" + out[-OUTPUT_CHARS // 2 :]
        how = f"超过 {seconds} 秒，被停掉了" if code == 124 else f"退出码 {code}"
        return f"（{how}）\n{out.rstrip() or '（没有输出）'}"
