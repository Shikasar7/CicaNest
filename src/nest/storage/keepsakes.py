"""回忆档案's table (from 027): a companion's records, word for word, per bot. A draft (037) is one she wrote
herself from her chat, kept apart until the user has read it."""

from __future__ import annotations

import time
from typing import Any

from nest.storage.db import Database

COLUMNS = "id, bot_id, title, body, source, draft, created_at, updated_at"


class KeepsakeStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def of_bot(self, bot_id: str, *, drafts: bool = True) -> list[dict[str, Any]]:
        """Her records, oldest first; `drafts=False`: only the ones the user kept."""
        kept = "" if drafts else " AND draft = 0"
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM keepsakes WHERE bot_id = ?{kept} ORDER BY id", (bot_id,)
        )
        return [_view(row) for row in rows]

    async def drafts(self) -> dict[str, int]:
        """Bot id -> how many of her drafts wait for the user to read them."""
        rows = await self.db.fetchall(
            "SELECT bot_id, count(*) AS n FROM keepsakes WHERE draft = 1 GROUP BY bot_id"
        )
        return {row["bot_id"]: int(row["n"]) for row in rows}

    async def get(self, keepsake_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM keepsakes WHERE id = ?", (keepsake_id,))
        return _view(row) if row else None

    async def add(self, bot_id: str, title: str, body: str, *, source: str = "", draft: bool = False) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO keepsakes (bot_id, title, body, source, draft, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (bot_id, title, body, source, int(draft), now, now),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def update(
        self, keepsake_id: int, *, title: str | None = None, body: str | None = None, kept: bool = False
    ) -> bool:
        """`kept`: the user read the draft and keeps it (it is a record like the others from now on)."""
        columns: dict[str, Any] = {}
        if title is not None:
            columns["title"] = title
        if body is not None:
            columns["body"] = body
        if kept:
            columns["draft"] = 0
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE keepsakes SET {assignments} WHERE id = ?", (*columns.values(), keepsake_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete(self, keepsake_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM keepsakes WHERE id = ?", (keepsake_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    # -- how far her chat is written up (keepsake_progress) -----------------------------------------

    async def progress(self, bot_id: str) -> tuple[float, float]:
        """(upto, run_at): the last message written up, and when she last wrote; (0, 0) before the first."""
        row = await self.db.fetchone("SELECT upto, run_at FROM keepsake_progress WHERE bot_id = ?", (bot_id,))
        return (float(row["upto"]), float(row["run_at"])) if row else (0.0, 0.0)

    async def set_progress(self, bot_id: str, upto: float, run_at: float) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO keepsake_progress (bot_id, upto, run_at) VALUES (?, ?, ?)
                   ON CONFLICT (bot_id) DO UPDATE SET upto = excluded.upto, run_at = excluded.run_at""",
                (bot_id, upto, run_at),
            )


def _view(row: Any) -> dict[str, Any]:
    return dict(row) | {"draft": bool(row["draft"])}
