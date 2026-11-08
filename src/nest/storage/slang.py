"""Per-group switches (table `group_features`) and each group's 黑话 (tables `slang`, `slang_runs`), from 007.

A group is keyed by its real identity, "qq:group:<number>": the three accounts in one QQ group share it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from nest.core.events import ChannelRef
from nest.storage.db import Database

SOURCES = ("auto", "edited", "manual")


def group_key_of(channel: ChannelRef) -> str:
    return f"{channel.platform}:{channel.kind}:{channel.id}"


def split_group_key(key: str) -> tuple[str, str, str]:
    """(platform, kind, external id); ValueError for anything else."""
    platform, kind, external_id = key.split(":", 2)
    if not platform or kind not in ("group", "private") or not external_id:
        raise ValueError(key)
    return platform, kind, external_id


@dataclass(frozen=True, slots=True)
class GroupFeatures:
    group_key: str
    collect_stickers: bool = False
    learn_slang: bool = False
    slang_learned_at: float | None = None
    slang_learned_until: float | None = None


class GroupFeatureStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def all(self) -> dict[str, GroupFeatures]:
        rows = await self.db.fetchall("SELECT * FROM group_features")
        return {
            row["group_key"]: GroupFeatures(
                group_key=row["group_key"],
                collect_stickers=bool(row["collect_stickers"]),
                learn_slang=bool(row["learn_slang"]),
                slang_learned_at=row["slang_learned_at"],
                slang_learned_until=row["slang_learned_until"],
            )
            for row in rows
        }

    async def get(self, group_key: str) -> GroupFeatures:
        return (await self.all()).get(group_key) or GroupFeatures(group_key)

    async def set(
        self, group_key: str, *, collect_stickers: bool | None = None, learn_slang: bool | None = None
    ) -> None:
        columns: dict[str, Any] = {}
        if collect_stickers is not None:
            columns["collect_stickers"] = int(collect_stickers)
        if learn_slang is not None:
            columns["learn_slang"] = int(learn_slang)
        async with self.db.transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO group_features (group_key) VALUES (?)", (group_key,))
            if columns:
                assignments = ", ".join(f"{column} = ?" for column in columns)
                await conn.execute(
                    f"UPDATE group_features SET {assignments} WHERE group_key = ?",
                    (*columns.values(), group_key),
                )

    async def learned(self, group_key: str, at: float, until: float | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO group_features (group_key) VALUES (?)", (group_key,))
            await conn.execute(
                """UPDATE group_features SET slang_learned_at = ?,
                       slang_learned_until = max(coalesce(slang_learned_until, 0), coalesce(?, 0))
                   WHERE group_key = ?""",
                (at, until, group_key),
            )


class SlangStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def entries(self, group_key: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM slang WHERE group_key = ? ORDER BY last_seen_at DESC, id DESC", (group_key,)
        )
        return [dict(row) for row in rows]

    async def counts(self) -> dict[str, int]:
        rows = await self.db.fetchall("SELECT group_key, count(*) AS n FROM slang GROUP BY group_key")
        return {row["group_key"]: row["n"] for row in rows}

    async def get(self, entry_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM slang WHERE id = ?", (entry_id,))
        return dict(row) if row else None

    async def find(self, group_key: str, term: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM slang WHERE group_key = ? AND term = ?", (group_key, term)
        )
        return dict(row) if row else None

    async def add(
        self,
        group_key: str,
        term: str,
        meaning: str,
        *,
        example: str = "",
        source: str = "auto",
        hits: int = 0,
        at: float | None = None,
    ) -> int:
        """Insert a term; sqlite3.IntegrityError (via aiosqlite) when the group already has it."""
        now = time.time() if at is None else at
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO slang (group_key, term, meaning, example, source, hits, first_seen_at,
                       last_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (group_key, term, meaning, example, source, hits, now, now, now),
            )
            entry_id = cursor.lastrowid
            await cursor.close()
        assert entry_id is not None
        return entry_id

    async def learned(
        self, entry_id: int, *, meaning: str | None, example: str | None, hits: int, at: float
    ) -> None:
        """What a learning run found about an existing term. The meaning and example only change for a term
        nobody edited (the caller decides: pass None to keep them)."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """UPDATE slang SET hits = hits + ?, last_seen_at = max(last_seen_at, ?),
                       meaning = coalesce(?, meaning), example = coalesce(?, example),
                       updated_at = CASE WHEN ? IS NULL THEN updated_at ELSE ? END
                   WHERE id = ?""",
                (hits, at, meaning, example, meaning, at, entry_id),
            )

    async def update(
        self,
        entry_id: int,
        *,
        term: str | None = None,
        meaning: str | None = None,
        example: str | None = None,
    ) -> bool:
        """An edit (or 「确认」 with nothing changed) from the console: learning no longer changes the
        meaning."""
        columns: dict[str, Any] = {}
        if term is not None:
            columns["term"] = term
        if meaning is not None:
            columns["meaning"] = meaning
        if example is not None:
            columns["example"] = example
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"""UPDATE slang SET {assignments},
                        source = CASE WHEN source = 'manual' THEN 'manual' ELSE 'edited' END
                    WHERE id = ?""",
                (*columns.values(), entry_id),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete(self, entry_id: int) -> dict[str, Any] | None:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM slang WHERE id = ? RETURNING *", (entry_id,))
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row else None

    async def trim(self, group_key: str, keep: int) -> int:
        """Beyond `keep` terms, delete the learned ones seen longest ago (never edited or added by hand)."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """DELETE FROM slang WHERE id IN (
                       SELECT id FROM slang WHERE group_key = ? AND source = 'auto'
                       ORDER BY last_seen_at DESC, id DESC LIMIT -1 OFFSET max(0, ? - (
                           SELECT count(*) FROM slang WHERE group_key = ? AND source != 'auto')))""",
                (group_key, keep, group_key),
            )
            deleted = cursor.rowcount
            await cursor.close()
        return deleted

    async def purge_stale(self, before: float) -> int:
        """Learned terms nobody edited that have not come up since `before`."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM slang WHERE source = 'auto' AND last_seen_at < ?", (before,)
            )
            deleted = cursor.rowcount
            await cursor.close()
        return deleted

    # -- runs --------------------------------------------------------------------------------------

    async def record_run(
        self,
        group_key: str,
        *,
        started_at: float,
        messages: int,
        added: int = 0,
        updated: int = 0,
        dropped: int = 0,
        detail: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        duration = int((time.time() - started_at) * 1000)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO slang_runs (group_key, started_at, duration_ms, messages, added, updated,
                       dropped, detail, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
                (group_key, started_at, duration, messages, added, updated, dropped, detail, error),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return dict(row)

    async def runs(self, group_key: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM slang_runs WHERE group_key = ? ORDER BY started_at DESC, id DESC LIMIT ?",
            (group_key, limit),
        )
        return [dict(row) for row in rows]

    async def purge_runs(self, before: float) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM slang_runs WHERE started_at < ?", (before,))
            deleted = cursor.rowcount
            await cursor.close()
        return deleted
