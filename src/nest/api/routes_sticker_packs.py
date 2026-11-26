"""表情包管理: each companion's own sticker packs, drawn by nest or uploaded, and the picker the
desk chat opens to send one."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.core.plugins import PluginError
from nest.core.sticker_packs import EXTRA_MAX, MOOD_MAX, MOODS, NAME_MAX, STYLES, PackError
from nest.core.stickers import StickerError

router = APIRouter(prefix="/api/sticker-packs", dependencies=[Depends(require_token)])

RECENT = 24  # 「常用」: the stickers you sent in the desk chat lately, at most this many
RECENT_LOOKBACK = 300


class Naming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=NAME_MAX * 2)] = ""


class PackPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=NAME_MAX * 2)] | None = None
    style: str | None = None
    extra: Annotated[str, Field(max_length=EXTRA_MAX * 2)] | None = None
    active: bool | None = None


class Drawing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    which: Literal["missing", "all"] | None = None
    slots: list[int] | None = None


class Mood(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood: Annotated[str, Field(max_length=MOOD_MAX * 2)]


class SlotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood: Annotated[str, Field(max_length=MOOD_MAX * 2)] | None = None
    locked: bool | None = None


class FromPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: str
    path: str


class DeskSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool


def _bot(request: Request, bot_id: str) -> Any:
    bot = request.app.state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个伙伴")
    return bot


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """The companions with how much of the pack in use is drawn, the styles and the usual moods."""
    state = request.app.state
    packs = state.sticker_packs
    summary = await packs.summary()
    bots = []
    for bot in state.brain.bots.values():
        done = summary.get(bot.id, {"pack_id": None, "done": 0, "total": 0})
        bots.append(
            {"bot_id": bot.id, "name": bot.name, **done, "desk_stickers": await packs.desk_on(bot.id)}
        )
    return {
        "bots": bots,
        "styles": [{"id": key, "name": name} for key, (name, _) in STYLES.items()],
        "moods": [mood for mood, _ in MOODS],
        "can_draw": state.providers.drawing() is not None,
    }


@router.get("/bots/{bot_id}")
async def packs_of(request: Request, bot_id: str) -> dict[str, Any]:
    _bot(request, bot_id)
    packs = request.app.state.sticker_packs
    store = request.app.state.stickers.store
    own = [s for i in await packs.own(bot_id) if (s := await store.get(i)) is not None]
    return {
        "packs": await packs.packs(bot_id),
        "desk_stickers": await packs.desk_on(bot_id),
        "own_only": await packs.own_only(bot_id),
        "group_own_only": await packs.group_own_only(bot_id),
        "own": own,
        "has_avatar": await packs.avatar_of(bot_id) is not None,
    }


@router.post("/bots/{bot_id}", status_code=201)
async def create_pack(request: Request, bot_id: str, body: Naming) -> dict[str, Any]:
    _bot(request, bot_id)
    try:
        return await request.app.state.sticker_packs.create(bot_id, body.name)  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.get("/missing")
async def missing(request: Request) -> dict[str, Any]:
    """What 「全部生成」 would draw: how many pictures, of which companions."""
    state = request.app.state
    found = await state.sticker_packs.missing()
    names = state.brain.bots
    return {
        "bots": [{"bot_id": b, "name": names[b].name, "count": n} for _, b, n in found],
        "count": sum(n for _, _, n in found),
    }


@router.post("/draw-everyone")
async def draw_everyone(request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.sticker_packs.draw_everyone()  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.get("/from-plugins")
async def from_plugins(request: Request) -> list[dict[str, Any]]:
    """Sticker packs of installed plugins, to take for a companion."""
    return await asyncio.to_thread(request.app.state.plugins.sticker_packs)


@router.post("/bots/{bot_id}/from-plugin", status_code=201)
async def take_from_plugin(request: Request, bot_id: str, body: FromPlugin) -> dict[str, Any]:
    _bot(request, bot_id)
    state = request.app.state

    def read() -> tuple[str, list[tuple[str, bytes]]]:
        pack = state.plugins.sticker_pack(body.plugin, body.path)
        return pack["name"], [(mood, path.read_bytes()) for mood, path in pack["items"]]

    try:
        name, items = await asyncio.to_thread(read)
        return await state.sticker_packs.take_in(bot_id, name, items)  # type: ignore[no-any-return]
    except (PackError, PluginError) as exc:
        raise _refused(exc) from None


@router.get("/{pack_id}/plugin")
async def as_plugin(request: Request, pack_id: int, author: str = "") -> Response:
    """The pack as a plugin zip, to share."""
    try:
        name, data = await request.app.state.sticker_packs.as_plugin(pack_id, author.strip()[:40])
    except PackError as exc:
        raise _refused(exc) from None
    return Response(
        data,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{name}"', "cache-control": "no-store"},
    )


@router.put("/bots/{bot_id}/desk")
async def desk_switch(request: Request, bot_id: str, body: DeskSwitch) -> dict[str, Any]:
    """「聊天里发表情包」: whether she sends stickers in the desk chat."""
    _bot(request, bot_id)
    await request.app.state.sticker_packs.set_desk(bot_id, body.on)
    return {"desk_stickers": body.on}


@router.put("/bots/{bot_id}/own-only")
async def own_only_switch(request: Request, bot_id: str, body: DeskSwitch) -> dict[str, Any]:
    """「只用专属表情包里的表情」: in the desk chat she picks from her own stickers only, or from the whole
    collection with hers first."""
    _bot(request, bot_id)
    await request.app.state.sticker_packs.set_own_only(bot_id, body.on)
    return {"own_only": body.on}


@router.put("/bots/{bot_id}/group-own-only")
async def group_own_only_switch(request: Request, bot_id: str, body: DeskSwitch) -> dict[str, Any]:
    """「群里只发专属表情包」: in the groups of a platform she sends her own stickers only, or (off, the
    default) the collection's, without anyone's pack."""
    _bot(request, bot_id)
    await request.app.state.sticker_packs.set_group_own_only(bot_id, body.on)
    return {"group_own_only": body.on}


@router.get("/bots/{bot_id}/picker")
async def picker(request: Request, bot_id: str) -> dict[str, Any]:
    """What the desk chat's sticker button shows: her pack in use, and what you sent lately."""
    _bot(request, bot_id)
    state = request.app.state
    store = state.stickers.store
    hers = [s for i in await state.sticker_packs.own(bot_id) if (s := await store.get(i)) is not None]
    rows = await state.db.fetchall(
        """SELECT segments FROM messages WHERE from_self = 0 AND external_id LIKE 'app:%'
           AND segments LIKE '%sticker%' ORDER BY id DESC LIMIT ?""",
        (RECENT_LOOKBACK,),
    )
    seen: list[int] = []
    for row in rows:
        for segment in json.loads(row["segments"] or "[]"):
            sticker_id = (segment.get("data") or {}).get("id") if segment.get("type") == "sticker" else None
            if isinstance(sticker_id, int) and sticker_id not in seen:
                seen.append(sticker_id)
    recent = [s for i in seen[:RECENT] if (s := await store.get(i)) is not None]
    return {"hers": hers, "recent": recent, "starred": await store.starred()}


