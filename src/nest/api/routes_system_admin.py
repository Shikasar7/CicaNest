"""The machine around nest: SnowLuma and QQ (where they are, start/stop), the proxy 监控 may fetch through,
nest itself, and the live log.

SnowLuma runs one of two ways ("snowluma_mode"): "program", a folder with node.exe that nest starts and
stops itself, next to the QQ clients the user logged in (Windows); or "docker", containers holding Linux QQ
and a OneBot server that nest starts and stops through `docker` (deploy/mac): one NapCat per
account, or SnowLuma with all of them ("snowluma_container" names one or several). QQ logs in there through
each container's own page, found from the port Docker publishes it on (LOGIN_PAGES), else qq_login_url. A
NapCat container's QR code can also be shown in the console itself (GET /api/system/qq/login): that page is
only published on the Mac, the console is reachable from the phone.

Process work runs in worker threads through nest.system, one action at a time (app.state.process_lock);
settings changes hold app.state.config_lock. Every change publishes an SSE "system" event
({"change": ...}); the console then reads GET /api/system again.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from nest import __version__, backup, docker, housekeeping, napcat, system
from nest.adapters.registry import check_recipient
from nest.api.auth import require_token
from nest.api.text import has_bad_chars, proxy_url
from nest.core import offline_alert
from nest.logs import BUFFER_SIZE, LEVELS
from nest.storage.accounts import list_accounts
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

PATH_LIMIT = 500
NOT_SET_SNOWLUMA = "还没有设置 SnowLuma 文件夹，请先在「系统」页填写"
NOT_SET_QQ = "还没有设置 QQ 程序的位置，请先在「系统」页填写"
DEFAULT_MODE = "program" if os.name == "nt" else "docker"
DEFAULT_CONTAINER = "snowluma"
DEFAULT_LOGIN_URL = "http://127.0.0.1:6081"  # SnowLuma's noVNC, when Docker does not say where it is
MAX_CONTAINERS = 8
# A container's page for scanning the login QR code, by the port it is published from: NapCat's WebUI, or
# SnowLuma's remote desktop (noVNC).
LOGIN_PAGES = {"6099/tcp": "/webui", "6081/tcp": ""}
QQ_IN_DOCKER = "QQ 在 Docker 容器里运行：用「扫码登录」打开它的画面"
NAPCAT_PAGE = "6099/tcp"  # a container publishing NapCat's WebUI: its QR code can be read (docker.login_qr)
# NapCat saves a new QR code about every 2 minutes while QQ waits for a scan; an older file is one QQ no
# longer shows (it logged in, or is logging in with the saved login).
QR_FRESH = 130


class SystemSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snowluma_dir: str | None = None
    qq_executable: str | None = None
    fetch_proxy: str | None = None  # "" removes it
    snowluma_mode: Literal["program", "docker"] | None = None
    snowluma_container: str | None = None
    qq_login_url: str | None = None
    history_days: int | None = Field(default=None, ge=1, le=housekeeping.MAX_DAYS)
    qq_cache_days: int | None = Field(default=None, ge=0, le=housekeeping.MAX_DAYS)  # 0: never clean
    offline_alert_to: str | None = None  # 掉线提醒: whom to tell (check_recipient); "" turns it off
    offline_alert_minutes: int | None = Field(default=None, ge=1, le=offline_alert.MAX_MINUTES)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


# -- settings ---------------------------------------------------------------------------------------


async def machine_settings(state: Any) -> dict[str, str]:
    """The saved settings, or the defaults (never stored): paths worked out from what exists on disk now,
    SnowLuma as a program on Windows and as a Docker container elsewhere, no proxy for 监控."""
    stored = await SettingsRepo(state.db).all()

    def text(key: str, default: str) -> str:
        value = stored.get(key)
        return value if isinstance(value, str) else default

    mode = text("snowluma_mode", DEFAULT_MODE)
    return {
        "snowluma_mode": mode if mode in ("program", "docker") else DEFAULT_MODE,
        "snowluma_dir": text("snowluma_dir", system.default_snowluma_dir()),
        "qq_executable": text("qq_executable", system.default_qq_executable()),
        "snowluma_container": text("snowluma_container", DEFAULT_CONTAINER),
        "qq_login_url": text("qq_login_url", DEFAULT_LOGIN_URL),
        "fetch_proxy": text("fetch_proxy", ""),
    }


async def offline_alert_settings(state: Any) -> tuple[str, int]:
    """掉线提醒: the QQ number to tell ("" = off) and after how many minutes down."""
    stored = await SettingsRepo(state.db).all()
    to, minutes = stored.get("offline_alert_to"), stored.get("offline_alert_minutes")
    return (
        to if isinstance(to, str) else "",
        minutes if isinstance(minutes, int) and minutes > 0 else offline_alert.DEFAULT_MINUTES,
    )


async def storage_plan(state: Any) -> housekeeping.Plan:
    """What the daily cleanup does: days of history (saved, else NEST_MESSAGE_RETENTION_DAYS) and, with
    SnowLuma in Docker, how old a QQ picture, video or file may get (saved, else a week)."""
    stored = await SettingsRepo(state.db).all()
    days = stored.get("history_days")
    cache_days = stored.get("qq_cache_days")
    in_docker = (await machine_settings(state))["snowluma_mode"] == "docker"
    return housekeeping.Plan(
        history_days=days if isinstance(days, int) and days > 0 else state.settings.message_retention_days,
        qq_cache_days=cache_days if isinstance(cache_days, int) else housekeeping.DEFAULT_QQ_CACHE_DAYS,
        qq_cache_dir=housekeeping.qq_cache_root(state.settings.home) if in_docker else None,
    )


def _path(value: str, what: str, example: str) -> Path:
    text = value.strip().strip('"').strip()  # Explorer's 复制文件地址 adds quotes
    if not text:
        raise _invalid(f"请填写 {what}")
    if len(text) > PATH_LIMIT or has_bad_chars(text):
        raise _invalid(f"{what}里有不能保存的字符，或者太长了")
    path = Path(text)
    if not path.is_absolute():
        raise _invalid(f"{what}要填从盘符开始的完整路径，例如 {example}")
    return path


def _snowluma_dir(value: str) -> str:
    path = _path(value, "SnowLuma 文件夹", r"D:\SnowLuma")
    if not system.is_snowluma_dir(path):
        raise _invalid(f"这个文件夹里没有 SnowLuma（要有 node.exe 和 index.mjs）：{path}")
    return str(path)


def _qq_executable(value: str) -> str:
    path = _path(value, "QQ 程序的位置", r"E:\Tencent\QQ.exe")
    if not system.is_qq_executable(path):
        raise _invalid(f"找不到 QQ 程序（要填 QQ.exe 的完整路径）：{path}")
    return str(path)


def container_names(value: str) -> list[str]:
    """The QQ containers in the saved setting: one name, or several separated by commas or spaces."""
    return [name for name in re.split(r"[\s,，]+", value) if name]


def _containers(value: str) -> str:
    names = list(dict.fromkeys(container_names(value)))
    if not names or not all(docker.valid_name(name) for name in names):
        raise _invalid("容器名只能用字母、数字、点、下划线和横线，几个容器用逗号隔开，例如 snowluma")
    if len(names) > MAX_CONTAINERS:
        raise _invalid(f"最多 {MAX_CONTAINERS} 个容器")
    return ", ".join(names)


def _login_url(value: str) -> str:
    url = value.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname or has_bad_chars(url):
        raise _invalid("扫码页地址要写成 http://主机:端口，例如 http://127.0.0.1:6081")
    return url.rstrip("/")


def _fetch_proxy(value: str) -> str:
    proxy = proxy_url(value)
    if proxy is None:
        raise _invalid("外网代理要写成 http://主机:端口，例如 http://127.0.0.1:7890，留空表示不用代理")
    return proxy


async def _hidden_names(state: Any) -> set[str]:
    """Environment variable names that may hold nest's secrets: a started program must not inherit them."""
    names = {config.key_ref for config in await state.repo.providers()}
    for account in await list_accounts(state.db):
        names |= {account.http_token_ref, account.ws_token_ref, *account.refs.values()}
    return names - {""}


