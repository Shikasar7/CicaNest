"""One Discord bot: the Gateway for events, REST for everything it says.

Unlike OneBot, reconnecting has a price. Identify is limited (1000 a day; past that Discord ends every
session and resets the token), so a lost connection resumes when it can, each new Identify first asks how
many are left and waits for the reset when few are, and repeated ones back off to minutes. Close codes that
retrying cannot fix end the connection for good and say why.

Discord may or may not send a bot its own messages: a sent message is delivered from the REST answer, under
its real id, so storage drops the copy if one also arrives.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Sequence
from typing import Any

import httpx
from websockets.exceptions import WebSocketException

from nest.adapters.base import AdapterStatus, BackfillSource, EventSink, StatusSink
from nest.adapters.common import Backoff, SentIds, StatusKeeper, catch_up, split_text
from nest.adapters.discord.gateway import Dropped, Fatal, Gateway
from nest.adapters.discord.normalize import (
    PLATFORM,
    Directory,
    channel_of,
    message_body,
    normalize,
    outgoing,
    recalls,
    snowflake_at,
)
from nest.adapters.discord.rest import (
    API_BASE,
    MEDIA_HOSTS,
    DiscordError,
    DiscordRest,
    Unauthorized,
    proxy_label,
)
from nest.core.events import (
    Action,
    ActionResult,
    ChannelInfo,
    ChannelRef,
    ChannelsSeen,
    Event,
    MessageEvent,
    SendMessage,
)
from nest.llm.http import scrub

log = logging.getLogger(__name__)

DEFAULT_GATEWAY = "wss://gateway.discord.gg"
IDENTIFY_RESERVE = 2  # stop this many short of the daily limit
IDENTIFY_FLOOR = 60.0  # the least to wait when the allowance is used up
HISTORY_PAGE = 100
HISTORY_PAGES = 5  # per channel: with "after", Discord answers the oldest ones first
LOOKUP_TIMEOUT = 3.0  # events wait for this
SEND_WINDOW = 20.0  # how long one request of a send may count as in flight


class DiscordAdapter:
    platform = PLATFORM
    capabilities = frozenset(
        {
            "fetch_history",
            "list_channels",
            "mention",
            "proactive",
            "reply",
            "sees_all",
            "send_image",
            "send_text",
            "send_voice",
        }
    )
    max_text = 2000
    stuck_send_at = 0.0  # a NapCat thing

    def __init__(
        self,
        *,
        account: str,
        token: str,
        proxy: str = "",
        on_status: StatusSink | None = None,
        backfill_source: BackfillSource | None = None,
        api_base: str = API_BASE,
        media_hosts: Sequence[str] = MEDIA_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        stable_after: float = 60.0,
        identify_initial: float = 5.0,  # Discord allows one every 5 seconds
        identify_max: float = 600.0,
        identify_stable: float = 300.0,
        identify_floor: float = IDENTIFY_FLOOR,
    ) -> None:
        self.account = account
        self._token = token
        self._proxy = proxy or None
        self._backfill_source = backfill_source
        self._status = StatusKeeper(f"Discord {account}", on_status)
        self._rest = DiscordRest(
            token, proxy=self._proxy, api_base=api_base, media_hosts=media_hosts, transport=transport
        )
        self._gateway = Gateway(token, self._on_dispatch, proxy=self._proxy)
        self._directory = Directory(account)
        self._backoff = Backoff(backoff_initial, backoff_max, stable_after)
        self._identify_backoff = Backoff(identify_initial, identify_max, identify_stable)
        self._identify_floor = identify_floor
        self._identified_at = 0.0  # monotonic; 0: not yet
        self.identifies = 0
        self._task: asyncio.Task | None = None
        self._backfill: asyncio.Task | None = None
        self._emit: EventSink | None = None
        self._stopping = False
        self._name = ""
        self._sent = SentIds()
        self._dm_of: dict[str, str] = {}  # person -> DM channel
        self._peers: dict[str, str] = {}  # DM channel -> person
        self._unknown: set[str] = set()  # channels already looked up without success
        self._seen_live: set[str] = set()
        self._backfilling = False

    # -- Adapter protocol --------------------------------------------------------------------------

    async def start(self, emit: EventSink) -> None:
        if self._task is None:
            self._stopping = False
            self._emit = emit
            self._task = asyncio.create_task(self._run(), name=f"discord:{self.account}")

    async def stop(self) -> None:
        self._stopping = True
        await self._gateway.close()
        for task in (self._task, self._backfill):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._backfill = None
        await self._rest.aclose()
        self._status.set("stopped", "已停止")

    def status(self) -> AdapterStatus:
        return self._status.current

    def sent_by_us(self, message_id: str) -> bool:
        return message_id in self._sent

    async def list_channels(self) -> list[ChannelInfo]:
        return [self._info(channel_id) for channel_id in self._directory.listed()]

    async def fetch_media(self, url: str, limit: int) -> bytes:
        return await self._rest.fetch(url, limit)

    async def perform(self, action: Action) -> ActionResult:
        """Send a message, in pieces if it is long. "failed" = certainly nothing went out; "unknown" = some of
        it may have. Never retried, except a 429, which Discord says it did not act on."""
        if not isinstance(action, SendMessage) or action.channel.account != self.account:
            return ActionResult("failed", error="这条消息不属于这个账号")
        current = self._status.current
        if current.state != "connected":
            reason = current.detail or current.state
            return ActionResult("failed", error=f"Discord 当前不在线（{reason}），没有发送")
        try:
            out = outgoing(action.segments)
        except ValueError as exc:
            return ActionResult("failed", error=str(exc))
        pieces = split_text(out.text, self.max_text)
        if not pieces and not out.uploads:
            return ActionResult("failed", error="这条消息里没有 Discord 能发的内容")
        try:
            target = await self._target(action.channel)
        except DiscordError as exc:
            return ActionResult("failed", error=self._said(exc))
        sent: list[str] = []
        self._sent.sending(SEND_WINDOW * max(1, len(pieces)))
        try:
            for index, piece in enumerate(pieces or [""]):
                body = message_body(piece, out, _nonce(action.idempotency_key, index), first=index == 0)
                uploads = out.uploads if index == 0 else ()
                try:
                    message = await self._rest.create_message(target, body, uploads)
                except DiscordError as exc:
                    return self._stopped_at(exc, sent)
                message_id = str(message.get("id"))
                sent.append(message_id)
                self._sent.add(message_id)
                await self._deliver_own(message, action.channel)
        finally:
            self._sent.sending(3)
        return ActionResult("succeeded", message_id=sent[0])

    def _stopped_at(self, exc: DiscordError, sent: list[str]) -> ActionResult:
        error = self._said(exc)
        if sent:  # what went out went out; the decision should not read as "nothing happened"
            error = f"长消息只发出了前 {len(sent)} 段：{error}"
            return ActionResult("unknown", message_id=sent[0], error=error)
        return ActionResult("failed" if exc.sure else "unknown", error=error)

    def _said(self, exc: Exception) -> str:
        return scrub(str(exc), self._token)

    async def _target(self, channel: ChannelRef) -> str:
        if channel.kind == "group":
            return channel.id
        known = self._dm_of.get(channel.id)
        if known is None:
            found = await self._rest.open_dm(channel.id)
            known = str(found["id"])
            self._remember_dm(channel.id, known)
        return known

    def _remember_dm(self, person: str, dm_channel: str) -> None:
        self._dm_of[person] = dm_channel
        self._peers[dm_channel] = person

    async def _deliver_own(self, message: dict[str, Any], channel: ChannelRef) -> None:
        try:
            event = normalize(message, channel, self._directory)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Discord %s 看不懂自己刚发的消息：%s", self.account, exc)
            return
        if event is not None:
            await self._deliver(event)

    async def _deliver(self, event: Event) -> None:
        assert self._emit is not None
        try:
            await self._emit(event)
        except Exception:
            log.exception("Discord %s 处理事件出错，已跳过这一条", self.account)

    def _info(self, channel_id: str) -> ChannelInfo:
        ref = ChannelRef(PLATFORM, self.account, "group", channel_id)
        return ChannelInfo(ref, self._directory.name(channel_id))

    # -- connection loop -------------------------------------------------------------------------

    async def _run(self) -> None:
        self._status.set("connecting", "正在连接")
        if not self._token:
            self._status.set("auth_failed", "还没有填 Token")
            return
        while not self._stopping:
            started = time.monotonic()
            try:
                # Resuming costs nothing from the allowance, and Discord replays what was missed.
                url = self._gateway.resume_url if self._gateway.resumable else await self._identify_slot()
                await self._gateway.run(url)
                detail = "连接被关闭"
            except asyncio.CancelledError:
                raise
            except Fatal as exc:
                self._status.set(exc.state, exc.detail)  # type: ignore[arg-type]
                return
            except Unauthorized:
                self._status.set("auth_failed", "Discord 不认这个 Token：到「系统」页填新的")
                return
            except (Dropped, DiscordError) as exc:
                detail = self._said(exc)
            except (OSError, TimeoutError, WebSocketException) as exc:
                detail = self._connection_trouble(exc)
            except Exception as exc:
                log.exception("Discord %s 连接出现未预期的错误", self.account)
                detail = f"内部错误：{type(exc).__name__}"
            if self._stopping:
                break
            wait = self._backoff.next(time.monotonic() - started)
            self._status.set("reconnecting", f"{detail}；{wait:.0f} 秒后重试" if wait >= 1 else detail)
            await asyncio.sleep(wait)

    def _connection_trouble(self, exc: Exception) -> str:
        """Why the socket did not open; never the token's fault, and never to be mistaken for that."""
        if self._proxy:
            return f"代理 {proxy_label(self._proxy)} 连不上（{type(exc).__name__}）"
        return f"连不上 Discord（{type(exc).__name__}）"

    async def _identify_slot(self) -> str:
        """Wait for the right to Identify: past the backoff, with allowance left. The Gateway's address."""
        if self._identified_at:
            gap = self._identify_backoff.next(time.monotonic() - self._identified_at)
            left = self._identified_at + gap - time.monotonic()
            if left > 0:
                await asyncio.sleep(left)
        while True:
            info = await self._rest.gateway_bot()
            limit = info.get("session_start_limit") or {}
            remaining = int(limit.get("remaining", IDENTIFY_RESERVE + 1))
            if remaining > IDENTIFY_RESERVE:
                break
            wait = max(float(limit.get("reset_after", 0)) / 1000, self._identify_floor)
            self._status.set(
                "reconnecting",
                f"Discord 今天的登录次数快用完了（还剩 {remaining} 次），{wait / 60:.0f} 分钟后再连",
            )
            await asyncio.sleep(wait)
        self._identified_at = time.monotonic()
        self.identifies += 1
        return str(info.get("url") or DEFAULT_GATEWAY)

    # -- events ------------------------------------------------------------------------------------

    async def _on_dispatch(self, kind: str, data: dict[str, Any]) -> None:
        try:
            await self._event(kind, data)
        except (Fatal, Dropped):
            raise
        except Exception:  # one odd event must not cost the connection
            log.exception("Discord %s 处理 %s 出错，已跳过", self.account, kind)

    async def _event(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "READY":
            await self._ready(data)
        elif kind == "RESUMED":
            self._status.set("connected", self._name)
        elif kind == "GUILD_CREATE" and not data.get("unavailable"):
            found = set(self._directory.add_guild(data))
            await self._named([c for c in self._directory.listed() if c in found])
        elif kind == "GUILD_DELETE":
            self._directory.remove_guild(str(data.get("id")))
        elif kind in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE"):
            if data.get("id") is not None and data.get("guild_id"):
                self._directory.put_channel(data)
                await self._named([c for c in self._directory.listed() if c == str(data["id"])])
        elif kind in ("CHANNEL_DELETE", "THREAD_DELETE"):
            self._directory.drop_channel(str(data.get("id")))
        elif kind == "MESSAGE_CREATE":
            await self._message(data)
        elif kind in ("MESSAGE_DELETE", "MESSAGE_DELETE_BULK"):
            for recall in recalls(data, self.account, self._peers, time.time()):
                await self._deliver(recall)

    async def _ready(self, data: dict[str, Any]) -> None:
        user = data.get("user") or {}
        if str(user.get("id")) != self.account:
            self._gateway.forget_session()
            raise Fatal("misconfigured", f"这个 Token 属于 bot {user.get('id')}，不是 {self.account}")
        self._name = str(user.get("global_name") or user.get("username") or "")
        connected_at = time.time()
        self._status.set("connected", self._name)
        # Only a new session has anything to catch up on: a resumed one gets it replayed.
        if self._backfill is not None:
            self._backfill.cancel()
        self._seen_live = set()
        self._backfilling = self._backfill_source is not None
        if self._backfill_source is not None:
            self._backfill = asyncio.create_task(
                self._catch_up(connected_at), name=f"discord:backfill:{self.account}"
            )

    async def _named(self, channel_ids: list[str]) -> None:
        if channel_ids:
            await self._deliver(ChannelsSeen(tuple(self._info(c) for c in channel_ids)))

    async def _message(self, data: dict[str, Any]) -> None:
        try:
            channel = channel_of(data, self.account, self._peers)
            if channel is None:
                return
            if channel.kind == "group":
                await self._learn_channel(channel.id, data)
            else:
                self._remember_dm(channel.id, str(data["channel_id"]))
            event = normalize(data, channel, self._directory)
        except (KeyError, TypeError, ValueError) as exc:
            self._gateway.skipped_frames += 1
            log.warning("Discord %s 跳过一条无法识别的消息：%s", self.account, exc)
            return
        if event is None:
            return
        if self._backfilling:
            self._seen_live.add(event.message_id)
        await self._deliver(event)

    async def _learn_channel(self, channel_id: str, data: dict[str, Any]) -> None:
        """A thread nobody told us about: find its name once. A message comes first if this fails."""
        if channel_id in self._directory.channels or channel_id in self._unknown:
            return
        try:
            async with asyncio.timeout(LOOKUP_TIMEOUT):
                found = await self._rest.channel(channel_id)
        except (DiscordError, TimeoutError) as exc:
            self._unknown.add(channel_id)
            log.info("Discord %s 查不到频道 %s：%s", self.account, channel_id, self._said(exc))
            return
        self._directory.put_channel({**found, "guild_id": data.get("guild_id")})
        await self._named([c for c in self._directory.listed() if c == channel_id])

    # -- catching up -------------------------------------------------------------------------------

    async def _catch_up(self, connected_at: float) -> None:
        assert self._backfill_source is not None and self._emit is not None
        try:
            await catch_up(
                self._backfill_source,
                self._history,
                self._emit,
                account=self.account,
                label=f"Discord {self.account}",
                connected_at=connected_at,
                seen_live=lambda: self._seen_live,
            )
        finally:
            self._backfilling = False
            self._seen_live = set()

    async def _history(self, channel: ChannelRef, since: float) -> list[MessageEvent]:
        """What was said in a channel since then, oldest first. Without READ_MESSAGE_HISTORY: nothing."""
        events: list[MessageEvent] = []
        after = snowflake_at(since)
        for _ in range(HISTORY_PAGES):
            page = await self._rest.messages(channel.id, after=after, limit=HISTORY_PAGE)
            if not page:
                break
            page.sort(key=lambda message: int(message["id"]))
            for payload in page:
                with contextlib.suppress(KeyError, TypeError, ValueError):
                    event = normalize(payload, channel, self._directory, backfilled=True)
                    if event is not None:
                        events.append(event)
            after = int(page[-1]["id"])
            if len(page) < HISTORY_PAGE:
                break
        return events


def _nonce(key: str, index: int) -> str:
    """Discord's nonce holds at most 25 characters; ours can be longer. A long reply's pieces differ."""
    return hashlib.sha256(f"{key}:{index}".encode()).hexdigest()[:25]
