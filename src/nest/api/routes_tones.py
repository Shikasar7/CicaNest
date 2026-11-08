"""语气 (the console's 「语气」 page): the tones bots speak in. Built-in ones can be rewritten and reset, new
ones added and, when no group uses them, deleted. A group picks one for 答题 and one for chat on the Bot
page (PATCH /api/bots/{bot}/channels/{channel}). Every change runs under app.state.config_lock and ends with
brain.load(), which re-reads the tones."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.bots import InUse, NameTaken
from nest.storage.tones import ToneRepo

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

LABEL_LIMIT = 20
MANNER_LIMIT = 800
ToneKey = Annotated[str, Path(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")]


class ToneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    manner: str


class TonePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    manner: str | None = None


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _label(value: str) -> str:
    value = " ".join(value.split())
    if not 1 <= len(value) <= LABEL_LIMIT or has_bad_chars(value):
        raise _invalid(f"语气名字要填 1–{LABEL_LIMIT} 个字")
    return value


def _manner(value: str) -> str:
    value = value.strip()
    if not value:
        raise _invalid("要写怎么说话，不能是空的")
    if len(value) > MANNER_LIMIT:
        raise _invalid(f"怎么说话最多 {MANNER_LIMIT} 个字")
    if has_bad_chars(value, allow="\n"):
        raise _invalid("里面有不能保存的控制字符")
    return value


def _repo(request: Request) -> ToneRepo:
    return ToneRepo(request.app.state.db)


async def _changed(state: Any) -> None:
    await state.brain.load()
    state.broadcaster.publish("bots", {"bot_id": None})


async def _view(request: Request, key: str) -> dict[str, Any]:
    view = next((v for v in await _repo(request).views() if v["key"] == key), None)
    if view is None:
        raise HTTPException(status_code=404, detail="没有这个语气")
    return view


@router.get("/tones")
async def tones(request: Request) -> list[dict[str, Any]]:
    """Built-in ones first; used_by lists the bots and groups using each, for 答题 or chat."""
    return await _repo(request).views()


@router.post("/tones", status_code=201)
async def create_tone(request: Request, body: ToneCreate) -> dict[str, Any]:
    state = request.app.state
    label, manner = _label(body.label), _manner(body.manner)
    async with state.config_lock:
        try:
            key = await _repo(request).create(label, manner)
        except NameTaken:
            raise HTTPException(status_code=409, detail=f"已经有叫「{label}」的语气") from None
        await _changed(state)
    return await _view(request, key)


@router.patch("/tones/{key}")
async def update_tone(request: Request, key: ToneKey, body: TonePatch) -> dict[str, Any]:
    state = request.app.state
    if body.label is None and body.manner is None:
        raise _invalid("没有要改的内容")
    label = _label(body.label) if body.label is not None else None
    manner = _manner(body.manner) if body.manner is not None else None
    async with state.config_lock:
        try:
            changed = await _repo(request).update(key, label=label, manner=manner)
        except NameTaken:
            raise HTTPException(status_code=409, detail=f"已经有叫「{label}」的语气") from None
        if not changed:
            raise HTTPException(status_code=404, detail="没有这个语气")
        await _changed(state)
    return await _view(request, key)


@router.post("/tones/{key}/reset")
async def reset_tone(request: Request, key: ToneKey) -> dict[str, Any]:
    """「恢复默认」: a built-in tone back to how it shipped."""
    state = request.app.state
    async with state.config_lock:
        if not await _repo(request).reset(key):
            raise HTTPException(status_code=404, detail="只有内置的语气能恢复默认")
        await _changed(state)
    return await _view(request, key)


@router.delete("/tones/{key}")
async def delete_tone(request: Request, key: ToneKey) -> dict[str, bool]:
    state = request.app.state
    async with state.config_lock:
        try:
            deleted = await _repo(request).delete(key)
        except ValueError:
            raise HTTPException(status_code=409, detail="内置的语气不能删，可以改写或恢复默认") from None
        except InUse as exc:
            raise HTTPException(status_code=409, detail=f"还用在：{'、'.join(exc.users)}") from None
        if not deleted:
            raise HTTPException(status_code=404, detail="没有这个语气")
        await _changed(state)
    return {"ok": True}
