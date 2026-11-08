"""What the adapters share: waiting between connection attempts, cutting long text, downloading a picture,
and catching up on what a connection missed. Each platform only says how to fetch one channel's messages."""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Collection

import httpx

from nest.adapters.base import AdapterState, AdapterStatus, BackfillSource, EventSink, MediaError, StatusSink
from nest.core.events import CaughtUp, ChannelRef, MessageEvent

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 15.0
RECENT_IDS = 256


class Backoff:
    """Doubling waits with a little jitter, back to the start after a connection that held."""

    def __init__(self, initial: float, maximum: float, stable_after: float) -> None:
        self._initial = initial
        self._maximum = maximum
        self._stable_after = stable_after
        self._delay = initial

    def next(self, lasted: float) -> float:
        """How long to wait, given how many seconds the connection that just ended lasted."""
        if lasted >= self._stable_after:
            self._delay = self._initial
        wait = self._delay * random.uniform(0.8, 1.2)
        self._delay = min(self._delay * 2, self._maximum)
        return wait


class StatusKeeper:
    """An adapter's state, told to the hub only when it changes."""

    def __init__(self, label: str, on_status: StatusSink | None) -> None:
        self._label = label
        self._on_status = on_status
        self.current = AdapterStatus("stopped", "未启动", time.time())

    def set(self, state: AdapterState, detail: str = "") -> None:
        if (state, detail) == (self.current.state, self.current.detail):
            return
        self.current = AdapterStatus(state, detail, time.time())
        log.info("%s：%s %s", self._label, state, detail)
        if self._on_status is not None:
            try:
                self._on_status(self.current)
            except Exception:
                log.exception("状态回调出错")


class SentIds:
    """The ids of messages nest sent lately. While a send is in flight every message from our own account
    counts: the platform may show it to us before the request returns."""

    def __init__(self) -> None:
        self._ids: deque[str] = deque(maxlen=RECENT_IDS)
        self._until = 0.0

    def sending(self, seconds: float) -> None:
        self._until = time.time() + seconds

    def add(self, message_id: str) -> None:
        self._ids.append(message_id)

    def __contains__(self, message_id: str) -> bool:
        return message_id in self._ids or time.time() < self._until


def split_text(text: str, limit: int, measure: Callable[[str], int] = len) -> list[str]:
    """Cut text into pieces of at most `limit` (by `measure`): between lines where it can, else mid-line."""
    if limit <= 0 or measure(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        joined = f"{current}\n{line}" if current else line
        if measure(joined) <= limit:
            current = joined
            continue
        if current:
            pieces.append(current)
        while measure(line) > limit:
            cut = _fit(line, limit, measure)
            pieces.append(line[:cut])
            line = line[cut:]
        current = line
    if current:
        pieces.append(current)
    return pieces


def _fit(line: str, limit: int, measure: Callable[[str], int]) -> int:
    low, high = 1, len(line)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(line[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


async def download(http: httpx.AsyncClient, url: str, limit: int) -> bytes:
    """A file by its url, at most `limit` bytes. MediaError says why not, in Chinese."""
    try:
        async with http.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as response:
            if response.status_code != 200:
                raise MediaError(f"下载失败（HTTP {response.status_code}）")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise MediaError(f"超过 {limit // 1024 // 1024} MB，不收")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise MediaError(f"下载失败（{type(exc).__name__}）") from None
    return b"".join(chunks)


async def catch_up(
    source: BackfillSource,
    fetch: Callable[[ChannelRef, float], Awaitable[list[MessageEvent]]],
    emit: EventSink,
    *,
    account: str,
    label: str,
    connected_at: float,
    seen_live: Callable[[], Collection[str]],
    prepare: Callable[[MessageEvent], Awaitable[MessageEvent]] | None = None,
) -> None:
    """Fetch history for known groups and deliver what was missed: newer than the newest stored message (same
    second included: storage dedupes the overlap) and older than this connection. Then say so with CaughtUp.
    `fetch` gives one group's recent messages; `prepare` finishes one that is really going out."""
    delivered = 0
    gaps: list[tuple[ChannelRef, float]] = []
    for channel, since in await source():
        if channel.kind != "group":
            continue
        gaps.append((channel, since))
        try:
            events = await fetch(channel, since)
        except Exception as exc:
            log.warning("%s 补收群 %s 失败：%s", label, channel.id, exc)
            continue
        for event in events:
            if event.channel != channel:
                continue
            if event.timestamp < since:
                continue  # older than what we already have: history, not a gap
            # Messages sent after we connected arrive live; skipping them keeps live ones marked live.
            if event.message_id in seen_live() or event.timestamp >= connected_at:
                continue
            if prepare is not None:
                event = await prepare(event)
            try:
                await emit(event)
            except Exception:
                log.exception("%s 处理补收消息出错，已跳过这一条", label)
                continue
            delivered += 1
    if delivered:
        log.info("%s 补收检查：%d 条可能在断线期间漏收（已收过的会被去重）", label, delivered)
    if gaps:
        try:
            await emit(CaughtUp(account, tuple(gaps), connected_at))
        except Exception:
            log.exception("%s 处理补收结果出错", label)
