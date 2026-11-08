"""表情包: the collection (browse, edit, keep, mark unsuitable, describe again, delete), its settings and
「测试看图」. Which groups collect is a group switch (routes_slang.group_features).

Writes that change settings hold app.state.config_lock; 「测试看图」 calls a model, so it runs outside it.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.routes_bots import group_key
from nest.api.text import has_bad_chars
from nest.core.stickers import StickerError
from nest.llm.base import ProviderError
from nest.llm.runtime import ProviderUnavailable

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

StickerId = Annotated[int, Path(ge=1, le=2**63 - 1)]
DESCRIPTION_LIMIT = 60
TAG_LIMIT = 8
IDEA_LIMIT = 200
CACHE = "private, max-age=31536000, immutable"  # the console asks for …/image?v=<sha256>
NOT_FOUND = "没有这张表情"


class StickerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    caption: str | None = None
    tags: list[str] | None = None
    kept: bool | None = None
    status: Literal["ready", "unsuitable"] | None = None  # 「恢复」 / 「不适合」


class StickerIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: Annotated[
        list[Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]], Field(min_length=1, max_length=500)
    ]


class StickerOwner(StickerIds):
    bot_id: str | None  # the only bot that sends them; null: every bot may


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision_provider: str | None = None  # null removes the vision model (told apart with model_fields_set)
    max_count: Annotated[int, Field(strict=True, ge=10, le=5000)] | None = None
    max_mb: Annotated[int, Field(strict=True, ge=5, le=2000)] | None = None
    describe_per_hour: Annotated[int, Field(strict=True, ge=1, le=600)] | None = None
    notes_per_hour: Annotated[int, Field(strict=True, ge=0, le=600)] | None = None
    hourly_per_bot: Annotated[int, Field(strict=True, ge=1, le=60)] | None = None
    group_gap: Annotated[int, Field(strict=True, ge=10, le=3600)] | None = None


class DrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea: Annotated[str, Field(min_length=1, max_length=IDEA_LIMIT)]
    bot_id: str | None = None  # drawn as this bot's character; only this bot sends it


class VisionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _words(value: str, what: str) -> str:
    value = " ".join(value.split())
    if len(value) > DESCRIPTION_LIMIT:
        raise _invalid(f"{what}最多 {DESCRIPTION_LIMIT} 个字")
    if has_bad_chars(value):
        raise _invalid(f"{what}里不能有控制字符")
    return value


def _idea(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise _invalid("先写想画什么")
    if has_bad_chars(value):
        raise _invalid("想画的内容里不能有控制字符")
    return value


def _tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    for value in values:
        value = value.strip()
        if not value or value in tags:
            continue
        if len(value) > TAG_LIMIT or has_bad_chars(value):
            raise _invalid(f"每个标签最多 {TAG_LIMIT} 个字，不能有控制字符")
        tags.append(value)
    if len(tags) > 3:
        raise _invalid("标签最多 3 个")
    return tags


async def group_names(state: Any) -> dict[str, str]:
    """Group key -> the group's name (the first non-empty one any of our accounts knows)."""
    names: dict[str, str] = {}
    for channel in await state.store.channels():
        if channel["kind"] == "group":
            key = group_key(channel)
            names[key] = names.get(key) or channel["name"] or ""
    return names


async def settings_view(state: Any) -> dict[str, Any]:
    keeper = state.stickers
    vision = keeper.config.vision_provider
    count, size = await keeper.store.totals()
    names = await group_names(state)
    return {
        "settings": keeper.config.view(),
        # Set while "vision_loaded" is false: that model is gone or failed to load.
        "vision_loaded": vision is not None and state.providers.get(vision) is not None,
        "can_draw": state.providers.drawing() is not None,
        "count": count,
        "bytes": size,
        "counts": await keeper.store.counts(),
        "groups": [
            {"key": key, "name": name, "collect": keeper.collecting(key)}
            for key, name in sorted(names.items(), key=lambda item: (item[1] or "~", item[0]))
        ],
    }


@router.get("/stickers")
async def stickers(
    request: Request,
    status: Literal["pending", "ready", "unsuitable", "failed"] | None = None,
    q: Annotated[str, Query(max_length=40)] = "",
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 60,
) -> dict[str, Any]:
    state = request.app.state
    items, total = await state.stickers.store.page(status=status, query=q.strip(), offset=offset, limit=limit)
    return {"items": items, "total": total, **await settings_view(state)}


@router.get("/stickers/{sticker_id}/image")
async def sticker_image(request: Request, sticker_id: StickerId) -> Response:
    keeper = request.app.state.stickers
    sticker = await keeper.store.get(sticker_id)
    if sticker is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        data = await keeper.read(sticker)
    except OSError:
        raise HTTPException(status_code=404, detail="这张表情的图片文件不见了") from None
    return Response(data, media_type=sticker["content_type"], headers={"cache-control": CACHE})


