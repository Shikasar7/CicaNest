"""Runs the platform adapters and routes what they receive: storage first, then live listeners.

Every event passes one queue and one dispatcher task, so storage has a single writer and a failure on
one event is logged without affecting the next.

An account whose QQ container is stopped (on the 系统 page, by nest.qr_guard, or by hand) is held: its
connection ends and is not retried, and it shows as stopped, until the container runs again. One that is
connected but gets no messages (nest.core.deaf) shows as deaf until a message reaches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from nest.adapters.base import Adapter, AdapterStatus, BackfillSource, StatusSink
from nest.adapters.registry import PLATFORMS, build_adapter, can
from nest.core.brain import Brain
from nest.core.broadcast import Broadcaster
from nest.core.events import CaughtUp, ChannelsSeen, Event, MessageEvent, RecallEvent
from nest.core.stickers import StickerKeeper
from nest.secrets import SecretStore
from nest.storage.accounts import Account, get_account, list_accounts
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.storage.watches import WatchStore

log = logging.getLogger(__name__)

AdapterFactory = Callable[[Account, SecretStore, StatusSink, BackfillSource], Adapter]
DAY = 86400.0
CONTAINER_STOPPED = "QQ 容器已停止，没有在连：在「系统」页启动它，或点「扫码登录」"


def status_dict(status: AdapterStatus) -> dict[str, Any]:
    return {"state": status.state, "detail": status.detail, "since": status.since}


class Hub:
    def __init__(
        self,
        db: Database,
        store: MessageStore,
        secrets: SecretStore,
        broadcaster: Broadcaster,
        *,
        factory: AdapterFactory = build_adapter,
        brain: Brain | None = None,
        retention_days: float = 30,
        queue_size: int = 10_000,
    ) -> None:
        self.db = db
        self.store = store
        self.secrets = secrets
        self.broadcaster = broadcaster
        self.brain = brain
        self.stickers: StickerKeeper | None = None  # 表情包: set by the app; sees every stored message
        self._factory = factory
        self._retention_days = retention_days
        self._queue: asyncio.Queue[Event] = asyncio.Queue(queue_size)
        self.accounts: dict[str, Account] = {}
        self.adapters: dict[str, Adapter] = {}
        self.held: dict[str, str] = {}  # account id -> why its connection is held (its container stopped)
        self.deaf: dict[str, AdapterStatus] = {}  # account id -> what to show while it is connected
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._spawn(self._dispatch(), "hub:dispatch")
        # The accounts of bots removed in the console stay in the database (their history), not here.
        removed = {
            row["account_id"]
            for row in await self.db.fetchall("SELECT account_id FROM bots WHERE deleted_at IS NOT NULL")
        }
        for account in await list_accounts(self.db):
            if account.id not in removed:
                await self._connect(account)

    async def _connect(self, account: Account) -> None:
        self.accounts[account.id] = account
        if not account.enabled or account.id in self.held:
            return
        if account.platform not in PLATFORMS:
            log.warning("账号 %s 的平台 %s 暂不支持，已跳过", account.id, account.platform)
            return
        adapter = self._factory(
            account,
            self.secrets,
            partial(self._status_changed, account.id),
            partial(self.store.backfill_targets, account.platform, account.self_id),
        )
        self.adapters[account.id] = adapter  # the same dict the brain and 监控 send through
        await adapter.start(self._queue.put)

    async def reconnect(self, account_id: str) -> None:
        """Pick up an account's changed connection settings: end its connection and open a new one."""
        account = await get_account(self.db, account_id)
        if account is None:
            return
        old = self.adapters.pop(account_id, None)
        if old is not None:
            await old.stop()
        await self._connect(account)

    async def hold(self, account_id: str, reason: str = CONTAINER_STOPPED) -> bool:
        """Its QQ container stopped: end the connection and do not retry it. True when it was not held."""
        if account_id not in self.accounts or account_id in self.held:
            return False
        self.held[account_id] = reason
        old = self.adapters.pop(account_id, None)
        if old is not None:
            await old.stop()
        self._publish_status(account_id)
        log.info("%s 的 QQ 容器停了，先不连它", account_id)
        return True

    async def release(self, account_id: str) -> bool:
        """Its QQ container runs again: connect. True when it was held."""
        if self.held.pop(account_id, None) is None:
            return False
        account = self.accounts.get(account_id)
        if account is not None:
            log.info("%s 的 QQ 容器又在运行了，重新连接", account_id)
            await self._connect(account)
        return True

    def _held(self, account_id: str) -> AdapterStatus:
        return AdapterStatus("stopped", self.held[account_id])

    async def forget(self, account_id: str) -> None:
        """A bot removed in the console: end its connection and stop listing its account."""
        old = self.adapters.pop(account_id, None)
        if old is not None:
            await old.stop()
        self.accounts.pop(account_id, None)
        self.held.pop(account_id, None)
        self.deaf.pop(account_id, None)

    async def stop(self) -> None:
        for adapter in self.adapters.values():
            await adapter.stop()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._queue.join(), 5)  # let already-received events reach storage
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def mark_deaf(self, account_id: str, detail: str, since: float) -> None:
        self.deaf[account_id] = AdapterStatus("deaf", detail, since)
        self._publish_status(account_id)

    def clear_deaf(self, account_id: str) -> None:
        if self.deaf.pop(account_id, None) is not None:
            self._publish_status(account_id)

    def status_of(self, account_id: str) -> AdapterStatus:
        """What the console shows for an account."""
        if account_id in self.held:
            return self._held(account_id)
        adapter = self.adapters.get(account_id)
        return self._shown(account_id, adapter.status()) if adapter else AdapterStatus("stopped", "已停用")

    def _shown(self, account_id: str, status: AdapterStatus) -> AdapterStatus:
        deaf = self.deaf.get(account_id)
        return deaf if deaf is not None and status.state == "connected" else status

    def name_of(self, account: Account) -> str:
        """The bot's name: the account's own was written at setup and does not follow a rename."""
        bots = self.brain.bots.values() if self.brain is not None else ()
        return next((bot.name for bot in bots if bot.account_id == account.id), account.name)

    def account_views(self) -> list[dict[str, Any]]:
        return [
            {
                "id": account.id,
                "platform": account.platform,
                "self_id": account.self_id,
                "name": self.name_of(account),
                "enabled": account.enabled,
                "status": status_dict(self.status_of(account.id)),
            }
            for account in self.accounts.values()
        ]

    # -- internals -------------------------------------------------------------------------------

    def _spawn(self, coro: Any, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish_status(self, account_id: str) -> None:
        status = status_dict(self.status_of(account_id))
        self.broadcaster.publish("status", {"account_id": account_id, **status})

    def _status_changed(self, account_id: str, status: AdapterStatus) -> None:
        shown = self._shown(account_id, status)
        self.broadcaster.publish("status", {"account_id": account_id, **status_dict(shown)})
        if status.state == "connected":
            self._spawn(self._refresh_channels(account_id), f"hub:channels:{account_id}")

    async def _refresh_channels(self, account_id: str) -> None:
        adapter = self.adapters.get(account_id)
        if adapter is None or not can(account_id, "list_channels"):
            return
        try:
            await self.store.name_channels(await adapter.list_channels())
        except Exception as exc:
            log.warning("读取 %s 的群列表失败：%s", account_id, exc)
            return
        self.broadcaster.publish("channels", {"account_id": account_id})

    async def _dispatch(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                if isinstance(event, MessageEvent):
                    row = await self.store.save(event)
                    if row is not None:
                        self.broadcaster.publish("message", row)
                        if self.stickers is not None:
                            self.stickers.on_message(event)
                        if self.brain is not None:
                            await self.brain.on_message(event, row)
                elif isinstance(event, RecallEvent):
                    row = await self.store.recall(event)
                    if row is not None:
                        self.broadcaster.publish("recall", row)
                elif isinstance(event, ChannelsSeen):
                    await self.store.name_channels(list(event.infos))
                    if event.infos:
                        owner = event.infos[0].channel
                        account = f"{owner.platform}:{owner.account}"
                        self.broadcaster.publish("channels", {"account_id": account})
                elif isinstance(event, CaughtUp) and self.brain is not None:
                    await self.brain.catch_up(event)
            except Exception:
                log.exception("保存事件失败，已跳过这一条")
            finally:
                self._queue.task_done()

    async def purge(self, now: float, *, days: float | None = None, calls_days: float = 14) -> dict[str, int]:
        """Delete what is older than `days` (the retention period by default): messages, decisions and
        监控 runs, and model calls older than `calls_days`. Counts what went; nest.housekeeping runs it
        daily."""
        days = self._retention_days if days is None else days
        before = now - days * DAY
        messages = await self.store.purge(before)
        if messages:
            log.info("清理了 %d 条超过 %g 天的消息", messages, days)
        runs = await WatchStore(self.db).purge_runs(before)
        if runs:
            log.info("清理了 %d 条超过 %g 天的监控记录", runs, days)
        records = runs
        if self.brain is not None:
            records += await self.brain.purge(decisions_before=before, calls_before=now - calls_days * DAY)
        return {"messages": messages, "records": records}
