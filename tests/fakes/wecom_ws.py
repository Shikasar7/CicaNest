"""In-process fake of 企业微信's long connection, on a real socket on 127.0.0.1, so the adapter under test
uses its real network code. Like the real one it answers each frame by req_id, and a second subscribe
kicks the first.

    async with FakeWecom(bot_id="aibxxx") as wecom:
        await wecom.push_text("@机器人 你好", chat="wrxxx", user="zhangsan")
        assert wecom.frames_of("aibot_respond_msg")
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect


class FakeWecom:
    def __init__(self, *, bot_id: str = "aibtest", secret: str = "fixture-secret") -> None:
        self.bot_id = bot_id
        self.secret = secret
        self.frames: list[dict[str, Any]] = []
        self.answer_pings = True
        self.subscribe_code = 0  # non-zero: subscribes are refused with this
        self.one_reply_per_callback = False  # a second aibot_respond_msg for a req_id is refused
        self.refuse: dict[str, tuple[int, str]] = {}  # cmd -> (errcode, errmsg)
        self.silent: set[str] = set()  # commands never answered
        self._answered: set[str] = set()
        self._subscribed: WebSocket | None = None
        self._clients: set[WebSocket] = set()
        self._next = 0
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._sock: socket.socket | None = None
        self.port = 0

    # -- lifecycle -------------------------------------------------------------------------------

    async def __aenter__(self) -> FakeWecom:
        app = Starlette(routes=[WebSocketRoute("/", self._ws)])
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
        self._task = asyncio.create_task(self._server.serve(sockets=[self._sock]))
        while not self._server.started:
            if self._task.done():
                self._task.result()
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
        assert self._server is not None and self._task is not None
        self._server.should_exit = True
        await self._task
        if self._sock is not None:
            self._sock.close()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/"

    # -- test controls ---------------------------------------------------------------------------

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    def frames_of(self, cmd: str) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame.get("cmd") == cmd]

    def req_id(self) -> str:
        self._next += 1
        return f"req-{self._next}"

    def callback(
        self,
        body: dict[str, Any],
        *,
        chat: str | None = "wrgroup",
        user: str = "zhangsan",
        msgid: str | None = None,
    ) -> dict[str, Any]:
        self._next += 1
        return {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": f"req-{self._next}"},
            "body": {
                "msgid": msgid or f"msg-{self._next}",
                "aibotid": self.bot_id,
                "chatid": chat or "",
                "chattype": "group" if chat else "single",
                "from": {"userid": user},
                **body,
            },
        }

    def text(self, content: str, **options: Any) -> dict[str, Any]:
        return self.callback({"msgtype": "text", "text": {"content": content}}, **options)

    async def push(self, frame: dict[str, Any] | str) -> None:
        text = frame if isinstance(frame, str) else json.dumps(frame, ensure_ascii=False)
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.send_text(text)

    async def kick(self) -> None:
        """Someone else subscribed with the same bot: what the connection that loses gets."""
        await self.push(
            {
                "cmd": "aibot_event_callback",
                "headers": {"req_id": self.req_id()},
                "body": {
                    "msgid": "event",
                    "aibotid": self.bot_id,
                    "msgtype": "event",
                    "event": {"eventtype": "disconnected_event"},
                },
            }
        )

    async def close(self, code: int = 1011) -> None:
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.close(code=code)
        self._clients.clear()

    # -- protocol --------------------------------------------------------------------------------

    async def _ws(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        try:
            while True:
                frame = json.loads(await websocket.receive_text())
                self.frames.append(frame)
                await self._on_frame(websocket, frame)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._clients.discard(websocket)

    async def _on_frame(self, websocket: WebSocket, frame: dict[str, Any]) -> None:
        cmd = frame.get("cmd")
        req_id = frame["headers"]["req_id"]
        if cmd == "aibot_subscribe":
            body = frame["body"]
            good = body.get("bot_id") == self.bot_id and body.get("secret") == self.secret
            code = self.subscribe_code or (0 if good else 40001)
            await self._answer(websocket, req_id, code, "ok" if not code else "invalid")
            if code == 0:
                old, self._subscribed = self._subscribed, websocket
                if old is not None and old is not websocket and old in self._clients:
                    await self.kick_one(old)
            return
        if cmd in self.silent or (cmd == "ping" and not self.answer_pings):
            return
        if cmd in self.refuse:
            code, message = self.refuse[cmd]
            await self._answer(websocket, req_id, code, message)
            return
        if cmd == "aibot_respond_msg" and self.one_reply_per_callback:
            if req_id in self._answered:
                await self._answer(websocket, req_id, 40058, "req_id already used")
                return
            self._answered.add(req_id)
        await self._answer(websocket, req_id, 0, "ok")

    async def kick_one(self, client: WebSocket) -> None:
        event = {"eventtype": "disconnected_event"}
        body = {"msgid": "event", "aibotid": self.bot_id, "msgtype": "event", "event": event}
        frame = {"cmd": "aibot_event_callback", "headers": {"req_id": self.req_id()}, "body": body}
        with contextlib.suppress(Exception):
            await client.send_text(json.dumps(frame))

    async def _answer(self, websocket: WebSocket, req_id: str, code: int, message: str) -> None:
        answer = {"headers": {"req_id": req_id}, "errcode": code, "errmsg": message}
        await websocket.send_text(json.dumps(answer))
