"""Runs the 监控: a scheduler for due checks, and one run = fetch → compare → message → record → push.

- Every `tick` seconds (sooner when a check falls due or one finishes) due watches start, at most
  `concurrency` at a time; a watch is never checked twice at once (also not by 立即检查).
- Each run sets last_run_at and next_run_at = start + interval when it starts, so a slow or failed check
  never builds a backlog. After a restart, overdue watches run once, `stagger` seconds apart.
- The result (state, run row) is written in one transaction against the watch as it is then: a check whose
  address, type or selector was edited meanwhile counts for nothing, and the mode and the bot's pause are
  read at that moment.
- Only "push" mode sends, never for a paused bot, at most `hourly_push_limit` pushes an hour across all
  watches. The run row says "unknown" before the send starts, so a shutdown mid-send leaves an honest record.
- A private push the bot cannot send goes out from another connected bot of the same platform instead.
- A push to the desk is a word of hers in her chat with the user, shown in the app; it needs no connection.
- A push that failed is sent again once the bot (or, for a private push, another one) is connected, for
  `resend_hours`, every `resend_gap` (a Discord opening: `quick_resend_gap`): the entry counts as seen, so
  nothing else would ever send it. One whose result is unknown is not, it may have arrived.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from nest.adapters.registry import can, place_of
from nest.core.broadcast import Broadcaster
from nest.core.desk import NOTICE, chat_of, chosen_chats
from nest.core.events import ChannelRef, Segment, SendMessage
from nest.core.tasks import TITLE_MAX
from nest.core.tools import MATERIAL
from nest.llm.base import ChatMessage
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderRegistry
from nest.storage.bots import BotChannel, BotConfig
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo
from nest.storage.tasks import ACTIVE
from nest.storage.watches import DESK_TARGET, RunRecord, Watch, WatchStore, min_interval
from nest.watch.check import INVITE_GONE, Check, evaluate, failed, invite_api
from nest.watch.fetch import WatchError, fetch

if TYPE_CHECKING:
    from nest.core.brain import Brain
    from nest.core.hub import Hub

log = logging.getLogger(__name__)

HOUR = 3600.0
NO_PROXY = "还没有在「系统」页设置外网代理"
EDITED = "检查的时候地址、类型或选择器被改了，这次结果不算，下次重新开始"
MODEL_FAILED = "模型没写出来，用了默认格式"
PREVIEW = "试运行，没有真的发出"
PAUSED = "bot 已暂停，没有推送"
CAPPED = "这个小时推送太多了，先不发"
INTERRUPTED = "推送时 nest 停止了，不确定是否已发出"
SENT = "已推送"
RESENT = "已补发"
NO_BOT = "找不到这个 bot，没有推送"
NO_CHAT = "还没有和她的桌面聊天，没有推送"
FOUND_CHARS = 1200  # of what was found, handed to the task
REWRITE = "把下面的更新写成一条发到 {place}的简短消息，保留链接；附加要求里没说长度就不超过 150 字。"


@dataclass(frozen=True)
class WatchPolicy:
    tick: float = 15.0
    concurrency: int = 2
    stagger: float = 5.0  # after a restart, between overdue watches
    startup_delay: float = 10.0  # after a restart, before the first overdue one (QQ connects first)
    hourly_push_limit: int = 20  # all watches together
    resend_hours: float = 24.0  # a failed push older than this is left alone
    resend_gap: float = 300.0  # seconds between two tries (only tries with the bot connected count)…
    resend_tries: int = 12  # …and how many there are
    quick_resend_gap: float = 60.0  # the gap for a Discord opening, which may last only half an hour
    fetch_timeout: float = 20.0
    max_message_chars: int = 1500
    max_tokens: int = 2000


class Busy(Exception):
    """This watch is being checked right now."""


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _detail(check: Check, notes: list[str | None]) -> str:
    head = "；".join([check.summary, *(note for note in notes if note)])
    return "\n".join([head, *check.lines])


class Watcher:
    def __init__(
        self,
        db: Database,
        *,
        brain: Brain,
        hub: Hub,
        providers: ProviderRegistry,
        broadcaster: Broadcaster,
        policy: WatchPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = WatchStore(db)
        self.settings = SettingsRepo(db)
        self.brain = brain
        self.hub = hub
        self.providers = providers
        self.broadcaster = broadcaster
        self.policy = policy or WatchPolicy()
        self.transport = transport  # tests: an httpx.MockTransport; None: the network
        self.fetch = fetch
        self._running: dict[int, asyncio.Task[dict[str, Any] | None]] = {}
        self._pushes: list[float] = []  # when the pushes of the last hour went out
        self._resends: dict[int, tuple[int, float]] = {}  # run id -> (tries so far, when the last one was)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle ---------------------------------------------------------------------------------

    async def start(self) -> None:
        now = time.time()
        self._pushes = await self.store.pushes_since(now - HOUR)
        # Missed checks are not caught up one by one: each overdue watch runs once, spread out.
        for index, watch in enumerate(await self.store.due(now)):
            at = now + self.policy.startup_delay + index * self.policy.stagger
            await self.store.schedule(watch.id, at)
        self._task = asyncio.create_task(self._loop(), name="watcher")

    async def stop(self) -> None:
        tasks = [task for task in (self._task, *self._running.values()) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None

    def wake(self) -> None:
        """Look at the schedule now (a watch was added, enabled or rescheduled)."""
        self._wake.set()

    def running(self, watch_id: int) -> bool:
        return watch_id in self._running

    def run_now(self, watch: Watch) -> asyncio.Task[dict[str, Any] | None]:
        """Check one watch now, whatever its schedule or switch. Raises Busy while it is being checked."""
        if watch.id in self._running:
            raise Busy(watch.id)
        return self._launch(watch)

    # -- the schedule --------------------------------------------------------------------------------

    def _launch(self, watch: Watch) -> asyncio.Task[dict[str, Any] | None]:
        task = asyncio.create_task(self._run(watch), name=f"watch:{watch.id}")
        self._running[watch.id] = task
        task.add_done_callback(partial(self._finished, watch.id))
        return task

    def _finished(self, watch_id: int, task: asyncio.Task[dict[str, Any] | None]) -> None:
        if self._running.get(watch_id) is task:
            del self._running[watch_id]
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("监控 %d 的检查出错：%s", watch_id, type(exc).__name__, exc_info=exc)
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            self._wake.clear()
            timeout = self.policy.tick
            try:
                now = time.time()
                for watch in await self.store.due(now):
                    if len(self._running) >= self.policy.concurrency:
                        break  # the next one starts when a running check finishes (it wakes the loop)
                    if watch.id not in self._running:
                        self._launch(watch)
                upcoming = await self.store.next_due(now)
                if upcoming is not None:
                    timeout = min(timeout, max(upcoming - time.time(), 0.0))
                await self._resend()
            except Exception:
                log.exception("读取监控计划失败")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout)

    # -- one run ---------------------------------------------------------------------------------------

    async def _check(self, watch: Watch) -> Check:
        try:
            proxy = None
            if watch.use_proxy:
                proxy = await self.settings.fetch_proxy()
                if not proxy:
                    return failed(NO_PROXY)
            url = invite_api(watch.url) if watch.kind == "discord" else watch.url
            try:
                page = await self.fetch(
                    url, proxy=proxy, timeout=self.policy.fetch_timeout, transport=self.transport
                )
            except WatchError as exc:
                if watch.kind == "discord" and exc.status == 404:
                    return failed(INVITE_GONE)
                raise
            # Parsing a big page or feed takes a moment: never on the loop the bots answer on.
            return await asyncio.to_thread(evaluate, watch, page)
        except WatchError as exc:
            return failed(str(exc))
        except Exception as exc:  # a parser bug must not stop the schedule
            log.exception("监控「%s」检查出错", watch.name)
            return failed(f"检查出错（{type(exc).__name__}）")

    @for_purpose("watch")
    async def _rewrite(self, watch: Watch, bot: BotConfig, text: str) -> str | None:
        """The model's version of the message, or None (the template is used)."""
        runtime = self.providers.get(watch.provider_id)
        if runtime is None:
            return None
        place = place_of(bot.account_id.partition(":")[0])
        if watch.target_kind == "group":
            persona = next(
                (
                    channel.persona
                    for channel in bot.channels.values()
                    if channel.channel.kind == "group" and channel.channel.id == watch.target_id
                ),
                "",
            )
            system = [persona.strip() or f"你是群里的{bot.name}。", REWRITE.format(place=place[0])]
        elif watch.target_kind == DESK_TARGET:
            chat = await self._chat(bot)
            persona = chat.persona if chat is not None else ""
            system = [persona.strip() or f"你是{bot.name}。", REWRITE.format(place="你们的桌面聊天")]
        else:
            system = [f"你是{bot.name}。", REWRITE.format(place=place[1])]
        if watch.instructions.strip():
            system.append(watch.instructions.strip())
        messages = [ChatMessage("system", "\n".join(system)), ChatMessage("user", text)]
        try:
            completion, _ = await runtime.complete(messages, bot_id=bot.id, max_tokens=self.policy.max_tokens)
        except Exception as exc:  # unavailable, a failed call, anything: the template will do
            log.info("监控「%s」的模型没有写出消息（%s），用默认格式", watch.name, type(exc).__name__)
            return None
        return completion.text.strip() or None

    def _reserve(self) -> float | None:
        """Take one of this hour's pushes; None when they are used up. Synchronous: never two at once."""
        now = time.time()
        self._pushes = [at for at in self._pushes if at > now - HOUR]
        if len(self._pushes) >= self.policy.hourly_push_limit:
            return None
        self._pushes.append(now)
        return now

    def _release(self, at: float) -> None:
        with contextlib.suppress(ValueError):
            self._pushes.remove(at)

    def _stand_ins(self, account_id: str) -> list[tuple[str, str]]:
        """(account id, bot name) of the other bots that could pass on a private push for this account: same
        platform, able to message first, connected, not paused (全部暂停 stops them too)."""
        platform = account_id.partition(":")[0]
        return [
            (bot.account_id, bot.name)
            for bot in self.brain.bots.values()
            if bot.account_id != account_id
            and bot.account_id.partition(":")[0] == platform
            and bot.mode != "paused"
            and can(bot.account_id, "proactive")
            and bot.account_id in self.hub.adapters
            and self.hub.status_of(bot.account_id).state == "connected"
        ]

    async def _send(self, watch: Watch, text: str, key: str) -> tuple[str, str, str | None]:
        """(delivered, note, error). A private push the watch's bot cannot send (its QQ is down, or QQ
        refused) is passed on by another connected bot of the same platform: a person is the same person to
        all of them, and a reminder like a Discord opening is no use late."""
        bot = self.brain.bots.get(watch.bot_id)
        if bot is None:
            return "failed", NO_BOT, "找不到这个 bot"
        if watch.target_kind == DESK_TARGET:
            return await self._to_desk(bot, text)
        delivered, note, error = await self._send_from(bot.account_id, watch, text, key)
        if delivered != "failed" or watch.target_kind != "private":
            return delivered, note, error
        for account_id, name in self._stand_ins(bot.account_id):
            other, other_note, other_error = await self._send_from(
                account_id, watch, text, f"{key}:{account_id}"
            )
            if other == "sent":
                return "sent", f"{SENT}（{bot.name} 发不出去，由{name}代发）", None
            if other == "unknown":  # it may have arrived: no more tries
                return other, other_note, other_error
        return delivered, note, error

    async def _chat(self, bot: BotConfig) -> BotChannel | None:
        """Her chat with the user in the app."""
        return chat_of(bot, (await chosen_chats(self.store.db)).get(bot.id))

    async def _to_desk(self, bot: BotConfig, text: str) -> tuple[str, str, str | None]:
        chat, desk = await self._chat(bot), self.brain.desk
        if chat is None or desk is None:
            return "failed", NO_CHAT, NO_CHAT
        result = await desk.deliver(bot, chat, [Segment("text", {"text": text})], kind=NOTICE)
        if result.state != "succeeded":
            return "failed", f"推送失败：{result.error}", result.error
        return "sent", SENT, None

    async def _send_from(
        self, account_id: str, watch: Watch, text: str, key: str
    ) -> tuple[str, str, str | None]:
        adapter = self.hub.adapters.get(account_id)
        if adapter is None:
            return "failed", "这个 bot 的号没有在运行，没有推送", "账号没有在运行"
        account = self.hub.accounts.get(account_id)
        platform, _, self_id = account_id.partition(":")
        platform = account.platform if account else platform
        channel = ChannelRef(platform, self_id, watch.target_kind, watch.target_id)  # type: ignore[arg-type]
        action = SendMessage(channel, (Segment("text", {"text": text}),), idempotency_key=key)
        try:
            result = await adapter.perform(action)
        except Exception as exc:
            error = f"发送时出错（{type(exc).__name__}）"
            return "unknown", f"推送结果不确定，没有重发：{error}", error
        if result.state == "succeeded":
            return "sent", SENT, None
        if result.state == "unknown":
            return "unknown", f"推送结果不确定，没有重发：{result.error}", result.error
        return "failed", f"推送失败：{result.error}", result.error

    async def _resend(self) -> None:
        """Failed pushes whose bot can send again, each as it was written."""
        now, policy = time.time(), self.policy
        for run in await self.store.undelivered(now - policy.resend_hours * HOUR):
            tries, last = self._resends.get(run["id"], (0, run["started_at"]))
            watch = await self.store.get(run["watch_id"])
            bot = self.brain.bots.get(watch.bot_id) if watch else None
            if (
                watch is None
                or bot is None
                or not watch.enabled
                or watch.mode != "push"
                or bot.mode == "paused"
                or tries >= policy.resend_tries
                or now - last < (policy.quick_resend_gap if watch.kind == "discord" else policy.resend_gap)
                or not (
                    watch.target_kind == DESK_TARGET
                    or self.hub.status_of(bot.account_id).state == "connected"
                    or (watch.target_kind == "private" and self._stand_ins(bot.account_id))
                )
            ):
                continue
            reserved = self._reserve()
            if reserved is None:
                return
            self._resends[run["id"]] = (tries + 1, now)
            key = f"watch:{watch.id}:run:{run['id']}:{tries + 1}"
            delivered, note, error = await self._send(watch, run["message"], key)
            if delivered == "failed":
                self._release(reserved)
                continue
            self._resends.pop(run["id"], None)
            head, *lines = run["detail"].split("\n")
            head = "；".join(
                [
                    head.split("；推送失败", 1)[0],
                    note.replace(SENT, RESENT, 1) if delivered == "sent" else note,
                ]
            )
            done = await self.store.set_delivery(
                run["id"],
                delivered=delivered,
                detail="\n".join([head, *lines]),
                error=error,
                duration_ms=run["duration_ms"],
            )
            log.info("监控「%s」：%s", watch.name, head)
            self.broadcaster.publish("watch", {"watch_id": watch.id, "run": done})

    async def _start_task(self, watch: Watch, found: str) -> None:
        """监控触发任务: the watch's bot works on what was found, in her chat with the user. Not while 试运行,
        not while she is not speaking, and not while the one before is still being worked on."""
        bot = self.brain.bots.get(watch.bot_id)
        tasks = self.brain.tasks
        if bot is None or tasks is None or watch.mode != "push" or bot.mode != "live":
            return
        chat = await self._chat(bot)
        if chat is None:
            log.info("监控「%s」要开任务，可是和 %s 还没有私聊", watch.name, bot.name)
            return
        title = f"监控：{watch.name}"[:TITLE_MAX]
        going = [t for t in await tasks.store.of_bot(bot.id) if t["status"] in ACTIVE and t["title"] == title]
        if going:
            log.info("监控「%s」上一次的任务还没做完，这次不另开", watch.name)
            return
        safe = found[:FOUND_CHARS].replace("<<<", "‹‹‹").replace(">>>", "›››")
        goal = f"{watch.task_goal}\n\n{MATERIAL.format(what='监控发现的变化', text=safe)}"
        started, why = await tasks.begin(bot, chat, title, goal, source="watch", unattended=True)
        if started is None:
            log.info("监控「%s」的任务没开成：%s", watch.name, why)

    async def _run(self, watch: Watch) -> dict[str, Any] | None:
        started = time.time()
        clock = time.monotonic()

        def elapsed() -> int:
            return round((time.monotonic() - clock) * 1000)

        interval = max(watch.interval_minutes, min_interval(watch.kind)) * 60  # a hand-edited 0 must not loop
        await self.store.started(watch.id, started, started + interval)
        check = await self._check(watch)
        notes: list[str | None] = []
        message = check.message
        bot = self.brain.bots.get(watch.bot_id)
        # A paused bot makes no model calls (全部暂停 is the emergency stop).
        if message is not None and watch.provider_id and bot is not None and bot.mode != "paused":
            rewritten = await self._rewrite(watch, bot, check.material or message)
            if rewritten is None:
                notes.append(MODEL_FAILED)
            else:
                message = rewritten
        if message is not None:
            message = _cap(message, self.policy.max_message_chars)
        reserved: float | None = None

        def record(current: Watch) -> RunRecord:
            nonlocal reserved
            if current.source != watch.source:
                return RunRecord("error", EDITED, error=EDITED)
            delivered, note = None, None
            if message is not None:
                target = self.brain.bots.get(current.bot_id)
                if target is None:
                    delivered, note = "failed", NO_BOT
                elif target.mode == "paused":
                    delivered, note = "paused", PAUSED
                elif current.mode != "push":
                    delivered, note = "preview", PREVIEW
                elif (reserved := self._reserve()) is None:
                    note = CAPPED
                else:
                    delivered, note = "unknown", INTERRUPTED  # until the send says otherwise
            return RunRecord(
                check.outcome,
                _detail(check, [*notes, note]),
                changes=check.changes,
                message=message,
                delivered=delivered,
                error=check.error,
                state=check.state,
            )

        try:
            finished = await self.store.finish(watch.id, record, started_at=started, duration_ms=elapsed())
        except BaseException:
            if reserved is not None:
                self._release(reserved)
            raise
        if finished is None:  # deleted during the check
            return None
        current, run = finished
        if reserved is not None and message is not None:
            delivered, note, error = await self._send(current, message, f"watch:{watch.id}:{started:.3f}")
            if delivered == "failed":
                self._release(reserved)
            run = (
                await self.store.set_delivery(
                    run["id"],
                    delivered=delivered,
                    detail=_detail(check, [*notes, note]),
                    error=error,
                    duration_ms=elapsed(),
                )
                or run
            )
        if message is not None and current.task_goal and run["outcome"] == "changed":
            await self._start_task(current, check.material or message)
        if run["outcome"] in ("changed", "error"):
            log.info("监控「%s」：%s", watch.name, run["detail"].split("\n", 1)[0])
        self.broadcaster.publish("watch", {"watch_id": watch.id, "run": run})
        return run
