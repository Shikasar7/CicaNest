"""Bots, their per-group and per-person (private chat) settings, model health, and the timeline of a group
or private chat (messages + decisions)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import can, capabilities_of, check_user
from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.core.events import ChannelInfo, ChannelRef
from nest.core.speech import split_ref
from nest.storage.bots import MAX_REPLY_LINES, UNSET
from nest.storage.tones import ToneRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

GROUP_LIST_TIMEOUT = 5.0  # asking QQ for the account's group list; the stored list is the fallback
NAME_LIMIT = 20  # a bot's name and each alias
ALIAS_LIMIT = 10
CHANNEL_NAME_LIMIT = 40


class BotPatch(BaseModel):
    """Every field optional; null means "not sent", except provider_id and backup_provider_id, where null
    removes the model (told apart with model_fields_set)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["observe", "live", "paused"] | None = None
    name: str | None = None
    aliases: list[str] | None = None
    provider_id: str | None = None
    backup_provider_id: str | None = None
    voice: Annotated[str, Field(max_length=260)] | None = None  # "<service>:<voice id>"; "" removes it


class ChannelPatch(BaseModel):
    """null means "not sent", except persona_id, where null removes the persona (model_fields_set)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    activity: Literal["mentions", "occasional", "active"] | None = None
    cooldown_seconds: Annotated[int, Field(strict=True, ge=0, le=600)] | None = None
    # Bounded like an SQLite id: a larger number cannot even be looked up.
    persona_id: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)] | None = None
    sticker_mode: Literal["off", "trial", "on"] | None = None  # 发表情
    voice_mode: Literal["off", "trial", "on"] | None = None  # 发语音
    voice_rate: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None  # 语音频率, percent
    reply_lines: Annotated[int, Field(strict=True, ge=1, le=MAX_REPLY_LINES)] | None = None  # 一次最多发
    split_rate: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None  # 分条频率, percent
    use_slang: bool | None = None  # 用黑话
    answer_questions: bool | None = None  # 答题
    # 语气 keys (GET /api/tones): for 答题, and for everyday chat.
    answer_tone: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    chat_tone: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    recorder: bool | None = None  # 问题记录官 (groups only; one bot per group)


class ChannelCreate(BaseModel):
    """Take part in another group the bot's account is in."""

    model_config = ConfigDict(extra="forbid")

    channel_id: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    activity: Literal["mentions", "occasional", "active"]
    persona_id: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)] | None = None


