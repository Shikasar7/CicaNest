"""小窝和她的日子: the house, the companions' days, their life profiles."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.core.initiative import PROVIDERS

router = APIRouter(prefix="/api/life", dependencies=[Depends(require_token)])


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: dict[str, Any]


def _bot(request: Request, bot_id: str) -> Any:
    bot = request.app.state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个伙伴")
    return bot


@router.get("")
async def view(request: Request) -> dict[str, Any]:
    return await request.app.state.life.view()  # type: ignore[no-any-return]


@router.put("")
async def switch(request: Request, body: Switch) -> dict[str, Any]:
    """「她们的日子」: on, the house plans a day each morning (one model call) and her chats know it."""
    life = request.app.state.life
    await life.set_on(body.on)
    if body.on:
        await life.today()
    return await life.view()  # type: ignore[no-any-return]


class Settings(BaseModel):
    """How often they come to the user on their own (nest.core.initiative). Only what is sent changes."""

    model_config = ConfigDict(extra="forbid")

    quiet_from: Annotated[int, Field(ge=0, le=23)] | None = None
    quiet_until: Annotated[int, Field(ge=0, le=23)] | None = None
    chatter_per_day: Annotated[int, Field(ge=0, le=50)] | None = None
    chatter_per_hour: Annotated[int, Field(ge=0, le=20)] | None = None
    outreach_per_bot: Annotated[int, Field(ge=0, le=20)] | None = None
    outreach_daily: Annotated[int, Field(ge=0, le=50)] | None = None
    outreach_hourly: Annotated[int, Field(ge=0, le=10)] | None = None
    chatter_provider: str | None = None  # null: each one's own model (told apart with model_fields_set)
    life_provider: str | None = None
    rhythm: bool | None = None


class Piece(BaseModel):
    """A piece of furniture in a room of the 3D 小窝 (web/src/lib/dorm.ts)."""

    model_config = ConfigDict(extra="forbid")

    kind: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")]
    x: Annotated[int, Field(ge=0, le=40)]
    z: Annotated[int, Field(ge=0, le=40)]
    turn: Annotated[int, Field(ge=0, le=3)]


class Hung(BaseModel):
    """Something on a wall (web/src/lib/dorm-look.ts)."""

    model_config = ConfigDict(extra="forbid")

    kind: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")]
    wall: Literal["left", "right"]
    at: Annotated[int, Field(ge=0, le=80)]
    y: Annotated[int, Field(ge=0, le=20)]


class Look(BaseModel):
    """Its paper, its floor and what hangs there."""

    model_config = ConfigDict(extra="forbid")

    wall: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")]
    floor: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")]
    hung: Annotated[list[Hung], Field(max_length=30)]


class Furnishing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pieces: Annotated[list[Piece], Field(max_length=60)] | None  # null: the room as it was
    look: Look | None = None  # null: its walls and floor as they were


@router.get("/rooms")
async def rooms(request: Request) -> dict[str, Any]:
    """How the user has furnished the rooms; a room not in it is as it comes."""
    return {"rooms": await request.app.state.life.furnished()}


@router.put("/rooms/{room}")
async def furnish(request: Request, room: str, body: Furnishing) -> dict[str, Any]:
    life = request.app.state.life
    if not life.is_room(room):
        raise HTTPException(status_code=404, detail="没有这个房间")
    await life.furnish(room, None if body.pieces is None else body.model_dump())
    return {"rooms": await life.furnished()}


@router.get("/settings")
async def settings(request: Request) -> dict[str, Any]:
    return request.app.state.initiative.view(time.time())  # type: ignore[no-any-return]


@router.put("/settings")
async def set_settings(request: Request, body: Settings) -> dict[str, Any]:
    state = request.app.state
    changes = {
        name: value
        for name, value in body.model_dump(exclude_unset=True).items()
        if value is not None or name in PROVIDERS
    }
    for name in PROVIDERS:
        if changes.get(name) and await state.repo.get_provider(changes[name]) is None:
            raise HTTPException(status_code=422, detail="没有这个模型")
    async with state.config_lock:
        await state.initiative.save(changes)
    return state.initiative.view(time.time())  # type: ignore[no-any-return]


@router.delete("/settings/rhythm")
async def forget_rhythm(request: Request) -> dict[str, Any]:
    """Forget what was noticed of the user's hours."""
    state = request.app.state
    await state.initiative.rhythm.forget()
    return state.initiative.view(time.time())  # type: ignore[no-any-return]


@router.post("/plan")
async def plan_again(request: Request) -> dict[str, Any]:
    """「重新排今天」."""
    life = request.app.state.life
    if not await life.on():
        raise HTTPException(status_code=409, detail="先打开「她们的日子」")
    await life.today(again=True)
    return await life.view()  # type: ignore[no-any-return]


@router.put("/profiles/{bot_id}")
async def set_profile(request: Request, bot_id: str, body: Profile) -> dict[str, Any]:
    _bot(request, bot_id)
    return await request.app.state.life.set_profile(bot_id, body.profile)  # type: ignore[no-any-return]


@router.post("/profiles/{bot_id}/draft")
async def draft(request: Request, bot_id: str) -> dict[str, Any]:
    """「让她自己写一份」: her model writes her profile from her persona."""
    _bot(request, bot_id)
    return await request.app.state.life.draft(bot_id)  # type: ignore[no-any-return]


@router.delete("/moments/{moment_id}")
async def forget(request: Request, moment_id: int) -> dict[str, Any]:
    await request.app.state.life.forget(moment_id)
    return {"ok": True}
