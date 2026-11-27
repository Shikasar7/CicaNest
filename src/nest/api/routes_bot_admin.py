"""增加 / 删除 bot (the console's Bot page). A platform other than QQ connects by itself: what the form holds
is tried, its secrets go to .env, and the bot starts as 只看不说.

With QQ in Docker, adding a bot sets up everything
for its QQ number: a NapCat container (nest.napcat: ports, OneBot config with new tokens, a host name and MAC
address that keep the login across restarts), the account and its connection, the bot itself (只看不说 until
switched to 正式发言) and the container in the 系统 page's list. The QQ is then logged in by scanning the code
shown on the console. The container is made from the image set on 平台接入 (「QQ 容器的镜像」): NapCat's own
left Docker Hub in 2026-11, so a machine without it is told what to do instead of seeing Docker's words.

A QQ account can also come with its own OneBot 11 endpoint (`onebot`: someone runs NapCat, a fork or another
protocol program themselves): nest tries it (the HTTP side must answer for this QQ number, the WebSocket must
open) and keeps the connection, with no container, whichever way QQ is set to run.

Removing a bot hides and silences it and disables its account; its container (one nest lists) is stopped and
deleted, but its QQ data and every record (messages, decisions, usage) stay. Adding the same QQ number again
brings it back with the same container name, host name and MAC address: the saved login usually works.

Both run under process_lock (docker) and config_lock (settings), in that order. Removing a bot of another
platform forgets its secrets too: adding it again means typing them again.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nest import backup, docker, housekeeping, napcat, system
from nest.adapters.onebot11 import probe
from nest.api.auth import require_token
from nest.api.routes_accounts import (
    TOKEN_LIMIT,
    URL_LIMIT,
    check_fields,
    checked_url,
    platform_spec,
    stored_config,
)
from nest.api.routes_bots import _aliases, _name
from nest.api.routes_system_admin import MAX_CONTAINERS, container_names, machine_settings
from nest.api.text import has_bad_chars
from nest.core import move_in
from nest.core.workspace import WorkspaceError
from nest.storage.accounts import account_id as make_account_id
from nest.storage.accounts import get_account, upsert_account
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

QQ_NUMBER = re.compile(r"[1-9][0-9]{4,11}")


class OwnOneBot(BaseModel):
    """A QQ account's OneBot 11 endpoint that someone runs themselves: one token for both sides, or none."""

    model_config = ConfigDict(extra="forbid")

    http_url: str = Field(max_length=URL_LIMIT)
    ws_url: str = Field(max_length=URL_LIMIT)
    token: str = Field(default="", max_length=TOKEN_LIMIT)


class BotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    self_id: str = ""  # QQ
    onebot: OwnOneBot | None = None  # QQ: its own endpoint instead of a container nest makes
    platform: str = "qq"
    fields: dict[str, str] = {}  # the platform's form, for anything but QQ
    aliases: list[str] = []
    provider_id: str | None = None


def _refs(self_id: str) -> tuple[str, str, str]:
    return (
        f"NEST_QQ_{self_id}_HTTP_TOKEN",
        f"NEST_QQ_{self_id}_WS_TOKEN",
        f"NEST_NAPCAT_{self_id}_WEBUI_TOKEN",
    )


def _boxes() -> dict[str, docker.Container]:
    """Blocking: every container, by name."""
    return {name: docker.container(name) for name in docker.names()}


async def _set_containers(state: Any, names: list[str]) -> None:
    await SettingsRepo(state.db).set_many({"snowluma_container": ", ".join(names)})


