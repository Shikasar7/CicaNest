"""Anthropic Messages API (Claude): POST {base_url}/messages. The key goes in an x-api-key header, or
as `Authorization: Bearer` for relays (中转站) that want that (option auth_header="bearer").

The system messages go into the top-level `system` field; pictures become base64 image blocks. Recent
Claude models think before answering and that thinking counts against `max_tokens`, so each call asks
for THINKING_ROOM more than the reply itself needs. `temperature` is never sent: the newer models
refuse it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError
from nest.llm.http import client, status_error, transport_error

API_VERSION = "2023-06-01"
THINKING_ROOM = 2048
# Short thinking keeps group replies quick (思考档 on the model's form). At "low" the model often skips
# thinking, and now and then writes its judgment into the reply instead.
EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "low"


def _image(url: str) -> dict[str, Any]:
    """A data URL ("data:image/png;base64,…") as an Anthropic image block."""
    header, _, data = url.partition(",")
    media_type = header.removeprefix("data:").removesuffix(";base64")
    if not header.startswith("data:") or not header.endswith(";base64") or not data:
        raise ProviderError("图片格式不对，只能发 base64 的 data URL", retryable=False)
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _content(message: ChatMessage) -> Any:
    if not message.images:
        return message.content
    return [*(_image(url) for url in message.images), {"type": "text", "text": message.content}]


def request_parts(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    """(system text, messages): every system message joined into one, the rest in order. The API wants
    the conversation to start with the user."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns = [{"role": m.role, "content": _content(m)} for m in messages if m.role != "system"]
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": "（继续）"})
    return system, turns


class Anthropic:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        extra_body: dict[str, Any] | None = None,
        auth_header: str = "x-api-key",
        effort: str = DEFAULT_EFFORT,
        proxy: str | None = None,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/messages"
        self._key = api_key
        self._extra_body = dict(extra_body or {})
        self._bearer = auth_header == "bearer"
        self._effort = effort
        self._http = client(timeout=timeout, proxy=proxy, transport=transport)

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int, temperature: float | None = None
    ) -> Completion:
        system, turns = request_parts(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens + THINKING_ROOM,
            "messages": turns,
            "cache_control": {"type": "ephemeral"},  # the persona prefix is the same call after call
            "output_config": {"effort": self._effort},
            **self._extra_body,
        }
        if system:
            body["system"] = system
        headers = {"anthropic-version": API_VERSION}
        if self._key and self._bearer:
            headers["authorization"] = f"Bearer {self._key}"
        elif self._key:
            headers["x-api-key"] = self._key
        started = time.monotonic()
        try:
            response = await self._http.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code >= 400:
            raise status_error(response, self._key)
        try:
            data = response.json()
            blocks = data["content"]
            text = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
        if data.get("stop_reason") == "refusal":
            raise ProviderError("模型拒绝回答这一条", retryable=False)
        usage = data.get("usage") or {}
        prompt = [
            usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ]
        return Completion(
            text=text,
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=sum(n for n in prompt if isinstance(n, int)) if prompt[0] is not None else None,
            completion_tokens=usage.get("output_tokens"),
        )

    async def aclose(self) -> None:
        await self._http.aclose()
