"""主动搭话 (伙伴模式): now and then a companion says something to the user on her own — asks how they are,
picks up something they talked about, is a little clingy — in their chat with her, on the desk.

The desktop app decides when there is a moment (the user is at the computer but pausing, she is out as a
pet, nobody asked her to be quiet) and asks; nest decides whether she may, and what she says:
  · not at night (QUIET_FROM – QUIET_UNTIL, local time),
  · at most PER_DAY lines a day for her, PER_HOUR an hour for all companions together,
  · not within GAP_SECONDS of her last one, nor while a conversation is going on (anything said in the chat
    within GAP_SECONDS),
  · not after IGNORED lines in a row that nobody answered: then she leaves it for the day; nor while a
    message she came with (主动找你, nest.core.outreach) waits for an answer.
Her model writes the line from her persona, the recent chat and what she remembers of the user; it may also
decide to say nothing (SKIP), which costs no budget. Her lines are stored with ids starting "app:c:", so
they can be counted and the console does not raise a notification for them.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nest.core.desk import APP, PROACTIVE, chat_of, chosen_chats, unanswered
from nest.core.events import Segment
from nest.core.initiative import quiet_now
from nest.core.prompt import speaker
from nest.core.reminders import now_label, one_line
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotConfig
from nest.storage.db import Database

log = logging.getLogger(__name__)

KIND = "c:"  # message ids of her lines: "app:c:<hex>"
OUTREACH = PROACTIVE[1]  # 主动找你's (nest.core.outreach)
MAX_CHARS = 80
MAX_TOKENS = 600
CONTEXT = 12

ASK = (
    "现在是{now}。你们是私聊，你想不想主动跟对方说一句话？可以关心一下对方、分享一个小念头、"
    "撒个娇、问问在忙什么，或者自然地提起之前聊过的事。像发消息那样随口一句，三十字以内，口语，不要加引号。"
    "{recent}不想说话，或者对方明显在忙、刚说过晚安之类，就只输出 SKIP。"
)
RECENT = "你最近主动说过：{said}。这次说点不一样的。"


@dataclass(frozen=True)
class ChatterConfig:
    per_day: int = 10
    per_hour: int = 4  # all companions together
    gap_seconds: float = 600.0
    ignored: int = 3
    quiet_from: int = 23
    quiet_until: int = 8


def start_of_day(at: float) -> float:
    moment = dt.datetime.fromtimestamp(at).astimezone()
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class ChatterKeeper:
    """`brain` is nest.core.brain.Brain (bots, models, memory); `desk` nest.core.desk.Desk."""

    def __init__(
        self,
        db: Database,
        brain: Any,
        desk: Any,
        *,
        config: ChatterConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.brain = brain
        self.desk = desk
        self.config = config or ChatterConfig()
        self._clock = clock

    async def _lines_since(self, since: float, channel_id: int | None = None) -> int:
        where = "channel_id = ? AND " if channel_id is not None else ""
        params: tuple[Any, ...] = (channel_id,) if channel_id is not None else ()
        row = await self.db.fetchone(
            f"""SELECT count(*) AS n FROM messages WHERE {where}from_self = 1 AND external_id LIKE ?
                AND sent_at >= ?""",
            (*params, f"{APP}{KIND}%", since),
        )
        return int(row["n"]) if row else 0

    async def why_not(self, bot: BotConfig, channel_id: int, now: float) -> str:
        """Why she may not speak now; "" when she may."""
        config = self.config
        hour = dt.datetime.fromtimestamp(now).astimezone().hour
        if quiet_now(hour, config.quiet_from, config.quiet_until):
            return "免打扰的时段不主动说话"
        if await self._lines_since(start_of_day(now), channel_id) >= config.per_day:
            return f"今天已经主动说了 {config.per_day} 句"
        if await self._lines_since(now - 3600) >= config.per_hour:
            return "这一小时大家已经说得够多了"
        last = await self.brain.store.recent(channel_id, limit=CONTEXT)
        if last and now - last[-1]["sent_at"] < config.gap_seconds:
            return "刚聊过或刚说过"
        waiting = unanswered(last, start_of_day(now))
        if OUTREACH in waiting:
            return "她主动发的消息还没人回，不换个法子追着说"
        if waiting.count(KIND) >= config.ignored:
            return f"连着 {config.ignored} 句没人回，今天不说了"
        return ""

    @for_purpose("chatter")
    async def maybe(self, bot_id: str) -> dict[str, Any]:
        """Let her say something if she may and wants to. {"said": bool, "reason": str}."""
        bot = self.brain.bots.get(bot_id)
        if bot is None:
            return {"said": False, "reason": "没有这个 bot"}
        if bot.mode != "live":
            return {"said": False, "reason": "不是正式发言"}
        chat = chat_of(bot, (await chosen_chats(self.db)).get(bot.id))
        if chat is None:
            return {"said": False, "reason": "还没有和对方的聊天"}
        now = self._clock()
        reason = await self.why_not(bot, chat.channel_id, now)
        if reason:
            return {"said": False, "reason": reason}
        words = await self._words(bot, chat, now)
        if not words:
            return {"said": False, "reason": "她这次没想说话"}
        await self.desk.deliver(bot, chat, [Segment("text", {"text": words})], kind=KIND)
        log.info("%s 主动说了一句", bot.name)
        return {"said": True, "reason": ""}

    async def _words(self, bot: BotConfig, chat: Any, now: float) -> str:
        rows = await self.brain.store.recent(chat.channel_id, limit=CONTEXT)
        mine = [r["text"] for r in rows if str(r["external_id"]).startswith(f"{APP}{KIND}")][-3:]

        def show(row: dict[str, Any]) -> str:
            who = speaker(row, bot.name) if row["from_self"] else row["sender_name"]
            return f"{who}：{' '.join(str(row.get('text') or '').split())[:200]}"

        parts = []
        people = await self.brain.memory.recall(bot, chat, rows) if self.brain.memory and rows else ""
        if people:
            parts.append(people)
        if rows:
            parts.append("最近的聊天：\n" + "\n".join(show(r) for r in rows))
        recent = RECENT.format(said="；".join(mine)) if mine else ""
        parts.append(ASK.format(now=now_label(now), recent=recent))
        messages = [
            ChatMessage("system", self.brain._persona(bot, chat) or f"你是{bot.name}。"),
            ChatMessage("user", "\n\n".join(parts)),
        ]
        try:
            completion, _, _ = await self.brain._complete(bot, messages, max_tokens=MAX_TOKENS)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 想主动说话，模型没回（%s）", bot.name, exc)
            return ""
        line = "" if completion.cut else one_line(completion.text)
        if not line or line.upper().startswith("SKIP"):
            return ""
        return line[:MAX_CHARS]
