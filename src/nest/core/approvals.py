"""确认: a step that needs the user's nod waits for it.

ask() records what she wants to do — the plugin, the tool and everything it was given, exactly as it will
be run — tells every console and desktop window (event "approval"), and waits until the user answers there
or the time runs out. The answers are those of the design: allow once, allow the same tool again for the
rest of this answer or task ("allowed_kind"), or no. There is no "always". Only the console's API can
answer (a signed-in device); nothing she or anyone says in a chat counts.

When nest restarts, what was waiting is marked expired: the step was not taken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from nest.storage.db import Database

log = logging.getLogger(__name__)

COLUMNS = (
    "id, bot_id, channel_id, task_id, plugin_id, plugin_name, tool, given, status, decided_by, created_at, "
    "expires_at, decided_at"
)
CHOICES = {"once": "allowed", "kind": "allowed_kind", "deny": "denied"}
GIVEN_MAX = 20_000
KEEP_DAYS = 90


class ApprovalKeeper:
    def __init__(self, db: Database, broadcaster: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.broadcaster = broadcaster
        self._clock = clock
        self._waiting: dict[int, asyncio.Future[str]] = {}

    async def start(self) -> None:
        now = self._clock()
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE approvals SET status = 'expired', decided_at = ? WHERE status = 'pending'", (now,)
            )
            await conn.execute("DELETE FROM approvals WHERE created_at < ?", (now - KEEP_DAYS * 86400,))

    async def ask(
        self,
        *,
        bot_id: str,
        channel_id: int | None,
        task_id: int | None,
        plugin_id: str,
        plugin_name: str,
        tool: str,
        given: dict[str, Any],
        wait: float,
    ) -> str:
        """Wait for the user's answer: 'allowed', 'allowed_kind', 'denied' or 'expired'."""
        now = self._clock()
        shown = json.dumps(given, ensure_ascii=False, indent=2)[:GIVEN_MAX]
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO approvals (bot_id, channel_id, task_id, plugin_id, plugin_name, tool, given,
                       created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (bot_id, channel_id, task_id, plugin_id, plugin_name, tool, shown, now, now + wait),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        approval_id = int(row[0])
        answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiting[approval_id] = answer
        self.broadcaster.publish("approval", {"id": approval_id, "bot_id": bot_id, "status": "pending"})
        log.info("%s 要用 %s 的 %s，等你点头（确认 %s）", bot_id, plugin_name, tool, approval_id)
        try:
            return await asyncio.wait_for(asyncio.shield(answer), wait)
        except TimeoutError:
            await self._settle(approval_id, "expired", "")
            return "expired"
        except asyncio.CancelledError:
            await asyncio.shield(self._settle(approval_id, "expired", ""))
            raise
        finally:
            self._waiting.pop(approval_id, None)

    async def decide(self, approval_id: int, choice: str, by: str) -> bool:
        """The user's answer (once / kind / deny); False when it is not waiting any more."""
        status = CHOICES.get(choice)
        if status is None:
            raise ValueError(choice)
        if not await self._settle(approval_id, status, by):
            return False
        waiting = self._waiting.get(approval_id)
        if waiting is not None and not waiting.done():
            waiting.set_result(status)
        log.info("确认 %s：%s（%s）", approval_id, status, by)
        return True

    async def _settle(self, approval_id: int, status: str, by: str) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (status, by[:200], self._clock(), approval_id),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
            cursor = await conn.execute("SELECT bot_id FROM approvals WHERE id = ?", (approval_id,))
            row = await cursor.fetchone()
            await cursor.close()
        if changed and row is not None:
            self.broadcaster.publish("approval", {"id": approval_id, "bot_id": row[0], "status": status})
        return changed

    async def record(
        self,
        *,
        bot_id: str,
        channel_id: int | None,
        task_id: int | None,
        name: str,
        tool: str,
        given: dict[str, Any],
        status: str,
        by: str,
    ) -> None:
        """A nod given (or refused) elsewhere: on the Windows PC, in its own window."""
        now = self._clock()
        shown = json.dumps(given, ensure_ascii=False, indent=2)[:GIVEN_MAX]
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO approvals (bot_id, channel_id, task_id, plugin_id, plugin_name, tool, given,
                       status, decided_by, created_at, expires_at, decided_at)
                   VALUES (?, ?, ?, 'nest.windows', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bot_id, channel_id, task_id, name, tool, shown, status, by[:200], now, now, now),
            )

    async def pending(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(f"SELECT {COLUMNS} FROM approvals WHERE status = 'pending' ORDER BY id")
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(f"SELECT {COLUMNS} FROM approvals ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
