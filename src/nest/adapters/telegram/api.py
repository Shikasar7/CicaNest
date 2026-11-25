"""The few Telegram Bot API calls nest makes, through the account's proxy.

The token is part of every url: no error here carries one, and httpx's own request log is off.
A 429 says how long to wait and is tried once more; a connection lost while sending may have sent it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from nest.adapters.base import MediaError
from nest.adapters.common import download
from nest.llm.http import client, scrub

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line holds the url, and so the token
logging.getLogger("httpcore").setLevel(logging.WARNING)  # and its DEBUG lines too

API_BASE = "https://api.telegram.org"
TIMEOUT = 20.0
MAX_WAIT = 30.0
WAIT_MARGIN = 0.05
DETAIL_CHARS = 160
FILE_SCHEME = "tgfile:"  # what nest keeps as a picture's url: its file id, never an address with the token
TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}")


class TelegramError(Exception):
    """A call that failed, in Chinese. `sure`: it certainly had no effect, so a send did not go out."""

    def __init__(self, message: str, *, sure: bool = True) -> None:
        super().__init__(message)
        self.sure = sure


class Unauthorized(TelegramError):
    pass


class Conflict(TelegramError):
    """Someone else receives this bot's messages: another program, or a webhook."""

    def __init__(self, message: str, *, webhook: bool) -> None:
        super().__init__(message)
        self.webhook = webhook


class ProxyDown(TelegramError):
    pass


class Refused(TelegramError):
    def __init__(self, code: int, message: str, parameters: Mapping[str, Any]) -> None:
        super().__init__(f"Telegram 拒绝了（{code}）：{message}")
        self.code = code
        self.parameters = parameters


@dataclass(frozen=True)
class Upload:
    field: str  # "photo", "animation", "voice", "document"
    name: str
    data: bytes
    content_type: str


class TelegramApi:
    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        api_base: str = API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        max_wait: float = MAX_WAIT,
    ) -> None:
        self._token = token
        self._proxy = proxy
        self._base = api_base.rstrip("/")
        self._max_wait = max_wait
        self._http = client(timeout=TIMEOUT, proxy=proxy, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def me(self) -> dict[str, Any]:
        found = await self._call("getMe")
        return found if isinstance(found, dict) else {}

    async def updates(self, offset: int, *, wait: int, kinds: list[str]) -> list[dict[str, Any]]:
        """What happened since `offset`; asking tells Telegram that everything before it was taken."""
        body = {"offset": offset, "timeout": wait, "allowed_updates": kinds, "limit": 100}
        found = await self._call("getUpdates", body, seconds=wait + TIMEOUT)
        return [u for u in found if isinstance(u, dict)] if isinstance(found, list) else []

    async def send(
        self, method: str, body: Mapping[str, Any], upload: Upload | None = None
    ) -> dict[str, Any]:
        found = await self._call(method, body, upload=upload, sending=True)
        if not isinstance(found, dict) or "message_id" not in found:
            raise TelegramError("Telegram 回答了看不懂的内容", sure=False)
        return found

    async def fetch(self, file_id: str, limit: int) -> bytes:
        """A file by its id."""
        try:
            found = await self._call("getFile", {"file_id": file_id})
        except TelegramError as exc:
            raise MediaError(str(exc)) from None
        path = str((found or {}).get("file_path") or "")
        if not path or ".." in path or "://" in path:
            raise MediaError("Telegram 没给出这个文件")
        return await download(self._http, f"{self._base}/file/bot{self._token}/{path}", limit)

    # -- plumbing ----------------------------------------------------------------------------------

    async def _call(
        self,
        method: str,
        body: Mapping[str, Any] | None = None,
        *,
        upload: Upload | None = None,
        seconds: float = TIMEOUT,
        sending: bool = False,
    ) -> Any:
        status, answer = await self._post(method, body or {}, upload, seconds)
        if status == 429:
            wait = _retry_after(answer)
            if wait > self._max_wait:
                raise TelegramError(f"Telegram 要求等 {wait:.0f} 秒才能再发，先不等了")
            log.info("Telegram 限速，等 %.0f 秒再试一次", wait)
            await asyncio.sleep(wait + WAIT_MARGIN)
            status, answer = await self._post(method, body or {}, upload, seconds)
            if status == 429:
                raise TelegramError("Telegram 限速，等过一次还是不让发")
        if answer is None:
            raise TelegramError(f"Telegram 回答了看不懂的内容（HTTP {status}）", sure=not sending)
        if answer.get("ok"):
            return answer.get("result")
        message = scrub(str(answer.get("description") or ""), self._token)[:DETAIL_CHARS]
        if status in (401, 404):  # 404: not even the shape of a token Telegram ever gave out
            raise Unauthorized("Telegram 不认这个 Token")
        if status == 409:
            raise Conflict(message, webhook="webhook" in message.lower())
        if status >= 500:
            raise TelegramError(f"Telegram 服务器出错（HTTP {status}）", sure=not sending)
        parameters = answer.get("parameters")
        raise Refused(status, message, parameters if isinstance(parameters, dict) else {})

    async def _post(
        self, method: str, body: Mapping[str, Any], upload: Upload | None, seconds: float
    ) -> tuple[int, dict[str, Any] | None]:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            if upload is None:
                response = await self._http.post(url, json=body, timeout=seconds)
            else:
                form = {k: v if isinstance(v, str) else json.dumps(v) for k, v in body.items()}
                files = {upload.field: (upload.name, upload.data, upload.content_type)}
                response = await self._http.post(url, data=form, files=files, timeout=seconds)
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if self._proxy:
                parts = urlsplit(self._proxy)
                raise ProxyDown(f"代理 {parts.hostname}:{parts.port} 连不上") from None
            raise TelegramError(f"连不上 Telegram（{type(exc).__name__}）") from None
        except httpx.HTTPError as exc:
            raise TelegramError(f"请求 Telegram 中途出错（{type(exc).__name__}）", sure=False) from None
        try:
            answer = response.json()
        except ValueError:
            answer = None
        return response.status_code, answer if isinstance(answer, dict) else None


def _retry_after(answer: dict[str, Any] | None) -> float:
    try:
        return float(((answer or {}).get("parameters") or {}).get("retry_after") or 1)
    except (TypeError, ValueError, AttributeError):
        return 1.0
