"""工作区 in the console and the chat window (伙伴模式): a companion's folder — look through it, download
what she made (给你的/), send her a file (into 收到的/), delete; the folders on this machine shared with her
(共享/); and what she can reach at all (her model calls tools? the 电脑上做事 plugin on for her?), so the page
can say what is missing. Paths are relative to her folder and checked like hers (nest.core.workspace)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.core.egress import is_online, recent, set_online
from nest.core.workspace import FILE_MAX, FOR_YOU, OLD, RECEIVED, SHARED, SHARED_MAX, WorkspaceError

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
CHUNK = 64 * 1024


def _bot(request: Request, bot_id: str) -> Any:
    if bot_id not in request.app.state.brain.bots:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    return request.app.state.workspace


@router.get("/workspaces/{bot_id}/files")
async def files(request: Request, bot_id: str, path: str = "") -> dict[str, Any]:
    space = _bot(request, bot_id)
    try:
        entries = await asyncio.to_thread(space.entries, bot_id, path)
        used = await asyncio.to_thread(space.used, bot_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {
        "path": path.strip().strip("/"),
        "entries": [
            {"name": e.name, "folder": e.folder, "size": e.size, "modified": e.modified} for e in entries
        ],
        "used": used,
        "limit": space.limit,
        "for_you": FOR_YOU,
        "received": RECEIVED,
        "can_run": request.app.state.sandbox is not None,
        "online": await is_online(request.app.state.db, bot_id),
        "shared_name": SHARED,
        "old_name": OLD,
        "shared": [{"name": s.name, "path": s.path, "writable": s.writable} for s in space.shared(bot_id)],
        "access": _access(request, bot_id),
    }


def _access(request: Request, bot_id: str) -> dict[str, Any]:
    """What she can reach: `tools` her model calls tools (none at all otherwise), `switch` it is an
    OpenAI-compatible one whose 工具调用 is off (it can be turned on), `files` her file tools are on for her,
    `windows` the 电脑上做事 plugin is on for her and `pc` a PC is connected for it."""
    brain = request.app.state.brain
    bot = brain.bots[bot_id]
    runtime = brain._model(bot)
    tools = getattr(brain, "tools", None)
    executor = getattr(request.app.state, "executor", None)
    allowed = (lambda name: tools.allowed(bot, name)) if tools is not None else (lambda name: False)
    return {
        "tools": runtime is not None and runtime.can_use_tools,
        "switch": runtime is not None and not runtime.can_use_tools and runtime.config.kind == "openai_chat",
        "model": runtime.config.id if runtime is not None else "",
        "files": allowed("write_file"),
        "windows": executor is not None and allowed("win_list_files"),
        "pc": executor is not None and executor.online,
    }


class SharedFolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=1000)
    writable: bool = False


class SharedFolders(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folders: list[SharedFolder] = Field(max_length=SHARED_MAX)


@router.put("/workspaces/{bot_id}/shared")
async def share(request: Request, bot_id: str, body: SharedFolders) -> dict[str, Any]:
    """The folders on this machine shared with her from now on (the whole list). Her container is made
    again at her next command, with them mounted."""
    space = _bot(request, bot_id)
    try:
        made = await asyncio.to_thread(space.set_shared, bot_id, [(f.path, f.writable) for f in body.folders])
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if request.app.state.sandbox is not None:
        await request.app.state.sandbox.forget(bot_id)
    request.app.state.broadcaster.publish("workspace", {"bot_id": bot_id})
    return {"shared": [{"name": s.name, "path": s.path, "writable": s.writable} for s in made]}


class OnlineSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    online: bool


@router.put("/workspaces/{bot_id}/online")
async def switch(request: Request, bot_id: str, body: OnlineSwitch) -> dict[str, Any]:
    """Her container's network: on (through the proxy) or off. Takes effect at her next command."""
    _bot(request, bot_id)
    await set_online(request.app.state.db, bot_id, body.online)
    if request.app.state.sandbox is not None:
        await request.app.state.sandbox.forget(bot_id)
    request.app.state.broadcaster.publish("workspace", {"bot_id": bot_id})
    return {"online": body.online}


@router.get("/workspaces/{bot_id}/network")
async def network(request: Request, bot_id: str) -> dict[str, Any]:
    """Where her container went (or tried to), newest first, from the proxy's log."""
    _bot(request, bot_id)
    sandbox = request.app.state.sandbox
    folder = sandbox.egress_log if sandbox is not None else None
    entries = await asyncio.to_thread(recent, folder, bot_id) if folder is not None else []
    return {"entries": entries, "online": await is_online(request.app.state.db, bot_id)}


def _chunks(handle: BinaryIO, size: int) -> Iterator[bytes]:
    """At most `size` bytes (the length announced), even if her container makes the file longer meanwhile."""
    with handle:
        while size > 0 and (chunk := handle.read(min(CHUNK, size))):
            size -= len(chunk)
            yield chunk


@router.get("/workspaces/{bot_id}/download")
async def download(request: Request, bot_id: str, path: str) -> StreamingResponse:
    space = _bot(request, bot_id)
    try:
        # Sent from the file opened now, not from a path opened later: her container could swap a folder
        # on the way for a link in between (nest.core.workspace).
        handle, name, size = await asyncio.to_thread(space.open_file, bot_id, path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    # Always a download, never shown in the console's page: what she wrote could be HTML.
    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    return StreamingResponse(
        _chunks(handle, size),  # read in a worker thread by Starlette
        media_type="application/octet-stream",
        headers={
            "content-disposition": disposition,
            "x-content-type-options": "nosniff",
            "content-length": str(size),
        },
    )


@router.put("/workspaces/{bot_id}/upload", status_code=201)
async def upload(request: Request, bot_id: str, name: str) -> dict[str, Any]:
    """The file is the request body; it goes into 收到的/."""
    space = _bot(request, bot_id)
    size = int(request.headers.get("content-length") or 0)
    if size > FILE_MAX:
        raise HTTPException(status_code=413, detail=f"文件不能超过 {FILE_MAX // 1024**2} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > FILE_MAX:
            raise HTTPException(status_code=413, detail=f"文件不能超过 {FILE_MAX // 1024**2} MB")
    try:
        saved = await asyncio.to_thread(space.receive, bot_id, name, bytes(data))
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    request.app.state.broadcaster.publish("workspace", {"bot_id": bot_id})
    return {"path": saved}


@router.delete("/workspaces/{bot_id}/files")
async def delete(request: Request, bot_id: str, path: str) -> dict[str, Any]:
    space = _bot(request, bot_id)
    try:
        await asyncio.to_thread(space.delete, bot_id, path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    request.app.state.broadcaster.publish("workspace", {"bot_id": bot_id})
    return {"ok": True}
