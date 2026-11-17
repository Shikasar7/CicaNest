"""生日祝福: on a group member's birthday, one of the bots that have it on in the group wishes them a happy
birthday, in its own words, @ing them.

Each bot sets it in its own group settings (off by default). Birthdays come from people's profiles; a
profile that does not show one (not set, or kept private) is left alone. What a profile says is kept
(table `birthdays`) and asked again only after about FRESH_DAYS, one person every LOOKUP_GAP seconds, so a
big group is looked up slowly over the first passes and hardly at all after that. On the day the profile
is asked once more right before the wish, so a birthday hidden since is not wished.

Every CHECK_SECONDS (local time, the machine's own time zone) the groups that have it on are looked at;
from WISH_HOUR until LAST_HOUR the people whose birthday it is are wished. Each person once a year per group
(table `birthday_wishes`): one bot of the group says it, a live one when there is one. An observing bot
only notes what it would have said, and a live bot switched on later that day still wishes. A wish that
failed is tried again at the next check, at most TRIES times. Someone born on 29 February is wished on 28
February in other years.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import random
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from nest.adapters.registry import can
from nest.core.events import Member
from nest.core.welcome import spoken
from nest.llm.base import ChatMessage
from nest.storage.birthdays import BirthdayStore

log = logging.getLogger(__name__)

CHECK_SECONDS = 600.0
FIRST_CHECK = 120.0  # after starting: the accounts connect first
WISH_HOUR = 9  # wishes go out from this hour…
LAST_HOUR = 22  # …until this one (a nest started late in the day still wishes)
FRESH_DAYS = 30.0  # a profile's birthday is asked again after about this long…
SPREAD = 0.25  # …give or take this share, so the lookups of a big group do not all fall due together
LOOKUP_GAP = 1.5  # seconds between two profile lookups
MAX_LOOKUPS = 400  # per pass: what is left waits for the next one
GIVE_UP = 5  # lookups failing in a row: the account is not answering, try again at the next check
TRIES = 3  # a wish that failed is tried again at most so often
RECENT = 3  # the bot's last wishes, shown so it says something new
MAX_CHARS = 60
MAX_TOKENS = 600  # a thinking model thinks first, inside this limit; one short line still fits after
DAY = 86400.0

ASK = (
    "今天是群友{who}的生日。用你自己的口吻在群里祝ta生日快乐：一定要有“生日快乐”，"
    "消息前面会自动@ta，所以不用再写ta的名字。像在群里随口说一句：三十个字以内，口语，不要加引号，"
    "不要解释，只输出这一句。"
)
AGAIN = "你前几次祝别人生日快乐是这么说的：{said}。这次换个说法，不要重复这些句子。"
SOMEONE = "一位群友"
QUOTES = "\"'“”‘’「」『』"
LEAD = "，,、。.！!？? "


def prompt(persona: str, bot_name: str, name: str, said: Sequence[str] = ()) -> list[ChatMessage]:
    """`name` as spoken(); `said`: the bot's latest wishes, newest first."""
    ask = ASK.format(who=f"「{name}」" if name else SOMEONE)
    if said:
        ask += AGAIN.format(said="；".join(f"“{s}”" for s in said[:RECENT]))
    return [
        ChatMessage("system", persona.strip() or f"你是群里的{bot_name}。"),
        ChatMessage("user", ask),
    ]


def line(text: str) -> str:
    """The model's answer as one short line that wishes a happy birthday: the first non-empty line,
    unquoted, without an @ of its own, 生日快乐 put in front when the model left 生日 out."""
    first = next((part.strip() for part in text.splitlines() if part.strip()), "")
    first = first.strip(QUOTES).strip()
    if first.upper() == "SKIP":  # some personas teach the model to stay quiet; a wish is never quiet
        return ""
    first = re.sub(r"^@\S+\s*", "", first)  # the @ comes before it already
    first = re.sub(r"\s+", " ", first).strip().lstrip(LEAD).strip(QUOTES).strip()
    if not first:
        return ""
    if "生日" not in first:
        first = f"生日快乐！{first}"
    return first[:MAX_CHARS]


def fallback() -> str:
    return "生日快乐！"


def born_on(month: int, day: int, today: dt.date) -> bool:
    """Whether today is the birthday; 29 February falls on 28 February in other years."""
    if (month, day) == (today.month, today.day):
        return True
    leap = today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0)
    return (month, day) == (2, 29) and not leap and (today.month, today.day) == (2, 28)


def recheck_after(roll: float) -> float:
    """Seconds until a profile is asked again; `roll` in [0, 1)."""
    return FRESH_DAYS * DAY * (1 + SPREAD * (2 * roll - 1))


