"""OpenAI-compatible Chat Completions (local Grok proxy, DeepSeek official, most gateways)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError
from nest.llm.http import client, status_error, transport_error


def _content(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):  # some gateways return content parts
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def _parts(message: ChatMessage) -> Any:
    """Plain text, or text plus image parts when the message carries pictures."""
    if not message.images:
        return message.content
    images = [{"type": "image_url", "image_url": {"url": url}} for url in message.images]
    return [{"type": "text", "text": message.content}, *images]


class OpenAIChat:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._temperature = temperature
        self._extra_body = dict(extra_body or {})  # vendor-specific request fields
        self._http = client(timeout=timeout, proxy=proxy, transport=transport)

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int, temperature: float | None = None
    ) -> Completion:
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": _parts(m)} for m in messages],
            "max_tokens": max_tokens,
            "stream": False,
            **self._extra_body,
        }
        if (temperature if temperature is not None else self._temperature) is not None:
            body["temperature"] = temperature if temperature is not None else self._temperature
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        started = time.monotonic()
        try:
            response = await self._http.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code >= 400:
            raise status_error(response, self._key)
        try:
            data = response.json()
            text = _content(data["choices"][0]["message"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def aclose(self) -> None:
        await self._http.aclose()
