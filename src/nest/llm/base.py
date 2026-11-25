"""The contract every model provider implements (Ark Responses, OpenAI-compatible chat, Codex…)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

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
    # A call with web search (Codex, or Claude with 联网搜索 on): how many searches or page visits it
    # made, and the pages the answer cites as (title, url).
    searches: int = 0
    sources: tuple[tuple[str, str], ...] = ()
    # The model hit the output limit and stopped mid-sentence (a thinking model can spend most of a small
    # limit thinking): the text is only the start of what it meant to say.
    cut: bool = False
    # Tools of ours the model called while answering (伙伴模式): (tool name, what it was given), in order.
    tools_used: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Tool:
    """Something a model may do while answering (伙伴模式, nest.core.tools): a name, what it is for, and its
    input as a JSON schema object. Only a provider that `can_use_tools` is given any."""

    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


# Told what the model writes as it writes it (逐字流式): a piece of text, or None when what came so far is not
# the answer after all (a tool was called, or the call starts over). Only a provider with `streams` gets one.
TextSink = Callable[[str | None], None]

# Runs one call of a tool: (name, input) -> what to tell the model. Never raises: a failure is told as text.
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked for in a round of `converse`: the provider's id for the call (to answer it
    with), the tool's name and what it was given."""

    id: str
    name: str
    given: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Turn:
    """One round of a conversation with tools (任务, nest.core.tasks): what the model said this round, the
    tools it asked for (none: it is done), and `state` — the conversation so far in the provider's own form,
    this round's reply included, to be handed back with the tools' results for the next round."""

    text: str
    calls: tuple[ToolCall, ...]
    state: Any
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cut: bool = False


class ProviderError(Exception):
    """A failed model call. `retryable` marks network errors and 5xx, not bad requests or auth."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    """`search=True` (web search while answering a question) is only ever passed to a provider whose
    runtime `can_search` (Codex, or Claude with 联网搜索 on)."""

    name: str

    async def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int, temperature: float | None = None
    ) -> Completion: ...


class Painter(Protocol):
    """A provider that can also draw (Codex). `images`: reference pictures, as data URLs."""

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes: ...
