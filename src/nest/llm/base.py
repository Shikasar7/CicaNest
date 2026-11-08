"""The contract every model provider implements (Ark Responses, OpenAI-compatible chat, Codex…)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Some models and relays hand their reasoning back inside the text. An opening tag without its end took
# the rest of the answer with it; an end without its opening means what came before it was the reasoning.
THOUGHT = re.compile(r"<(think|thinking|reasoning)>.*?(?:</\1>|\Z)", re.DOTALL | re.IGNORECASE)
THOUGHT_END = re.compile(r"^.*</(?:think|thinking|reasoning)>", re.DOTALL | re.IGNORECASE)


def without_thought(text: str) -> str:
    return THOUGHT_END.sub("", THOUGHT.sub("", text)).strip()


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str
    # Pictures that go with the text, as data URLs ("data:image/png;base64,…"): only a model that can see
    # images understands them (表情包 descriptions).
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # A call with web search (Codex only): how many searches or page visits it made, and the pages the
    # answer cites as (title, url).
    searches: int = 0
    sources: tuple[tuple[str, str], ...] = ()


class ProviderError(Exception):
    """A failed model call. `retryable` marks network errors and 5xx, not bad requests or auth."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    """`search=True` (web search while answering a question) is only ever passed to a Codex provider."""

    name: str

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int, temperature: float | None = None
    ) -> Completion: ...


class Painter(Protocol):
    """A provider that can also draw (Codex). `images`: reference pictures, as data URLs."""

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes: ...
