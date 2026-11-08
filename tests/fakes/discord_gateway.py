"""In-process fake Discord: the Gateway and the REST routes nest uses, on real sockets on 127.0.0.1, so the
adapter under test uses its real network code. Tests drive it: push events, close with a code, refuse
heartbeats, answer with a 429, inspect what was sent.

    async with FakeDiscord(bot_id="900") as discord:
        await discord.push_message(discord.message(channel_id="11", content="hi"))
        assert discord.sent
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import json
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect


def snowflake(ms: int, sequence: int = 0) -> str:
    return str(((ms - 1420070400000) << 22) + sequence)


class FakeDiscord:
    def __init__(
        self,
        *,
        bot_id: str = "900000000000000001",
        username: str = "nestbot",
        token: str = "fixture-token",
        heartbeat_ms: int = 60_000,
    ) -> None:
        self.bot_id = bot_id
        self.username = username
        self.token = token
        self.heartbeat_ms = heartbeat_ms
        self.ack_heartbeats = True
        self.echo_own = False  # send the bot its own messages over the Gateway
        self.resume_ok = True
        self.ready_id = bot_id  # who READY says the token belongs to
        self.session_start = {
            "total": 1000,
            "remaining": 1000,
            "reset_after": 3_600_000,
            "max_concurrency": 1,
        }
        self.frames: list[dict[str, Any]] = []  # everything clients sent on the Gateway
        self.closes: list[int | None] = []  # how clients closed
        self.user_agents: list[str] = []  # of REST requests
        self.calls: list[tuple[str, str]] = []  # every REST call
        self.sent: list[
            dict[str, Any]
        ] = []  # POST /channels/x/messages bodies, plus "channel_id" and "files"
        self.guilds: list[dict[str, Any]] = []  # sent after READY as GUILD_CREATE
        self.history: dict[str, list[dict[str, Any]]] = {}  # channel id -> messages, for GET …/messages
        self.channel_details: dict[str, dict[str, Any]] = {}  # for GET /channels/x
        self.unreadable: set[str] = set()  # no READ_MESSAGE_HISTORY there
        self.refuse_send_after: int | None = None  # this many messages go through, the next gets a 403
        self.files: dict[str, bytes] = {}  # path under /cdn/ -> bytes
        self.dm_opens: list[str] = []
        self.next_answers: list[
            Callable[[Request], Response]
        ] = []  # used up one per REST call, before routing
        self.rate_headers: dict[str, str] = {}
        self._clients: set[WebSocket] = set()
        self._log: list[dict[str, Any]] = []  # dispatched events, for Resume to replay
        self._seq = 0
        self._next_id = 1000
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._sock: socket.socket | None = None
        self.port = 0

    # -- lifecycle -------------------------------------------------------------------------------

    async def __aenter__(self) -> FakeDiscord:
        app = Starlette(
            routes=[
                WebSocketRoute("/", self._gateway),
                WebSocketRoute("/resume/", self._gateway),
                Route("/cdn/{path:path}", self._cdn, methods=["GET"]),
                Route("/api/v10/{path:path}", self._rest, methods=["GET", "POST"]),
            ]
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
        await self.close(1011)
        assert self._server is not None and self._task is not None
        self._server.should_exit = True
        await self._task
        if self._sock is not None:
            self._sock.close()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v10"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def cdn_url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}/cdn/{path}"

    # -- test controls ---------------------------------------------------------------------------

    @property
    def connected_clients(self) -> int:
        return len(self._clients)

    def ops(self, op: int) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame.get("op") == op]

    @property
    def identifies(self) -> list[dict[str, Any]]:
        return self.ops(2)

    @property
    def resumes(self) -> list[dict[str, Any]]:
        return self.ops(6)

    @property
    def heartbeats(self) -> list[dict[str, Any]]:
        return self.ops(1)

    def message_id(self) -> str:
        self._next_id += 1
        return snowflake(int(datetime.now(UTC).timestamp() * 1000), self._next_id)

    def message(
        self,
        *,
        channel_id: str = "11",
        guild_id: str | None = "1",
        author_id: str = "42",
        username: str = "小明",
        content: str = "",
        bot: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        message = {
            "id": self.message_id(),
            "channel_id": channel_id,
            "author": {"id": author_id, "username": username, "global_name": None, "bot": bot},
            "content": content,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": 0,
            "mentions": [],
            "mention_roles": [],
            "attachments": [],
            **extra,
        }
        if guild_id is not None:
            message["guild_id"] = guild_id
        return message

    def guild(
        self,
        guild_id: str = "1",
        name: str = "测试服",
        channels: list[dict[str, Any]] | None = None,
        roles: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        everyone = {"id": guild_id, "name": "@everyone", "permissions": str(1 << 10)}
        return {
            "id": guild_id,
            "name": name,
            "roles": [everyone, *(roles or [])],
            "channels": channels if channels is not None else [{"id": "11", "name": "闲聊", "type": 0}],
            "threads": [],
            "members": [{"user": {"id": self.bot_id}, "roles": []}],
            **extra,
        }

    async def push(self, kind: str, data: dict[str, Any]) -> None:
        """Dispatch an event to connected clients, and keep it for a Resume that comes later."""
        self._seq += 1
        frame = {"op": 0, "t": kind, "s": self._seq, "d": data}
        self._log.append(frame)
        await self.raw(frame)

    async def push_message(self, message: dict[str, Any]) -> None:
        await self.push("MESSAGE_CREATE", message)

    def miss_message(self, message: dict[str, Any]) -> None:
        """A message that happened while the client was away: only a Resume brings it."""
        self._seq += 1
        self._log.append({"op": 0, "t": "MESSAGE_CREATE", "s": self._seq, "d": message})

    async def raw(self, frame: dict[str, Any] | str) -> None:
        text = frame if isinstance(frame, str) else json.dumps(frame, ensure_ascii=False)
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.send_text(text)

    async def close(self, code: int = 1011) -> None:
        for client in list(self._clients):
            with contextlib.suppress(Exception):
                await client.close(code=code)
        self._clients.clear()

    def answer_once(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.next_answers.append(lambda _: JSONResponse(body, status_code=status, headers=headers))

    def rate_limit_once(self, retry_after: float = 0.05, *, is_global: bool = False) -> None:
        body = {"message": "You are being rate limited.", "retry_after": retry_after, "global": is_global}
        headers = {"X-RateLimit-Scope": "global"} if is_global else {}
        self.answer_once(429, body, headers)

    # -- Gateway -----------------------------------------------------------------------------------

    async def _gateway(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        await self._send(websocket, {"op": 10, "d": {"heartbeat_interval": self.heartbeat_ms}})
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    self.closes.append(message.get("code"))
                    return
                frame = json.loads(message["text"])
                self.frames.append(frame)
                await self._on_frame(websocket, frame)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._clients.discard(websocket)

    async def _send(self, websocket: WebSocket, frame: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(frame, ensure_ascii=False))

    async def _on_frame(self, websocket: WebSocket, frame: dict[str, Any]) -> None:
        op = frame.get("op")
        if op == 1 and self.ack_heartbeats:
            await self._send(websocket, {"op": 11})
        elif op == 2:
            self.session_start["remaining"] -= 1
            if frame["d"].get("token") != self.token:
                await websocket.close(code=4004)
                return
            await self._ready(websocket)
        elif op == 6:
            if not self.resume_ok:
                await self._send(websocket, {"op": 9, "d": False})
                return
            for missed in self._log:
                if missed["s"] > (frame["d"].get("seq") or 0):
                    await self._send(websocket, missed)
            self._seq += 1
            await self._send(websocket, {"op": 0, "t": "RESUMED", "s": self._seq, "d": {}})

    async def _ready(self, websocket: WebSocket) -> None:
        self._seq += 1
        await self._send(
            websocket,
            {
                "op": 0,
                "t": "READY",
                "s": self._seq,
                "d": {
                    "v": 10,
                    "user": {"id": self.ready_id, "username": self.username, "bot": True},
                    "guilds": [{"id": g["id"], "unavailable": True} for g in self.guilds],
                    "session_id": f"session-{len(self.identifies)}",
                    "resume_gateway_url": f"{self.ws_url}/resume",
                },
            },
        )
        for guild in self.guilds:
            self._seq += 1
            await self._send(websocket, {"op": 0, "t": "GUILD_CREATE", "s": self._seq, "d": guild})

    # -- REST --------------------------------------------------------------------------------------

    async def _cdn(self, request: Request) -> Response:
        path = request.path_params["path"]
        if "authorization" in request.headers:
            return JSONResponse({"message": "the token went to the CDN"}, status_code=418)
        if path not in self.files:
            return Response(status_code=404)
        return Response(self.files[path])

    async def _rest(self, request: Request) -> Response:
        path = "/" + request.path_params["path"]
        self.calls.append((request.method, path))
        self.user_agents.append(request.headers.get("user-agent", ""))
        if self.next_answers:
            return self.next_answers.pop(0)(request)
        if request.headers.get("authorization") != f"Bot {self.token}":
            return JSONResponse({"message": "401: Unauthorized", "code": 0}, status_code=401)
        response = await self._route(request, path)
        response.headers.update(self.rate_headers)
        return response

    async def _route(self, request: Request, path: str) -> Response:
        parts = path.strip("/").split("/")
        if path == "/users/@me":
            return JSONResponse({"id": self.ready_id, "username": self.username, "bot": True})
        if path == "/gateway/bot":
            return JSONResponse({"url": self.ws_url, "shards": 1, "session_start_limit": self.session_start})
        if path == "/users/@me/channels":
            recipient = (await request.json())["recipient_id"]
            self.dm_opens.append(recipient)
            return JSONResponse({"id": f"dm-{recipient}", "type": 1})
        if parts[0] == "channels" and len(parts) == 2:
            found = self.channel_details.get(parts[1])
            if found is None:
                return JSONResponse({"message": "Unknown Channel", "code": 10003}, status_code=404)
            return JSONResponse(found)
        if parts[0] == "channels" and parts[2:] == ["messages"]:
            if request.method == "POST":
                return await self._create_message(request, parts[1])
            return self._list_messages(parts[1], request)
        return JSONResponse({"message": "404", "code": 0}, status_code=404)

    def _list_messages(self, channel_id: str, request: Request) -> Response:
        if channel_id in self.unreadable:
            return JSONResponse({"message": "Missing Access", "code": 50001}, status_code=403)
        after = int(request.query_params.get("after", "0"))
        limit = int(request.query_params.get("limit", "50"))
        found = sorted(
            (m for m in self.history.get(channel_id, []) if int(m["id"]) > after), key=lambda m: int(m["id"])
        )
        return JSONResponse(list(reversed(found[:limit])))  # the oldest ones after `after`, newest first

    async def _create_message(self, request: Request, channel_id: str) -> Response:
        if self.refuse_send_after is not None and len(self.sent) >= self.refuse_send_after:
            return JSONResponse({"message": "Missing Permissions", "code": 50013}, status_code=403)
        content_type = request.headers.get("content-type", "")
        files: dict[str, tuple[str, bytes, str]] = {}
        if content_type.startswith("multipart/"):
            raw = await request.body()
            parsed = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + raw)
            body: dict[str, Any] = {}
            for part in parsed.get_payload():  # type: ignore[union-attr]
                name = part.get_param("name", header="content-disposition")
                data = part.get_payload(decode=True)
                if name == "payload_json":
                    body = json.loads(data)
                else:
                    files[str(name)] = (str(part.get_filename()), data, part.get_content_type())
        else:
            body = await request.json()
        self.sent.append({**body, "channel_id": channel_id, "files": files})
        message = self.message(
            channel_id=channel_id,
            guild_id=None,
            author_id=self.bot_id,
            username=self.username,
            content=str(body.get("content") or ""),
            bot=True,
            nonce=body.get("nonce"),
            attachments=[
                {
                    "id": str(n),
                    "filename": name,
                    "url": self.cdn_url(f"att/{n}/{name}"),
                    "content_type": kind,
                }
                for n, (name, _, kind) in enumerate(files.values())
            ],
        )
        if self.echo_own:
            await self.push_message({**message, "guild_id": "1"})
        return JSONResponse(message)