@router.post("/bots", status_code=201)
async def add_bot(request: Request, body: BotCreate) -> dict[str, Any]:
    state = request.app.state
    name = _name(body.name)
    aliases = _aliases([name, *body.aliases])
    if body.platform != "qq":
        return await _add_platform_bot(state, body, name, aliases)
    self_id = body.self_id.strip()
    if not QQ_NUMBER.fullmatch(self_id):
        raise HTTPException(status_code=422, detail="QQ 号要填 5–12 位数字")
    account = make_account_id("qq", self_id)
    if account in state.brain.bots:
        raise HTTPException(status_code=409, detail=f"已经有用 {self_id} 的 bot 了")
    if body.provider_id is not None and await state.repo.get_provider(body.provider_id) is None:
        raise HTTPException(status_code=400, detail="没有这个模型")
    if body.onebot is not None:
        return await _add_own_onebot(state, body, self_id, name, aliases)
    paths = await machine_settings(state)
    if paths["snowluma_mode"] != "docker":
        raise HTTPException(
            status_code=400,
            detail="QQ 设成了在这台电脑上运行 SnowLuma：选「接我自己运行的协议端」，填它的地址和令牌",
        )
    listed = container_names(paths["snowluma_container"])
    async with state.process_lock, state.config_lock:
        try:
            boxes = await asyncio.to_thread(_boxes)
        except system.ProcessError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        container = next((n for n, box in boxes.items() if box.account == self_id), None)
        http_ref, ws_ref, webui_ref = _refs(self_id)
        if container is not None:
            # The account's container is there (a bot added before, or one of the compose file's): use it.
            known = await get_account(state.db, account)
            if known is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"已经有这个号的容器 {container}，但 nest 里没有它的连接设置：先在「平台接入」页用它"
                    ),
                )
            http_url, ws_url = known.http_url, known.ws_url
            http_ref, ws_ref = known.http_token_ref, known.ws_token_ref
            if not boxes[container].running:
                await asyncio.to_thread(docker.act, container, "start")
        else:
            if len(listed) >= MAX_CONTAINERS:
                raise HTTPException(status_code=409, detail=f"最多管 {MAX_CONTAINERS} 个 QQ 容器")
            taken = {port for box in boxes.values() for _, port in box.ports}
            root = housekeeping.qq_cache_root(state.settings.home)
            try:
                plan = napcat.plan(self_id, root, taken)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            http_token, ws_token, webui_token = napcat.new_token(), napcat.new_token(), napcat.new_token()
            await asyncio.to_thread(napcat.write_config, plan, http_token, ws_token)
            for ref, value in ((http_ref, http_token), (ws_ref, ws_token), (webui_ref, webui_token)):
                state.secrets.set(ref, value)
            image = paths["napcat_image"]
            args = napcat.run_args(plan, webui_token=webui_token, image=image)
            try:
                await asyncio.to_thread(docker.create, args)
            except docker.ImageMissing as exc:
                log.error("建 QQ 容器 %s 失败，拿不到镜像 %s：%s", plan.name, image, exc)
                raise HTTPException(status_code=400, detail=image_missing(image)) from None
            except system.ProcessError as exc:
                log.error("建 QQ 容器 %s 失败：%s", plan.name, exc)
                raise HTTPException(status_code=500, detail=f"建 QQ 容器失败：{exc}") from None
            container, http_url, ws_url = plan.name, plan.http_url, plan.ws_url
            log.info(
                "已为 %s 建 QQ 容器 %s（OneBot %d/%d，管理页 %d）",
                self_id,
                container,
                plan.http_port,
                plan.ws_port,
                plan.webui_port,
            )
        await upsert_account(
            state.db,
            platform="qq",
            self_id=self_id,
            name=name,
            http_url=http_url,
            ws_url=ws_url,
            http_token_ref=http_ref,
            ws_token_ref=ws_ref,
            enabled=True,
        )
        await state.repo.upsert_bot(
            account_id=account, name=name, aliases=aliases, provider_id=body.provider_id, mode="observe"
        )
        if container not in listed:
            await _set_containers(state, [*listed, container])
        await state.brain.load()
        await state.hub.reconnect(account)
    log.info("已增加 bot %s（%s），容器 %s", name, self_id, container)
    state.broadcaster.publish("bots", {"bot_id": account})
    state.broadcaster.publish("system", {"change": "bot_added"})
    return {"bot_id": account, "container": container}


