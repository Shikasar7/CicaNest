"""A small MCP client: talks to one MCP server over stdio — JSON-RPC 2.0, one message per line
— enough to list its tools and call them. No other MCP features (resources, prompts, sampling) are used;
what the server asks of us is answered with "not supported", except ping.

The server is a process nest started (in a companion's container: nest.core.mcp_plugins); this module
only speaks the protocol over its pipes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

PROTOCOL = "2025-06-18"
START_TIMEOUT = 60.0
CALL_TIMEOUT = 120.0
LINE_LIMIT = 8 * 1024 * 1024
RESULT_CHARS = 12_000
TOOLS_MAX = 64


class McpError(Exception):
    """Why the server could not be used, in one Chinese sentence."""


def flatten(result: Any) -> tuple[str, bool]:
    """(the text of a tools/call result, whether it is an error)."""
    if not isinstance(result, dict):
        return "", True
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(str(resource.get("text") or f"[资源 {resource.get('uri', '')}]"))
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "resource_link":
            parts.append(f"[链接 {item.get('uri', '')}]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p)
    if len(text) > RESULT_CHARS:
        text = text[:RESULT_CHARS] + "\n……（后面略）"
    return text, result.get("isError") is True


class McpSession:
    """One running server. `process` has stdin and stdout pipes."""

    def __init__(self, process: asyncio.subprocess.Process, *, name: str = "") -> None:
        self.process = process
        self.name = name
        self._next = 0
        self._waiting: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.process.returncode is None and self._reader is not None and not self._reader.done()

    async def start(self) -> dict[str, Any]:
        self._reader = asyncio.create_task(self._read(), name=f"mcp-{self.name}")
        info = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "nest", "version": "1"},
            },
            START_TIMEOUT,
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return info if isinstance(info, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            page = await self.request("tools/list", {"cursor": cursor} if cursor else {}, START_TIMEOUT)
            for tool in (page or {}).get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools.append(tool)
            cursor = (page or {}).get("nextCursor")
            if not cursor or len(tools) >= TOOLS_MAX:
                break
        return tools[:TOOLS_MAX]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], seconds: float = CALL_TIMEOUT
    ) -> tuple[str, bool]:
        return flatten(await self.request("tools/call", {"name": name, "arguments": arguments}, seconds))

    async def request(self, method: str, params: dict[str, Any], seconds: float) -> Any:
        if self.process.returncode is not None:
            raise McpError("插件的程序已经退出了")
        self._next += 1
        number = self._next
        waiting: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._waiting[number] = waiting
        try:
            await self._send({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
            return await asyncio.wait_for(waiting, seconds)
        except TimeoutError:
            raise McpError(f"插件的程序 {seconds:.0f} 秒没有回应") from None
        finally:
            self._waiting.pop(number, None)

    async def _send(self, message: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise McpError("插件的程序已经退出了")
        async with self._write_lock:
            try:
                stdin.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
                await stdin.drain()
            except (ConnectionError, OSError):
                raise McpError("插件的程序已经退出了") from None

    async def _read(self) -> None:
        stdout = self.process.stdout
        assert stdout is not None
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # a server writing logs to stdout
                if isinstance(message, dict):
                    await self._handle(message)
        except (ValueError, asyncio.LimitOverrunError):
            log.info("MCP %s：回来的一行太长，断开", self.name)
        finally:
            for waiting in self._waiting.values():
                if not waiting.done():
                    waiting.set_exception(McpError("插件的程序已经退出了"))

    async def _handle(self, message: dict[str, Any]) -> None:
        if "method" in message:  # the server asks something of us
            if "id" in message:
                if message["method"] == "ping":
                    reply: dict[str, Any] = {"result": {}}
                else:
                    reply = {"error": {"code": -32601, "message": "not supported"}}
                with contextlib.suppress(McpError):
                    await self._send({"jsonrpc": "2.0", "id": message["id"], **reply})
            return
        waiting = self._waiting.get(message.get("id")) if isinstance(message.get("id"), int) else None
        if waiting is None or waiting.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            waiting.set_exception(McpError(f"插件的程序出错了：{str(error.get('message') or '')[:200]}"))
        else:
            waiting.set_result(message.get("result"))

    async def close(self) -> None:
        if self.process.stdin is not None:
            with contextlib.suppress(Exception):
                self.process.stdin.close()
        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
