"""桌面聊天 in the console and the desktop app: talk with a bot one to one (nest.core.desk).

Each bot shows one private chat here: the one chosen for it, else the first it answers. A bot that answers
nobody in private can be given a chat that only exists here. Reading the chat and hearing new messages use
what the console already has: GET /api/channels/{id}/messages and the "message" and "decision" events.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import DESK
from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.core.desk import (
    CHOSEN,
    DESK_PEER,
    MAX_CHARS,
    ORDER,
    asked_before,
    chat_of,
    chats_of,
    chosen_chats,
    from_app,
    open_chat,
    order,
    sort_bots,
)
from nest.core.desk_media import FILE_EXT, MAX_PICTURE, MAX_PICTURES, content_type
from nest.llm.speech import SpeechError
from nest.storage.bots import BotChannel, BotConfig
from nest.storage.settings import SettingsRepo

router = APIRouter(prefix="/api/desk", dependencies=[Depends(require_token)])

NO_BOT = "没有这个 bot"
NO_CHAT = "还没有和这个 bot 的聊天，先开一个"
ALOUD_CHARS = 300  # a longer message is read up to here
STICKER_LINE = re.compile(r"（发了表情：[^）]*）|\[表情：[^\]]*\]")
# 读出来: what was read aloud already (message id -> its audio), so reading it again costs nothing.
_read: OrderedDict[int, str] = OrderedDict()
READ_KEPT = 300
RETRY_LOOKBACK = 60
SEARCH_MOST = 50


class Words(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=MAX_CHARS)] = ""
    sticker_id: int | None = None  # a 表情包 from the collection instead of words
    # Pictures sent along, with or without words: their names, from POST /{bot_id}/pictures?keep=true.
    pictures: Annotated[list[str], Field(max_length=MAX_PICTURES)] = []


class Aloud(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: int


class Opening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: int | None = None


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_ids: Annotated[list[str], Field(max_length=200)]


async def _chosen(request: Request) -> dict[str, int]:
    return await chosen_chats(request.app.state.db)


def _bot(request: Request, bot_id: str) -> BotConfig:
    bot = request.app.state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail=NO_BOT)
    return bot


def _view(bot: BotConfig, chosen: int | None) -> dict[str, Any]:
    chat = chat_of(bot, chosen)
    return {
        "bot_id": bot.id,
        "name": bot.name,
        "mode": bot.mode,
        "desk_only": bot.account_id.partition(":")[0] == DESK,
        "has_model": bot.provider_id is not None,
        "channel_id": chat.channel_id if chat else None,
        "chats": [
            {
                "channel_id": c.channel_id,
                "peer": c.channel.id,
                "name": c.name,
                "persona": c.persona_name,
                "desk_only": c.channel.id == DESK_PEER,
            }
            for c in chats_of(bot)
        ],
    }


async def _last(request: Request, channel_id: int | None) -> dict[str, Any] | None:
    """The newest message of the chat, for the companion list (「她说的最后一句」)."""
    if channel_id is None:
        return None
    rows = await request.app.state.store.recent(channel_id, limit=RETRY_LOOKBACK)
    row = next((r for r in reversed(rows) if not r["recalled_at"]), None)
    if row is None:
        return None
    sticker = any(s.get("type") == "sticker" for s in row.get("segments") or [])
    return {"text": row["text"], "from_self": row["from_self"], "sent_at": row["sent_at"], "sticker": sticker}


@router.get("")
async def desk(request: Request) -> list[dict[str, Any]]:
    db = request.app.state.db
    chosen = await chosen_chats(db)
    bots = sort_bots(list(request.app.state.brain.bots.values()), await order(db))
    views = [_view(bot, chosen.get(bot.id)) for bot in bots]
    drafts = await request.app.state.keepsakes.store.drafts()
    for view in views:
        view["last"] = await _last(request, view["channel_id"])
        view["drafts"] = drafts.get(view["bot_id"], 0)  # 回忆草稿 waiting for the user
    return views


@router.post("/{bot_id}/chatter")
async def chatter(request: Request, bot_id: str) -> dict[str, Any]:
    """主动搭话: the desktop app has a moment (the user is at the computer, pausing): she may say something of
    her own, if she wants to and is within her limits (nest.core.chatter). {"said", "reason"}."""
    _bot(request, bot_id)
    return await request.app.state.chatter.maybe(bot_id)


@router.put("/order")
async def set_order(request: Request, body: Order) -> dict[str, Any]:
    """The order the desk lists the bots in (ids it does not know are dropped)."""
    state = request.app.state
    known = state.brain.bots
    wanted = [
        bot_id for n, bot_id in enumerate(body.bot_ids) if bot_id in known and bot_id not in body.bot_ids[:n]
    ]
    async with state.config_lock:
        await SettingsRepo(state.db).set_many({ORDER: wanted})
    return {"ok": True, "bot_ids": wanted}


@router.post("/{bot_id}/open", status_code=201)
async def open_desk_chat(request: Request, bot_id: str, body: Opening) -> dict[str, Any]:
    """Give the bot a chat that only exists here (for a bot that answers nobody in private)."""
    state = request.app.state
    async with state.config_lock:
        bot = _bot(request, bot_id)
        if body.persona_id is not None and await state.repo.get_persona(body.persona_id) is None:
            raise HTTPException(status_code=400, detail="没有这个人设")
        if any(c.channel.id == DESK_PEER for c in bot.channels.values() if c.channel.kind == "private"):
            raise HTTPException(status_code=409, detail=f"已经有和 {bot.name} 的桌面聊天了")
        channel_id = await open_chat(state.repo, state.store, bot, persona_id=body.persona_id, remember=False)
        await state.brain.load()
        state.broadcaster.publish("bots", {"bot_id": bot_id})
    return {"ok": True, "channel_id": channel_id}


@router.put("/{bot_id}/chat")
async def choose_chat(request: Request, bot_id: str, body: Choice) -> dict[str, Any]:
    """Which of the bot's private chats its desk window shows."""
    state = request.app.state
    async with state.config_lock:
        bot = _bot(request, bot_id)
        if body.channel_id not in {c.channel_id for c in chats_of(bot)}:
            raise HTTPException(status_code=404, detail="没有这段私聊")
        chosen = await _chosen(request) | {bot_id: body.channel_id}
        await SettingsRepo(state.db).set_many({CHOSEN: chosen})
    return _view(bot, body.channel_id)


