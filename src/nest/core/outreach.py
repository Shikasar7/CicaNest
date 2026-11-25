"""主动找你: now and then a companion comes to the user herself.

Not the 「碎碎念」 of the desktop pet (nest.core.chatter, a line in a bubble): these are real messages in her
chat with the user, with a reason:

- 分享: something of her day she wanted to tell (a 「share」 of today's plan in the 小窝, once its time came);
- 关心: a meal time with no word from the user for a while; very late and the user still at the computer;
- 想你了: two days without talking;
- 问起你说过的事: something with a day in her long-term memory of the user (「明天考试」), the day after or
  that evening;
- 打个招呼: the user is back at the computer after a few hours away;
- 纪念日: a hundred days, a year since they first talked;
- 叫你玩: now and then in the afternoon or evening, a little text game (games_hint gives her the rules
  once a game is being talked about).

And in a 桌面群 gone quiet for a while, one of them starts a topic; and when two of them are together in the
小窝, they may make a group of their own (or use the one they have) and start talking there.

nest decides when (a loop every few minutes), only while 「她们的日子」 is on (nest.core.life) and the desktop
app is open with the user at it (it reports how long they have been idle). Budgets keep it gentle: per
companion and per day, for everyone per day and per hour, a pause between two, none right after the user
talked, quiet hours, and after two in a row with no answer she leaves it for the day (nor does she come
while a line of her pet's waits for an answer). 安静一会儿 on the tray holds everyone back. Her own model
writes the line and may decide to say nothing (that costs no budget).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nest.core.chatter import start_of_day
from nest.core.desk import APP, PROACTIVE, chat_of, chosen_chats, unanswered
from nest.core.events import Segment
from nest.core.groups import GroupError
from nest.core.initiative import DAY_STARTS, into_day, quiet_now
from nest.core.life import BEDROOM, block_at, today_key
from nest.core.prompt import clock_label
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotConfig
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

KIND = "o:"  # her message ids: "app:o:…"
PET = PROACTIVE[0]  # the pet's 碎碎念 (nest.core.chatter)
LOOP_SECONDS = 300
CONTEXT = 12
MAX_TOKENS = 200
MAX_CHARS = 120


@dataclass(frozen=True)
class OutreachConfig:
    per_bot_daily: int = 3
    daily: int = 6
    hourly: int = 2
    gap: float = 30 * 60  # between two of hers, and after the last word in her chat
    quiet_from: int = 23  # quiet hours (local); the late-night 关心 is the one exception
    quiet_until: int = 8
    idle_max: float = 10 * 60  # the user counts as at the computer within this much idle time
    ignored_max: int = 2  # this many in a row with no answer: no more that day
    meal_hours: tuple[tuple[int, int], ...] = ((12, 13), (18, 19))
    quiet_chat: float = 2 * 3600  # 关心 at a meal time only when the chat has been quiet this long
    late_hour: int = 0  # 「这么晚还不睡」 from this hour (until quiet_until), the user active
    late_after: float = 1.0  # …or this many hours after they usually leave, once that is known (作息)
    away: float = 3 * 3600  # away this long and back: 打个招呼
    hello_within: float = 20 * 60  # …within this long of being back
    together_chance: float = 0.15  # each look while two of them are together, at most once a day
    together_gap: float = 3 * 86400  # between two groups they make themselves
    miss_after: float = 2 * 86400
    group_cold: float = 3 * 3600
    group_daily: int = 2  # per group
    play_hours: tuple[int, int] = (14, 22)
    play_chance: float = 0.15  # each look within those hours, at most once a day


ASK = """{reason}

现在是 {now}。{rhythm}{life}
最近的聊天：
{talk}

用你自己的语气主动给对方发一条消息（一两句，像平时聊天那样自然，不要解释你为什么来找对方）。
觉得现在不合适就只输出 SKIP。"""
GROUP_ASK = """群里有一阵没人说话了。{life}
最近的聊天：
{talk}

用你自己的语气在群里起个话头（一两句，可以说说你今天的事，或者问问大家），自然一点。不想说就只输出 SKIP。"""
TOGETHER_ASK = """小窝里，你和{others}这会儿都在{room}（你在{doing}）。
你们在桌面上有个小群「{name}」，对方也在里面。
{life}
最近的聊天：
{talk}

用你自己的语气在群里对她们说一句话，起个头聊两句（说说你们正在做的事），自然一点。不想说就只输出 SKIP。"""
REASONS = {
    "hello": "对方有几个小时不在，刚回到电脑前，你想打个招呼。",
    "anniv": "今天是你和对方{label}的日子，你想跟对方说一声。",
    "share": "你今天在小窝里有件事想告诉对方：{share}",
    "meal": "到饭点了，对方有一阵没说话了，你想关心一下对方吃了没有。",
    "late": "已经很晚了，对方还在电脑前，你有点担心对方不睡觉。",
    "miss": "你和对方有两天没聊天了，你有点想对方。",
    "follow": "你记得对方说过：{content}（{when}）。你想问问对方这件事怎么样了。",
    "play": (
        "你有点无聊，想叫对方一起玩个小游戏（成语接龙、猜数字、二十个问题、真心话里挑一个），"
        "用你的语气邀请一下。"
    ),
}
GAMES = (
    "（要是你们在玩小游戏：成语接龙——下一个成语的第一个字接上一个的最后一个字（同音也算），接不上算输；"
    "猜数字——一个人想 1 到 100 的数，另一个猜，只说大了还是小了；二十个问题——一个人想一样东西，另一个最多问 "
    "20 个是不是的问题来猜；真心话——轮流问对方一个问题，要说实话。按你的性格玩，可以耍赖撒娇，但规则要对。）"
)
GAME_WORDS = ("游戏", "接龙", "猜数字", "二十个问题", "真心话", "玩一局", "再来一局")
STATE = "outreach_today"  # today's count, kept across restarts: {"day", "sent", "done"}


def games_hint(rows: list[dict[str, Any]]) -> str:
    """The rules of the little text games, for her prompt while one is being talked about."""
    said = " ".join(str(r.get("text") or "") for r in rows[-6:])
    return GAMES if any(word in said for word in GAME_WORDS) else ""


def _line(text: str) -> str:
    """Her message as it goes out: words only, no SKIP, no quotes around it."""
    words = " ".join(text.split()).strip().strip("「」\"'")
    return "" if not words or words.upper().startswith("SKIP") else words[:MAX_CHARS]


class OutreachKeeper:
    def __init__(
        self,
        brain: Any,
        desk: Any,
        life: Any,
        groups: Any,
        *,
        desk_open: Callable[[], bool],
        config: OutreachConfig | None = None,
        clock: Callable[[], float] = time.time,
        chance: Callable[[], float] = random.random,
    ) -> None:
        self.brain = brain
        self.desk = desk
        self.life = life
        self.groups = groups
        self.desk_open = desk_open
        self.config = config or OutreachConfig()
        self.clock = clock
        self.chance = chance
        self.learns = True  # 作息 (nest.core.initiative)
        self.on = False  # 她们的日子, as last looked: nothing is noted of the user while it is off
        self.rhythm: Any = None
        self.came_at = 0.0  # when the user was last back after being away for a while
        self._here_at: float | None = None  # when they were last seen at the computer
        self.idle: float | None = None  # seconds since the user last touched the computer (desktop app)
        self.quiet_until = 0.0  # the tray's 安静一会儿: nobody comes to the user until then
        self._sent: list[tuple[str, float, int, str]] = []  # (bot id or "g:<id>", when, channel, kind)
        self._done: set[tuple[str, str, str]] = set()  # (day, bot id, what): a share or care said already
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._load()
        self._task = asyncio.create_task(self._loop(), name="outreach")

    async def _load(self) -> None:
        """Today's count, as it was before a restart."""
        stored = (await SettingsRepo(self.brain.repo.db).all()).get(STATE) or {}
        if not isinstance(stored, dict) or stored.get("day") != today_key(self.clock()):
            return
        self._sent = [tuple(x) for x in stored.get("sent") or [] if isinstance(x, list) and len(x) == 4]
        self._done = {(stored["day"], str(a), str(b)) for a, b in stored.get("done") or []}

    async def _save(self, now: float) -> None:
        day = today_key(now)
        await SettingsRepo(self.brain.repo.db).set_many(
            {
                STATE: {
                    "day": day,
                    "sent": [list(s) for s in self._today(now)],
                    "done": [[b, w] for d, b, w in self._done if d == day],
                }
            }
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(LOOP_SECONDS)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("主动找你出错")

    def seen(self, idle: float | None, quiet: float | None = None) -> None:
        """The desktop app's report: how long the user has been idle, and for how many seconds more they
        asked for quiet (安静一会儿 on the tray; 0 or None: not asked)."""
        self.idle = idle
        now = self.clock()
        self.quiet_until = now + quiet if quiet else 0.0
        if idle is None or idle > self.config.idle_max:
            return
        if self._here_at is not None:
            if now - self._here_at >= self.config.away:
                self.came_at = now
            elif self.learns and self.on and self.rhythm is not None:
                self.rhythm.note(now, min(now - self._here_at, 180.0) / 60)
        self._here_at = now

    # -- when ---------------------------------------------------------------------------------------

    def _present(self) -> bool:
        if self.clock() < self.quiet_until:
            return False
        return self.desk_open() and (self.idle is None or self.idle <= self.config.idle_max)

    def _today(self, now: float) -> list[tuple[str, float, int, str]]:
        day = today_key(now)
        return [s for s in self._sent if today_key(s[1]) == day]

    def why_not(self, who: str, now: float) -> str:
        """Why nobody / `who` may not come now; "" when she may."""
        c = self.config
        today = self._today(now)
        if len(today) >= c.daily:
            return f"今天已经主动找过 {len(today)} 次"
        if sum(1 for s in today if now - s[1] < 3600) >= c.hourly:
            return "这个小时已经主动找过了"
        if sum(1 for s in today if s[0] == who) >= c.per_bot_daily:
            return "她今天已经主动找过好几次了"
        mine = [s for s in self._sent if s[0] == who]
        if mine and now - mine[-1][1] < c.gap:
            return "她刚主动找过"
        return ""

    @for_purpose("outreach")
    async def tick(self) -> int:
        """One look: who might come now, and say it if she wants to. How many messages went out."""
        self.on = await self.life.on()
        if not self.on or not self._present():
            return 0
        now = self.clock()
        hour = dt.datetime.fromtimestamp(now).hour
        c = self.config
        quiet = quiet_now(hour, c.quiet_from, c.quiet_until)
        late = self._late(now, hour) and self.idle is not None and self.idle < 120
        if self.rhythm is not None:
            await self.rhythm.save()
        sent = 0
        chosen = await chosen_chats(self.brain.repo.db)
        plan = await self.life.plan_of(today_key(now)) or {}
        candidates = list(self.brain.bots.values())
        random.shuffle(candidates)
        for bot in candidates:
            chat = chat_of(bot, chosen.get(bot.id))
            if bot.mode != "live" or chat is None or self.why_not(bot.id, now):
                continue
            rows = await self.brain.store.recent(chat.channel_id, limit=CONTEXT)
            waiting = unanswered(rows, start_of_day(now))
            if PET in waiting or waiting.count(KIND) >= c.ignored_max:
                continue  # the pet was not answered, or her messages: she does not press on another way
            last = rows[-1]["sent_at"] if rows else 0.0
            if now - last < c.gap:
                continue
            follows = await self._follow_ups(bot, chat.channel_id, now) if not quiet else []
            anniv = await self.life.milestone(bot.id) if not quiet else ""
            reason = self._reason(bot, plan.get(bot.id, []), now, hour, last, quiet, late, follows, anniv)
            if reason is None:
                continue
            kind, text, what = reason
            if await self._say(bot, chat, rows, kind, text, what, now):
                sent += 1
                if self.why_not("", now).startswith(("今天", "这个小时")):
                    return sent
        if not quiet:
            sent += await self._groups(now)
            sent += await self._together(plan, now)
        return sent

    def _late(self, now: float, hour: int) -> bool:
        """Whether it is late for the user: past their usual hours when known, else past midnight."""
        c = self.config
        usual = self.rhythm.usual(now) if self.rhythm is not None and self.learns else None
        if usual is None:
            return c.late_hour <= hour < c.quiet_until
        return into_day(now) >= usual.end + c.late_after and (hour >= 22 or hour < DAY_STARTS)

    def _reason(
        self,
        bot: BotConfig,
        blocks: list[dict[str, str]],
        now: float,
        hour: int,
        last: float,
        quiet: bool,
        late: bool,
        follows: Sequence[tuple[str, str]] = (),
        anniv: str = "",
    ) -> tuple[str, str, str] | None:
        """(kind, what she is told, what marks it done today) or None."""
        c = self.config
        day = today_key(now)
        if late and (day, bot.id, "late") not in self._done:
            return "late", REASONS["late"], "late"
        if quiet:
            return None
        if anniv and (day, bot.id, "anniv") not in self._done:
            return "anniv", REASONS["anniv"].format(label=anniv), "anniv"
        if now - self.came_at <= c.hello_within and (day, "all", "hello") not in self._done:
            self._done.add((day, "all", "hello"))  # one of them says it, once a day
            return "hello", REASONS["hello"], "hello"
        minute = dt.datetime.fromtimestamp(now).hour * 60 + dt.datetime.fromtimestamp(now).minute
        for block in blocks:
            start = int(block["from"][:2]) * 60 + int(block["from"][3:])
            if block.get("share") and start <= minute and (day, bot.id, block["share"]) not in self._done:
                return "share", REASONS["share"].format(share=block["share"]), block["share"]
        for content, when in follows:
            if (day, bot.id, f"follow:{content}") not in self._done:
                return "follow", REASONS["follow"].format(content=content, when=when), f"follow:{content}"
        for start, end in c.meal_hours:
            if (
                start <= hour < end
                and now - last >= c.quiet_chat
                and (day, bot.id, f"meal{start}") not in self._done
            ):
                return f"meal{start}", REASONS["meal"], f"meal{start}"
        if last and now - last >= c.miss_after and (day, bot.id, "miss") not in self._done:
            return "miss", REASONS["miss"], "miss"
        if (
            c.play_hours[0] <= hour < c.play_hours[1]
            and (day, bot.id, "play") not in self._done
            and self.chance() < c.play_chance
        ):
            return "play", REASONS["play"], "play"
        return None

    async def _follow_ups(self, bot: BotConfig, channel_id: int, now: float) -> list[tuple[str, str]]:
        """What the user told her with a day (long-term memory, 约定): due yesterday, or due today from the
        late afternoon. (content, 「昨天」/「今天」)."""
        today = dt.date.fromtimestamp(now)
        days = {(today - dt.timedelta(days=1)).isoformat(): "昨天"}
        if dt.datetime.fromtimestamp(now).hour >= 17:
            days[today.isoformat()] = "今天"
        marks = ",".join("?" * len(days))
        rows = await self.brain.repo.db.fetchall(
            f"""SELECT content, due FROM memories WHERE place = ? AND due IN ({marks})
                AND (bot_id IS NULL OR bot_id = ?) ORDER BY due""",
            (f"chat:{channel_id}", *days, bot.id),
        )
        return [(r["content"], days[r["due"]]) for r in rows]

    # -- saying it ------------------------------------------------------------------------------------

    def _talk(self, bot: BotConfig, rows: list[dict[str, Any]]) -> str:
        def show(row: dict[str, Any]) -> str:
            who = f"你（{bot.name}）" if row["from_self"] else row["sender_name"]
            return f"{who}：{' '.join(str(row.get('text') or '').split())[:200]}"

        return "\n".join(show(r) for r in rows) or "（还没聊过）"

    async def _write(self, bot: BotConfig, persona: str, ask: str) -> str:
        messages = [ChatMessage("system", persona or f"你是{bot.name}。"), ChatMessage("user", ask)]
        try:
            completion, _, _ = await self.brain._complete(bot, messages, max_tokens=MAX_TOKENS)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 想主动找对方，模型没回（%s）", bot.name, exc)
            return ""
        return "" if completion.cut else _line(completion.text)

    async def _say(
        self,
        bot: BotConfig,
        chat: Any,
        rows: list[dict[str, Any]],
        kind: str,
        reason: str,
        what: str,
        now: float,
    ) -> bool:
        day = today_key(now)
        life = await self.life.line(bot)
        rhythm = self.rhythm.line(now) if self.rhythm is not None and self.learns else ""
        ask = ASK.format(
            reason=reason, now=clock_label(now), rhythm=rhythm, life=life, talk=self._talk(bot, rows)
        )
        self._done.add((day, bot.id, what))  # tried once: not again today, said or not
        line = await self._write(bot, self.brain._persona(bot, chat), ask)
        if not line:
            await self._save(now)
            return False
        shown = await self.desk.deliver(bot, chat, [Segment("text", {"text": line})], kind=KIND)
        if shown.state != "succeeded":
            return False
        self._sent.append((bot.id, now, chat.channel_id, kind))
        await self._save(now)
        if kind == "share":
            await self.life.remember(bot.id, what, "share")
        log.info("%s 主动找了对方（%s）", bot.name, kind)
        return True

    async def _groups(self, now: float) -> int:
        """A 桌面群 quiet for long: one of them starts a topic (at most `group_daily` a day per group)."""
        sent = 0
        for group in await self.groups.groups():
            key = f"g:{group['id']}"
            if group["view"] is None or self.why_not(key, now):
                continue
            if sum(1 for s in self._today(now) if s[0] == key) >= self.config.group_daily:
                continue
            rows = await self.brain.store.recent(group["view"], limit=CONTEXT)
            if rows and now - rows[-1]["sent_at"] < self.config.group_cold:
                continue
            if not rows:
                continue  # a group nobody has said anything in yet: the user starts it
            members = [
                (b, c)
                for b, c in await self.groups.members_of(group["id"])
                if b.mode == "live" and b.provider_id
            ]
            if not members:
                continue
            bot, channel_id = members[int(self.chance() * len(members)) % len(members)]
            channel = bot.channels.get(channel_id)
            if channel is None:
                continue
            talk = self._talk(bot, rows)
            ask = GROUP_ASK.format(life=await self.life.line(bot), talk=talk)
            line = await self._write(bot, self.brain._persona(bot, channel), ask)
            if not line:
                self._sent.append((key, now, group["view"], "group-skip"))
                await self._save(now)
                continue
            shown = await self.desk.deliver(bot, channel, [Segment("text", {"text": line})], kind=KIND)
            if shown.state == "succeeded":
                self._sent.append((key, now, group["view"], "group"))
                await self._save(now)
                sent += 1
        return sent

    async def _together(self, plan: dict[str, list[dict[str, str]]], now: float) -> int:
        """Two or three of them in the same room of the 小窝: at most once a day, one starts talking in the
        group they have with each other (made now when there is none, not too often)."""
        day = today_key(now)
        if (day, "all", "together") in self._done or self.why_not("g:together", now):
            return 0
        rooms: dict[str, list[tuple[BotConfig, dict[str, str]]]] = {}
        for bot_id, blocks in plan.items():
            bot, block = self.brain.bots.get(bot_id), block_at(blocks, now)
            if bot is None or bot.mode != "live" or not bot.provider_id or block is None:
                continue
            if block["room"] != BEDROOM:
                rooms.setdefault(block["room"], []).append((bot, block))
        found = [(room, people[:3]) for room, people in rooms.items() if len(people) >= 2]
        if not found or self.chance() >= self.config.together_chance:
            return 0
        room, people = found[int(self.chance() * len(found)) % len(found)]
        ids = {bot.id for bot, _ in people}
        groups = await self.groups.groups()
        group = next((g for g in groups if {m["bot_id"] for m in g["members"]} == ids), None)
        if group is None:
            theirs = [g["created_at"] for g in groups if g["created_by"] != "user"]
            if theirs and now - max(theirs) < self.config.together_gap:
                return 0
        self._done.add((day, "all", "together"))
        (bot, block), *others = people
        if group is None:
            name = "、".join(b.name for b, _ in people)
            try:
                group = await self.groups.create(
                    name if len(name) <= 20 else f"{room}小群", sorted(ids), created_by=bot.id
                )
            except GroupError as exc:
                log.info("她们的小群没建成：%s", exc)
                return 0
        bot = self.brain.bots.get(bot.id, bot)  # as reloaded with the group
        view = next((m["channel_id"] for m in group["members"] if m["bot_id"] == bot.id), None)
        channel = bot.channels.get(view) if view is not None else None
        if channel is None:
            return 0
        rows = await self.brain.store.recent(channel.channel_id, limit=CONTEXT)
        ask = TOGETHER_ASK.format(
            others="、".join(b.name for b, _ in others),
            room=room,
            doing=block["doing"],
            name=group["name"],
            life=await self.life.line(bot),
            talk=self._talk(bot, rows),
        )
        line = await self._write(bot, self.brain._persona(bot, channel), ask)
        if not line:
            await self._save(now)
            return 0
        shown = await self.desk.deliver(bot, channel, [Segment("text", {"text": line})], kind=KIND)
        if shown.state != "succeeded":
            return 0
        self._sent.append(("g:together", now, channel.channel_id, "together"))
        await self._save(now)
        log.info("%s 在「%s」里起了个头", bot.name, group["name"])
        return 1


def from_outreach(message_id: Any) -> bool:
    return isinstance(message_id, str) and message_id.startswith(f"{APP}{KIND}")
