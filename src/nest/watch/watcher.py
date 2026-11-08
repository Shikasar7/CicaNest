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
- A push that failed is sent again once the bot is connected, for `resend_hours`: the entry counts as seen,
  so nothing else would ever send it. One whose result is unknown is not, it may have arrived.
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

from nest.adapters.registry import place_of
from nest.core.broadcast import Broadcaster
from nest.core.events import ChannelRef, Segment, SendMessage
from nest.llm.base import ChatMessage
from nest.llm.runtime import ProviderRegistry
from nest.storage.bots import BotConfig
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo
from nest.storage.watches import RunRecord, Watch, WatchStore
from nest.watch.check import Check, evaluate, failed
from nest.watch.fetch import WatchError, fetch

if TYPE_CHECKING:
    from nest.core.brain import Brain
    from nest.core.hub import Hub

log = logging.getLogger(__name__)

HOUR = 3600.0
MIN_INTERVAL = 5  # minutes
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
            page = await self.fetch(
                watch.url, proxy=proxy, timeout=self.policy.fetch_timeout, transport=self.transport
            )
            # Parsing a big page or feed takes a moment: never on the loop the bots answer on.
            return await asyncio.to_thread(evaluate, watch, page)
        except WatchError as exc:
            return failed(str(exc))
        except Exception as exc:  # a parser bug must not stop the schedule
            log.exception("监控「%s」检查出错", watch.name)
            return failed(f"检查出错（{type(exc).__name__}）")

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

    async def _send(self, watch: Watch, text: str, key: str) -> tuple[str, str, str | None]:
        """(delivered, note, error)."""
        bot = self.brain.bots.get(watch.bot_id)
        if bot is None:
            return "failed", NO_BOT, "找不到这个 bot"
        adapter = self.hub.adapters.get(bot.account_id)
        if adapter is None:
            return "failed", "这个 bot 的号没有在运行，没有推送", "账号没有在运行"
        account = self.hub.accounts.get(bot.account_id)
        platform = account.platform if account else bot.account_id.split(":", 1)[0]
        channel = ChannelRef(platform, bot.self_id, watch.target_kind, watch.target_id)  # type: ignore[arg-type]
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
                or now - last < policy.resend_gap
                or self.hub.status_of(bot.account_id).state != "connected"
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
            head = "；".join([head.split("；推送失败", 1)[0], RESENT if delivered == "sent" else note])
            done = await self.store.set_delivery(
                run["id"],
                delivered=delivered,
                detail="\n".join([head, *lines]),
                error=error,
                duration_ms=run["duration_ms"],
            )
            log.info("监控「%s」：%s", watch.name, head)
            self.broadcaster.publish("watch", {"watch_id": watch.id, "run": done})

    async def _run(self, watch: Watch) -> dict[str, Any] | None:
        started = time.time()
        clock = time.monotonic()

        def elapsed() -> int:
            return round((time.monotonic() - clock) * 1000)

        interval = max(watch.interval_minutes, MIN_INTERVAL) * 60  # a hand-edited 0 must not loop
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
        if run["outcome"] in ("changed", "error"):
            log.info("监控「%s」：%s", watch.name, run["detail"].split("\n", 1)[0])
        self.broadcaster.publish("watch", {"watch_id": watch.id, "run": run})
        return run
