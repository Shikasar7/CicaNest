"""黑话 (each group's terms: view, add, edit or confirm, delete, learn now, the learning model) and the
per-group switches 「收藏表情包」 and 「学习黑话」.

Writes hold app.state.config_lock; 「立即学习」 calls a model, so it runs outside it (learning itself never
runs twice at once for one group).
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import can
from nest.api.auth import require_token
from nest.api.routes_stickers import group_names
from nest.api.text import has_bad_chars
from nest.core.slang import MEANING_MAX, TERM_MAX

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

EntryId = Annotated[int, Path(ge=1, le=2**63 - 1)]
EXAMPLE_MAX = 120
NOT_FOUND = "没有这个词条"
NO_GROUP = "没有这个群"


class Features(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collect_stickers: bool | None = None
    learn_slang: bool | None = None
    welcome: bool | None = None


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str
    term: str
    meaning: str
    example: str = ""


class EntryPatch(BaseModel):
    """Every field optional; an empty patch is 「确认」: the meaning stays, and learning no longer changes
    it."""

    model_config = ConfigDict(extra="forbid")

    term: str | None = None
    meaning: str | None = None
    example: str | None = None


class Learn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str


class SlangSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Annotated[str, Field(min_length=1)] | None  # null: the first bot's model


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _text(value: str, what: str, limit: int, *, required: bool = True) -> str:
    value = " ".join(value.split())
    if required and not value:
        raise _invalid(f"{what}不能是空的")
    if len(value) > limit:
        raise _invalid(f"{what}最多 {limit} 个字")
    if has_bad_chars(value):
        raise _invalid(f"{what}里不能有控制字符")
    return value


async def _group(state: Any, key: str) -> str:
    """The key of a group our accounts are in (404 otherwise)."""
    if key not in await group_names(state):
        raise HTTPException(status_code=404, detail=NO_GROUP)
    return key


@router.patch("/groups/{key}/features")
async def group_features(request: Request, key: str, body: Features) -> dict[str, Any]:
    state = request.app.state
    if body.collect_stickers is None and body.learn_slang is None and body.welcome is None:
        raise _invalid("没有要改的内容")
    if body.collect_stickers and not can(key, "sticker_source"):
        raise HTTPException(status_code=400, detail="这个平台的表情不收藏")
    if body.learn_slang and not can(key, "sees_all"):
        raise HTTPException(status_code=400, detail="这个平台的 bot 只看得到找它的话，学不了黑话")
    if body.welcome and not can(key, "member_joins"):
        raise HTTPException(status_code=400, detail="这个平台不告诉 nest 有人进群，没法欢迎新人")
    async with state.config_lock:
        await _group(state, key)
        await state.stickers.groups.set(
            key, collect_stickers=body.collect_stickers, learn_slang=body.learn_slang, welcome=body.welcome
        )
        await state.stickers.reload()
    features = await state.stickers.groups.get(key)
    state.broadcaster.publish("groups", {"key": key})
    return {
        "key": key,
        "collect_stickers": features.collect_stickers,
        "learn_slang": features.learn_slang,
        "welcome": features.welcome,
    }


@router.get("/slang")
async def slang(request: Request, group: Annotated[str, Query(max_length=80)] = "") -> dict[str, Any]:
    state = request.app.state
    learner = state.slang
    names = await group_names(state)
    features = await learner.groups.all()
    counts = await learner.slang.counts()
    groups = [
        {
            "key": key,
            "name": name,
            "learn": bool(features.get(key) and features[key].learn_slang),
            "learned_at": features[key].slang_learned_at if key in features else None,
            "terms": counts.get(key, 0),
        }
        for key, name in sorted(names.items(), key=lambda item: (item[1] or "~", item[0]))
    ]
    chosen = group if group in names else (groups[0]["key"] if groups else "")
    provider = learner.provider_id()
    return {
        "groups": groups,
        "group": chosen,
        "settings": {
            "provider_id": learner.config.provider_id,  # null: the first bot's model (default_provider)
            "default_provider": state.brain.default_provider(),
            "provider_loaded": state.providers.get(provider) is not None,
        },
        "entries": await learner.slang.entries(chosen) if chosen else [],
        "runs": await learner.slang.runs(chosen) if chosen else [],
    }


@router.patch("/slang/settings")
async def slang_settings(request: Request, body: SlangSettings) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        if body.provider_id is not None and (
            has_bad_chars(body.provider_id) or await state.repo.get_provider(body.provider_id) is None
        ):
            raise HTTPException(status_code=400, detail="没有这个模型")
        config = await state.slang.save_config(body.provider_id)
    state.broadcaster.publish("slang", {"group_key": None})
    return {"provider_id": config.provider_id}


@router.post("/slang", status_code=201)
async def add_entry(request: Request, body: EntryCreate) -> dict[str, Any]:
    state = request.app.state
    term = _text(body.term, "词", TERM_MAX)
    meaning = _text(body.meaning, "意思", MEANING_MAX)
    example = _text(body.example, "例句", EXAMPLE_MAX, required=False)
    async with state.config_lock:
        key = await _group(state, body.group_key)
        try:
            entry_id = await state.slang.slang.add(key, term, meaning, example=example, source="manual")
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"这个群已经有「{term}」了") from None
    state.slang.changed(key)
    return await state.slang.slang.get(entry_id)  # type: ignore[no-any-return]


@router.patch("/slang/{entry_id}")
async def update_entry(request: Request, entry_id: EntryId, body: EntryPatch) -> dict[str, Any]:
    state = request.app.state
    term = _text(body.term, "词", TERM_MAX) if body.term is not None else None
    meaning = _text(body.meaning, "意思", MEANING_MAX) if body.meaning is not None else None
    example = _text(body.example, "例句", EXAMPLE_MAX, required=False) if body.example is not None else None
    async with state.config_lock:
        entry = await state.slang.slang.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        try:
            await state.slang.slang.update(entry_id, term=term, meaning=meaning, example=example)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"这个群已经有「{term}」了") from None
    state.slang.changed(entry["group_key"])
    return await state.slang.slang.get(entry_id)  # type: ignore[no-any-return]


@router.delete("/slang/{entry_id}")
async def delete_entry(request: Request, entry_id: EntryId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        entry = await state.slang.slang.delete(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    state.slang.changed(entry["group_key"])
    return {"ok": True}


@router.post("/slang/learn")
async def learn_now(request: Request, body: Learn) -> dict[str, Any]:
    """「立即学习」: one run now, whether or not the group's switch is on (it needs a few messages)."""
    state = request.app.state
    key = await _group(state, body.group_key)
    return await state.slang.learn(key, force=True)  # type: ignore[no-any-return]
