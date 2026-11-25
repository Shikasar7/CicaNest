"""表情包套装: each companion's own stickers, drawn by nest in her likeness.

A pack is a list of moods (开心, 卖萌, 害羞…), one picture each. nest draws them one at a time with the model
that can draw (nest.core.stickers.StickerKeeper.draw, her picture as the reference), or the user uploads
one for a mood. A companion may have several packs; the one in use is what she picks from in the desk chat
(nest.core.brain offers its stickers there and nowhere else: what she sends in QQ groups is unchanged).

The pictures are ordinary rows of the sticker collection (owned by her, kind "drawn" or "upload", kept,
described by their mood so no vision call is needed). A picture replaced or a pack deleted takes its
pictures out of the collection too.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Callable, Mapping
from typing import Any

from nest import __version__
from nest.core.broadcast import Broadcaster
from nest.core.stickers import StickerError, StickerKeeper
from nest.llm.base import ProviderError
from nest.llm.runtime import ProviderUnavailable
from nest.storage.avatars import AvatarStore
from nest.storage.bots import BotConfig
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

# The moods a new pack starts with, and what each looks like (said to the model that draws it).
MOODS: tuple[tuple[str, str], ...] = (
    ("开心", "开心地笑，眼睛弯成月牙"),
    ("卖萌", "歪头眨一只眼，比个剪刀手"),
    ("害羞", "双手捧着红红的脸，低头笑"),
    ("惊讶", "嘴巴张成圆形，眼睛瞪得圆圆的"),
    ("委屈", "眼泪汪汪，嘟着嘴"),
    ("得意", "叉腰，得意地扬起下巴"),
    ("比心", "双手在胸前比一个爱心"),
    ("无语", "半眯着眼，额头挂一滴汗，一脸无语"),
    ("嫌弃", "皱眉撇嘴，身子往后躲"),
    ("心虚", "眼神飘到一边，冒冷汗干笑"),
    ("生气", "鼓起脸颊，头上冒出生气的符号"),
    ("哭哭", "放声大哭，眼泪像两条小河"),
    ("晕倒", "眼睛变成蚊香圈，头晕得快倒了"),
    ("困了", "打着哈欠，睡眼惺忪"),
    ("偷看", "从画面边上探出半张脸偷看"),
)
HINTS = dict(MOODS)
# How the pictures are drawn: (name shown, what the model is told).
STYLES: dict[str, tuple[str, str]] = {
    "half": (
        "半身 Q 版",
        "风格：半身 Q 版，只画到胸口，头大身小，大眼睛，线条干净，表情夸张一眼能看懂。",
    ),
    "full": ("全身 Q 版", "风格：全身 Q 版，二头身，动作夸张，线条干净，表情一眼能看懂。"),
    "head": ("大头贴", "风格：只画一个大头，表情占满画面，线条干净，表情夸张一眼能看懂。"),
}
FRAME = "纯白背景，方形构图，人物居中占满画面，没有边框，没有水印，不写字。"
DEFAULT_STYLE = "half"
DESK_SETTING = "desk_stickers"  # {bot id: False}: she sends no stickers in the desk chat (default: she does)
# {bot id: False}: in the desk chat she may also pick from the whole collection, her own first (default: only
# her own, 「只用专属表情包」).
OWN_ONLY_SETTING = "desk_stickers_own_only"
NAME_MAX = 20
MOOD_MAX = 12
EXTRA_MAX = 200
SLOTS_MAX = 40
PACKS_MAX = 10
MINUTES_PER_PICTURE = 1.5  # what 「全部生成」 says it will take


class PackError(Exception):
    """Why that cannot be done, in one Chinese sentence."""


def style_prompt(style: str) -> str:
    return STYLES.get(style, STYLES[DEFAULT_STYLE])[1] + FRAME


def idea_of(mood: str, extra: str) -> str:
    hint = HINTS.get(mood, "")
    idea = f"「{mood}」的表情：{hint}。" if hint else f"「{mood}」的表情。"
    return idea + (f"\n另外：{extra}" if extra else "")


class PackKeeper:
    def __init__(
        self,
        db: Database,
        stickers: StickerKeeper,
        avatars: AvatarStore,
        broadcaster: Broadcaster,
        bots: Callable[[], Mapping[str, BotConfig]],
    ) -> None:
        self.db = db
        self.stickers = stickers
        self.avatars = avatars
        self.broadcaster = broadcaster
        self.bots = bots
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()  # one edit of the packs at a time

    async def start(self) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """UPDATE sticker_slots SET state = 'failed', error = 'nest 重启了，这张没画完'
                   WHERE state IN ('queued', 'drawing')"""
            )
        self._worker = asyncio.create_task(self._work(), name="sticker-packs")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    def _changed(self, bot_id: str, pack_id: int | None = None) -> None:
        self.broadcaster.publish("stickers", {"change": "pack", "bot_id": bot_id, "pack_id": pack_id})

    # -- reading ------------------------------------------------------------------------------------

    async def packs(self, bot_id: str) -> list[dict[str, Any]]:
        """Her packs, oldest first, each with its slots in order."""
        packs = [
            dict(row)
            for row in await self.db.fetchall(
                "SELECT * FROM sticker_packs WHERE bot_id = ? ORDER BY id", (bot_id,)
            )
        ]
        slots: dict[int, list[dict[str, Any]]] = {p["id"]: [] for p in packs}
        if packs:
            marks = ",".join("?" * len(packs))
            for row in await self.db.fetchall(
                f"SELECT * FROM sticker_slots WHERE pack_id IN ({marks}) ORDER BY position, id",
                [p["id"] for p in packs],
            ):
                slots[row["pack_id"]].append(dict(row))
        for pack in packs:
            pack["active"] = bool(pack["active"])
            pack["slots"] = slots[pack["id"]]
        return packs

    async def pack(self, pack_id: int) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT bot_id FROM sticker_packs WHERE id = ?", (pack_id,))
        if row is None:
            raise PackError("没有这套表情包")
        return next(p for p in await self.packs(row["bot_id"]) if p["id"] == pack_id)

    async def summary(self) -> dict[str, dict[str, Any]]:
        """bot id -> {"done", "total"} of the pack in use (for the companion list)."""
        rows = await self.db.fetchall(
            """SELECT p.bot_id, p.id AS pack_id, count(s.id) AS total,
                      sum(CASE WHEN s.state = 'done' AND s.sticker_id IS NOT NULL THEN 1 ELSE 0 END) AS done
               FROM sticker_packs p LEFT JOIN sticker_slots s ON s.pack_id = p.id
               WHERE p.active = 1 GROUP BY p.id"""
        )
        return {
            r["bot_id"]: {"pack_id": r["pack_id"], "done": r["done"] or 0, "total": r["total"]} for r in rows
        }

    async def in_use(self, bot_id: str) -> list[int]:
        """The stickers of her pack in use, in order: what she may send in the desk chat."""
        rows = await self.db.fetchall(
            """SELECT s.sticker_id FROM sticker_slots s JOIN sticker_packs p ON p.id = s.pack_id
               WHERE p.bot_id = ? AND p.active = 1 AND s.state = 'done' AND s.sticker_id IS NOT NULL
               ORDER BY s.position, s.id""",
            (bot_id,),
        )
        return [r["sticker_id"] for r in rows]

    async def own(self, bot_id: str) -> list[int]:
        """Her 专属表情包: the pack in use, then the other pictures that are hers alone (drawn for her with
        「画一张」, or given to her on the 表情包 page); not the pictures of her packs not in use."""
        in_use = await self.in_use(bot_id)
        packed = await self.all_pictures()
        rows = await self.db.fetchall(
            """SELECT id FROM stickers WHERE bot_id = ? AND status = 'ready'
               ORDER BY last_seen_at DESC, id DESC""",
            (bot_id,),
        )
        return in_use + [r["id"] for r in rows if r["id"] not in packed]

    async def own_only(self, bot_id: str) -> bool:
        values = (await SettingsRepo(self.db).all()).get(OWN_ONLY_SETTING) or {}
        return values.get(bot_id, True) is not False

    async def set_own_only(self, bot_id: str, on: bool) -> None:
        repo = SettingsRepo(self.db)
        values = dict((await repo.all()).get(OWN_ONLY_SETTING) or {})
        if on:
            values.pop(bot_id, None)
        else:
            values[bot_id] = False
        await repo.set_many({OWN_ONLY_SETTING: values})
        self._changed(bot_id)

    async def all_pictures(self) -> set[int]:
        """Every sticker that belongs to a pack (kept out of the group chats)."""
        rows = await self.db.fetchall("SELECT sticker_id FROM sticker_slots WHERE sticker_id IS NOT NULL")
        return {r["sticker_id"] for r in rows}

    async def desk_on(self, bot_id: str) -> bool:
        off = (await SettingsRepo(self.db).all()).get(DESK_SETTING) or {}
        return off.get(bot_id, True) is not False

    async def set_desk(self, bot_id: str, on: bool) -> None:
        repo = SettingsRepo(self.db)
        values = dict((await repo.all()).get(DESK_SETTING) or {})
        if on:
            values.pop(bot_id, None)
        else:
            values[bot_id] = False
        await repo.set_many({DESK_SETTING: values})
        self._changed(bot_id)

    # -- editing ------------------------------------------------------------------------------------

    async def create(self, bot_id: str, name: str) -> dict[str, Any]:
        """A new pack with the usual moods, nothing drawn yet. Her first one is put in use."""
        name = name.strip()[:NAME_MAX] or "表情包"
        async with self._lock:
            count = await self.db.fetchone(
                "SELECT count(*) AS n FROM sticker_packs WHERE bot_id = ?", (bot_id,)
            )
            if count is not None and count["n"] >= PACKS_MAX:
                raise PackError(f"一个伙伴最多 {PACKS_MAX} 套表情包")
            async with self.db.transaction() as conn:
                cursor = await conn.execute(
                    """INSERT INTO sticker_packs (bot_id, name, style, active, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (bot_id, name, DEFAULT_STYLE, int(not (count and count["n"])), time.time()),
                )
                pack_id = int(cursor.lastrowid or 0)
                await conn.executemany(
                    "INSERT INTO sticker_slots (pack_id, position, mood) VALUES (?, ?, ?)",
                    [(pack_id, n, mood) for n, (mood, _) in enumerate(MOODS)],
                )
        self._changed(bot_id, pack_id)
        return await self.pack(pack_id)

    async def update(
        self,
        pack_id: int,
        *,
        name: str | None = None,
        style: str | None = None,
        extra: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        pack = await self.pack(pack_id)
        columns: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise PackError("起个名字吧")
            columns["name"] = name.strip()[:NAME_MAX]
        if style is not None:
            if style not in STYLES:
                raise PackError("没有这种画风")
            columns["style"] = style
        if extra is not None:
            columns["extra"] = extra.strip()[:EXTRA_MAX]
        async with self._lock, self.db.transaction() as conn:
            if columns:
                sets = ", ".join(f"{c} = ?" for c in columns)
                await conn.execute(
                    f"UPDATE sticker_packs SET {sets} WHERE id = ?", (*columns.values(), pack_id)
                )
            if active:
                await conn.execute(
                    "UPDATE sticker_packs SET active = (id = ?) WHERE bot_id = ?", (pack_id, pack["bot_id"])
                )
            elif active is False:
                await conn.execute("UPDATE sticker_packs SET active = 0 WHERE id = ?", (pack_id,))
        self._changed(pack["bot_id"], pack_id)
        return await self.pack(pack_id)

    async def delete(self, pack_id: int) -> None:
        """The pack and its pictures. When it was in use, her oldest other pack is put in use."""
        pack = await self.pack(pack_id)
        pictures = [s["sticker_id"] for s in pack["slots"] if s["sticker_id"] is not None]
        async with self._lock:
            async with self.db.transaction() as conn:
                await conn.execute("DELETE FROM sticker_packs WHERE id = ?", (pack_id,))
                if pack["active"]:
                    await conn.execute(
                        """UPDATE sticker_packs SET active = 1
                           WHERE id = (SELECT min(id) FROM sticker_packs WHERE bot_id = ?)""",
                        (pack["bot_id"],),
                    )
            await self._forget(pictures)
        self._changed(pack["bot_id"], pack_id)

    async def add_slot(self, pack_id: int, mood: str) -> dict[str, Any]:
        pack = await self.pack(pack_id)
        mood = _mood(mood)
        if len(pack["slots"]) >= SLOTS_MAX:
            raise PackError(f"一套最多 {SLOTS_MAX} 张")
        if any(s["mood"] == mood for s in pack["slots"]):
            raise PackError(f"已经有「{mood}」了")
        last = max((s["position"] for s in pack["slots"]), default=-1)
        async with self._lock, self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO sticker_slots (pack_id, position, mood) VALUES (?, ?, ?)",
                (pack_id, last + 1, mood),
            )
        self._changed(pack["bot_id"], pack_id)
        return await self.pack(pack_id)

    async def _slot(self, slot_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        row = await self.db.fetchone(
            """SELECT s.*, p.bot_id, p.style, p.extra FROM sticker_slots s
               JOIN sticker_packs p ON p.id = s.pack_id WHERE s.id = ?""",
            (slot_id,),
        )
        if row is None:
            raise PackError("没有这一格")
        slot = dict(row)
        return slot, {
            "id": slot["pack_id"],
            "bot_id": slot["bot_id"],
            "style": slot["style"],
            "extra": slot["extra"],
        }

    async def rename_slot(self, slot_id: int, mood: str) -> dict[str, Any]:
        slot, pack = await self._slot(slot_id)
        mood = _mood(mood)
        async with self._lock, self.db.transaction() as conn:
            await conn.execute("UPDATE sticker_slots SET mood = ? WHERE id = ?", (mood, slot_id))
        if slot["sticker_id"] is not None:
            await self._label(slot["sticker_id"], pack["bot_id"], mood)
        self._changed(pack["bot_id"], pack["id"])
        return await self.pack(pack["id"])

    async def delete_slot(self, slot_id: int) -> dict[str, Any]:
        slot, pack = await self._slot(slot_id)
        if slot["state"] in ("queued", "drawing"):
            raise PackError("这一格正在画，画完再删")
        async with self._lock:
            async with self.db.transaction() as conn:
                await conn.execute("DELETE FROM sticker_slots WHERE id = ?", (slot_id,))
            await self._forget([slot["sticker_id"]] if slot["sticker_id"] is not None else [])
        self._changed(pack["bot_id"], pack["id"])
        return await self.pack(pack["id"])

    async def upload(self, slot_id: int, data: bytes) -> dict[str, Any]:
        """The user's own picture for this mood. StickerError says why it was refused."""
        slot, pack = await self._slot(slot_id)
        if slot["state"] in ("queued", "drawing"):
            raise PackError("这一格正在画，画完再换")
        row, _ = await self.stickers.add_own(data, "upload", pack["bot_id"])
        await self._place(slot, pack, row["id"])
        return await self.pack(pack["id"])

    # -- drawing ------------------------------------------------------------------------------------

    async def draw(self, pack_id: int, which: str | list[int]) -> dict[str, Any]:
        """Queue pictures to draw: "missing" (the ones not drawn yet or failed), "all" (every one again), or
        these slot ids. {"queued": n, "minutes": about how long}."""
        pack = await self.pack(pack_id)
        busy = {"queued", "drawing"}
        if which == "missing":
            chosen = [s for s in pack["slots"] if s["state"] not in busy and s["sticker_id"] is None]
        elif which == "all":
            chosen = [s for s in pack["slots"] if s["state"] not in busy and not s["locked"]]
        else:
            wanted = set(which)
            chosen = [s for s in pack["slots"] if s["id"] in wanted and s["state"] not in busy]
        if not chosen:
            raise PackError("没有要画的了")
        if self.stickers.providers.drawing() is None:
            raise PackError("没有能画图的模型：先在「模型」页加一个 Codex（ChatGPT 订阅）模型")
        if await self.avatar_of(pack["bot_id"]) is None:
            raise PackError("她还没有头像：先在 Bot 页给她设一张，nest 照着头像画")
        ids = [s["id"] for s in chosen]
        marks = ",".join("?" * len(ids))
        async with self._lock, self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE sticker_slots SET state = 'queued', error = '' WHERE id IN ({marks})", ids
            )
        for slot_id in ids:
            self._queue.put_nowait(slot_id)
        self._changed(pack["bot_id"], pack_id)
        return {"queued": len(ids), "minutes": max(1, round(self._queue.qsize() * MINUTES_PER_PICTURE))}

    async def lock_slot(self, slot_id: int, locked: bool) -> dict[str, Any]:
        """「锁住」: 「全部重画」 leaves it."""
        _, pack = await self._slot(slot_id)
        async with self._lock, self.db.transaction() as conn:
            await conn.execute("UPDATE sticker_slots SET locked = ? WHERE id = ?", (int(locked), slot_id))
        self._changed(pack["bot_id"], pack["id"])
        return await self.pack(pack["id"])

    async def missing(self) -> list[tuple[int, str, int]]:
        """(pack, bot, how many) of every companion's pack in use with pictures not drawn yet, for those who
        can be drawn (they have a picture to go by)."""
        rows = await self.db.fetchall(
            """SELECT p.id, p.bot_id, count(*) AS n FROM sticker_packs p
               JOIN sticker_slots s ON s.pack_id = p.id
               WHERE p.active = 1 AND s.sticker_id IS NULL AND s.state NOT IN ('queued', 'drawing')
               GROUP BY p.id ORDER BY p.id"""
        )
        return [
            (r["id"], r["bot_id"], r["n"])
            for r in rows
            if r["bot_id"] in self.bots() and await self.avatar_of(r["bot_id"]) is not None
        ]

    async def draw_everyone(self) -> dict[str, Any]:
        """「全部生成」: what is missing of every companion's pack in use."""
        queued = 0
        for pack_id, _, _ in await self.missing():
            queued += (await self.draw(pack_id, "missing"))["queued"]
        if not queued:
            raise PackError("没有要画的了")
        return {"queued": queued, "minutes": max(1, round(self._queue.qsize() * MINUTES_PER_PICTURE))}

    async def take_in(self, bot_id: str, name: str, items: list[tuple[str, bytes]]) -> dict[str, Any]:
        """A pack of pictures from elsewhere (a plugin's), as a new pack of hers: (mood, picture) in order. A
        picture that cannot be used is left out."""
        pack = await self.create(bot_id, name)
        async with self._lock, self.db.transaction() as conn:
            await conn.execute("DELETE FROM sticker_slots WHERE pack_id = ?", (pack["id"],))
            await conn.executemany(
                "INSERT INTO sticker_slots (pack_id, position, mood) VALUES (?, ?, ?)",
                [(pack["id"], n, _mood(mood)) for n, (mood, _) in enumerate(items)],
            )
        made = await self.pack(pack["id"])
        skipped = 0
        for slot, (_, data) in zip(made["slots"], items, strict=True):
            try:
                await self.upload(slot["id"], data)
            except StickerError as exc:
                skipped += 1
                log.info("这张表情没放进来（%s）：%s", slot["mood"], exc)
        return await self.pack(pack["id"]) | {"skipped": skipped}

    async def as_plugin(self, pack_id: int, author: str) -> tuple[str, bytes]:
        """The pack as a plugin zip to share: (file name, zip). Its pictures and their moods, nothing else."""
        pack = await self.pack(pack_id)
        files: list[tuple[str, bytes]] = []
        items = []
        for slot in pack["slots"]:
            row = await self.stickers.store.get(slot["sticker_id"]) if slot["sticker_id"] else None
            if row is None:
                continue
            data = await asyncio.to_thread(self.stickers.path(row["sha256"], row["content_type"]).read_bytes)
            name = f"{len(items) + 1:02d}{self.stickers.path(row['sha256'], row['content_type']).suffix}"
            files.append((name, data))
            items.append({"mood": slot["mood"], "file": name})
        if not items:
            raise PackError("这套里还没有画好的")
        maker = re.sub(r"[^a-z0-9-]", "", author.lower()) or "someone"
        plugin_id = f"{maker}.stickers-{pack_id}"
        meta = {
            "id": plugin_id,
            "version": "1.0.0",
            "name": f"表情包：{pack['name']}",
            "author": author,
            "description": f"一套表情包，{len(items)} 张",
            "nest": __version__,
            "provides": {"stickers": ["stickers/pack"]},
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("plugin.json", json.dumps(meta, ensure_ascii=False, indent=2))
            listed = {"name": pack["name"], "items": items}
            archive.writestr("stickers/pack/pack.json", json.dumps(listed, ensure_ascii=False, indent=2))
            for name, data in files:
                archive.writestr(f"stickers/pack/{name}", data)
        return f"{plugin_id}-1.0.0.zip", out.getvalue()

    async def avatar_of(self, bot_id: str) -> bytes | None:
        chosen = (await self.avatars.views()).get(bot_id, {}).get("chosen")
        picture = await self.avatars.get(bot_id, chosen) if chosen else None
        return picture.data if picture else None

    async def _work(self) -> None:
        while True:
            slot_id = await self._queue.get()
            try:
                await self._draw_one(slot_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # one picture failing never stops the rest
                log.exception("画表情包失败（第 %s 格）", slot_id)

    async def _draw_one(self, slot_id: int) -> None:
        try:
            slot, pack = await self._slot(slot_id)
        except PackError:
            return  # deleted while it waited
        if slot["state"] != "queued":
            return
        bot = self.bots().get(pack["bot_id"])
        await self._set(slot_id, "drawing")
        self._changed(pack["bot_id"], pack["id"])
        error = ""
        try:
            if bot is None:
                raise PackError("这个伙伴不在了")
            avatar = await self.avatar_of(bot.id)
            if avatar is None:
                raise PackError("她没有头像了：先在 Bot 页给她设一张")
            row = await self.stickers.draw(
                idea_of(slot["mood"], pack["extra"]),
                bot_name=bot.name,
                bot_id=bot.id,
                avatar=avatar,
                style=style_prompt(pack["style"]),
                wait=True,  # after a picture being drawn in the 表情包 page
            )
        except (PackError, StickerError) as exc:
            error = str(exc)
        except (ProviderError, ProviderUnavailable) as exc:
            error = f"没画成：{str(exc)[:160]}"
        if error:
            await self._set(slot_id, "failed", error)
            self._changed(pack["bot_id"], pack["id"])
            return
        try:
            slot, pack = await self._slot(slot_id)
        except PackError:  # deleted while it was drawn: the picture goes too
            await self.stickers.remove([row["id"]])
            return
        await self._place(slot, pack, row["id"])

    async def _set(self, slot_id: int, state: str, error: str = "") -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE sticker_slots SET state = ?, error = ? WHERE id = ?", (state, error, slot_id)
            )

    async def _place(self, slot: dict[str, Any], pack: dict[str, Any], sticker_id: int) -> None:
        """Put the picture in the slot, label it with the mood, and drop the one it replaces."""
        await self._label(sticker_id, pack["bot_id"], slot["mood"])
        async with self._lock:
            async with self.db.transaction() as conn:
                await conn.execute(
                    "UPDATE sticker_slots SET sticker_id = ?, state = 'done', error = '' WHERE id = ?",
                    (sticker_id, slot["id"]),
                )
            old = slot["sticker_id"]
            if old is not None and old != sticker_id:
                await self._forget([old])
        self._changed(pack["bot_id"], pack["id"])

    async def _label(self, sticker_id: int, bot_id: str, mood: str) -> None:
        """Described by its mood (so no vision call is needed), ready to send."""
        bot = self.bots().get(bot_id)
        who = bot.name if bot else ""
        hint = HINTS.get(mood, "")
        await self.stickers.store.update(
            sticker_id,
            description=f"{who}{mood}" + (f"：{hint}" if hint else ""),
            tags=[mood],
            status="ready",
        )

    async def _forget(self, ids: list[int]) -> None:
        """Remove pictures no other slot uses any more (from the collection, with their files)."""
        if not ids:
            return
        used = await self.all_pictures()
        gone = [i for i in ids if i not in used]
        if gone:
            await self.stickers.remove(gone)


def _mood(value: str) -> str:
    mood = " ".join(value.split())[:MOOD_MAX]
    if not mood:
        raise PackError("写一个表情的名字，比如「害羞」")
    return mood
