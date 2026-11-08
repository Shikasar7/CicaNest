"""The few Discord REST calls nest makes. Rate limits are read from the response headers, never assumed: a
route's bucket is remembered and waited for, a 429 is waited out once and tried again. 401 and 403 are never
retried: enough of those in ten minutes and Cloudflare bans the address.

Errors carry no token: it only ever travels in the Authorization header, and every detail taken from a
response goes through scrub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from nest import __version__
from nest.adapters.base import MediaError
from nest.adapters.common import download
from nest.llm.http import client, scrub

log = logging.getLogger(__name__)

# Timers may fire a few ms early (Windows), and one try too soon is a second 429: the message is then lost.
WAIT_MARGIN = 0.05

API_BASE = "https://discord.com/api/v10"
# Discord's required form is "DiscordBot ($url, $version)"; this is no one's address on purpose.
USER_AGENT = f"DiscordBot (https://nest.invalid, {__version__})"
MEDIA_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
TIMEOUT = 15.0
MAX_WAIT = 30.0  # a longer rate limit fails the call: someone is waiting for this reply, or nobody should
DETAIL_CHARS = 160


class DiscordError(Exception):
    """A call that failed, in Chinese. `sure`: it certainly had no effect, so a send did not go out."""

    def __init__(self, message: str, *, sure: bool = True) -> None:
        super().__init__(message)
        self.sure = sure


class Unauthorized(DiscordError):
    pass


class ProxyDown(DiscordError):
    pass


class Refused(DiscordError):
    """Discord answered with an error."""

    def __init__(self, status: int, code: Any, message: str) -> None:
        super().__init__(f"Discord 拒绝了（HTTP {status}，错误码 {code}）：{message}")
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes
    content_type: str


@dataclass
class Bucket:
    remaining: int = 1
    reset_at: float = 0.0


def proxy_label(proxy: str) -> str:
    parts = urlsplit(proxy)
    return f"{parts.hostname}:{parts.port}"


class DiscordRest:
    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        api_base: str = API_BASE,
        media_hosts: Sequence[str] = MEDIA_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        max_wait: float = MAX_WAIT,
    ) -> None:
        self._token = token
        self._proxy = proxy
        self._base = api_base.rstrip("/")
        self._media_hosts = tuple(media_hosts)
        self._max_wait = max_wait
        self._http = client(timeout=TIMEOUT, proxy=proxy, transport=transport)
        self._route_bucket: dict[str, str] = {}  # "POST /channels/1/messages" -> Discord's bucket id
        self._buckets: dict[str, Bucket] = {}
        self._global_until = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- calls -------------------------------------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        return await self._json("GET", "/users/@me")

    async def gateway_bot(self) -> dict[str, Any]:
        return await self._json("GET", "/gateway/bot")

    async def channel(self, channel_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/channels/{channel_id}")

    async def open_dm(self, user_id: str) -> dict[str, Any]:
        return await self._json("POST", "/users/@me/channels", body={"recipient_id": user_id})

    async def messages(self, channel_id: str, *, after: int, limit: int = 100) -> list[dict[str, Any]]:
        found = await self._json(
            "GET", f"/channels/{channel_id}/messages", params={"after": str(after), "limit": str(limit)}
        )
        return found if isinstance(found, list) else []

    async def create_message(
        self, channel_id: str, body: Mapping[str, Any], uploads: Sequence[Upload] = ()
    ) -> dict[str, Any]:
        """Send one message; with `uploads`, as multipart with the JSON in payload_json."""
        path = f"/channels/{channel_id}/messages"
        if not uploads:
            return await self._json("POST", path, body=body)
        attachments = [{"id": n, "filename": u.name} for n, u in enumerate(uploads)]
        form = {"payload_json": json.dumps({**body, "attachments": attachments}, ensure_ascii=False)}
        files = [(f"files[{n}]", (u.name, u.data, u.content_type)) for n, u in enumerate(uploads)]
        return await self._json("POST", path, data=form, files=files)

    async def fetch(self, url: str, limit: int) -> bytes:
        """A CDN file, through the same proxy, without the token: only Discord's own hosts, since the url
        came in a message."""
        host = urlsplit(url).hostname or ""
        if host not in self._media_hosts:
            raise MediaError(f"不从 {host} 下载")
        return await download(self._http, url, limit)

    # -- plumbing ----------------------------------------------------------------------------------

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise DiscordError(
                f"Discord 回答了看不懂的内容（HTTP {response.status_code}）", sure=method == "GET"
            ) from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        files: Any = None,
    ) -> httpx.Response:
        route = f"{method} {path}"
        await self._wait_for(route)
        response = await self._send(method, path, body, params, data, files)
        self._remember(route, response)
        if response.status_code == 429:
            wait = self._retry_after(response)
            if wait > self._max_wait:
                raise DiscordError(f"Discord 要求等 {wait:.0f} 秒才能再发，先不等了")
            log.info("Discord 限速（%s），等 %.1f 秒再试一次", route.split(" ", 1)[0], wait)
            await asyncio.sleep(wait + WAIT_MARGIN)
            response = await self._send(method, path, body, params, data, files)
            self._remember(route, response)
            if response.status_code == 429:
                raise DiscordError("Discord 限速，等过一次还是不让发")
        if response.status_code == 401:
            raise Unauthorized("Discord 不认这个 Token")
        if response.status_code >= 500:
            raise DiscordError(f"Discord 服务器出错（HTTP {response.status_code}）", sure=False)
        if response.status_code >= 400:
            code, message = self._detail(response)
            raise Refused(response.status_code, code, message)
        return response

    async def _send(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
        params: Mapping[str, str] | None,
        data: Mapping[str, str] | None,
        files: Any,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bot {self._token}", "User-Agent": USER_AGENT}
        try:
            return await self._http.request(
                method,
                f"{self._base}{path}",
                json=body,
                params=params,
                data=data,
                files=files,
                headers=headers,
            )
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if self._proxy:
                raise ProxyDown(f"代理 {proxy_label(self._proxy)} 连不上") from exc
            raise DiscordError(f"连不上 Discord（{type(exc).__name__}）") from exc
        except httpx.HTTPError as exc:
            raise DiscordError(f"请求 Discord 中途出错（{type(exc).__name__}）", sure=False) from exc

    async def _wait_for(self, route: str) -> None:
        now = time.time()
        wait = max(0.0, self._global_until - now)
        bucket = self._buckets.get(self._route_bucket.get(route, ""))
        if bucket is not None and bucket.remaining <= 0:
            wait = max(wait, bucket.reset_at - now)
        if wait > self._max_wait:
            raise DiscordError(f"Discord 限速，还要等 {wait:.0f} 秒，先不发了")
        if wait > 0:
            await asyncio.sleep(wait + WAIT_MARGIN)

    def _remember(self, route: str, response: httpx.Response) -> None:
        headers = response.headers
        if response.status_code == 429 and (
            headers.get("x-ratelimit-global") or headers.get("x-ratelimit-scope") == "global"
        ):
            self._global_until = time.time() + self._retry_after(response)
        name = headers.get("x-ratelimit-bucket")
        if not name:
            return
        self._route_bucket[route] = name
        try:
            remaining = int(headers.get("x-ratelimit-remaining", "1"))
            reset_after = float(headers.get("x-ratelimit-reset-after", "0"))
        except ValueError:
            return
        self._buckets[name] = Bucket(remaining, time.time() + reset_after)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            return float(response.json().get("retry_after"))
        except (ValueError, TypeError, AttributeError):
            pass
        try:
            return float(response.headers.get("retry-after", "1"))
        except ValueError:
            return 1.0

    def _detail(self, response: httpx.Response) -> tuple[Any, str]:
        try:
            body = response.json()
            code, message = body.get("code"), str(body.get("message") or "")
        except (ValueError, AttributeError):
            code, message = None, response.text
        return code, scrub(message, self._token)[:DETAIL_CHARS]