class BirthdayKeeper:
    """The checks. `brain` is nest.core.brain.Brain: who has it on, and the wish itself."""

    def __init__(
        self,
        store: BirthdayStore,
        brain: Any,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = asyncio.sleep,
        chance: Callable[[], float] = random.random,
        lookup_gap: float = LOOKUP_GAP,
    ) -> None:
        self.store = store
        self.brain = brain
        self._clock = clock
        self._sleep = sleep
        self._chance = chance
        self._lookup_gap = lookup_gap
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="birthdays")

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
                log.exception("生日祝福检查出错")
            await asyncio.sleep(CHECK_SECONDS)

    async def check(self) -> list[tuple[str, str, str]]:
        """One pass over the groups that have it on; the wishes it made: (group key, user id, outcome)."""
        wishers: dict[str, list[tuple[Any, Any]]] = self.brain.birthday_wishers()
        lookups = MAX_LOOKUPS
        made: list[tuple[str, str, str]] = []
        for key, pool in wishers.items():
            asker = next(((b, c) for b, c in pool if can(b.account_id, "birthdays")), None)
            if asker is None:
                continue
            adapter = self.brain.adapters.get(asker[0].account_id)
            if adapter is None:
                continue
            try:
                members = await adapter.group_members(asker[1].channel.id)
            except Exception as exc:
                log.info("生日祝福：拿不到群 %s 的成员（%s），下次再看", key, exc)
                continue
            ours = self.brain.our_accounts
            people = [m for m in members if m.user_id not in ours and not m.robot]
            platform = asker[1].channel.platform
            lookups = await self._refresh(adapter, platform, people, lookups)
            made += await self._wish(key, platform, adapter, people)
        return made

    async def _refresh(self, adapter: Any, platform: str, people: list[Member], lookups: int) -> int:
        """Ask the profiles that are due, slowly; how many lookups this pass has left."""
        now = self._clock()
        known = await self.store.known(platform, [m.user_id for m in people])
        due = [m for m in people if m.user_id not in known or known[m.user_id].recheck_at <= now]
        failed = 0
        for member in due:
            if lookups <= 0:
                break
            lookups -= 1
            if not await self._look_up(adapter, platform, member.user_id):
                failed += 1
                if failed >= GIVE_UP:
                    log.info("生日祝福：连续 %d 次查不到资料，下次再查", failed)
                    break
            else:
                failed = 0
            await self._sleep(self._lookup_gap)
        return lookups

    async def _look_up(self, adapter: Any, platform: str, user_id: str) -> tuple[int, int] | None:
        try:
            month, day = await adapter.birthday(user_id)
        except Exception as exc:
            log.debug("生日祝福：查资料失败：%r", exc)
            return None
        now = self._clock()
        await self.store.save(
            platform, user_id, month, day, now=now, recheck_at=now + recheck_after(self._chance())
        )
        return month, day

    async def _wish(
        self, key: str, platform: str, adapter: Any, people: list[Member]
    ) -> list[tuple[str, str, str]]:
        now = self._clock()
        today = dt.datetime.fromtimestamp(now).date()
        hour = dt.datetime.fromtimestamp(now).hour
        if not WISH_HOUR <= hour < LAST_HOUR:
            return []
        known = await self.store.known(platform, [m.user_id for m in people])
        made = []
        for member in people:
            birthday = known.get(member.user_id)
            if birthday is None or not born_on(birthday.month, birthday.day, today):
                continue
            # Settings may have changed during the lookups: who may wish, now.
            pool = self.brain.birthday_wishers().get(key, [])
            live = [(b, c) for b, c in pool if b.mode == "live"]
            choices = live or pool
            if not choices:
                return made
            before = await self.store.wish(key, member.user_id, today.year)
            if before is not None and (
                before.outcome == "replied"
                or (before.outcome == "would_reply" and not live)
                or (before.outcome == "failed" and before.tries >= TRIES)
            ):
                continue
            fresh = await self._look_up(adapter, platform, member.user_id)  # still shown, still today?
            if fresh is None or not born_on(*fresh, today):
                continue
            bot, channel = choices[min(int(self._chance() * len(choices)), len(choices) - 1)]
            said = await self.store.said(bot.id, RECENT)
            outcome, words = await self.brain.wish_birthday(
                bot, channel, member.user_id, spoken(member.name), said
            )
            await self.store.note(
                key, member.user_id, today.year, bot_id=bot.id, outcome=outcome, said=words, now=self._clock()
            )
            made.append((key, member.user_id, outcome))
        return made
