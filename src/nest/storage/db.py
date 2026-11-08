"""SQLite access: one connection, serialized writes, numbered migrations.

Schema changes go in `migrations/NNN_name.sql` (contiguous numbers, applied in order, tracked with
PRAGMA user_version). Never edit a migration after it has run on real data; add a new one instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sqlite3
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
# VACUUM and emptying the WAL wait for reads other tasks have started on the shared connection: tried this
# often, with growing pauses (about 1.5 s in all), then left for the next cleanup.
IDLE_TRIES = 5
IDLE_PAUSE = 0.05


class SchemaTooNew(RuntimeError):
    pass


def load_migrations() -> list[tuple[int, str, str]]:
    """Return (number, filename, sql) sorted by number; numbers must be 1..n without gaps."""
    found = []
    for entry in resources.files("nest.storage").joinpath("migrations").iterdir():
        match = MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    numbers = [number for number, _, _ in found]
    if numbers != list(range(1, len(found) + 1)):
        raise RuntimeError(f"迁移编号必须从 001 连续递增，实际为 {numbers}")
    return found


class Database:
    def __init__(self, conn: aiosqlite.Connection, path: Path) -> None:
        self._conn = conn
        self._write_lock = asyncio.Lock()
        self.path = path

    @classmethod
    async def open(cls, path: Path) -> Database:
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: no implicit transactions; writes use transaction() explicitly.
        conn = await aiosqlite.connect(path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA busy_timeout=5000")
            db = cls(conn, path)
            await db.migrate()
        except BaseException:
            await conn.close()
            raise
        return db

    async def close(self) -> None:
        await self._conn.close()

    async def schema_version(self) -> int:
        row = await self.fetchone("PRAGMA user_version")
        return int(row[0]) if row else 0

    async def migrate(self) -> int:
        migrations = load_migrations()
        current = await self.schema_version()
        if current > len(migrations):
            raise SchemaTooNew(f"数据库版本 {current} 比程序支持的 {len(migrations)} 新，请更新程序")
        async with self._write_lock:
            for number, name, sql in migrations[current:]:
                try:
                    await self._conn.executescript(
                        f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;"
                    )
                except Exception as exc:
                    if self._conn.in_transaction:
                        await self._conn.execute("ROLLBACK")
                    raise RuntimeError(f"迁移 {name} 失败：{exc}") from exc
        return len(migrations)

    def size(self) -> int:
        """Bytes on disk: the database file and its WAL."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                total += os.path.getsize(f"{self.path}{suffix}")
        return total

    async def compact(self, min_free: int) -> int:
        """Give unused pages back to the disk (VACUUM) once at least `min_free` bytes are free, and empty
        the WAL. Returns how many bytes the files shrank by."""
        before = self.size()
        async with self._write_lock:
            page = await self.fetchone("PRAGMA page_size")
            free = await self.fetchone("PRAGMA freelist_count")
            if page is not None and free is not None and page[0] * free[0] >= min_free:
                await self._when_idle("VACUUM")
            await self._when_idle("PRAGMA wal_checkpoint(TRUNCATE)")
        return max(0, before - self.size())

    async def _when_idle(self, sql: str) -> bool:
        """Run `sql`, which SQLite refuses while another task's read on this connection is half done
        ("database table is locked", "SQL statements in progress"). False when it never got its turn."""
        for attempt in range(IDLE_TRIES):
            try:
                async with self._conn.execute(sql):
                    return True
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "in progress" not in str(exc):
                    raise
                if attempt + 1 == IDLE_TRIES:
                    log.warning("数据库一直在忙，%s 留到下次清理：%s", sql, exc)
                    return False
                await asyncio.sleep(IDLE_PAUSE * 2**attempt)
        return False

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self._conn.execute(sql, tuple(params)) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self._conn.execute(sql, tuple(params)) as cursor:
            return list(await cursor.fetchall())

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialized write transaction; commits on success, rolls back on any error."""
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            else:
                await self._conn.execute("COMMIT")
