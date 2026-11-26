"""小游戏's table (from 042, state from 043): the games the user played with each companion."""

from __future__ import annotations

import json
import time
from typing import Any

from nest.storage.db import Database

COLUMNS = (
    "id, bot_id, channel_id, kind, her_color, level, moves, status, win, undos, state, created_at, "
    "updated_at, finished_at"
)


def _row(row: Any) -> dict[str, Any]:
    found = dict(row)
    found["moves"] = [tuple(m) for m in json.loads(found["moves"])]
    found["win"] = [tuple(m) for m in json.loads(found["win"])]
    found["state"] = json.loads(found["state"] or "{}")
    return found


class GameStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, game_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM games WHERE id = ?", (game_id,))
        return _row(row) if row else None

    async def current(self, bot_id: str) -> dict[str, Any] | None:
        """Her newest game, playing or not."""
        row = await self.db.fetchone(
            f"SELECT {COLUMNS} FROM games WHERE bot_id = ? ORDER BY id DESC LIMIT 1", (bot_id,)
        )
        return _row(row) if row else None

    async def last_of(self, bot_id: str, kind: str) -> dict[str, Any] | None:
        """Her newest game of this kind."""
        row = await self.db.fetchone(
            f"SELECT {COLUMNS} FROM games WHERE bot_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
            (bot_id, kind),
        )
        return _row(row) if row else None

    async def add(
        self,
        *,
        bot_id: str,
        channel_id: int,
        kind: str,
        her_color: int,
        level: str,
        state: dict[str, Any] | None = None,
    ) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE games SET status = 'left', finished_at = ?, updated_at = ? "
                "WHERE bot_id = ? AND status = 'playing'",
                (now, now, bot_id),
            )
            cursor = await conn.execute(
                """INSERT INTO games
                       (bot_id, channel_id, kind, her_color, level, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    bot_id,
                    channel_id,
                    kind,
                    her_color,
                    level,
                    json.dumps(state or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return int(row[0])

    async def save(
        self,
        game_id: int,
        moves: list[tuple[int, int]],
        *,
        status: str = "playing",
        win: list[tuple[int, int]] | None = None,
        undos: int | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        columns: dict[str, Any] = {"moves": json.dumps(moves), "status": status, "updated_at": now}
        if win is not None:
            columns["win"] = json.dumps(win)
        if undos is not None:
            columns["undos"] = undos
        if state is not None:
            columns["state"] = json.dumps(state, ensure_ascii=False)
        if status != "playing":
            columns["finished_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            await conn.execute(f"UPDATE games SET {assignments} WHERE id = ?", (*columns.values(), game_id))

    async def record(self, bot_id: str, kind: str) -> dict[str, int]:
        """Her finished games: {"won": …, "lost": …, "draw": …} (given up counts as lost)."""
        rows = await self.db.fetchall(
            "SELECT status, COUNT(*) AS n FROM games WHERE bot_id = ? AND kind = ? GROUP BY status",
            (bot_id, kind),
        )
        counts = {r["status"]: int(r["n"]) for r in rows}
        return {
            "won": counts.get("won", 0),
            "lost": counts.get("lost", 0) + counts.get("resigned", 0),
            "draw": counts.get("draw", 0),
        }
