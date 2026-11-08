"""One 企业微信 智能机器人 over its long connection: nest connects out, so no
public address is needed. Replies go back over the same socket.

A bot has one connection at a time: a new subscribe kicks out the old one, which is told by a
disconnected_event. Whoever is kicked must stay down, or the two would kick each other forever, so that ends
in misconfigured until the user saves the connection again. For the same reason nothing here, or in a test of
the connection, may subscribe while the account is online.

The platform never shows a bot its own messages: what goes out is delivered as a from_self event here, or the
bot would not see what it said. Only text goes out, and only @s come in from groups.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from nest.adapters.base import AdapterStatus, EventSink, MediaError, StatusSink
from nest.adapters.common import Backoff, SentIds, StatusKeeper, split_text
from nest.adapters.wecom.normalize import PLATFORM, normalize
from nest.core.events import (
    Action,
    ActionResult,
    ChannelInfo,
    ChannelRef,
    Event,
    MessageEvent,
    Segment,
    SendMessage,
)
from nest.core.segments import render_text
from nest.llm.http import scrub

log = logging.getLogger(__name__)

# websockets logs every frame at debug level, and the subscribe frame holds the secret.
quiet = logging.getLogger("nest.adapters.wecom.frames")
quiet.setLevel(logging.WARNING)

WS_URL = "wss://openws.work.weixin.qq.com"
MAX_FRAME = 16 * 1024 * 1024
OPEN_TIMEOUT = 10.0
ACK_TIMEOUT = 10.0
PING_SECONDS = 30.0
MISSED_PINGS = 2
MAX_BYTES = 20_000  # the platform's limit is 20480, in bytes
REPLY_WINDOW = 24 * 3600.0  # a message can be answered this long
REMEMBERED = 2048
PER_MINUTE, PER_HOUR = 30, 1000  # per conversation, replies and pushes together
RETRY_CODES = (-1, 45009, 45033)  # busy or too fast: try again later
KICKED = (
    "这个企业微信机器人在别处连上了，nest 这边被顶了下来，没有再连：两边抢会一直互相踢。"
    "确认只有一处在用这个 BotID 后，到「系统」页保存一次连接设置"
)


class Kicked(Exception):
    pass


class Refused(Exception):
    """The platform answered with an error."""

    def __init__(self, code: Any, message: str) -> None:
        super().__init__(f"企业微信拒绝了（{code}）：{message}")
        self.code = code


def frame(cmd: str, req_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"cmd": cmd, "headers": {"req_id": req_id}}
    if body is not None:
        message["body"] = body
    return message


def new_id() -> str:
    return uuid.uuid4().hex


def subscribe_frame(bot_id: str, secret: str) -> dict[str, Any]:
    return frame("aibot_subscribe", new_id(), {"bot_id": bot_id, "secret": secret})


def connect(url: str) -> Any:
    # proxy=None: 企业微信 is reached directly. The default would read the environment and system settings.
    return websockets.connect(
        url, proxy=None, ping_interval=None, open_timeout=OPEN_TIMEOUT, max_size=MAX_FRAME, logger=quiet
    )


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


class WecomAdapter:
    platform = PLATFORM
    # proactive: only into a conversation someone has spoken in before, the platform refuses the rest.
    capabilities = frozenset({"proactive", "send_text"})
    max_text = MAX_BYTES
    stuck_send_at = 0.0  # a NapCat thing

    def __init__(
        self,
        *,
        account: str,
        secret: str,
        name: str = "",
        on_status: StatusSink | None = None,
        url: str = WS_URL,
        backoff_initial: float = 5.0,  # subscribing too often is refused
        backoff_max: float = 300.0,
        stable_after: float = 60.0,
        ping_seconds: float = PING_SECONDS,
        ack_timeout: float = ACK_TIMEOUT,
    ) -> None:
        self.account = account
        self._secret = secret
        self._name = name or account
        self._url = url
        self._status = StatusKeeper(f"企业微信 {account}", on_status)
        self._backoff = Backoff(backoff_initial, backoff_max, stable_after)
        self._ping_seconds = ping_seconds
        self._ack_timeout = ack_timeout
        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._emit: EventSink | None = None
        self._stopping = False
        self._waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}  # req_id -> the ack of what was sent
        self._sending = asyncio.Lock()  # a reply reuses its callback's req_id: one in flight at a time
        self._sent = SentIds()
        self._callbacks: OrderedDict[str, tuple[str, float]] = OrderedDict()  # msgid -> (req_id, when)
        self._spoken: dict[str, deque[float]] = {}  # conversation -> when something went out
        self.subscribes = 0

    # -- Adapter protocol --------------------------------------------------------------------------

    async def start(self, emit: EventSink) -> None:
        if self._task is None:
            self._stopping = False
            self._emit = emit
            self._task = asyncio.create_task(self._run(), name=f"wecom:{self.account}")

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._status.set("stopped", "已停止")

    def status(self) -> AdapterStatus:
        return self._status.current

    def sent_by_us(self, message_id: str) -> bool:
        return message_id in self._sent

    async def list_channels(self) -> list[ChannelInfo]:
        return []  # the platform has no such list: a group turns up when someone first @s the bot

    async def fetch_media(self, url: str, limit: int) -> bytes:
        raise MediaError("企业微信的图片是加密的，现在还读不了")

    async def perform(self, action: Action) -> ActionResult:
        """Send text. "failed" = it certainly did not go out; "unknown" = it may have. Never retried."""
        if not isinstance(action, SendMessage) or action.channel.account != self.account:
            return ActionResult("failed", error="这条消息不属于这个账号")
        current = self._status.current
        if current.state != "connected":
            reason = current.detail or current.state
            return ActionResult("failed", error=f"企业微信当前不在线（{reason}），没有发送")
        words = "".join(str(s.data.get("text", "")) for s in action.segments if s.type == "text")
        pieces = split_text(words.strip(), MAX_BYTES, _bytes)
        if not pieces:
            return ActionResult("failed", error="这条消息里没有企业微信能发的文字")
        sent: list[str] = []
        for piece in pieces:
            if not self._may_speak(action.channel):
                return self._stopped_at("这个会话发得太多了，先停一停", sent, sure=True)
            try:
                await self._say(action, piece)
            except Refused as exc:
                return self._stopped_at(self._clean(str(exc)), sent, sure=True)
            except (TimeoutError, ConnectionClosed, OSError) as exc:
                why = f"发送中途出错（{type(exc).__name__}），不确定是否已发出"
                return self._stopped_at(why, sent, sure=False)
            sent.append(await self._deliver_own(action.channel, piece))
        return ActionResult("succeeded", message_id=sent[0])

    def _stopped_at(self, error: str, sent: list[str], *, sure: bool) -> ActionResult:
        if sent:
            return ActionResult(
                "unknown", message_id=sent[0], error=f"长消息只发出了前 {len(sent)} 段：{error}"
            )
        return ActionResult("failed" if sure else "unknown", error=error)

    def _may_speak(self, channel: ChannelRef) -> bool:
        now = time.time()
        times = self._spoken.setdefault(channel.key, deque())
        while times and times[0] < now - 3600:
            times.popleft()
        if len(times) >= PER_HOUR or sum(at > now - 60 for at in times) >= PER_MINUTE:
            return False
        times.append(now)
        return True

    async def _say(self, action: SendMessage, text: str) -> None:
        """As a reply to the message it answers while that can still be answered; else as a push. A refused
        reply is pushed too: whether one callback takes several replies is not documented."""
        known = self._callbacks.get(action.trigger or "")
        if known is not None and time.time() - known[1] < REPLY_WINDOW:
            stream = {"id": new_id(), "finish": True, "content": text}
            reply = frame("aibot_respond_msg", known[0], {"msgtype": "stream", "stream": stream})
            try:
                await self._request(reply)
                return
            except Refused as exc:
                log.info("企业微信 %s 回复被拒（%s），改为主动发", self.account, exc.code)
        kind = 2 if action.channel.kind == "group" else 1
        body = {
            "chatid": action.channel.id,
            "chat_type": kind,
            "msgtype": "markdown",
            "markdown": {"content": text},
        }
        await self._request(frame("aibot_send_msg", new_id(), body))

    async def _deliver_own(self, channel: ChannelRef, text: str) -> str:
        """No echo here: this is the only way the message reaches storage, and the bot's own context."""
        message_id = f"self:{new_id()}"
        self._sent.add(message_id)
        segments = (Segment("text", {"text": text}),)
        event = MessageEvent(
            channel=channel,
            message_id=message_id,
            sender_id=self.account,
            sender_name=self._name,
            segments=segments,
            text=render_text(segments),
            timestamp=time.time(),
            from_self=True,
        )
        await self._deliver(event)
        return message_id

    async def _deliver(self, event: Event) -> None:
        assert self._emit is not None
        try:
            await self._emit(event)
        except Exception:
            log.exception("企业微信 %s 处理事件出错，已跳过这一条", self.account)

    def _clean(self, text: str) -> str:
        return scrub(text, self._secret)

    # -- connection loop -------------------------------------------------------------------------

    async def _run(self) -> None:
        self._status.set("connecting", "正在连接")
        if not self._secret:
            self._status.set("auth_failed", "还没有填 Secret")
            return
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._session()
                detail = "连接被关闭"
            except asyncio.CancelledError:
                raise
            except Kicked:
                self._status.set("misconfigured", KICKED)
                return
            except Refused as exc:
                detail = self._clean(str(exc))
                if exc.code not in RETRY_CODES:
                    self._status.set("auth_failed", f"BotID 或 Secret 不对：{detail}")
                    return
            except (OSError, TimeoutError, WebSocketException) as exc:
                detail = f"连不上企业微信（{type(exc).__name__}）"
            except Exception as exc:
                log.exception("企业微信 %s 连接出现未预期的错误", self.account)
                detail = f"内部错误：{type(exc).__name__}"
            finally:
                self._ws = None
                self._drop_waiting()
            if self._stopping:
                break
            wait = self._backoff.next(time.monotonic() - started)
            self._status.set("reconnecting", f"{detail}；{wait:.0f} 秒后重试" if wait >= 1 else detail)
            await asyncio.sleep(wait)

    def _drop_waiting(self) -> None:
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(ConnectionError("连接断了"))
        self._waiting.clear()

    async def _session(self) -> None:
        async with connect(self._url) as ws:
            self._ws = ws
            await self._subscribe(ws)
            self._status.set("connected", self._name)
            pings = asyncio.create_task(self._ping(ws), name=f"wecom:ping:{self.account}")
            try:
                async for raw in ws:
                    await self._frame(raw)
            finally:
                pings.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pings

    async def _subscribe(self, ws: Any) -> None:
        self.subscribes += 1
        request = subscribe_frame(self.account, self._secret)
        await ws.send(json.dumps(request))
        async with asyncio.timeout(self._ack_timeout):
            while True:
                answer = _load(await ws.recv())
                if (
                    answer is not None
                    and answer.get("headers", {}).get("req_id") == request["headers"]["req_id"]
                ):
                    break
        if answer.get("errcode"):
            raise Refused(answer.get("errcode"), str(answer.get("errmsg") or ""))

    async def _ping(self, ws: Any) -> None:
        missed = 0
        while True:
            await asyncio.sleep(self._ping_seconds)
            try:
                await self._request(frame("ping", new_id()))
                missed = 0
            except (TimeoutError, Refused):
                missed += 1
            except (ConnectionError, ConnectionClosed):
                return
            if missed >= MISSED_PINGS:
                log.info("企业微信 %s 不回应 ping，换一条连接", self.account)
                with contextlib.suppress(Exception):
                    await ws.close()
                return

    async def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a frame and wait for its answer. Raises Refused, TimeoutError, or a closed socket's error."""
        async with self._sending:
            ws = self._ws
            if ws is None:
                raise ConnectionError("没有连接")
            req_id = message["headers"]["req_id"]
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._waiting[req_id] = future
            try:
                await ws.send(json.dumps(message, ensure_ascii=False))
                answer = await asyncio.wait_for(future, self._ack_timeout)
            finally:
                self._waiting.pop(req_id, None)
        if answer.get("errcode"):
            raise Refused(answer.get("errcode"), str(answer.get("errmsg") or ""))
        return answer

    async def _frame(self, raw: str | bytes) -> None:
        payload = _load(raw)
        if payload is None:
            log.warning("企业微信 %s 收到无法解析的数据，已跳过", self.account)
            return
        cmd = payload.get("cmd")
        if cmd is None:  # the answer to something sent
            future = self._waiting.get(str(payload.get("headers", {}).get("req_id")))
            if future is not None and not future.done():
                future.set_result(payload)
        elif cmd == "aibot_msg_callback":
            await self._message(payload)
        elif cmd == "aibot_event_callback":
            event = (payload.get("body") or {}).get("event") or {}
            if event.get("eventtype") == "disconnected_event":
                raise Kicked

    async def _message(self, payload: dict[str, Any]) -> None:
        try:
            body = payload["body"]
            msgid = str(body["msgid"])
            if msgid in self._callbacks:  # sent again: the platform did not hear us take it
                return
            self._callbacks[msgid] = (str(payload["headers"]["req_id"]), time.time())
            while len(self._callbacks) > REMEMBERED:
                self._callbacks.popitem(last=False)
            event = normalize(body, self.account)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("企业微信 %s 跳过一条无法识别的消息：%s", self.account, exc)
            return
        if event is not None:
            await self._deliver(event)


def _load(raw: str | bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
