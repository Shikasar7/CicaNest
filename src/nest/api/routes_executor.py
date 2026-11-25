"""Windows 执行端: the desktop app on the user's PC asks here for steps to do and hands
back what came of them (nest.core.executor). Only a signed-in device (or the local token) gets in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

ANOTHER = "已经有另一台电脑连着了（同时只接一台）"


@router.get("/executor")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.executor.status()


@router.get("/executor/requests")
async def poll(request: Request, device: str, fresh: bool = False, key: str = "") -> dict[str, Any]:
    hub = request.app.state.executor
    steps = await hub.poll(device, key=key, fresh=fresh)
    if steps is None:
        raise HTTPException(status_code=409, detail=ANOTHER)
    # folders_known false: tell me your folders again (nest restarted since you last did).
    return {"requests": steps, "folders_known": hub.folders_known}


class Folder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=400)
    read_only: bool = False


class Folders(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: str = Field(max_length=80)
    key: str = Field(default="", max_length=128)  # the PC's own key (nest.core.executor)
    folders: list[Folder] = Field(max_length=50)
    allow_all: bool = False  # 完全允许 on that PC (only for her hint: the PC decides by its own settings)


@router.put("/executor/folders")
async def folders(request: Request, body: Folders) -> dict[str, Any]:
    hub = request.app.state.executor
    folders = [f.model_dump() for f in body.folders]
    if not hub.report(body.device, folders, key=body.key, allow_all=body.allow_all):
        raise HTTPException(status_code=409, detail=ANOTHER)
    return {"ok": True}


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device: str = Field(max_length=80)
    key: str = Field(default="", max_length=128)
    text: str = Field(max_length=200_000)
    decided: str = Field(default="", max_length=20)  # what the user said to it there: once, kind, denied


@router.post("/executor/requests/{step_id}")
async def result(request: Request, step_id: str, body: Result) -> dict[str, Any]:
    hub = request.app.state.executor
    if not hub.answer(body.device, step_id, body.text, key=body.key, decided=body.decided):
        raise HTTPException(status_code=409, detail="这一步已经不在等了（超时了，或者 nest 重启过）")
    return {"ok": True}
