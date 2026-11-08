"""监控 rows (watches) and what each check found (watch_runs)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nest.storage.db import Database

log = logging.getLogger(__name__)

KINDS = ("feed", "page", "price")
TARGET_KINDS = ("group", "private")
MODES = ("preview", "push")
OUTCOMES = ("baseline", "unchanged", "changed", "error")
DELIVERIES = ("sent", "preview", "paused", "failed", "unknown")
PUSHED = ("sent", "unknown")  # deliveries that went out, or may have: they count toward the hourly limit

COLUMNS = (
    "id, name, kind, url, selector, threshold, interval_minutes, bot_id, target_kind, target_id, "
    "provider_id, instructions, use_proxy, mode, enabled, state, last_run_at, next_run_at, created_at, "
    "updated_at"
)
RUN_COLUMNS = "id, watch_id, started_at, duration_ms, outcome, changes, detail, message, delivered, error"
EDITABLE = (
    "name",
    "kind",
    "url",
    "selector",
    "threshold",
    "interval_minutes",
    "bot_id",
    "target_kind",
    "target_id",
    "provider_id",
    "instructions",
    "use_proxy",
    "mode",
    "enabled",
    "state",
    "next_run_at",
)


@dataclass(frozen=True, slots=True)
class Watch:
    id: int
    name: str
    kind: str
    url: str
    selector: str
    threshold: float | None
    interval_minutes: int
    bot_id: str
    target_kind: str
    target_id: str
    provider_id: str | None
    instructions: str
    use_proxy: bool
    mode: str
    enabled: bool
    state: dict[str, Any] = field(default_factory=dict)
    last_run_at: float | None = None
    next_run_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def source(self) -> tuple[str, str, str]:
        """What a check reads: changing any of it makes the next check a new baseline."""
        return (self.kind, self.url, self.selector)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """What one check wrote: its watch_runs row, and the watch's new state (None keeps the stored one)."""

    outcome: str
    detail: str
    changes: int = 0
    message: str | None = None
    delivered: str | None = None
    error: str | None = None
    state: dict[str, Any] | None = None


