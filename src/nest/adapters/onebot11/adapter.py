"""One QQ account through a OneBot v11 bridge (SnowLuma): forward WebSocket for events, HTTP for API calls.

Lifecycle per connection attempt:
  1. HTTP get_login_info — the endpoint must belong to this account (otherwise: misconfigured, no events used)
  2. open the WebSocket and deliver events; a bad frame is logged and skipped, never fatal
  3. in parallel, backfill recent history of known channels (messages sent before this connection)
On any failure: wait with exponential backoff (+jitter) and try again, until stop().
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import httpx
import websockets

from nest.adapters.base import AdapterState, AdapterStatus, BackfillSource, EventSink, StatusSink
from nest.adapters.common import Backoff, catch_up, download
from nest.adapters.onebot11.normalize import normalize, outbound
from nest.core.events import (
    Action,
    ActionResult,
    ChannelInfo,
    ChannelRef,
    MessageEvent,
    Segment,
    SendMessage,
)
from nest.core.segments import render_text
from nest.watch import fetch as watch_fetch

log = logging.getLogger(__name__)

MEMBER_NAME_TTL = 6 * 3600.0  # a member's group name; also refreshed whenever they speak
MEMBER_MISS_TTL = 300.0  # a lookup that failed or found no name is tried again after this long
MEMBER_LOOKUP_TIMEOUT = 3.0  # events wait for the lookup, so it must stay short
MEMBER_CACHE_MAX = 4096
UPLOAD_CHARS_PER_SECOND = 500_000  # of a base64 file: about 3 Mbit/s, on the slow side
VOICE_TRIES = 3  # QQ's first answer is often "failed"; asked again the words are there
VOICE_RETRY_WAIT = 0.5
VOICE_TIMEOUT = 5.0  # events wait for the words, so it must stay short


class AuthFailed(Exception):
    pass


class Misconfigured(Exception):
    pass


class OneBotError(Exception):
    def __init__(self, action: str, retcode: Any, message: str) -> None:
        super().__init__(f"{action} 失败（{retcode}）：{message}")
        self.retcode = retcode


def stuck_send(detail: str) -> bool:
    """NapCat's answer when QQ took a message and the sign that it went out never came back: a client
    that gets no messages (nest.core.deaf) fails like this."""
    return "EventChecker Failed" in detail and "NodeIKernelMsgService/sendMsg" in detail


def send_timeout(base: float, segments: Iterable[Segment]) -> float:
    """How long a send may take: QQ has to take in a big picture or voice message first."""
    return base + sum(len(str(s.data.get("file") or "")) for s in segments) / UPLOAD_CHARS_PER_SECOND


class OneBotAdapter:
    platform = "qq"
    capabilities = frozenset(
        {
            "fetch_history",
            "list_channels",
            "mention",
            "proactive",
            "reply",
            "sees_all",
            "self_echo",
            "send_image",
            "send_text",
            "send_voice",
            "sticker_source",
        }
    )
    max_text = 0

    def __init__(
        self,
        *,
        account: str,
        http_url: str,
        ws_url: str,
        http_token: str = "",
        ws_token: str = "",
        on_status: StatusSink | None = None,
        backfill_source: BackfillSource | None = None,
        history_count: int = 100,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        stable_after: float = 60.0,
        request_timeout: float = 10.0,
    ) -> None:
        self.account = account
        self.http_url = http_url.rstrip("/")
        self.ws_url = ws_url
        self._http_token = http_token
        self._ws_token = ws_token
        self._on_status = on_status
        self._backfill_source = backfill_source
        self._history_count = history_count
        self._backoff = Backoff(backoff_initial, backoff_max, stable_after)
        self._request_timeout = request_timeout
        self._http = httpx.AsyncClient(timeout=request_timeout, trust_env=False)
        self._status = AdapterStatus("stopped", "未启动", time.time())
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        self._stopping = False
        self._seen_live: set[str] = set()  # live message ids, only tracked while a backfill runs
        self._backfilling = False
        self.skipped_frames = 0
        self.attempts = 0  # connection attempts since start()
        self._sent_ids: deque[str] = deque(maxlen=256)  # message ids nest sent recently
        self._sending_until = 0.0
        self.stuck_send_at = 0.0  # see stuck_send; a send that works or a new connection clears it
        self._member_names: dict[tuple[str, str], tuple[str, float]] = {}  # (group, qq) -> (name, expires)

    # -- Adapter protocol --------------------------------------------------------------------------

    async def start(self, emit: EventSink) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(emit), name=f"onebot:{self.account}")

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
        await self._http.aclose()
        self._set_status("stopped", "已停止")

    async def perform(self, action: Action) -> ActionResult:
        """Send a message. "failed" = it certainly did not go out; "unknown" = it may have. Never retried."""
        if not isinstance(action, SendMessage) or action.channel.account != self.account:
            return ActionResult("failed", error="这条消息不属于这个账号")
        if self._status.state != "connected":
            reason = self._status.detail or self._status.state
            return ActionResult("failed", error=f"QQ 当前不在线（{reason}），没有发送")
        channel = action.channel
        if channel.kind == "group":
            api, target = "send_group_msg", {"group_id": int(channel.id)}
        else:
            api, target = "send_private_msg", {"user_id": int(channel.id)}
        body = {**target, "message": [outbound(s) for s in action.segments]}
        headers = {"authorization": f"Bearer {self._http_token}"} if self._http_token else {}
        timeout = send_timeout(self._request_timeout, action.segments)
        # Our own message comes back on the event stream, sometimes before this request returns.
        self._sending_until = time.time() + timeout + 5
        try:
            response = await self._http.post(
                f"{self.http_url}/{api}", json=body, headers=headers, timeout=timeout
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return ActionResult("failed", error=f"连不上 {self.http_url}，没有发出（{type(exc).__name__}）")
        except httpx.HTTPError as exc:
            error = f"发送请求中途出错（{type(exc).__name__}），不确定是否已发出"
            return ActionResult("unknown", error=error)
        finally:
            self._sending_until = time.time() + 3
        if response.status_code in (401, 403):
            return ActionResult("failed", error="HTTP 接口拒绝了令牌，没有发出")
        try:
            data = response.json()
        except ValueError:
            return ActionResult("unknown", error=f"QQ 返回了看不懂的结果（HTTP {response.status_code}）")
        if data.get("status") == "ok" or data.get("retcode") == 0:
            message_id = (data.get("data") or {}).get("message_id")
            if message_id is not None:
                self._sent_ids.append(str(message_id))
            self.stuck_send_at = 0.0
            return ActionResult("succeeded", message_id=None if message_id is None else str(message_id))
        detail = str(data.get("message") or data.get("wording") or "")
        if stuck_send(detail):
            self.stuck_send_at = time.time()
        return ActionResult("failed", error=f"QQ 拒绝了这条消息（{data.get('retcode')}）：{detail}")

    def sent_by_us(self, message_id: str) -> bool:
        """Whether a message from our own account was sent by nest (or arrived while a send was in flight)."""
        return message_id in self._sent_ids or time.time() < self._sending_until

    async def fetch_media(self, url: str, limit: int) -> bytes:
        transport = watch_fetch.network_transport(None)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
            return await download(http, url, limit)

    async def list_channels(self) -> list[ChannelInfo]:
        groups = await self.call("get_group_list")
        return [
            ChannelInfo(
                ChannelRef("qq", self.account, "group", str(g["group_id"])), str(g.get("group_name") or "")
            )
            for g in groups or []
            if isinstance(g, dict) and "group_id" in g
        ]

    def status(self) -> AdapterStatus:
        return self._status

    # -- HTTP API --------------------------------------------------------------------------------

    async def call(self, action: str, **params: Any) -> Any:
        headers = {"authorization": f"Bearer {self._http_token}"} if self._http_token else {}
        try:
            response = await self._http.post(f"{self.http_url}/{action}", json=params, headers=headers)
        except httpx.HTTPError as exc:
            log.debug("QQ %s 请求 %s 失败：%r", self.account, action, exc)
            raise ConnectionError(f"连不上 {self.http_url}（QQ 可能没有登录）") from exc
        if response.status_code in (401, 403):
            raise AuthFailed("HTTP 接口拒绝了令牌")
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "ok" and body.get("retcode") not in (0, None):
            raise OneBotError(
                action, body.get("retcode"), str(body.get("message") or body.get("wording") or "")
            )
        return body.get("data")

    # -- connection loop -------------------------------------------------------------------------

    def _set_status(self, state: AdapterState, detail: str = "") -> None:
        if (state, detail) == (self._status.state, self._status.detail):
            return
        self._status = AdapterStatus(state, detail, time.time())
        log.info("QQ %s：%s %s", self.account, state, detail)
        if self._on_status is not None:
            try:
                self._on_status(self._status)
            except Exception:
                log.exception("状态回调出错")

    async def _run(self, emit: EventSink) -> None:
        attempt = 0
        while not self._stopping:
            attempt += 1
            self.attempts += 1
            started = time.monotonic()
            if attempt == 1:
                self._set_status("connecting", "正在连接")
            # On retries the status keeps the last failure reason until the outcome is known.
            try:
                await self._session(emit)
                detail = "连接被关闭"
                state: AdapterState = "reconnecting"
            except asyncio.CancelledError:
                raise
            except AuthFailed as exc:
                state, detail = "auth_failed", f"令牌错误：{exc}"
            except Misconfigured as exc:
                state, detail = "misconfigured", str(exc)
            except websockets.InvalidStatus as exc:
                code = exc.response.status_code
                if code in (401, 403):
                    state, detail = "auth_failed", "令牌错误：WebSocket 拒绝了令牌"
                else:
                    state, detail = "reconnecting", f"WebSocket 握手失败（HTTP {code}）"
            except (
                OSError,
                TimeoutError,
                websockets.WebSocketException,
                ConnectionError,
                OneBotError,
            ) as exc:
                state, detail = "reconnecting", str(exc) or type(exc).__name__
            except Exception as exc:
                log.exception("QQ %s 连接出现未预期的错误", self.account)
                state, detail = "reconnecting", f"内部错误：{type(exc).__name__}"
            finally:
                self._ws = None
            if self._stopping:
                break
            wait = self._backoff.next(time.monotonic() - started)
            self._set_status(state, f"{detail}；{wait:.0f} 秒后重试" if wait >= 1 else detail)
            await asyncio.sleep(wait)

    async def _session(self, emit: EventSink) -> None:
        login = await self.call("get_login_info")
        if str((login or {}).get("user_id")) != self.account:
            raise Misconfigured(f"这个地址登录的是 QQ {(login or {}).get('user_id')}，不是 {self.account}")
        headers = {"Authorization": f"Bearer {self._ws_token}"} if self._ws_token else {}
        async with websockets.connect(
            self.ws_url,
            additional_headers=headers,
            proxy=None,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        ) as ws:
            self._ws = ws
            connected_at = time.time()
            self._seen_live = set()
            self._backfilling = self._backfill_source is not None
            self.stuck_send_at = 0.0
            self._set_status("connected", str(login.get("nickname") or ""))
            backfill = asyncio.create_task(self._backfill(emit, connected_at))
            try:
                async for frame in ws:
                    await self._handle_frame(frame, emit)
            finally:
                backfill.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await backfill

    async def _handle_frame(self, frame: str | bytes, emit: EventSink) -> None:
        try:
            payload = json.loads(frame)
        except ValueError:
            self.skipped_frames += 1
            log.warning("QQ %s 收到无法解析的数据，已跳过", self.account)
            return
        if not isinstance(payload, dict) or "post_type" not in payload:
            return  # API responses or noise; nest uses HTTP for calls
        if payload["post_type"] == "meta_event":
            self._meta(payload)
            return
        if str(payload.get("self_id", self.account)) != self.account:
            log.warning("QQ %s 收到属于 %s 的事件，已忽略", self.account, payload.get("self_id"))
            return
        try:
            await self._name_mentions(payload)
            event = normalize(payload, self.account)
        except (KeyError, TypeError, ValueError) as exc:
            self.skipped_frames += 1
            log.warning("QQ %s 跳过一条无法识别的事件：%s", self.account, exc)
            return
        if event is None:
            return
        if isinstance(event, MessageEvent):
            if self._backfilling:
                self._seen_live.add(event.message_id)
            event = await self._with_words(event)
        try:
            await emit(event)
        except Exception:
            log.exception("QQ %s 处理事件出错，已跳过这一条", self.account)

    async def _with_words(self, event: MessageEvent) -> MessageEvent:
        """A voice message with what was said, by QQ's own speech-to-text; as it was if QQ cannot tell."""
        if event.from_self or not any(s.type == "record" for s in event.segments):
            return event
        try:
            async with asyncio.timeout(VOICE_TIMEOUT):
                words = await self._voice_words(event.message_id)
        except (TimeoutError, ConnectionError, AuthFailed, httpx.HTTPError, ValueError) as exc:
            log.info("QQ %s 没听出语音：%r", self.account, exc)
            return event
        if not words:
            log.info("QQ %s 没听出语音 %s", self.account, event.message_id)
            return event
        segments = tuple(
            Segment(s.type, {**s.data, "words": words}) if s.type == "record" else s for s in event.segments
        )
        return replace(event, segments=segments, text=render_text(segments))

    async def _voice_words(self, message_id: str) -> str:
        for attempt in range(VOICE_TRIES):
            if attempt:
                await asyncio.sleep(VOICE_RETRY_WAIT)
            try:
                data = await self.call("fetch_ptt_text", message_id=message_id)
            except OneBotError:
                continue
            return str((data or {}).get("text") or "").strip()
        return ""

    async def _name_mentions(self, payload: dict[str, Any]) -> None:
        """QQ sends an @ as a bare QQ number; fill in the member's group name so the text reads "@阿鲸"."""
        if payload.get("message_type") != "group" or not isinstance(payload.get("message"), list):
            return
        try:
            group = str(payload.get("group_id"))
            sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
            if payload.get("user_id") is not None and (sender.get("card") or sender.get("nickname")):
                self._remember(
                    group, str(payload["user_id"]), str(sender.get("card") or sender.get("nickname"))
                )
            for item in payload["message"]:
                data = item.get("data") if isinstance(item, dict) and item.get("type") == "at" else None
                if (
                    isinstance(data, dict)
                    and data.get("qq") not in (None, "", "all")
                    and not data.get("name")
                ):
                    name = await self._member_name(group, str(data["qq"]))
                    if name:
                        data["name"] = name
        except Exception:  # names are a nicety: the message still goes through with numbers
            log.exception("QQ %s 补全 @ 名字出错", self.account)

    async def _member_name(self, group: str, qq: str) -> str:
        cached = self._member_names.get((group, qq))
        if cached is not None and cached[1] > time.time():
            return cached[0]
        try:
            info = await asyncio.wait_for(
                self.call("get_group_member_info", group_id=int(group), user_id=int(qq)),
                MEMBER_LOOKUP_TIMEOUT,
            )
        except (ConnectionError, AuthFailed, OneBotError, httpx.HTTPError, TimeoutError, ValueError) as exc:
            log.debug("QQ %s 查不到群 %s 成员 %s 的名字：%r", self.account, group, qq, exc)
            info = None
        name = str(info.get("card") or info.get("nickname") or "") if isinstance(info, dict) else ""
        self._remember(group, qq, name)
        return name

    def _remember(self, group: str, qq: str, name: str) -> None:
        if len(self._member_names) >= MEMBER_CACHE_MAX:
            self._member_names.clear()
        ttl = MEMBER_NAME_TTL if name else MEMBER_MISS_TTL
        self._member_names[(group, qq)] = (name, time.time() + ttl)

    def _meta(self, payload: dict[str, Any]) -> None:
        if payload.get("meta_event_type") != "heartbeat":
            return
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        if status.get("online") is False or status.get("good") is False:
            self._set_status("offline", "QQ 客户端离线")
        elif self._status.state == "offline":
            self._set_status("connected", "")

    async def _backfill(self, emit: EventSink, connected_at: float) -> None:
        if self._backfill_source is None:
            return
        try:
            await catch_up(
                self._backfill_source,
                self._group_history,
                emit,
                account=self.account,
                label=f"QQ {self.account}",
                connected_at=connected_at,
                seen_live=lambda: self._seen_live,
                prepare=self._with_words,
            )
        finally:
            self._backfilling = False
            self._seen_live = set()

    async def _group_history(self, channel: ChannelRef, since: float) -> list[MessageEvent]:
        data = await self.call("get_group_msg_history", group_id=int(channel.id), count=self._history_count)
        events: list[MessageEvent] = []
        for payload in (data or {}).get("messages") or []:
            if not isinstance(payload, dict):
                continue
            # SnowLuma's history entries carry no post_type (verified against the real bridge);
            # they are messages by definition.
            payload = {"post_type": "message", **payload}
            try:
                await self._name_mentions(payload)
                event = normalize(payload, self.account, backfilled=True)
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(event, MessageEvent):
                events.append(event)
        return events
