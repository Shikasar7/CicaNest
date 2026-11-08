"""One connection to Discord's Gateway at a time: Hello, heartbeats, Identify or Resume, and what it means
when it closes. Reconnecting, and how long to wait first, is the adapter's business.

The session survives a dropped connection: session_id, resume_gateway_url and the last sequence number are
kept here, and the next run() resumes, which Discord answers by replaying what was missed. Resuming costs no
Identify. A missed heartbeat ACK means the connection is dead though the socket looks fine: it is closed with
a code other than 1000 or 1001, the two that would end the session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from nest.adapters.discord.rest import USER_AGENT

log = logging.getLogger(__name__)

# websockets logs every frame at debug level, and the Identify frame holds the token.
quiet = logging.getLogger("nest.adapters.discord.frames")
quiet.setLevel(logging.WARNING)

INTENTS = (
    (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)
)  # GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT
VERSION = "10"
MAX_FRAME = 16 * 1024 * 1024
OPEN_TIMEOUT = 15.0
HELLO_TIMEOUT = 15.0
RESUMABLE_CLOSE = 4000  # how nest ends a connection it wants to resume

DISPATCH, HEARTBEAT, IDENTIFY, RESUME, RECONNECT, INVALID_SESSION, HELLO, HEARTBEAT_ACK = (
    0,
    1,
    2,
    6,
    7,
    9,
    10,
    11,
)

MESSAGE_CONTENT_HINT = (
    "Discord 不让收消息内容：到 Developer Portal 的 Bot 页打开 Message Content Intent，再到「系统」页保存一次"
)
# Close codes that retrying cannot fix. Reconnecting after these only burns the Identify allowance.
FATAL = {
    4004: ("auth_failed", "Discord 不认这个 Token：到 Developer Portal 的 Bot 页重置，再到「系统」页填新的"),
    4010: ("misconfigured", "Discord 说分片设置不对（4010）"),
    4011: ("misconfigured", "这个 bot 的服务器太多，Discord 要求分片（4011），nest 还不支持"),
    4012: ("misconfigured", "Discord 说接口版本不对（4012）：nest 要升级"),
    4013: ("misconfigured", "Discord 说 Intent 写得不对（4013）：nest 要升级"),
    4014: ("misconfigured", MESSAGE_CONTENT_HINT),
}
SESSION_OVER = {4007, 4009}  # invalid sequence, session timed out: connect again, but with a new session


class Fatal(Exception):
    """Discord ended this and retrying will not help."""

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


class Dropped(Exception):
    """The connection ended; the adapter connects again. `detail` is for the console."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


Dispatch = Callable[[str, dict[str, Any]], Awaitable[None]]


class Gateway:
    def __init__(self, token: str, on_dispatch: Dispatch, *, proxy: str | None = None) -> None:
        self._token = token
        self._on_dispatch = on_dispatch
        self._proxy = proxy
        self.session_id = ""
        self.resume_url = ""
        self.seq: int | None = None
        self.skipped_frames = 0
        self.heartbeats = 0  # sent on this adapter's lifetime
        self._ws: Any = None
        self._acked = True

    @property
    def resumable(self) -> bool:
        return bool(self.session_id and self.resume_url)

    def forget_session(self) -> None:
        self.session_id = self.resume_url = ""
        self.seq = None

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()

    async def run(self, url: str) -> None:
        """One connection, until it ends: always raises Dropped or Fatal."""
        target = f"{url.rstrip('/')}/?v={VERSION}&encoding=json"
        # proxy must be explicit: the default reads the environment and the system settings.
        async with websockets.connect(
            target,
            proxy=self._proxy,
            ping_interval=None,  # Discord has its own heartbeat
            open_timeout=OPEN_TIMEOUT,
            close_timeout=2,
            max_size=MAX_FRAME,
            user_agent_header=USER_AGENT,
            logger=quiet,
        ) as ws:
            self._ws = ws
            beat: asyncio.Task[None] | None = None
            try:
                hello = await asyncio.wait_for(self._hello(ws), HELLO_TIMEOUT)
                self._acked = True
                beat = asyncio.create_task(self._beat(ws, hello / 1000), name="discord:heartbeat")
                await ws.send(json.dumps(self._greeting()))
                async for frame in ws:
                    await self._frame(ws, frame)
                self._closed(ws.close_code)
            except ConnectionClosed as exc:
                self._closed(exc.rcvd.code if exc.rcvd is not None else None)
            finally:
                if beat is not None:
                    beat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await beat
                self._ws = None

    async def _hello(self, ws: Any) -> float:
        while True:
            payload = _load(await ws.recv())
            if payload is not None and payload.get("op") == HELLO:
                return float(payload["d"]["heartbeat_interval"])

    def _greeting(self) -> dict[str, Any]:
        if self.resumable:
            return {
                "op": RESUME,
                "d": {"token": self._token, "session_id": self.session_id, "seq": self.seq},
            }
        return {
            "op": IDENTIFY,
            "d": {
                "token": self._token,
                "intents": INTENTS,
                "properties": {"os": "linux", "browser": "nest", "device": "nest"},
            },
        }

    async def _beat(self, ws: Any, interval: float) -> None:
        """The first heartbeat comes a random part of the interval after Hello, so a restart of everything
        does not make everyone beat at once."""
        await asyncio.sleep(interval * random.random())
        while True:
            if not self._acked:
                log.info("Discord 没有回应心跳，换一条连接")
                with contextlib.suppress(Exception):
                    await ws.close(code=RESUMABLE_CLOSE, reason="no heartbeat ack")
                return
            self._acked = False
            await self._heartbeat(ws)
            await asyncio.sleep(interval)

    async def _heartbeat(self, ws: Any) -> None:
        self.heartbeats += 1
        with contextlib.suppress(ConnectionClosed):
            await ws.send(json.dumps({"op": HEARTBEAT, "d": self.seq}))

    async def _frame(self, ws: Any, frame: str | bytes) -> None:
        payload = _load(frame)
        if payload is None:
            self.skipped_frames += 1
            log.warning("Discord 发来无法解析的数据，已跳过")
            return
        op = payload.get("op")
        if op == DISPATCH:
            if payload.get("s") is not None:
                self.seq = payload["s"]
            await self._dispatch(str(payload.get("t")), payload.get("d"))
        elif op == HEARTBEAT:
            await self._heartbeat(ws)
        elif op == HEARTBEAT_ACK:
            self._acked = True
        elif op == RECONNECT:
            with contextlib.suppress(Exception):
                await ws.close(code=RESUMABLE_CLOSE, reason="reconnect requested")
            raise Dropped("Discord 要求重连")
        elif op == INVALID_SESSION:
            if not payload.get("d"):
                self.forget_session()
            with contextlib.suppress(Exception):
                await ws.close(code=RESUMABLE_CLOSE, reason="invalid session")
            raise Dropped("Discord 说这个会话失效了")

    async def _dispatch(self, kind: str, data: Any) -> None:
        if not isinstance(data, dict):
            data = {}
        if kind == "READY":
            self.session_id = str(data.get("session_id") or "")
            self.resume_url = str(data.get("resume_gateway_url") or "")
        await self._on_dispatch(kind, data)

    def _closed(self, code: int | None) -> None:
        if code in FATAL:
            state, detail = FATAL[code]
            raise Fatal(state, detail)
        if code in SESSION_OVER:
            self.forget_session()
        raise Dropped(f"Discord 断开了连接（{code}）" if code else "和 Discord 的连接断了")


def _load(frame: str | bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(frame)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