def image_missing(image: str) -> str:
    """What to do when Docker has no NapCat image to make a container from."""
    return (
        f"这台电脑上没有 QQ 容器的镜像 {image}，也下载不到。"
        "NapCat 在 2026 年 11 月停止了更新，官方镜像已经下架，nest 不自带它。"
        "可以在「平台接入」页的「QQ 容器的镜像」里填一个你能拿到的 NapCat 镜像"
        "（比如自己用 docker load 导入的），再加一次；"
        "或者自己运行一个 OneBot 11 协议端，选「接我自己运行的协议端」"
    )


async def _add_own_onebot(
    state: Any, body: BotCreate, self_id: str, name: str, aliases: list[str]
) -> dict[str, Any]:
    """A QQ account on an endpoint someone runs: tried, then kept like a container's, but no container."""
    assert body.onebot is not None
    http_url = checked_url("http", body.onebot.http_url)
    ws_url = checked_url("ws", body.onebot.ws_url)
    token = body.onebot.token.strip()
    if has_bad_chars(token):
        raise HTTPException(status_code=422, detail="令牌里有不能保存的字符")
    http, ws = await asyncio.gather(
        probe.check_http(http_url, token, self_id=self_id, transport=state.fetch_transport),
        probe.check_ws(ws_url, token),
    )
    failed = [
        f"{side}：{found.detail}" for side, found in (("HTTP", http), ("WebSocket", ws)) if not found.ok
    ]
    if failed:
        raise HTTPException(status_code=400, detail="；".join(failed))
    account = make_account_id("qq", self_id)
    http_ref, ws_ref, _ = _refs(self_id)
    async with state.config_lock:
        for ref in (http_ref, ws_ref):
            if token:
                state.secrets.set(ref, token)
            else:
                state.secrets.unset(ref)
        await upsert_account(
            state.db,
            platform="qq",
            self_id=self_id,
            name=name,
            http_url=http_url,
            ws_url=ws_url,
            http_token_ref=http_ref,
            ws_token_ref=ws_ref,
            enabled=True,
        )
        await state.repo.upsert_bot(
            account_id=account, name=name, aliases=aliases, provider_id=body.provider_id, mode="observe"
        )
        await state.brain.load()
        await state.hub.reconnect(account)
    log.info("已增加 bot %s（%s），连的是自己运行的协议端 %s", name, self_id, http_url)
    state.broadcaster.publish("bots", {"bot_id": account})
    state.broadcaster.publish("system", {"change": "bot_added"})
    return {"bot_id": account, "container": None}


async def _add_platform_bot(state: Any, body: BotCreate, name: str, aliases: list[str]) -> dict[str, Any]:
    """Try the connection, then keep it: the secrets in .env, the account, and the bot, 只看不说."""
    spec = platform_spec(body.platform)
    if body.provider_id is not None and await state.repo.get_provider(body.provider_id) is None:
        raise HTTPException(status_code=400, detail="没有这个模型")
    fields = check_fields(spec, body.fields)
    identity = next((f.name for f in spec.fields if f.identity), "")
    if identity and make_account_id(spec.key, fields[identity]) in state.brain.bots:
        raise HTTPException(
            status_code=409, detail=f"已经有用 {fields[identity]} 的 bot 了"
        )  # before any probe
    found = await spec.probe(fields)
    if not found.ok:
        raise HTTPException(status_code=400, detail=found.detail)
    account = make_account_id(spec.key, found.self_id)
    refs = spec.refs(found.self_id)
    async with state.config_lock:
        if account in state.brain.bots:
            raise HTTPException(status_code=409, detail=f"已经有用 {found.self_id} 的 bot 了")
        for field, ref in refs.items():
            state.secrets.set(ref, fields[field])
        await upsert_account(
            state.db,
            platform=spec.key,
            self_id=found.self_id,
            name=name,
            http_url="",
            ws_url="",
            config=stored_config(spec, fields, refs),
            enabled=True,
        )
        await state.repo.upsert_bot(
            account_id=account, name=name, aliases=aliases, provider_id=body.provider_id, mode="observe"
        )
        await state.brain.load()
        await state.hub.reconnect(account)
    log.info("已增加 %s bot %s（%s）", spec.label, name, found.self_id)
    state.broadcaster.publish("bots", {"bot_id": account})
    state.broadcaster.publish("system", {"change": "bot_added"})
    return {"bot_id": account, "container": ""}


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion: str  # the bot id of one who lives only on the desk


