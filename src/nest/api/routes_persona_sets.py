"""人设方案: save which persona a bot uses in each of its places, and switch the whole set back in one go.

The sets themselves are listed with each bot in GET /api/bots (persona_sets). Every change runs under
app.state.config_lock; switching ends with brain.load(), so the new personas take effect at once.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.bots import NameTaken

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

NAME_LIMIT = 20
SETS_LIMIT = 20  # per bot
SetId = Annotated[int, Path(ge=1, le=2**63 - 1)]


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
    now (every such place has the persona the set names)."""
    views = []
    for persona_set in sets:
        items = [
            {"channel_id": channel_id, "persona_id": persona_id}
            for channel_id, persona_id in persona_set["items"].items()
            if channel_id in bot.channels
        ]
        current = bool(items) and all(
            bot.channels[item["channel_id"]].persona_id == item["persona_id"] for item in items
        )
        views.append(
            {"id": persona_set["id"], "name": persona_set["name"], "items": items, "current": current}
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
    """Switch to the set. changed counts the places whose persona changed."""
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        changed = await state.repo.apply_persona_set(bot_id, set_id)
        if changed is None:
            raise _missing()
        await state.brain.load()
        _changed(state, bot_id)
    return {"changed": changed}


@router.delete("/bots/{bot_id}/persona-sets/{set_id}")
async def delete_set(request: Request, bot_id: str, set_id: SetId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _bot(state, bot_id)
        if not await state.repo.delete_persona_set(bot_id, set_id):
            raise _missing()
        _changed(state, bot_id)
    return {"ok": True}
