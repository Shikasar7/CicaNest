"""Scripted model provider for tests: returns queued replies, raises queued errors, records every request."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Sequence

from nest.llm.base import ChatMessage, Completion

# A Completion is returned as it is (e.g. one that searched the web and cites pages).
Reply = str | Completion | BaseException | Callable[[Sequence[ChatMessage]], str]


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
        self.closed = False

    def queue(self, *replies: Reply) -> None:
        self.replies.extend(replies)

    async def aclose(self) -> None:
        self.closed = True

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float | None = None,
        search: bool = False,
    ) -> Completion:
        # Like an HTTP client: a closed provider cannot call, also when it is closed mid-call.
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.requests.append(list(messages))
        self.searched.append(search)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        reply = self.replies.popleft() if self.replies else self.default
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Completion):
            return reply
        text = reply(messages) if callable(reply) else reply
        return Completion(text=text, latency_ms=self.delay * 1000)
