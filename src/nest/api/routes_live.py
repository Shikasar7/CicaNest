"""Accounts, channels, messages, and the live update stream (Server-Sent Events) for the console."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from nest.api.auth import device_signed_in, require_token
from nest.core.broadcast import CLOSED

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

KEEPALIVE_SECONDS = 15.0


def sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/accounts")
async def accounts(request: Request) -> list[dict[str, Any]]:
    return request.app.state.hub.account_views()


@router.get("/channels")
async def channels(request: Request) -> list[dict[str, Any]]:
    names = {a["id"]: a["name"] or a["self_id"] for a in request.app.state.hub.account_views()}
    rows = await request.app.state.store.channels()
    return [row | {"account_name": names.get(row["account_id"], row["account_id"])} for row in rows]


@router.get("/channels/{channel_id}/messages")
async def messages(
    request: Request, channel_id: int, limit: int = Query(50, ge=1, le=200), before: int | None = None
) -> list[dict[str, Any]]:
    store = request.app.state.store
    if not any(row["id"] == channel_id for row in await store.channels()):
        raise HTTPException(status_code=404, detail="没有这个群")
    return await store.recent(channel_id, limit=limit, before=before)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    hub = request.app.state.hub
    broadcaster = request.app.state.broadcaster

    by_device = getattr(request.state, "device", None) is not None
    loop = asyncio.get_running_loop()

    async def stream() -> AsyncIterator[str]:
        queue = broadcaster.subscribe()  # inside the generator: unsubscribed in finally on any exit
        checked = loop.time()
        try:
            yield sse("hello", {"accounts": hub.account_views()})
            while True:
                if by_device and loop.time() - checked >= KEEPALIVE_SECONDS:
                    # A device signed out (or its password changed) stops hearing anything at the next
                    # check; the console then finds itself signed out when it reconnects.
                    if not await device_signed_in(request):
                        return
                    checked = loop.time()
                try:
                    kind, data = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield sse(kind, data)
                if kind == CLOSED:
                    return
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )
