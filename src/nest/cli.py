"""Command line: `nest serve` (foreground), `nest start` (background + console), `nest stop`, `status`,
`set-password` (for signing in from other devices), and setup commands."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable

import httpx
import uvicorn

from nest import __version__, hints, service
from nest.api.auth import new_token
from nest.app import create_app
from nest.config import Settings
from nest.logs import configure
from nest.runtime import InstanceLock, RuntimeInfo, pid_alive, terminate

START_TIMEOUT = 20.0
STOP_TIMEOUT = 15.0
# Open streams (the console's live updates) must not hold shutdown hostage: after this long they are
# cut and the app's shutdown (adapters, brain, database) runs. Without it a "stopped" process lingered
# with its QQ connections alive.
GRACEFUL_SECONDS = 5


def _client() -> httpx.Client:
    # trust_env=False: never send local control traffic through a system proxy.
    return httpx.Client(timeout=3.0, trust_env=False)


def _healthy(settings: Settings) -> bool:
    try:
        with _client() as client:
            data = client.get(settings.base_url + "/api/health").json()
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(data, dict) and data.get("app") == "nest"


def _bind(settings: Settings) -> socket.socket:
    host = settings.bind_host
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if os.name != "nt":
        # Without it a restart cannot listen again until the old connections' TIME_WAIT ends (~30 s on
        # macOS: launchd's retries kept failing with "port in use"). Only one nest runs at a time anyway
        # (the instance lock). Not on Windows, where the option would let another program share the port.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, settings.port))
    except OSError:
        sock.close()
        raise
    return sock


def lock_for(settings: Settings) -> InstanceLock:
    return InstanceLock(settings.data_dir / "nest.lock")


def make_server(app) -> uvicorn.Server:  # type: ignore[no-untyped-def]
    return uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=GRACEFUL_SECONDS,
            # Who may connect is decided by the TCP peer's address (nest.api.auth). uvicorn would otherwise
            # take it from X-Forwarded-For whenever the peer is this machine: any local program could
            # then pose as a different address, e.g. to dodge the wrong-password lockout.
            proxy_headers=False,
        )
    )


def serve(settings: Settings, *, console: bool) -> int:
    configure(settings.log_dir, settings.log_level, console=console)
    lock = lock_for(settings)
    if not lock.acquire():
        print(f"已经有一个 nest 进程在用这个数据目录（可能还没完全退出），请先{hints.STOP}")
        return 1
    try:
        return _serve_locked(settings)
    finally:
        lock.release()


def _serve_locked(settings: Settings) -> int:
    if _healthy(settings):
        print(f"nest 已经在 {settings.base_url} 运行")
        return 1
    try:
        sock = _bind(settings)
    except OSError:
        print(f"端口 {settings.port} 已被其他程序占用；可在 .env 里设置 NEST_PORT 换一个端口")
        return 1

    token = new_token()
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(settings, token=token, request_shutdown=request_shutdown)
    server = make_server(app)
    info = RuntimeInfo(os.getpid(), settings.host, settings.port, token, time.time(), __version__)

    async def run() -> None:
        async def announce() -> None:
            while not server.started:
                if server.should_exit:
                    return
                await asyncio.sleep(0.05)
            info.write(settings.runtime_file)

        announcer = asyncio.create_task(announce())
        try:
            await server.serve(sockets=[sock])
        finally:
            announcer.cancel()
            RuntimeInfo.remove_if_owned(settings.runtime_file, info.pid)

    try:
        asyncio.run(run())
    finally:
        sock.close()
    return 0


def _login_url(settings: Settings, info: RuntimeInfo) -> str:
    return f"{settings.base_url}/login?token={info.token}"


def _spawn_server(settings: Settings) -> None:
    flags = 0
    kwargs: dict = {}
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "nest", "serve", "--quiet"],
        cwd=settings.home,
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def start(settings: Settings, *, open_browser: bool) -> int:
    if not _healthy(settings) and lock_for(settings).held_elsewhere():
        print(f"有一个 nest 进程还没退出（控制台连不上它），请先{hints.STOP}再启动")
        return 1
    if not _healthy(settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if service.installed():
            # The launchd agent owns the process: start its job rather than a second, unsupervised copy.
            if not service.kickstart():
                print(
                    "没能通过 launchd 启动 nest 服务："
                    "请查看 data/logs/launchd.log，或运行 nest service install"
                )
                return 1
        else:
            _spawn_server(settings)
        deadline = time.monotonic() + START_TIMEOUT
        while not (_healthy(settings) and RuntimeInfo.read(settings.runtime_file)):
            if time.monotonic() > deadline:
                print(f"nest 没能在 {START_TIMEOUT:.0f} 秒内启动，请查看 {settings.log_dir / 'nest.log'}")
                return 1
            time.sleep(0.2)
        print(f"nest 已在后台启动：{settings.base_url}")
    else:
        print(f"nest 已经在运行：{settings.base_url}")
    info = RuntimeInfo.read(settings.runtime_file)
    if info is None:
        print(f"找不到 data/runtime.json，无法自动登录控制台；请先{hints.STOP}再重新启动")
        return 1
    if open_browser:
        webbrowser.open(_login_url(settings, info))
    return 0


def stop(settings: Settings) -> int:
    info = RuntimeInfo.read(settings.runtime_file)
    if not _healthy(settings):
        if info is not None and pid_alive(info.pid) and lock_for(settings).held_elsewhere():
            # Stopped answering but never exited: its QQ connections are still open. End it.
            terminate(info.pid)
            if not _wait_exit(info.pid, STOP_TIMEOUT):
                print(f"进程 {info.pid} 结束不了，请在{hints.PROCESSES}里结束它")
                return 1
            RuntimeInfo.remove_if_owned(settings.runtime_file, info.pid)
            print(f"nest 已停止（进程 {info.pid} 之前没有完全退出，已强制结束）")
            return 0
        if info is not None:
            RuntimeInfo.remove_if_owned(settings.runtime_file, info.pid)
        if lock_for(settings).held_elsewhere():
            print(
                "有一个 nest 进程还没退出，但找不到它的进程号；"
                f"请在{hints.PROCESSES}里结束 python（nest serve）"
            )
            return 1
        print("nest 没有在运行")
        return 0
    if info is None:
        print("nest 在运行，但找不到 data/runtime.json，无法安全停止")
        return 1
    try:
        with _client() as client:
            client.post(
                settings.base_url + "/api/shutdown", headers={"authorization": f"Bearer {info.token}"}
            ).raise_for_status()
    except httpx.HTTPError as exc:
        print(f"停止请求失败：{exc}")
        return 1
    # Not answering HTTP is not enough: the process must be gone, or its bots keep running.
    if _wait_exit(info.pid, STOP_TIMEOUT):
        print("nest 已停止")
        return 0
    terminate(info.pid)
    if not _wait_exit(info.pid, 5.0):
        print(f"nest 没有在预期时间内退出，进程 {info.pid} 也结束不了，请在{hints.PROCESSES}里结束它")
        return 1
    RuntimeInfo.remove_if_owned(settings.runtime_file, info.pid)
    print(f"nest 在 {STOP_TIMEOUT:.0f} 秒内没有退出，已强制结束")
    return 0


def _wait_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def status(settings: Settings) -> int:
    if not _healthy(settings):
        print("nest 没有在运行")
        return 1
    info = RuntimeInfo.read(settings.runtime_file)
    detail = (
        f"，进程 {info.pid}，启动于 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info.started_at))}"
        if info
        else ""
    )
    print(f"nest 正在运行：{settings.base_url}{detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nest", description="nest bot runtime")
    parser.add_argument("--version", action="version", version=f"nest {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_cmd = sub.add_parser("serve", help="在前台运行服务")
    serve_cmd.add_argument("--quiet", action="store_true", help="只写日志文件，不输出到终端")
    start_cmd = sub.add_parser("start", help="在后台启动并打开控制台")
    start_cmd.add_argument("--no-browser", action="store_true")
    sub.add_parser("stop", help="停止后台服务")
    sub.add_parser("status", help="查看是否在运行")
    provider_cmd = sub.add_parser("provider-add", help="添加或更新一个模型接口")
    provider_cmd.add_argument("id", help="模型的名字，例如 deepseek")
    provider_cmd.add_argument(
        "--kind", required=True, help="openai_chat / openai_responses / anthropic / codex"
    )
    provider_cmd.add_argument("--base-url", required=True)
    provider_cmd.add_argument("--model", required=True)
    provider_cmd.add_argument("--key-ref", default="", help=".env 里存密钥的名字")
    provider_cmd.add_argument(
        "--key-from", default="", metavar="文件:名字", help="从凭据文件读取密钥写进 .env"
    )
    provider_cmd.add_argument(
        "--option", action="append", default=[], metavar="名字=值", help="例如 proxy=… temperature=0.8"
    )
    bot_cmd = sub.add_parser("bot-add", help="为一个已有的 QQ 账号创建或更新 bot")
    bot_cmd.add_argument("uin", help="QQ 号")
    bot_cmd.add_argument("--provider", required=True, help="用哪个模型")
    bot_cmd.add_argument("--name", default="")
    bot_cmd.add_argument("--alias", action="append", default=[], help="叫到这些名字就算点名（可重复）")
    bot_cmd.add_argument("--group", action="append", default=[], help="在这些群启用（可重复）")
    bot_cmd.add_argument("--activity", default="occasional", help="mentions / occasional / active")
    bot_cmd.add_argument("--mode", default=None, help="observe（只看不说）/ paused（暂停）")
    sub.add_parser("set-password", help="设置从其他设备登录控制台的密码（已登录的设备都会退出）")
    service_args = sub.add_parser("service", help="macOS：把 nest 装成登录后自动启动、崩溃自动重启的后台服务")
    service_args.add_argument("action", choices=["install", "uninstall", "status"])
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "serve":
        return serve(settings, console=not args.quiet)
    if args.command == "start":
        return start(settings, open_browser=not args.no_browser)
    if args.command == "stop":
        return stop(settings)
    if args.command == "set-password":
        return set_password(settings)
    if args.command == "service":
        return service_cmd(settings, args.action)
    if args.command in ("provider-add", "bot-add"):
        return configure_cmd(settings, args)
    return status(settings)


def service_cmd(settings: Settings, action: str) -> int:
    if action == "status":
        if not service.installed():
            print("没有安装 nest 后台服务" + ("" if service.supported() else "（只在 macOS 上可用）"))
            return 0
        print(
            f"已安装 nest 后台服务（{service.plist_path()}）；"
            + ("正在运行" if _healthy(settings) else "现在没有运行")
        )
        return 0
    if action == "install" and _healthy(settings) and not service.installed():
        print(f"nest 正在运行，请先{hints.STOP}，再安装后台服务")
        return 1
    result = service.install(settings.home) if action == "install" else service.uninstall()
    print(result.message)
    return 0 if result.ok else 1


def set_password(settings: Settings, *, ask: Callable[[str], str] = getpass.getpass) -> int:
    """Ask for the password twice (never echoed) and store its hash; signs out every device."""
    from nest.remote import RemoteAccess, password_problem
    from nest.storage.db import Database

    password = ask("新密码：")
    problem = password_problem(password)
    if problem:
        print(problem)
        return 1
    if ask("再输一遍：") != password:
        print("两次输入的不一样，密码没有改")
        return 1

    async def run() -> None:
        db = await Database.open(settings.db_path)
        try:
            await RemoteAccess(db).set_password(password)
        finally:
            await db.close()

    asyncio.run(run())
    print("密码已设置。之前登录过的设备需要用新密码重新登录。")
    if not settings.remote:
        print("远程访问还没开启：在 .env 里加一行 NEST_REMOTE=true（或在「系统」页开启），然后重启 nest。")
    return 0


def configure_cmd(settings: Settings, args: argparse.Namespace) -> int:
    from nest import setup_cmds
    from nest.secrets import SecretStore
    from nest.storage.db import Database

    async def run() -> list[str]:
        db = await Database.open(settings.db_path)
        try:
            if args.command == "provider-add":
                config = await setup_cmds.add_provider(
                    db,
                    SecretStore(settings.home / ".env"),
                    provider_id=args.id,
                    kind=args.kind,
                    base_url=args.base_url,
                    model=args.model,
                    key_ref=args.key_ref,
                    key_from=args.key_from,
                    options=args.option,
                )
                key = f"，密钥在 .env 的 {config.key_ref}" if config.key_ref else ""
                return [f"模型 {config.id}：{config.kind} · {config.model} · {config.base_url}{key}"]
            if args.command == "bot-add":
                notes = await setup_cmds.add_bot(
                    db,
                    uin=args.uin,
                    provider_id=args.provider,
                    name=args.name,
                    aliases=args.alias,
                    groups=args.group,
                    activity=args.activity,
                    mode=args.mode,
                )
                return [f"bot QQ {args.uin} 使用模型 {args.provider}", *notes]
            raise ValueError(f"不认识的命令 {args.command}")
        finally:
            await db.close()

    try:
        for line in asyncio.run(run()):
            print(line)
    except (ValueError, OSError) as exc:
        print(f"失败：{exc}")
        return 1
    if _healthy(settings):
        print("nest 正在运行，重启后生效。")
    return 0
