"""Bot pictures: a bot's QQ avatar or an uploaded picture, shown in the console instead of its brush
character. And the user's own picture (ME), beside what they say in the chat window.

Pictures live in the database and are served from here, so the console never loads anything from outside.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.storage.avatars import MAX_BYTES, empty_view, image_type
from nest.watch.fetch import WatchError, fetch

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

QQ_AVATAR = "https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640"  # Tencent's public avatar address, by QQ number
QQ_TIMEOUT = 10.0
# The console asks for /avatar/<source>?v=<updated_at>, so a stored picture never changes under its URL.
CACHE = "private, max-age=31536000, immutable"
TOO_BIG = f"图片太大了（超过 {MAX_BYTES // (1024 * 1024)} MB），换一张小一点的"
NOT_AN_IMAGE = "只能用 PNG、JPEG、WebP 或 GIF 图片"
MISSING = {"qq": "还没有 QQ 头像，先获取一次", "custom": "还没有上传图片"}
# The user (聊天窗口里「你」): not a bot, so not in bot_avatars; one uploaded picture, a file in data/me/.
# Listed and served beside the bots' under this id, which no bot can have (theirs are "<platform>:<id>").
ME = "me"


def _me_file(request: Request) -> Path:
    return request.app.state.settings.data_dir / "me" / "avatar"


def _me_view(request: Request) -> dict[str, Any]:
    try:
        changed = _me_file(request).stat().st_mtime
    except OSError:
        return empty_view()
    return empty_view() | {"chosen": "custom", "custom": changed}


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chosen: Literal["glyph", "qq", "custom"]


def _bot(request: Request, bot_id: str) -> Any:
    bot = request.app.state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    return bot


async def _changed(request: Request, bot_id: str) -> dict[str, Any]:
    """The bot's pictures after a change, which every console showing the bot hears about."""
    request.app.state.broadcaster.publish("avatars", {"bot_id": bot_id})
    return (await request.app.state.avatars.views()).get(bot_id, empty_view())


@router.get("/avatars")
async def avatars(request: Request) -> dict[str, dict[str, Any]]:
    """Every bot -> which picture it shows ("chosen": "qq" | "custom" | null = its little face) and
    when each stored picture last changed (null: none stored)."""
    state = request.app.state
    stored = await state.avatars.views()
    return {bot_id: stored.get(bot_id, empty_view()) for bot_id in state.brain.bots} | {ME: _me_view(request)}


@router.get("/bots/{bot_id}/avatar/{source}")
async def avatar(request: Request, bot_id: str, source: Literal["qq", "custom"]) -> Response:
    if bot_id == ME:
        try:
            data = _me_file(request).read_bytes() if source == "custom" else b""
        except OSError:
            data = b""
        content_type = image_type(data)
        if content_type is None:
            raise HTTPException(status_code=404, detail=MISSING[source])
        return Response(data, media_type=content_type, headers={"cache-control": CACHE})
    picture = await request.app.state.avatars.get(bot_id, source)
    if picture is None:
        raise HTTPException(status_code=404, detail=MISSING[source])
    return Response(picture.data, media_type=picture.content_type, headers={"cache-control": CACHE})


@router.put("/bots/{bot_id}/avatar")
async def choose_avatar(request: Request, bot_id: str, choice: Choice) -> dict[str, Any]:
    _bot(request, bot_id)
    source = None if choice.chosen == "glyph" else choice.chosen
    if not await request.app.state.avatars.choose(bot_id, source):
        raise HTTPException(status_code=409, detail=MISSING[choice.chosen])
    return await _changed(request, bot_id)


@router.post("/bots/{bot_id}/avatar/qq")
async def fetch_qq_avatar(request: Request, bot_id: str) -> dict[str, Any]:
    """Fetch the bot's current QQ avatar (works while QQ is offline too: it comes from Tencent's servers)
    and show it."""
    bot = _bot(request, bot_id)
    if bot.account_id.partition(":")[0] != "qq" or not bot.self_id.isdigit():
        raise HTTPException(status_code=400, detail="只有 QQ 号才能获取 QQ 头像")
    try:
        page = await fetch(
            QQ_AVATAR.format(uin=bot.self_id),
            timeout=QQ_TIMEOUT,
            transport=request.app.state.fetch_transport,
            max_bytes=MAX_BYTES,
        )
    except WatchError as exc:
        raise HTTPException(status_code=502, detail=f"没能从 QQ 取到头像：{exc}") from None
    content_type = image_type(page.body)
    if content_type is None:
        raise HTTPException(status_code=502, detail="QQ 返回的不是图片，稍后再试")
    await request.app.state.avatars.put(bot_id, "qq", content_type, page.body)
    return await _changed(request, bot_id)


@router.put("/bots/{bot_id}/avatar/custom")
async def upload_avatar(request: Request, bot_id: str) -> dict[str, Any]:
    """The picture is the raw request body (the console sends the cropped square); its type is read from
    its first bytes, not from the request."""
    if bot_id != ME:
        _bot(request, bot_id)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=TOO_BIG)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=TOO_BIG)
    data = bytes(body)
    content_type = image_type(data)
    if content_type is None:
        raise HTTPException(status_code=415, detail=NOT_AN_IMAGE)
    if bot_id == ME:
        target = _me_file(request)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f"avatar.{time.time_ns()}.part")
        part.write_bytes(data)
        os.replace(part, target)
        request.app.state.broadcaster.publish("avatars", {"bot_id": ME})
        return _me_view(request)
    await request.app.state.avatars.put(bot_id, "custom", content_type, data)
    return await _changed(request, bot_id)


@router.delete("/bots/{bot_id}/avatar/custom")
async def delete_avatar(request: Request, bot_id: str) -> dict[str, Any]:
    if bot_id == ME:
        _me_file(request).unlink(missing_ok=True)
        request.app.state.broadcaster.publish("avatars", {"bot_id": ME})
        return empty_view()
    _bot(request, bot_id)
    await request.app.state.avatars.delete(bot_id, "custom")
    return await _changed(request, bot_id)