class ChannelName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class PrivateCreate(BaseModel):
    """Answer one person's private messages: the bot replies to everything they send it."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    persona_id: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)] | None = None


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= NAME_LIMIT:
        raise _invalid(f"名字要填 1–{NAME_LIMIT} 个字")
    if has_bad_chars(value):
        raise _invalid("名字里不能有换行或控制字符")
    return value


def _aliases(values: list[str]) -> list[str]:
    """Trimmed, empty ones dropped, duplicates dropped keeping the first."""
    aliases: list[str] = []
    for value in values:
        value = value.strip()
        if not value or value in aliases:
            continue
        if has_bad_chars(value):
            raise _invalid("别名里不能有换行或控制字符")
        aliases.append(value)
        if len(value) > NAME_LIMIT or len(aliases) > ALIAS_LIMIT:
            raise _invalid(f"别名最多 {ALIAS_LIMIT} 个，每个最多 {NAME_LIMIT} 字")
    return aliases


FOREIGN_RECENT = 30 * 60.0


def _recent(at: float | None, window: float) -> float | None:
    return at if at is not None and time.time() - at <= window else None


def _start_of_day() -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))


@router.get("/bots")
async def bots(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    today = await state.repo.outcome_counts(_start_of_day())
    last_lines = await state.repo.last_lines()
    # Group names arrive after the bots are loaded (when each account connects), so read them fresh.
    names = {c["id"]: c["name"] for c in await state.store.channels()}
    views = []
    for bot in state.brain.bots.values():
        provider = state.providers.get(bot.provider_id)
        views.append(
            {
                "id": bot.id,
                "name": bot.name,
                "self_id": bot.self_id,
                "platform": bot.account_id.partition(":")[0],
                "capabilities": sorted(capabilities_of(bot.account_id.partition(":")[0])),
                "aliases": list(bot.aliases),
                "voice": bot.voice,
                "mode": bot.mode,
                # Set while "provider" is null: that model failed to load.
                "provider_id": bot.provider_id,
                "provider": provider.health_view() if provider else None,
                "backup_provider_id": bot.backup_provider_id,
                "today": today.get(bot.id, {}),
                # The overview's speech bubble: the recent-activity feed may not reach back that far.
                "last_line": last_lines.get(bot.id),
                # Only recent sightings matter: an old one is history, not a live conflict.
                "foreign_at": _recent(state.brain.foreign_at.get(bot.id), FOREIGN_RECENT),
                "channels": [
                    {
                        "channel_id": ch.channel_id,
                        "kind": ch.channel.kind,
                        "group_id": ch.channel.id,
                        "name": names.get(ch.channel_id) or ch.name,
                        "enabled": ch.enabled,
                        "activity": ch.activity,
                        "persona_id": ch.persona_id,
                        "persona_name": ch.persona_name,
                        "persona": ch.persona,
                        "cooldown_seconds": ch.cooldown_seconds,
                        "sticker_mode": ch.sticker_mode,
                        "voice_mode": ch.voice_mode,
                        "voice_rate": ch.voice_rate,
                        "reply_lines": ch.reply_lines,
                        "split_rate": ch.split_rate,
                        "use_slang": ch.use_slang,
                        "answer_questions": ch.answer_questions,
                        "answer_tone": ch.answer_tone,
                        "chat_tone": ch.chat_tone,
                        "recorder": ch.recorder,
                    }
                    for ch in sorted(bot.channels.values(), key=lambda c: names.get(c.channel_id) or c.name)
                ],
            }
        )
    return views


@router.patch("/bots/{bot_id}")
async def update_bot(request: Request, bot_id: str, patch: BotPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        provider_sent = "provider_id" in patch.model_fields_set
        backup_sent = "backup_provider_id" in patch.model_fields_set
        if (
            patch.mode is None
            and patch.name is None
            and patch.aliases is None
            and patch.voice is None
            and not provider_sent
            and not backup_sent
        ):
            raise _invalid("没有要改的内容")
        bot = state.brain.bots.get(bot_id)
        if bot is None:
            raise HTTPException(status_code=404, detail="没有这个 bot")
        name = _name(patch.name) if patch.name is not None else None
        aliases = _aliases(patch.aliases) if patch.aliases is not None else None
        provider_id = patch.provider_id if provider_sent else bot.provider_id
        if (
            provider_sent
            and provider_id is not None
            and (has_bad_chars(provider_id) or await state.repo.get_provider(provider_id) is None)
        ):
            raise HTTPException(status_code=400, detail="没有这个模型")
        backup = patch.backup_provider_id
        if backup_sent and backup is not None:
            if has_bad_chars(backup) or await state.repo.get_provider(backup) is None:
                raise HTTPException(status_code=400, detail="没有这个备用模型")
            if backup == provider_id:
                raise _invalid("备用模型要选另一个模型")
        # Judged on the result: going live, or switching the model of a live bot, needs a model that
        # loaded. A name or alias edit of a live bot whose model is broken is still allowed.
        mode = patch.mode or bot.mode
        switched = provider_sent and provider_id != bot.provider_id
        if mode == "live" and (patch.mode == "live" or switched) and state.providers.get(provider_id) is None:
            raise HTTPException(status_code=400, detail="正式发言的 bot 必须有可用的模型")
        await state.repo.update_bot(
            bot_id,
            name=name,
            aliases=aliases,
            provider_id=provider_id if provider_sent else UNSET,
            backup_provider_id=backup if backup_sent else UNSET,
            mode=patch.mode,
            voice=_voice(patch.voice) if patch.voice is not None else None,
        )
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": bot_id})
    return {"ok": True}


def _voice(value: str) -> str:
    value = value.strip()
    if value and (not split_ref(value)[0] or has_bad_chars(value)):
        raise HTTPException(
            status_code=422, detail="音色写成「服务:音色 ID」，服务是 fish、minimax 或 openai"
        )
    return value


@router.post("/bots/pause-all")
async def pause_all(request: Request) -> dict[str, Any]:
    """Emergency stop: every bot to paused (no model calls, nothing sent)."""
    state = request.app.state
    async with state.config_lock:
        for bot_id in list(state.brain.bots):
            await state.repo.set_mode(bot_id, "paused")
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": None})
    return {"ok": True, "paused": len(state.brain.bots)}


def _within_capabilities(account: str, patch: ChannelPatch | ChannelCreate) -> None:
    """What the platform cannot do cannot be switched on: the console hides these, this is for the rest."""

    def refuse(detail: str) -> None:
        raise HTTPException(status_code=400, detail=detail)

    if not can(account, "sees_all") and patch.activity not in (None, "mentions"):
        refuse("这个平台的 bot 只收得到找它的话，只能选「只回点名」")
    if isinstance(patch, ChannelCreate):
        return
    if not can(account, "sees_all") and patch.recorder:
        refuse("这个平台的 bot 只收得到找它的话，当不了问题记录官")
    if not can(account, "sees_all") and patch.use_slang:
        refuse("这个平台的 bot 只收得到找它的话，用不了黑话")
    if not can(account, "send_voice") and patch.voice_mode not in (None, "off"):
        refuse("这个平台发不了语音")
    if not can(account, "send_image") and patch.sticker_mode not in (None, "off"):
        refuse("这个平台发不了表情")


@router.patch("/bots/{bot_id}/channels/{channel_id}")
async def update_bot_channel(
    request: Request, bot_id: str, channel_id: int, patch: ChannelPatch
) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        bot = state.brain.bots.get(bot_id)
        if bot is None or channel_id not in bot.channels:
            raise HTTPException(status_code=404, detail="这个 bot 不在这个群")
        # Sent as null: no persona, the brain's default 你是群里的<name>。
        persona_id = patch.persona_id if "persona_id" in patch.model_fields_set else UNSET
        if isinstance(persona_id, int) and await state.repo.get_persona(persona_id) is None:
            raise HTTPException(status_code=400, detail="没有这个人设")
        if patch.recorder and bot.channels[channel_id].channel.kind != "group":
            raise HTTPException(status_code=400, detail="问题记录官只在群里当")
        _within_capabilities(bot.account_id, patch)
        for key in (patch.answer_tone, patch.chat_tone):
            if key is not None and await ToneRepo(state.db).get(key) is None:
                raise HTTPException(status_code=400, detail="没有这个语气")
        await state.repo.set_channel(
            bot_id,
            channel_id,
            enabled=patch.enabled,
            activity=patch.activity,
            persona_id=persona_id,
            cooldown_seconds=patch.cooldown_seconds,
            sticker_mode=patch.sticker_mode,
            voice_mode=patch.voice_mode,
            voice_rate=patch.voice_rate,
            reply_lines=patch.reply_lines,
            split_rate=patch.split_rate,
            use_slang=patch.use_slang,
            answer_questions=patch.answer_questions,
            answer_tone=patch.answer_tone,
            chat_tone=patch.chat_tone,
            recorder=patch.recorder,
        )
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": bot_id})
    return {"ok": True}


@router.get("/bots/{bot_id}/groups")
async def bot_groups(request: Request, bot_id: str) -> dict[str, Any]:
    """Every group the bot's account is in, and whether the bot takes part there. A platform with no list
    of them has only the groups a message came from."""
    state = request.app.state
    bot = state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    live: set[int] | None = None
    adapter = state.hub.adapters.get(bot.account_id)
    online = adapter is not None and adapter.status().state == "connected"
    if online and can(bot.account_id, "list_channels"):
        try:
            infos = await asyncio.wait_for(adapter.list_channels(), GROUP_LIST_TIMEOUT)
            await state.store.name_channels(infos)
            live = {await state.store.channel_id(info.channel) for info in infos}
        except Exception as exc:  # offline or slow: the list stored at the last connection will do
            log.info("读取 %s 的群列表失败：%s", bot.account_id, type(exc).__name__)
    rows = [
        row
        for row in await state.store.channels()
        if row["account_id"] == bot.account_id
        and row["kind"] == "group"
        and (live is None or row["id"] in live or row["id"] in bot.channels)
    ]
    groups = [
        {
            "channel_id": row["id"],
            "group_id": row["external_id"],
            "name": row["name"],
            "joined": row["id"] in bot.channels,
        }
        for row in rows
    ]
    groups.sort(key=lambda g: (g["joined"], g["name"] or "~", g["group_id"]))
    return {"fresh": live is not None, "groups": groups}


@router.patch("/channels/{channel_id}")
async def name_channel(request: Request, channel_id: int, body: ChannelName) -> dict[str, Any]:
    """A group's name, from the user: 企业微信 tells nest none. Where the platform has a list of its groups,
    that list decides, and this would be overwritten the next time it connects."""
    state = request.app.state
    name = body.name.strip()
    if not 1 <= len(name) <= CHANNEL_NAME_LIMIT or has_bad_chars(name):
        raise _invalid(f"群名要填 1–{CHANNEL_NAME_LIMIT} 个字，不能有换行")
    async with state.config_lock:
        channel = next((c for c in await state.store.channels() if c["id"] == channel_id), None)
        if channel is None or channel["kind"] != "group":
            raise HTTPException(status_code=404, detail="没有这个群")
        if can(channel["account_id"], "list_channels"):
            raise HTTPException(status_code=400, detail="这个群的名字跟着平台走，不用在这里改")
        platform, self_id = channel["account_id"].split(":", 1)
        ref = ChannelRef(platform, self_id, "group", channel["external_id"])
        await state.store.name_channels([ChannelInfo(ref, name)])
    state.broadcaster.publish("channels", {"account_id": channel["account_id"]})
    state.broadcaster.publish("bots", {"bot_id": None})
    return {"ok": True, "name": name}


@router.post("/bots/{bot_id}/channels", status_code=201)
async def add_bot_channel(request: Request, bot_id: str, body: ChannelCreate) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        bot = state.brain.bots.get(bot_id)
        if bot is None:
            raise HTTPException(status_code=404, detail="没有这个 bot")
        channel = next((c for c in await state.store.channels() if c["id"] == body.channel_id), None)
        if channel is None or channel["account_id"] != bot.account_id or channel["kind"] != "group":
            raise HTTPException(status_code=400, detail=f"{bot.name} 的号不在这个群里")
        if body.channel_id in bot.channels:
            raise HTTPException(status_code=409, detail=f"{bot.name} 已经在这个群了")
        _within_capabilities(bot.account_id, body)
        if body.persona_id is not None and await state.repo.get_persona(body.persona_id) is None:
            raise HTTPException(status_code=400, detail="没有这个人设")
        await state.repo.set_channel(
            bot_id,
            body.channel_id,
            enabled=True,
            activity=body.activity,
            persona_id=UNSET if body.persona_id is None else body.persona_id,
        )
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": bot_id})
    return {"ok": True, "channel_id": body.channel_id}


@router.post("/bots/{bot_id}/private", status_code=201)
async def add_private(request: Request, bot_id: str, body: PrivateCreate) -> dict[str, Any]:
    """Reply to one person's private messages (only people added here: the bot ignores everyone else)."""
    state = request.app.state
    async with state.config_lock:
        bot = state.brain.bots.get(bot_id)
        if bot is None:
            raise HTTPException(status_code=404, detail="没有这个 bot")
        try:
            user = check_user(bot.account_id.partition(":")[0], body.user_id)
        except ValueError as exc:
            raise _invalid(str(exc)) from None
        if user in state.brain.our_accounts:
            raise _invalid("这是我们自己的 bot 号")
        if body.persona_id is not None and await state.repo.get_persona(body.persona_id) is None:
            raise HTTPException(status_code=400, detail="没有这个人设")
        account = state.hub.accounts.get(bot.account_id)
        platform = account.platform if account else bot.account_id.split(":", 1)[0]
        channel_id = await state.store.channel_id(ChannelRef(platform, bot.self_id, "private", user))
        if channel_id in bot.channels:
            raise HTTPException(status_code=409, detail=f"{bot.name} 已经会回 {user} 的私聊了")
        await state.repo.set_channel(
            bot_id,
            channel_id,
            enabled=True,
            activity="mentions",  # unused in a private chat: every message there calls the bot
            persona_id=UNSET if body.persona_id is None else body.persona_id,
            cooldown_seconds=0,
        )
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": bot_id})
    return {"ok": True, "channel_id": channel_id}