@router.post("/{bot_id}/messages", status_code=202)
async def say(request: Request, bot_id: str, body: Words) -> dict[str, Any]:
    """Say something to the bot, or send her a sticker. Her answer arrives as a "message" event of the same
    chat."""
    state = request.app.state
    bot = _bot(request, bot_id)
    text = body.text.replace("\r\n", "\n").strip()
    sticker: tuple[int, str] | None = None
    if body.sticker_id is not None:
        if body.pictures:
            raise HTTPException(status_code=422, detail="表情和图片分开发吧")
        row = await state.stickers.store.get(body.sticker_id)
        if row is None:
            raise HTTPException(status_code=404, detail="没有这张表情")
        sticker = (row["id"], state.stickers.describe_choice(row) if row["status"] == "ready" else "表情")
        text = ""
    elif not text and not body.pictures:
        raise HTTPException(status_code=422, detail="说点什么吧")
    if has_bad_chars(text, allow="\n\t"):
        raise HTTPException(status_code=422, detail="里面有不能发的控制字符")
    pictures = list(dict.fromkeys(body.pictures))
    if not all(state.desk_media.picture(name) for name in pictures):
        raise HTTPException(status_code=404, detail="有张图已经不在了，重新放一次")
    chat = await _talkable(request, bot)
    message_id = await state.desk.say(chat, text, sticker, pictures)
    return {"ok": True, "channel_id": chat.channel_id, "message_id": message_id}


@router.delete("/{bot_id}/messages/{message_id}")
async def take_back(request: Request, bot_id: str, message_id: int) -> dict[str, Any]:
    """撤回: take back something you typed in her chat. Its words are removed, and she does not answer it (or
    think of it later)."""
    state = request.app.state
    chats = {c.channel_id: c for c in chats_of(_bot(request, bot_id))}
    row = await state.store.message(message_id)
    if row is None or row["channel_id"] not in chats:
        raise HTTPException(status_code=404, detail="没有这条消息")
    if row["from_self"] or not from_app(row["external_id"]):
        raise HTTPException(status_code=409, detail="只能撤回你自己在这里说的")
    if row["recalled_at"] is None:
        await state.desk.take_back([chats[row["channel_id"]].channel], row["external_id"])
    return {"ok": True}


@router.post("/{bot_id}/messages/{message_id}/retry", status_code=202)
async def retry(request: Request, bot_id: str, message_id: int) -> dict[str, Any]:
    """重说: her newest answer is taken back and she answers again."""
    state = request.app.state
    bot = _bot(request, bot_id)
    chat = await _talkable(request, bot)
    if state.brain.busy(bot.id, chat.channel_id):
        raise HTTPException(status_code=409, detail=f"{bot.name}正在想怎么回，等她说完")
    answers, asked = asked_before(await state.store.recent(chat.channel_id, limit=RETRY_LOOKBACK))
    if message_id not in {row["id"] for row in answers}:
        raise HTTPException(status_code=409, detail="只能让她把最新的一条重说一遍")
    said = "\n".join(row["text"] for row in answers if row["text"])
    for row in answers:
        await state.desk.take_back([chat.channel], row["external_id"])
    state.brain.again(bot, chat, asked, said)
    return {"ok": True, "channel_id": chat.channel_id}


