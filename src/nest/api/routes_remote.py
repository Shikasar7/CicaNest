"""Opening the console from other devices: signing in with the password, and the 系统 page's 远程访问 card
(which addresses work, the password, signed-in devices, turning remote access on or off)."""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nest import hints
from nest.api.auth import (
    client_ip,
    is_local,
    refuse_cross_site,
    require_token,
    set_device_cookie,
    signed_in,
)
from nest.net import reachable_addresses
from nest.remote import COOKIE, password_problem

log = logging.getLogger(__name__)

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

REMOTE_SETTING = "NEST_REMOTE"
NO_PASSWORD = "还没有设置登录密码：在 nest 所在的电脑上打开控制台，到「系统」页设置，或运行 nest set-password"


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=1000)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: str | None = Field(default=None, max_length=1000)
    password: str = Field(max_length=1000)


class RemoteSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def _wait_message(seconds: float) -> str:
    return f"密码错误次数太多，{max(1, math.ceil(seconds / 60))} 分钟后再试"


async def _check_password(request: Request, password: str) -> str | None:
    """Check a password against the throttle: the attempt counts before the (slow) check starts, so a
    burst of parallel guesses is cut off at the limit. Returns check()'s tag, or None when wrong."""
    state, ip = request.app.state, client_ip(request)
    wait = state.throttle.attempt(ip)
    if wait:
        raise HTTPException(status_code=429, detail=_wait_message(wait))
    tag = await state.remote.check(password)
    if tag is not None:
        state.throttle.succeeded(ip)
        return tag
    state.throttle.failed()
    log.warning("有人输错了 nest 的登录密码（来自 %s）", ip)
    return None


def _configured(request: Request) -> bool:
    """Whether remote access is on after the next start: .env, unless the environment overrides it."""
    secrets = request.app.state.secrets
    secrets.reload()  # .env may have been edited by hand
    return secrets.get(REMOTE_SETTING).strip().lower() in ("1", "true", "yes", "on")


@public.get("/auth")
async def auth(request: Request) -> dict[str, Any]:
    """For the sign-in screen: whether this browser is signed in, and whether a password can be used."""
    return {
        "signed_in": await signed_in(request),
        "password_set": await request.app.state.remote.password_set(),
        "local": is_local(request),
        "start_hint": hints.START,  # how this machine opens the console without a password
    }


@public.post("/login")
async def login(request: Request, body: Login, response: Response) -> dict[str, Any]:
    refuse_cross_site(request)
    state = request.app.state
    if not await state.remote.password_set():
        raise HTTPException(status_code=409, detail=NO_PASSWORD)
    tag = await _check_password(request, body.password)
    if tag is None:
        raise HTTPException(status_code=401, detail="密码不对")
    signed = await state.remote.sign_in(
        ip=client_ip(request), agent=request.headers.get("user-agent", ""), tag=tag
    )
    if signed is None:
        raise HTTPException(status_code=409, detail="密码刚刚改过，请用新密码登录")
    token, device = signed
    set_device_cookie(response, token, request)
    log.info("一台设备登录了 nest 控制台（来自 %s）", device.ip)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    device = getattr(request.state, "device", None)
    if device is not None:
        await request.app.state.remote.sign_out(device.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/remote")
async def remote(request: Request) -> dict[str, Any]:
    state = request.app.state
    settings, access = state.settings, state.access
    current = getattr(request.state, "device", None)
    addresses = [
        {"url": f"http://{address}:{settings.port}", "kind": kind} for address, kind in reachable_addresses()
    ]
    names = sorted(name for name in access.names if "." in name)  # bare host names rarely resolve elsewhere
    return {
        "enabled": access.remote,
        "configured": _configured(request),
        "port": settings.port,
        "addresses": addresses,
        "names": [{"url": f"http://{name}:{settings.port}", "name": name} for name in names],
        "password_set": await state.remote.password_set(),
        "devices": [
            {
                "id": d.id,
                "ip": d.ip,
                "agent": d.agent,
                "created_at": d.created_at,
                "last_seen_at": d.last_seen_at,
                "current": current is not None and d.id == current.id,
            }
            for d in sorted(await state.remote.devices(), key=lambda d: d.last_seen_at, reverse=True)
        ],
    }


@router.put("/remote/password")
async def change_password(request: Request, body: PasswordChange) -> dict[str, Any]:
    state = request.app.state
    if await state.remote.password_set() and (
        not body.current or await _check_password(request, body.current) is None
    ):
        raise HTTPException(status_code=403, detail="现在的密码不对")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    current = getattr(request.state, "device", None)
    await state.remote.set_password(body.password, keep=current.id if current else None)
    log.info("nest 的登录密码已更改，其他设备需要重新登录")
    return {"ok": True}


@router.delete("/remote/devices/{device_id}")
async def sign_out_device(request: Request, device_id: str, response: Response) -> dict[str, Any]:
    if not await request.app.state.remote.sign_out(device_id):
        raise HTTPException(status_code=404, detail="这台设备已经退出了")
    current = getattr(request.state, "device", None)
    if current is not None and current.id == device_id:
        response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.delete("/remote/devices")
async def sign_out_others(request: Request) -> dict[str, Any]:
    current = getattr(request.state, "device", None)
    await request.app.state.remote.sign_out_all(keep=current.id if current else None)
    return {"ok": True}


@router.patch("/remote")
async def switch_remote(request: Request, body: RemoteSwitch) -> dict[str, Any]:
    """Turn remote access on or off from the next start (written to .env)."""
    state = request.app.state
    if body.enabled and not await state.remote.password_set():
        raise HTTPException(status_code=409, detail="先设置登录密码，再开启远程访问")
    if state.secrets.source(REMOTE_SETTING) == "env":
        raise HTTPException(status_code=409, detail="远程访问由环境变量 NEST_REMOTE 决定，这里改不了")
    state.secrets.set(REMOTE_SETTING, "true" if body.enabled else "false")
    log.info("远程访问已%s，重启 nest 后生效", "开启" if body.enabled else "关闭")
    return {"enabled": state.access.remote, "configured": body.enabled}
