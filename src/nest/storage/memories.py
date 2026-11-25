"""长期记忆's tables (from 026): what is remembered of people, per place, and how far each place was read."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from nest.storage.db import Database

KINDS = ("like", "fact", "promise")
CHUNK = 500  # ids per IN (...) query
COLUMNS = "id, place, person_id, person, bot_id, kind, content, due, source, created_at, updated_at, used_at"


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- reading ------------------------------------------------------------------------------------

    async def of_people(self, place: str, person_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Everything remembered of these people in this place (every bot's 约定 included), oldest first."""
        found: list[dict[str, Any]] = []
        for start in range(0, len(person_ids), CHUNK):
            chunk = list(person_ids[start : start + CHUNK])
            marks = ",".join("?" * len(chunk))
            rows = await self.db.fetchall(
                f"""SELECT {COLUMNS} FROM memories WHERE place = ? AND person_id IN ({marks})
                    ORDER BY created_at, id""",
                (place, *chunk),
            )
            found.extend(dict(row) for row in rows)
        found.sort(key=lambda row: (row["created_at"], row["id"]))
        return found

    async def of_place(self, place: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"""SELECT {COLUMNS} FROM memories WHERE place = ?
                ORDER BY person, person_id, kind, created_at, id""",
            (place,),
        )
        return [dict(row) for row in rows]

    async def get(self, memory_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM memories WHERE id = ?", (memory_id,))
        return dict(row) if row else None

    async def places(self) -> dict[str, int]:
        """Every place with memories, and how many."""
        rows = await self.db.fetchall("SELECT place, count(*) AS n FROM memories GROUP BY place")
        return {row["place"]: row["n"] for row in rows}

    # -- writing ------------------------------------------------------------------------------------

    async def add(
        self,
        place: str,
        person_id: str,
        person: str,
        kind: str,
        content: str,
        *,
        bot_id: str | None = None,
        due: str | None = None,
        source: str = "learned",
        at: float,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO memories (place, person_id, person, bot_id, kind, content, due, source,
                       created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (place, person_id, person, bot_id, kind, content, due, source, at, at),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def update(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        kind: str | None = None,
        due: str | None = None,
        clear_due: bool = False,
        source: str | None = None,
        at: float,
    ) -> None:
        columns: dict[str, Any] = {"updated_at": at}
        if content is not None:
            columns["content"] = content
        if kind is not None:
            columns["kind"] = kind
        if due is not None or clear_due:
            columns["due"] = due
        if source is not None:
            columns["source"] = source
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE memories SET {assignments} WHERE id = ?", (*columns.values(), memory_id)
            )

    async def rename(self, place: str, person_id: str, person: str) -> None:
        """The name they go by now: shown in the console and to the model."""
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE memories SET person = ? WHERE place = ? AND person_id = ? AND person != ?",
                (person, place, person_id, person),
            )

    async def delete(self, memory_ids: Iterable[int]) -> int:
        ids = list(memory_ids)
        removed = 0
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            marks = ",".join("?" * len(chunk))
            async with self.db.transaction() as conn:
                cursor = await conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", chunk)
                removed += cursor.rowcount
                await cursor.close()
        return removed

    async def used(self, memory_ids: Iterable[int], at: float) -> None:
        ids = list(memory_ids)
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            marks = ",".join("?" * len(chunk))
            async with self.db.transaction() as conn:
                await conn.execute(f"UPDATE memories SET used_at = ? WHERE id IN ({marks})", (at, *chunk))

    async def forget_place(self, place: str) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM memories WHERE place = ?", (place,))
            removed = cursor.rowcount
            await cursor.close()
            await conn.execute("DELETE FROM memory_progress WHERE place = ?", (place,))
        return removed

    # -- cleanup ------------------------------------------------------------------------------------

    async def expired_promises(self, before_day: str, unused_since: float) -> list[int]:
        """Learned 约定 whose day is before `before_day`, or without a day and not touched since then."""
        rows = await self.db.fetchall(
            """SELECT id FROM memories WHERE kind = 'promise' AND source = 'learned'
               AND ((due IS NOT NULL AND due < ?)
                    OR (due IS NULL AND max(updated_at, coalesce(used_at, 0)) < ?))""",
            (before_day, unused_since),
        )
        return [row["id"] for row in rows]

    async def overflow(self, limit: int, promise_limit: int) -> list[int]:
        """Learned memories past the limits, the least recently used or updated first to go: per person and
        place, `limit` likes and facts together, and `promise_limit` 约定 per bot. The ones the user added
        (typed in the console, brought in with a companion) are never counted: however many there are, what
        she learns since still has its room."""
        rows = await self.db.fetchall(
            """SELECT id FROM (
                   SELECT id, row_number() OVER (
                       PARTITION BY place, person_id, kind = 'promise', coalesce(bot_id, '')
                       ORDER BY max(updated_at, coalesce(used_at, 0)) DESC, id DESC
                   ) AS n, kind FROM memories WHERE source = 'learned')
               WHERE n > CASE WHEN kind = 'promise' THEN ? ELSE ? END""",
            (promise_limit, limit),
        )
        return [row["id"] for row in rows]

    # -- progress -----------------------------------------------------------------------------------

    async def progress(self) -> dict[str, dict[str, Any]]:
        rows = await self.db.fetchall("SELECT place, upto, learned_at, detail, error FROM memory_progress")
        return {row["place"]: dict(row) for row in rows}

    async def record(
        self, place: str, *, learned_at: float, upto: float | None, detail: str = "", error: str = ""
    ) -> None:
        """A run: `upto` (None: unchanged) is how far the place has now been read."""
        async with self.db.transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO memory_progress (place) VALUES (?)", (place,))
            await conn.execute(
                """UPDATE memory_progress SET learned_at = ?, detail = ?, error = ?,
                       upto = max(upto, coalesce(?, 0)) WHERE place = ?""",
                (learned_at, detail, error, upto, place),
            )
