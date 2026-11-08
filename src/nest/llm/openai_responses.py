"""OpenAI Responses-style API (Volcengine Ark coding plan: .../api/plan/v3/responses).

By default the conversation is flattened into one text `input`, the way Ark is known to
accept it. Set option input_format="messages" to send role-tagged input instead.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError
from nest.llm.http import client, status_error, transport_error


def output_text(data: Any) -> str:
    """Text of a Responses payload: `output_text`, or the text parts of `output[*].content[*]`."""
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") in (None, "output_text", "text")
            )
    return "".join(parts)


class OpenAIResponses:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        input_format: str = "text",
        extra_body: dict[str, Any] | None = None,
        proxy: str | None = None,
        timeout: float = 40.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/responses"
        self._key = api_key
        self._input_format = input_format
        # Vendor-specific request fields, e.g. Ark's {"thinking": {"type": "disabled"}}: without it the
        # reasoning pass can use up a small output budget and leave no text.
        self._extra_body = dict(extra_body or {})
        self._http = client(timeout=timeout, proxy=proxy, transport=transport)

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int, temperature: float | None = None
    ) -> Completion:
        if any(m.images for m in messages):  # pictures need role-tagged input with content parts
            input_: Any = [
                {
                    "role": m.role,
                    "content": [
                        {"type": "input_text", "text": m.content},
                        *({"type": "input_image", "image_url": url} for url in m.images),
                    ],
                }
                for m in messages
            ]
        elif self._input_format == "messages":
            input_ = [{"role": m.role, "content": m.content} for m in messages]
        else:
            input_ = "\n\n".join(m.content for m in messages)
        body: dict = {
            "model": self.model,
            "input": input_,
            "max_output_tokens": max_tokens,
            **self._extra_body,
        }
        if temperature is not None:
            body["temperature"] = temperature
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
        except ValueError as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
        usage = data.get("usage") or {}
        return Completion(
            text=output_text(data),
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            cut=data.get("status") == "incomplete",
        )

    async def aclose(self) -> None:
        await self._http.aclose()