@router.get("/{bot_id}/search")
async def search(request: Request, bot_id: str, q: Annotated[str, Query(max_length=100)]) -> dict[str, Any]:
    """Look through her chat (the one the window shows): the newest messages with every word of `q`."""
    bot = _bot(request, bot_id)
    chat = chat_of(bot, (await _chosen(request)).get(bot.id))
    found = await request.app.state.store.find(chat.channel_id, q.split(), limit=SEARCH_MOST) if chat else []
    return {
        "items": [{k: r[k] for k in ("id", "from_self", "sender_name", "text", "sent_at")} for r in found]
    }


async def _talkable(request: Request, bot: BotConfig) -> BotChannel:
    """Her desk chat, when she would answer in it; HTTPException when not."""
    chat = chat_of(bot, (await _chosen(request)).get(bot.id))
    if chat is None:
        raise HTTPException(status_code=409, detail=NO_CHAT)
    if bot.mode == "paused":
        raise HTTPException(status_code=409, detail=f"{bot.name} 已暂停，不会回话")
    if bot.mode != "live":
        raise HTTPException(status_code=409, detail=f"{bot.name} 现在只看不说，不会回话")
    return chat


async def _body(request: Request, limit: int, too_big: str) -> bytes:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise HTTPException(status_code=413, detail=too_big)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status_code=413, detail=too_big)
    return bytes(body)


@router.post("/{bot_id}/pictures", status_code=202)
async def send_picture(request: Request, bot_id: str, keep: bool = False) -> dict[str, Any]:
    """Send her a picture: the raw request body (PNG, JPEG, GIF or WebP, read from its first bytes). She
    sees it through the vision model, like a picture in any chat. `keep`: only keep it, for a message that
    is sent with it afterwards (`name` in its pictures)."""
    state = request.app.state
    bot = _bot(request, bot_id)
    chat = await _talkable(request, bot)
    data = await _body(request, MAX_PICTURE, "图片太大了，10 MB 以内")
    name = await asyncio.to_thread(state.desk_media.save_picture, data)
    if name is None:
        raise HTTPException(status_code=415, detail="只能发 PNG、JPEG、GIF 或 WebP 图片")
    if keep:
        return {"ok": True, "name": name}
    message_id = await state.desk.say(chat, "", pictures=[name])
    return {"ok": True, "channel_id": chat.channel_id, "message_id": message_id}


@router.get("/media/{name}")
async def media(request: Request, name: str) -> FileResponse:
    """A picture or voice of the desk chat (nest.core.desk_media)."""
    found = request.app.state.desk_media.path(name)
    if found is None or name.endswith(f".{FILE_EXT}"):
        raise HTTPException(status_code=404, detail="这个文件已经不在了")
    return FileResponse(
        found, media_type=content_type(name), headers={"cache-control": "private, max-age=604800, immutable"}
    )


@router.post("/{bot_id}/read-aloud")
async def read_aloud(request: Request, bot_id: str, body: Aloud) -> dict[str, Any]:
    """读出来: one of her messages in her voice. {"media": file name, served from /api/desk/media/}."""
    state = request.app.state
    bot = _bot(request, bot_id)
    if body.message_id in _read and state.desk_media.path(_read[body.message_id]):
        return {"media": _read[body.message_id]}
    if not state.speech.ready(bot.voice):
        raise HTTPException(status_code=409, detail=f"{bot.name}还没有声音：在 Bot 页给她选一个音色")
    chats = {c.channel_id for c in chats_of(bot)}
    row = await state.store.message(body.message_id)
    if row is None or row["channel_id"] not in chats or not row["from_self"]:
        raise HTTPException(status_code=404, detail="没有她说的这句话")
    text = " ".join(STICKER_LINE.sub("", row["text"] or "").split())[:ALOUD_CHARS]
    if not text:
        raise HTTPException(status_code=409, detail="这句没有可以读的字")
    try:
        audio = await state.speech.say(bot.voice, text)
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    name = await asyncio.to_thread(state.desk_media.save_audio, audio)
    if name is None:
        raise HTTPException(status_code=502, detail="语音服务给的不是认得的音频")
    _read[body.message_id] = name
    while len(_read) > READ_KEPT:
        _read.popitem(last=False)
    return {"media": name}
