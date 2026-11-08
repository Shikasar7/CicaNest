"""表情包: collect the stickers group members send, describe them with a model that can
see pictures, offer a few to a bot when it speaks, and keep the collection within its limits.

  message (group that collects) → sticker segments → queue (one download per picture: QQ names the file
  by its MD5, and the three accounts receive the same message)
  → download (≤ 2 MB, must decode as PNG/JPEG/GIF/WebP) → data/stickers/ab/<sha256>.<ext> → row "pending"
  → describer (vision model, hourly budget) → "ready" | "unsuitable" | "failed"
Ordinary photos (image sub_type 0) are never downloaded or shown to a model.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import time
from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw

from nest.adapters.base import Adapter, MediaError
from nest.adapters.common import download
from nest.adapters.registry import capabilities_of
from nest.core.broadcast import Broadcaster
from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.core.segments import render_text
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.runtime import ProviderRegistry, ProviderRuntime, ProviderUnavailable
from nest.storage.accounts import account_id
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo
from nest.storage.slang import GroupFeatures, GroupFeatureStore, group_key_of
from nest.storage.stickers import NewSticker, StickerStore, label
from nest.watch import fetch as watch_fetch

log = logging.getLogger(__name__)

HOUR = 3600.0
DAY = 86400.0
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
MD5_NAME = re.compile(r"^([0-9A-Fa-f]{32})(\.[A-Za-z0-9]{1,5})?$")
MAX_PIXELS = 4096 * 4096
# 看起来一样: a 16×16 gradient hash per frame. Of 256 bits, a copy saved again
# differs by about 5, the same picture with another caption by 10 or more, unrelated pictures by 90 or more.
LOOK_SIDE = 16
LOOK_DISTANCE = 8
LOOK_MIN_BITS = 8  # fewer: a plain picture, which says nothing about what it shows
PREVIEW_SIDE = 512  # what the vision model sees of a still picture: at most this wide and high
SHEET_FRAMES = 6  # …and of an animated one: this many frames, spread over it, side by side on one sheet
SHEET_CELL = 256
SHEET_GAP = 8
QUEUE_SIZE = 200
ORPHAN_AGE = HOUR  # a file without a row is only removed once it is this old (it may be being added)
DESCRIBE_RETRY = 600.0  # the vision model is unavailable: try the queue again after this long
SETTINGS_KEY = "stickers"

DESCRIBE_PROMPT = (
    "你在帮一个 QQ 群 bot 整理表情包收藏。看这张表情包，只输出一个 JSON 对象，不要别的文字：\n"
    '{"description": "一句话说清画面和它表达的意思，不超过30字", '
    '"text": "图上的文字，原样照抄，没有就空字符串", '
    '"tags": ["最多3个情绪或用途，比如 无语、开心、谢谢、晚安"], "suitable": true, "reason": ""}\n'
    "这些情况 suitable 填 false，reason 写原因：色情或擦边、血腥恐怖、真人的私人照片、广告或二维码、"
    "政治敏感、歧视或人身攻击。"
)
ANIMATED_NOTE = (
    "\n这是一张动图，不是拼图或多格漫画：图里从左到右、从上到下排着同一张动图先后的 {frames} 个瞬间。"
    "description 要说清整段动作和前后的变化，可以写到 45 字；text 把各个瞬间出现的文字按先后都抄下来。"
)
TEST_QUESTION = "图里画的是什么颜色的什么形状？用一句话回答。"
DRAW_SIDE = 512  # a drawn sticker is stored at most this wide and high
DRAW_STYLE = (
    "风格：Q 版，线条干净，表情夸张，一眼能看懂。纯白背景，方形构图，主体占满画面，没有边框，没有水印。"
    "要写字就只写几个大字，字要写对。"
)


@dataclass(frozen=True)
class StickerConfig:
    """Saved in settings["stickers"]; everything else is a default."""

    vision_provider: str | None = None
    max_count: int = 300
    max_mb: int = 100
    describe_per_hour: int = 30
    notes_per_hour: int = 20  # 图片说明: pictures in the chat the vision model may look at; 0 is none
    hourly_per_bot: int = 6  # stickers one bot may send in one group per hour
    group_gap: float = 60.0  # seconds between two stickers from any of our bots in one group
    repeat_hours: float = 6.0  # the same sticker is not sent to a group again within this
    candidates: int = 12
    file_max_bytes: int = 2 * 1024 * 1024  # collected from a group
    upload_max_bytes: int = 10 * 1024 * 1024  # added by hand: chosen, so a big one is worth its room
    unsuitable_days: float = 7.0

    @property
    def max_bytes(self) -> int:
        return self.max_mb * 1024 * 1024

    def view(self) -> dict[str, Any]:
        return {
            "vision_provider": self.vision_provider,
            "max_count": self.max_count,
            "max_mb": self.max_mb,
            "describe_per_hour": self.describe_per_hour,
            "notes_per_hour": self.notes_per_hour,
            "hourly_per_bot": self.hourly_per_bot,
            "group_gap": self.group_gap,
        }


EDITABLE = (
    "vision_provider",
    "max_count",
    "max_mb",
    "describe_per_hour",
    "notes_per_hour",
    "hourly_per_bot",
    "group_gap",
)


@dataclass(frozen=True)
class Sighting:
    source_key: str
    url: str
    kind: str  # "image" | "mface"
    group_key: str
    at: float
    account: str = ""  # who saw it: its adapter downloads it


@dataclass(frozen=True)
class Picture:
    content_type: str
    width: int
    height: int
    animated: bool


class StickerError(Exception):
    """Why a sticker was not collected, in Chinese (logged at debug level: it happens to odd messages)."""


def sticker_ref(segment: Segment | Mapping[str, Any]) -> tuple[str, str, str] | None:
    """(source key, url, kind) for a sticker segment; None for anything else, ordinary photos included."""
    kind, data = (
        (segment.type, segment.data)
        if isinstance(segment, Segment)
        else (segment.get("type"), segment.get("data") or {})
    )
    url = str(data.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return None
    if kind == "image":
        if str(data.get("sub_type", "")) != "1":
            return None  # a photo or screenshot: never collected
        match = MD5_NAME.match(str(data.get("file") or ""))
        return (f"md5:{match.group(1).upper()}", url, "image") if match else None
    if kind == "mface":
        emoji = str(data.get("emoji_id") or "").strip()
        return (f"mface:{emoji}", url, "mface") if emoji else None
    return None


def inspect(data: bytes) -> Picture:
    """Blocking. What the bytes are; StickerError unless they decode as one of the four formats."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            content_type = FORMATS.get(image.format or "")
            if content_type is None:
                raise StickerError(f"不支持的图片格式：{image.format}")
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise StickerError(f"图片太大：{width}×{height}")
            image.load()  # the first frame must really decode
            return Picture(content_type, width, height, bool(getattr(image, "is_animated", False)))
    except StickerError:
        raise
    except Exception as exc:
        raise StickerError(f"不是能用的图片（{type(exc).__name__}）") from None


