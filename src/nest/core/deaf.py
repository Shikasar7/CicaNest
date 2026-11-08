"""假在线: an account that shows as connected, and NapCat's get_status calls online, while no message reaches
it and what it sends fails. Nothing else notices it, sometimes for hours.

Every half minute the accounts' newest messages are compared; there are two signs:

- in a group two of our accounts share, another one stored MISSED messages since this one last got
  anything, in any chat. Each account stores its own copy of a group's messages, so these should match.
  Counting missed messages instead of minutes keeps quiet hours and small groups out of it: nothing said,
  nothing missed. A healthy account misses a few in a row at most, one really cut off many more. Its own
  messages do not count (it is not sent those), a group it has left does not either, and neither does a
  connection younger than SETTLE, whose messages are still catching up.
  Only accounts that see every message (sees_all) compare: 企业微信 is only told what @s it, so two of them
  in a group getting different counts says nothing;
- a send failed the way NapCat's did that day (OneBotAdapter.stuck_send_at), and nothing arrived for QUIET
  up to now. Rare (that one line in a week of logs), so one is enough.

Either marks the account in the hub, which shows it as "deaf" instead of connected: 掉线提醒 (nest.core.
offline_alert) tells the user, and it stops being picked to send the alert or the 问题记录 digest. Only a
message that arrives live takes the mark off, not a backfilled one or a reconnection: NapCat serves history
to a client that gets nothing new. Nothing here restarts the container or logs in again, that is for the
user to decide. When every account in a group is deaf, nobody is left to compare with.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from nest.adapters.registry import can
from nest.storage.messages import MessageStore

log = logging.getLogger(__name__)

CHECK_SECONDS = 30
WINDOW = 1800.0  # how far back messages are compared
MISSED = 8
SETTLE = 300.0
QUIET = 600.0
GROUP_LIST_TIMEOUT = 5.0


def last_heard(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Each account's newest message from someone else that arrived live."""
    heard: dict[str, float] = {}
    for row in rows:
        account = row["account_id"]
        own = row["from_self"] or row["sender_id"] == account.partition(":")[2]
        if row["source"] == "live" and not own:
            heard[account] = max(heard.get(account, 0.0), row["sent_at"])
    return heard


def missed(rows: list[dict[str, Any]], account: str, self_id: str, after: float) -> dict[str, int]:
    """Group number -> the most messages any other account stored there after `after`, not from `self_id`."""
    counts = Counter(
        (row["account_id"], row["external_id"])
        for row in rows
        if row["kind"] == "group"
        and row["account_id"] != account
        and can(row["account_id"], "sees_all")
        and row["sent_at"] > after
        and row["sender_id"] != self_id
    )
    most: dict[str, int] = {}
    for (_, group), count in counts.items():
        most[group] = max(most.get(group, 0), count)
    return most


class DeafWatch:
    def __init__(self, hub: Any, store: MessageStore, *, clock: Callable[[], float] = time.time) -> None:
        self._hub = hub
        self._store = store
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="deaf-watch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                await self.check()
            except Exception:
                log.exception("检查 bot 是否假在线时出错")

    async def check(self) -> list[str]:
        """One pass; the accounts it marked."""
        hub = self._hub
        now = self._clock()
        rows = await self._store.traffic(now - WINDOW)
        heard = last_heard(rows)
        # Stopped on purpose or removed: not a fault to report any more.
        views = {
            v["id"]: v for v in hub.account_views() if v["enabled"] and v["status"]["state"] != "stopped"
        }
        for gone in set(hub.deaf) - set(views):
            hub.clear_deaf(gone)
        marked: list[str] = []
        for account, view in views.items():
            last = heard.get(account, 0.0)
            if account in hub.deaf:
                if last >= hub.deaf[account].since:
                    hub.clear_deaf(account)
                    log.info("%s 又收到消息了", view["name"] or account)
                continue
            adapter = hub.adapters.get(account)
            connected_at = view["status"]["since"]
            if adapter is None or view["status"]["state"] != "connected" or now - connected_at < SETTLE:
                continue
            detail = self._send_stuck(adapter, now, last, connected_at) or await self._left_out(
                rows, view, adapter, max(last, connected_at + SETTLE)
            )
            if detail:
                hub.mark_deaf(account, detail, now)
                log.warning("%s 假在线：%s", view["name"] or account, detail)
                marked.append(account)
        return marked

    def _send_stuck(self, adapter: Any, now: float, last: float, connected_at: float) -> str:
        stuck = adapter.stuck_send_at
        if not stuck or now - stuck > WINDOW or stuck <= last or now - max(last, connected_at) < QUIET:
            return ""
        return f"收不到消息：发消息失败（sendMsg 没有结果），而且 {int(QUIET // 60)} 分钟以上没收到任何消息"

    async def _left_out(
        self, rows: list[dict[str, Any]], view: dict[str, Any], adapter: Any, after: float
    ) -> str:
        if not can(view["id"], "sees_all"):
            return ""
        most = missed(rows, view["id"], view["self_id"], after)
        loud = {group: count for group, count in most.items() if count >= MISSED}
        if not loud:
            return ""
        groups = await self._groups(view["id"], adapter)
        loud = {group: count for group, count in loud.items() if group in groups}
        if not loud:
            return ""
        group = max(loud, key=lambda key: loud[key])
        name = next((r["name"] for r in rows if r["external_id"] == group and r["name"]), f"群 {group}")
        return f"收不到消息：「{name}」里别的 bot 收到了 {loud[group]} 条，它一条也没收到"

    async def _groups(self, account: str, adapter: Any) -> set[str]:
        """The groups it is in, as QQ says; as far as nest knows when QQ does not answer, which a client in
        this state may well not."""
        if can(account, "list_channels"):
            try:
                infos = await asyncio.wait_for(adapter.list_channels(), GROUP_LIST_TIMEOUT)
            except Exception as exc:
                log.info("读取 %s 的群列表失败：%s", account, type(exc).__name__)
            else:
                if infos:
                    return {info.channel.id for info in infos}
        channels = await self._store.channels()
        return {c["external_id"] for c in channels if c["account_id"] == account and c["kind"] == "group"}
