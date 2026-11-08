"""The 系统 page's 「存储」: how long history is kept, the daily cleanup (and 立即清理), shrinking the
database, and deleting old QQ pictures, videos and files from the Docker container's data. Nothing here
touches the real QQ folders: every QQ tree is built under tmp_path."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from nest import housekeeping
from nest.adapters.base import AdapterStatus
from nest.api import routes_system_admin
from nest.app import create_app
from nest.config import Settings
from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.storage.accounts import upsert_account
from nest.storage.db import Database
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}
GROUP = ChannelRef("qq", "10001", "group", "42")
DAY = 86400


def message(mid: str, text: str = "hi") -> MessageEvent:
    return MessageEvent(
        channel=GROUP,
        message_id=mid,
        sender_id="7",
        sender_name="小明",
        segments=(Segment("text", {"text": text}),),
        text=text,
        timestamp=time.time(),
        mentions=frozenset(),
    )


def write(path: Path, size: int = 10, *, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    when = time.time() - age_days * DAY
    os.utime(path, (when, when))
    return path


@dataclass
class World:
    http: httpx.AsyncClient
    app: FastAPI
    home: Path

    async def age_messages(self, days: float) -> None:
        async with self.app.state.db.transaction() as conn:
            await conn.execute("UPDATE messages SET received_at = ?", (time.time() - days * DAY,))

    async def messages(self) -> int:
        return (await self.app.state.db.fetchone("SELECT count(*) FROM messages"))[0]


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    monkeypatch.setattr(routes_system_admin, "DEFAULT_MODE", "program")
    home = tmp_path / "nest"  # the QQ data folder is beside it (tmp_path/qq), the compose file's default
    db = await Database.open(home / "data" / "nest.db")
    await upsert_account(
        db, platform="qq", self_id="10001", name="阿鲸", http_url="http://x", ws_url="ws://x/"
    )
    await db.close()
    settings = Settings(home=home, port=18800, _env_file=None)

    app = create_app(settings, token=TOKEN, adapter_factory=lambda *a: _Idle())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            yield World(client, app, home)


class _Idle:
    platform = "qq"
    capabilities = frozenset()

    async def start(self, emit: object) -> None:
        pass

    async def stop(self) -> None:
        pass

    def status(self) -> AdapterStatus:
        return AdapterStatus("connected", "", 0.0)


def qq_tree(home: Path, profile: str = "qq-config") -> Path:
    """One QQ client's profile in the QQ data folder: a NapCat container's ("napcat:<bot>"), or in
    SnowLuma's, the image's own or an extra one's HOME."""
    root = housekeeping.qq_cache_root(home)
    if profile.startswith("napcat:"):
        return root / "napcat" / profile.removeprefix("napcat:") / "qq"
    snowluma = root / "snowluma"
    return snowluma / "qq-config" / "QQ" if profile == "qq-config" else snowluma / profile / ".config" / "QQ"


async def test_defaults_and_sizes(world: World) -> None:
    view = (await world.http.get("/api/system/storage")).json()
    assert view["history_days"] == 30  # NEST_MESSAGE_RETENTION_DAYS' default
    assert view["qq_cache_days"] == 7
    assert view["qq_cache"] is False  # program mode: QQ's folder is the user's own, never cleaned
    assert view["sizes"]["database"] > 0 and view["sizes"]["qq_cache"] is None
    assert view["last"] is None


async def test_saving_the_days(world: World) -> None:
    response = await world.http.patch("/api/system/settings", json={"history_days": 7, "qq_cache_days": 0})
    assert response.status_code == 200
    view = (await world.http.get("/api/system/storage")).json()
    assert (view["history_days"], view["qq_cache_days"]) == (7, 0)
    for body in ({"history_days": 0}, {"history_days": 366}, {"qq_cache_days": -1}, {"history_days": 1.5}):
        assert (await world.http.patch("/api/system/settings", json=body)).status_code == 422


async def test_cleaning_now_keeps_only_the_saved_days(world: World) -> None:
    await world.app.state.store.save(message("old"))
    await world.age_messages(10)
    await world.app.state.store.save(message("new"))
    response = await world.http.post("/api/system/storage/clean")
    assert response.status_code == 200
    assert await world.messages() == 2  # 30 days by default: both stay
    await world.http.patch("/api/system/settings", json={"history_days": 7})
    view = (await world.http.post("/api/system/storage/clean")).json()
    assert await world.messages() == 1
    assert view["last"]["messages"] == 1 and view["last"]["files"] == 0


