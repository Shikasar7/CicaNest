"""提醒's table (from 028): what a companion reminds the user of, and when."""

from __future__ import annotations

import time
from typing import Any

from nest.storage.db import Database

COLUMNS = (
    "id, bot_id, channel_id, text, due_at, repeat, also_qq, active, source, fired_at, nudge_at, created_at, "
    "updated_at"
)


def _row(row: Any) -> dict[str, Any]:
    found = dict(row)
    found["also_qq"] = bool(found["also_qq"])
    found["active"] = bool(found["active"])
    return found


class ReminderStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def of_bot(self, bot_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM reminders WHERE bot_id = ? ORDER BY active DESC, due_at, id", (bot_id,)
        )
        return [_row(r) for r in rows]

    async def get(self, reminder_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM reminders WHERE id = ?", (reminder_id,))
        return _row(row) if row else None

    async def due(self, now: float) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM reminders WHERE active = 1 AND due_at <= ? ORDER BY due_at, id", (now,)
        )
        return [_row(r) for r in rows]

    async def to_nudge(self, now: float) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM reminders WHERE nudge_at IS NOT NULL AND nudge_at <= ? ORDER BY nudge_at",
            (now,),
        )
        return [_row(r) for r in rows]

    async def add(
        self,
        *,
        bot_id: str,
        channel_id: int,
        text: str,
        due_at: float,
        repeat: str = "",
        also_qq: bool = False,
        source: str = "console",
    ) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO reminders (bot_id, channel_id, text, due_at, repeat, also_qq, source,
                       created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (bot_id, channel_id, text, due_at, repeat, int(also_qq), source, now, now),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def update(self, reminder_id: int, **changes: Any) -> bool:
        allowed = {"text", "due_at", "repeat", "also_qq", "active", "fired_at", "nudge_at"}
        columns = {k: (int(v) if isinstance(v, bool) else v) for k, v in changes.items() if k in allowed}
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE reminders SET {assignments} WHERE id = ?", (*columns.values(), reminder_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete(self, reminder_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted
