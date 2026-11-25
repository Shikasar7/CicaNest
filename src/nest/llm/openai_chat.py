"""OpenAI-compatible Chat Completions (local Grok proxy, DeepSeek official, most gateways).

Tools of ours (伙伴模式) go as function tools when the model's option `tools` is on (gateways differ in
whether they pass them through): the model's tool_calls are run through `run` and sent back as tool
messages, at most MAX_TOOL_ROUNDS times.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError, TextSink, Tool, ToolCall, ToolRunner, Turn
from nest.llm.http import client, is_sse, scrub, sse, status_error, transport_error

MAX_TOOL_ROUNDS = 10  # calls that may ask for our tools, per answer; the next one may not
TOOL_RESULT_CHARS = 12_000


def function_tools(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": dict(t.parameters)},
        }
        for t in tools
    ]


def arguments(raw: Any) -> dict[str, Any]:
    """A tool call's arguments: a JSON object as text (or already an object); {} when unreadable."""
    if isinstance(raw, dict):
        return raw
    try:
        found = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


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
    streams = True

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
        self._cannot_stream = False  # the gateway refused a streamed call once

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        tools: Sequence[Tool] = (),
        run: ToolRunner | None = None,
        on_text: TextSink | None = None,
    ) -> Completion:
        turns: list[dict[str, Any]] = [{"role": m.role, "content": _parts(m)} for m in messages]
        body: dict = {
            "model": self.model,
            "messages": turns,
            "max_tokens": max_tokens,
            "stream": False,
            **self._extra_body,
        }
        if tools and run is not None:
            body["tools"] = function_tools(tools)
        if (temperature if temperature is not None else self._temperature) is not None:
            body["temperature"] = temperature if temperature is not None else self._temperature
        headers = {"authorization": f"Bearer {self._key}"} if self._key else {}
        started = time.monotonic()
        used: list[tuple[str, str]] = []
        prompt_tokens = completion_tokens = 0
        counted = False
        # The last call may use no tool: it answers in words, saying how far it got. (Before, a sixth
        # round that asked for one more tool ended the answer with no text at all.)
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            if round_ == MAX_TOOL_ROUNDS and "tools" in body:
                body["tool_choice"] = "none"
            data = await self._streamed(body, headers, on_text) if on_text is not None else None
            if data is None:
                data = await self._post(body, headers)
                # It answers when asked the usual way: it was the streaming it refused.
                self._cannot_stream = self._cannot_stream or on_text is not None
            try:
                choice = data["choices"][0]
                message = choice["message"]
                text = _content(message)
                cut = choice.get("finish_reason") == "length"
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
            usage = data.get("usage") or {}
            if usage.get("prompt_tokens") is not None:
                counted = True
                prompt_tokens += usage.get("prompt_tokens") or 0
                completion_tokens += usage.get("completion_tokens") or 0
            calls = message.get("tool_calls") if isinstance(message, dict) else None
            if not calls or run is None or round_ == MAX_TOOL_ROUNDS:
                break
            if on_text is not None:
                on_text(None)  # what it said before the tool is not the answer
            turns.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                given = arguments(function.get("arguments"))
                result = (await run(name, given))[:TOOL_RESULT_CHARS]
                used.append((name, json.dumps(given, ensure_ascii=False)[:200]))
                turns.append({"role": "tool", "tool_call_id": call.get("id"), "content": result})
        return Completion(
            text=text,
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=prompt_tokens if counted else None,
            completion_tokens=completion_tokens if counted else None,
            cut=cut,
            tools_used=tuple(used),
        )

    async def _post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        try:
            response = await self._http.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code >= 400:
            raise status_error(response, self._key)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc

    async def _streamed(
        self, body: dict[str, Any], headers: dict[str, str], on_text: TextSink
    ) -> dict[str, Any] | None:
        """The same call, streamed: what it comes to, in the form of an answer that was not. None when the
        gateway refuses it (it is then asked the usual way, and from now on when that works)."""
        if self._cannot_stream:
            return None
        asked = {**body, "stream": True, "stream_options": {"include_usage": True}}
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: Any = None
        usage: dict[str, Any] = {}
        heard = False
        try:
            async with self._http.stream("POST", self._url, json=asked, headers=headers) as response:
                if response.status_code >= 400:
                    await response.aread()
                    if response.status_code in (400, 404, 415, 422):
                        return None
                    raise status_error(response, self._key)
                if not is_sse(response):
                    await response.aread()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
                    return data if isinstance(data, dict) else {}
                async for event in sse(response):
                    heard = True
                    if isinstance(event.get("error"), dict):
                        detail = scrub(str(event["error"].get("message") or ""), self._key)[:160]
                        raise ProviderError(f"模型中途出错：{detail}", retryable=True)
                    usage = event.get("usage") or usage
                    choice = next(iter(event.get("choices") or []), None)
                    if not isinstance(choice, dict):
                        continue
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    piece = _content(delta)
                    if piece:
                        text.append(piece)
                        on_text(piece)
                    for part in delta.get("tool_calls") or []:
                        number = part.get("index")
                        made = calls.setdefault(
                            number if isinstance(number, int) else len(calls),
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        made["id"] = part.get("id") or made["id"]
                        function = part.get("function") or {}
                        made["function"]["name"] += function.get("name") or ""
                        made["function"]["arguments"] += function.get("arguments") or ""
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if not heard:
            raise ProviderError("模型返回的格式看不懂", retryable=False)
        message: dict[str, Any] = {"content": "".join(text)}
        if calls:
            message["tool_calls"] = [calls[n] for n in sorted(calls)]
        return {"choices": [{"message": message, "finish_reason": finish}], "usage": usage}

    async def converse(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Tool],
        max_tokens: int,
        state: Any = None,
        results: Sequence[tuple[ToolCall, str]] = (),
        words_only: bool = False,
    ) -> Turn:
        """One round with tools (任务): the caller runs what it asks for and hands back the results."""
        turns: list[dict[str, Any]] = (
            [{"role": m.role, "content": _parts(m)} for m in messages] if state is None else list(state)
        )
        turns += [
            {"role": "tool", "tool_call_id": call.id, "content": result[:TOOL_RESULT_CHARS]}
            for call, result in results
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": turns,
            "max_tokens": max_tokens,
            "stream": False,
            **self._extra_body,
        }
        if tools:
            body["tools"] = function_tools(tools)
            if words_only:
                body["tool_choice"] = "none"
        if self._temperature is not None:
            body["temperature"] = self._temperature
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
            choice = data["choices"][0]
            message = choice["message"]
            text = _content(message)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
        raw_calls = message.get("tool_calls") if isinstance(message, dict) else None
        calls = tuple(
            ToolCall(
                str(c.get("id") or ""),
                str((c.get("function") or {}).get("name") or ""),
                arguments((c.get("function") or {}).get("arguments")),
            )
            for c in (raw_calls if isinstance(raw_calls, list) else [])
            if isinstance(c, dict)
        )
        reply: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        if calls:
            reply["tool_calls"] = raw_calls
        usage = data.get("usage") or {}
        return Turn(
            text=text,
            calls=calls,
            state=[*turns, reply],
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cut=choice.get("finish_reason") == "length",
        )

    async def aclose(self) -> None:
        await self._http.aclose()
