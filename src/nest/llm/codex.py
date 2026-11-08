"""ChatGPT subscription through the Codex backend.

This is not a public API and may change without notice. Credentials come from the Codex CLI's
auth file (~/.codex/auth.json); on HTTP 401 the access token is refreshed and written back, as the
Codex CLI itself does.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError
from nest.llm.http import client, status_error, transport_error
from nest.llm.openai_responses import output_text

DEFAULT_URL = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # the Codex CLI's public OAuth client id
# For prompts without a system message (表情 descriptions, 截图读字): they say themselves what to write, so
# nothing here may narrow it. "One sentence" made every description come back without its JSON.
INSTRUCTIONS = "不要使用工具。照用户的要求回答。"
# Answering a question: the model searches the web by itself when the facts need checking.
SEARCH_INSTRUCTIONS = "需要查证的事实先联网搜索再回答，不要凭印象编。用中文回答，或 SKIP。"
SEARCH_TIMEOUT = 120.0  # a few searches and page visits take 15–40 seconds
DRAW_INSTRUCTIONS = "用 image_generation 工具画一张图，不要说别的。"
DRAW_TIMEOUT = 180.0  # one picture takes under a minute

# One refresh at a time per login file, shared by every Codex instance: during a settings change the old
# and the new runtime use the same login, and a refresh token is single-use.
_REFRESH_LOCKS: dict[Path, asyncio.Lock] = {}


def _refresh_lock(auth_file: Path) -> asyncio.Lock:
    return _REFRESH_LOCKS.setdefault(auth_file.resolve(), asyncio.Lock())


def parse_sse(raw: str) -> tuple[str, dict[str, Any]]:
    """(text, usage) from a Codex event stream: the completed response if present, else the deltas."""
    completed, deltas, usage = None, [], {}
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if event.get("type") == "response.output_text.delta":
            deltas.append(str(event.get("delta") or ""))
        elif event.get("type") == "response.completed":
            response = event.get("response") or {}
            completed = output_text(response)
            usage = response.get("usage") or {}
    return (completed if completed else "".join(deltas)), usage


def parse_search(raw: str) -> tuple[int, tuple[tuple[str, str], ...]]:
    """(searches and page visits, cited pages as (title, url) in order, each once) from a Codex event
    stream of a call with web search."""
    searches, sources, seen = 0, [], set()
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if event.get("type") == "response.output_item.done" and (event.get("item") or {}).get("type") == (
            "web_search_call"
        ):
            searches += 1
        elif event.get("type") == "response.output_text.annotation.added":
            note = event.get("annotation") or {}
            url = str(note.get("url") or "")
            if note.get("type") == "url_citation" and url and url not in seen:
                seen.add(url)
                sources.append((str(note.get("title") or ""), url))
    return searches, tuple(sources)


def parse_image(raw: str) -> str | None:
    """The base64 picture from a Codex event stream of a call with image generation; None without one."""
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        item = event.get("item") or {} if event.get("type") == "response.output_item.done" else {}
        if item.get("type") == "image_generation_call" and item.get("result"):
            return str(item["result"])
    return None


class Codex:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str = DEFAULT_URL,
        auth_file: str | Path = "~/.codex/auth.json",
        instructions: str = INSTRUCTIONS,
        proxy: str | None = None,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
        token_url: str = TOKEN_URL,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/responses"
        self._auth_file = Path(auth_file).expanduser()
        self._instructions = instructions
        self._token_url = token_url
        self._http = client(timeout=timeout, proxy=proxy, transport=transport)

    def _tokens(self) -> tuple[dict[str, Any], str, str]:
        try:
            data = json.loads(self._auth_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProviderError(f"读不到 Codex 登录信息（{self._auth_file}）", retryable=False) from exc
        tokens = data.get("tokens") or {}
        access, account = tokens.get("access_token") or "", tokens.get("account_id") or ""
        if not access or not account:
            raise ProviderError("Codex 订阅登录不完整，请在 Codex 里重新登录", retryable=False)
        return data, access, account

    async def _refresh(self, stale_access: str) -> str:
        async with _refresh_lock(self._auth_file):
            data, access, _ = self._tokens()
            if access != stale_access:  # another call refreshed it meanwhile
                return access
            refresh = (data.get("tokens") or {}).get("refresh_token") or ""
            if not refresh:
                raise ProviderError("Codex 登录已过期且缺少刷新凭证，请在 Codex 里重新登录", retryable=False)
            try:
                response = await self._http.post(
                    self._token_url,
                    json={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID},
                    headers={"accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise transport_error(exc) from exc
            if response.status_code >= 400:
                raise ProviderError(f"刷新 Codex 登录失败（HTTP {response.status_code}）", retryable=False)
            fresh = response.json()
            tokens = data.setdefault("tokens", {})
            for key in ("access_token", "refresh_token", "id_token"):
                if fresh.get(key):
                    tokens[key] = fresh[key]
            temp = self._auth_file.with_suffix(".nest-tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self._auth_file)
            return tokens.get("access_token") or ""

    async def _call(
        self, body: dict[str, Any], access: str, account: str, seconds: float | None = None
    ) -> httpx.Response:
        """`seconds`: how long this request may take, instead of the client's timeout."""
        headers = {
            "authorization": f"Bearer {access}",
            "chatgpt-account-id": account,
            "openai-beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
        }
        try:
            if seconds is None:
                return await self._http.post(self._url, json=body, headers=headers)
            return await self._http.post(self._url, json=body, headers=headers, timeout=seconds)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc

    async def _post(self, body: dict[str, Any], seconds: float | None = None) -> httpx.Response:
        """One request, with the token refreshed on a 401. ProviderError on any other failure."""
        _, access, account = self._tokens()
        response = await self._call(body, access, account, seconds)
        if response.status_code == 401:
            access = await self._refresh(access)
            response = await self._call(body, access, account, seconds)
        if response.status_code >= 400:
            raise status_error(response, access)
        return response

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        search: bool = False,
    ) -> Completion:
        # The prompt's own system message (persona and rules) is the instructions. Merged into the input
        # under the fixed "one sentence of Chinese" instruction it lost: replies meant to be JSON (答题's
        # sorting) came back as a sentence.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        prompt = "\n\n".join(m.content for m in messages if m.role != "system")
        tools: dict[str, Any] = {"tool_choice": "none"}
        if search:
            tools = {"tools": [{"type": "web_search"}], "tool_choice": "auto"}
            instructions = f"{SEARCH_INSTRUCTIONS}\n\n{system}" if system else SEARCH_INSTRUCTIONS
        else:
            instructions = system or self._instructions
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        *({"type": "input_image", "image_url": url} for m in messages for url in m.images),
                    ],
                }
            ],
            "store": False,
            "stream": True,
            "max_output_tokens": max_tokens,
            **tools,
        }
        seconds = SEARCH_TIMEOUT if search else None
        started = time.monotonic()
        response = await self._post(body, seconds)
        text, usage = parse_sse(response.text)
        searches, sources = parse_search(response.text) if search else (0, ())
        return Completion(
            text=text,
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            searches=searches,
            sources=sources,
        )

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes:
        """One picture from the subscription's image generation. `images`: reference pictures, data URLs."""
        body = {
            "model": self.model,
            "instructions": DRAW_INSTRUCTIONS,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        *({"type": "input_image", "image_url": url} for url in images),
                    ],
                }
            ],
            "store": False,
            "stream": True,
            "tools": [{"type": "image_generation"}],
            "tool_choice": "auto",
        }
        response = await self._post(body, DRAW_TIMEOUT)
        encoded = parse_image(response.text)
        if encoded is None:
            said = " ".join(parse_sse(response.text)[0].split())[:80]
            raise ProviderError(f"模型没有画出图{f'：{said}' if said else ''}", retryable=False)
        try:
            return base64.b64decode(encoded, validate=True)
        except binascii.Error:
            raise ProviderError("模型返回的图片读不了", retryable=False) from None

    async def aclose(self) -> None:
        await self._http.aclose()