def _frame_look(frame: Image.Image) -> int:
    rgba = frame.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, "white")
    flat.alpha_composite(rgba)
    pixels = flat.convert("L").resize((LOOK_SIDE + 1, LOOK_SIDE), Image.Resampling.LANCZOS).tobytes()
    value = 0
    for y in range(LOOK_SIDE):
        row = y * (LOOK_SIDE + 1)
        for x in range(LOOK_SIDE):
            value = value << 1 | (pixels[row + x] > pixels[row + x + 1])
    return value if value.bit_count() >= LOOK_MIN_BITS else 0


def look_of(data: bytes) -> str:
    """Blocking. What the picture looks like, for same_look: the first, middle and last frame of an animated
    one. Empty when it cannot tell (a plain picture, or one that does not decode)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            frames = getattr(image, "n_frames", 1)
            parts = []
            for index in sorted({0, frames // 2, frames - 1}):
                image.seek(index)
                parts.append(_frame_look(image))
    except Exception:
        return ""
    return ":".join(f"{part:064x}" for part in parts) if any(parts) else ""


def same_look(one: str, other: str) -> bool:
    """Whether two looks are the same picture, saved again or resized. Animated never matches still."""
    left, right = one.split(":"), other.split(":")
    return (
        bool(one and other)
        and len(left) == len(right)
        and all(
            (int(a, 16) ^ int(b, 16)).bit_count() <= LOOK_DISTANCE for a, b in zip(left, right, strict=True)
        )
    )


def preview_png(data: bytes, side: int = PREVIEW_SIDE) -> bytes:
    """Blocking. The first frame as a PNG, at most `side` pixels wide and high."""
    with Image.open(io.BytesIO(data)) as image:
        image.seek(0)
        frame = image.convert("RGBA")
    frame.thumbnail((side, side))
    out = io.BytesIO()
    frame.save(out, format="PNG")
    return out.getvalue()


def frame_sheet(data: bytes) -> tuple[bytes, int]:
    """Blocking. What the vision model is shown, and how many moments of the picture that is: a still picture
    (or an animated one that hardly changes) as preview_png, else a sheet of frames in order, each moment
    once: a frame that looks like the one before it adds nothing."""
    moments: list[Image.Image] = []
    with Image.open(io.BytesIO(data)) as image:
        total = getattr(image, "n_frames", 1)
        count = min(SHEET_FRAMES, total)
        before = None
        for index in sorted({round(n * (total - 1) / max(count - 1, 1)) for n in range(count)}):
            image.seek(index)
            look = _frame_look(image)
            if before is None or (look ^ before).bit_count() > LOOK_DISTANCE:
                moments.append(image.convert("RGBA"))
            before = look
    if len(moments) < 2:
        return preview_png(data), 1
    columns = 2 if len(moments) in (2, 4) else 3
    rows = -(-len(moments) // columns)
    step = SHEET_CELL + SHEET_GAP
    sheet = Image.new("RGBA", (columns * step - SHEET_GAP, rows * step - SHEET_GAP), "gray")
    for number, frame in enumerate(moments):
        frame.thumbnail((SHEET_CELL, SHEET_CELL))
        cell = Image.new("RGBA", (SHEET_CELL, SHEET_CELL), "white")
        cell.alpha_composite(frame, ((SHEET_CELL - frame.width) // 2, (SHEET_CELL - frame.height) // 2))
        sheet.paste(cell, (number % columns * step, number // columns * step))
    out = io.BytesIO()
    sheet.convert("RGB").save(out, format="PNG")
    return out.getvalue(), len(moments)


def shrink_png(data: bytes, side: int = DRAW_SIDE) -> bytes:
    """Blocking. A drawn picture as the PNG that is stored: at most `side` wide and high."""
    with Image.open(io.BytesIO(data)) as image:
        small = image.convert("RGBA")
    small.thumbnail((side, side))
    out = io.BytesIO()
    small.save(out, format="PNG", optimize=True)
    return out.getvalue()


def draw_prompt(idea: str, bot_name: str = "") -> str:
    character = f"主角是{bot_name}，长相、发型和衣服照参考图画，是同一个角色。\n" if bot_name else ""
    return f"画一张聊天用的表情包：{idea}\n{character}{DRAW_STYLE}"


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def test_picture() -> bytes:
    """Blocking. The 「测试看图」 picture: a red circle on white."""
    image = Image.new("RGB", (160, 160), "white")
    ImageDraw.Draw(image).ellipse((30, 30, 130, 130), fill=(220, 30, 30))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def parse_description(text: str) -> dict[str, Any]:
    """The describer's JSON, checked and trimmed; ValueError when it is not there."""
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有 JSON")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("不是 JSON 对象")
    description = " ".join(str(data.get("description") or "").split())[:60]
    caption = " ".join(str(data.get("text") or "").split())[:60]
    tags_value = data.get("tags")
    tags = []
    for tag in tags_value if isinstance(tags_value, list) else []:
        tag = str(tag).strip()[:8]
        if tag and tag not in tags:
            tags.append(tag)
    suitable = data.get("suitable")
    if not description and not caption:
        raise ValueError("没有描述")
    return {
        "description": description,
        "caption": caption,
        "tags": tags[:3],
        "suitable": suitable is not False and str(suitable).lower() != "false",
        "reason": " ".join(str(data.get("reason") or "").split())[:60],
    }


