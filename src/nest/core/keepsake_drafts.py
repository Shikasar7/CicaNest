"""回忆草稿 (伙伴模式): her chat with the user is cleaned away after some days (history_days, the 系统 page),
and what she learns from it as 长期记忆 is a few short lines. So now and then — about once a week, and in
any case before the oldest of it is cleaned away — she writes what was worth keeping into a record of her
回忆档案: the days, what happened, what the user said that mattered, what they agreed. Only from the chat
itself; what she is not sure of she says so.

It is a draft: she does not look things up in it until the user has read it, corrected it if need be, and
kept it (the 回忆档案 pane). Nothing goes anywhere; the model is her own, one call per draft. When nothing was
worth keeping she says SKIP and no draft is made.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nest.core.desk import chat_of, chosen_chats
from nest.core.keepsakes import BODY_MAX, RECORDS_MAX, TITLE_MAX
from nest.core.prompt import first_part
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotConfig

log = logging.getLogger(__name__)

DAY = 86_400.0
SOURCE = "聊天整理"

ASK = (
    "下面是你和{user}从{start}到{end}的聊天记录（只有这段时间的）。这些记录过些天会被清掉，"
    "所以请你把其中值得长久记住的事整理成一份回忆，留在你的回忆档案里。\n\n"
    "写法：\n"
    "- 按日期分段，每段用「## 11月24日」这样的标题；\n"
    "- 写发生了什么、{user}说过的重要的话（可以原样引用）、你们说好的事、{user}的近况和心情；\n"
    "- 只写聊天里真有的，不补、不猜；拿不准的写明「不确定」；数字和状态写明是哪天说的；\n"
    "- 闲聊、寒暄、已经办完不再重要的小事不用写；\n"
    "- 用你自己的口吻，像写给以后的自己看的回忆。\n\n"
    "如果这段时间没有值得长久记住的事，只输出 SKIP。\n\n"
    "聊天记录：\n{lines}"
)


@dataclass(frozen=True)
class DraftConfig:
    every_days: float = 7.0  # about once a week
    ahead_days: float = 5.0  # and in any case this long before the oldest unwritten message is cleaned away
    min_user_lines: int = 10  # from the user since the last one, for the weekly one
    max_lines: int = 400  # read per draft, oldest first: a longer stretch takes several drafts
    line_chars: int = 4000  # a message of the desk chat to its end (nest.core.desk.MAX_CHARS)
    read_chars: int = 40_000  # read per draft, as max_lines
    max_tokens: int = 3000
    check_seconds: float = 3600.0
    first_delay: float = 300.0


def _day(at: float) -> str:
    moment = dt.datetime.fromtimestamp(at).astimezone()
    return f"{moment.month}月{moment.day}日"


def _when(at: float) -> str:
    moment = dt.datetime.fromtimestamp(at).astimezone()
    return f"{moment.month}-{moment.day} {moment:%H:%M}"


def title_of(start: float, end: float) -> str:
    first, last = _day(start), _day(end)
    return f"回忆草稿：{first}" if first == last else f"回忆草稿：{first}–{last}"


def clean(text: str) -> str:
    """Her draft as kept: "" when she said there was nothing (SKIP)."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("markdown").strip()
    return "" if not body or body.upper().startswith("SKIP") else body[:BODY_MAX]


