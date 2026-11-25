"""小窝和她的日子: the companions live in a little house and have days of their own.

Cheap and consistent rather than simulated: once a day (from 5 in the morning, or the first time it is
needed) one model call plans the whole house's day together, so that two of them can do something together
("下午阿鲸和阿马在客厅打游戏"). Each companion has a life profile (when she gets up, the rooms she likes, what
she is into lately…), drafted from her persona once and editable. What she is doing now goes into her
prompt in the chats with the user (private, desk, 桌面群; never a QQ group: 群里行为不变), and the small
things she has told the user are kept (life_moments) and shown to the next day's planning, so the story
goes on.

All of it is off until the user turns 「她们的日子」 on (setting "life"); then it costs one call a day, plus
one per profile drafted.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from nest.llm.base import ChatMessage, ProviderError
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotConfig
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

SETTING = "life"  # {"on": bool}
ROOMS_SETTING = "life_rooms"  # room -> its furniture, walls and floor, as the user wants them (the 3D 小窝)
BEDROOM_OF = "卧室:"  # and whose it is
SHARED_ROOMS = ("客厅", "厨房", "书房", "阳台")
BEDROOM = "卧室"  # each companion's own
ROOMS = (*SHARED_ROOMS, BEDROOM)
# How she is seen in the 小窝 (web/src/lib/home-stage.ts): what the plan says, else guessed from her doing.
POSES = {"站": "stand", "坐": "sit", "躺": "lie"}
LYING = re.compile(r"赖床|躺|睡|眯|午休|小憩|打盹|被窝")
UP = re.compile(r"起床|起身|叠被|梳|洗漱|收拾")
COOKING = re.compile(r"做.{0,2}饭|做早餐|煮|炒|煎|烤|炖|蒸|切|洗|揉面|下厨")
SITTING = re.compile(
    r"坐|靠|沙发|忙自己|书|读|写|画|题|复习|剧|番|电影|电视|游戏|棋|牌|吃|喝|茶|咖啡|聊|电脑|代码|消息|织|缝|琴|吉他|歇|"
    r"发呆|拼图|手账|日记|整理.{0,4}(清单|笔记|资料)"
)
WEEKDAYS = "一二三四五六日"
PLAN_FROM_HOUR = 5
LOOP_SECONDS = 300
PLAN_TOKENS = 3000
DRAFT_TOKENS = 600
MOMENTS_SHOWN = 8
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DEFAULT_PROFILE: dict[str, Any] = {
    "wake": "07:30",
    "sleep": "23:30",
    "rooms": ["客厅"],
    "hobbies": [],
    "lately": "",
    "habits": "",
    "friends": "",
    "home": True,  # lives in the house
    "since": "",  # the day you first talked, YYYY-MM-DD ("": as far as nest knows)
}
MILESTONES = (7, 30, 100, 200, 300, 500)  # days since, and every thousand
# Days everyone knows. Lunar ones for the years nest will see first.
FESTIVALS = {
    "01-01": "元旦",
    "02-14": "情人节",
    "05-01": "劳动节",
    "06-01": "儿童节",
    "10-01": "国庆节",
    "10-31": "万圣节",
    "12-24": "平安夜",
    "12-25": "圣诞节",
    "2026-02-17": "春节",
    "2026-03-03": "元宵节",
    "2026-06-19": "端午节",
    "2026-08-19": "七夕",
    "2026-09-25": "中秋节",
    "2027-02-06": "春节",
    "2027-02-20": "元宵节",
    "2027-06-09": "端午节",
    "2027-08-08": "七夕",
    "2027-09-15": "中秋节",
}

PLAN_SYSTEM = (
    "你是一个温柔的生活编剧，给住在同一个小窝里的几个伙伴排今天一天的日常。她们是对方（用户）的伙伴，"
    "各有各的性格和小习惯。日常要像真的：有赖床、做饭、忙自己的事、发呆、偶尔一起玩，也有一点小插曲"
    "（蛋糕烤糊了、看到很美的晚霞）。不编造新闻、天气这类现实世界的事实。"
)
PLAN_ASK = """今天是 {date}（星期{weekday}）{festival}。

