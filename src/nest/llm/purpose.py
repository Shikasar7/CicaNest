"""What a model call is for (用量页的「用在哪」): set where the work starts, read where the call is logged.

A context variable, so it reaches every call made on the way (a backup model, a retry, a tool's own call) and
the asyncio tasks started there, without being handed through each function.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, TypeVar

PURPOSES = {
    "chat": "聊天",
    "group": "平台的群",
    "desk_group": "桌面群",
    "chatter": "桌宠碎碎念",
    "outreach": "主动找你",
    "game": "小游戏",
    "life": "她们的日子",
    "task": "后台任务",
    "reminder": "提醒",
    "memory": "整理记忆和回忆",
    "draw": "画图",
    "watch": "监控",
    "upkeep": "表情包、黑话、资料等整理",
    "": "其他",
}
_now: ContextVar[str] = ContextVar("nest_llm_purpose", default="")
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def current() -> str:
    return _now.get()


def begin(purpose: str) -> None:
    """For the rest of this asyncio task."""
    _now.set(purpose)


@contextmanager
def using(purpose: str) -> Iterator[None]:
    token = _now.set(purpose)
    try:
        yield
    finally:
        _now.reset(token)


def for_purpose(purpose: str) -> Callable[[F], F]:
    """Decorates an async function: the model calls in it are for `purpose`."""

    def decorate(function: F) -> F:
        @wraps(function)
        async def inner(*args: Any, **kwargs: Any) -> Any:
            with using(purpose):
                return await function(*args, **kwargs)

        return inner  # type: ignore[return-value]

    return decorate
