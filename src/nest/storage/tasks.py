"""任务's tables (from 029): a companion's background tasks and their steps."""

from __future__ import annotations

import time
from typing import Any

from nest.storage.db import Database

COLUMNS = (
    "id, bot_id, channel_id, title, goal, status, source, provider_id, steps, tokens, seconds, max_steps, "
    "max_seconds, max_tokens, report, error, created_at, started_at, ended_at, updated_at, unattended, "
    "also_qq, routine_id"
)
STEP_COLUMNS = "id, task_id, kind, tool, given, result, status, seen, ms, created_at"
ACTIVE = ("queued", "running", "waiting_pc")
STEP_RESULT_CHARS = 4000  # kept of a tool's result; the model saw more


def _step(row: Any) -> dict[str, Any]:
    found = dict(row)
    found["seen"] = bool(found["seen"])
    return found


class TaskStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        *,
        bot_id: str,
        channel_id: int,
        title: str,
        goal: str,
        source: str,
        max_steps: int,
        max_seconds: float,
        max_tokens: int,
        unattended: bool = False,
        also_qq: bool = False,
        routine_id: int | None = None,
    ) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO tasks (bot_id, channel_id, title, goal, source, max_steps, max_seconds,
                       max_tokens, created_at, updated_at, unattended, also_qq, routine_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    bot_id,
                    channel_id,
                    title,
                    goal,
                    source,
                    max_steps,
                    max_seconds,
                    max_tokens,
                    now,
                    now,
                    int(unattended),
                    int(also_qq),
                    routine_id,
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def get(self, task_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM tasks WHERE id = ?", (task_id,))
        return dict(row) if row else None

    async def of_bot(self, bot_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {COLUMNS} FROM tasks WHERE bot_id = ? ORDER BY id DESC LIMIT ?", (bot_id, limit)
        )
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(f"SELECT {COLUMNS} FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    async def active_of_routine(self, routine_id: int) -> dict[str, Any] | None:
        """The task of a routine still queued, running or waiting, if any."""
        marks = ", ".join("?" for _ in ACTIVE)
        row = await self.db.fetchone(
            f"SELECT {COLUMNS} FROM tasks WHERE routine_id = ? AND status IN ({marks}) ORDER BY id DESC",
            (routine_id, *ACTIVE),
        )
        return dict(row) if row else None

    async def queued(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(f"SELECT {COLUMNS} FROM tasks WHERE status = 'queued' ORDER BY id")
        return [dict(r) for r in rows]

    async def update(self, task_id: int, **changes: Any) -> bool:
        allowed = {
            "status",
            "provider_id",
            "steps",
            "tokens",
            "seconds",
            "max_steps",
            "max_seconds",
            "max_tokens",
            "report",
            "error",
            "started_at",
            "ended_at",
        }
        columns = {k: v for k, v in changes.items() if k in allowed}
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?", (*columns.values(), task_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def requeue_waiting(self) -> list[int]:
        """At startup: what was waiting for the PC goes back into the queue. The step it waited with was not
        done, so nothing is done twice. Their ids."""
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute("SELECT id FROM tasks WHERE status = 'waiting_pc' ORDER BY id")
            ids = [int(row[0]) for row in await cursor.fetchall()]
            await cursor.close()
            for task_id in ids:
                await conn.execute(
                    "UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ?", (now, task_id)
                )
                await conn.execute(
                    "UPDATE task_steps SET status = 'done', result = ?"
                    " WHERE task_id = ? AND status = 'running'",
                    ("等电脑的时候 nest 重启了，这一步没做。", task_id),
                )
        return ids

    async def interrupt_running(self) -> int:
        """At startup: what was running when nest stopped is marked so, never re-run by itself."""
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE tasks SET status = 'interrupted', ended_at = ?, updated_at = ?"
                " WHERE status = 'running'",
                (now, now),
            )
            changed = cursor.rowcount
            await cursor.close()
        return changed

    async def delete(self, task_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM tasks WHERE id = ? AND status NOT IN ('queued', 'running')", (task_id,)
            )
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    async def purge(self, before: float) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM tasks WHERE status NOT IN ('queued', 'running') AND updated_at < ?", (before,)
            )
            deleted = cursor.rowcount
            await cursor.close()
        return deleted

    # -- steps --------------------------------------------------------------------------------------------

    async def add_step(
        self,
        task_id: int,
        kind: str,
        *,
        tool: str = "",
        given: str = "",
        result: str = "",
        status: str = "done",
        seen: bool = True,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO task_steps (task_id, kind, tool, given, result, status, seen, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (task_id, kind, tool, given, result[:STEP_RESULT_CHARS], status, int(seen), time.time()),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def finish_step(self, step_id: int, *, result: str, ms: float, status: str = "done") -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE task_steps SET result = ?, ms = ?, status = ? WHERE id = ?",
                (result[:STEP_RESULT_CHARS], ms, status, step_id),
            )

    async def steps(self, task_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            f"SELECT {STEP_COLUMNS} FROM task_steps WHERE task_id = ? ORDER BY id", (task_id,)
        )
        return [_step(r) for r in rows]

    async def unseen_notes(self, task_id: int) -> list[dict[str, Any]]:
        """What the user added since she was last told; marked seen as it is read."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"SELECT {STEP_COLUMNS} FROM task_steps WHERE task_id = ? AND kind = 'note' AND seen = 0"
                " ORDER BY id",
                (task_id,),
            )
            rows = [_step(r) for r in await cursor.fetchall()]
            await cursor.close()
            if rows:
                await conn.execute(
                    "UPDATE task_steps SET seen = 1 WHERE task_id = ? AND kind = 'note' AND seen = 0",
                    (task_id,),
                )
        return rows