小窝里的房间：客厅、厨房、书房、阳台，还有每个人自己的卧室（写「卧室」就是她自己的）。

住在这里的伙伴：
{people}

{yesterday}{moments}给每个人排今天的日程：每人 6 到 10 段，从起床排到睡觉，时间不重叠；
偶尔让两三个人同一时间在同一个房间一起做事；
每人挑一两段写 share：一件可以告诉对方的小事（一句话，像她会说的，没有就写空字符串）；
pose 写这一段她多半是「站」「坐」还是「躺」。

只输出 JSON，不要别的：
{{"<名字>": [{{"from": "07:30", "to": "08:10", "room": "卧室", "doing": "赖床", "mood": "迷迷糊糊",
"pose": "躺", "share": ""}}, …], …}}"""
DRAFT_ASK = """下面是一个伙伴的人设。按她的性格，替她写一份小窝里的生活设定。

人设：
{persona}

只输出 JSON：{{"wake": "起床时间 HH:MM", "sleep": "睡觉时间 HH:MM",
"rooms": ["她爱待的房间，从客厅、厨房、书房、阳台里选"], "hobbies": ["两三个爱好"],
"lately": "最近在迷的东西，一句话", "habits": "一个小习惯，一句话", "friends": "和谁要好，可以空着"}}"""


def today_key(now: float) -> str:
    return dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def festival_of(day: dt.date) -> str:
    return FESTIVALS.get(day.isoformat()) or FESTIVALS.get(day.strftime("%m-%d")) or ""


def clean_profile(value: Any) -> dict[str, Any]:
    """A profile as stored: only the known fields, sane values."""
    raw = value if isinstance(value, dict) else {}
    out = dict(DEFAULT_PROFILE)
    for key in ("wake", "sleep"):
        if isinstance(raw.get(key), str) and TIME.match(raw[key].strip()):
            hour, minute = raw[key].strip().split(":")
            out[key] = f"{int(hour):02d}:{minute}"
    for key in ("rooms", "hobbies"):
        if isinstance(raw.get(key), list):
            items = [str(x).strip()[:20] for x in raw[key] if str(x).strip()]
            out[key] = [r for r in items if key != "rooms" or r in SHARED_ROOMS][:5]
    for key in ("lately", "habits", "friends"):
        if isinstance(raw.get(key), str):
            out[key] = raw[key].strip()[:80]
    if isinstance(raw.get("home"), bool):
        out["home"] = raw["home"]
    if isinstance(raw.get("since"), str):
        try:
            out["since"] = dt.date.fromisoformat(raw["since"].strip()).isoformat()
        except ValueError:
            out["since"] = ""
    return out


def milestone_of(since: dt.date, today: dt.date) -> str:
    """「认识 100 天」「认识 2 周年」 on the day itself; "" on any other."""
    days = (today - since).days
    years = today.year - since.year
    leap = (since.month, since.day) == (2, 29) and (today.month, today.day) == (3, 1)
    if years > 0 and ((today.month, today.day) == (since.month, since.day) or leap):
        return f"认识 {years} 周年"
    if days in MILESTONES or (days > 0 and days % 1000 == 0):
        return f"认识 {days} 天"
    return ""


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def pose_of(room: str, doing: str, said: Any = "") -> str:
    """stand, sit or lie: as the plan says, else what she does is likely done like that."""
    for word, pose in POSES.items():
        if word in str(said or ""):
            return pose
    if room == BEDROOM and LYING.search(doing) and not UP.search(doing):
        return "lie"
    if room == "厨房" and COOKING.search(doing):
        return "stand"
    return "sit" if SITTING.search(doing) and not UP.search(doing) else "stand"


def clean_blocks(value: Any) -> list[dict[str, str]]:
    """One companion's day from the model: valid times, known rooms, in order, not overlapping."""
    blocks = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        start, end = str(item.get("from", "")).strip(), str(item.get("to", "")).strip()
        if not (TIME.match(start) and TIME.match(end)) or _minutes(end) <= _minutes(start):
            continue
        room = str(item.get("room", "")).strip()
        room = next((r for r in ROOMS if r in room), BEDROOM if "卧" in room else "客厅")
        doing = " ".join(str(item.get("doing", "")).split())[:40]
        if not doing:
            continue
        blocks.append(
            {
                "from": f"{_minutes(start) // 60:02d}:{_minutes(start) % 60:02d}",
                "to": f"{_minutes(end) // 60:02d}:{_minutes(end) % 60:02d}",
                "room": room,
                "doing": doing,
                "mood": " ".join(str(item.get("mood", "")).split())[:12],
                "pose": pose_of(room, doing, item.get("pose")),
                "share": " ".join(str(item.get("share", "")).split())[:80],
            }
        )
    blocks.sort(key=lambda b: _minutes(b["from"]))
    kept: list[dict[str, str]] = []
    for block in blocks:
        if kept and _minutes(block["from"]) < _minutes(kept[-1]["to"]):
            continue
        kept.append(block)
    return kept[:12]


