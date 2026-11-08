"""Bot pictures for the console (bot_avatars): one per source, at most one of them chosen per bot."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from nest.storage.db import Database

SOURCES = ("qq", "custom")
MAX_BYTES = 2 * 1024 * 1024
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def image_type(data: bytes) -> str | None:
    """The picture's type from its first bytes (never from what the sender claims), or None when it is
    not a PNG, JPEG, WebP or GIF. SVG is refused on purpose: it can carry script."""
    for signature, content_type in SIGNATURES:
        if data.startswith(signature):
            return content_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True, slots=True)
class Avatar:
    content_type: str
    data: bytes
    updated_at: float


class AvatarStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def views(self) -> dict[str, dict[str, Any]]:
        """bot id -> {"chosen": source or None, "qq": updated_at or None, "custom": ...}, for bots with
        at least one picture."""
        views: dict[str, dict[str, Any]] = {}
        for row in await self.db.fetchall("SELECT bot_id, source, chosen, updated_at FROM bot_avatars"):
            view = views.setdefault(row["bot_id"], empty_view())
            view[row["source"]] = row["updated_at"]
            if row["chosen"]:
                view["chosen"] = row["source"]
        return views

    async def get(self, bot_id: str, source: str) -> Avatar | None:
        row = await self.db.fetchone(
            "SELECT content_type, data, updated_at FROM bot_avatars WHERE bot_id = ? AND source = ?",
            (bot_id, source),
        )
        return Avatar(row["content_type"], bytes(row["data"]), row["updated_at"]) if row else None

    async def put(self, bot_id: str, source: str, content_type: str, data: bytes) -> None:
        """Store the bot's picture from `source` (replacing the previous one) and show it."""
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE bot_avatars SET chosen = 0 WHERE bot_id = ?", (bot_id,))
            await conn.execute(
                """INSERT INTO bot_avatars (bot_id, source, content_type, data, chosen, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT (bot_id, source) DO UPDATE SET content_type = excluded.content_type,
                       data = excluded.data, chosen = 1, updated_at = excluded.updated_at""",
                (bot_id, source, content_type, data, time.time()),
            )

    async def choose(self, bot_id: str, source: str | None) -> bool:
        """Show the picture from `source`, or the brush character (None). False: no such picture."""
        async with self.db.transaction() as conn:
            if source is not None:
                async with conn.execute(
                    "SELECT 1 FROM bot_avatars WHERE bot_id = ? AND source = ?", (bot_id, source)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        return False
            await conn.execute(
                "UPDATE bot_avatars SET chosen = (source IS ?) WHERE bot_id = ?", (source, bot_id)
            )
        return True

    async def delete(self, bot_id: str, source: str) -> None:
        """Forget that picture; when it was the one shown, the brush character is shown again."""
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM bot_avatars WHERE bot_id = ? AND source = ?", (bot_id, source))


def empty_view() -> dict[str, Any]:
    return {"chosen": None} | dict.fromkeys(SOURCES)