# -- the view -----------------------------------------------------------------------------------------


def _login_page(box: docker.Container, fallback: str) -> str:
    """Where to scan the container's login QR code: its published NapCat WebUI or noVNC, else `fallback`."""
    published = dict(box.ports)
    for inside, path in LOGIN_PAGES.items():
        if inside in published:
            return f"http://127.0.0.1:{published[inside]}{path}"
    return fallback


def _probe(paths: dict[str, str]) -> dict[str, Any]:
    """Blocking: walks the process table or asks Docker. Run in a worker thread."""
    if paths["snowluma_mode"] == "docker":
        boxes = [(name, docker.container(name)) for name in container_names(paths["snowluma_container"])]
        found = [box for _name, box in boxes if box.found]
        return {
            "snowluma_configured": bool(found),
            "snowluma_pids": [],
            "snowluma_running": bool(found) and all(box.running for box in found),
            "snowluma_status": boxes[0][1].status if len(boxes) == 1 else "",
            "snowluma_problem": next((box.problem for _name, box in boxes if box.problem), ""),
            "containers": [
                {
                    "name": name,
                    "found": box.found,
                    "running": box.running,
                    "status": box.status,
                    "problem": box.problem,
                    "login_url": _login_page(box, paths["qq_login_url"]),
                    "qr_login": NAPCAT_PAGE in dict(box.ports),  # the console can show its QR code
                    "self_id": box.account,  # the QQ number it logs in, when the container says (NapCat)
                    "password_set": box.quick_password,  # NapCat has a 回退密码 (routes_qq_password)
                }
                for name, box in boxes
            ],
            "qq_configured": False,
            "qq_clients": None,  # inside the containers: not visible from here
            "memory_mb": system.memory_mb(),
        }
    configured = system.is_snowluma_dir(paths["snowluma_dir"])
    pids = system.snowluma_pids(paths["snowluma_dir"]) if configured else []
    return {
        "snowluma_configured": configured,
        "snowluma_pids": pids,
        "snowluma_running": bool(pids) or system.webui_listening(),
        "snowluma_status": "",
        "snowluma_problem": "",
        "containers": [],
        "qq_configured": system.is_qq_executable(paths["qq_executable"]),
        "qq_clients": system.qq_clients(),
        "memory_mb": system.memory_mb(),
    }


