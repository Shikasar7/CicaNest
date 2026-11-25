"""Scripted model provider for tests: returns queued replies, raises queued errors, records every request."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from nest.llm.base import ChatMessage, Completion, ToolRunner, Turn
from nest.llm.base import ToolCall as Call


@dataclass(frozen=True)
class ToolCall:
    """A scripted reply that calls one of our tools (the call must have been given some), then goes on."""

    name: str
    given: dict = field(default_factory=dict)


# A Completion (or, in a task, a Turn) is returned as it is (e.g. one that searched the web and cites pages).
Reply = str | Completion | Turn | BaseException | Callable[[Sequence[ChatMessage]], str] | ToolCall


class FakeProvider:
    def __init__(
        self, *replies: Reply, name: str = "fake", delay: float = 0.0, default: str = "SKIP"
    ) -> None:
        self.name = name
        self.delay = delay
        self.default = default
        self.replies: deque[Reply] = deque(replies)
        self.requests: list[list[ChatMessage]] = []
        self.searched: list[bool] = []  # per request: whether web search was allowed
        self.tool_lists: list[list[str]] = []  # per request: the names of the tools it was given
        self.tool_results: list[tuple[str, str]] = []  # (tool, what it answered), every call
        self.rounds: list[
            list[tuple[str, str]]
        ] = []  # converse: the (tool, result) pairs handed back, per round
        self.words_only: list[bool] = []  # converse: whether no tool could be asked for, per round
        self.closed = False
        self.streams = False  # set by a test: it tells what it writes, ten characters at a time
        self.letters = 0.0  # seconds between two of them

    def queue(self, *replies: Reply) -> None:
        self.replies.extend(replies)

    async def aclose(self) -> None:
        self.closed = True

    async def converse(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence = (),
        max_tokens: int,
        state: Any = None,
        results: Sequence[tuple[Call, str]] = (),
        words_only: bool = False,
    ) -> Turn:
        """A round of a task: a scripted ToolCall asks for that tool, anything else is the last words."""
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.requests.append(list(messages))
        self.tool_lists.append([t.name for t in tools])
        self.rounds.append([(call.name, result) for call, result in results])
        self.words_only.append(words_only)
        self.tool_results.extend((call.name, result) for call, result in results)
        if self.delay:
            await asyncio.sleep(self.delay)
        number = (state or 0) + 1
        reply = self.replies.popleft() if self.replies else self.default
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Turn):
            return replace(reply, state=number)
        if isinstance(reply, ToolCall):
            call = Call(f"call-{number}", reply.name, dict(reply.given))
            return Turn(
                text="", calls=(call,), state=number, latency_ms=0, prompt_tokens=10, completion_tokens=5
            )
        text = reply(messages) if callable(reply) else str(reply)
        return Turn(text=text, calls=(), state=number, latency_ms=0, prompt_tokens=10, completion_tokens=5)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        search: bool = False,
        tools: Sequence = (),
        run: ToolRunner | None = None,
        on_text: Callable[[str | None], None] | None = None,
    ) -> Completion:
        # Like an HTTP client: a closed provider cannot call, also when it is closed mid-call.
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.requests.append(list(messages))
        self.searched.append(search)
        self.tool_lists.append([t.name for t in tools])
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        reply = self.replies.popleft() if self.replies else self.default
        used: list[tuple[str, str]] = []
        while isinstance(reply, ToolCall):
            if run is None:
                raise AssertionError(f"scripted to call {reply.name}, but this call was given no tools")
            self.tool_results.append((reply.name, await run(reply.name, dict(reply.given))))
            used.append((reply.name, str(reply.given)))
            reply = self.replies.popleft() if self.replies else self.default
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Completion):
            return reply
        text = reply(messages) if callable(reply) else reply
        if on_text is not None:
            for start in range(0, len(text), 10):
                on_text(text[start : start + 10])
                await asyncio.sleep(self.letters)
        return Completion(text=text, latency_ms=self.delay * 1000, tools_used=tuple(used))
