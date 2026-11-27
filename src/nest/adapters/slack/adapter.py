"""One Slack bot over Socket Mode: nest connects out, so no public address is needed; the Web API for what it
says.

Every event comes in an envelope that has to be acknowledged within seconds, or Slack sends it again: it is
acknowledged first and worked on after. Slack closes a connection now and then and says so beforehand
("disconnect"): the next one is opened at once. What was missed meanwhile is fetched from each channel's
history.

Slack sends a bot its own messages. What was posted is delivered from the answer, under its real id, and its
copy is dropped; a shared file has no id until that copy arrives, so there the copy is the event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from nest.adapters.base import AdapterStatus, BackfillSource, EventSink, StatusSink
from nest.adapters.common import Backoff, SentIds, StatusKeeper, catch_up, split_text
from nest.adapters.slack.api import (
    API_BASE,
    MEDIA_HOSTS,
    MissingScope,
    SlackApi,
    SlackError,
    Unauthorized,
)
from nest.adapters.slack.normalize import (
    MAX_TEXT,
    PLATFORM,
    is_direct,
    mentioned,
    message_key,
    normalize,
    outgoing,
    person_name,
    split_key,
)
from nest.core.events import (
    Action,
    ActionResult,
    ChannelInfo,
    ChannelRef,
    ChannelsSeen,
    Event,
    MemberJoined,
    MessageEvent,
    RecallEvent,
    SendMessage,
)

log = logging.getLogger(__name__)

# websockets logs every frame at debug level, and the address holds a ticket.
quiet = logging.getLogger("nest.adapters.slack.frames")
quiet.setLevel(logging.WARNING)

OPEN_TIMEOUT = 15.0
HELLO_TIMEOUT = 15.0
MAX_FRAME = 16 * 1024 * 1024
QUICK_SECONDS = 5.0  # a connection ended sooner than this is not renewed at once
LOOKUP_TIMEOUT = 3.0  # a message waits this long for its sender's name
SEND_WINDOW = 20.0
NAMES_KEPT = 5000
THREADS_KEPT = 4096
NAME_AGE = 24 * 3600.0
NO_SOCKET_MODE = "这个 Slack 应用没有打开 Socket Mode：打开以后，到「平台接入」页保存一次连接设置"


class Wrong(Exception):
    """The connection cannot work until the user changes something."""


class Again(Exception):
    """Slack asked for a new connection: nothing is wrong."""


class SlackAdapter:
    platform = PLATFORM
    capabilities = frozenset(
        {
            "comparable",
            "fetch_history",
            "list_channels",
            "mention",
            "proactive",
            "sees_all",
            "send_image",
            "send_text",
            "send_voice",
        }
    )
    max_text = MAX_TEXT
    stuck_send_at = 0.0  # a NapCat thing

    def __init__(
        self,
        *,
        account: str,
        bot_token: str,
        app_token: str,
        proxy: str = "",
        on_status: StatusSink | None = None,
        backfill_source: BackfillSource | None = None,
        api_base: str = API_BASE,
        media_hosts: tuple[str, ...] = MEDIA_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
        stable_after: float = 60.0,
    ) -> None:
        self.account = account
        self._complete = bool(bot_token and app_token)
        self._proxy = proxy or None
        self._api = SlackApi(
            bot_token,
            app_token,
            proxy=self._proxy,
            api_base=api_base,
            media_hosts=media_hosts,
            transport=transport,
        )
        self._status = StatusKeeper(f"Slack {account}", on_status)
        self._backoff = Backoff(backoff_initial, backoff_max, stable_after)
        self._backfill_source = backfill_source
        self._task: asyncio.Task | None = None
        self._backfill: asyncio.Task | None = None
        self._work: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._emit: EventSink | None = None
        self._stopping = False
        self._bot_id = ""  # our app's bot id
        self._name = ""
        self._sent = SentIds()
        self._posted: OrderedDict[str, None] = OrderedDict()  # posted and delivered: the copy is dropped
        self._names: OrderedDict[str, tuple[str, float]] = OrderedDict()  # person -> (name, when)
        self._titles: dict[str, str] = {}
        self._dms: dict[str, str] = {}  # person -> direct message channel
        self._people: dict[str, str] = {}  # direct message channel -> person
        self._threads: OrderedDict[str, str] = OrderedDict()  # message -> the thread it is in
        self._seen_live: set[str] = set()
        self._backfilling = False

    # -- Adapter protocol --------------------------------------------------------------------------

    async def start(self, emit: EventSink) -> None:
        if self._task is None:
            self._stopping = False
            self._emit = emit
            self._worker = asyncio.create_task(self._events(), name=f"slack:events:{self.account}")
            self._task = asyncio.create_task(self._run(), name=f"slack:{self.account}")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._task, self._backfill, self._worker):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._backfill = self._worker = None
        await self._api.aclose()
        self._status.set("stopped", "已停止")

    def status(self) -> AdapterStatus:
        return self._status.current

    def sent_by_us(self, message_id: str) -> bool:
        return message_id in self._sent

    async def list_channels(self) -> list[ChannelInfo]:
        try:
            found = await self._api.channels()
        except SlackError as exc:
            log.info("Slack %s 读不到频道列表：%s", self.account, self._api.clean(str(exc)))
            return []
        infos = [self._info(c) for c in found]
        self._titles.update({info.channel.id: info.name for info in infos})
        return infos

    async def fetch_media(self, url: str, limit: int) -> bytes:
        return await self._api.fetch(url, limit)

    async def perform(self, action: Action) -> ActionResult:
        """Send a message, in pieces if it is long, then its files. "failed" = certainly nothing went out;
        "unknown" = some of it may have. Never retried, except a 429, which Slack says it did not act on."""
        if not isinstance(action, SendMessage) or action.channel.account != self.account:
            return ActionResult("failed", error="这条消息不属于这个账号")
        current = self._status.current
        if current.state != "connected":
            reason = current.detail or current.state
            return ActionResult("failed", error=f"Slack 当前不在线（{reason}），没有发送")
        try:
            out = outgoing(action.segments)
        except ValueError as exc:
            return ActionResult("failed", error=str(exc))
        pieces = split_text(out.text, MAX_TEXT)
        if not pieces and not out.files:
            return ActionResult("failed", error="这条消息里没有 Slack 能发的内容")
        sent: list[str] = []
        self._sent.sending(SEND_WINDOW * (len(pieces) + len(out.files)))
        try:
            target = await self._target(action.channel)
            thread = self._thread_of(action)
            for piece in pieces:
                sent.append(await self._post(action.channel, target, piece, thread))
            for name, data in out.files:
                await self._api.upload(target, name, data, thread=thread)
                sent.append("")
        except SlackError as exc:
            return self._stopped_at(exc, sent)
        finally:
            self._sent.sending(5)  # a shared file's copy arrives a little later
        return ActionResult("succeeded", message_id=next((s for s in sent if s), None))

    async def _target(self, channel: ChannelRef) -> str:
        if channel.kind == "group":
            return channel.id
        known = self._dms.get(channel.id)
        if known is None:
            known = await self._api.open_dm(channel.id)
            if not known:
                raise SlackError("没能和对方开私聊")
            self._remember_dm(channel.id, known)
        return known

    def _remember_dm(self, person: str, dm: str) -> None:
        self._dms[person] = dm
        self._people[dm] = person

    def _thread_of(self, action: SendMessage) -> str:
        """The thread what it answers is in, "" when it is said in the channel itself."""
        return self._threads.get(action.trigger or "", "")

    async def _post(self, channel: ChannelRef, target: str, text: str, thread: str) -> str:
        body: dict[str, Any] = {"channel": target, "text": text, "unfurl_links": False}
        if thread:
            body["thread_ts"] = thread
        answer = await self._api.post(body)
        ts = str(answer.get("ts") or "")
        if not ts:
            raise SlackError("Slack 回答了看不懂的内容", sure=False)
        key = message_key(target, ts)
        self._sent.add(key)
        self._posted[key] = None
        while len(self._posted) > THREADS_KEPT:
            self._posted.popitem(last=False)
        said = answer.get("message") if isinstance(answer.get("message"), dict) else {}
        event = {
            **said,
            "channel": target,
            "ts": ts,
            "text": said.get("text") or text,
            "user": self.account,
            **({"thread_ts": thread} if thread else {}),
        }
        event.pop("subtype", None)
        with contextlib.suppress(KeyError, TypeError, ValueError):
            own = normalize(event, channel, self._known(), own_bot=self._bot_id)
            if own is not None:
                await self._deliver(own)
        return key

    def _stopped_at(self, exc: SlackError, sent: list[str]) -> ActionResult:
        error = self._api.clean(str(exc))
        if sent:  # what went out went out; the decision should not read as "nothing happened"
            error = f"只发出了前 {len(sent)} 段：{error}"
            return ActionResult("unknown", message_id=next((s for s in sent if s), None), error=error)
        return ActionResult("failed" if exc.sure else "unknown", error=error)

    async def _deliver(self, event: Event) -> None:
        assert self._emit is not None
        try:
            await self._emit(event)
        except Exception:
            log.exception("Slack %s 处理事件出错，已跳过这一条", self.account)

    def _info(self, channel: dict[str, Any]) -> ChannelInfo:
        ref = ChannelRef(PLATFORM, self.account, "group", str(channel["id"]))
        name = str(channel.get("name") or channel["id"])
        return ChannelInfo(ref, f"#{name}" if not channel.get("is_mpim") else name)

    def _known(self) -> dict[str, str]:
        return {person: name for person, (name, _) in self._names.items()}

    # -- connection loop -------------------------------------------------------------------------

    async def _run(self) -> None:
        self._status.set("connecting", "正在连接")
        if not self._complete:
            self._status.set("auth_failed", "Bot Token 和 App Token 都要填")
            return
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._session()
                detail = "连接被关闭"
            except asyncio.CancelledError:
                raise
            except Again:
                if time.monotonic() - started > QUICK_SECONDS:
                    continue
                detail = "Slack 要求换一条连接"  # at once, again and again: not without a pause
            except Wrong as exc:
                self._status.set("misconfigured", str(exc))
                return
            except Unauthorized:
                self._status.set("auth_failed", "Slack 不认这两个 Token：到「平台接入」页填新的")
                return
            except MissingScope as exc:
                self._status.set("misconfigured", f"{self._api.clean(str(exc))}；加上以后重新安装应用")
                return
            except SlackError as exc:
                detail = self._api.clean(str(exc))
            except (OSError, TimeoutError, WebSocketException) as exc:
                detail = f"连不上 Slack（{type(exc).__name__}）"
            except Exception as exc:
                log.exception("Slack %s 连接出现未预期的错误", self.account)
                detail = f"内部错误：{type(exc).__name__}"
            if self._stopping:
                break
            wait = self._backoff.next(time.monotonic() - started)
            self._status.set("reconnecting", f"{detail}；{wait:.0f} 秒后重试" if wait >= 1 else detail)
            await asyncio.sleep(wait)

    async def _session(self) -> None:
        me = await self._api.me()
        if str(me.get("user_id")) != self.account:
            raise Wrong(f"这个 Token 属于 bot {me.get('user_id')}，不是 {self.account}")
        self._bot_id, self._name = str(me.get("bot_id") or ""), str(me.get("user") or "")
        url = await self._api.open_socket()
        async with websockets.connect(
            url, proxy=self._proxy, open_timeout=OPEN_TIMEOUT, max_size=MAX_FRAME, logger=quiet
        ) as ws:
            hello = _load(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
            if hello is None or hello.get("type") != "hello":
                raise self._ended(hello or {})
            connected_at = time.time()
            self._status.set("connected", self._name)
            self._catch_up_from(connected_at)
            try:
                async for raw in ws:
                    await self._frame(ws, raw)
            except ConnectionClosed:
                return

    async def _frame(self, ws: Any, raw: str | bytes) -> None:
        frame = _load(raw)
        if frame is None:
            log.warning("Slack %s 收到无法解析的数据，已跳过", self.account)
            return
        if envelope := frame.get("envelope_id"):
            await ws.send(json.dumps({"envelope_id": envelope}))
        if frame.get("type") == "disconnect":
            raise self._ended(frame)
        if frame.get("type") == "events_api":
            event = (frame.get("payload") or {}).get("event")
            if isinstance(event, dict):
                self._work.put_nowait(event)

    @staticmethod
    def _ended(frame: dict[str, Any]) -> Exception:
        return Wrong(NO_SOCKET_MODE) if frame.get("reason") == "link_disabled" else Again()

    # -- events ------------------------------------------------------------------------------------

    async def _events(self) -> None:
        """One after another, in the order they came; the socket is not kept waiting."""
        while True:
            event = await self._work.get()
            try:
                await self._event(event)
            except asyncio.CancelledError:
                raise
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                log.warning("Slack %s 跳过一条无法识别的消息：%s", self.account, type(exc).__name__)
            except Exception:  # one odd event must not end the rest
                log.exception("Slack %s 处理事件出错，已跳过", self.account)

    async def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "message":
            await self._message(event)
        elif kind == "member_joined_channel":
            person = str(event["user"])
            channel = ChannelRef(PLATFORM, self.account, "group", str(event["channel"]))
            if person == self.account:
                await self._named(channel.id, force=True)
                return
            name = await self._name_of(person)
            await self._deliver(
                MemberJoined(channel, person, name, float(event.get("event_ts") or time.time()))
            )
        elif kind == "channel_rename" and isinstance(event.get("channel"), dict):
            await self._title(event["channel"])

    async def _message(self, event: dict[str, Any]) -> None:
        channel = self._channel_of(event)
        if channel is None:
            return
        if event.get("subtype") == "message_deleted":
            gone = str(event.get("deleted_ts") or "")
            if gone:
                key = message_key(str(event["channel"]), gone)
                await self._deliver(RecallEvent(channel, key, float(event.get("ts") or time.time())))
            return
        key = message_key(str(event["channel"]), str(event["ts"]))
        if key in self._posted:
            return
        shaped = await self._shape(event, channel)
        if shaped is None:
            return
        if channel.kind == "group":
            await self._named(channel.id)
        if self._backfilling:
            self._seen_live.add(key)
        await self._deliver(shaped)

    async def _shape(
        self, event: dict[str, Any], channel: ChannelRef, *, backfilled: bool = False
    ) -> MessageEvent | None:
        for person in {str(event.get("user") or ""), *mentioned(str(event.get("text") or ""))}:
            if person and person != self.account and not backfilled:
                await self._name_of(person)
        shaped = normalize(event, channel, self._known(), own_bot=self._bot_id, backfilled=backfilled)
        if shaped is not None and shaped.reply_to:
            key = split_key(shaped.reply_to)
            if key is not None:
                self._threads[shaped.message_id] = key[1]
                while len(self._threads) > THREADS_KEPT:
                    self._threads.popitem(last=False)
        return shaped

    def _channel_of(self, event: dict[str, Any]) -> ChannelRef | None:
        place = str(event["channel"])
        if not is_direct(event):
            return ChannelRef(PLATFORM, self.account, "group", place)
        person = str(event.get("user") or "")
        if person and person != self.account:
            self._remember_dm(person, place)
        else:  # our own words, or a deletion: whose chat it is was learnt before
            person = self._people.get(place, "")
        return ChannelRef(PLATFORM, self.account, "private", person) if person else None

    async def _name_of(self, person: str) -> str:
        known = self._names.get(person)
        if known is not None and time.time() - known[1] < NAME_AGE:
            return known[0]
        name = known[0] if known else ""
        try:
            async with asyncio.timeout(LOOKUP_TIMEOUT):
                name = person_name(await self._api.user(person)) or name
        except (SlackError, TimeoutError) as exc:
            log.info("Slack %s 查不到成员的名字：%s", self.account, self._api.clean(str(exc)))
        self._names[person] = (name, time.time())
        while len(self._names) > NAMES_KEPT:
            self._names.popitem(last=False)
        return name

    async def _named(self, channel_id: str, *, force: bool = False) -> None:
        """Tell the channel's name, once (read with the list of all the bot is in)."""
        if channel_id in self._titles and not force:
            return
        self._titles.setdefault(channel_id, "")
        infos = await self.list_channels()
        if infos:
            await self._deliver(ChannelsSeen(tuple(infos)))

    async def _title(self, channel: dict[str, Any]) -> None:
        info = self._info(channel)
        self._titles[info.channel.id] = info.name
        await self._deliver(ChannelsSeen((info,)))

    # -- catching up -------------------------------------------------------------------------------

    def _catch_up_from(self, connected_at: float) -> None:
        if self._backfill_source is None:
            return
        if self._backfill is not None:
            self._backfill.cancel()
        self._seen_live = set()
        self._backfilling = True
        self._backfill = asyncio.create_task(
            self._catch_up(connected_at), name=f"slack:backfill:{self.account}"
        )

    async def _catch_up(self, connected_at: float) -> None:
        assert self._backfill_source is not None and self._emit is not None
        try:
            await catch_up(
                self._backfill_source,
                self._history,
                self._emit,
                account=self.account,
                label=f"Slack {self.account}",
                connected_at=connected_at,
                seen_live=lambda: self._seen_live,
            )
        finally:
            self._backfilling = False
            self._seen_live = set()

    async def _history(self, channel: ChannelRef, since: float) -> list[MessageEvent]:
        """What was said in a channel since then, oldest first (what was said inside threads is not there)."""
        events = []
        for message in reversed(await self._api.history(channel.id, since)):
            with contextlib.suppress(KeyError, TypeError, ValueError):
                event = {**message, "channel": channel.id}
                shaped = await self._shape(event, channel, backfilled=True)
                if shaped is not None:
                    events.append(shaped)
        return events


def _load(raw: str | bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
