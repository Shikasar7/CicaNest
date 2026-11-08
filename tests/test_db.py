from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nest.storage.db import Database, SchemaTooNew, load_migrations


async def test_fresh_database_is_migrated_with_wal(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "data" / "nest.db")
    try:
        assert await db.schema_version() == len(load_migrations())
        assert (await db.fetchone("PRAGMA journal_mode"))[0] == "wal"
        assert await db.fetchone("SELECT name FROM sqlite_master WHERE name='settings'") is not None
    finally:
        await db.close()


async def test_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nest.db"
    for _ in range(2):
        db = await Database.open(path)
        version = await db.schema_version()
        await db.close()
    assert version == len(load_migrations())


async def test_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    try:
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO settings VALUES ('a', '1', 0)")
        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO settings VALUES ('b', '2', 0)")
                raise RuntimeError("boom")
        rows = await db.fetchall("SELECT key FROM settings ORDER BY key")
        assert [row["key"] for row in rows] == ["a"]
    finally:
        await db.close()


async def test_refuses_database_from_newer_version(tmp_path: Path) -> None:
    path = tmp_path / "nest.db"
    with sqlite3.connect(path) as raw:
        raw.execute("PRAGMA user_version = 999")
    with pytest.raises(SchemaTooNew):
        await Database.open(path)


def test_migration_numbers_are_contiguous() -> None:
    numbers = [number for number, _, _ in load_migrations()]
    assert numbers == list(range(1, len(numbers) + 1))
