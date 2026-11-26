"""人设方案: save which persona a bot uses in each of its places, and switch the whole set back in one go.

A set also keeps who the bot is when it was saved: her name (and aliases), voice and model, and a profile
picture. So one QQ number can carry two characters, switched with one click: switching
renames the bot, and where the platform allows it (set_profile) puts the name on as the QQ nickname (and the
group card, where the account has one) and the set's picture on as the QQ avatar.

The sets themselves are listed with each bot in GET /api/bots (persona_sets). Every change runs under
app.state.config_lock; switching ends with brain.load(), so the new personas take effect at once. The
profile is changed after that, outside the lock: it waits on QQ.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict

from nest.adapters.registry import can
from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.avatars import MAX_BYTES, image_type
from nest.storage.bots import NameTaken

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

NAME_LIMIT = 20
SETS_LIMIT = 20  # per bot
SetId = Annotated[int, Path(ge=1, le=2**63 - 1)]
PROFILE_TIMEOUT = 60.0  # nickname, avatar and every group card, one request each
# What QQ takes as an avatar: a GIF or WebP set as one may not show.
AVATAR_TYPES = ("image/png", "image/jpeg")


class SetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class SetPatch(BaseModel):
    """A new name, and/or resave=true to save the bot's current personas over the set."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    resave: bool = False


def _name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= NAME_LIMIT:
        raise HTTPException(status_code=422, detail=f"方案名字要填 1–{NAME_LIMIT} 个字")
    if has_bad_chars(value):
        raise HTTPException(status_code=422, detail="方案名字里不能有换行或控制字符")
    return value


def _taken(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"已经有叫「{name}」的方案")


def _bot(state: Any, bot_id: str) -> Any:
    bot = state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    return bot


def _missing() -> HTTPException:
    return HTTPException(status_code=404, detail="没有这个方案")


def set_views(bot: Any, sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For GET /api/bots: each set with the places the bot still has, and whether it is what the bot uses
    now (every such place has the persona the set names, and the bot has the name the set keeps)."""
    views = []
    for persona_set in sets:
        items = [
            {"channel_id": channel_id, "persona_id": persona_id}
            for channel_id, persona_id in persona_set["items"].items()
            if channel_id in bot.channels
        ]
        identity = persona_set.get("identity")
        current = (
            bool(items)
            and all(bot.channels[item["channel_id"]].persona_id == item["persona_id"] for item in items)
            and (identity is None or identity["name"] == bot.name)
        )
        views.append(
            {
                "id": persona_set["id"],
                "name": persona_set["name"],
                "items": items,
                "current": current,
                "identity": identity,
            }
        )
    return views


def _changed(state: Any, bot_id: str) -> None:
    state.broadcaster.publish("bots", {"bot_id": bot_id})


@router.post("/bots/{bot_id}/persona-sets", status_code=201)
async def create_set(request: Request, bot_id: str, body: SetCreate) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        bot = _bot(state, bot_id)
        name = _name(body.name)
        if not bot.channels:
            raise HTTPException(status_code=400, detail="这个 bot 还没有群或私聊，没有人设可存")
        if len((await state.repo.persona_sets()).get(bot_id, [])) >= SETS_LIMIT:
            detail = f"一个 bot 最多存 {SETS_LIMIT} 个方案，先删掉用不到的"
            raise HTTPException(status_code=400, detail=detail)
        try:
            set_id = await state.repo.create_persona_set(bot_id, name)
        except NameTaken:
            raise _taken(name) from None
        _changed(state, bot_id)
    return {"id": set_id}


@router.patch("/bots/{bot_id}/persona-sets/{set_id}")
async def update_set(request: Request, bot_id: str, set_id: SetId, patch: SetPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        if patch.name is None and not patch.resave:
            raise HTTPException(status_code=422, detail="没有要改的内容")
        name = _name(patch.name) if patch.name is not None else None
        try:
            updated = await state.repo.update_persona_set(bot_id, set_id, name=name, resave=patch.resave)
        except NameTaken:
            raise _taken(name or "") from None
        if not updated:
            raise _missing()
        _changed(state, bot_id)
    return {"ok": True}


@router.post("/bots/{bot_id}/persona-sets/{set_id}/apply")
async def apply_set(request: Request, bot_id: str, set_id: SetId) -> dict[str, Any]:
    """Switch to the set. changed counts the places whose persona changed; renamed, whether the bot's name,
    aliases, voice or model changed; problems, what could not be done to the account's profile (in words:
    the switch itself has happened)."""
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        switched = await state.repo.apply_persona_set(bot_id, set_id)
        if switched is None:
            raise _missing()
        await state.store.load_switches()
        await state.brain.load()
        _changed(state, bot_id)
        bot = _bot(state, bot_id)
    problems: list[str] = []
    if switched.name is not None and can(bot.account_id, "set_profile"):
        problems = await _wear(state, bot, switched.name, switched.avatar)
    return {"changed": switched.changed, "renamed": switched.renamed, "problems": problems}


async def _wear(state: Any, bot: Any, name: str, avatar: tuple[str, bytes] | None) -> list[str]:
    """Put the set's name and picture on the account; what did not work, in words."""
    adapter = state.hub.adapters.get(bot.account_id)
    if adapter is None or adapter.status().state != "connected":
        return ["账号没在线，昵称和头像没改：上线后再点一次「换成这套」"]
    groups = [ch.channel.id for ch in bot.channels.values() if ch.channel.kind == "group"]
    try:
        problems: list[str] = await asyncio.wait_for(
            adapter.set_profile(name, avatar[1] if avatar else None, groups), PROFILE_TIMEOUT
        )
    except TimeoutError:
        return ["改昵称和头像太久没有回音，稍后看看 QQ 上改了没有"]
    if avatar is not None and not any("头像" in problem for problem in problems):
        # The console shows the QQ avatar nest keeps: the one she wears now.
        await state.avatars.put(bot.id, "qq", avatar[0], avatar[1])
        state.broadcaster.publish("avatars", {"bot_id": bot.id})
    for problem in problems:
        log.info("%s 换上方案的资料时：%s", bot.name, problem)
    return problems


