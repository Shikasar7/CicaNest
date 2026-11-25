"""定时任务 (伙伴模式): something a companion does again and again at set times —
"every morning at 8, look at the weather and the news I follow and tell me".

Each time it is due a task (nest.core.tasks) is started for it, in her chat with the user, and her report
goes there as usual (also to QQ when the routine says so). Nobody is there to nod: the task is unattended,
so steps that need the user's nod (a plugin's confirm-listed tool, a step on the Windows PC outside 完全允许)
are not done and she says in her report where she got stuck. There is no "skip the nods for routines".

One she sets herself in a chat (set_routine, source "chat") starts switched off: it runs only after the
user has read what it would do and switched it on in the console — words in a page she read cannot plant
something that then runs every night with nobody there.

When the last one is still queued or running, this time is skipped (and noted). A time missed while nest
was not running is not made up for: it is noted and the routine goes on from the next time. The times
repeat as reminders do (每天 / 工作日 / 每周 / 每月, nest.core.reminders.next_due), or every few hours
from the first time (every_2h: 08:00, 10:00, 12:00 …).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import Any

from nest.core.reminders import REPEAT_LABELS, WEEKDAYS, local, next_due, when_label
from nest.storage.bots import BotChannel, BotConfig
from nest.storage.db import Database
from nest.storage.routines import RoutineStore

log = logging.getLogger(__name__)

CHECK_SECONDS = 30.0
GRACE_SECONDS = 5 * 60.0  # due this long ago at most (nest was busy starting): still run
HOURLY = ("every_1h", "every_2h", "every_3h", "every_4h", "every_6h", "every_12h")
REPEATS = ("daily", "weekdays", "weekly", "monthly", *HOURLY)
REPEAT_WORDS = "daily、weekdays、weekly、monthly 或 every_1h / 2h / 3h / 4h / 6h / 12h"
TITLE_MAX = 40
GOAL_MAX = 2000
PER_BOT = 10
SKIP_BUSY = "上一次还没做完，这次跳过"
SKIP_MISSED = "那时 nest 没在运行，错过了（不补做）"


def hours_of(repeat: str) -> int | None:
    """every_3h -> 3; None for the calendar ones."""
    return int(repeat[6:-1]) if repeat in HOURLY else None


def following(first_at: float, repeat: str, now: float) -> float:
    """Its first time after `now`."""
    hours = hours_of(repeat)
    if hours is None:
        return next_due(first_at, repeat, now) or now + 86400
    step = hours * 3600
    if first_at > now:
        return first_at
    return first_at + (int((now - first_at) // step) + 1) * step


def rule_label(first_at: float, repeat: str) -> str:
    """「每天 08:00」「每周一 08:00」「每月 3 号 08:00」「工作日每天 08:00」「每 2 小时（从 08:00 起）」."""
    moment = local(first_at)
    clock = f"{moment:%H:%M}"
    hours = hours_of(repeat)
    if hours is not None:
        return f"每 {hours} 小时（从 {clock} 起）"
    if repeat == "weekly":
        return f"每周{WEEKDAYS[moment.weekday()]} {clock}"
    if repeat == "monthly":
        return f"每月 {moment.day} 号 {clock}"
    return f"{REPEAT_LABELS.get(repeat, '')} {clock}".strip()


def parse_when(text: str) -> float | None:
    """'YYYY-MM-DD HH:MM' or the console's 'YYYY-MM-DDTHH:MM' (local time) -> a timestamp; None when it is
    neither."""
    try:
        return dt.datetime.strptime(text.strip().replace("T", " "), "%Y-%m-%d %H:%M").astimezone().timestamp()
    except ValueError:
        return None


class RoutineKeeper:
    def __init__(
        self,
        db: Database,
        brain: Any,
        broadcaster: Any,
        *,
        clock: Callable[[], float] = time.time,
        check_seconds: float = CHECK_SECONDS,
    ) -> None:
        self.store = RoutineStore(db)
        self.brain = brain
        self.broadcaster = broadcaster
        self._clock = clock
        self._check_seconds = check_seconds
        self._task: asyncio.Task[None] | None = None

    # -- running ----------------------------------------------------------------------------------------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="routines")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("定时任务检查出错")
            await asyncio.sleep(self._check_seconds)

    async def check(self) -> int:
        """Start what is due; how many were started."""
        now = self._clock()
        started = 0
        for routine in await self.store.due(now):
            await self.store.update(
                routine["id"], next_at=following(routine["first_at"], routine["repeat"], now), last_at=now
            )
            if now - routine["next_at"] > GRACE_SECONDS:
                await self.store.add_run(routine["id"], routine["next_at"], skipped=SKIP_MISSED)
                log.info("定时任务 %s 错过了一次（nest 那时没在运行）", routine["id"])
            elif await self._run(routine, now, attended=False):
                started += 1
            self._publish(routine["bot_id"])
        return started

    async def run_now(self, routine_id: int) -> tuple[int | None, str]:
        """「现在跑一次」: the user is there, so it is not unattended. (task id, "") or (None, why not)."""
        routine = await self.store.get(routine_id)
        if routine is None:
            return None, "没有这个定时任务"
        task_id = await self._run(routine, self._clock(), attended=True)
        self._publish(routine["bot_id"])
        if task_id is None:
            last = (await self.store.runs(routine_id, limit=1) or [{}])[0]
            return None, last.get("skipped") or "没开成"
        return task_id, ""

    async def _run(self, routine: dict[str, Any], now: float, *, attended: bool) -> int | None:
        tasks = self.brain.tasks
        bot = self.brain.bots.get(routine["bot_id"])
        channel = bot.channels.get(routine["channel_id"]) if bot else None
        if tasks is None or bot is None or channel is None:
            await self.store.add_run(routine["id"], now, skipped="她或这段私聊已经不在了")
            return None
        if await tasks.store.active_of_routine(routine["id"]) is not None:
            await self.store.add_run(routine["id"], now, skipped=SKIP_BUSY)
            log.info("定时任务 %s 上一次还没做完，这次跳过", routine["id"])
            return None
        task_id, why = await tasks.begin(
            bot,
            channel,
            routine["title"],
            routine["goal"],
            source="routine",
            unattended=not attended,
            also_qq=routine["also_qq"],
            routine_id=routine["id"],
        )
        await self.store.add_run(routine["id"], now, task_id=task_id, skipped="" if task_id else why)
        if task_id is not None:
            log.info("%s 到点开始做定时任务 %s（任务 %s）", bot.name, routine["id"], task_id)
        return task_id

    def _publish(self, bot_id: str) -> None:
        self.broadcaster.publish("routines", {"bot_id": bot_id})

    # -- setting them -----------------------------------------------------------------------------------

    async def create(
        self,
        bot: BotConfig,
        channel: BotChannel,
        *,
        title: str,
        goal: str,
        when: str,
        repeat: str,
        also_qq: bool = False,
        source: str = "console",
    ) -> tuple[dict[str, Any] | None, str]:
        """(the routine, "") or (None, why not)."""
        title, goal = " ".join(title.split())[:TITLE_MAX], goal.strip()[:GOAL_MAX]
        if not title:
            return None, "定时任务要有个名字"
        if repeat not in REPEATS:
            return None, f"多久做一次要是 {REPEAT_WORDS}"
        now = self._clock()
        first = parse_when(when)
        if first is None:
            return None, "第一次的时间要写成 YYYY-MM-DD HH:MM"
        if len(await self.store.of_bot(bot.id)) >= PER_BOT:
            return None, f"她已经有 {PER_BOT} 个定时任务了，先删掉一个"
        # Hers (from a chat): off until the user switches it on. The user's own (console): on.
        routine_id = await self.store.add(
            enabled=source != "chat",
            bot_id=bot.id,
            channel_id=channel.channel_id,
            title=title,
            goal=goal or title,
            first_at=first,
            repeat=repeat,
            next_at=following(first, repeat, now),
            also_qq=also_qq,
            source=source,
        )
        self._publish(bot.id)
        log.info("%s 有了一个定时任务（%s，%s）", bot.name, routine_id, rule_label(first, repeat))
        return await self.store.get(routine_id), ""

    async def change(self, routine_id: int, **changes: Any) -> tuple[dict[str, Any] | None, str]:
        """title / goal / when + repeat / enabled / also_qq. The next time is worked out again."""
        routine = await self.store.get(routine_id)
        if routine is None:
            return None, "没有这个定时任务"
        now = self._clock()
        fields: dict[str, Any] = {}
        if "title" in changes:
            title = " ".join(str(changes["title"]).split())[:TITLE_MAX]
            if not title:
                return None, "定时任务要有个名字"
            fields["title"] = title
        if "goal" in changes:
            fields["goal"] = str(changes["goal"]).strip()[:GOAL_MAX] or routine["title"]
        repeat = changes.get("repeat", routine["repeat"])
        if repeat not in REPEATS:
            return None, f"多久做一次要是 {REPEAT_WORDS}"
        first = routine["first_at"]
        if changes.get("when"):
            parsed = parse_when(str(changes["when"]))
            if parsed is None:
                return None, "时间要写成 YYYY-MM-DD HH:MM"
            first = parsed
        fields.update(repeat=repeat, first_at=first)
        if "enabled" in changes:
            fields["enabled"] = bool(changes["enabled"])
        if "also_qq" in changes:
            fields["also_qq"] = bool(changes["also_qq"])
        fields["next_at"] = following(first, repeat, now)
        await self.store.update(routine_id, **fields)
        self._publish(routine["bot_id"])
        return await self.store.get(routine_id), ""

    async def remove(self, routine_id: int) -> bool:
        routine = await self.store.get(routine_id)
        if routine is None or not await self.store.delete(routine_id):
            return False
        self._publish(routine["bot_id"])
        return True

    def view(self, routine: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        return routine | {
            "when": f"{local(routine['first_at']):%Y-%m-%dT%H:%M}",
            "rule": rule_label(routine["first_at"], routine["repeat"]),
            "next": when_label(routine["next_at"], now) if routine["enabled"] else "",
        }

    async def describe(self, bot: BotConfig) -> str:
        """Her routines, for her (the list_routines tool)."""
        found = await self.store.of_bot(bot.id)
        if not found:
            return "你现在没有定时任务"
        lines = []
        for r in found:
            state = f"下次 {self.view(r)['next']}" if r["enabled"] else "停用中"
            lines.append(f"{r['id']}号：{rule_label(r['first_at'], r['repeat'])}「{r['title']}」（{state}）")
        return "\n".join(lines)
