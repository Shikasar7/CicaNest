"""Channels and messages: the single writer for everything adapters receive."""

from __future__ import annotations

import json
import time
from typing import Any

from nest.core.events import ChannelInfo, ChannelRef, MessageEvent, RecallEvent
from nest.storage.accounts import account_id
from nest.storage.db import Database

MESSAGE_COLUMNS = (
    "id, channel_id, external_id, sender_id, sender_name, from_self, text, segments, mentions, "
    "reply_to, sent_at, received_at, source, recalled_at"
)


def message_dict(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    data = dict(row)
    data["from_self"] = bool(data["from_self"])
    data["segments"] = json.loads(data["segments"])
    data["mentions"] = json.loads(data["mentions"])
    return data


class MessageStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._channel_ids: dict[str, int] = {}

    async def channel_id(self, ref: ChannelRef) -> int:
        cached = self._channel_ids.get(ref.key)
        if cached is not None:
            return cached
        owner = account_id(ref.platform, ref.account)
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO channels (account_id, kind, external_id) VALUES (?, ?, ?)",
                (owner, ref.kind, ref.id),
            )
        row = await self.db.fetchone(
            "SELECT id FROM channels WHERE account_id = ? AND kind = ? AND external_id = ?",
            (owner, ref.kind, ref.id),
        )
        assert row is not None
        self._channel_ids[ref.key] = row["id"]
        return row["id"]

    async def save(self, event: MessageEvent) -> dict[str, Any] | None:
        """Store a message; returns the stored row, or None if already known (e.g. backfill overlap)."""
        channel = await self.channel_id(event.channel)
        segments = json.dumps(
            [{"type": s.type, "data": dict(s.data)} for s in event.segments], ensure_ascii=False
        )
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"""INSERT OR IGNORE INTO messages (channel_id, external_id, sender_id, sender_name,
                        from_self, text, segments, mentions, reply_to, sent_at, received_at, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING {MESSAGE_COLUMNS}""",
                (
                    channel,
                    event.message_id,
                    event.sender_id,
                    event.sender_name,
                    int(event.from_self),
                    event.text,
                    segments,
                    json.dumps(sorted(event.mentions)),
                    event.reply_to,
                    event.timestamp,
                    time.time(),
                    "backfill" if event.backfilled else "live",
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            await conn.execute(
                "UPDATE channels SET last_message_at = max(coalesce(last_message_at, 0), ?) WHERE id = ?",
                (event.timestamp, channel),
            )
        return message_dict(row)

    async def recall(self, event: RecallEvent) -> dict[str, Any] | None:
        channel = await self.channel_id(event.channel)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"""UPDATE messages SET recalled_at = ? WHERE channel_id = ? AND external_id = ?
                    AND recalled_at IS NULL RETURNING {MESSAGE_COLUMNS}""",
                (event.timestamp, channel, event.message_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return message_dict(row) if row else None

    async def name_channels(self, infos: list[ChannelInfo]) -> None:
        for info in infos:
            channel = await self.channel_id(info.channel)
            async with self.db.transaction() as conn:
                await conn.execute("UPDATE channels SET name = ? WHERE id = ?", (info.name, channel))

    async def channels(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """SELECT c.id, c.account_id, c.kind, c.external_id, c.name, c.last_message_at,
                      (SELECT count(*) FROM messages m WHERE m.channel_id = c.id) AS message_count
               FROM channels c ORDER BY c.last_message_at IS NULL, c.last_message_at DESC, c.id"""
        )
        return [dict(row) for row in rows]

    async def traffic(self, since: float) -> list[dict[str, Any]]:
        """Who got what since then, every account's chats together (nest.core.deaf); no message content."""
        rows = await self.db.fetchall(
            """SELECT c.account_id, c.kind, c.external_id, c.name,
                      m.sender_id, m.from_self, m.source, m.sent_at
               FROM channels c JOIN messages m ON m.channel_id = c.id
               WHERE m.sent_at >= ?""",
            (since,),
        )
        return [dict(row) for row in rows]

    async def recent(
        self, channel: int, *, limit: int = 50, before: int | None = None
    ) -> list[dict[str, Any]]:
        """Newest `limit` messages of a channel (optionally older than message id `before`), oldest first."""
        rows = await self.db.fetchall(
            f"""SELECT {MESSAGE_COLUMNS} FROM messages
                WHERE channel_id = ? AND (? IS NULL OR id < ?)
                ORDER BY sent_at DESC, id DESC LIMIT ?""",
            (channel, before, before, limit),
        )
        return [message_dict(row) for row in reversed(rows)]

    async def after(self, channel: int, message_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        """Messages stored after message id `message_id` in a channel, oldest first."""
        rows = await self.db.fetchall(
            f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE channel_id = ? AND id > ? ORDER BY id LIMIT ?",
            (channel, message_id, limit),
        )
        return [message_dict(row) for row in rows]

    async def memory(self, channel: int) -> tuple[str, int]:
        """(summary of a private chat's older talk, the last message id in it)."""
        row = await self.db.fetchone(
            "SELECT summary, upto_id FROM channel_memories WHERE channel_id = ?", (channel,)
        )
        return (row["summary"], row["upto_id"]) if row else ("", 0)

    async def remember(self, channel: int, summary: str, upto_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO channel_memories (channel_id, summary, upto_id, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT (channel_id) DO UPDATE SET summary = excluded.summary,
                       upto_id = excluded.upto_id, updated_at = excluded.updated_at""",
                (channel, summary, upto_id, time.time()),
            )

    async def search(
        self, channel: int, *, words: list[str], links: bool, before: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        """答题's look through the chat log: the newest `limit` messages of a channel older than message id
        `before` that contain one of `words` (in the text or in a shared card) or, with `links`, carry a
        link or a card; oldest first. Recalled messages are left out."""
        found: dict[int, dict[str, Any]] = {}
        likes = [f"%{_escape_like(word)}%" for word in words if word.strip()]
        if likes:
            matches = " OR ".join("text LIKE ? ESCAPE '\\' OR segments LIKE ? ESCAPE '\\'" for _ in likes)
            rows = await self.db.fetchall(
                f"""SELECT {MESSAGE_COLUMNS} FROM messages
                    WHERE channel_id = ? AND id < ? AND recalled_at IS NULL AND ({matches})
                    ORDER BY sent_at DESC, id DESC LIMIT ?""",
                (channel, before, *[like for like in likes for _ in (0, 1)], limit),
            )
            found.update((row["id"], message_dict(row)) for row in rows)
        if links:
            rows = await self.db.fetchall(
                f"""SELECT {MESSAGE_COLUMNS} FROM messages
                    WHERE channel_id = ? AND id < ? AND recalled_at IS NULL
                      AND (text LIKE '%http%' OR segments LIKE '%"type": "json"%'
                           OR segments LIKE '%"type": "share"%')
                    ORDER BY sent_at DESC, id DESC LIMIT ?""",
                (channel, before, limit),
            )
            found.update((row["id"], message_dict(row)) for row in rows)
        newest = sorted(found.values(), key=lambda row: (row["sent_at"], row["id"]))[-limit:]
        return newest

    async def message(self, message_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE id = ?", (message_id,))
        return message_dict(row) if row else None

    async def by_external_id(self, channel: int, external_id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE channel_id = ? AND external_id = ?",
            (channel, external_id),
        )
        return message_dict(row) if row else None

    async def from_members(
        self, channel: int, *, since: float, exclude: set[str], limit: int
    ) -> list[dict[str, Any]]:
        """The newest `limit` messages people (not our accounts) sent in a channel after `since`, oldest
        first."""
        marks = ",".join("?" * len(exclude)) or "SELECT NULL WHERE 0"  # NOT IN (NULL) matches nothing
        rows = await self.db.fetchall(
            f"""SELECT {MESSAGE_COLUMNS} FROM messages
                WHERE channel_id = ? AND sent_at > ? AND from_self = 0 AND sender_id NOT IN ({marks})
                ORDER BY sent_at DESC, id DESC LIMIT ?""",
            (channel, since, *sorted(exclude), limit),
        )
        return [message_dict(row) for row in reversed(rows)]

    async def count_from_members(self, channel: int, *, since: float, exclude: set[str]) -> int:
        marks = ",".join("?" * len(exclude)) or "SELECT NULL WHERE 0"  # NOT IN (NULL) matches nothing
        row = await self.db.fetchone(
            f"""SELECT count(*) FROM messages
                WHERE channel_id = ? AND sent_at > ? AND from_self = 0 AND sender_id NOT IN ({marks})""",
            (channel, since, *sorted(exclude)),
        )
        return int(row[0]) if row else 0

    async def mentions_in_group(
        self, platform: str, group_id: str, uin: str, after: float, before: float
    ) -> list[dict[str, Any]]:
        """Messages of one group that @ `uin`, oldest first, as every one of our accounts stored them: one
        that was offline can see here what the others received."""
        columns = ", ".join(f"m.{name.strip()}" for name in MESSAGE_COLUMNS.split(","))
        rows = await self.db.fetchall(
            f"""SELECT {columns} FROM messages m JOIN channels c ON c.id = m.channel_id
                WHERE c.kind = 'group' AND c.external_id = ? AND c.account_id LIKE ?
                  AND m.sent_at > ? AND m.sent_at < ? AND m.recalled_at IS NULL
                  AND EXISTS (SELECT 1 FROM json_each(m.mentions) WHERE value = ?)
                ORDER BY m.sent_at, m.id""",
            (group_id, f"{platform}:%", after, before, uin),
        )
        return [message_dict(row) for row in rows]

    async def backfill_targets(self, platform: str, account: str) -> list[tuple[ChannelRef, float]]:
        """Channels with stored messages, and the newest stored message time of each."""
        rows = await self.db.fetchall(
            """SELECT kind, external_id, last_message_at FROM channels
               WHERE account_id = ? AND last_message_at IS NOT NULL""",
            (account_id(platform, account),),
        )
        return [
            (ChannelRef(platform, account, row["kind"], row["external_id"]), row["last_message_at"])
            for row in rows
        ]

    async def purge(self, older_than: float) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM messages WHERE received_at < ?", (older_than,))
            deleted = cursor.rowcount
            await cursor.close()
        return deleted


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
