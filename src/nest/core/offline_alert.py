"""掉线提醒: a private message to the user when a bot's QQ has been disconnected for a while, and another
when it is back.

Every half minute the accounts' connection states are looked at (Hub.account_views). An enabled account
that has not been connected for the set minutes (「系统」页「掉线提醒」, 5 by default) gets one message,
sent by any bot that is still connected; when it connects again, one more says so. Short drops (the
weekly restart, NapCat restarting its login) end before the alert is due and are not reported. With no
QQ number set it is off. When no bot is connected nobody can send it: logged once, tried again while the
account stays down.

An account that is connected but gets no messages (假在线, nest.core.deaf) is reported the same way, at
once, since finding that out already took a while; when messages reach it again, one more says so. It does
not count as down, nor as a bot that can send the alert.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from nest.adapters.base import parse_recipient
from nest.adapters.registry import can

log = logging.getLogger(__name__)

CHECK_SECONDS = 30
DEFAULT_MINUTES = 5
MAX_MINUTES = 120

# (whom to tell, see nest.adapters.base.parse_recipient, "" = off; minutes an account must be down first)
SettingsReader = Callable[[], Awaitable[tuple[str, int]]]
PrivateSender = Callable[[str, str, str], Awaitable[bool]]  # (account id, user id, text) -> sent


class OfflineAlert:
    def __init__(
        self,
        hub: Any,
        send_private: PrivateSender,
        read_settings: SettingsReader,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._hub = hub
        self._send_private = send_private
        self._read_settings = read_settings
        self._clock = clock
        self._down_since: dict[str, float] = {}  # account id -> first seen not connected
        self._told: set[str] = set()  # told it was down, not yet that it is back
        self._unsent: set[str] = set()  # down, due, but no bot could send it (logged once)
        self._deaf_told: dict[str, float] = {}  # told it gets no messages, not yet that it does: since when
        self._deaf_unsent: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="offline-alert")

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
                log.exception("检查 bot 是否掉线时出错")

    async def check(self) -> list[str]:
        """One pass; the messages it sent."""
        to, minutes = await self._read_settings()
        # Stopped on purpose (its QQ container, see Hub.hold): not down, nothing to tell.
        views = [v for v in self._hub.account_views() if v["enabled"] and v["status"]["state"] != "stopped"]
        now = self._clock()
        if not to:
            self._down_since.clear()
            self._told.clear()
            self._unsent.clear()
            self._deaf_told.clear()
            self._deaf_unsent.clear()
            return []
        present = {v["id"] for v in views}
        for gone in set(self._down_since) - present:  # removed in the console, or disabled
            self._down_since.pop(gone, None)
            self._told.discard(gone)
            self._unsent.discard(gone)
        for gone in (set(self._deaf_told) | self._deaf_unsent) - present:
            self._deaf_told.pop(gone, None)
            self._deaf_unsent.discard(gone)
        connected = [v for v in views if v["status"]["state"] == "connected"]
        sent: list[str] = []
        for view in views:
            account = view["id"]
            who = f"{view['name']}（{view['self_id']}）" if view["name"] else view["self_id"]
            sent += await self._check_deaf(to, view, who, connected, now)
            if view["status"]["state"] in ("connected", "deaf"):
                since = self._down_since.pop(account, None)
                self._unsent.discard(account)
                if account in self._told and since is not None:
                    self._told.discard(account)
                    gone_for = max(1, math.ceil((now - since) / 60))
                    text = f"nest 提醒：{who}又连上了，前后断了大约 {gone_for} 分钟。"
                    if await self._tell(to, text, connected, first=account):
                        sent.append(text)
                continue
            since = self._down_since.setdefault(account, now)
            if account in self._told or now - since < minutes * 60:
                continue
            down_for = math.floor((now - since) / 60)
            detail = view["status"]["detail"]
            advice = "如果是 QQ 登录失效，处理好以后到控制台「系统」页点它的「扫码登录」。"
            text = (
                f"nest 提醒：{who}掉线 {down_for} 分钟了"
                + (f"：{detail}" if detail else "")
                + ("。" + advice if account.startswith("qq:") else "。")
            )
            if await self._tell(to, text, connected):
                self._told.add(account)
                self._unsent.discard(account)
                sent.append(text)
                log.warning("%s 掉线 %d 分钟了，已私聊通知 %s", who, down_for, to)
            elif account not in self._unsent:
                self._unsent.add(account)
                log.warning("%s 掉线 %d 分钟了，但没有在线的 bot 能私聊通知 %s", who, down_for, to)
        return sent

    async def _check_deaf(
        self, to: str, view: dict[str, Any], who: str, connected: list[dict[str, Any]], now: float
    ) -> list[str]:
        account, status = view["id"], view["status"]
        if status["state"] == "connected" and account in self._deaf_told:
            gone_for = max(1, math.ceil((now - self._deaf_told.pop(account)) / 60))
            text = f"nest 提醒：{who}又能收到消息了，从发现到现在大约 {gone_for} 分钟。"
            return [text] if await self._tell(to, text, connected, first=account) else []
        if status["state"] != "deaf" or account in self._deaf_told:
            return []
        text = f"nest 提醒：{who}显示在线，其实是假在线。{status['detail']}。这时它不会回消息"
        if account.startswith("qq:"):
            text += "，可以到控制台「系统」页重启它的容器，不行再点「扫码登录」"
        text += "。"
        if await self._tell(to, text, connected):
            self._deaf_told[account] = status["since"]
            self._deaf_unsent.discard(account)
            log.warning("%s 假在线，已私聊通知 %s", who, to)
            return [text]
        if account not in self._deaf_unsent:
            self._deaf_unsent.add(account)
            log.warning("%s 假在线，但没有在线的 bot 能私聊通知 %s", who, to)
        return []

    async def _tell(self, to: str, text: str, connected: list[dict[str, Any]], *, first: str = "") -> bool:
        """Send from the first connected bot that can (`first`, the account that just came back, before the
        others): one on the platform of the person told, and never the account being told."""
        platform, user = parse_recipient(to)
        senders = sorted(connected, key=lambda v: v["id"] != first)
        for view in senders:
            if view["self_id"] == user or view["id"].partition(":")[0] != platform:
                continue
            if not can(view["id"], "proactive"):
                continue
            if await self._send_private(view["id"], to, text):
                return True
        return False