async def test_qq_cache_is_cleaned_only_in_docker_mode(world: World) -> None:
    root = qq_tree(world.home)
    old_pic = write(root / "nt_qq_abc" / "nt_data" / "Pic" / "2026-10" / "old.jpg", 100, age_days=30)
    new_pic = write(root / "nt_qq_abc" / "nt_data" / "Pic" / "2026-10" / "new.jpg", 100)
    old_video = write(
        qq_tree(world.home, "qq-extra-1") / "nt_qq_def" / "nt_data" / "Video" / "old.mp4", 1000, age_days=30
    )
    old_file = write(
        qq_tree(world.home, "napcat:pony") / "nt_qq_ghi" / "nt_data" / "File" / "old.zip", 2000, age_days=30
    )
    database = write(root / "nt_qq_abc" / "nt_db" / "nt_msg.db", 50, age_days=30)
    shared = write(root / "global" / "nt_data" / "Pic" / "old.jpg", 50, age_days=30)

    await world.http.post("/api/system/storage/clean")
    assert old_pic.exists()  # program mode: untouched

    await world.http.patch("/api/system/settings", json={"snowluma_mode": "docker"})
    view = (await world.http.get("/api/system/storage")).json()
    assert view["qq_cache"] is True and view["sizes"]["qq_cache"] == 3200
    view = (await world.http.post("/api/system/storage/clean")).json()
    assert view["last"]["files"] == 3 and view["last"]["freed"] >= 3100
    assert not old_pic.exists() and not old_video.exists() and not old_file.exists()
    assert new_pic.exists() and database.exists() and shared.exists()
    assert view["sizes"]["qq_cache"] == 100

    await world.http.patch("/api/system/settings", json={"qq_cache_days": 0})
    write(old_pic, 100, age_days=30)
    await world.http.post("/api/system/storage/clean")
    assert old_pic.exists()  # 0: keep QQ's cache


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_qq_cache_never_follows_links(tmp_path: Path) -> None:
    home = tmp_path / "nest"
    root = qq_tree(home)
    outside = write(tmp_path / "elsewhere" / "keep.jpg", 10, age_days=30)
    (root / "nt_qq_abc" / "nt_data" / "Pic").mkdir(parents=True)
    (root / "nt_qq_abc" / "nt_data" / "Pic" / "link").symlink_to(outside.parent, target_is_directory=True)
    (root / "nt_qq_abc" / "nt_data" / "File").symlink_to(outside.parent, target_is_directory=True)
    assert housekeeping.clean_qq_cache(housekeeping.qq_cache_root(home), time.time()) == (0, 0)
    assert outside.exists()


async def test_compacting_gives_space_back(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    try:
        async with db.transaction() as conn:
            await conn.execute("CREATE TABLE junk (data BLOB)")
            for _ in range(200):
                await conn.execute("INSERT INTO junk VALUES (?)", (b"x" * 50_000,))
        await db.compact(min_free=2**40)  # nothing free yet: only the WAL is emptied
        full = db.size()
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM junk")
        assert await db.compact(min_free=1) > 5_000_000
        assert db.size() < full / 2
    finally:
        await db.close()


async def test_compacting_waits_for_a_read_another_task_has_half_done(tmp_path: Path) -> None:
    """SQLite refuses VACUUM and emptying the WAL ("database table is locked") while a read on the same
    connection is unfinished; compacting waits for it, and gives up quietly if it never finishes."""
    db = await Database.open(tmp_path / "nest.db")
    try:
        async with db.transaction() as conn:
            await conn.execute("CREATE TABLE junk (data BLOB)")
            for _ in range(100):
                await conn.execute("INSERT INTO junk VALUES (?)", (b"x" * 50_000,))
            await conn.execute("DELETE FROM junk WHERE rowid > 1")
        reading = await db._conn.execute("SELECT rowid FROM sqlite_master UNION ALL SELECT rowid FROM junk")
        await reading.fetchone()

        async def finish() -> None:
            await asyncio.sleep(0.1)
            await reading.close()

        closing = asyncio.create_task(finish())
        assert await db.compact(min_free=1) > 2_000_000
        await closing

        stuck = await db._conn.execute("SELECT rowid FROM sqlite_master UNION ALL SELECT rowid FROM junk")
        await stuck.fetchone()
        await db.compact(min_free=1)  # left for next time, no error
        await stuck.close()
    finally:
        await db.close()
