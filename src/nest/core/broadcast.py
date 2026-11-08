"""Fan-out of live updates to console connections (SSE). Slow listeners drop their oldest items."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

Item = tuple[str, dict[str, Any]]

CLOSED = "closed"  # last item a console stream gets: nest is stopping


class Broadcaster:
    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Item]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[Item]:
        queue: asyncio.Queue[Item] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Item]) -> None:
        self._subscribers.discard(queue)

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait((kind, data))

    def close(self) -> None:
        """End every console stream. An open stream would otherwise hold the server's shutdown open."""
        self.publish(CLOSED, {})
