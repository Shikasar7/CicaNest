"""语气 (tables from 013): the tones bots can speak in, and which group uses which (bot_channels)."""

from __future__ import annotations

import time
from typing import Any

from nest.core.tones import BUILTIN, BUILTIN_BY_KEY, Tone
from nest.storage.bots import InUse, NameTaken
from nest.storage.db import Database


class ToneRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def seed(self) -> None:
        """Add the built-in tones that are not there yet (never overwrites an edited one)."""
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.executemany(
                "INSERT OR IGNORE INTO tones (key, label, manner, builtin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                [(t.key, t.label, t.manner, now, now) for t in BUILTIN],
            )

    async def tones(self) -> dict[str, Tone]:
        rows = await self.db.fetchall("SELECT key, label, manner FROM tones")
        return {r["key"]: Tone(r["key"], r["label"], r["manner"]) for r in rows}

    async def views(self) -> list[dict[str, Any]]:
        """Built-in ones first (in their order), then the added ones by age; with where each is used."""
        rows = await self.db.fetchall(
            "SELECT key, label, manner, builtin, created_at, updated_at FROM tones ORDER BY created_at, key"
        )
        uses = await self.uses()
        order = {t.key: n for n, t in enumerate(BUILTIN)}
        views = [dict(r) | {"builtin": bool(r["builtin"]), "used_by": uses.get(r["key"], [])} for r in rows]
        views.sort(key=lambda v: (not v["builtin"], order.get(v["key"], 0), v["created_at"]))
        for view in views:
            original = BUILTIN_BY_KEY.get(view["key"])
            view["edited"] = bool(
                original and (original.label, original.manner) != (view["label"], view["manner"])
            )
        return views

    async def get(self, key: str) -> Tone | None:
        row = await self.db.fetchone("SELECT key, label, manner FROM tones WHERE key = ?", (key,))
        return Tone(row["key"], row["label"], row["manner"]) if row else None

    async def uses(self) -> dict[str, list[dict[str, str]]]:
        """Tone key -> the bots and groups using it, for chat ("chat") or 答题 ("answer", where it is on)."""
        rows = await self.db.fetchall(
            """SELECT bc.answer_tone, bc.chat_tone, bc.answer_questions, b.name AS bot, c.kind,
                      c.name AS channel,
                      c.external_id
               FROM bot_channels bc JOIN bots b ON b.id = bc.bot_id JOIN channels c ON c.id = bc.channel_id
               WHERE b.deleted_at IS NULL"""
        )
        out: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            where = r["channel"] or (
                f"私聊 {r['external_id']}" if r["kind"] == "private" else r["external_id"]
            )
            out.setdefault(r["chat_tone"], []).append({"bot": r["bot"], "channel": where, "as": "chat"})
            if r["answer_questions"]:  # an answer tone only counts where 答题 is on
                out.setdefault(r["answer_tone"], []).append(
                    {"bot": r["bot"], "channel": where, "as": "answer"}
                )
        return out

    async def create(self, label: str, manner: str) -> str:
        now = time.time()
        async with self.db.transaction() as conn:
            if await (await conn.execute("SELECT 1 FROM tones WHERE label = ?", (label,))).fetchone():
                raise NameTaken(label)
            row = await (await conn.execute("SELECT count(*) FROM tones WHERE builtin = 0")).fetchone()
            n = int(row[0]) + 1
            while await (
                await conn.execute("SELECT 1 FROM tones WHERE key = ?", (f"custom-{n}",))
            ).fetchone():
                n += 1
            key = f"custom-{n}"
            await conn.execute(
                "INSERT INTO tones (key, label, manner, builtin, created_at, updated_at)"
                " VALUES (?, ?, ?, 0, ?, ?)",
                (key, label, manner, now, now),
            )
        return key

    async def update(self, key: str, *, label: str | None = None, manner: str | None = None) -> bool:
        async with self.db.transaction() as conn:
            if (
                label is not None
                and await (
                    await conn.execute("SELECT 1 FROM tones WHERE label = ? AND key != ?", (label, key))
                ).fetchone()
            ):
                raise NameTaken(label)
            cursor = await conn.execute(
                "UPDATE tones SET label = coalesce(?, label), manner = coalesce(?, manner), updated_at = ? "
                "WHERE key = ?",
                (label, manner, time.time(), key),
            )
            return cursor.rowcount > 0

    async def reset(self, key: str) -> bool:
        """A built-in tone back to how it shipped."""
        original = BUILTIN_BY_KEY.get(key)
        if original is None:
            return False
        return await self.update(key, label=original.label, manner=original.manner)

    async def delete(self, key: str) -> bool:
        """An added tone (built-in ones stay); InUse while a group uses it."""
        if key in BUILTIN_BY_KEY:
            raise ValueError("built-in")
        users = (await self.uses()).get(key, [])
        if users:
            raise InUse([f"{u['bot']}「{u['channel']}」" for u in users])
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM tones WHERE key = ?", (key,))
            return cursor.rowcount > 0
