from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nest.core.events import ChannelInfo, ChannelRef, MessageEvent, RecallEvent, Segment
from nest.storage.accounts import list_accounts, upsert_account
from nest.storage.db import Database
from nest.storage.messages import MessageStore

GROUP = ChannelRef("qq", "10001", "group", "42")


def message(mid: str, text: str = "hi", *, at: float | None = None, backfilled: bool = False) -> MessageEvent:
    return MessageEvent(
        channel=GROUP,
        message_id=mid,
        sender_id="7",
        sender_name="小明",
        segments=(Segment("text", {"text": text}),),
        text=text,
        timestamp=at if at is not None else time.time(),
        mentions=frozenset({"10001"}),
        backfilled=backfilled,
    )


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[MessageStore]:
    db = await Database.open(tmp_path / "nest.db")
    await upsert_account(
        db, platform="qq", self_id="10001", name="阿鲸", http_url="http://x", ws_url="ws://x/"
    )
    try:
        yield MessageStore(db)
    finally:
        await db.close()


async def test_save_dedupes_by_message_id_and_keeps_first_source(store: MessageStore) -> None:
    first = await store.save(message("1", "你好"))
    assert first is not None and first["source"] == "live" and first["mentions"] == ["10001"]
    assert first["segments"] == [{"type": "text", "data": {"text": "你好"}}] and first["from_self"] is False
    assert await store.save(message("1", "你好", backfilled=True)) is None
    [channel] = await store.channels()
    assert channel["message_count"] == 1 and channel["last_message_at"] == first["sent_at"]


async def test_recent_is_oldest_first_and_pages_backwards(store: MessageStore) -> None:
    base = time.time()
    for i in range(5):
        await store.save(message(str(i), f"第{i}条", at=base + i))
    channel = (await store.channels())[0]["id"]
    newest = await store.recent(channel, limit=2)
    assert [m["text"] for m in newest] == ["第3条", "第4条"]
    older = await store.recent(channel, limit=2, before=newest[0]["id"])
    assert [m["text"] for m in older] == ["第1条", "第2条"]


async def test_recall_marks_once(store: MessageStore) -> None:
    await store.save(message("9"))
    recalled = await store.recall(RecallEvent(GROUP, "9", 123.0))
    assert recalled is not None and recalled["recalled_at"] == 123.0
    assert await store.recall(RecallEvent(GROUP, "9", 456.0)) is None
    assert await store.recall(RecallEvent(GROUP, "unknown", 1.0)) is None


async def test_channel_names_and_backfill_targets(store: MessageStore) -> None:
    quiet = ChannelRef("qq", "10001", "group", "77")
    await store.name_channels([ChannelInfo(GROUP, "测试群"), ChannelInfo(quiet, "没人说话的群")])
    await store.save(message("1", at=100.0))
    await store.save(message("2", at=200.0))
    await store.save(message("0", at=50.0, backfilled=True))  # an older one arriving late
    names = {c["external_id"]: c["name"] for c in await store.channels()}
    assert names == {"42": "测试群", "77": "没人说话的群"}
    # Only channels with stored messages are worth asking history for, from the newest stored message on.
    assert await store.backfill_targets("qq", "10001") == [(GROUP, 200.0)]


async def test_purge_removes_only_old_messages(store: MessageStore) -> None:
    await store.save(message("old"))
    async with store.db.transaction() as conn:
        await conn.execute("UPDATE messages SET received_at = 0")
    await store.save(message("new"))
    assert await store.purge(time.time() - 60) == 1
    channel = (await store.channels())[0]["id"]
    assert [m["external_id"] for m in await store.recent(channel)] == ["new"]


async def test_account_upsert_keeps_one_row(store: MessageStore) -> None:
    await upsert_account(
        store.db, platform="qq", self_id="10001", name="改名了", http_url="http://y", ws_url="ws://y/"
    )
    [account] = await list_accounts(store.db)
    assert account.name == "改名了" and account.http_url == "http://y" and account.enabled
