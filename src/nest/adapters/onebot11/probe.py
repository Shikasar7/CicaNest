"""Trying a OneBot connection before saving it (the 系统 page's 测试连接): the HTTP side must answer
get_login_info for the right QQ number with the token, the WebSocket must open with its token. Each check
is one Chinese sentence for the console, never raising."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import websockets
from websockets.exceptions import InvalidStatus

TIMEOUT = 5.0


@dataclass(frozen=True)
class Result:
    ok: bool
    detail: str


def _where(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)


async def check_http(
    url: str, token: str, *, self_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> Result:
    headers = {"authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT, trust_env=False) as client:
            response = await client.post(f"{url.rstrip('/')}/get_login_info", json={}, headers=headers)
    except httpx.TimeoutException:
        return Result(False, f"{_where(url)} 在 {TIMEOUT:.0f} 秒内没有回应")
    except httpx.HTTPError:
        return Result(False, f"连不上 {_where(url)}：SnowLuma 没开，或者端口不对")
    if response.status_code in (401, 403):
        return Result(False, "令牌不对：HTTP 接口拒绝了它")
    if response.status_code >= 400:
        return Result(False, f"HTTP 接口回答了错误 {response.status_code}")
    try:
        body = response.json()
        data = body.get("data") or {}
        user = str(data.get("user_id") or "")
        nickname = str(data.get("nickname") or "")
    except (ValueError, AttributeError):
        return Result(False, "这个地址回答的不是 OneBot 接口")
    if not user:
        return Result(False, "这个地址上的 QQ 还没有登录")
    if user != self_id:
        return Result(False, f"这个地址登录的是 QQ {user}，不是 {self_id}")
    return Result(True, f"连上了：{nickname or user}（{user}）")


async def check_ws(url: str, token: str) -> Result:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with asyncio.timeout(TIMEOUT):
            async with websockets.connect(url, additional_headers=headers, proxy=None, open_timeout=TIMEOUT):
                pass
    except InvalidStatus as exc:
        status = exc.response.status_code
        if status in (401, 403):
            return Result(False, "令牌不对：WebSocket 拒绝了它")
        return Result(False, f"WebSocket 回答了错误 {status}")
    except TimeoutError:
        return Result(False, f"{_where(url)} 在 {TIMEOUT:.0f} 秒内没有回应")
    except (OSError, websockets.exceptions.WebSocketException, ValueError):
        return Result(False, f"连不上 {_where(url)}：SnowLuma 没开，或者端口不对")
    return Result(True, "WebSocket 连上了")