def _state(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _watch(row) -> Watch:  # type: ignore[no-untyped-def]
    data = dict(row)
    data["use_proxy"] = bool(data["use_proxy"])
    data["enabled"] = bool(data["enabled"])
    data["state"] = _state(data["state"])
    return Watch(**data)


def run_view(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A watch_runs row as the API shows it (RunView)."""
    data = dict(row)
    data.pop("watch_id", None)
    return data


def _encode(column: str, value: Any) -> Any:
    if column == "state":
        return json.dumps(value, ensure_ascii=False)
    if column in ("use_proxy", "enabled"):
        return int(value)
    return value


class WatchStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- watches -----------------------------------------------------------------------------------

    async def all(self) -> list[Watch]:
        rows = await self.db.fetchall(f"SELECT {COLUMNS} FROM watches ORDER BY name, id")
        return [_watch(row) for row in rows]

    async def get(self, watch_id: int) -> Watch | None:
        row = await self.db.fetchone(f"SELECT {COLUMNS} FROM watches WHERE id = ?", (watch_id,))
        return _watch(row) if row else None

    async def create(self, values: dict[str, Any]) -> int:
        now = time.time()
        columns = {key: _encode(key, value) for key, value in values.items() if key in EDITABLE}
        columns |= {"created_at": now, "updated_at": now}
        names = ", ".join(columns)
        marks = ", ".join("?" * len(columns))
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"INSERT INTO watches ({names}) VALUES ({marks})", tuple(columns.values())
            )
            watch_id = cursor.lastrowid
            await cursor.close()
        assert watch_id is not None
        return watch_id

    async def update(self, watch_id: int, values: dict[str, Any]) -> bool:
        """Change only the given columns. False when there is no such watch."""
        columns = {key: _encode(key, value) for key, value in values.items() if key in EDITABLE}
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE watches SET {assignments} WHERE id = ?", (*columns.values(), watch_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete(self, watch_id: int) -> bool:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    # -- the schedule ------------------------------------------------------------------------------

    async def due(self, now: float) -> list[Watch]:
        """Enabled watches whose next check is due (never scheduled counts as due), earliest first."""
        rows = await self.db.fetchall(
            f"""SELECT {COLUMNS} FROM watches WHERE enabled = 1 AND coalesce(next_run_at, 0) <= ?
                ORDER BY coalesce(next_run_at, 0), id""",
            (now,),
        )
        return [_watch(row) for row in rows]

    async def next_due(self, now: float) -> float | None:
        """When the earliest enabled watch that is not due yet will be."""
        row = await self.db.fetchone(
            "SELECT min(next_run_at) FROM watches WHERE enabled = 1 AND next_run_at > ?", (now,)
        )
        return row[0] if row and row[0] is not None else None

    async def schedule(self, watch_id: int, next_run_at: float) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE watches SET next_run_at = ? WHERE id = ?", (next_run_at, watch_id))

    async def started(self, watch_id: int, at: float, next_run_at: float) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE watches SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                (at, next_run_at, watch_id),
            )

    # -- runs ----------------------------------------------------------------------------------------

    async def finish(
        self,
        watch_id: int,
        record: Callable[[Watch], RunRecord],
        *,
        started_at: float,
        duration_ms: int,
    ) -> tuple[Watch, dict[str, Any]] | None:
        """Write a check's result in one transaction. `record` gets the watch as it is now (it may have been
        edited during the check) and must not wait on anything. None when the watch was deleted."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(f"SELECT {COLUMNS} FROM watches WHERE id = ?", (watch_id,))
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            current = _watch(row)
            result = record(current)
            if result.state is not None:
                await conn.execute(
                    "UPDATE watches SET state = ? WHERE id = ?",
                    (json.dumps(result.state, ensure_ascii=False), watch_id),
                )
            cursor = await conn.execute(
                f"""INSERT INTO watch_runs (watch_id, started_at, duration_ms, outcome, changes, detail,
                        message, delivered, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING {RUN_COLUMNS}""",
                (
                    watch_id,
                    started_at,
                    duration_ms,
                    result.outcome,
                    result.changes,
                    result.detail,
                    result.message,
                    result.delivered,
                    result.error,
                ),
            )
            run = await cursor.fetchone()
            await cursor.close()
        assert run is not None
        return current, run_view(run)

    async def set_delivery(
        self, run_id: int, *, delivered: str | None, detail: str, error: str | None, duration_ms: int
    ) -> dict[str, Any] | None:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"""UPDATE watch_runs SET delivered = ?, detail = ?, error = ?, duration_ms = ? WHERE id = ?
                    RETURNING {RUN_COLUMNS}""",
                (delivered, detail, error, duration_ms, run_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return run_view(row) if row else None

    async def undelivered(self, since: float) -> list[dict[str, Any]]:
        """Runs whose push failed, oldest first."""
        rows = await self.db.fetchall(
            f"""SELECT {RUN_COLUMNS} FROM watch_runs
                WHERE delivered = 'failed' AND message IS NOT NULL AND started_at >= ?
                ORDER BY started_at, id""",
            (since,),
        )
        return [{**run_view(row), "watch_id": row["watch_id"]} for row in rows]

    async def runs(self, watch_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first."""
        rows = await self.db.fetchall(
            f"""SELECT {RUN_COLUMNS} FROM watch_runs WHERE watch_id = ?
                ORDER BY started_at DESC, id DESC LIMIT ?""",
            (watch_id, limit),
        )
        return [run_view(row) for row in rows]

    async def latest_runs(self, *, with_message: bool = False) -> dict[int, dict[str, Any]]:
        """Watch id -> its newest run (or its newest run that has a message)."""
        where = "WHERE message IS NOT NULL" if with_message else ""
        rows = await self.db.fetchall(
            f"""SELECT {RUN_COLUMNS} FROM watch_runs
                WHERE id IN (SELECT max(id) FROM watch_runs {where} GROUP BY watch_id)"""
        )
        return {row["watch_id"]: run_view(row) for row in rows}

    async def counts(self, since: float) -> dict[int, dict[str, int]]:
        rows = await self.db.fetchall(
            """SELECT watch_id, count(*) AS runs, sum(outcome = 'changed') AS changes,
                      sum(outcome = 'error') AS errors
               FROM watch_runs WHERE started_at >= ? GROUP BY watch_id""",
            (since,),
        )
        return {
            row["watch_id"]: {
                "runs_7d": row["runs"],
                "changes_7d": row["changes"] or 0,
                "errors_7d": row["errors"] or 0,
            }
            for row in rows
        }

    async def pushes_since(self, since: float) -> list[float]:
        marks = ",".join("?" * len(PUSHED))
        rows = await self.db.fetchall(
            f"SELECT started_at FROM watch_runs WHERE delivered IN ({marks}) AND started_at >= ?",
            (*PUSHED, since),
        )
        return [row[0] for row in rows]

    async def purge_runs(self, before: float) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM watch_runs WHERE started_at < ?", (before,))
            deleted = cursor.rowcount
            await cursor.close()
        return deleted
