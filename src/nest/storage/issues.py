"""问题记录 (migration 014): the issues each group's 记录官 wrote down, and how far it has read."""

from __future__ import annotations

import time
from typing import Any

from nest.storage.db import Database

KINDS = ("unsolved", "wrong", "bug", "request", "feedback")
STATUSES = ("open", "done", "ignored")
ISSUE_COLUMNS = (
    "id, group_id, group_name, bot_id, message_id, said_at, speaker, speaker_id, kind, summary, quote, "
    "ai_reply, status, created_at, reported_at"
)


class IssueRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        *,
        group_id: str,
        group_name: str,
        bot_id: str,
        message_id: int,
        said_at: float,
        speaker: str,
        speaker_id: str,
        kind: str,
        summary: str,
        quote: str,
        ai_reply: str,
    ) -> int | None:
        """The new issue's id, or None when this message was already written down."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT OR IGNORE INTO issues (group_id, group_name, bot_id, message_id, said_at, speaker,
                       speaker_id, kind, summary, quote, ai_reply, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    group_id,
                    group_name,
                    bot_id,
                    message_id,
                    said_at,
                    speaker,
                    speaker_id,
                    kind,
                    summary,
                    quote,
                    ai_reply,
                    time.time(),
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return row["id"] if row else None

    async def issues(
        self, *, status: str | None = None, group_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Newest first."""
        rows = await self.db.fetchall(
            f"""SELECT {ISSUE_COLUMNS} FROM issues
                WHERE (? IS NULL OR status = ?) AND (? IS NULL OR group_id = ?)
                ORDER BY said_at DESC, id DESC LIMIT ?""",
            (status, status, group_id, group_id, limit),
        )
        return [dict(row) for row in rows]

    async def since(self, group_id: str, said_after: float) -> list[dict[str, Any]]:
        """A group's issues said after a time, oldest first: what the 记录官 already wrote down."""
        rows = await self.db.fetchall(
            f"SELECT {ISSUE_COLUMNS} FROM issues WHERE group_id = ? AND said_at > ? ORDER BY said_at",
            (group_id, said_after),
        )
        return [dict(row) for row in rows]

    async def unreported(self) -> list[dict[str, Any]]:
        """Open issues no digest has carried yet, oldest first."""
        rows = await self.db.fetchall(
            f"""SELECT {ISSUE_COLUMNS} FROM issues WHERE reported_at IS NULL AND status = 'open'
                ORDER BY said_at, id"""
        )
        return [dict(row) for row in rows]

    async def mark_reported(self, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        async with self.db.transaction() as conn:
            await conn.execute(
                f"UPDATE issues SET reported_at = ? WHERE id IN ({marks})", (time.time(), *ids)
            )

    async def set_status(self, issue_id: int, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"未知状态：{status}")
        async with self.db.transaction() as conn:
            cursor = await conn.execute("UPDATE issues SET status = ? WHERE id = ?", (status, issue_id))
            changed = cursor.rowcount > 0
            await cursor.close()
        return changed

    async def delete(self, issue_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    async def read_to(self, channel_id: int) -> tuple[int, float] | None:
        """(last message id looked at, when) for a 记录官's channel, or None before its first look."""
        row = await self.db.fetchone(
            "SELECT last_message_id, reviewed_at FROM issue_reviews WHERE channel_id = ?", (channel_id,)
        )
        return (row["last_message_id"], row["reviewed_at"]) if row else None

    async def set_read_to(self, channel_id: int, message_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO issue_reviews (channel_id, last_message_id, reviewed_at) VALUES (?, ?, ?)
                   ON CONFLICT (channel_id) DO UPDATE SET
                       last_message_id = excluded.last_message_id, reviewed_at = excluded.reviewed_at""",
                (channel_id, message_id, time.time()),
            )
