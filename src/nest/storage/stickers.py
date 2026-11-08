"""表情包 rows (table `stickers`, from 007): what was collected, how it was described, how often it was sent.

The files themselves are kept by nest.core.stickers; this module only knows the rows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from nest.storage.db import Database

STATUSES = ("pending", "ready", "unsuitable", "failed")
SENT_STATES = ("sent", "trial", "unknown")  # what counts as the bot having (or as good as) sent a sticker


@dataclass(frozen=True, slots=True)
class NewSticker:
    sha256: str
    source_key: str
    kind: str
    content_type: str
    bytes: int
    width: int
    height: int
    animated: bool
    group_key: str
    seen_at: float
    look: str = ""
    kept: bool = False
    bot_id: str | None = None  # only this bot sends it; None: any bot


def sticker_dict(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    data = dict(row)
    del data["look"]  # only ever compared in the keeper (looks())
    data["tags"] = json.loads(data["tags"])
    data["animated"] = bool(data["animated"])
    data["edited"] = bool(data["edited"])
    data["kept"] = bool(data["kept"])
    return data


class StickerStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def by_source(self, source_key: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM stickers WHERE source_key = ? LIMIT 1", (source_key,))
        return sticker_dict(row) if row else None

    async def by_sha(self, sha256: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM stickers WHERE sha256 = ?", (sha256,))
        return sticker_dict(row) if row else None

    async def get(self, sticker_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM stickers WHERE id = ?", (sticker_id,))
        return sticker_dict(row) if row else None

    async def many(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = await self.db.fetchall(f"SELECT * FROM stickers WHERE id IN ({marks})", ids)
        return {row["id"]: sticker_dict(row) for row in rows}

    async def labels(self, source_keys: list[str]) -> dict[str, str]:
        """source key -> a short label for the prompt, for the described ones among `source_keys`."""
        if not source_keys:
            return {}
        marks = ",".join("?" * len(source_keys))
        rows = await self.db.fetchall(
            f"""SELECT source_key, description, caption FROM stickers
                WHERE source_key IN ({marks}) AND status IN ('ready', 'unsuitable')""",
            source_keys,
        )
        return {row["source_key"]: label(row["description"], row["caption"]) for row in rows}

    async def seen(self, sticker_id: int, at: float) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE stickers SET seen_count = seen_count + 1, last_seen_at = max(last_seen_at, ?)"
                " WHERE id = ?",
                (at, sticker_id),
            )

    async def add(self, new: NewSticker) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO stickers (sha256, source_key, kind, content_type, bytes, width, height,
                       animated, group_key, first_seen_at, last_seen_at, status_at, kept, bot_id, look)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new.sha256,
                    new.source_key,
                    new.kind,
                    new.content_type,
                    new.bytes,
                    new.width,
                    new.height,
                    int(new.animated),
                    new.group_key,
                    new.seen_at,
                    new.seen_at,
                    time.time(),
                    int(new.kept),
                    new.bot_id,
                    new.look,
                ),
            )
            sticker_id = cursor.lastrowid
            await cursor.close()
        assert sticker_id is not None
        return sticker_id

    async def looks(self) -> dict[int, str]:
        rows = await self.db.fetchall("SELECT id, look FROM stickers WHERE look != ''")
        return {row["id"]: row["look"] for row in rows}

    async def without_look(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM stickers WHERE look = ''")
        return [sticker_dict(row) for row in rows]

    async def set_look(self, sticker_id: int, look: str) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE stickers SET look = ? WHERE id = ?", (look, sticker_id))

    async def totals(self) -> tuple[int, int]:
        """(count, bytes) of every stored sticker."""
        row = await self.db.fetchone("SELECT count(*), coalesce(sum(bytes), 0) FROM stickers")
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def counts(self) -> dict[str, int]:
        rows = await self.db.fetchall("SELECT status, count(*) AS n FROM stickers GROUP BY status")
        return {row["status"]: row["n"] for row in rows}

    async def evictable(self, limit: int) -> list[dict[str, Any]]:
        """Stickers to remove first when over the limits: never kept ones; unsuitable and failed first,
        then the ones never sent, then by when they were last seen or sent."""
        rows = await self.db.fetchall(
            """SELECT * FROM stickers WHERE kept = 0
               ORDER BY status NOT IN ('unsuitable', 'failed'), used_count > 0,
                        max(last_seen_at, coalesce(last_used_at, 0)), id
               LIMIT ?""",
            (limit,),
        )
        return [sticker_dict(row) for row in rows]

    async def expired_unsuitable(self, before: float) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM stickers WHERE status = 'unsuitable' AND kept = 0 AND status_at < ?", (before,)
        )
        return [sticker_dict(row) for row in rows]

    async def all_shas(self) -> set[str]:
        return {row["sha256"] for row in await self.db.fetchall("SELECT sha256 FROM stickers")}

    async def next_pending(self, exclude: set[int]) -> dict[str, Any] | None:
        marks = ",".join("?" * len(exclude)) or "SELECT NULL WHERE 0"  # NOT IN (NULL) matches nothing
        row = await self.db.fetchone(
            f"""SELECT * FROM stickers WHERE status = 'pending' AND id NOT IN ({marks})
                ORDER BY first_seen_at, id LIMIT 1""",
            list(exclude),
        )
        return sticker_dict(row) if row else None

    async def describe(
        self,
        sticker_id: int,
        *,
        status: str,
        description: str = "",
        caption: str = "",
        tags: list[str] | None = None,
        note: str = "",
    ) -> None:
        """The describer's result. A description edited by hand meanwhile is kept, only the status changes."""
        async with self.db.transaction() as conn:
            await conn.execute(
                """UPDATE stickers SET status = ?, status_at = ?, note = ?,
                       description = CASE WHEN edited THEN description ELSE ? END,
                       caption = CASE WHEN edited THEN caption ELSE ? END,
                       tags = CASE WHEN edited THEN tags ELSE ? END
                   WHERE id = ?""",
                (
                    status,
                    time.time(),
                    note,
                    description,
                    caption,
                    json.dumps(tags or [], ensure_ascii=False),
                    sticker_id,
                ),
            )

    async def update(
        self,
        sticker_id: int,
        *,
        description: str | None = None,
        caption: str | None = None,
        tags: list[str] | None = None,
        kept: bool | None = None,
        status: str | None = None,
    ) -> bool:
        """An edit from the console. Changing the words marks the description as edited by hand."""
        columns: dict[str, Any] = {}
        if description is not None:
            columns["description"] = description
        if caption is not None:
            columns["caption"] = caption
        if tags is not None:
            columns["tags"] = json.dumps(tags, ensure_ascii=False)
        if columns:
            columns["edited"] = 1
        if kept is not None:
            columns["kept"] = int(kept)
        if status is not None:
            columns["status"] = status
            columns["status_at"] = time.time()
            columns["note"] = ""
        if not columns:
            return await self.get(sticker_id) is not None
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE stickers SET {assignments} WHERE id = ?", (*columns.values(), sticker_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def set_owner(self, ids: list[int], bot_id: str | None) -> int:
        """Only this bot sends these stickers (None: every bot may). How many there were."""
        marks = ",".join("?" * len(ids))
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE stickers SET bot_id = ? WHERE id IN ({marks})", (bot_id, *ids)
            )
            updated = cursor.rowcount
            await cursor.close()
        return updated

    async def redescribe(self, sticker_id: int) -> bool:
        """Back to the describer's queue, forgetting a hand edit."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE stickers SET status = 'pending', status_at = ?, note = '', edited = 0 WHERE id = ?",
                (time.time(), sticker_id),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def retry_failed(self) -> int:
        """Failed descriptions go back to the queue (e.g. once a vision model is chosen)."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE stickers SET status = 'pending', status_at = ?, note = '' WHERE status = 'failed'",
                (time.time(),),
            )
            count = cursor.rowcount
            await cursor.close()
        return count

    async def delete(self, ids: list[int]) -> list[dict[str, Any]]:
        """Delete rows; returns the deleted ones (their files are the caller's)."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        async with self.db.transaction() as conn:
            cursor = await conn.execute(f"DELETE FROM stickers WHERE id IN ({marks}) RETURNING *", ids)
            rows = await cursor.fetchall()
            await cursor.close()
        return [sticker_dict(row) for row in rows]

    async def used(self, sticker_id: int, at: float) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE stickers SET used_count = used_count + 1, last_used_at = ? WHERE id = ?",
                (at, sticker_id),
            )

    async def ready(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM stickers WHERE status = 'ready' ORDER BY last_seen_at DESC, id DESC"
        )
        return [sticker_dict(row) for row in rows]

    async def page(
        self, *, status: str | None, query: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Newest first, filtered by status and by words in the description, caption or tags."""
        where, params = ["1 = 1"], []
        if status:
            where.append("status = ?")
            params.append(status)
        if query:
            where.append("(description LIKE ? OR caption LIKE ? OR tags LIKE ?)")
            like = f"%{query}%"
            params += [like, like, like]
        clause = " AND ".join(where)
        rows = await self.db.fetchall(
            f"""SELECT * FROM stickers WHERE {clause}
                ORDER BY last_seen_at DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )
        total = await self.db.fetchone(f"SELECT count(*) FROM stickers WHERE {clause}", params)
        return [sticker_dict(row) for row in rows], int(total[0]) if total else 0

    # -- what the bots sent (decisions.sticker_id / sticker_state) ----------------------------------

    async def sent_in_group(
        self, kind: str, external_id: str, since: float, bot_id: str | None = None
    ) -> int:
        """Stickers our bots sent (or would have, in trial) in one group since `since`."""
        marks = ",".join("?" * len(SENT_STATES))
        row = await self.db.fetchone(
            f"""SELECT count(*) FROM decisions d JOIN channels c ON c.id = d.channel_id
                WHERE c.kind = ? AND c.external_id = ? AND d.sticker_state IN ({marks})
                  AND d.created_at >= ? AND (? IS NULL OR d.bot_id = ?)""",
            (kind, external_id, *SENT_STATES, since, bot_id, bot_id),
        )
        return int(row[0]) if row else 0

    async def recent_in_group(self, kind: str, external_id: str, since: float) -> set[int]:
        marks = ",".join("?" * len(SENT_STATES))
        rows = await self.db.fetchall(
            f"""SELECT DISTINCT d.sticker_id FROM decisions d JOIN channels c ON c.id = d.channel_id
                WHERE c.kind = ? AND c.external_id = ? AND d.sticker_state IN ({marks})
                  AND d.sticker_id IS NOT NULL AND d.created_at >= ?""",
            (kind, external_id, *SENT_STATES, since),
        )
        return {row[0] for row in rows}


def label(description: str, caption: str) -> str:
    """How a sticker reads in a prompt: its description, with its words when the description lacks them."""
    description, caption = description.strip(), caption.strip()
    if caption and caption not in description:
        return f"{description}，配字「{caption}」" if description else f"配字「{caption}」"
    return description or "表情"