@router.patch("/{pack_id}")
async def update_pack(request: Request, pack_id: int, body: PackPatch) -> dict[str, Any]:
    try:
        return await request.app.state.sticker_packs.update(pack_id, **body.model_dump(exclude_unset=True))  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.delete("/{pack_id}")
async def delete_pack(request: Request, pack_id: int) -> dict[str, Any]:
    try:
        await request.app.state.sticker_packs.delete(pack_id)
    except PackError as exc:
        raise _refused(exc) from None
    return {"ok": True}


@router.post("/{pack_id}/draw")
async def draw(request: Request, pack_id: int, body: Drawing) -> dict[str, Any]:
    """Queue pictures to draw (about a minute and a half each, one at a time): "missing", "all", or
    `slots`."""
    which: str | list[int] = body.slots if body.slots else (body.which or "missing")
    try:
        return await request.app.state.sticker_packs.draw(pack_id, which)  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.post("/{pack_id}/slots", status_code=201)
async def add_slot(request: Request, pack_id: int, body: Mood) -> dict[str, Any]:
    try:
        return await request.app.state.sticker_packs.add_slot(pack_id, body.mood)  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.patch("/slots/{slot_id}")
async def update_slot(request: Request, slot_id: int, body: SlotPatch) -> dict[str, Any]:
    packs = request.app.state.sticker_packs
    try:
        if body.locked is not None:
            await packs.lock_slot(slot_id, body.locked)
        if body.mood is not None:
            return await packs.rename_slot(slot_id, body.mood)  # type: ignore[no-any-return]
        slot = await packs._slot(slot_id)
        return await packs.pack(slot[1]["id"])  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.delete("/slots/{slot_id}")
async def delete_slot(request: Request, slot_id: int) -> dict[str, Any]:
    try:
        return await request.app.state.sticker_packs.delete_slot(slot_id)  # type: ignore[no-any-return]
    except PackError as exc:
        raise _refused(exc) from None


@router.put("/slots/{slot_id}/picture")
async def upload_picture(request: Request, slot_id: int) -> dict[str, Any]:
    """「上传」: your own picture for this mood, as the raw request body."""
    keeper = request.app.state.stickers
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > keeper.config.upload_max_bytes:
            break  # add_own refuses it, without reading the rest
    try:
        return await request.app.state.sticker_packs.upload(slot_id, bytes(body))  # type: ignore[no-any-return]
    except (PackError, StickerError) as exc:
        raise HTTPException(
            status_code=422 if isinstance(exc, StickerError) else 409, detail=str(exc)
        ) from None
