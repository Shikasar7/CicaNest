"""The 企业微信 adapter against a fake long connection: being kicked stays final, the bot's own words are
delivered by the adapter itself, a reply finds its callback, and only @s arrive from groups."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nest.adapters.base import AdapterStatus, MediaError
from nest.adapters.registry import PLATFORMS, build_adapter, can, capabilities_of, check_user, place_of
from nest.adapters.wecom.adapter import KICKED, MAX_BYTES, PER_MINUTE, WecomAdapter
from nest.adapters.wecom.normalize import normalize
from nest.adapters.wecom.probe import probe
from nest.core.events import ChannelRef, Event, MessageEvent, Segment, SendMessage
from nest.secrets import SecretStore
from nest.storage.accounts import Account, upsert_account
from tests.fakes.wecom_ws import FakeWecom
from tests.helpers import eventually
from tests.test_brain import World, world  # noqa: F401

BOT = "aibtest"
GROUP = ChannelRef("wecom", BOT, "group", "wrgroup")
PERSON = ChannelRef("wecom", BOT, "private", "zhangsan")


@dataclass
class Run:
    adapter: WecomAdapter
    events: list[Event] = field(default_factory=list)
    statuses: list[AdapterStatus] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.adapter.status().state

    def messages(self) -> list[MessageEvent]:
        return [e for e in self.events if isinstance(e, MessageEvent)]


@asynccontextmanager
async def running(wecom: FakeWecom, *, connect: bool = True, **options: Any) -> AsyncIterator[Run]:
    settings = {"backoff_initial": 0.01, "backoff_max": 0.05, "ack_timeout": 1.0, **options}
    run = Run(None)  # type: ignore[arg-type]
    run.adapter = WecomAdapter(
        account=wecom.bot_id,
        secret=wecom.secret,
        name="企微小助手",
        on_status=run.statuses.append,
        url=wecom.url,
        **settings,
    )

    async def emit(event: Event) -> None:
        run.events.append(event)

    await run.adapter.start(emit)
    try:
        if connect:
            await eventually(lambda: run.state == "connected")
        yield run
    finally:
        await run.adapter.stop()


def say(words: str, channel: ChannelRef = GROUP, trigger: str | None = None) -> SendMessage:
    return SendMessage(channel, (Segment("text", {"text": words}),), "k", trigger=trigger)


# -- connecting --------------------------------------------------------------------------------------------


async def test_it_subscribes_with_its_bot_id_and_secret() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        [frame] = wecom.frames_of("aibot_subscribe")
        assert frame["body"] == {"bot_id": BOT, "secret": wecom.secret}
        assert run.adapter.status().detail == "企微小助手"


async def test_pings_go_out_and_silence_ends_the_connection() -> None:
    async with FakeWecom() as wecom, running(wecom, ping_seconds=0.02, ack_timeout=0.05) as run:
        await eventually(lambda: len(wecom.frames_of("ping")) >= 2)
        assert run.state == "connected"
        wecom.answer_pings = False
        await eventually(lambda: len(wecom.frames_of("aibot_subscribe")) >= 2)
        wecom.answer_pings = True


async def test_a_dropped_connection_comes_back() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        await wecom.close()
        await eventually(lambda: len(wecom.frames_of("aibot_subscribe")) == 2)
        await eventually(lambda: run.state == "connected")


def test_subscribes_wait_longer_and_longer() -> None:
    adapter = WecomAdapter(account=BOT, secret="s")
    waits = [adapter._backoff.next(0) for _ in range(8)]
    assert waits[0] >= 4 and max(waits) >= 240  # subscribing is rate protected


async def test_a_refused_subscribe_is_an_auth_failure_and_is_not_tried_again() -> None:
    async with FakeWecom() as wecom:
        wecom.secret = "the-right-one"
        adapter = WecomAdapter(account=BOT, secret="a-wrong-secret", url=wecom.url, backoff_initial=0.01)
        await adapter.start(lambda event: asyncio.sleep(0))  # type: ignore[arg-type,return-value]
        await eventually(lambda: adapter.status().state == "auth_failed")
        assert "BotID 或 Secret" in adapter.status().detail
        assert "a-wrong-secret" not in adapter.status().detail
        await asyncio.sleep(0.05)
        assert len(wecom.frames_of("aibot_subscribe")) == 1
        await adapter.stop()


async def test_being_told_to_slow_down_is_tried_again() -> None:
    async with FakeWecom() as wecom:
        wecom.subscribe_code = 45009
        async with running(wecom, connect=False) as run:
            await eventually(lambda: len(wecom.frames_of("aibot_subscribe")) >= 2)
            assert run.state == "reconnecting"
            wecom.subscribe_code = 0
            await eventually(lambda: run.state == "connected")


async def test_no_secret_is_said_without_trying() -> None:
    async with FakeWecom() as wecom:
        adapter = WecomAdapter(account=BOT, secret="", url=wecom.url)
        await adapter.start(lambda event: asyncio.sleep(0))  # type: ignore[arg-type,return-value]
        await eventually(lambda: adapter.status().state == "auth_failed")
        assert not wecom.frames
        await adapter.stop()


# -- being kicked ------------------------------------------------------------------------------------------


async def test_being_kicked_is_final() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        await wecom.kick()
        await eventually(lambda: run.state == "misconfigured")
        assert run.adapter.status().detail == KICKED
        await asyncio.sleep(0.1)
        assert len(wecom.frames_of("aibot_subscribe")) == 1  # or the two would kick each other forever
        assert run.state == "misconfigured"


async def test_two_connections_do_not_fight() -> None:
    async with FakeWecom() as wecom, running(wecom) as first, running(wecom) as second:
        await eventually(lambda: first.state == "misconfigured")
        await asyncio.sleep(0.1)
        assert second.state == "connected"
        assert len(wecom.frames_of("aibot_subscribe")) == 2


# -- receiving ---------------------------------------------------------------------------------------------


def body(**fields: Any) -> dict[str, Any]:
    return {"msgid": "m1", "chatid": "wrgroup", "chattype": "group", "from": {"userid": "zhangsan"}, **fields}


def group_text(content: str, **fields: Any) -> MessageEvent:
    event = normalize(body(msgtype="text", text={"content": content}, **fields), BOT)
    assert event is not None
    return event


def test_a_group_message_always_calls_the_bot_and_the_at_is_cut_off_the_text() -> None:
    event = group_text("@小助手 你好\n第二行")
    assert event.channel == GROUP and event.sender_id == "zhangsan" and event.sender_name == "zhangsan"
    assert event.segments == (
        Segment("at", {"id": BOT, "name": "小助手"}),
        Segment("text", {"text": "你好\n第二行"}),
    )
    assert event.text == "@小助手你好\n第二行"
    assert event.mentions == {BOT} and not event.from_self
    assert group_text("没有前缀的话").mentions == {BOT}


def test_a_private_chat_is_with_the_person_and_nothing_is_cut() -> None:
    event = normalize(body(chattype="single", chatid="", msgtype="text", text={"content": "@谁 嗨"}), BOT)
    assert event is not None
    assert event.channel == PERSON and event.text == "@谁 嗨" and not event.mentions


def test_a_quote_has_no_id_and_becomes_words() -> None:
    event = group_text("@小助手 这个呢", quote={"msgtype": "text", "text": {"content": "原话"}})
    assert event.reply_to is None
    assert event.text == "@小助手[引用] 原话\n这个呢"
    pictured = group_text("这个", quote={"msgtype": "image", "image": {"url": "https://x"}})
    assert "[引用] [图片]" in pictured.text
    assert "https" not in str(pictured.segments)


def test_pictures_stay_placeholders_and_a_voice_message_brings_its_words() -> None:
    private = {"chattype": "single", "chatid": ""}
    image = normalize(body(msgtype="image", image={"url": "https://x", "aeskey": "k"}, **private), BOT)
    assert image is not None and image.text == "[图片]" and image.segments == (Segment("image"),)
    voice = normalize(body(msgtype="voice", voice={"content": "阿鱼唱首歌"}, **private), BOT)
    assert voice is not None and voice.text == "（语音）阿鱼唱首歌"
    file = normalize(body(msgtype="file", file={"url": "https://x"}, **private), BOT)
    assert file is not None and file.text == "[文件]"


def test_mixed_messages_keep_their_order() -> None:
    items = [
        {"msgtype": "text", "text": {"content": "@小助手 看图"}},
        {"msgtype": "image", "image": {"url": "https://x"}},
    ]
    event = normalize(body(msgtype="mixed", mixed={"msg_item": items}), BOT)
    assert event is not None and event.text == "@小助手看图[图片]"


def test_a_kind_nest_has_nothing_to_say_about_is_dropped() -> None:
    assert normalize(body(msgtype="stream", stream={"id": "1"}), BOT) is None


async def test_callbacks_become_events_and_a_repeat_is_dropped() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        frame = wecom.text("@小助手 在吗")
        await wecom.push(frame)
        await wecom.push(frame)
        await wecom.push(wecom.text("在吗", chat=None))
        await eventually(lambda: len(run.messages()) == 2)
        await asyncio.sleep(0.05)
        first, second = run.messages()
        assert first.channel == GROUP and second.channel == PERSON


async def test_a_frame_that_makes_no_sense_is_skipped() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        await wecom.push("不是 JSON")
        await wecom.push(
            {"cmd": "aibot_msg_callback", "headers": {"req_id": "x"}, "body": {"msgtype": "text"}}
        )
        await wecom.push(wecom.text("还活着"))
        await eventually(run.messages)
        assert run.state == "connected"


# -- sending -----------------------------------------------------------------------------------------------


async def test_a_reply_goes_back_on_its_callback_and_comes_back_as_our_own() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        frame = wecom.text("@小助手 在吗")
        await wecom.push(frame)
        [heard] = await eventually(run.messages)
        result = await run.adapter.perform(say("在的", trigger=heard.message_id))
        assert result.state == "succeeded" and result.message_id
        [reply] = wecom.frames_of("aibot_respond_msg")
        assert reply["headers"]["req_id"] == frame["headers"]["req_id"]
        assert reply["body"]["msgtype"] == "stream"
        assert reply["body"]["stream"]["finish"] is True and reply["body"]["stream"]["content"] == "在的"
        own = run.messages()[-1]
        assert own.from_self and own.text == "在的" and own.channel == GROUP
        assert own.message_id == result.message_id and own.sender_id == BOT
        assert run.adapter.sent_by_us(result.message_id) and not run.adapter.sent_by_us("someone-else")


async def test_without_a_callback_it_is_a_push() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        assert (await run.adapter.perform(say("监控的更新"))).state == "succeeded"
        assert (await run.adapter.perform(say("私聊", channel=PERSON, trigger="gone"))).state == "succeeded"
        group, private = wecom.frames_of("aibot_send_msg")
        assert group["body"]["chatid"] == "wrgroup" and group["body"]["chat_type"] == 2
        assert group["body"]["markdown"] == {"content": "监控的更新"}
        assert private["body"]["chatid"] == "zhangsan" and private["body"]["chat_type"] == 1
        assert not wecom.frames_of("aibot_respond_msg")


async def test_a_second_line_refused_on_the_same_callback_is_pushed() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        wecom.one_reply_per_callback = True
        await wecom.push(wecom.text("@小助手 在吗"))
        [heard] = await eventually(run.messages)
        for line in ("第一行", "第二行"):
            assert (await run.adapter.perform(say(line, trigger=heard.message_id))).state == "succeeded"
        assert len(wecom.frames_of("aibot_respond_msg")) == 2
        [push] = wecom.frames_of("aibot_send_msg")
        assert push["body"]["markdown"]["content"] == "第二行"


async def test_a_push_nobody_has_asked_for_is_refused_as_failed() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        wecom.refuse["aibot_send_msg"] = (40058, "chat not found")
        result = await run.adapter.perform(say("嗨"))
        assert result.state == "failed" and "40058" in str(result.error)
        assert not run.messages()  # nothing went out, so nothing is stored as said


async def test_no_answer_may_mean_it_went_out() -> None:
    async with FakeWecom() as wecom, running(wecom, ack_timeout=0.05) as run:
        wecom.silent.add("aibot_send_msg")
        result = await run.adapter.perform(say("嗨"))
        assert result.state == "unknown" and "不确定" in str(result.error)


async def test_only_words_go_out() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        picture = (Segment("image", {"file": "base64://aaaa"}),)
        result = await run.adapter.perform(SendMessage(GROUP, picture, "k"))
        assert result.state == "failed" and "文字" in str(result.error)
        mixed = (Segment("reply", {"id": "1"}), Segment("at", {"id": "x"}), Segment("text", {"text": "嗨"}))
        assert (await run.adapter.perform(SendMessage(GROUP, mixed, "k"))).state == "succeeded"
        assert wecom.frames_of("aibot_send_msg")[0]["body"]["markdown"]["content"] == "嗨"


async def test_long_text_is_cut_by_bytes() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        assert (await run.adapter.perform(say("字" * 10_000))).state == "succeeded"
        contents = [f["body"]["markdown"]["content"] for f in wecom.frames_of("aibot_send_msg")]
        assert len(contents) == 2 and "".join(contents) == "字" * 10_000
        assert all(len(c.encode()) <= MAX_BYTES for c in contents)


async def test_a_conversation_is_held_to_the_platforms_rate() -> None:
    async with FakeWecom() as wecom, running(wecom) as run:
        for _ in range(PER_MINUTE):
            assert (await run.adapter.perform(say("嗨"))).state == "succeeded"
        result = await run.adapter.perform(say("嗨"))
        assert result.state == "failed" and "太多" in str(result.error)
        assert (await run.adapter.perform(say("别的会话", channel=PERSON))).state == "succeeded"


async def test_offline_nothing_is_tried() -> None:
    adapter = WecomAdapter(account=BOT, secret="s")
    result = await adapter.perform(say("嗨"))
    assert result.state == "failed" and "不在线" in str(result.error)


async def test_pictures_cannot_be_fetched_yet() -> None:
    with pytest.raises(MediaError):
        await WecomAdapter(account=BOT, secret="s").fetch_media("https://x", 10)


async def test_there_is_no_group_list() -> None:
    assert await WecomAdapter(account=BOT, secret="s").list_channels() == []


# -- with the brain ----------------------------------------------------------------------------------------


async def test_a_call_in_a_group_is_answered_on_its_callback_and_the_bot_remembers(
    world: World,  # noqa: F811
) -> None:
    await upsert_account(world.db, platform="wecom", self_id=BOT, name="企微", http_url="", ws_url="")
    account = f"wecom:{BOT}"
    await world.repo.upsert_bot(
        account_id=account, name="小助手", aliases=[], provider_id="model-10001", mode="live"
    )
    channel = await world.store.channel_id(GROUP)
    await world.repo.set_channel(account, channel, enabled=True, activity="mentions", cooldown_seconds=0)
    await world.brain.load()

    async def emit(event: Event) -> None:
        assert isinstance(event, MessageEvent)
        row = await world.store.save(event)
        if row is not None:
            await world.brain.on_message(event, row)

    async with FakeWecom() as wecom:
        adapter = WecomAdapter(account=BOT, secret=wecom.secret, name="小助手", url=wecom.url)
        world.brain.adapters = {account: adapter}
        await adapter.start(emit)
        await eventually(lambda: adapter.status().state == "connected")
        world.providers["10001"].queue("在呢")
        frame = wecom.text("@小助手 在吗")
        await wecom.push(frame)
        [decision] = await world.settle(1)
        assert decision["outcome"] == "replied" and decision["reply"] == "在呢"
        [reply] = wecom.frames_of("aibot_respond_msg")
        assert reply["headers"]["req_id"] == frame["headers"]["req_id"]
        rows = await world.store.recent(channel)
        assert [(r["text"], bool(r["from_self"])) for r in rows] == [("@小助手在吗", False), ("在呢", True)]
        assert account not in world.brain.foreign_at  # its own line, not another program's
        system = world.providers["10001"].requests[0][0].content
        assert "企业微信群" in system and "QQ" not in system
        await adapter.stop()


# -- no secret anywhere ------------------------------------------------------------------------------------


def nest_logs(caplog: pytest.LogCaptureFixture) -> str:
    """What nest and the websockets client wrote; the fake server's own debug log shows every frame it got."""
    return " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("uvicorn"))


