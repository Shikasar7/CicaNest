"""问题记录: each group's 记录官 (one bot per group, chosen on the Bot page) writes down what the AI bots
could not solve there, and feedback about them, for the console's 「问题记录」 page and a daily digest.

About once an hour the 记录官's model reads the group's new messages (with a few earlier ones for
context) and lists what is worth keeping: questions the bots did not answer or got wrong, bugs, wished-for
features, other feedback, and anything someone asked it to write down. Each issue keeps the message it
starts from: who said it, when, and the words. A failed call leaves the messages to be read next time.

At each set time (「问题记录」页, 21:00 by default) the new issues go by private message to the set QQ
number, from a 记录官 that is online (else any bot that is; one that is 假在线, nest.core.deaf, does not
count as online). No QQ number set: nothing is sent; nothing new: nothing is sent either. A time missed
by more than half an hour (nest was down) is skipped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nest.adapters.base import parse_recipient
from nest.adapters.registry import can, place_of
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotChannel, BotConfig
from nest.storage.db import Database
from nest.storage.issues import KINDS, IssueRepo
from nest.storage.messages import MessageStore

log = logging.getLogger(__name__)

CHECK_SECONDS = 60
REVIEW_SECONDS = 3600  # how often a 记录官 reads its group
BATCH = 200  # messages per model call
CHUNKS = 3  # calls per look at most; a busier group is caught up next time
CONTEXT = 10  # earlier messages shown, already read
FIRST_LOOK = 86400.0  # a new 记录官 starts with the last day
KNOWN_DAYS = 3  # issues of this many days are shown so they are not written down twice
REVIEW_TOKENS = 1500
TEXT_CHARS = 300
SUMMARY_CHARS = 60
REPLY_CHARS = 40
QUOTE_CHARS = 500
DIGEST_GRACE = 1800.0
DEFAULT_TIMES = ("21:00",)
MAX_TIMES = 6
SHOWN = 15

KIND_LABEL = {
    "unsolved": "没解决",
    "wrong": "答错了",
    "bug": "故障",
    "request": "想要的功能",
    "feedback": "意见",
}
RECORDER_HINT = (
    "你也是这个群的问题记录官：有人让你记下问题、反馈或建议时，简短答应一声就行，系统会自动记下来，"
    "不用复述整理，也不要说记不了。"
)
REVIEW_RULES = (
    "你是 {place}「{group}」的问题记录官。群里有几个 AI bot（{bots}）陪大家聊天、回答问题。"
    "请从「新消息」里找出值得记下来给维护者看的事，只输出 JSON，不要别的。\n"
    "要记的：\n"
    "unsolved：有人问了正经问题或求助，bot 没回答、说不知道、拿不准，或者没帮上忙，后来也没人解决；\n"
    "wrong：bot 答错了、答非所问，被人指出或明显不对；\n"
    "bug：有人说 bot 或相关软件出了故障、报错、行为异常；\n"
    "request：有人想要新功能，或提出改进建议；\n"
    "feedback：对 bot 的其他意见、抱怨或表扬，值得维护者知道的。\n"
    "有人明确让记录官记下某件事（比如「记一下」「反馈一下」），一定要记。\n"
    "不要记：闲聊、玩笑、后来已经解决了的问题、人和人之间的私事；「之前的消息」里的事；"
    "「已经记过的」里的同一件事。\n"
    '输出格式：{{"issues": [{{"message_id": 问题开始那条消息的编号, '
    '"kind": "unsolved|wrong|bug|request|feedback", '
    '"summary": "一句话说清是什么，40 字以内", "ai_reply": "bot 当时怎么回的，20 字以内，没回就空着"}}]}}；'
    '没有要记的就输出 {{"issues": []}}。'
)

# (QQ number to send the digest to, "" = off; the times of day, "HH:MM")
SettingsReader = Callable[[], Awaitable[tuple[str, list[str]]]]


def render(row: dict[str, Any], bots: set[str]) -> str:
    stamp = time.strftime("%m-%d %H:%M", time.localtime(row["sent_at"]))
    who = row["sender_name"] or row["sender_id"]
    if row.get("from_self") or row["sender_id"] in bots:
        who += "（bot）"
    text = " ".join(str(row.get("text") or "").split())[:TEXT_CHARS] or "[非文本]"
    return f"[{row['id']}] {stamp} {who}：{text}"


def build_review_prompt(
    *,
    group: str,
    bot_names: list[str],
    context: list[dict[str, Any]],
    batch: list[dict[str, Any]],
    known: list[dict[str, Any]],
    place: str = "QQ 群",
    bots: set[str],
) -> list[ChatMessage]:
    parts = []
    if known:
        parts.append("已经记过的：\n" + "\n".join(f"- {issue['summary']}" for issue in known))
    if context:
        parts.append("之前的消息（只是背景）：\n" + "\n".join(render(row, bots) for row in context))
    parts.append("新消息：\n" + "\n".join(render(row, bots) for row in batch))
    system = REVIEW_RULES.format(place=place, group=group, bots="、".join(bot_names) or "无")
    return [ChatMessage("system", system), ChatMessage("user", "\n\n".join(parts))]


def parse_issues(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有 JSON")
    data = json.loads(cleaned[start : end + 1])
    issues = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        raise ValueError("没有 issues")
    return [item for item in issues if isinstance(item, dict)]


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def digest_text(issues: list[dict[str, Any]]) -> str:
    lines = [f"nest 问题记录：上次汇总以来记下 {len(issues)} 条"]
    for n, issue in enumerate(issues[:SHOWN], start=1):
        stamp = time.strftime("%m-%d %H:%M", time.localtime(issue["said_at"]))
        group = issue["group_name"] or f"群 {issue['group_id']}"
        reply = f"（bot 回：{issue['ai_reply']}）" if issue["ai_reply"] else ""
        label = KIND_LABEL.get(issue["kind"], issue["kind"])
        lines.append(
            f"{n}. 【{label}】{stamp} {group}，{issue['speaker'] or '有人'}：{issue['summary']}{reply}"
        )
    if len(issues) > SHOWN:
        lines.append(f"……还有 {len(issues) - SHOWN} 条")
    lines.append("完整记录在控制台「问题记录」页。")
    return "\n".join(lines)


def due_slots(times: list[str], now: float, done: set[str]) -> list[str]:
    """The set times of today (as "YYYY-MM-DD HH:MM") that have come within DIGEST_GRACE and not run."""
    today = time.localtime(now)
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
    due = []
    for at in times:
        hours, minutes = (int(part) for part in at.split(":"))
        slot = midnight + hours * 3600 + minutes * 60
        key = time.strftime("%Y-%m-%d ", today) + at
        if slot <= now < slot + DIGEST_GRACE and key not in done:
            due.append(key)
    return due


class IssueKeeper:
    def __init__(
        self,
        db: Database,
        store: MessageStore,
        brain: Any,
        hub: Any,
        broadcaster: Any,
        read_settings: SettingsReader,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = IssueRepo(db)
        self._store = store
        self._brain = brain
        self._hub = hub
        self._broadcaster = broadcaster
        self._read_settings = read_settings
        self._clock = clock
        self._lock = asyncio.Lock()  # one look at the groups at a time (the hourly one, 「立即整理」)
        self._done: set[str] = set()  # digest times that ran today
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="issues")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def recorders(self) -> list[tuple[BotConfig, BotChannel]]:
        return [
            (bot, channel)
            for bot in self._brain.bots.values()
            for channel in bot.channels.values()
            if channel.recorder and channel.channel.kind == "group" and can(bot.account_id, "sees_all")
        ]

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                await self.tick()
            except Exception:
                log.exception("问题记录出错")

    async def tick(self) -> None:
        now = self._clock()
        _, times = await self._read_settings()
        due = due_slots(times, now, self._done)
        if due:
            self._done = {key for key in self._done if key[:10] == due[0][:10]} | set(due)
            await self.send_digest()
            return
        for bot, channel in self.recorders():
            mark = await self.repo.read_to(channel.channel_id)
            if mark is None or now - mark[1] >= REVIEW_SECONDS:
                await self.review(bot, channel)

    async def review_all(self) -> int:
        added = 0
        for bot, channel in self.recorders():
            added += await self.review(bot, channel)
        return added

    async def review(self, bot: BotConfig, channel: BotChannel) -> int:
        """Read the group's new messages; returns how many issues were written down."""
        async with self._lock:
            added = 0
            for _ in range(CHUNKS):
                found, more = await self._review_batch(bot, channel)
                added += found
                if not more:
                    break
        if added:
            self._broadcaster.publish("issues", {"added": added})
        return added

    async def _review_batch(self, bot: BotConfig, channel: BotChannel) -> tuple[int, bool]:
        """(issues added, whether more messages are waiting)."""
        channel_id = channel.channel_id
        mark = await self.repo.read_to(channel_id)
        if mark is None:
            latest = await self._store.recent(channel_id, limit=BATCH)
            rows = [row for row in latest if row["sent_at"] > self._clock() - FIRST_LOOK]
            if not rows:
                if latest:
                    await self.repo.set_read_to(channel_id, latest[-1]["id"])
                return 0, False
        else:
            rows = await self._store.after(channel_id, mark[0], limit=BATCH)
            if not rows:
                return 0, False
        more = len(rows) == BATCH
        bots = set(self._brain.our_accounts)
        if all(row.get("from_self") or row["sender_id"] in bots for row in rows):
            await self.repo.set_read_to(channel_id, rows[-1]["id"])
            return 0, more
        provider = self._brain.providers.get(bot.provider_id or self._brain.default_provider)
        if provider is None:
            log.info("%s 没有可用的模型，这次没整理问题", bot.name)
            return 0, False
        group_id = channel.channel.id
        group = channel.name or f"群 {group_id}"
        messages = build_review_prompt(
            group=group,
            bot_names=[b.name for b in self._brain.bots.values()],
            context=await self._store.recent(channel_id, limit=CONTEXT, before=rows[0]["id"]),
            batch=rows,
            known=await self.repo.since(group_id, self._clock() - KNOWN_DAYS * 86400),
            bots=bots,
            place=place_of(channel.channel.platform)[0],
        )
        try:
            completion, _ = await provider.complete(messages, bot_id=bot.id, max_tokens=REVIEW_TOKENS)
            items = parse_issues(completion.text)
        except (ProviderError, ProviderUnavailable, ValueError) as exc:
            log.info("%s 整理「%s」的问题没成功，下次再试：%s", bot.name, group, exc)
            return 0, False
        by_id = {row["id"]: row for row in rows}
        added = 0
        for item in items:
            row = by_id.get(item.get("message_id"))  # type: ignore[arg-type]
            kind = item.get("kind")
            summary = _clip(item.get("summary"), SUMMARY_CHARS)
            if row is None or kind not in KINDS or not summary:
                continue
            issue_id = await self.repo.add(
                group_id=group_id,
                group_name=channel.name,
                bot_id=bot.id,
                message_id=row["id"],
                said_at=row["sent_at"],
                speaker=row["sender_name"] or row["sender_id"],
                speaker_id=row["sender_id"],
                kind=kind,
                summary=summary,
                quote=" ".join(str(row.get("text") or "").split())[:QUOTE_CHARS],
                ai_reply=_clip(item.get("ai_reply"), REPLY_CHARS),
            )
            added += issue_id is not None
        await self.repo.set_read_to(channel_id, rows[-1]["id"])
        if added:
            log.info("%s 在「%s」记下 %d 个问题", bot.name, group, added)
        return added, more

    async def send_digest(self) -> tuple[bool, str]:
        """Read the groups once more, then send what is new. (sent, what happened)."""
        to, _ = await self._read_settings()
        if not to:
            return False, "还没有设置接收汇总的人"
        await self.review_all()
        pending = await self.repo.unreported()
        if not pending:
            return False, "上次汇总以来没有新记下的问题，没有发"
        text = digest_text(pending)
        connected = [
            v for v in self._hub.account_views() if v["enabled"] and v["status"]["state"] == "connected"
        ]
        first = {bot.account_id for bot, _ in self.recorders()}
        platform, user = parse_recipient(to)
        for view in sorted(connected, key=lambda v: v["id"] not in first):
            if view["self_id"] == user or view["id"].partition(":")[0] != platform:
                continue
            if await self._brain.send_private(view["id"], to, text):
                await self.repo.mark_reported([issue["id"] for issue in pending])
                self._broadcaster.publish("issues", {"reported": len(pending)})
                log.info("问题汇总已私聊发给 %s，%d 条", to, len(pending))
                return True, f"已发给 {to}，共 {len(pending)} 条"
        log.warning("问题汇总没发出去：没有在线的 bot 能私聊 %s", to)
        return False, "没有在线的 bot 能私聊这个号，没发出去"