@router.get("/bots/{bot_id}/persona-sets/{set_id}/avatar")
async def set_avatar(request: Request, bot_id: str, set_id: SetId) -> Response:
    picture = await request.app.state.repo.persona_set_avatar(bot_id, set_id)
    if picture is None:
        raise HTTPException(status_code=404, detail="这个方案没有头像")
    # The console asks with ?v=<updated_at>, so a picture never changes under its URL.
    return Response(picture[1], media_type=picture[0], headers={"cache-control": "private, max-age=31536000"})


@router.put("/bots/{bot_id}/persona-sets/{set_id}/avatar")
async def upload_set_avatar(request: Request, bot_id: str, set_id: SetId) -> dict[str, Any]:
    """The set's profile picture is the raw request body (a PNG or JPEG; read from its first bytes)."""
    state = request.app.state
    length = request.headers.get("content-length", "")
    too_big = f"图片太大了（超过 {MAX_BYTES // (1024 * 1024)} MB），换一张小一点的"
    if length.isdigit() and int(length) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=too_big)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=too_big)
    data = bytes(body)
    content_type = image_type(data)
    if content_type not in AVATAR_TYPES:
        raise HTTPException(status_code=415, detail="头像只能用 PNG 或 JPEG 图片")
    async with state.config_lock:
        _bot(state, bot_id)
        if not await state.repo.set_persona_set_avatar(bot_id, set_id, (content_type, data)):
            raise _missing()
        _changed(state, bot_id)
    return {"ok": True}


@router.delete("/bots/{bot_id}/persona-sets/{set_id}/avatar")
async def delete_set_avatar(request: Request, bot_id: str, set_id: SetId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        if not await state.repo.set_persona_set_avatar(bot_id, set_id, None):
            raise _missing()
        _changed(state, bot_id)
    return {"ok": True}


@router.delete("/bots/{bot_id}/persona-sets/{set_id}")
async def delete_set(request: Request, bot_id: str, set_id: SetId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        if not await state.repo.delete_persona_set(bot_id, set_id):
            raise _missing()
        _changed(state, bot_id)
    return {"ok": True}