def group_key(channel: dict[str, Any]) -> str:
    """Groups are merged across our accounts (one real group); a private chat is one account's own."""
    platform = channel["account_id"].split(":", 1)[0]
    if channel["kind"] == "private":
        owner = channel["account_id"].split(":", 1)[1]
        return f"{platform}:private:{owner}:{channel['external_id']}"
    return f"{platform}:{channel['kind']}:{channel['external_id']}"


@router.get("/groups")
async def groups(request: Request) -> list[dict[str, Any]]:
    """Channels merged by real group: the same QQ group seen by three bots is one entry."""
    state = request.app.state
    names = {a["id"]: a["name"] or a["self_id"] for a in state.hub.account_views()}
    merged: dict[str, dict[str, Any]] = {}
    for channel in await state.store.channels():
        key = group_key(channel)
        entry = merged.setdefault(
            key,
            {
                "key": key,
                "kind": channel["kind"],
                "external_id": channel["external_id"],
                "name": "",
                "last_message_at": None,
                "channels": [],
            },
        )
        entry["name"] = entry["name"] or channel["name"]
        if channel["last_message_at"] and (entry["last_message_at"] or 0) < channel["last_message_at"]:
            entry["last_message_at"] = channel["last_message_at"]
        entry["channels"].append(
            {
                "channel_id": channel["id"],
                "account_id": channel["account_id"],
                "account_name": names.get(channel["account_id"], channel["account_id"]),
                "message_count": channel["message_count"],
            }
        )
    return sorted(merged.values(), key=lambda g: -(g["last_message_at"] or 0))


@router.get("/groups/{key}/timeline")
async def timeline(request: Request, key: str, limit: int = Query(80, ge=1, le=300)) -> dict[str, Any]:
    """Messages as seen by one of our accounts in the group, plus every bot's decisions there."""
    state = request.app.state
    members = [c for c in await state.store.channels() if group_key(c) == key]
    if not members:
        raise HTTPException(status_code=404, detail="没有这个群")
    viewer = max(members, key=lambda c: c["message_count"])
    messages = await state.store.recent(viewer["id"], limit=limit)
    since = messages[0]["sent_at"] if messages else None
    decisions = await state.repo.decisions([c["id"] for c in members], limit=limit * 3)
    if since is not None:
        decisions = [d for d in decisions if d["created_at"] >= since]
    names = {bot.id: bot.name for bot in state.brain.bots.values()}
    accounts = {a["id"]: a["name"] or a["self_id"] for a in state.hub.account_views()}
    return {
        "viewer": accounts.get(viewer["account_id"], viewer["account_id"]),
        "viewer_channel_id": viewer["id"],
        "channel_ids": [c["id"] for c in members],
        "messages": messages,
        "decisions": [d | {"bot_name": names.get(d["bot_id"], d["bot_id"])} for d in decisions],
    }