@router.post("/bots/{bot_id}/move-in")
async def take_in(request: Request, bot_id: str, body: MoveIn) -> dict[str, Any]:
    """接上平台账号: the companion moves into this bot, added for her just before, with everything of hers
    (nest.core.move_in). A copy of the database is made first."""
    state = request.app.state
    async with state.config_lock:
        bot, companion = state.brain.bots.get(bot_id), state.brain.bots.get(body.companion)
        if bot is None or companion is None:
            raise HTTPException(status_code=404, detail="没有这个 bot")
        if why := await move_in.problem(state.db, companion, bot):
            raise HTTPException(status_code=409, detail=why)
        if any(state.brain.busy(b.id, channel) for b in (bot, companion) for channel in b.channels):
            raise HTTPException(status_code=409, detail=f"{companion.name}正在想怎么回，等她说完")
        settings = state.settings
        copy = await asyncio.to_thread(backup.before, settings.data_dir, settings.db_path, "move-in")
        if state.sandbox is not None:
            await state.sandbox.forget(companion.id)
        try:
            await asyncio.to_thread(state.workspace.move, companion.id, bot.id)
        except (WorkspaceError, OSError) as exc:
            raise HTTPException(status_code=409, detail=f"她的文件夹搬不过去：{exc}") from None
        try:
            await move_in.move_in(state.db, companion, bot)
        except BaseException as exc:
            await asyncio.to_thread(state.workspace.move, bot.id, companion.id)
            if isinstance(exc, move_in.MoveError):
                raise HTTPException(status_code=409, detail=str(exc)) from None
            raise
        await state.hub.forget(companion.account_id)
        await state.brain.load()
        state.keepsakes.changed(companion.id)
        state.keepsakes.changed(bot.id)
        await state.hub.reconnect(bot.account_id)
    log.info("%s 搬进了 %s，搬之前的数据库在 %s", companion.name, bot.account_id, copy.name)
    state.broadcaster.publish("bots", {"bot_id": bot.id, "was": companion.id})
    return {"bot_id": bot.id, "backup": copy.name}


@router.delete("/bots/{bot_id}")
async def remove_bot(request: Request, bot_id: str) -> dict[str, Any]:
    state = request.app.state
    bot = state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    paths = await machine_settings(state)
    problem, removed = "", None
    async with state.process_lock, state.config_lock:
        await state.repo.remove_bot(bot_id)
        await state.hub.forget(bot.account_id)
        await state.brain.load()
        account = await get_account(state.db, bot.account_id)
        for ref in account.refs.values() if account is not None else ():
            state.secrets.unset(ref)
        if paths["snowluma_mode"] == "docker" and bot.account_id.startswith("qq:"):
            listed = container_names(paths["snowluma_container"])
            try:
                boxes = await asyncio.to_thread(_boxes)
                # only a container nest lists: one someone runs for their own endpoint stays theirs
                removed = next(
                    (n for n, box in boxes.items() if box.account == bot.self_id and n in listed), None
                )
                if removed is not None:
                    await asyncio.to_thread(docker.remove, removed)
            except system.ProcessError as exc:
                problem = f"bot 已删除，但它的 QQ 容器没删掉：{exc}"
                log.warning("%s", problem)
            if removed is not None and removed in listed:
                await _set_containers(state, [n for n in listed if n != removed])
    log.info("已删除 bot %s（%s），容器 %s；记录都保留", bot.name, bot.self_id, removed or "无")
    state.broadcaster.publish("bots", {"bot_id": bot_id})
    state.broadcaster.publish("system", {"change": "bot_removed"})
    return {"ok": True, "container": removed, "problem": problem}
