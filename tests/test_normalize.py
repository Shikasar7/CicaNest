from __future__ import annotations

import pytest

from nest.adapters.onebot11.normalize import normalize, outbound, render_text
from nest.core.brain import typed_text
from nest.core.events import ChannelRef, MessageEvent, RecallEvent, Segment
from tests.fakes.onebot import FakeOneBot

ACCOUNT = "10001"
bot = FakeOneBot(self_id=int(ACCOUNT))  # only used to build payloads; never started


def test_group_message_with_reply_mention_and_media() -> None:
    payload = bot.group_message(group_id=42, user_id=7, text="你好", nickname="小明", at=[10001], reply_to=55)
    payload["message"].append({"type": "image", "data": {"file": "a.png"}})
    payload["message"].append({"type": "face", "data": {"id": "14"}})
    event = normalize(payload, ACCOUNT)
    assert isinstance(event, MessageEvent)
    assert event.channel == ChannelRef("qq", ACCOUNT, "group", "42")
    assert event.sender_id == "7" and event.sender_name == "小明" and not event.from_self
    assert event.mentions == frozenset({"10001"}) and event.reply_to == "55"
    assert event.text == "@10001你好[图片][表情]"
    assert event.segments[0] == Segment("reply", {"id": "55"})
    assert not event.backfilled


def test_card_name_wins_over_nickname_and_summary_is_used_for_stickers() -> None:
    payload = bot.group_message(group_id=1, user_id=2, text="")
    payload["sender"]["card"] = "群名片"
    payload["message"] = [{"type": "image", "data": {"summary": "[动画表情]"}}]
    event = normalize(payload, ACCOUNT)
    assert isinstance(event, MessageEvent)
    assert event.sender_name == "群名片" and event.text == "[动画表情]"


@pytest.mark.parametrize("post_type", ["message_sent", "message"])
def test_own_messages_are_marked_from_self(post_type: str) -> None:
    # SnowLuma may report our own messages either way; the sender id decides for plain "message".
    payload = bot.group_message(group_id=1, user_id=int(ACCOUNT), text="我说的")
    payload["post_type"] = post_type
    event = normalize(payload, ACCOUNT)
    assert isinstance(event, MessageEvent) and event.from_self


def test_private_message_channel_is_the_peer() -> None:
    incoming = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 7,
        "message_id": 1,
        "time": 1,
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "sender": {"nickname": "小明"},
    }
    outgoing = incoming | {"post_type": "message_sent", "user_id": int(ACCOUNT), "target_id": 7}
    assert normalize(incoming, ACCOUNT).channel == ChannelRef("qq", ACCOUNT, "private", "7")  # type: ignore[union-attr]
    assert normalize(outgoing, ACCOUNT).channel == ChannelRef("qq", ACCOUNT, "private", "7")  # type: ignore[union-attr]


def test_recall_notice() -> None:
    event = normalize(bot.recall(group_id=42, message_id=99, operator_id=7), ACCOUNT)
    assert event == RecallEvent(ChannelRef("qq", ACCOUNT, "group", "42"), "99", event.timestamp)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "payload",
    [
        {"post_type": "meta_event", "meta_event_type": "heartbeat"},
        {"post_type": "request", "request_type": "friend"},
        {"post_type": "notice", "notice_type": "group_increase"},
        {"post_type": "message", "message_type": "guild"},
    ],
)
def test_unused_events_are_ignored(payload: dict) -> None:
    assert normalize(payload, ACCOUNT) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"message": 123},
        {"message": [{"data": {}}]},
        {"message_type": "group", "group_id": None, "message": "x"} | {"group_id": None},
    ],
)
def test_malformed_messages_raise(broken: dict) -> None:
    payload = bot.group_message(group_id=1, user_id=2, text="x") | broken
    if payload.get("group_id") is None:
        del payload["group_id"]
    with pytest.raises((ValueError, KeyError)):
        normalize(payload, ACCOUNT)


def test_string_message_format_is_tolerated() -> None:
    payload = bot.group_message(group_id=1, user_id=2) | {"message": "纯文本"}
    event = normalize(payload, ACCOUNT)
    assert isinstance(event, MessageEvent) and event.text == "纯文本"


def test_what_was_said_in_a_voice_counts_as_what_the_sender_said() -> None:
    heard = (Segment("record", {"file": "a.amr", "words": "阿鱼唱首歌"}),)
    assert render_text(heard) == "（语音）阿鱼唱首歌"
    assert typed_text(heard) == "阿鱼唱首歌"  # so it calls a bot by name, asks for a song…
    assert render_text((Segment("record", {"file": "a.amr"}),)) == "[语音]"


def test_an_at_is_neutral_in_nest_and_onebot_only_on_the_wire() -> None:
    event = normalize(bot.group_message(group_id=42, user_id=7, text="嗨", at=[10001]), ACCOUNT)
    assert isinstance(event, MessageEvent)
    assert event.segments[0] == Segment("at", {"id": "10001"})
    assert event.mentions == {"10001"} and event.text == "@10001嗨"
    assert outbound(Segment("at", {"id": "7", "name": "小明"})) == {
        "type": "at",
        "data": {"qq": "7", "name": "小明"},
    }
    assert outbound(Segment("text", {"text": "嗨"})) == {"type": "text", "data": {"text": "嗨"}}


def test_messages_stored_in_the_old_at_form_still_read() -> None:
    old = (
        Segment("at", {"qq": "all"}),
        Segment("at", {"qq": "7", "name": "小明"}),
        Segment("at", {"qq": "8"}),
    )
    assert render_text(old) == "@全体成员@小明@8"
    assert render_text((Segment("at", {"id": "all"}), Segment("at", {"id": "8"}))) == "@全体成员@8"
