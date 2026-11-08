"""截图读字: players ask about an error by posting a screenshot of it. When a
bot is called with a question (答题), the pictures in the question, or in the message it quotes, are read by
the vision model (the one 表情包 uses) and their words go along with the question, so sorting and the
help-docs search see the error text.

Only ordinary pictures are read (QQ marks stickers sub_type 1). Long runs that look like keys are hidden,
by the model and again here. A picture is read once (cached by its hash for a while).

图片说明: the same reader writes one line about the pictures people post in the chat, so a bot can talk about
them instead of seeing [图片]. A picture gets its line once, shared by the bots, within an hourly budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx
from PIL import Image

from nest.adapters.base import Adapter
from nest.adapters.common import download
from nest.core.stickers import data_url
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.runtime import ProviderRuntime, ProviderUnavailable
from nest.watch import fetch as watch_fetch

log = logging.getLogger(__name__)

MAX_PICTURES = 3  # read at most this many per question
MAX_BYTES = 10 * 1024 * 1024
SIDE = 1280  # long enough side that small text stays readable
CACHE = 200
TEXT_LIMIT = 1500
READ_PROMPT = (
    "这是 {place}里有人发的截图（多半是软件界面或报错）。把图里的文字原样抄出来，尤其是报错、提示框、按钮、"
    "菜单和设置项上的字，按原来的顺序和换行。看不清的字用 □ 代替。像 API Key、令牌、密码这样的一长串字母数字"
    "（比如 sk- 开头的），写成 ***，不要抄出来。图里没有文字就只输出「（没有文字）」。"
    "只输出抄下的文字，不要解释。"
)
# What a key or token looks like: hidden even if the model copied it.
SECRET = re.compile(r"\b(?:sk|pk|rk|ak)-[A-Za-z0-9_\-]{8,}|\b[A-Za-z0-9_\-]{32,}\b")
NO_TEXT = "（没有文字）"
NOTE_PROMPT = (
    "这是聊天里有人发的图片。用一句不超过 30 字的中文说清图上是什么，图上有字就带上关键的字。"
    "像 API Key、令牌、密码这样的一长串字母数字写成 ***。只输出这一句，不要解释。"
)
NOTE_PICTURES = 2  # at most this many new pictures per reply: the reply waits for them
NOTE_TIMEOUT = 12.0
NOTE_CHARS = 60
NOTES = 300
HOUR = 3600.0


def pictures(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Download URLs of the ordinary pictures (not stickers) in `rows`, in order, at most MAX_PICTURES."""
    urls: list[str] = []
    for row in rows:
        for segment in row.get("segments") or ():
            data = segment.get("data") or {}
            url = str(data.get("url") or "")
            if (
                segment.get("type") == "image"
                and str(data.get("sub_type", "0")) != "1"
                and url.startswith(("http://", "https://"))
                and url not in urls
            ):
                urls.append(url)
    return urls[:MAX_PICTURES]


