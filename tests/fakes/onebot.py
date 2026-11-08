"""In-process fake OneBot v11 endpoint, shaped like one SnowLuma account.

Real sockets on 127.0.0.1 (HTTP API + forward WebSocket, "Universal" role), so adapters under test use
their real network code. Tests drive it: push events, drop connections, inspect API calls.

    async with FakeOneBot(self_id=10001) as bot:
        await bot.push(bot.group_message(group_id=1, user_id=2, text="hi"))
        assert bot.calls_to("send_group_msg")
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import socket
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

Handler = Callable[[dict[str, Any]], Any]


class ActionFailed(Exception):
    def __init__(self, retcode: int, message: str) -> None:
        super().__init__(message)
        self.retcode = retcode


class FakeOneBot:
    def __init__(
        self,
        *,
        self_id: int = 10001,
        nickname: str = "测试号",
        token: str = "fixture-token",
        self_message_post_type: str = "message_sent",
    ) -> None:
        self.self_id = self_id
        self.nickname = nickname
        self.token = token
        self.self_message_post_type = self_message_post_type
        self.calls: list[tuple[str, dict[str, Any]]] = []  # authorized API calls
        self.rejected_requests = 0  # HTTP requests refused for a bad token
        self.history: dict[int, list[dict[str, Any]]] = defaultdict(list)  # group_id -> message events
        self.groups: dict[int, str] = {}  # group_id -> name, for get_group_list
        self.members: dict[int, str] = {}  # user_id -> group card, for get_group_member_info
        self.handlers: dict[str, Handler] = {
            "get_login_info": lambda _: {"user_id": self.self_id, "nickname": self.nickname},
            "get_group_list": lambda _: [
                {"group_id": gid, "group_name": name, "member_count": 3} for gid, name in self.groups.items()
            ],
            "send_group_msg": self._send_group_msg,
            "get_group_msg_history": self._group_history,
            "get_msg": self._get_msg,
            "get_group_member_info": self._member_info,
        }
        self._clients: set[WebSocket] = set()
        self._next_id = 1000
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._sock: socket.socket | None = None
        self.port = 0

    # -- lifecycle -------------------------------------------------------------------------------

    async def __aenter__(self) -> FakeOneBot:
        app = Starlette(
            routes=[WebSocketRoute("/", self._ws), Route("/{action}", self._http, methods=["GET", "POST"])]
        )
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
        await self.drop_connections()
        assert self._server is not None and self._task is not None
        self._server.should_exit = True
        await self._task
        if self._sock is not None:
            self._sock.close()

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/"

    # -- test controls ---------------------------------------------------------------------------

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    def calls_to(self, action: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == action]

    def message_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def group_message(
        self,
        *,
        group_id: int,
        user_id: int,
        text: str = "",
        nickname: str = "群友",
        at: list[int] | None = None,
        reply_to: int | None = None,
        message_id: int | None = None,
    ) -> dict[str, Any]:
        segments: list[dict[str, Any]] = []
        if reply_to is not None:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        segments += [{"type": "at", "data": {"qq": str(qq)}} for qq in at or []]
        if text:
            segments.append({"type": "text", "data": {"text": text}})
        return {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "time": int(time.time()),
            "self_id": self.self_id,
            "group_id": group_id,
            "user_id": user_id,
            "message_id": message_id or self.message_id(),
            "message": segments,
            "message_format": "array",
            "raw_message": text,
            "font": 0,
            "sender": {"user_id": user_id, "nickname": nickname, "card": "", "role": "member"},
        }

    def recall(self, *, group_id: int, message_id: int, operator_id: int) -> dict[str, Any]:
        return {
            "post_type": "notice",
            "notice_type": "group_recall",
            "time": int(time.time()),
            "self_id": self.self_id,
            "group_id": group_id,
            "user_id": operator_id,
            "operator_id": operator_id,
            "message_id": message_id,
        }

    def heartbeat(self, *, good: bool = True) -> dict[str, Any]:
        return {
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
            "time": int(time.time()),
            "self_id": self.self_id,
            "interval": 30000,
            "status": {"online": good, "good": good},
        }

    async def push(self, event: dict[str, Any], *, record: bool = True) -> None:
        """Deliver an event to every connected WebSocket client (and keep group messages for history)."""
        if (
            record
            and event.get("message_type") == "group"
            and event.get("post_type") in ("message", "message_sent")
        ):
            self.history[int(event["group_id"])].append(event)
        await self.push_raw(json.dumps(event, ensure_ascii=False))

    async def push_raw(self, text: str) -> None:
        """Send an arbitrary text frame (e.g. malformed JSON) to every connected client."""
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.send_text(text)

    async def drop_connections(self, code: int = 1011) -> None:
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.close(code=code)
        self._clients.clear()

    # -- protocol --------------------------------------------------------------------------------

    def _authorized(self, headers: Any, query: Any) -> bool:
        header = headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else query.get("access_token")
        return supplied == self.token

    async def _call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, params))
        handler = self.handlers.get(action)
        if handler is None:
            return {
                "status": "failed",
                "retcode": 1404,
                "message": f"unsupported action {action}",
                "data": None,
            }
        try:
            data = handler(params)
            if inspect.isawaitable(data):  # async handlers can simulate a slow bridge
                data = await data
            return {"status": "ok", "retcode": 0, "data": data}
        except ActionFailed as exc:
            return {"status": "failed", "retcode": exc.retcode, "message": str(exc), "data": None}

    async def _http(self, request: Request) -> JSONResponse:
        if not self._authorized(request.headers, request.query_params):
            self.rejected_requests += 1
            return JSONResponse({"status": "failed", "retcode": 1403, "message": "token"}, status_code=403)
        params: dict[str, Any] = dict(request.query_params)
        params.pop("access_token", None)
        if request.method == "POST":
            body = await request.body()
            if body:
                params.update(json.loads(body))
        return JSONResponse(await self._call(request.path_params["action"], params))

    async def _ws(self, websocket: WebSocket) -> None:
        if not self._authorized(websocket.headers, websocket.query_params):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        self._clients.add(websocket)
        await websocket.send_text(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                    "self_id": self.self_id,
                }
            )
        )
        try:
            while True:
                request = json.loads(await websocket.receive_text())
                reply = await self._call(request.get("action", ""), request.get("params") or {})
                if "echo" in request:
                    reply["echo"] = request["echo"]
                await websocket.send_text(json.dumps(reply, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._clients.discard(websocket)

    # -- default handlers ------------------------------------------------------------------------

    def _send_group_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        mid = self.message_id()
        group_id = int(params["group_id"])
        message = params.get("message")
        segments = (
            [{"type": "text", "data": {"text": message}}] if isinstance(message, str) else list(message or [])
        )
        echo = {
            "post_type": self.self_message_post_type,
            "message_type": "group",
            "sub_type": "normal",
            "time": int(time.time()),
            "self_id": self.self_id,
            "group_id": group_id,
            "user_id": self.self_id,
            "message_id": mid,
            "message": segments,
            "message_format": "array",
            "raw_message": "".join(s["data"].get("text", "") for s in segments if s.get("type") == "text"),
            "sender": {"user_id": self.self_id, "nickname": self.nickname, "card": "", "role": "member"},
        }
        # Like SnowLuma with reportSelfMessage=true: our own message comes back on the event stream.
        asyncio.get_running_loop().create_task(self.push(echo))
        return {"message_id": mid}

    def _group_history(self, params: dict[str, Any]) -> dict[str, Any]:
        group_id = int(params["group_id"])
        count = int(params.get("count", 20))
        # Shaped like real SnowLuma history entries (checked 2026-10-26): no post_type / self_id,
        # plus message_seq, real_id and group_name.
        entries = []
        for event in self.history.get(group_id, [])[-count:]:
            entry = {k: v for k, v in event.items() if k not in ("post_type", "self_id", "message_format")}
            entries.append(
                entry
                | {
                    "message_seq": event["message_id"],
                    "real_id": event["message_id"],
                    "group_name": self.groups.get(group_id, ""),
                    "anonymous": None,
                }
            )
        return {"messages": entries}

    def _member_info(self, params: dict[str, Any]) -> dict[str, Any]:
        # Like SnowLuma: an unknown user is still "ok", just with empty names.
        user_id = int(params["user_id"])
        return {
            "group_id": int(params["group_id"]),
            "user_id": user_id,
            "card": self.members.get(user_id, ""),
            "nickname": "",
        }

    def _get_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = int(params["message_id"])
        for events in self.history.values():
            for event in events:
                if event["message_id"] == wanted:
                    return event
        raise ActionFailed(1404, "message not found")