class KeepsakeDrafter:
    """`brain`: nest.core.brain.Brain (bots, store, its model calls); `keepsakes`: the KeepsakeKeeper;
    `history_days`: how many days of chat the daily cleanup keeps, read each time (the 系统 page)."""

    def __init__(
        self,
        brain: Any,
        keepsakes: Any,
        broadcaster: Any,
        history_days: Callable[[], Awaitable[float]],
        *,
        config: DraftConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.brain = brain
        self.keepsakes = keepsakes
        self.store = keepsakes.store
        self.broadcaster = broadcaster
        self.history_days = history_days
        self.config = config or DraftConfig()
        self.clock = clock
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="keepsake-drafts")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(self.config.first_delay)
        while True:
            try:
                await self.run()
            except Exception:
                log.exception("整理回忆草稿出错")
            await asyncio.sleep(self.config.check_seconds)

    @for_purpose("memory")
    async def run(self) -> int:
        """One look at every companion that talks: write the drafts that are due. How many were written."""
        now = self.clock()
        keep_days = await self.history_days()
        chosen = await chosen_chats(self.brain.store.db)
        written = 0
        for bot in list(self.brain.bots.values()):
            chat = chat_of(bot, chosen.get(bot.id))
            if bot.mode != "live" or not bot.provider_id or chat is None:
                continue
            try:
                written += await self._maybe(bot, chat.channel_id, now, keep_days)
            except (ProviderUnavailable, ProviderError) as exc:
                log.info("%s 的回忆草稿这次没写成（%s），下次再试", bot.name, exc)
        return written

    async def _maybe(self, bot: BotConfig, channel_id: int, now: float, keep_days: float) -> int:
        c = self.config
        upto, run_at = await self.store.progress(bot.id)
        since = max(upto, now - keep_days * DAY)
        rows = await self._rows(channel_id, since)
        users = [r for r in rows if not r["from_self"]]
        if not users:
            return 0
        # Kept only a few days: half of that, or each new line would be a draft of its own.
        ahead = min(c.ahead_days, keep_days / 2)
        soon_gone = users[0]["sent_at"] < now - (keep_days - ahead) * DAY
        enough = len(users) >= c.min_user_lines and now - run_at >= c.every_days * DAY
        if not enough and not soon_gone:  # about to be cleaned away: written up however little it is
            return 0
        if len(await self.store.of_bot(bot.id)) >= RECORDS_MAX:
            log.info("%s 的回忆档案满了（%d 份），回忆草稿先不写", bot.name, RECORDS_MAX)
            return 0
        part = first_part(rows, lambda _: c.line_chars, c.read_chars)
        more = len(part) < len(rows) or len(rows) >= c.max_lines
        rows = part
        body = await self._write(bot, channel_id, rows)
        end = rows[-1]["sent_at"]
        title = title_of(rows[0]["sent_at"], end)
        draft = None
        if body:
            draft = await self.store.add(bot.id, title[:TITLE_MAX], body, source=SOURCE, draft=True)
        # Only now (the draft is safe). Read only part of a long stretch: the rest is written up at the next
        # look, not a week later.
        await self.store.set_progress(bot.id, end, run_at if more else now)
        if draft is None:
            log.info("%s 看了 %s 的聊天，觉得没有要留成回忆的", bot.name, title.removeprefix("回忆草稿："))
            return 0
        self.broadcaster.publish("bots", {"bot_id": bot.id, "keepsake": draft})  # the chat list shows it
        log.info("%s 写了一份回忆草稿（%d 字），等对方过目", bot.name, len(body))
        return 1

    async def _rows(self, channel_id: int, since: float) -> list[dict[str, Any]]:
        rows = await self.brain.store.db.fetchall(
            """SELECT sender_name, from_self, text, sent_at FROM messages
               WHERE channel_id = ? AND sent_at > ? AND recalled_at IS NULL AND trim(text) <> ''
               ORDER BY sent_at, id LIMIT ?""",
            (channel_id, since, self.config.max_lines),
        )
        return [dict(r) | {"from_self": bool(r["from_self"])} for r in rows]

    async def _write(self, bot: BotConfig, channel_id: int, rows: list[dict[str, Any]]) -> str:
        channel = bot.channels.get(channel_id)
        persona = (channel.persona if channel else "").strip() or f"你是{bot.name}。"
        user = next((r["sender_name"] for r in rows if not r["from_self"] and r["sender_name"]), "对方")
        lines = "\n".join(
            f"{_when(r['sent_at'])} {'你' if r['from_self'] else r['sender_name'] or user}："
            + " ".join(str(r["text"]).split())[: self.config.line_chars]
            for r in rows
        )
        start, end = _day(rows[0]["sent_at"]), _day(rows[-1]["sent_at"])
        ask = ASK.format(user=user, start=start, end=end, lines=lines)
        completion, _, _ = await self.brain._complete(
            bot, [ChatMessage("system", persona), ChatMessage("user", ask)], max_tokens=self.config.max_tokens
        )
        return clean(completion.text)