def picture_keys(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(key, url) of each ordinary picture in a message. The key is QQ's file name when it has one: the
    url differs from one account to the next."""
    found = []
    for segment in row.get("segments") or ():
        data = segment.get("data") or {}
        url = str(data.get("url") or "")
        if (
            segment.get("type") == "image"
            and str(data.get("sub_type", "0")) != "1"
            and url.startswith(("http://", "https://"))
        ):
            found.append((str(data.get("file") or url), url))
    return found


def hide_secrets(text: str) -> str:
    return SECRET.sub("***", text)


def _png(data: bytes) -> bytes:
    """Blocking. The picture as a PNG, at most SIDE pixels on its long side; ValueError if it is none."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            frame = image.convert("RGB")
    except Exception as exc:
        raise ValueError(f"不是能用的图片（{type(exc).__name__}）") from None
    frame.thumbnail((SIDE, SIDE))
    out = io.BytesIO()
    frame.save(out, format="PNG")
    return out.getvalue()


class ScreenshotReader:
    def __init__(
        self,
        vision: Callable[[], ProviderRuntime | None],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        notes_per_hour: Callable[[], int] = lambda: 0,
        adapters: Mapping[str, Adapter] | None = None,
    ) -> None:
        self._vision = vision
        self._transport = transport
        self._adapters = adapters if adapters is not None else {}
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._notes_per_hour = notes_per_hour
        self._notes: OrderedDict[str, str] = OrderedDict()
        self._noting: dict[str, asyncio.Task[str]] = {}
        self._noted: deque[float] = deque()

    @property
    def ready(self) -> bool:
        return self._vision() is not None

    async def read(self, urls: list[str], *, place: str = "QQ 群", account: str = "") -> list[str]:
        """The words in each picture (failures and pictures without text left out)."""
        runtime = self._vision()
        if runtime is None:
            return []
        out = []
        for url in urls:
            text = await self._read_one(url, runtime, place, account)
            if text:
                out.append(text)
        return out

    async def _read_one(self, url: str, runtime: ProviderRuntime, place: str, account: str) -> str:
        start = time.perf_counter()
        try:
            data = await self._download(url, account)
        except (httpx.HTTPError, ValueError) as exc:
            log.info("截图下载不了：%s", exc)
            return ""
        key = hashlib.sha256(data).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        try:
            png = await asyncio.to_thread(_png, data)
        except ValueError as exc:
            log.info("截图读不了：%s", exc)
            return ""
        messages = [ChatMessage("user", READ_PROMPT.format(place=place), images=(data_url(png),))]
        try:
            completion, _ = await runtime.complete(messages, bot_id=None, max_tokens=800)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("看图模型没读出截图：%s", exc)
            return ""
        text = hide_secrets(completion.text.strip())[:TEXT_LIMIT]
        text = "" if not text or text == NO_TEXT else text
        self._cache[key] = text
        while len(self._cache) > CACHE:
            self._cache.popitem(last=False)
        log.info("读了一张截图（%d 字，%.1f 秒）", len(text), time.perf_counter() - start)
        return text

    async def noted(
        self, context: list[dict[str, Any]], fresh: list[dict[str, Any]], account: str = ""
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The rows with [图片] replaced by what the pictures show: the new ones get a line now, older ones
        keep the line they got. A picture without a line stays [图片]."""
        wanted = [pair for row in fresh for pair in picture_keys(row)]
        await self._write_notes([(key, url) for key, url in wanted if key not in self._notes], account)
        return [self._with_notes(row) for row in context], [self._with_notes(row) for row in fresh]

    def _with_notes(self, row: dict[str, Any]) -> dict[str, Any]:
        text = str(row.get("text") or "")
        for key, _ in picture_keys(row):
            if self._notes.get(key):
                text = text.replace("[图片]", f"[图片：{self._notes[key]}]", 1)
        return row | {"text": text} if text != row.get("text") else row

    async def _write_notes(self, pairs: list[tuple[str, str]], account: str) -> None:
        now = time.time()
        while self._noted and self._noted[0] < now - HOUR:
            self._noted.popleft()
        tasks = []
        for key, url in dict(pairs).items():
            if len(tasks) >= NOTE_PICTURES:
                break
            task = self._noting.get(key)  # the bots of one group see the same picture at the same moment
            if task is None:
                if len(self._noted) >= self._notes_per_hour() or self._vision() is None:
                    continue
                self._noted.append(now)
                task = asyncio.create_task(self._write_note(key, url, account))
                self._noting[key] = task
                task.add_done_callback(lambda _, key=key: self._noting.pop(key, None))
            tasks.append(task)
        if tasks:
            await asyncio.wait(tasks, timeout=NOTE_TIMEOUT)  # a late line still serves the next reply

    async def _write_note(self, key: str, url: str, account: str) -> str:
        runtime = self._vision()
        if runtime is None:
            return ""
        try:
            png = await asyncio.to_thread(_png, await self._download(url, account))
            completion, _ = await runtime.complete(
                [ChatMessage("user", NOTE_PROMPT, images=(data_url(png),))], bot_id=None, max_tokens=120
            )
        except (httpx.HTTPError, ValueError, ProviderUnavailable, ProviderError) as exc:
            log.info("没写出图片说明：%s", exc)
            return ""
        note = " ".join(hide_secrets(completion.text).split())[:NOTE_CHARS]
        self._notes[key] = note
        while len(self._notes) > NOTES:
            self._notes.popitem(last=False)
        return note

    async def _download(self, url: str, account: str) -> bytes:
        """Through the account that saw the picture (Discord's CDN needs its proxy). A test's transport comes
        first. Raises ValueError."""
        adapter = None if self._transport else self._adapters.get(account)
        if adapter is not None:
            return await adapter.fetch_media(url, MAX_BYTES)
        transport = self._transport or watch_fetch.network_transport(None)
        async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
            return await download(http, url, MAX_BYTES)