async def test_the_secret_shows_up_in_no_log_no_status_and_no_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with FakeWecom(secret="very-secret-value") as wecom, running(wecom) as run:
        wecom.refuse["aibot_send_msg"] = (40001, "bad very-secret-value")
        result = await run.adapter.perform(say("嗨"))
        assert "very-secret-value" not in str(result.error)
        await wecom.kick()
        await eventually(lambda: run.state == "misconfigured")
        everything = nest_logs(caplog) + " ".join(s.detail for s in run.statuses)
        assert "very-secret-value" not in everything


# -- trying a bot ------------------------------------------------------------------------------------------


async def test_probe_subscribes_once_and_leaves() -> None:
    async with FakeWecom() as wecom:
        found = await probe({"bot_id": BOT, "secret": wecom.secret}, url=wecom.url)
        assert found.ok and found.self_id == BOT
        await eventually(lambda: wecom.connected_clients == 0)
        wrong = await probe({"bot_id": BOT, "secret": "wrong"}, url=wecom.url)
        assert not wrong.ok and "BotID 或 Secret" in wrong.detail and "wrong" not in wrong.detail
        assert not (await probe({"bot_id": BOT}, url=wecom.url)).ok


async def test_probe_says_when_it_cannot_connect() -> None:
    found = await probe({"bot_id": BOT, "secret": "s"}, url="ws://127.0.0.1:1/")
    assert not found.ok and "连不上" in found.detail