async def system_view(state: Any) -> dict[str, Any]:
    paths = await machine_settings(state)
    probe = await asyncio.to_thread(_probe, paths)
    in_docker = paths["snowluma_mode"] == "docker"
    started = state.started_at
    return {
        "nest": {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": started,
            "uptime_seconds": round(time.time() - started, 1),
            "memory_mb": probe["memory_mb"],
        },
        "snowluma": {
            "mode": paths["snowluma_mode"],
            "container": paths["snowluma_container"],
            "status": probe["snowluma_status"],  # Docker's word for the container, in docker mode
            "problem": probe["snowluma_problem"],  # why Docker could not be asked
            "dir": paths["snowluma_dir"],
            "configured": probe["snowluma_configured"],
            "running": probe["snowluma_running"],
            "pid": probe["snowluma_pids"][0] if probe["snowluma_pids"] else None,
            "webui_url": system.WEBUI_URL,
        },
        "containers": probe["containers"],  # docker mode: each QQ container, with its login page
        "qq": {
            "executable": paths["qq_executable"],
            "configured": probe["qq_configured"],
            "clients": probe["qq_clients"],
            "login_url": paths["qq_login_url"] if in_docker else "",
        },
        "fetch_proxy": paths["fetch_proxy"],
        # 掉线提醒
        "offline_alert": dict(zip(("to", "minutes"), await offline_alert_settings(state), strict=True)),
        "accounts": [
            {
                "id": view["id"],
                "self_id": view["self_id"],
                "name": view["name"],
                "state": view["status"]["state"],
                "detail": view["status"]["detail"],
            }
            for view in state.hub.account_views()
        ],
    }


@router.get("/system")
async def get_system(request: Request) -> dict[str, Any]:
    return await system_view(request.app.state)


