"""定时任务's tables (from 032): what a companion does at set times, and each time it was due."""

from __future__ import annotations

import time
from typing import Any

from nest.storage.db import Database

COLUMNS = (
    "id, bot_id, channel_id, title, goal, first_at, repeat, next_at, enabled, also_qq, source, last_at, "
    "created_at, updated_at"
)
RUN_COLUMNS = "r.id, r.routine_id, r.at, r.task_id, r.skipped, t.status, t.report, t.error"
RUNS_KEPT = 60  # per routine


def _row(row: Any) -> dict[str, Any]:
    found = dict(row)
    found["enabled"] = bool(found["enabled"])
    found["also_qq"] = bool(found["also_qq"])
    return found


class RoutineStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def of_bot(self, bot_id: str) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM routines WHERE bot_id = ? ORDER BY enabled DESC, next_at, id", (bot_id,)
        )
        return [_row(r) for r in rows]

    async def get(self, routine_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM routines WHERE id = ?", (routine_id,))
        return _row(row) if row else None

    async def due(self, now: float) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM routines WHERE enabled = 1 AND next_at <= ? ORDER BY next_at, id", (now,)
        )
        return [_row(r) for r in rows]

    async def add(
        self,
        *,
        bot_id: str,
        channel_id: int,
        title: str,
        goal: str,
        first_at: float,
        repeat: str,
        next_at: float,
        also_qq: bool = False,
        source: str = "console",
        enabled: bool = True,
    ) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO routines (bot_id, channel_id, title, goal, first_at, repeat, next_at, also_qq,
                       source, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    bot_id,
                    channel_id,
                    title,
                    goal,
                    first_at,
                    repeat,
                    next_at,
                    int(also_qq),
                    source,
                    int(enabled),
                    now,
                    now,
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def update(self, routine_id: int, **changes: Any) -> bool:
        allowed = {"title", "goal", "first_at", "repeat", "next_at", "enabled", "also_qq", "last_at"}
        columns = {k: (int(v) if isinstance(v, bool) else v) for k, v in changes.items() if k in allowed}
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE routines SET {assignments} WHERE id = ?", (*columns.values(), routine_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete(self, routine_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    async def add_run(
        self, routine_id: int, at: float, *, task_id: int | None = None, skipped: str = ""
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO routine_runs (routine_id, at, task_id, skipped) VALUES (?, ?, ?, ?)",
                (routine_id, at, task_id, skipped),
            )
            await conn.execute(
                """DELETE FROM routine_runs WHERE routine_id = ? AND id NOT IN
                       (SELECT id FROM routine_runs WHERE routine_id = ? ORDER BY id DESC LIMIT ?)""",
                (routine_id, routine_id, RUNS_KEPT),
            )

    async def runs(self, routine_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first, with the task's state and report when one was started."""
        rows = await self.db.fetchall(
            f"""SELECT {RUN_COLUMNS} FROM routine_runs r LEFT JOIN tasks t ON t.id = r.task_id
                WHERE r.routine_id = ? ORDER BY r.id DESC LIMIT ?""",
            (routine_id, limit),
        )
        return [dict(r) for r in rows]