def default_day(profile: Mapping[str, Any]) -> list[dict[str, str]]:
    """A plain day from the profile alone, when no model could plan one."""
    wake, sleep = _minutes(profile["wake"]), _minutes(profile["sleep"])
    favourite = (profile.get("rooms") or ["客厅"])[0]
    hobby = (profile.get("hobbies") or ["忙自己的事"])[0]

    def at(m: int) -> str:
        m = max(0, min(m, 23 * 60 + 59))
        return f"{m // 60:02d}:{m % 60:02d}"

    steps = [
        (wake, wake + 30, BEDROOM, "起床，收拾自己", "迷迷糊糊"),
        (wake + 30, wake + 70, "厨房", "做早饭、吃早饭", "还行"),
        (wake + 70, 12 * 60, favourite, hobby, "专心"),
        (12 * 60, 13 * 60, "厨房", "吃午饭", "满足"),
        (13 * 60, 17 * 60, favourite, "忙自己的事", "平静"),
        (17 * 60 + 30, 18 * 60 + 30, "厨房", "做晚饭、吃晚饭", "开心"),
        (19 * 60, sleep - 30, "客厅", "看看剧、发发呆", "放松"),
        (sleep - 30, sleep, BEDROOM, "洗漱，准备睡觉", "困了"),
    ]
    return [
        {"from": at(a), "to": at(b), "room": r, "doing": d, "mood": mood, "share": ""}
        for a, b, r, d, mood in steps
        if b > a
    ]


def block_at(blocks: list[dict[str, str]], now: float) -> dict[str, str] | None:
    """What she is doing at `now`; asleep (a bedroom block) outside her day."""
    moment = dt.datetime.fromtimestamp(now)
    minute = moment.hour * 60 + moment.minute
    for block in blocks:
        if _minutes(block["from"]) <= minute < _minutes(block["to"]):
            return block
    if blocks and (minute >= _minutes(blocks[-1]["to"]) or minute < _minutes(blocks[0]["from"])):
        return {"from": "", "to": "", "room": BEDROOM, "doing": "睡着了", "mood": "", "share": ""}
    if blocks:  # between two blocks: on her way, at the next one
        later = [b for b in blocks if _minutes(b["from"]) > minute]
        return later[0] if later else blocks[-1]
    return None


def _posed(block: dict[str, str] | None) -> dict[str, str] | None:
    """With her pose, also when the plan has none (one from before poses, the plain day, asleep)."""
    if block is None or block.get("pose"):
        return block
    return block | {"pose": pose_of(block["room"], block["doing"])}


