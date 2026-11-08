"""答题's first step and its look through the chat log: reading the model's sorting, which calls are worth
sorting at all, shared cards' links, and the stored-message search."""

from __future__ import annotations

import json
import time
from pathlib import Path

from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.core.prompt import Sorting, card_links, parse_sorting, worth_sorting
from nest.storage.accounts import upsert_account
from nest.storage.db import Database
from nest.storage.messages import MessageStore


def test_sorting_is_read_from_the_json_line_and_anything_else_is_unreadable() -> None:
    assert parse_sorting('{"kind": "chat"}') == Sorting("chat")
    sorting = parse_sorting('嗯：{"kind": "question", "topic": " 今天  几号 "}')
    assert sorting == Sorting("question", "今天 几号")
    raw = '{"kind": "history", "topic": "谁发的", "keywords": ["B站", "", 3, "a", "b"], "links": true}'
    assert parse_sorting(raw) == Sorting("history", "谁发的", ("B站", "3", "a"), True)
    # Nothing to look for: an ordinary question.
    assert parse_sorting('{"kind": "history", "keywords": []}') == Sorting("question")
    for raw in ("question", '{"kind": "maybe"}', "[1]", '{"kind": "chat"', ""):
        assert parse_sorting(raw) is None


def test_only_a_call_that_may_be_a_question_is_sorted() -> None:
    for text in ("几点了", "推荐个耳机", "上次那个链接", "你知道 Python 3.14 吗", "这是什么？"):
        assert worth_sorting([text]), text
    for text in ("哈哈哈哈", "出来玩", "早", "", "好耶"):
        assert not worth_sorting([text]), text
    assert worth_sorting(["我昨天在车站附近看到一家拉面店排了好长的队一直排到街角"])  # long enough


def test_a_shared_card_shows_its_title_and_link() -> None:
    card = {"meta": {"news": {"title": "一个好笑的视频", "jumpUrl": "https://x.com/1", "desc": "88 赞"}}}
    segments = [
        {"type": "text", "data": {"text": "看"}},
        {"type": "json", "data": {"data": json.dumps(card)}},
        {"type": "json", "data": {"data": "not json"}},
        {"type": "share", "data": {"url": "https://y.com", "title": "Y"}},
    ]
    assert card_links(segments) == ["[分享] 一个好笑的视频 https://x.com/1", "[分享] Y https://y.com"]
    assert card_links(None) == []


async def test_the_chat_log_is_searched_by_words_and_for_links(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    try:
        await upsert_account(db, platform="qq", self_id="1", name="鲸", http_url="http://x", ws_url="ws://x")
        store = MessageStore(db)
        ref = ChannelRef("qq", "1", "group", "42")
        ids = []
        for n, text in enumerate(["100%好笑", "1000好笑", "看 https://a.com", "B站那个", "别的"]):
            segments = (Segment("text", {"text": text}),)
            row = await store.save(MessageEvent(ref, f"m{n}", "7", "小明", segments, text, time.time() + n))
            assert row is not None
            ids.append(row["id"])
        channel = await store.channel_id(ref)
        found = await store.search(channel, words=["100%"], links=False, before=ids[-1] + 1)
        assert [row["text"] for row in found] == ["100%好笑"]  # % is a character, not a wildcard
        found = await store.search(channel, words=["B站"], links=True, before=ids[-1] + 1)
        assert [row["text"] for row in found] == ["看 https://a.com", "B站那个"]
        assert await store.search(channel, words=["B站"], links=False, before=ids[3]) == []
        assert await store.search(channel, words=[" "], links=False, before=ids[-1] + 1) == []
    finally:
        await db.close()
