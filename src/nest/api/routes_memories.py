"""长期记忆 in the console: what the bots remember of people, per group or private chat — view, add, edit,
delete, forget a whole place, 「立即整理」, and the model that does the learning. Turning it on is per bot in
its group or private chat settings (routes_bots).

Writes hold app.state.config_lock; 「立即整理」 calls a model, so it runs outside it (learning itself never
runs twice at once for one place). Anything added or edited here is the owner's: learning leaves it alone.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.routes_stickers import group_names
from nest.api.text import has_bad_chars
from nest.core.memory import CONTENT_MAX, is_private

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

MemoryId = Annotated[int, Path(ge=1, le=2**63 - 1)]
Kind = Literal["like", "fact", "promise"]
NOT_FOUND = "没有这条记忆"
NO_PLACE = "没有这个群或私聊"


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: Annotated[str, Field(max_length=80)]
    person_id: Annotated[str, Field(min_length=1, max_length=64)]
    kind: Kind
    content: str
    bot_id: Annotated[str, Field(max_length=64)] | None = None  # 约定 only: the bot it was made with
    due: str | None = None  # 约定 only: YYYY-MM-DD


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    due: str | None = None  # "" removes the day


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: Annotated[str, Field(max_length=80)]


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Annotated[str, Field(min_length=1)] | None  # null: the model of a bot that remembers there


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _content(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise _invalid("内容不能是空的")
    if len(text) > CONTENT_MAX:
        raise _invalid(f"内容最多 {CONTENT_MAX} 个字")
    if has_bad_chars(text):
        raise _invalid("内容里不能有控制字符")
    return text


def _due(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise _invalid("日期要写成 2026-01-31 这样") from None


async def _places(state: Any) -> dict[str, dict[str, Any]]:
    """Every place to show: where a bot remembers, or something is remembered. key -> name, kind, bots."""
    keeper = state.memory
    groups = await group_names(state)
    channels = {c["id"]: c for c in await state.store.channels()}
    by_account = {bot.account_id: bot for bot in state.brain.bots.values()}
    remembering = keeper.places()
    found: dict[str, dict[str, Any]] = {}
    for key in {*remembering, *(await keeper.store.places())}:
        if is_private(key):
            channel = channels.get(int(key.removeprefix("chat:")))
            if channel is None:
                continue
            bot = by_account.get(channel["account_id"])
            who = channel["name"] or channel["external_id"]
            name = f"{bot.name if bot else '?'} 和 {who} 的私聊"
            kind = "private"
        else:
            if key not in groups:
                continue
            name, kind = groups[key] or key, "group"
        found[key] = {
            "name": name,
            "kind": kind,
            "bots": sorted(bot.name for bot, _ in remembering.get(key, [])),
        }
    return found


@router.get("/memories")
async def memories(request: Request, place: Annotated[str, Query(max_length=80)] = "") -> dict[str, Any]:
    state = request.app.state
    keeper = state.memory
    places = await _places(state)
    counts = await keeper.store.places()
    progress = await keeper.store.progress()
    listed = [
        {
            "key": key,
            **info,
            "count": counts.get(key, 0),
            "learned_at": (progress.get(key) or {}).get("learned_at") or None,
            "detail": (progress.get(key) or {}).get("detail", ""),
            "error": (progress.get(key) or {}).get("error", ""),
        }
        for key, info in sorted(places.items(), key=lambda item: (item[1]["kind"], item[1]["name"]))
    ]
    chosen = place if place in places else (listed[0]["key"] if listed else "")
    names = {bot.id: bot.name for bot in state.brain.bots.values()}
    provider = keeper.provider_for(chosen) if chosen else keeper.config.provider_id
    return {
        "places": listed,
        "place": chosen,
        "bots": [{"id": bot.id, "name": bot.name} for bot in keeper.bots_in(chosen)] if chosen else [],
        "settings": {
            "provider_id": keeper.config.provider_id,
            "provider_loaded": provider is None or state.providers.get(provider) is not None,
        },
        "memories": [
            m | {"bot_name": names.get(m["bot_id"], "") if m["bot_id"] else ""}
            for m in (await keeper.store.of_place(chosen) if chosen else [])
        ],
    }


@router.patch("/memories/settings")
async def memory_settings(request: Request, body: MemorySettings) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        if body.provider_id is not None and (
            has_bad_chars(body.provider_id) or await state.repo.get_provider(body.provider_id) is None
        ):
            raise HTTPException(status_code=400, detail="没有这个模型")
        config = await state.memory.save_config(body.provider_id)
    state.broadcaster.publish("memories", {"place": None})
    return {"provider_id": config.provider_id}


@router.post("/memories", status_code=201)
async def add_memory(request: Request, body: MemoryCreate) -> dict[str, Any]:
    state = request.app.state
    keeper = state.memory
    content = _content(body.content)
    async with state.config_lock:
        if body.place not in await _places(state):
            raise HTTPException(status_code=404, detail=NO_PLACE)
        known = await keeper.store.of_people(body.place, [body.person_id])
        if not known:
            raise HTTPException(status_code=404, detail="这里还没有记过这个人")
        bot_id = due = None
        if body.kind == "promise":
            if body.bot_id not in {bot.id for bot in keeper.bots_in(body.place)}:
                raise _invalid("约定要选和哪个 bot 约的")
            bot_id, due = body.bot_id, _due(body.due)
        memory_id = await keeper.store.add(
            body.place,
            body.person_id,
            known[-1]["person"],
            body.kind,
            content,
            bot_id=bot_id,
            due=due,
            source="manual",
            at=time.time(),
        )
    keeper.changed(body.place)
    return await keeper.store.get(memory_id)  # type: ignore[no-any-return]


@router.patch("/memories/{memory_id}")
async def update_memory(request: Request, memory_id: MemoryId, body: MemoryPatch) -> dict[str, Any]:
    state = request.app.state
    keeper = state.memory
    content = _content(body.content) if body.content is not None else None
    async with state.config_lock:
        memory = await keeper.store.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        due = _due(body.due) if body.due is not None and memory["kind"] == "promise" else None
        await keeper.store.update(
            memory_id,
            content=content,
            due=due,
            clear_due=body.due is not None and due is None,
            source="manual",
            at=time.time(),
        )
    keeper.changed(memory["place"])
    return await keeper.store.get(memory_id)  # type: ignore[no-any-return]


@router.delete("/memories/{memory_id}")
async def delete_memory(request: Request, memory_id: MemoryId) -> dict[str, Any]:
    state = request.app.state
    keeper = state.memory
    async with state.config_lock:
        memory = await keeper.store.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        await keeper.store.delete([memory_id])
    keeper.changed(memory["place"])
    return {"ok": True}


@router.post("/memories/forget")
async def forget_place(request: Request, body: Place) -> dict[str, Any]:
    """Everything remembered in a place, gone; it is read again from now on, not from the start."""
    state = request.app.state
    keeper = state.memory
    async with state.config_lock:
        if body.place not in await _places(state):
            raise HTTPException(status_code=404, detail=NO_PLACE)
        removed = await keeper.store.forget_place(body.place)
        now = time.time()
        await keeper.store.record(body.place, learned_at=now, upto=now, detail=f"清空了 {removed} 条记忆")
    keeper.changed(body.place)
    return {"removed": removed}


@router.post("/memories/learn")
async def learn_now(request: Request, body: Place) -> dict[str, Any]:
    state = request.app.state
    if body.place not in state.memory.places():
        raise HTTPException(status_code=400, detail="这里没有 bot 开着长期记忆")
    return await state.memory.learn(body.place, force=True)  # type: ignore[no-any-return]
