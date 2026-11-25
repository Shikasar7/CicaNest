"""The few Slack Web API calls nest makes, through the account's proxy.

Two tokens: the bot's (xoxb-) for everything it says and reads, the app's (xapp-) only to open the Socket Mode
connection. They travel in the Authorization header alone, and only to Slack: never to a file's address.
A 429 is waited out once and tried again; a connection lost while sending may have sent it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from nest.adapters.base import MediaError
from nest.adapters.common import DOWNLOAD_TIMEOUT
from nest.llm.http import client, scrub

log = logging.getLogger(__name__)

API_BASE = "https://slack.com/api"
MEDIA_HOSTS = ("files.slack.com",)
TIMEOUT = 20.0
UPLOAD_TIMEOUT = 60.0
MAX_WAIT = 30.0
WAIT_MARGIN = 0.05
# What Slack says when a token is no good; anything else may be a passing trouble, or one call's.
BAD_TOKEN = {
    "account_inactive",
    "invalid_auth",
    "not_authed",
    "token_expired",
    "token_revoked",
    "not_allowed_token_type",
}
SAID = {
    "not_in_channel": "bot 不在这个频道里：在频道里 /invite 它",
    "channel_not_found": "没有这个频道，或者 bot 不在里面",
    "is_archived": "这个频道已经归档了",
    "msg_too_long": "消息太长",
    "restricted_action": "工作区的管理员不让 bot 在这里发言",
    "file_upload_size_restricted": "文件太大",
}


class SlackError(Exception):
    """A call that failed, in Chinese. `sure`: it certainly had no effect, so a send did not go out."""

    def __init__(self, message: str, *, sure: bool = True) -> None:
        super().__init__(message)
        self.sure = sure


class Unauthorized(SlackError):
    pass


class MissingScope(SlackError):
    pass


class ProxyDown(SlackError):
    pass


class Refused(SlackError):
    def __init__(self, code: str) -> None:
        super().__init__(SAID.get(code) or f"Slack 拒绝了：{code}")
        self.code = code


class SlackApi:
    def __init__(
        self,
        bot_token: str,
        app_token: str,
        *,
        proxy: str | None = None,
        api_base: str = API_BASE,
        media_hosts: tuple[str, ...] = MEDIA_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        max_wait: float = MAX_WAIT,
    ) -> None:
        self._bot = bot_token
        self._app = app_token
        self._proxy = proxy
        self._base = api_base.rstrip("/")
        self._media_hosts = media_hosts
        self._max_wait = max_wait
        self._http = client(timeout=TIMEOUT, proxy=proxy, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    def clean(self, text: str) -> str:
        return scrub(scrub(text, self._bot), self._app)

    async def me(self) -> dict[str, Any]:
        return await self.call("auth.test")

    async def open_socket(self) -> str:
        answer = await self.call("apps.connections.open", token=self._app)
        url = str(answer.get("url") or "")
        if not url:
            raise SlackError("Slack 没给出连接地址")
        return url

    async def post(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return await self.call("chat.postMessage", body, sending=True)

    async def channels(self) -> list[dict[str, Any]]:
        """The channels the bot is in."""
        found: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(20):
            answer = await self.call(
                "users.conversations",
                {
                    "types": "public_channel,private_channel,mpim",
                    "exclude_archived": "true",
                    "limit": "200",
                    **({"cursor": cursor} if cursor else {}),
                },
                form=True,
            )
            found += [c for c in answer.get("channels") or [] if isinstance(c, dict)]
            cursor = str((answer.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break
        return found

    async def history(self, channel: str, oldest: float) -> list[dict[str, Any]]:
        answer = await self.call(
            "conversations.history",
            {"channel": channel, "oldest": f"{oldest:.6f}", "inclusive": "true", "limit": "200"},
            form=True,
        )
        return [m for m in answer.get("messages") or [] if isinstance(m, dict)]

    async def user(self, user: str) -> dict[str, Any]:
        answer = await self.call("users.info", {"user": user}, form=True)
        found = answer.get("user")
        return found if isinstance(found, dict) else {}

    async def open_dm(self, user: str) -> str:
        answer = await self.call("conversations.open", {"users": user})
        return str((answer.get("channel") or {}).get("id") or "")

    async def upload(self, channel: str, name: str, data: bytes, *, thread: str = "") -> None:
        """Share a file in a channel: Slack gives a place to put it, then is told it is there."""
        place = await self.call(
            "files.getUploadURLExternal", {"filename": name, "length": str(len(data))}, form=True
        )
        url, file_id = str(place.get("upload_url") or ""), str(place.get("file_id") or "")
        if not url or not file_id:
            raise SlackError("Slack 没给出放文件的地方")
        try:
            # No token here: the address itself is the permission.
            response = await self._http.post(url, content=data, timeout=UPLOAD_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SlackError(f"文件没传上去（{type(exc).__name__}）") from None
        if response.status_code != 200:
            raise SlackError(f"文件没传上去（HTTP {response.status_code}）")
        body: dict[str, Any] = {"files": [{"id": file_id}], "channel_id": channel}
        if thread:
            body["thread_ts"] = thread
        await self.call("files.completeUploadExternal", body, sending=True)

    async def fetch(self, url: str, limit: int) -> bytes:
        """A file someone shared. Only Slack's own file host gets the token, and no redirect is followed."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self._media_hosts:
            raise MediaError(f"不从 {parts.hostname} 下载")
        headers = {"Authorization": f"Bearer {self._bot}"}
        try:
            async with self._http.stream(
                "GET", url, headers=headers, timeout=DOWNLOAD_TIMEOUT, follow_redirects=False
            ) as response:
                if response.status_code != 200 or response.headers.get("content-type", "").startswith(
                    "text/html"
                ):  # a sign-in page instead of the file: the bot may not read files
                    raise MediaError(f"下载失败（HTTP {response.status_code}），bot 可能没有 files:read 权限")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise MediaError(f"超过 {limit // 1024 // 1024} MB，不收")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise MediaError(f"下载失败（{type(exc).__name__}）") from None
        return b"".join(chunks)

    # -- plumbing ----------------------------------------------------------------------------------

    async def call(
        self,
        method: str,
        body: Mapping[str, Any] | None = None,
        *,
        token: str = "",
        form: bool = False,
        sending: bool = False,
    ) -> dict[str, Any]:
        response = await self._send(method, body or {}, token or self._bot, form)
        if response.status_code == 429:
            wait = _retry_after(response)
            if wait > self._max_wait:
                raise SlackError(f"Slack 要求等 {wait:.0f} 秒才能再发，先不等了")
            log.info("Slack 限速，等 %.0f 秒再试一次", wait)
            await asyncio.sleep(wait + WAIT_MARGIN)
            response = await self._send(method, body or {}, token or self._bot, form)
            if response.status_code == 429:
                raise SlackError("Slack 限速，等过一次还是不让发")
        if response.status_code >= 500:
            raise SlackError(f"Slack 服务器出错（HTTP {response.status_code}）", sure=not sending)
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise SlackError(f"Slack 回答了看不懂的内容（HTTP {response.status_code}）", sure=not sending)
        if answer.get("ok"):
            return answer
        code = self.clean(str(answer.get("error") or ""))[:80]
        if code in BAD_TOKEN:
            raise Unauthorized("Slack 不认这个 Token")
        if code == "missing_scope":
            raise MissingScope(f"bot 缺权限：{str(answer.get('needed') or '')[:80]}")
        raise Refused(code)

    async def _send(self, method: str, body: Mapping[str, Any], token: str, form: bool) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if form:
                return await self._http.post(f"{self._base}/{method}", data=dict(body), headers=headers)
            headers["Content-Type"] = "application/json; charset=utf-8"
            content = json.dumps(body, ensure_ascii=False).encode()
            return await self._http.post(f"{self._base}/{method}", content=content, headers=headers)
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if self._proxy:
                parts = urlsplit(self._proxy)
                raise ProxyDown(f"代理 {parts.hostname}:{parts.port} 连不上") from None
            raise SlackError(f"连不上 Slack（{type(exc).__name__}）") from None
        except httpx.HTTPError as exc:
            raise SlackError(f"请求 Slack 中途出错（{type(exc).__name__}）", sure=False) from None


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", "1"))
    except ValueError:
        return 1.0
