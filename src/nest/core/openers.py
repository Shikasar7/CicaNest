"""冷场时主动开口: in a group of a platform (QQ…) gone quiet, a companion who has it on there says
something on her own — small talk, a little grumble, being cute, or asking who wants to play a little game
(猜数字, 海龟汤, 成语接龙: nest.core.group_games keeps the game).

Set per bot and group (bot_channels.opener, off by default): after `opener_quiet` minutes without a message
there, at most `opener_daily` times a day. Every CHECK_SECONDS, only from DAY_FROM until DAY_UNTIL (local
time), only for a live bot whose account is connected; not while a game of hers is on there, and after two of
hers in a row that nobody answered she leaves that group alone for the day. Her model may say nothing (SKIP),
which costs nothing. What she said that day is kept in the settings (SETTING), so a restart does not reset it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable
from typing import Any

from nest.adapters.registry import can
from nest.core.group_games import KINDS as GAMES
from nest.core.groups import is_desk_group
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

CHECK_SECONDS = 300.0
FIRST_CHECK = 180.0  # after starting: the accounts connect, and a restart is not a reason to talk
DAY_FROM, DAY_UNTIL = 9, 23  # local hours she may start talking in
IGNORED = 2  # hers in a row that nobody answered: no more that day in that group
SETTING = "group_openers"  # {"day": "YYYY-MM-DD", "sent": [[bot id, channel id, at], …]}
# What she starts with, and how likely each is.
MOODS: tuple[tuple[str, float], ...] = (("chat", 0.35), ("grumble", 0.2), ("cute", 0.2), ("game", 0.25))
GAME_KINDS = tuple(GAMES)


def today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


class OpenerKeeper:
    """The checks. `brain` is nest.core.brain.Brain (who has it on, and the line itself)."""

    def __init__(
        self,
        db: Database,
        brain: Any,
        games: Any,
        *,
        clock: Callable[[], float] = time.time,
        chance: Callable[[], float] = random.random,
    ) -> None:
        self.db = db
        self.brain = brain
        self.games = games
        self.clock = clock
        self.chance = chance
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="openers")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_CHECK)
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("冷场开口检查出错")
            await asyncio.sleep(CHECK_SECONDS)

    def pick(self) -> str:
        roll, total = self.chance(), 0.0
        for mood, weight in MOODS:
            total += weight
            if roll < total:
                return mood
        return MOODS[-1][0]

    async def check(self) -> list[tuple[str, int, str]]:
        """One pass: (bot id, channel id, what she started with) for each line that went out."""
        now = self.clock()
        if not DAY_FROM <= time.localtime(now).tm_hour < DAY_UNTIL:
            return []
        repo = SettingsRepo(self.db)
        record = (await repo.all()).get(SETTING) or {}
        sent: list[list[Any]] = record.get("sent", []) if record.get("day") == today(now) else []
        said: list[tuple[str, int, str]] = []
        for bot in list(self.brain.bots.values()):
            if bot.mode != "live" or not bot.provider_id or not can(bot.account_id, "proactive"):
                continue
            adapter = self.brain.adapters.get(bot.account_id)
            if adapter is None or adapter.status().state != "connected":
                continue
            for channel in list(bot.channels.values()):
                if not (channel.enabled and channel.opener and channel.channel.kind == "group"):
                    continue
                if is_desk_group(channel.channel) or self.games.active(bot.id, channel.channel_id):
                    continue
                mine = [float(at) for b, c, at in sent if b == bot.id and c == channel.channel_id]
                if len(mine) >= channel.opener_daily:
                    continue
                rows = await self.brain.store.recent(channel.channel_id, limit=30)
                if not rows or now - float(rows[-1]["sent_at"]) < channel.opener_quiet * 60:
                    continue
                if ignored(rows, mine):
                    continue
                mood = self.pick()
                game = (
                    GAME_KINDS[int(self.chance() * len(GAME_KINDS)) % len(GAME_KINDS)]
                    if mood == "game"
                    else ""
                )
                quiet = now - float(rows[-1]["sent_at"])
                line = await self.brain.open_group(bot, channel, mood, rows, quiet=quiet, game=game)
                if line is None:
                    continue
                sent.append([bot.id, channel.channel_id, now])
                await repo.set_many({SETTING: {"day": today(now), "sent": sent}})
                said.append((bot.id, channel.channel_id, f"{mood}:{game}" if game else mood))
        return said


def ignored(rows: list[dict[str, Any]], mine: list[float]) -> bool:
    """Her last IGNORED lines today each went without anyone else saying a word after them."""
    if len(mine) < IGNORED:
        return False
    times = sorted(mine)[-IGNORED:]
    bounds = [*times[1:], float("inf")]
    for start, end in zip(times, bounds, strict=True):
        if any(not r.get("from_self") and start < float(r["sent_at"]) < end for r in rows):
            return False
    return True