# -- the registry ------------------------------------------------------------------------------------------


def test_what_the_platform_can_do_is_what_the_design_says() -> None:
    assert capabilities_of("wecom") == {"proactive", "send_text"}
    for missing in ("sees_all", "reply", "send_voice", "send_image", "fetch_history", "list_channels"):
        assert not can("wecom:x", missing)
    assert place_of("wecom") == ("企业微信群", "企业微信私聊")
    assert check_user("wecom", " zhang.san ") == "zhang.san"
    with pytest.raises(ValueError):
        check_user("wecom", "带:冒号")


def test_a_bot_id_becomes_a_secret_name_and_never_holds_a_colon() -> None:
    spec = PLATFORMS["wecom"]
    assert spec.refs("aibABC123") == {"secret": "NEST_WECOM_aibABC123_SECRET"}
    odd = spec.refs("aib-x.y")["secret"]
    assert (
        odd.startswith("NEST_WECOM_aib_x_y_")
        and odd.endswith("_SECRET")
        and odd != spec.refs("aib_x_y")["secret"]
    )
    with pytest.raises(ValueError):
        spec.clean({"bot_id": "a:b", "secret": "s"})
    assert spec.clean({"bot_id": " abc ", "secret": " s "}) == {"bot_id": "abc", "secret": "s"}


async def test_the_real_builder_reads_the_secret_of_the_account(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("NEST_WECOM_aib_SECRET", "s3cret")
    config = {"refs": {"secret": "NEST_WECOM_aib_SECRET"}}
    account = Account("wecom:aib", "wecom", "aib", "企微", "", "", "", "", True, config)
    adapter = build_adapter(account, secrets, lambda status: None, None)  # type: ignore[arg-type]
    assert isinstance(adapter, WecomAdapter) and adapter._secret == "s3cret" and adapter.account == "aib"
