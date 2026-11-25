"""任务 in the console and the chat window (伙伴模式): a companion's background tasks — list, look into
one step by step, start one, stop, go on, add something while it runs, delete a finished one."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.core.desk import chat_of, chosen_chats
from nest.core.tasks import GOAL_MAX, LIMITS, NOTE_MAX, TITLE_MAX

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

TaskId = Annotated[int, Path(ge=1, le=2**63 - 1)]
NO_TASK = "没有这个任务"


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
    goal: Annotated[str, Field(max_length=GOAL_MAX)] = ""


class TaskNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=NOTE_MAX)]


class TaskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int | None = None
    max_minutes: int | None = None
    max_tokens: int | None = None
    at_once: int | None = None


@router.get("/task-settings")
async def limits(request: Request) -> dict[str, Any]:
    """The limits each task gets, and the range of each."""
    ranges = {name: {"default": d, "min": low, "max": high} for name, (d, low, high) in LIMITS.items()}
    return {"limits": await request.app.state.tasks.limits(), "ranges": ranges}


@router.put("/task-settings")
async def set_limits(request: Request, body: TaskLimits) -> dict[str, Any]:
    return {"limits": await request.app.state.tasks.set_limits(**body.model_dump(exclude_none=True))}


def _view(task: dict[str, Any], keeper: Any) -> dict[str, Any]:
    return task | {"live": keeper.running(task["id"])}


@router.get("/tasks")
async def tasks(request: Request, bot_id: str | None = None) -> dict[str, Any]:
    state = request.app.state
    if bot_id is not None and bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    rows = await (state.tasks.store.of_bot(bot_id) if bot_id else state.tasks.store.recent())
    return {"tasks": [_view(r, state.tasks) for r in rows]}


@router.get("/tasks/{task_id}")
async def task(request: Request, task_id: TaskId) -> dict[str, Any]:
    keeper = request.app.state.tasks
    found = await keeper.store.get(task_id)
    if found is None:
        raise HTTPException(status_code=404, detail=NO_TASK)
    return _view(found, keeper) | {"log": await keeper.store.steps(task_id)}


@router.post("/tasks", status_code=201)
async def add(request: Request, body: TaskCreate) -> dict[str, Any]:
    state = request.app.state
    bot = state.brain.bots.get(body.bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
    if chat is None:
        raise HTTPException(status_code=409, detail=f"还没有和 {bot.name} 的聊天，先在聊天页开一个")
    task_id, why = await state.tasks.begin(bot, chat, body.title, body.goal, source="console")
    if task_id is None:
        raise HTTPException(status_code=409, detail=why)
    return {"id": task_id}


@router.post("/tasks/{task_id}/stop")
async def stop(request: Request, task_id: TaskId) -> dict[str, Any]:
    if not await request.app.state.tasks.halt(task_id):
        raise HTTPException(status_code=409, detail="这个任务没有在做")
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
async def resume(request: Request, task_id: TaskId) -> dict[str, Any]:
    if not await request.app.state.tasks.resume(task_id):
        raise HTTPException(status_code=409, detail="这个任务不能接着做（还在做，或者已经做完了）")
    return {"ok": True}


@router.post("/tasks/{task_id}/notes")
async def note(request: Request, task_id: TaskId, body: TaskNote) -> dict[str, Any]:
    if not await request.app.state.tasks.add_note(task_id, body.text):
        raise HTTPException(status_code=409, detail="这个任务已经不在做了，追加不进去")
    return {"ok": True}


@router.delete("/tasks/{task_id}")
async def remove(request: Request, task_id: TaskId) -> dict[str, Any]:
    if not await request.app.state.tasks.store.delete(task_id):
        raise HTTPException(status_code=409, detail="没有这个任务，或者它还在做（先停下）")
    return {"ok": True}
