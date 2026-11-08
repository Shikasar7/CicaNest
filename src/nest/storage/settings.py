"""Machine settings edited from the console, one JSON value per key (table `settings`, from 001_init).

Only what the user saved is stored: defaults are worked out by the caller when reading, so a default
follows what exists on disk until the user picks something else.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from nest.storage.db import Database

log = logging.getLogger(__name__)


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def all(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for row in await self.db.fetchall("SELECT key, value FROM settings"):
            try:
                values[row["key"]] = json.loads(row["value"])
            except ValueError:  # edited by hand: treat as not set
                log.warning("设置 %s 的值不是 JSON，已忽略", row["key"])
        return values

    async def fetch_proxy(self) -> str:
        """The proxy 监控 may fetch through (http(s)://host:port, never with credentials); "" = none."""
        row = await self.db.fetchone("SELECT value FROM settings WHERE key = 'fetch_proxy'")
        try:
            value = json.loads(row["value"]) if row else ""
        except ValueError:
            return ""
        return value if isinstance(value, str) else ""

    async def set_many(self, values: dict[str, Any]) -> None:
        now = time.time()
        async with self.db.transaction() as conn:
            for key, value in values.items():
                await conn.execute(
                    """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT (key) DO UPDATE SET
                           value = excluded.value, updated_at = excluded.updated_at""",
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