@router.patch("/system/settings")
async def update_settings(request: Request, patch: SystemSettingsPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        if not patch.model_fields_set:
            raise _invalid("没有要改的内容")
        values: dict[str, Any] = {}
        if patch.snowluma_mode is not None:
            values["snowluma_mode"] = patch.snowluma_mode
        if patch.snowluma_container is not None:
            values["snowluma_container"] = _containers(patch.snowluma_container)
        if patch.qq_login_url is not None:
            values["qq_login_url"] = _login_url(patch.qq_login_url)
        if patch.snowluma_dir is not None:
            values["snowluma_dir"] = _snowluma_dir(patch.snowluma_dir)
        if patch.qq_executable is not None:
            values["qq_executable"] = _qq_executable(patch.qq_executable)
        if patch.fetch_proxy is not None:
            values["fetch_proxy"] = _fetch_proxy(patch.fetch_proxy)
        if patch.history_days is not None:
            values["history_days"] = patch.history_days
        if patch.qq_cache_days is not None:
            values["qq_cache_days"] = patch.qq_cache_days
        if patch.offline_alert_to is not None:
            try:
                values["offline_alert_to"] = check_recipient(patch.offline_alert_to)
            except ValueError as exc:
                raise _invalid(f"掉线提醒：{exc}，留空表示不提醒") from None
        if patch.offline_alert_minutes is not None:
            values["offline_alert_minutes"] = patch.offline_alert_minutes
        if not values:
            raise _invalid("没有要改的内容")
        await SettingsRepo(state.db).set_many(values)
        log.info("系统设置已修改：%s", "、".join(values))
        state.broadcaster.publish("system", {"change": "settings"})
    return await system_view(state)


# -- storage --------------------------------------------------------------------------------------------


def _sizes(state: Any, plan: housekeeping.Plan) -> dict[str, int | None]:
    """Blocking: bytes used by nest's database, its log files and (docker mode) QQ's media caches."""
    root = plan.qq_cache_dir
    return {
        "database": state.db.size(),
        "logs": housekeeping.folder_size(state.settings.data_dir / "logs"),
        "qq_cache": housekeeping.qq_cache_size(root) if root is not None and root.is_dir() else None,
    }


async def storage_view(state: Any) -> dict[str, Any]:
    plan = await storage_plan(state)
    last = state.housekeeper.last
    sticker_count, sticker_bytes = await state.stickers.store.totals()
    return {
        "history_days": plan.history_days,
        "qq_cache_days": plan.qq_cache_days,
        "qq_cache": plan.qq_cache_dir is not None,  # docker mode: the container's QQ data can be cleaned
        "sizes": await asyncio.to_thread(_sizes, state, plan) | {"stickers": sticker_bytes},
        "sticker_count": sticker_count,
        "last": last.view() if last is not None else None,
    }


@router.get("/system/storage")
async def get_storage(request: Request) -> dict[str, Any]:
    return await storage_view(request.app.state)


@router.post("/system/storage/clean")
async def clean_storage(request: Request) -> dict[str, Any]:
    """The daily cleanup, now: deletes history and (docker mode) QQ cache older than the saved days."""
    state = request.app.state
    await state.housekeeper.run()
    state.broadcaster.publish("system", {"change": "storage"})
    return await storage_view(state)


# -- backup ---------------------------------------------------------------------------------------------


@router.get("/system/backups")
async def get_backups(request: Request) -> list[dict[str, Any]]:
    return await asyncio.to_thread(backup.listed, request.app.state.settings.data_dir)


@router.post("/system/backups")
async def make_backup(request: Request) -> dict[str, Any]:
    """一键备份: the database and the stickers. Keys and QQ logins are left out."""
    state = request.app.state
    if state.backing_up.locked():
        raise HTTPException(status_code=409, detail="正在备份，等这次做完")
    async with state.backing_up:
        path = await asyncio.to_thread(
            backup.make,
            state.settings.data_dir,
            state.db.path,
            version=__version__,
            schema=await state.db.schema_version(),
        )
    log.info("备份好了：%s", path.name)
    return {"name": path.name, "size": path.stat().st_size}


@router.get("/system/backups/{name}")
async def download_backup(request: Request, name: str) -> FileResponse:
    path = backup.path_of(request.app.state.settings.data_dir, name)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这份备份")
    return FileResponse(path, media_type="application/zip", filename=name)


# -- SnowLuma and QQ ------------------------------------------------------------------------------------


async def _snowluma_folder(state: Any) -> str:
    folder = (await machine_settings(state))["snowluma_dir"]
    if not system.is_snowluma_dir(folder):
        raise HTTPException(status_code=400, detail=NOT_SET_SNOWLUMA)
    return folder


def _failed(exc: system.ProcessError) -> HTTPException:
    log.error("%s", exc)
    return HTTPException(status_code=500, detail=str(exc))


def _running(folder: str) -> bool:
    return bool(system.snowluma_pids(folder)) or system.webui_listening()


async def _start(state: Any, folder: str) -> bool:
    """Start SnowLuma unless it is already running. Call with process_lock held."""
    if await asyncio.to_thread(_running, folder):
        return False
    hide = await _hidden_names(state)
    try:
        pid = await asyncio.to_thread(system.start_snowluma, folder, hide=hide)
    except system.ProcessError as exc:
        raise _failed(exc) from None
    log.info("SnowLuma 已启动（进程 %d）", pid)
    return True


async def _stop(folder: str) -> bool:
    """Stop SnowLuma's process tree (never QQ). Call with process_lock held."""
    pids = await asyncio.to_thread(system.snowluma_pids, folder)
    try:
        stopped = await asyncio.to_thread(system.stop_snowluma, pids) if pids else False
    except system.ProcessError as exc:
        raise _failed(exc) from None
    if stopped:
        log.info("SnowLuma 已停止（进程 %s）", "、".join(map(str, pids)))
    return stopped


async def _container_names(state: Any, only: str | None = None) -> list[str] | None:
    """The containers to act on in docker mode (all, or just `only`, which must be one of them); None in
    program mode."""
    paths = await machine_settings(state)
    if paths["snowluma_mode"] != "docker":
        return None
    names = container_names(paths["snowluma_container"])
    if only is None:
        return names
    if only not in names:
        raise HTTPException(status_code=400, detail=f"{only} 不是「系统」页设置里的 QQ 容器")
    return [only]


async def _docker(name: str, action: str) -> bool:
    """Start, stop or restart the container; False when there was nothing to do (already in that state).
    Call with process_lock held."""
    box = await asyncio.to_thread(docker.container, name)
    if box.problem:
        raise HTTPException(status_code=400, detail=box.problem)
    if not box.found:
        raise HTTPException(
            status_code=400, detail=f"Docker 里没有叫 {name} 的容器：先在 deploy/mac 里 docker compose up -d"
        )
    if (action == "start" and box.running) or (action == "stop" and not box.running):
        return False
    try:
        await asyncio.to_thread(docker.act, name, action)
    except system.ProcessError as exc:
        raise _failed(exc) from None
    log.info("QQ 容器 %s 已%s", name, {"start": "启动", "stop": "停止", "restart": "重启"}[action])
    return True


async def sync_connection(state: Any, box: docker.Container) -> None:
    """The account in a NapCat container is connected only while the container runs (Hub.hold)."""
    if not box.found or not box.account:
        return
    account_id = f"qq:{box.account}"
    if box.running:
        await state.hub.release(account_id)
    else:
        await state.hub.hold(account_id)


async def _sync_connections(state: Any, names: list[str]) -> None:
    for name in names:
        await sync_connection(state, await asyncio.to_thread(docker.container, name))


async def _docker_all(names: list[str], action: str) -> bool:
    """`_docker` on each container in turn; True when any of them changed."""
    changed = False
    for name in names:
        changed = await _docker(name, action) or changed
    return changed


# `container`: in docker mode, act on that one container only (one of the saved names); without it, on all.


@router.post("/system/snowluma/start")
async def snowluma_start(request: Request, container: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state
    async with state.process_lock:
        names = await _container_names(state, container)
        if names is not None:
            started = await _docker_all(names, "start")
            await _sync_connections(state, names)
        else:
            started = await _start(state, await _snowluma_folder(state))
    if started:
        state.broadcaster.publish("system", {"change": "snowluma_started"})
    return {"ok": True, "started": started}


@router.post("/system/snowluma/stop")
async def snowluma_stop(request: Request, container: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state
    async with state.process_lock:
        names = await _container_names(state, container)
        if names is not None:
            stopped = await _docker_all(names, "stop")
            await _sync_connections(state, names)
        else:
            stopped = await _stop(await _snowluma_folder(state))
    if stopped:
        state.broadcaster.publish("system", {"change": "snowluma_stopped"})
    return {"ok": True, "stopped": stopped}


@router.post("/system/snowluma/restart")
async def snowluma_restart(request: Request, container: str | None = Query(default=None)) -> dict[str, Any]:
    state = request.app.state
    async with state.process_lock:
        names = await _container_names(state, container)
        if names is not None:
            await _docker_all(names, "restart")
            await _sync_connections(state, names)
            state.broadcaster.publish("system", {"change": "snowluma_restarted"})
            return {"ok": True}
        folder = await _snowluma_folder(state)
        stopped = await _stop(folder)
        try:
            started = await _start(state, folder)
            if not started:
                # Still answering on its port after its processes ended: not one nest can identify.
                raise HTTPException(
                    status_code=409, detail="SnowLuma 的端口还被占用（可能是别的程序），没有重新启动"
                )
        except HTTPException:
            if stopped:
                state.broadcaster.publish("system", {"change": "snowluma_stopped"})
            raise
    state.broadcaster.publish("system", {"change": "snowluma_restarted"})
    return {"ok": True}


@router.get("/system/qq/login")
async def qq_login(request: Request, container: str = Query(...)) -> dict[str, Any]:
    """What to show for scanning container's login QR code in the console: the code itself while QQ waits
    for a scan, or that the account is connected. The console asks again every few seconds; while it does,
    nest.qr_guard leaves the container waiting for the scan."""
    state = request.app.state
    names = await _container_names(state, container)
    if names is None:
        raise HTTPException(status_code=400, detail="QQ 不在 Docker 容器里，没有登录二维码")
    state.qr_guard.want(container)
    box = await asyncio.to_thread(docker.container, container)
    if box.problem:
        raise HTTPException(status_code=400, detail=box.problem)
    views = state.hub.account_views()
    account = next((v for v in views if box.account and v["self_id"] == box.account), None)
    view: dict[str, Any] = {
        "container": container,
        "self_id": box.account,
        "name": account["name"] if account else "",
        "logged_in": False,
        "running": box.found and box.running,  # stopped (by nest.qr_guard, or by hand): start it to scan
        "qr": None,  # a data: URL of the PNG
        "detail": "",
    }
    who = view["name"] or box.account or container
    if not box.found or not box.running:
        return view | {"detail": f"容器 {container} 没有在运行：先在上面启动它"}
    if account and account["status"]["state"] == "connected":
        return view | {"logged_in": True, "detail": f"{who} 已经登录，nest 也连上了"}
    try:
        found = await asyncio.to_thread(docker.login_qr, container)
    except system.ProcessError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    try:
        log = await asyncio.to_thread(docker.login_log, container)
    except system.ProcessError:
        log = ""  # only an explanation: the view stands without it
    if found is None:
        trouble = napcat.login_trouble(log)
        if trouble:
            return view | {"detail": f"{who} 没登上：{trouble}"}
        return view | {"detail": f"{who} 现在没有在等扫码：可能正在用保存的登录自己登录，稍等一下"}
    image, age = found
    failed = napcat.scan_failed(log)
    if failed:
        return view | {"detail": f"{who} 没登上：{failed}"}
    if age > QR_FRESH:
        return view | {"detail": f"{who} 现在没有在等扫码：可能已经登上了，正在等 nest 连上"}
    return view | {
        "qr": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        "detail": f"用 {who} 的手机 QQ 扫码。二维码大约 2 分钟换一张，这里会自动换成新的",
    }


@router.post("/system/qq/open")
async def qq_open(request: Request) -> dict[str, Any]:
    state = request.app.state
    async with state.process_lock:
        if await _container_names(state) is not None:
            raise HTTPException(status_code=400, detail=QQ_IN_DOCKER)
        executable = (await machine_settings(state))["qq_executable"]
        if not system.is_qq_executable(executable):
            raise HTTPException(status_code=400, detail=NOT_SET_QQ)
        hide = await _hidden_names(state)
        try:
            await asyncio.to_thread(system.open_qq, executable, hide=hide)
        except system.ProcessError as exc:
            raise _failed(exc) from None
        log.info("已打开 QQ：%s", executable)
    state.broadcaster.publish("system", {"change": "qq_opened"})
    return {"ok": True}


# -- the live log ---------------------------------------------------------------------------------------


@router.get("/logs")
async def logs(
    request: Request, limit: int = Query(200, ge=1, le=BUFFER_SIZE), level: str = Query("INFO")
) -> list[dict[str, Any]]:
    """The newest records at or above `level`, newest last (new ones arrive as SSE "log" events)."""
    levelno = LEVELS.get(level.upper())
    if levelno is None:
        raise _invalid("日志级别只能是 DEBUG、INFO、WARNING 或 ERROR")
    return request.app.state.logs.records(level=levelno, limit=limit)