def parse_plan(text: str, names: Mapping[str, str]) -> dict[str, list[dict[str, str]]]:
    """The model's JSON plan, by bot id (`names`: name -> bot id). Missing ones are left out."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    plan = {}
    for name, blocks in data.items() if isinstance(data, dict) else []:
        bot_id = names.get(str(name).strip())
        cleaned = clean_blocks(blocks)
        if bot_id and cleaned:
            plan[bot_id] = cleaned
    return plan


class LifeKeeper:
    def __init__(
        self,
        db: Database,
        brain: Any,
        broadcaster: Any,
        bots: Callable[[], Mapping[str, BotConfig]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.brain = brain  # nest.core.brain.Brain: its models (_complete) and personas
        self.broadcaster = broadcaster
        self.bots = bots
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._planning = asyncio.Lock()
        self._furnishing = asyncio.Lock()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="life")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                if await self.on() and dt.datetime.fromtimestamp(self.clock()).hour >= PLAN_FROM_HOUR:
                    await self.today()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("排小窝的日程出错")
            await asyncio.sleep(LOOP_SECONDS)

    def _changed(self) -> None:
        self.broadcaster.publish("bots", {"life": True})  # the desk and the 小窝 page refetch

    # -- the switch and the profiles ----------------------------------------------------------------

    async def on(self) -> bool:
        stored = (await SettingsRepo(self.db).all()).get(SETTING) or {}
        return bool(stored.get("on")) if isinstance(stored, dict) else False

    async def set_on(self, on: bool) -> None:
        await SettingsRepo(self.db).set_many({SETTING: {"on": on}})
        self._changed()

    async def profiles(self) -> dict[str, dict[str, Any]]:
        rows = await self.db.fetchall("SELECT bot_id, profile FROM life_profiles")
        stored = {r["bot_id"]: clean_profile(json.loads(r["profile"])) for r in rows}
        return {bot_id: stored.get(bot_id, dict(DEFAULT_PROFILE)) for bot_id in self.bots()}

    async def set_profile(self, bot_id: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = clean_profile(dict(profile))
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO life_profiles (bot_id, profile, updated_at) VALUES (?, ?, ?)",
                (bot_id, json.dumps(cleaned, ensure_ascii=False), self.clock()),
            )
        self._changed()
        return cleaned

    async def since(self, bot_id: str, profile: Mapping[str, Any] | None = None) -> str:
        """The day the user first talked with her (YYYY-MM-DD): as set in her profile, else the earliest nest
        knows of (a message in her private chats, or the day she was added); "" when there is neither."""
        if profile is None:
            profile = (await self.profiles()).get(bot_id, DEFAULT_PROFILE)
        if profile.get("since"):
            return str(profile["since"])
        row = await self.db.fetchone(
            """SELECT min(at) AS at FROM (
                   SELECT min(m.sent_at) AS at FROM messages m
                       JOIN bot_channels bc ON bc.channel_id = m.channel_id AND bc.bot_id = ?
                       JOIN channels c ON c.id = m.channel_id AND c.kind = 'private'
                   UNION ALL SELECT created_at FROM bots WHERE id = ?)""",
            (bot_id, bot_id),
        )
        at = row["at"] if row else None
        return dt.datetime.fromtimestamp(at).date().isoformat() if at else ""

    async def milestone(self, bot_id: str) -> str:
        """「认识 100 天」 when today is such a day for her and the user, else ""."""
        since = await self.since(bot_id)
        if not since:
            return ""
        return milestone_of(dt.date.fromisoformat(since), dt.date.fromtimestamp(self.clock()))

    def residents(self, profiles: Mapping[str, Mapping[str, Any]]) -> list[BotConfig]:
        """Who lives in the house: the companions that talk (live, with a model) and have not moved out."""
        return [
            bot
            for bot in self.bots().values()
            if bot.mode == "live"
            and bot.provider_id
            and profiles.get(bot.id, DEFAULT_PROFILE).get("home", True)
        ]

    def _persona(self, bot: BotConfig) -> str:
        """Her persona as she has it with the user (her first private chat's), else any."""
        chats = [c for c in bot.channels.values() if c.channel.kind == "private" and c.persona]
        chosen = chats or [c for c in bot.channels.values() if c.persona]
        return chosen[0].persona if chosen else ""

    @for_purpose("life")
    async def draft(self, bot_id: str) -> dict[str, Any]:
        """Her profile, written by her own model from her persona; the default when that fails."""
        bot = self.bots().get(bot_id)
        if bot is None:
            raise KeyError(bot_id)
        persona = self._persona(bot).strip() or f"你是{bot.name}。"
        messages = [
            ChatMessage("system", "你替伙伴写生活设定，只输出 JSON。"),
            ChatMessage("user", DRAFT_ASK.format(persona=persona[:1500])),
        ]
        try:
            completion, _, _ = await self.brain._complete(bot, messages, max_tokens=DRAFT_TOKENS)
            start, end = completion.text.find("{"), completion.text.rfind("}")
            drafted = json.loads(completion.text[start : end + 1]) if 0 <= start < end else {}
        except (ProviderUnavailable, ProviderError, json.JSONDecodeError, ValueError) as exc:
            log.info("%s 的生活设定没写成（%s），用默认的", bot.name, exc)
            drafted = {}
        return await self.set_profile(bot_id, clean_profile(drafted))

    # -- the days -------------------------------------------------------------------------------------

    async def plan_of(self, day: str) -> dict[str, list[dict[str, str]]] | None:
        row = await self.db.fetchone("SELECT plan FROM life_days WHERE day = ?", (day,))
        return json.loads(row["plan"]) if row else None

    async def today(self, *, again: bool = False) -> dict[str, list[dict[str, str]]]:
        """Today's plan, planned now if there is none yet (or `again`)."""
        day = today_key(self.clock())
        async with self._planning:
            if not again and (found := await self.plan_of(day)) is not None:
                return found
            plan, made_by = await self._plan(day)
            async with self.db.transaction() as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO life_days (day, plan, made_by, created_at) VALUES (?, ?, ?, ?)",
                    (day, json.dumps(plan, ensure_ascii=False), made_by, self.clock()),
                )
        self._changed()
        return plan

    @for_purpose("life")
    async def _plan(self, day: str) -> tuple[dict[str, list[dict[str, str]]], str]:
        profiles = await self.profiles()
        residents = self.residents(profiles)
        if not residents:
            return {}, "default"
        date = dt.date.fromisoformat(day)
        festival = festival_of(date)
        marked = {b.id: await self.milestone(b.id) for b in residents}
        people = "\n".join(
            f"- {b.name}：{self._persona(b)[:200].strip() or '（没有人设）'}\n  生活设定："
            + json.dumps(
                {k: v for k, v in profiles[b.id].items() if k not in ("home", "since")}, ensure_ascii=False
            )
            + (f"\n  今天是她和对方{marked[b.id]}的日子。" if marked[b.id] else "")
            for b in residents
        )
        yesterday_plan = await self.plan_of((date - dt.timedelta(days=1)).isoformat()) or {}
        yesterday = ""
        if yesterday_plan:
            lines = [
                f"- {b.name}：" + "；".join(x["doing"] for x in yesterday_plan.get(b.id, []))
                for b in residents
                if yesterday_plan.get(b.id)
            ]
            yesterday = (
                "昨天她们做了这些（事情可以接着发展）：\n" + "\n".join(lines) + "\n\n" if lines else ""
            )
        told = await self.moments(limit=MOMENTS_SHOWN)
        moments = (
            "她们最近跟对方说过的自己的事（不要和这些矛盾）：\n"
            + "\n".join(
                f"- {self.bots()[m['bot_id']].name}：{m['text']}" for m in told if m["bot_id"] in self.bots()
            )
            + "\n\n"
            if told
            else ""
        )
        ask = PLAN_ASK.format(
            date=f"{date.month}月{date.day}日",
            weekday=WEEKDAYS[date.weekday()],
            festival=f"，{festival}" if festival else "",
            people=people,
            yesterday=yesterday,
            moments=moments,
        )
        planner = residents[0]
        plan: dict[str, list[dict[str, str]]] = {}
        try:
            completion, _, _ = await self.brain._complete(
                planner,
                [ChatMessage("system", PLAN_SYSTEM), ChatMessage("user", ask)],
                max_tokens=PLAN_TOKENS,
            )
            plan = parse_plan(completion.text, {b.name: b.id for b in residents})
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("小窝的日程没排成（%s），用默认的", exc)
        made_by = planner.id if plan else "default"
        for bot in residents:
            plan.setdefault(bot.id, default_day(profiles[bot.id]))
        return plan, made_by

    async def now_of(self, bot_id: str) -> dict[str, str] | None:
        """What she is doing now, if 她们的日子 is on and she lives in the house."""
        if not await self.on():
            return None
        plan = await self.plan_of(today_key(self.clock()))
        if not plan or bot_id not in plan:
            return None
        return block_at(plan[bot_id], self.clock())

    async def line(self, bot: BotConfig) -> str:
        """For her prompt in the chats with the user: where she is and what her day is like."""
        if not await self.on():
            return ""
        plan = await self.plan_of(today_key(self.clock()))
        if not plan or bot.id not in plan:
            return ""
        now = block_at(plan[bot.id], self.clock())
        day = " → ".join(f"{b['from']} {b['room']}{b['doing']}" for b in plan[bot.id])
        mood = f"，心情{now['mood']}" if now and now.get("mood") else ""
        here = f"你现在在{now['room']}{now['doing']}{mood}。" if now else ""
        told = [m["text"] for m in await self.moments(bot.id, limit=3)]
        label = await self.milestone(bot.id)
        here += f"今天是你和对方{label}的日子。" if label else ""
        said = f"你最近跟对方说过：{'；'.join(told)}。" if told else ""
        return (
            f"（你在小窝里的生活：{here}今天：{day}。{said}"
            "自然地提到就好，不要每句都说；这是你和对方之间的小日子，不要当成现实世界的事去做。）"
        )

    async def moments(self, bot_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT * FROM life_moments WHERE (? IS NULL OR bot_id = ?)
               ORDER BY at DESC, id DESC LIMIT ?""",
            (bot_id, bot_id, limit),
        )
        return [dict(r) for r in rows]

    async def remember(self, bot_id: str, text: str, source: str) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO life_moments (bot_id, at, text, source) VALUES (?, ?, ?, ?)",
                (bot_id, self.clock(), text[:200], source),
            )
        self._changed()

    async def forget(self, moment_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM life_moments WHERE id = ?", (moment_id,))
        self._changed()

    def is_room(self, room: str) -> bool:
        return room in SHARED_ROOMS or (
            room.startswith(BEDROOM_OF) and room.removeprefix(BEDROOM_OF) in self.bots()
        )

    async def furnished(self) -> dict[str, Any]:
        value = (await SettingsRepo(self.db).all()).get(ROOMS_SETTING)
        rooms = value if isinstance(value, dict) else {}
        return {room: wanted for room, wanted in rooms.items() if self.is_room(room)}

    async def furnish(self, room: str, wanted: dict[str, Any] | None) -> None:
        async with self._furnishing:
            rooms = await self.furnished()
            rooms.pop(room, None)
            if wanted is not None:
                rooms[room] = wanted
            await SettingsRepo(self.db).set_many({ROOMS_SETTING: rooms})
        self._changed()

    async def view(self) -> dict[str, Any]:
        """What the 小窝 page shows."""
        on = await self.on()
        profiles = await self.profiles()
        day = today_key(self.clock())
        plan = (await self.plan_of(day)) or {}
        bots = self.bots()
        people = []
        for bot in bots.values():
            blocks = plan.get(bot.id, [])
            people.append(
                {
                    "bot_id": bot.id,
                    "name": bot.name,
                    "home": profiles[bot.id].get("home", True)
                    and bot.mode == "live"
                    and bool(bot.provider_id),
                    "profile": profiles[bot.id],
                    "since": await self.since(bot.id, profiles[bot.id]),
                    "milestone": await self.milestone(bot.id),
                    "day": blocks,
                    "now": _posed(block_at(blocks, self.clock())) if on else None,
                }
            )
        return {
            "on": on,
            "day": day,
            "planned": bool(plan),
            "rooms": list(SHARED_ROOMS),
            "people": people,
            "moments": [m for m in await self.moments(limit=30) if m["bot_id"] in bots],
        }