@router.patch("/stickers/settings")
async def update_settings(request: Request, patch: SettingsPatch) -> dict[str, Any]:
    state = request.app.state
    values = patch.model_dump(exclude_unset=True)
    if not values:
        raise _invalid("没有要改的内容")
    async with state.config_lock:
        vision = values.get("vision_provider")
        if vision is not None and (has_bad_chars(vision) or await state.repo.get_provider(vision) is None):
            raise HTTPException(status_code=400, detail="没有这个模型")
        await state.stickers.save_config(values)
    if "max_count" in values or "max_mb" in values:
        await state.stickers.tidy()  # lower limits apply now, not at the next daily cleanup
    state.broadcaster.publish("stickers", {"change": "settings"})
    return await settings_view(state)


@router.put("/stickers/upload")
async def upload_sticker(request: Request) -> dict[str, Any]:
    """「添加表情」: the picture is the raw request body; its type is read from its first bytes."""
    keeper = request.app.state.stickers
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > keeper.config.upload_max_bytes:
            break  # add_own refuses it, without reading the rest
    try:
        sticker, added = await keeper.add_own(bytes(body), "upload")
    except StickerError as exc:
        raise _invalid(str(exc)) from None
    return {"sticker": sticker, "added": added}


@router.post("/stickers/draw")
async def draw_sticker(request: Request, body: DrawRequest) -> dict[str, Any]:
    """「画一张」: takes about a minute. The picture joins the collection like an uploaded one."""
    state = request.app.state
    idea = _idea(body.idea)
    name, avatar = "", None
    if body.bot_id is not None:
        bot = state.brain.bots.get(body.bot_id)
        if bot is None:
            raise HTTPException(status_code=404, detail="没有这个 bot")
        name = bot.name
        chosen = (await state.avatars.views()).get(body.bot_id, {}).get("chosen")
        picture = await state.avatars.get(body.bot_id, chosen) if chosen else None
        avatar = picture.data if picture else None
    try:
        return await state.stickers.draw(idea, bot_name=name, bot_id=body.bot_id, avatar=avatar)  # type: ignore[no-any-return]
    except StickerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (ProviderError, ProviderUnavailable) as exc:
        raise HTTPException(status_code=502, detail=f"没画成：{str(exc)[:200]}") from None


@router.post("/stickers/vision-test")
async def vision_test(request: Request, body: VisionTest) -> dict[str, Any]:
    """Show a model a red circle: a model that names it can see pictures."""
    state = request.app.state
    runtime = state.providers.get(body.provider_id)
    if runtime is None:
        raise HTTPException(status_code=400, detail="没有这个模型，或者它没有加载")
    try:
        ok, answer = await state.stickers.test_vision(runtime)
    except (ProviderError, ProviderUnavailable) as exc:
        return {"ok": False, "answer": "", "error": str(exc)[:200]}
    return {"ok": ok, "answer": answer, "error": None}


@router.patch("/stickers/{sticker_id}")
async def update_sticker(request: Request, sticker_id: StickerId, patch: StickerPatch) -> dict[str, Any]:
    state = request.app.state
    store = state.stickers.store
    if not patch.model_fields_set:
        raise _invalid("没有要改的内容")
    updated = await store.update(
        sticker_id,
        description=_words(patch.description, "描述") if patch.description is not None else None,
        caption=_words(patch.caption, "图上的字") if patch.caption is not None else None,
        tags=_tags(patch.tags) if patch.tags is not None else None,
        kept=patch.kept,
        status=patch.status,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    state.broadcaster.publish("stickers", {"id": sticker_id, "change": "edited"})
    return await store.get(sticker_id)  # type: ignore[no-any-return]


@router.post("/stickers/{sticker_id}/describe")
async def describe_again(request: Request, sticker_id: StickerId) -> dict[str, Any]:
    """Back to the describer's queue; with no vision model chosen it waits there."""
    state = request.app.state
    if not await state.stickers.store.redescribe(sticker_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    await state.stickers.reload()  # wakes the describer
    state.broadcaster.publish("stickers", {"id": sticker_id, "change": "edited"})
    return await state.stickers.store.get(sticker_id)  # type: ignore[no-any-return]


@router.post("/stickers/owner")
async def set_owner(request: Request, body: StickerOwner) -> dict[str, Any]:
    state = request.app.state
    if body.bot_id is not None and body.bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    updated = await state.stickers.store.set_owner(sorted(set(body.ids)), body.bot_id)
    state.broadcaster.publish("stickers", {"change": "edited"})
    return {"updated": updated}


@router.post("/stickers/delete")
async def delete_stickers(request: Request, body: StickerIds) -> dict[str, Any]:
    removed = await request.app.state.stickers.remove(sorted(set(body.ids)))
    return {"removed": len(removed)}


@router.delete("/stickers/{sticker_id}")
async def delete_sticker(request: Request, sticker_id: StickerId) -> dict[str, Any]:
    if not await request.app.state.stickers.remove([sticker_id]):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"removed": 1}