def _bigrams(text: str) -> set[str]:
    text = "".join(text.split())
    return {text[i : i + 2] for i in range(len(text) - 1)}


def relevance(sticker: Mapping[str, Any], text: str) -> int:
    """How much a sticker has to do with what was said: its tags, words and description against the text."""
    score = sum(3 for tag in sticker["tags"] if tag and tag in text)
    words = _bigrams(text)
    score += min(3, len(_bigrams(sticker["caption"]) & words))
    score += min(2, len(_bigrams(sticker["description"]) & words))
    return score


def sticker_keys(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """Source keys of the stickers in stored message rows."""
    keys = set()
    for row in rows:
        for segment in row.get("segments") or []:
            ref = sticker_ref(segment)
            if ref:
                keys.add(ref[0])
    return keys


def relabel(rows: list[dict[str, Any]], labels: Mapping[str, str]) -> list[dict[str, Any]]:
    """Copies of `rows` where a described sticker reads 「[表情：…]」 instead of 「[动画表情]」. The bot's own
    read differently: it takes what it wrote itself as the way to write, and would type them out."""
    if not labels:
        return rows
    out = []
    for row in rows:
        segments = row.get("segments") or []
        found = False
        rebuilt = []
        for segment in segments:
            ref = sticker_ref(segment)
            if ref and ref[0] in labels:
                said = (
                    f"（发了表情：{labels[ref[0]]}）" if row.get("from_self") else f"[表情：{labels[ref[0]]}]"
                )
                rebuilt.append(Segment("text", {"text": said}))
                found = True
            else:
                rebuilt.append(Segment(str(segment.get("type")), segment.get("data") or {}))
        out.append(row | {"text": render_text(tuple(rebuilt))} if found else row)
    return out


class StickerKeeper:
    def __init__(
        self,
        db: Database,
        providers: ProviderRegistry,
        broadcaster: Broadcaster,
        folder: Path,
        *,
        our_accounts: set[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        describe_idle: float = 300.0,
    ) -> None:
        self.store = StickerStore(db)
        self.groups = GroupFeatureStore(db)
        self.settings = SettingsRepo(db)
        self.providers = providers
        self.broadcaster = broadcaster
        self.folder = folder
        # The brain's set (mutated in place): our bots' own stickers are not collected.
        self.our_accounts = our_accounts if our_accounts is not None else set()
        self.features: dict[str, GroupFeatures] = {}
        self.config = StickerConfig()
        self._transport = transport
        self.adapters: Mapping[str, Adapter] = {}  # set by the app: the hub's, by account id
        self._describe_idle = describe_idle
        self._queue: asyncio.Queue[Sighting] = asyncio.Queue(QUEUE_SIZE)
        self._recent: OrderedDict[tuple[str, str], None] = OrderedDict()  # (group, message id + key)
        self._wake = asyncio.Event()
        self._described: deque[float] = deque()  # when each recent description call started
        self._lock = asyncio.Lock()  # adding or removing files
        self._drawing = asyncio.Lock()  # one picture at a time: each takes a minute of the subscription
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle ----------------------------------------------------------------------------------

    async def start(self) -> None:
        await self.reload()
        for coro, name in (
            (self._collect_loop(), "stickers:collect"),
            (self._describe_loop(), "stickers:describe"),
            (self._fill_looks(), "stickers:looks"),
        ):
            task = asyncio.create_task(coro, name=name)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def reload(self) -> None:
        """Pick up changed switches and settings."""
        self.features = await self.groups.all()
        self.config = await self.load_config()
        self._wake.set()

    async def load_config(self) -> StickerConfig:
        stored = (await self.settings.all()).get(SETTINGS_KEY)
        values = (
            {k: v for k, v in (stored or {}).items() if k in EDITABLE} if isinstance(stored, dict) else {}
        )
        try:
            return replace(StickerConfig(), **values)
        except TypeError:
            return StickerConfig()

    async def save_config(self, values: dict[str, Any]) -> StickerConfig:
        merged = self.config.view() | {k: v for k, v in values.items() if k in EDITABLE}
        await self.settings.set_many({SETTINGS_KEY: merged})
        before = self.config.vision_provider
        await self.reload()
        if self.config.vision_provider and self.config.vision_provider != before:
            await self.store.retry_failed()  # a new model gets another go at what the old one failed
            self._wake.set()
        return self.config

    def collecting(self, group_key: str) -> bool:
        features = self.features.get(group_key)
        platform = group_key.partition(":")[0]
        return bool(features and features.collect_stickers and "sticker_source" in capabilities_of(platform))

    # -- collecting ---------------------------------------------------------------------------------

    def on_message(self, event: MessageEvent) -> None:
        """A stored message: queue its stickers when its group collects them. Never blocks, never raises."""
        channel = event.channel
        if (
            event.backfilled
            or event.from_self
            or channel.kind != "group"
            or event.sender_id in self.our_accounts
        ):
            return
        group_key = group_key_of(channel)
        if not self.collecting(group_key):
            return
        for segment in event.segments:
            ref = sticker_ref(segment)
            if ref is None:
                continue
            marker = (group_key, f"{event.message_id}:{ref[0]}")
            if marker in self._recent:
                continue  # the same message as another of our accounts received it
            self._recent[marker] = None
            while len(self._recent) > 1000:
                self._recent.popitem(last=False)
            try:
                self._queue.put_nowait(
                    Sighting(
                        ref[0],
                        ref[1],
                        ref[2],
                        group_key,
                        event.timestamp,
                        account_id(channel.platform, channel.account),
                    )
                )
            except asyncio.QueueFull:
                log.debug("表情收藏队列满了，跳过一张")

    async def _collect_loop(self) -> None:
        while True:
            sighting = await self._queue.get()
            try:
                await self.collect(sighting)
            except StickerError as exc:
                log.debug("没有收藏这张表情：%s", exc)
            except Exception:
                log.exception("收藏表情出错，已跳过")

    async def collect(self, sighting: Sighting) -> dict[str, Any] | None:
        """Store one sighted sticker (or count it as seen again). The new row, else None."""
        known = await self.store.by_source(sighting.source_key)
        if known is not None:
            await self.store.seen(known["id"], sighting.at)
            return None
        data = await self._download(sighting.url, sighting.account)
        picture = await asyncio.to_thread(inspect, data)
        sha = hashlib.sha256(data).hexdigest()
        look = await asyncio.to_thread(look_of, data)
        async with self._lock:
            known = await self.store.by_sha(sha) or await self._twin(look)
            if known is not None:
                await self.store.seen(known["id"], sighting.at)
                return None
            if not await self._make_room(len(data)):
                log.info("表情包已经满了，而且都是「保留」的，这张没有收藏")
                return None
            sticker_id = await self._save(
                data,
                picture,
                sha,
                source_key=sighting.source_key,
                kind=sighting.kind,
                group_key=sighting.group_key,
                seen_at=sighting.at,
                look=look,
            )
        self._added(sticker_id)
        return await self.store.get(sticker_id)

    async def _save(self, data: bytes, picture: Picture, sha: str, **fields: Any) -> int:
        """The file and its row, with self._lock held and room made."""
        await asyncio.to_thread(_write, self.path(sha, picture.content_type), data)
        return await self.store.add(
            NewSticker(
                sha256=sha,
                content_type=picture.content_type,
                bytes=len(data),
                width=picture.width,
                height=picture.height,
                animated=picture.animated,
                **fields,
            )
        )

    async def _twin(self, look: str) -> dict[str, Any] | None:
        """The sticker that shows the same picture, if there is one."""
        if look:
            for sticker_id, other in (await self.store.looks()).items():
                if same_look(look, other):
                    return await self.store.get(sticker_id)
        return None

    async def _fill_looks(self) -> None:
        """Stickers from before looks were kept. Plain ones have none and are looked at again next start."""
        for sticker in await self.store.without_look():
            try:
                look = await asyncio.to_thread(look_of, await self.read(sticker))
            except OSError:
                continue
            if look:
                await self.store.set_look(sticker["id"], look)

    def _added(self, sticker_id: int) -> None:
        self.broadcaster.publish("stickers", {"id": sticker_id, "change": "added"})
        self._wake.set()

    async def add_own(self, data: bytes, kind: str, bot_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """A picture from the console (kind "upload") or drawn for it ("drawn"): (its row, whether it is new).
        Kept, so it is never removed to make room; it is described and checked like a collected one. A
        picture already in the collection, also saved again or resized, is marked kept instead. StickerError
        says why it was refused."""
        if len(data) > self.config.upload_max_bytes:
            raise StickerError(f"图片太大了，最多 {self.config.upload_max_bytes // (1024 * 1024)} MB")
        picture = await asyncio.to_thread(inspect, data)
        sha = hashlib.sha256(data).hexdigest()
        look = await asyncio.to_thread(look_of, data)
        async with self._lock:
            known = await self.store.by_sha(sha) or await self._twin(look)
            if known is not None:
                await self.store.update(known["id"], kept=True)
                self.broadcaster.publish("stickers", {"id": known["id"], "change": "edited"})
                return {**known, "kept": True}, False
            if not await self._make_room(len(data)):
                raise StickerError("表情包满了，而且都是「保留」的：先删几张，或者调高上限")
            sticker_id = await self._save(
                data,
                picture,
                sha,
                source_key=f"{kind}:{sha}",
                kind=kind,
                group_key="",
                seen_at=time.time(),
                kept=True,
                bot_id=bot_id,
                look=look,
            )
        self._added(sticker_id)
        row = await self.store.get(sticker_id)
        assert row is not None
        return row, True

    async def draw(
        self, idea: str, *, bot_name: str = "", bot_id: str | None = None, avatar: bytes | None = None
    ) -> dict[str, Any]:
        """Have a model draw a sticker and add it. With a bot, it is drawn as that character (`avatar`: the
        reference picture) and only that bot sends it. StickerError, ProviderError or ProviderUnavailable
        say why there is no picture."""
        runtime = self.providers.drawing()
        if runtime is None:
            raise StickerError("没有能画图的模型：先在「模型」页加一个 Codex（ChatGPT 订阅）模型")
        if self._drawing.locked():
            raise StickerError("正在画另一张，画完再来")
        async with self._drawing:
            images = (data_url(await asyncio.to_thread(preview_png, avatar)),) if avatar else ()
            drawn = await runtime.draw(draw_prompt(idea, bot_name), images)
            try:
                png = await asyncio.to_thread(shrink_png, drawn)
            except (OSError, ValueError) as exc:
                raise StickerError(f"模型画的图打不开（{type(exc).__name__}）") from None
            row, _ = await self.add_own(png, "drawn", bot_id)
            return row

    async def _download(self, url: str, account: str = "") -> bytes:
        """Through the account that saw the picture (its proxy); the network itself when it has gone. A test's
        transport comes first."""
        limit = self.config.file_max_bytes
        adapter = None if self._transport else self.adapters.get(account)
        try:
            if adapter is not None:
                return await adapter.fetch_media(url, limit)
            transport = self._transport or watch_fetch.network_transport(None)
            async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
                return await download(http, url, limit)
        except MediaError as exc:
            raise StickerError(str(exc)) from None

    def path(self, sha: str, content_type: str) -> Path:
        return self.folder / sha[:2] / f"{sha}{EXTENSIONS.get(content_type, '.img')}"

    async def read(self, sticker: Mapping[str, Any]) -> bytes:
        return await asyncio.to_thread(self.path(sticker["sha256"], sticker["content_type"]).read_bytes)

    # -- room ---------------------------------------------------------------------------------------

    async def _make_room(self, incoming: int) -> bool:
        """Remove stickers until one more of `incoming` bytes fits. False if only kept ones are left."""
        count, total = await self.store.totals()
        while count + 1 > self.config.max_count or total + incoming > self.config.max_bytes:
            victims = await self.store.evictable(1)
            if not victims:
                return False
            await self._remove_files(await self.store.delete([victims[0]["id"]]))
            count, total = count - 1, total - victims[0]["bytes"]
        return True

    async def remove(self, ids: list[int]) -> list[dict[str, Any]]:
        async with self._lock:
            removed = await self.store.delete(ids)
            await self._remove_files(removed)
        if removed:
            self.broadcaster.publish("stickers", {"change": "removed", "ids": [s["id"] for s in removed]})
        return removed

    async def _remove_files(self, stickers: list[dict[str, Any]]) -> None:
        for sticker in stickers:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(self.path(sticker["sha256"], sticker["content_type"]).unlink)

    async def tidy(self, now: float | None = None) -> tuple[int, int]:
        """The daily cleanup: expired unsuitable ones, anything over the limits, files without a row.
        Returns (stickers removed, bytes freed)."""
        now = time.time() if now is None else now
        removed = freed = 0
        async with self._lock:
            expired = await self.store.expired_unsuitable(now - self.config.unsuitable_days * DAY)
            gone = await self.store.delete([s["id"] for s in expired])
            await self._remove_files(gone)
            removed += len(gone)
            freed += sum(s["bytes"] for s in gone)
            count, total = await self.store.totals()
            while count > self.config.max_count or total > self.config.max_bytes:
                victims = await self.store.evictable(1)
                if not victims:
                    break
                gone = await self.store.delete([victims[0]["id"]])
                await self._remove_files(gone)
                count, total = count - 1, total - victims[0]["bytes"]
                removed += 1
                freed += victims[0]["bytes"]
            known = await self.store.all_shas()
            files, size = await asyncio.to_thread(_remove_orphans, self.folder, known, now - ORPHAN_AGE)
            freed += size
        if removed or files:
            log.info("清理了 %d 张表情包和 %d 个多余的文件（%.1f MB）", removed, files, freed / 2**20)
            self.broadcaster.publish("stickers", {"change": "removed"})
        return removed, freed

    # -- describing ---------------------------------------------------------------------------------

    def vision(self) -> ProviderRuntime | None:
        return self.providers.get(self.config.vision_provider)

    async def _describe_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._describe_idle)
            self._wake.clear()
            try:
                await self.describe_pending()
            except Exception:
                log.exception("描述表情包出错")

    async def describe_pending(self) -> int:
        """Describe queued stickers while the hourly budget and the vision model allow. Returns how many."""
        done, skip = 0, set[int]()
        while True:
            runtime = self.vision()
            if runtime is None:
                return done
            now = time.time()
            while self._described and self._described[0] < now - HOUR:
                self._described.popleft()
            if len(self._described) >= self.config.describe_per_hour:
                return done  # the loop comes back within describe_idle
            sticker = await self.store.next_pending(skip)
            if sticker is None:
                return done
            self._described.append(now)
            if not await self.describe(sticker, runtime):
                return done
            skip.add(sticker["id"])
            done += 1

    async def describe(self, sticker: Mapping[str, Any], runtime: ProviderRuntime) -> bool:
        """Describe one sticker. False when the model could not be called (the sticker stays queued)."""
        try:
            png, moments = await asyncio.to_thread(frame_sheet, await self.read(sticker))
        except (OSError, ValueError) as exc:
            await self.store.describe(
                sticker["id"], status="failed", note=f"读不了图片文件：{type(exc).__name__}"
            )
            return True
        prompt = DESCRIBE_PROMPT + (ANIMATED_NOTE.format(frames=moments) if moments > 1 else "")
        messages = [ChatMessage("user", prompt, images=(data_url(png),))]
        try:
            completion, _ = await runtime.complete(messages, bot_id=None, max_tokens=300)
        except ProviderUnavailable:
            return False
        except ProviderError as exc:
            if exc.retryable:
                return False
            await self.store.describe(sticker["id"], status="failed", note=f"看图模型出错：{str(exc)[:120]}")
            self._changed(sticker["id"])
            return True
        try:
            result = parse_description(completion.text)
        except ValueError:
            await self.store.describe(sticker["id"], status="failed", note="看图模型的回答看不懂")
            self._changed(sticker["id"])
            return True
        await self.store.describe(
            sticker["id"],
            status="ready" if result["suitable"] else "unsuitable",
            description=result["description"],
            caption=result["caption"],
            tags=result["tags"],
            note="" if result["suitable"] else (result["reason"] or "看图模型认为不适合发"),
        )
        self._changed(sticker["id"])
        return True

    def _changed(self, sticker_id: int) -> None:
        self.broadcaster.publish("stickers", {"id": sticker_id, "change": "described"})

    async def test_vision(self, runtime: ProviderRuntime) -> tuple[bool, str]:
        """「测试看图」: (whether the answer names the red circle, the answer)."""
        picture = data_url(await asyncio.to_thread(test_picture))
        completion, _ = await runtime.complete(
            [ChatMessage("user", TEST_QUESTION, images=(picture,))], bot_id=None, max_tokens=80
        )
        answer = " ".join(completion.text.split())[:120]
        return ("红" in answer and "圆" in answer), answer

    # -- sending ------------------------------------------------------------------------------------

    async def labels(self, rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
        return await self.store.labels(sorted(sticker_keys(rows)))

    async def candidates(
        self, channel: ChannelRef, bot_id: str, text: str, exclude: set[str]
    ) -> tuple[list[dict[str, Any]], str]:
        """(stickers the bot may pick from now, why there are none). Throttled: an empty list and a reason."""
        config, now = self.config, time.time()
        if await self.store.sent_in_group(channel.kind, channel.id, now - config.group_gap):
            return [], f"{config.group_gap:.0f} 秒内群里刚发过表情"
        sent = await self.store.sent_in_group(channel.kind, channel.id, now - HOUR, bot_id)
        if sent >= config.hourly_per_bot:
            return [], f"这个小时已经发了 {sent} 张表情，到上限了"
        recent = await self.store.recent_in_group(channel.kind, channel.id, now - config.repeat_hours * HOUR)
        pool = [
            s
            for s in await self.store.ready()
            if s["id"] not in recent and s["source_key"] not in exclude and s["bot_id"] in (None, bot_id)
        ]
        if not pool:
            return [], "没有能发的表情"
        scored = sorted(pool, key=lambda s: -relevance(s, text))  # stable: ties stay newest-seen first
        picked = [s for s in scored if relevance(s, text) > 0][: config.candidates]
        picked += [s for s in pool if s not in picked][: config.candidates - len(picked)]
        return picked, ""

    async def blocked(self, channel: ChannelRef) -> str:
        """Checked again right before sending: another bot may have sent one while this one was thinking."""
        if await self.store.sent_in_group(channel.kind, channel.id, time.time() - self.config.group_gap):
            return f"{self.config.group_gap:.0f} 秒内群里刚发过表情"
        return ""

    async def segment(self, sticker: Mapping[str, Any]) -> Segment:
        """The picture as a OneBot image segment: base64 (SnowLuma in Docker cannot read nest's files), shown
        as a sticker rather than a photo."""
        encoded = base64.b64encode(await self.read(sticker)).decode("ascii")
        return Segment("image", {"file": f"base64://{encoded}", "sub_type": 1, "summary": "[动画表情]"})

    def describe_choice(self, sticker: Mapping[str, Any]) -> str:
        tags = "、".join(sticker["tags"])
        return label(sticker["description"], sticker["caption"]) + (f"（{tags}）" if tags else "")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _remove_orphans(folder: Path, known: set[str], before: float) -> tuple[int, int]:
    """Blocking: delete files under `folder` that no row points to and that are older than `before`."""
    files = size = 0
    if not folder.is_dir():
        return 0, 0
    for path in folder.glob("*/*"):
        with contextlib.suppress(OSError):
            info = path.lstat()
            if not path.is_file() or path.is_symlink() or info.st_mtime >= before:
                continue
            if path.name.split(".", 1)[0] in known:
                continue
            path.unlink()
            files += 1
            size += info.st_size
    return files, size
