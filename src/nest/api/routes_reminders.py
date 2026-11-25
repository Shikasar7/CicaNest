"""提醒 in the console (伙伴模式): a companion's reminders for the user — list, add, change, switch off,
delete — and the desktop app saying it is open (so reminders go to the desk rather than to QQ).

Times come and go as local time "YYYY-MM-DDTHH:MM" (what an <input type="datetime-local"> holds) besides the
unix seconds, so the console never works out time zones.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.core.desk import chat_of, chosen_chats
from nest.core.reminders import TEXT_MAX, local, next_due, when_label

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

ReminderId = Annotated[int, Path(ge=1, le=2**63 - 1)]
Repeat = Annotated[str, Field(pattern="^(|daily|weekdays|weekly|monthly)$")]
NO_BOT = "没有这个 bot"


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    text: Annotated[str, Field(min_length=1, max_length=TEXT_MAX)]
    due: str  # local "YYYY-MM-DDTHH:MM"
    repeat: Repeat = ""
    also_qq: bool = False


class ReminderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=TEXT_MAX)] | None = None
    due: str | None = None
    repeat: Repeat | None = None
    also_qq: bool | None = None
    active: bool | None = None


def _due(value: str) -> float:
    try:
        moment = dt.datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise HTTPException(status_code=422, detail="时间要写成 2026-01-31T09:00") from None
    return moment.astimezone().timestamp()


def _text(value: str) -> str:
    text = value.strip()
    if not text or has_bad_chars(text):
        raise HTTPException(status_code=422, detail="提醒的内容不能为空，也不能有换行或控制字符")
    return text


def _view(row: dict[str, Any], now: float) -> dict[str, Any]:
    return row | {
        "due": f"{local(row['due_at']):%Y-%m-%dT%H:%M}",
        "when": when_label(row["due_at"], now),
    }


@router.get("/reminders")
async def reminders(request: Request, bot_id: str) -> dict[str, Any]:
    state = request.app.state
    if bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail=NO_BOT)
    now = time.time()
    rows = await state.reminders.store.of_bot(bot_id)
    return {"reminders": [_view(r, now) for r in rows], "desk_open": state.reminders.desk_open()}


@router.post("/reminders", status_code=201)
async def add(request: Request, body: ReminderCreate) -> dict[str, Any]:
    state = request.app.state
    bot = state.brain.bots.get(body.bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail=NO_BOT)
    chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
    if chat is None:
        raise HTTPException(status_code=409, detail=f"还没有和 {bot.name} 的聊天，先在聊天页开一个")
    now = time.time()
    due = _due(body.due)
    if due < now - 60:
        if not body.repeat:
            raise HTTPException(status_code=422, detail="这个时间已经过去了")
        due = next_due(due, body.repeat, now) or due
    new = await state.reminders.store.add(
        bot_id=bot.id,
        channel_id=chat.channel_id,
        text=_text(body.text),
        due_at=due,
        repeat=body.repeat,
        also_qq=body.also_qq,
    )
    return {"id": new}


@router.patch("/reminders/{reminder_id}")
async def change(request: Request, reminder_id: ReminderId, body: ReminderPatch) -> dict[str, Any]:
    state = request.app.state
    found = await state.reminders.store.get(reminder_id)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这个提醒")
    changes: dict[str, Any] = {}
    if body.text is not None:
        changes["text"] = _text(body.text)
    if body.repeat is not None:
        changes["repeat"] = body.repeat
    if body.also_qq is not None:
        changes["also_qq"] = body.also_qq
    if body.due is not None:
        changes["due_at"] = _due(body.due)
    if body.active is not None:
        changes["active"] = body.active
    now = time.time()
    due = changes.get("due_at", found["due_at"])
    repeat = changes.get("repeat", found["repeat"])
    if changes.get("active", found["active"]) and due < now - 60:
        if not repeat:
            raise HTTPException(status_code=422, detail="这个时间已经过去了，换个时间再打开")
        changes["due_at"] = next_due(due, repeat, now) or due
    if "due_at" in changes or changes.get("active") is False:
        changes["nudge_at"] = None
    await state.reminders.store.update(reminder_id, **changes)
    return {"ok": True}


@router.delete("/reminders/{reminder_id}")
async def remove(request: Request, reminder_id: ReminderId) -> dict[str, Any]:
    if not await request.app.state.reminders.store.delete(reminder_id):
        raise HTTPException(status_code=404, detail="没有这个提醒")
    return {"ok": True}


@router.post("/desk/presence")
async def presence(request: Request) -> dict[str, Any]:
    """The desktop app is open (it says so about every minute): reminders go to it rather than to QQ. It may
    say how long the user has been idle ({"idle": seconds}): 主动找你 (nest.core.outreach) waits for them; and
    how many seconds of 安静一会儿 are left ({"quiet": seconds}): nobody comes to them until then."""
    request.app.state.reminders.seen()
    idle = quiet = None
    with contextlib.suppress(ValueError, TypeError):
        body = await request.json() if await request.body() else {}
        body = body if isinstance(body, dict) else {}
        idle, quiet = _seconds(body.get("idle")), _seconds(body.get("quiet"), most=2 * 86_400)
    outreach = getattr(request.app.state, "outreach", None)
    if outreach is not None:
        outreach.seen(idle, quiet)
    return {"ok": True}


def _seconds(value: Any, most: float = float("inf")) -> float | None:
    """A count of seconds the desktop app sent: a number from 0 to `most`, else not sent."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= most:
        return None
    return float(value)
