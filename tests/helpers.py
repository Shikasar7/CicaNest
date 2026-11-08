"""Shared async test helpers."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn

from nest.adapters.base import Adapter, BackfillSource, StatusSink
from nest.adapters.onebot11.adapter import OneBotAdapter
from nest.secrets import SecretStore
from nest.storage.accounts import Account


async def eventually(check: Callable[[], Any], timeout: float = 5.0, interval: float = 0.02) -> Any:  # noqa: ASYNC109
    """Poll until `check()` returns something truthy; fail the test with the last value otherwise."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"条件在 {timeout} 秒内没有满足，最后的值：{value!r}")
        await asyncio.sleep(interval)


def fast_adapter(
    account: Account, secrets: SecretStore, on_status: StatusSink, backfill: BackfillSource
) -> Adapter:
    """The production OneBot adapter with test-speed backoff."""
    return OneBotAdapter(
        account=account.self_id,
        http_url=account.http_url,
        ws_url=account.ws_url,
        http_token=secrets.get(account.http_token_ref),
        ws_token=secrets.get(account.ws_token_ref),
        on_status=on_status,
        backfill_source=backfill,
        backoff_initial=0.05,
        backoff_max=0.2,
        stable_after=0.5,
    )


def free_socket() -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


@asynccontextmanager
async def serve(app: Any, sock: socket.socket) -> AsyncIterator[str]:
    """Run an ASGI app on a real socket (needed for streaming responses such as SSE)."""
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await eventually(lambda: server.started or task.done(), timeout=10)
        if task.done():
            task.result()
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse a text/event-stream response into (event, data) pairs; comments are skipped."""
    kind, data = "message", ""
    async for line in response.aiter_lines():
        if line.startswith(":"):
            continue
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data += line[6:]
        elif line == "" and data:
            yield kind, json.loads(data)
            kind, data = "message", ""
