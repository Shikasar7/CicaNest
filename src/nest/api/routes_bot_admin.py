"""增加 / 删除 bot (the console's Bot page). A platform other than QQ connects by itself: what the form holds
is tried, its secrets go to .env, and the bot starts as 只看不说.

With QQ in Docker, adding a bot sets up everything
for its QQ number: a NapCat container (nest.napcat: ports, OneBot config with new tokens, a host name and MAC
address that keep the login across restarts), the account and its connection, the bot itself (只看不说 until
switched to 正式发言) and the container in the 系统 page's list. The QQ is then logged in by scanning the code
shown on the console.

Removing a bot hides and silences it and disables its account; its container is stopped and deleted, but its
QQ data and every record (messages, decisions, usage) stay. Adding the same QQ number again brings it back,
with the same container name, host name and MAC address, so the saved login usually still works.

Both run under process_lock (docker) and config_lock (settings), in that order. Removing a bot of another
platform forgets its secrets too: adding it again means typing them again.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from nest import docker, housekeeping, napcat, system
from nest.api.auth import require_token
from nest.api.routes_accounts import check_fields, platform_spec, stored_config
from nest.api.routes_bots import _aliases, _name
from nest.api.routes_system_admin import MAX_CONTAINERS, container_names, machine_settings
from nest.storage.accounts import account_id as make_account_id
from nest.storage.accounts import get_account, upsert_account
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

QQ_NUMBER = re.compile(r"[1-9][0-9]{4,11}")


class BotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    self_id: str = ""  # QQ
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
    paths = await machine_settings(state)
    if paths["snowluma_mode"] != "docker":
        raise HTTPException(status_code=400, detail="只有 QQ 跑在 Docker 里时才能在这里增加 bot")
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
                    detail=f"已经有这个号的容器 {container}，但 nest 里没有它的连接设置：先在「系统」页用它",
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
            try:
                await asyncio.to_thread(docker.create, napcat.run_args(plan, webui_token=webui_token))
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
            try:
                boxes = await asyncio.to_thread(_boxes)
                removed = next((n for n, box in boxes.items() if box.account == bot.self_id), None)
                if removed is not None:
                    await asyncio.to_thread(docker.remove, removed)
            except system.ProcessError as exc:
                problem = f"bot 已删除，但它的 QQ 容器没删掉：{exc}"
                log.warning("%s", problem)
            listed = container_names(paths["snowluma_container"])
            if removed is not None and removed in listed:
                await _set_containers(state, [n for n in listed if n != removed])
    log.info("已删除 bot %s（%s），容器 %s；记录都保留", bot.name, bot.self_id, removed or "无")
    state.broadcaster.publish("bots", {"bot_id": bot_id})
    state.broadcaster.publish("system", {"change": "bot_removed"})
    return {"ok": True, "container": removed, "problem": problem}
