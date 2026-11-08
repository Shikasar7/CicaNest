"""项目资料 bases, which group uses which, and the cached section vectors (tables from 009, 010, 011)."""

from __future__ import annotations

import time
from array import array
from dataclasses import dataclass

from nest.storage.db import Database

VECTOR_DAYS = 30  # a cached vector no section has used for this long is dropped


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: int
    name: str
    folder: str
    created_at: float
    forward_to: str = ""  # QQ number unanswered questions are forwarded to
    source_dir: str = ""  # the project's source checkout, read when the docs do not answer; "" = none


class KnowledgeRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def bases(self) -> list[KnowledgeBase]:
        rows = await self.db.fetchall(
            "SELECT id, name, folder, created_at, forward_to, source_dir FROM knowledge_bases ORDER BY id"
        )
        return [
            KnowledgeBase(r["id"], r["name"], r["folder"], r["created_at"], r["forward_to"], r["source_dir"])
            for r in rows
        ]

    async def add(self, name: str, folder: str) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO knowledge_bases (name, folder, created_at) VALUES (?, ?, ?)",
                (name, folder, time.time()),
            )
            return int(cursor.lastrowid or 0)

    async def update(
        self,
        base_id: int,
        *,
        name: str | None = None,
        folder: str | None = None,
        source_dir: str | None = None,
    ) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE knowledge_bases SET name = coalesce(?, name), folder = coalesce(?, folder), "
                "source_dir = coalesce(?, source_dir) WHERE id = ?",
                (name, folder, source_dir, base_id),
            )
            return cursor.rowcount > 0

    async def delete(self, base_id: int) -> bool:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE group_features SET knowledge_base_id = NULL WHERE knowledge_base_id = ?", (base_id,)
            )
            cursor = await conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (base_id,))
            return cursor.rowcount > 0

    async def groups(self) -> dict[str, int]:
        """Group key -> the base it answers from."""
        rows = await self.db.fetchall(
            "SELECT group_key, knowledge_base_id FROM group_features WHERE knowledge_base_id IS NOT NULL"
        )
        return {r["group_key"]: r["knowledge_base_id"] for r in rows}

    async def bind(self, group_key: str, base_id: int | None) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO group_features (group_key) VALUES (?)", (group_key,))
            await conn.execute(
                "UPDATE group_features SET knowledge_base_id = ? WHERE group_key = ?", (base_id, group_key)
            )

    async def vectors(self, model: str) -> dict[str, array]:
        rows = await self.db.fetchall("SELECT key, vector FROM knowledge_vectors WHERE model = ?", (model,))
        out = {}
        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector"])
            out[row["key"]] = vector
        return out

    async def keep_vectors(self, model: str, vectors: dict[str, array], used: set[str]) -> None:
        """Store new vectors, mark the ones in use, and drop those unused for VECTOR_DAYS."""
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.executemany(
                "INSERT INTO knowledge_vectors (key, model, vector, used_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET used_at = excluded.used_at",
                [(key, model, vectors[key].tobytes(), now) for key in used if key in vectors],
            )
            cutoff = now - VECTOR_DAYS * 86400
            await conn.execute("DELETE FROM knowledge_vectors WHERE used_at < ?", (cutoff,))


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    base_id: int
    title: str
    body: str
    created_at: float


GAP_COLUMNS = (
    "id, base_id, group_key, account_id, message_id, asker, question, answer, reason, status, created_at, "
    "forwarded_at, note_id, analysis, analysis_sure, analysis_files"
)


class GapRepo:
    """Unanswered project questions (knowledge_gaps), the answers added for them (knowledge_notes), and
    whom a base forwards them to."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def forward_to(self, base_id: int) -> str:
        row = await self.db.fetchone("SELECT forward_to FROM knowledge_bases WHERE id = ?", (base_id,))
        return str(row["forward_to"]) if row else ""

    async def set_forward_to(self, base_id: int, user_id: str) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE knowledge_bases SET forward_to = ? WHERE id = ?", (user_id, base_id)
            )
            return cursor.rowcount > 0

    async def add_gap(
        self,
        *,
        base_id: int,
        group_key: str,
        account_id: str,
        message_id: int | None,
        asker: str,
        question: str,
        answer: str,
        reason: str,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO knowledge_gaps (base_id, group_key, account_id, message_id, asker, question,
                       answer, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (base_id, group_key, account_id, message_id, asker, question, answer, reason, time.time()),
            )
            return int(cursor.lastrowid or 0)

    async def gaps(self, base_id: int, *, status: str | None = None, limit: int = 200) -> list[dict]:
        rows = await self.db.fetchall(
            f"""SELECT {GAP_COLUMNS} FROM knowledge_gaps WHERE base_id = ? AND (? IS NULL OR status = ?)
                ORDER BY created_at DESC LIMIT ?""",
            (base_id, status, status, limit),
        )
        return [dict(row) for row in rows]

    async def gap(self, gap_id: int) -> dict | None:
        row = await self.db.fetchone(f"SELECT {GAP_COLUMNS} FROM knowledge_gaps WHERE id = ?", (gap_id,))
        return dict(row) if row else None

    async def unforwarded(self, base_id: int) -> list[dict]:
        rows = await self.db.fetchall(
            f"""SELECT {GAP_COLUMNS} FROM knowledge_gaps
                WHERE base_id = ? AND status = 'open' AND forwarded_at IS NULL ORDER BY created_at""",
            (base_id,),
        )
        return [dict(row) for row in rows]

    async def mark_forwarded(self, gap_ids: list[int]) -> None:
        if not gap_ids:
            return
        marks = ",".join("?" * len(gap_ids))
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE knowledge_gaps SET forwarded_at = ? WHERE id IN ({marks})", (time.time(), *gap_ids)
            )

    async def set_analysis(self, gap_id: int, analysis: str, *, sure: bool, files: list[str]) -> None:
        """What reading the source showed for this question (看源码)."""
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE knowledge_gaps SET analysis = ?, analysis_sure = ?, analysis_files = ? WHERE id = ?",
                (analysis, int(sure), "\n".join(files), gap_id),
            )

    async def set_status(self, gap_id: int, status: str, note_id: int | None = None) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE knowledge_gaps SET status = ?, note_id = coalesce(?, note_id) WHERE id = ?",
                (status, note_id, gap_id),
            )
            return cursor.rowcount > 0

    async def add_note(self, base_id: int, title: str, body: str) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO knowledge_notes (base_id, title, body, created_at) VALUES (?, ?, ?, ?)",
                (base_id, title, body, time.time()),
            )
            return int(cursor.lastrowid or 0)

    async def notes(self, base_id: int) -> list[Note]:
        rows = await self.db.fetchall(
            "SELECT id, base_id, title, body, created_at FROM knowledge_notes WHERE base_id = ? ORDER BY id",
            (base_id,),
        )
        return [Note(r["id"], r["base_id"], r["title"], r["body"], r["created_at"]) for r in rows]

    async def delete_note(self, note_id: int) -> int | None:
        """The base the note belonged to, or None."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM knowledge_notes WHERE id = ? RETURNING base_id", (note_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                await conn.execute(
                    "UPDATE knowledge_gaps SET status = 'open', note_id = NULL WHERE note_id = ?", (note_id,)
                )
        return int(row[0]) if row else None
