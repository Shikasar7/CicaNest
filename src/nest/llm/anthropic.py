"""Anthropic Messages API (Claude): POST {base_url}/messages. The key goes in an x-api-key header, or
as `Authorization: Bearer` for relays (中转站) that want that (option auth_header="bearer").

The system messages go into the top-level `system` field; pictures become base64 image blocks. Recent
Claude models think before answering and that thinking counts against `max_tokens`, so each call asks
for THINKING_ROOM more than the reply itself needs. `temperature` is never sent: the newer models
refuse it.

Answering a question on a model with 联网搜索 on (option web_search), the call carries Anthropic's own web
search and page reading tools; they run on Anthropic's side and are billed per search. Only the text
after the last search is the answer: what the model says before searching ("我查一下") is not.

Tools of ours (伙伴模式: read a page, set a reminder…) are client tools: when the model asks for them the
call stops ("tool_use"), they are run here through `run`, and their results are sent back in a new call, at
most MAX_TOOL_ROUNDS times; the text after the last tool is the answer, as with search.

`converse` is one such call at a time (任务, which run for many rounds and are stopped between them): the
caller runs the tools and hands their results back with the state.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx

from nest.llm.base import ChatMessage, Completion, ProviderError, TextSink, Tool, ToolCall, ToolRunner, Turn
from nest.llm.http import client, is_sse, scrub, sse, status_error, transport_error

API_VERSION = "2023-06-01"
THINKING_ROOM = 2048
# Short thinking keeps group replies quick (思考档 on the model's form). At "low" the model often skips
# thinking, and now and then writes its judgment into the reply instead.
EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "low"
SEARCH_TIMEOUT = 120.0  # a few searches and page reads take 15–60 seconds
SEARCH_ROOM = 1024  # what the search queries themselves take of the output
MAX_SEARCHES = 5  # per answer
MAX_FETCHES = 3
# The server pauses a long search turn ("pause_turn"); it is sent back to go on, at most this many times.
MAX_CONTINUATIONS = 2
MAX_TOOL_ROUNDS = 10  # calls that end asking for our tools, per answer; the next one may not
TOOL_RESULT_CHARS = 12_000
# Models with the newer search tools, which filter the results before reading them; the others (Haiku,
# Claude 4.5 and older) have only the basic search.
FILTERING = re.compile(r"fable|mythos|(?:opus|sonnet)-(?:4-[6-9]|[5-9])", re.IGNORECASE)
NOT_ANSWER = frozenset({"text", "thinking", "redacted_thinking"})


def search_tools(model: str) -> list[dict[str, Any]]:
    if not FILTERING.search(model):
        return [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": MAX_FETCHES},
    ]


def answer_text(blocks: Sequence[Any]) -> str:
    """The text after the last tool block (all of it when there is none)."""
    last = max(
        (i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") not in NOT_ANSWER),
        default=-1,
    )
    return "".join(
        b.get("text", "") for b in blocks[last + 1 :] if isinstance(b, dict) and b.get("type") == "text"
    )


def search_record(
    blocks: Sequence[Any], usages: Sequence[dict[str, Any]]
) -> tuple[int, tuple[tuple[str, str], ...]]:
    """(searches and page reads, cited pages as (title, url) in order, each once)."""
    calls = sum(
        1
        for b in blocks
        if isinstance(b, dict)
        and b.get("type") == "server_tool_use"
        and b.get("name") in ("web_search", "web_fetch")
    )
    counted = 0
    for usage in usages:
        used = usage.get("server_tool_use") or {}
        counted += sum(
            n for n in (used.get("web_search_requests"), used.get("web_fetch_requests")) if isinstance(n, int)
        )
    sources, seen = [], set()
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        for cite in b.get("citations") or ():
            url = str(cite.get("url") or "") if isinstance(cite, dict) else ""
            if url and url not in seen:
                seen.add(url)
                sources.append((str(cite.get("title") or ""), url))
    return max(calls, counted), tuple(sources)


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
    streams = True

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
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        search: bool = False,
        tools: Sequence[Tool] = (),
        run: ToolRunner | None = None,
        on_text: TextSink | None = None,
    ) -> Completion:
        system, turns = request_parts(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens + THINKING_ROOM + (SEARCH_ROOM if search else 0),
            "messages": turns,
            "cache_control": {"type": "ephemeral"},  # the persona prefix is the same call after call
            "output_config": {"effort": self._effort},
            **self._extra_body,
        }
        if system:
            body["system"] = system
        ours = (
            [
                {"name": t.name, "description": t.description, "input_schema": dict(t.parameters)}
                for t in tools
            ]
            if run is not None
            else []
        )
        if search or ours:
            body["tools"] = (search_tools(self.model) if search else []) + ours
        started = time.monotonic()
        blocks: list[Any] = []
        usages: list[dict[str, Any]] = []
        used: list[tuple[str, str]] = []
        history = list(turns)
        rounds = 0
        for _ in range(MAX_CONTINUATIONS + MAX_TOOL_ROUNDS + 1):
            data = await self._post(body, SEARCH_TIMEOUT if (search or ours) else None, on_text)
            content = data.get("content")
            if not isinstance(content, list):
                raise ProviderError("模型返回的格式看不懂", retryable=False)
            blocks.extend(content)
            usages.append(data.get("usage") or {})
            stop = data.get("stop_reason")
            if stop == "tool_use" and ours and run is not None and rounds < MAX_TOOL_ROUNDS:
                rounds += 1
                results = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    given = block.get("input") if isinstance(block.get("input"), dict) else {}
                    name = str(block.get("name") or "")
                    result = (await run(name, given))[:TOOL_RESULT_CHARS]
                    used.append((name, json.dumps(given, ensure_ascii=False)[:200]))
                    results.append({"type": "tool_result", "tool_use_id": block.get("id"), "content": result})
                history += [{"role": "assistant", "content": content}, {"role": "user", "content": results}]
                body["messages"] = history
                if rounds == MAX_TOOL_ROUNDS:
                    # The last call may use no tool: it answers in words, saying how far it got.
                    body["tool_choice"] = {"type": "none"}
                continue
            if stop != "pause_turn" or not search:
                break
            # Sent back as it is, the paused turn goes on where it stopped.
            history = [*history, {"role": "assistant", "content": content}]
            body["messages"] = history
        stop = data.get("stop_reason")
        if stop == "refusal":
            raise ProviderError("模型拒绝回答这一条", retryable=False)
        searches, sources = search_record(blocks, usages) if search else (0, ())
        prompt = [
            usage.get(k)
            for usage in usages
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ]
        output = [usage.get("output_tokens") for usage in usages]
        return Completion(
            text=answer_text(blocks),
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=sum(n for n in prompt if isinstance(n, int))
            if usages[0].get("input_tokens") is not None
            else None,
            completion_tokens=sum(n for n in output if isinstance(n, int)) if output[0] is not None else None,
            searches=searches,
            sources=sources,
            cut=stop in ("max_tokens", "pause_turn"),
            tools_used=tuple(used),
        )

    async def converse(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[Tool],
        max_tokens: int,
        state: Any = None,
        results: Sequence[tuple[ToolCall, str]] = (),
    ) -> Turn:
        if state is None:
            system, turns = request_parts(messages)
        else:
            system, turns = state["system"], list(state["messages"])
        if results:
            turns.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call.id, "content": result[:TOOL_RESULT_CHARS]}
                        for call, result in results
                    ],
                }
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens + THINKING_ROOM,
            "messages": turns,
            "cache_control": {"type": "ephemeral"},
            "output_config": {"effort": self._effort},
            **self._extra_body,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.parameters)}
                for t in tools
            ]
        started = time.monotonic()
        data = await self._post(body, SEARCH_TIMEOUT)
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderError("模型返回的格式看不懂", retryable=False)
        stop = data.get("stop_reason")
        if stop == "refusal":
            raise ProviderError("模型拒绝回答这一条", retryable=False)
        calls = tuple(
            ToolCall(
                str(b.get("id") or ""),
                str(b.get("name") or ""),
                b.get("input") if isinstance(b.get("input"), dict) else {},
            )
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        )
        usage = data.get("usage") or {}
        prompt = [
            usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ]
        return Turn(
            text="".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            ),
            calls=calls,
            state={"system": system, "messages": [*turns, {"role": "assistant", "content": content}]},
            latency_ms=(time.monotonic() - started) * 1000,
            prompt_tokens=sum(n for n in prompt if isinstance(n, int))
            if usage.get("input_tokens") is not None
            else None,
            completion_tokens=usage.get("output_tokens"),
            cut=stop == "max_tokens",
        )

    async def _post(
        self, body: dict[str, Any], seconds: float | None, on_text: TextSink | None = None
    ) -> dict[str, Any]:
        headers = {"anthropic-version": API_VERSION}
        if self._key and self._bearer:
            headers["authorization"] = f"Bearer {self._key}"
        elif self._key:
            headers["x-api-key"] = self._key
        if on_text is not None:
            return await self._streamed(body, headers, seconds, on_text)
        try:
            if seconds is None:
                response = await self._http.post(self._url, json=body, headers=headers)
            else:
                response = await self._http.post(self._url, json=body, headers=headers, timeout=seconds)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if response.status_code >= 400:
            raise status_error(response, self._key)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
        if not isinstance(data, dict):
            raise ProviderError("模型返回的格式看不懂", retryable=False)
        return data

    async def _streamed(
        self, body: dict[str, Any], headers: dict[str, str], seconds: float | None, on_text: TextSink
    ) -> dict[str, Any]:
        """The same call, streamed: the message it comes to, as one that was not."""
        message: dict[str, Any] = {}
        blocks: list[dict[str, Any]] = []
        given: dict[int, str] = {}  # a tool call's input, in pieces
        timeout = {} if seconds is None else {"timeout": seconds}
        try:
            async with self._http.stream(
                "POST", self._url, json={**body, "stream": True}, headers=headers, **timeout
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise status_error(response, self._key)
                if not is_sse(response):  # a relay that does not stream
                    await response.aread()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError("模型返回的格式看不懂", retryable=False) from exc
                    return data if isinstance(data, dict) else {}
                async for event in sse(response):
                    kind, n = event.get("type"), event.get("index")
                    if kind == "message_start":
                        message = dict(event.get("message") or {})
                    elif kind == "content_block_start":
                        block = dict(event.get("content_block") or {})
                        blocks.append(block)
                        if block.get("type") not in NOT_ANSWER:
                            on_text(None)  # what it said before a tool is not the answer
                    elif kind == "content_block_delta" and isinstance(n, int) and n < len(blocks):
                        self._add(blocks[n], event.get("delta") or {}, given, n, on_text)
                    elif kind == "content_block_stop" and isinstance(n, int) and n in given:
                        try:
                            blocks[n]["input"] = json.loads(given.pop(n) or "{}")
                        except ValueError:
                            blocks[n]["input"] = {}
                    elif kind == "message_delta":
                        message |= {k: v for k, v in (event.get("delta") or {}).items() if v is not None}
                        usage = event.get("usage") or {}
                        message["usage"] = {**(message.get("usage") or {}), **usage}
                    elif kind == "error":
                        error = event.get("error") or {}
                        detail = scrub(str(error.get("message") or ""), self._key)[:160]
                        raise ProviderError(f"模型中途出错：{detail}", retryable=True)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
        if not message:
            raise ProviderError("模型返回的格式看不懂", retryable=False)
        return message | {"content": blocks}

    @staticmethod
    def _add(
        block: dict[str, Any], delta: dict[str, Any], given: dict[int, str], n: int, on_text: TextSink
    ) -> None:
        kind = delta.get("type")
        if kind == "text_delta":
            block["text"] = block.get("text", "") + str(delta.get("text") or "")
            on_text(str(delta.get("text") or ""))
        elif kind == "input_json_delta":
            given[n] = given.get(n, "") + str(delta.get("partial_json") or "")
        elif kind == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + str(delta.get("thinking") or "")
        elif kind == "signature_delta":
            block["signature"] = block.get("signature", "") + str(delta.get("signature") or "")
        elif kind == "citations_delta":
            block["citations"] = [*(block.get("citations") or []), delta.get("citation")]

    async def aclose(self) -> None:
        await self._http.aclose()
