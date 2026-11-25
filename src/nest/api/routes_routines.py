"""定时任务 in the console (伙伴模式): a companion's routines — list, add, change, switch off, delete,
run one now — and what came of each time it was due.

Times come and go as local "YYYY-MM-DDTHH:MM" (an <input type="datetime-local">) besides unix seconds.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.core.desk import chat_of, chosen_chats
from nest.core.routines import GOAL_MAX, TITLE_MAX

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

RoutineId = Annotated[int, Path(ge=1, le=2**63 - 1)]
Repeat = Annotated[str, Field(pattern="^(daily|weekdays|weekly|monthly|every_(1|2|3|4|6|12)h)$")]
Title = Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
Goal = Annotated[str, Field(max_length=GOAL_MAX)]
When = Annotated[str, Field(max_length=20)]
NO_BOT = "没有这个 bot"
NO_ROUTINE = "没有这个定时任务"


class RoutineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    title: Title
    goal: Goal = ""
    when: When  # the first time, local "YYYY-MM-DDTHH:MM"
    repeat: Repeat = "daily"
    also_qq: bool = False


class RoutinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    goal: Goal | None = None
    when: When | None = None
    repeat: Repeat | None = None
    enabled: bool | None = None
    also_qq: bool | None = None


def _title(value: str) -> str:
    if has_bad_chars(value):
        raise HTTPException(status_code=422, detail="名字里不能有换行或控制字符")
    return value


@router.get("/routines")
async def routines(request: Request, bot_id: str) -> dict[str, Any]:
    state = request.app.state
    if bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail=NO_BOT)
    keeper = state.routines
    return {"routines": [keeper.view(r) for r in await keeper.store.of_bot(bot_id)]}


@router.post("/routines", status_code=201)
async def add(request: Request, body: RoutineCreate) -> dict[str, Any]:
    state = request.app.state
    bot = state.brain.bots.get(body.bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail=NO_BOT)
    chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
    if chat is None:
        raise HTTPException(status_code=409, detail=f"还没有和 {bot.name} 的聊天，先在聊天页开一个")
    made, why = await state.routines.create(
        bot,
        chat,
        title=_title(body.title),
        goal=body.goal,
        when=body.when,
        repeat=body.repeat,
        also_qq=body.also_qq,
    )
    if made is None:
        raise HTTPException(status_code=422, detail=why)
    return {"id": made["id"]}


@router.patch("/routines/{routine_id}")
async def change(request: Request, routine_id: RoutineId, body: RoutinePatch) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    if "title" in changes:
        _title(changes["title"])
    changed, why = await request.app.state.routines.change(routine_id, **changes)
    if changed is None:
        raise HTTPException(status_code=404 if why == NO_ROUTINE else 422, detail=why)
    return {"ok": True}


@router.delete("/routines/{routine_id}")
async def remove(request: Request, routine_id: RoutineId) -> dict[str, Any]:
    if not await request.app.state.routines.remove(routine_id):
        raise HTTPException(status_code=404, detail=NO_ROUTINE)
    return {"ok": True}


@router.post("/routines/{routine_id}/run")
async def run_now(request: Request, routine_id: RoutineId) -> dict[str, Any]:
    task_id, why = await request.app.state.routines.run_now(routine_id)
    if task_id is None:
        raise HTTPException(status_code=404 if why == NO_ROUTINE else 409, detail=why)
    return {"task_id": task_id}


@router.get("/routines/{routine_id}/runs")
async def runs(request: Request, routine_id: RoutineId) -> dict[str, Any]:
    keeper = request.app.state.routines
    if await keeper.store.get(routine_id) is None:
        raise HTTPException(status_code=404, detail=NO_ROUTINE)
    return {"runs": await keeper.store.runs(routine_id)}
