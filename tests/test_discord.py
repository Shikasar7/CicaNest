"""The Discord adapter against a fake Gateway and REST API: heartbeats, Resume, close codes, the Identify
allowance, rate limits, long text, what is allowed to notify whom, and catching up."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from nest.adapters.base import AdapterStatus, MediaError
from nest.adapters.discord import gateway as gateway_module
from nest.adapters.discord.adapter import DiscordAdapter
from nest.adapters.discord.gateway import INTENTS
from nest.adapters.discord.normalize import Directory, normalize, outgoing, snowflake_at
from nest.adapters.discord.probe import check_proxy, probe
from nest.adapters.discord.rest import USER_AGENT
from nest.core.events import (
    CaughtUp,
    ChannelRef,
    ChannelsSeen,
    Event,
    MessageEvent,
    RecallEvent,
    Segment,
    SendMessage,
)
from tests.fakes.discord_gateway import FakeDiscord, snowflake
from tests.helpers import eventually

SECRET = "fixture-token"
BOT = "900000000000000001"
GROUP = ChannelRef("discord", BOT, "group", "11")
PERSON = ChannelRef("discord", BOT, "private", "42")


@dataclass
class Run:
    adapter: DiscordAdapter
    events: list[Event] = field(default_factory=list)
    statuses: list[AdapterStatus] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.adapter.status().state

    def messages(self) -> list[MessageEvent]:
        return [e for e in self.events if isinstance(e, MessageEvent)]


@asynccontextmanager
async def running(discord: FakeDiscord, *, connect: bool = True, **options: Any) -> AsyncIterator[Run]:
    settings = {
        "backoff_initial": 0.01,
        "backoff_max": 0.05,
        "identify_initial": 0.01,
        "identify_max": 0.05,
        "identify_floor": 0.05,
        **options,
    }
    run = Run(None)  # type: ignore[arg-type]
    run.adapter = DiscordAdapter(
        account=BOT,
        token=discord.token,
        api_base=discord.api_base,
        media_hosts=("127.0.0.1",),
        on_status=run.statuses.append,
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


def send(
    *segments: Segment, channel: ChannelRef = GROUP, key: str = "k1", trigger: str | None = None
) -> SendMessage:
    return SendMessage(channel, segments, key, trigger=trigger)


def text(words: str) -> Segment:
    return Segment("text", {"text": words})


# -- connecting --------------------------------------------------------------------------------------------


async def test_identify_asks_for_message_content_and_says_nothing_about_this_machine() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        [identify] = discord.identifies
        assert identify["d"]["intents"] == INTENTS == 37377
        assert identify["d"]["properties"] == {"os": "linux", "browser": "nest", "device": "nest"}
        assert run.adapter.status().detail == discord.username
        assert run.adapter.identifies == 1
        assert all(agent == USER_AGENT for agent in discord.user_agents)
        assert USER_AGENT.startswith("DiscordBot (https://nest.invalid, ")


async def test_the_proxy_is_given_to_the_gateway_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def connect(url: str, **options: Any) -> Any:
        seen.append({"url": url, **options})
        raise OSError("refused")

    monkeypatch.setattr(gateway_module.websockets, "connect", connect)
    for proxy in ("http://127.0.0.1:7890", None):
        with pytest.raises(OSError):
            await gateway_module.Gateway("t", lambda *_: None, proxy=proxy).run("wss://g")  # type: ignore[arg-type,return-value]
    assert [options["proxy"] for options in seen] == ["http://127.0.0.1:7890", None]
    assert seen[0]["ping_interval"] is None and seen[0]["url"] == "wss://g/?v=10&encoding=json"


async def test_a_dead_proxy_is_not_called_a_token_problem() -> None:
    async with FakeDiscord() as discord, running(discord, connect=False, proxy="http://127.0.0.1:1") as run:
        await eventually(lambda: "代理 127.0.0.1:1 连不上" in run.adapter.status().detail)
        assert run.state == "reconnecting" and "Token" not in run.adapter.status().detail


async def test_a_token_nobody_knows_stops_at_once() -> None:
    async with FakeDiscord() as discord:
        discord.token = "not-the-one-sent"
        adapter = DiscordAdapter(account=BOT, token=SECRET, api_base=discord.api_base)
        await adapter.start(lambda event: asyncio.sleep(0))  # type: ignore[arg-type,return-value]
        await eventually(lambda: adapter.status().state == "auth_failed")
        await asyncio.sleep(0.05)
        assert len(discord.calls) == 1  # 401s are not retried: Cloudflare counts them
        await adapter.stop()


async def test_no_token_at_all_is_said_without_trying() -> None:
    async with FakeDiscord() as discord:
        adapter = DiscordAdapter(account=BOT, token="", api_base=discord.api_base)
        await adapter.start(lambda event: asyncio.sleep(0))  # type: ignore[arg-type,return-value]
        await eventually(lambda: adapter.status().state == "auth_failed")
        assert adapter.status().detail == "还没有填 Token" and not discord.calls
        await adapter.stop()


async def test_a_token_of_another_bot_is_refused_at_ready() -> None:
    async with FakeDiscord() as discord:
        discord.ready_id = "900000000000000002"
        async with running(discord, connect=False) as run:
            await eventually(lambda: run.state == "misconfigured")
            assert "900000000000000002" in run.adapter.status().detail
            await asyncio.sleep(0.05)
            assert len(discord.identifies) == 1


# -- heartbeats --------------------------------------------------------------------------------------------


async def test_heartbeats_carry_the_last_sequence_number() -> None:
    async with FakeDiscord(heartbeat_ms=20) as discord, running(discord):
        await discord.push_message(discord.message(content="一"))
        await eventually(lambda: len(discord.heartbeats) >= 3)
        latest = discord.heartbeats[-1]["d"]
        assert isinstance(latest, int) and latest >= 2  # READY and the message


async def test_the_server_can_ask_for_a_heartbeat_at_once() -> None:
    async with FakeDiscord() as discord, running(discord):
        await discord.raw({"op": 1})
        await eventually(lambda: discord.heartbeats)


async def test_no_ack_is_a_dead_connection_closed_so_that_it_can_resume() -> None:
    async with FakeDiscord(heartbeat_ms=20) as discord, running(discord) as run:
        discord.ack_heartbeats = False
        await eventually(lambda: discord.resumes)
        assert discord.closes and discord.closes[0] not in (1000, 1001)
        discord.ack_heartbeats = True
        await eventually(lambda: run.state == "connected")
        assert len(discord.identifies) == 1


# -- resuming ----------------------------------------------------------------------------------------------


async def test_a_dropped_connection_resumes_and_gets_what_it_missed() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.push_message(discord.message(content="断之前"))
        await eventually(lambda: len(run.messages()) == 1)
        discord.miss_message(discord.message(content="断的时候"))
        await discord.close(4000)
        await eventually(lambda: discord.resumes)
        await eventually(lambda: run.state == "connected" and len(run.messages()) == 2)
        assert [m.text for m in run.messages()] == ["断之前", "断的时候"]
        [resume] = discord.resumes
        assert resume["d"]["session_id"] == "session-1" and resume["d"]["token"] == discord.token
        assert len(discord.identifies) == 1  # Resume does not touch the allowance
        assert discord.session_start["remaining"] == 999


async def test_op7_resumes_and_a_resumable_invalid_session_does_too() -> None:
    async with FakeDiscord() as discord, running(discord):
        await discord.raw({"op": 7})
        await eventually(lambda: len(discord.resumes) == 1)
        await asyncio.sleep(0.02)
        await discord.raw({"op": 9, "d": True})
        await eventually(lambda: len(discord.resumes) == 2)
        assert len(discord.identifies) == 1


async def test_an_invalid_session_starts_a_new_one() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.raw({"op": 9, "d": False})
        await eventually(lambda: len(discord.identifies) == 2)
        await eventually(lambda: run.state == "connected")
        assert not discord.resumes


async def test_a_refused_resume_starts_a_new_session() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.resume_ok = False
        await discord.close(4000)
        await eventually(lambda: len(discord.identifies) == 2)
        await eventually(lambda: run.state == "connected")
        assert len(discord.resumes) == 1


@pytest.mark.parametrize("code", [4007, 4009])
async def test_close_codes_that_end_the_session_identify_again(code: int) -> None:
    async with FakeDiscord() as discord, running(discord):
        await discord.close(code)
        await eventually(lambda: len(discord.identifies) == 2)
        assert not discord.resumes


# -- close codes -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "state", "says"),
    [
        (4004, "auth_failed", "Token"),
        (4010, "misconfigured", "分片"),
        (4011, "misconfigured", "分片"),
        (4012, "misconfigured", "版本"),
        (4013, "misconfigured", "Intent"),
        (4014, "misconfigured", "Message Content"),
    ],
)
async def test_close_codes_retrying_cannot_fix_end_the_connection(code: int, state: str, says: str) -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.close(code)
        await eventually(lambda: run.state == state)
        assert says in run.adapter.status().detail
        await asyncio.sleep(0.1)
        assert len(discord.identifies) == 1 and not discord.resumes  # and it stays down
        assert run.state == state


# -- the Identify allowance --------------------------------------------------------------------------------


async def test_no_identify_while_the_allowance_is_used_up() -> None:
    async with FakeDiscord() as discord:
        discord.session_start.update(remaining=1, reset_after=10)
        async with running(discord, connect=False, identify_floor=0.2) as run:
            await eventually(lambda: "登录次数快用完了" in run.adapter.status().detail)
            assert run.state == "reconnecting" and not discord.identifies
            discord.session_start["remaining"] = 1000  # the reset came
            await eventually(lambda: run.state == "connected", timeout=2)
            assert len(discord.identifies) == 1


async def test_repeated_identifies_wait_longer_and_longer() -> None:
    async with FakeDiscord() as discord:
        discord.resume_ok = False
        async with running(discord, identify_initial=0.05, identify_max=0.4, identify_stable=100) as run:
            times = []
            for count in range(2, 5):
                await discord.raw({"op": 9, "d": False})
                await eventually(lambda: len(discord.identifies) == count, timeout=3)  # noqa: B023
                times.append(time.monotonic())
                await eventually(lambda: run.state == "connected")
            assert times[2] - times[1] > times[1] - times[0]


def test_the_default_backoff_reaches_minutes() -> None:
    adapter = DiscordAdapter(account=BOT, token="t")
    waits = [adapter._identify_backoff.next(0) for _ in range(10)]
    assert waits[0] < 10 and max(waits) > 300


# -- sending -----------------------------------------------------------------------------------------------


async def test_a_message_is_sent_with_an_allow_list_a_nonce_and_a_quote() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        segments = (Segment("reply", {"id": "777"}), Segment("at", {"id": "42"}), text(" @everyone 你好"))
        result = await run.adapter.perform(send(*segments, key="x" * 80))
        assert result.state == "succeeded"
        [body] = discord.sent
        assert body["content"] == "<@42> @everyone 你好"
        assert body["allowed_mentions"] == {"parse": [], "users": ["42"], "replied_user": True}
        assert body["message_reference"] == {"message_id": "777", "fail_if_not_exists": False}
        assert len(body["nonce"]) <= 25 and body["enforce_nonce"] is True


async def test_nothing_is_ever_allowed_to_notify_everyone() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await run.adapter.perform(send(text("@everyone @here <@&5> <@42>")))
        [body] = discord.sent
        assert body["allowed_mentions"] == {"parse": [], "users": [], "replied_user": False}


async def test_long_text_goes_out_in_pieces_and_only_the_first_quotes() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        lines = "\n".join("一行字" * 100 for _ in range(15))  # 300 a line, 4514 in all
        segments = (Segment("reply", {"id": "777"}), text(lines))
        result = await run.adapter.perform(send(*segments))
        assert result.state == "succeeded"
        contents = [body["content"] for body in discord.sent]
        assert len(contents) == 3 and all(len(c) <= 2000 for c in contents)
        assert "\n".join(contents) == lines  # cut between lines
        assert "message_reference" in discord.sent[0]
        assert "message_reference" not in discord.sent[1]
        assert len({body["nonce"] for body in discord.sent}) == 3


async def test_one_line_longer_than_the_limit_is_cut_too() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await run.adapter.perform(send(text("字" * 4100)))
        assert [len(body["content"]) for body in discord.sent] == [2000, 2000, 100]


async def test_a_long_message_cut_off_midway_is_not_called_failed() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.refuse_send_after = 1
        result = await run.adapter.perform(send(text("字" * 3000)))
        assert result.state == "unknown" and result.message_id
        assert "前 1 段" in str(result.error) and "50013" in str(result.error)


async def test_a_429_is_waited_out_once() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.rate_limit_once(0.05)
        started = time.monotonic()
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "succeeded" and len(discord.sent) == 1
        assert time.monotonic() - started >= 0.05


async def test_a_second_429_gives_up_and_nothing_went_out() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.rate_limit_once(0.01)
        discord.rate_limit_once(0.01)
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "failed" and "限速" in str(result.error) and not discord.sent


async def test_a_long_rate_limit_is_not_waited_for() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.rate_limit_once(600)
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "failed" and "600" in str(result.error)


async def test_a_bucket_at_zero_waits_for_its_reset() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.rate_headers = {
            "X-RateLimit-Bucket": "b1",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset-After": "0.1",
        }
        await run.adapter.perform(send(text("一")))
        started = time.monotonic()
        await run.adapter.perform(send(text("二")))
        assert time.monotonic() - started >= 0.08


@pytest.mark.parametrize("status", [401, 403])
async def test_refusals_are_not_retried(status: int) -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        before = len(discord.calls)
        discord.answer_once(status, {"message": "nope", "code": 50013})
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "failed" and len(discord.calls) == before + 1 and not discord.sent


async def test_a_server_error_may_have_gone_out_and_is_not_retried() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        before = len(discord.calls)
        discord.answer_once(500, {"message": "oops"})
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "unknown" and len(discord.calls) == before + 1


async def test_a_connection_that_cannot_open_certainly_sent_nothing() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        run.adapter._rest._base = "http://127.0.0.1:1"
        result = await run.adapter.perform(send(text("嗨")))
        assert result.state == "failed" and "连不上" in str(result.error)


async def test_offline_nothing_is_tried() -> None:
    async with FakeDiscord() as discord:
        adapter = DiscordAdapter(account=BOT, token=discord.token, api_base=discord.api_base)
        result = await adapter.perform(send(text("嗨")))
        assert result.state == "failed" and "不在线" in str(result.error) and not discord.sent
        await adapter.stop()


async def test_a_picture_and_a_voice_go_out_as_files_and_qq_fields_are_ignored() -> None:
    png = b"\x89PNG\r\n\x1a\n rest"
    mp3 = b"ID3 rest"
    async with FakeDiscord() as discord, running(discord) as run:
        image = {"file": "base64://" + base64.b64encode(png).decode(), "sub_type": 1, "summary": "[动画表情]"}
        result = await run.adapter.perform(send(Segment("image", image)))
        assert result.state == "succeeded"
        voice = {"file": "base64://" + base64.b64encode(mp3).decode()}
        await run.adapter.perform(send(Segment("record", voice)))
        first, second = discord.sent
        assert first["files"]["files[0]"] == ("picture.png", png, "image/png")
        assert second["files"]["files[0]"] == ("voice.mp3", mp3, "audio/mpeg")
        assert "content" not in first and first["allowed_mentions"]["parse"] == []
        own = run.messages()
        assert own[0].text == "[图片]" and "base64" not in json.dumps([dict(s.data) for s in own[0].segments])


async def test_what_discord_has_no_use_for_is_skipped_not_an_error() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        odd = (Segment("face", {"id": "14"}), Segment("image", {"file": "http://x/a.png"}), text("嗨"))
        assert (await run.adapter.perform(send(*odd))).state == "succeeded"
        assert discord.sent[0]["content"] == "嗨"
        nothing = await run.adapter.perform(send(Segment("face", {"id": "14"})))
        assert nothing.state == "failed" and "没有" in str(nothing.error)


def test_a_file_over_the_limit_is_refused_before_sending() -> None:
    too_big = "base64://" + base64.b64encode(b"\0" * (20 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValueError, match="20 MB"):
        outgoing([Segment("image", {"file": too_big})])


async def test_a_sent_message_is_delivered_once_echo_or_not() -> None:
    for echo in (False, True):
        async with FakeDiscord() as discord, running(discord) as run:
            discord.echo_own = echo
            result = await run.adapter.perform(send(text("我说的")))
            assert result.message_id
            await eventually(lambda: run.messages())
            await asyncio.sleep(0.05)
            ids = {m.message_id for m in run.messages()}
            assert ids == {result.message_id}  # the store drops an exact repeat
            assert all(m.from_self and not m.from_bot and m.sender_id == BOT for m in run.messages())
            assert run.adapter.sent_by_us(result.message_id)
            run.adapter._sent.sending(-1)
            assert run.adapter.sent_by_us(result.message_id) and not run.adapter.sent_by_us("someone-else")


# -- private chats -----------------------------------------------------------------------------------------


async def test_a_dm_channel_is_opened_once() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await run.adapter.perform(send(text("一"), channel=PERSON))
        await run.adapter.perform(send(text("二"), channel=PERSON))
        assert discord.dm_opens == ["42"]
        assert [body["channel_id"] for body in discord.sent] == ["dm-42", "dm-42"]
        assert run.messages()[0].channel == PERSON


async def test_a_dm_is_a_private_chat_with_its_sender() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.push_message(
            discord.message(channel_id="dm-7", guild_id=None, author_id="7", content="嗨")
        )
        [event] = await eventually(run.messages)
        assert event.channel == ChannelRef("discord", BOT, "private", "7")
        await run.adapter.perform(send(text("回你"), channel=event.channel))
        assert not discord.dm_opens and discord.sent[0]["channel_id"] == "dm-7"  # it knew the channel already


async def test_our_own_dm_nest_knows_nothing_about_is_left_alone() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.push_message(
            discord.message(channel_id="dm-9", guild_id=None, author_id=BOT, content="？")
        )
        await discord.push_message(discord.message(content="之后"))
        await eventually(run.messages)
        assert [m.text for m in run.messages()] == ["之后"]


# -- receiving ---------------------------------------------------------------------------------------------


def directory() -> Directory:
    known = Directory(BOT)
    known.add_guild(
        {
            "id": "1",
            "name": "测试服",
            "roles": [
                {"id": "1", "name": "@everyone", "permissions": str(1 << 10)},
                {"id": "5", "name": "管理员", "permissions": "0"},
                {"id": "6", "name": "nestbot", "permissions": "0", "tags": {"bot_id": BOT}},
            ],
            "channels": [{"id": "11", "name": "闲聊", "type": 0}, {"id": "12", "name": "公告", "type": 5}],
            "members": [],
        }
    )
    return known


def incoming(**fields: Any) -> dict[str, Any]:
    return {
        "id": snowflake(1_700_000_000_000),
        "channel_id": "11",
        "guild_id": "1",
        "author": {"id": "42", "username": "xiaoming", "global_name": "小明"},
        "content": "",
        "timestamp": "2026-11-07T12:00:00.000000+00:00",
        "type": 0,
        **fields,
    }


def event_of(**fields: Any) -> MessageEvent:
    event = normalize(incoming(**fields), GROUP, directory())
    assert event is not None
    return event


def test_mentions_become_at_segments_and_the_rest_reads_as_text() -> None:
    mentioned = [
        {"id": BOT, "username": "nestbot", "member": {"nick": "小鲸"}},
        {"id": "43", "username": "lihua", "global_name": "李华"},
    ]
    event = event_of(content=f"<@{BOT}> 和 <@!43> 去 <#12> 问 <@&5> <:ok:99> <a:wow:98>", mentions=mentioned)
    assert event.text == "@小鲸 和 @李华 去 #公告 问 @管理员 :ok: :wow:"
    assert event.segments[0] == Segment("at", {"id": BOT, "name": "小鲸"})
    assert event.segments[2] == Segment("at", {"id": "43", "name": "李华"})
    assert event.mentions == {BOT, "43"}
    assert event.timestamp == datetime(2026, 11, 7, 12, tzinfo=UTC).timestamp()


def test_the_bots_own_role_counts_as_calling_it_and_another_role_does_not() -> None:
    assert BOT in event_of(content="<@&6> 在吗", mention_roles=["6"]).mentions
    assert BOT not in event_of(content="<@&5> 在吗", mention_roles=["5"]).mentions


def test_a_nick_comes_before_the_global_name_and_that_before_the_username() -> None:
    assert event_of(member={"nick": "群名片"}).sender_name == "群名片"
    assert event_of().sender_name == "小明"
    assert event_of(author={"id": "42", "username": "xiaoming"}).sender_name == "xiaoming"


def test_attachments_are_sorted_by_kind() -> None:
    event = event_of(
        attachments=[
            {"id": "1", "filename": "a.png", "url": "https://cdn/a.png", "content_type": "image/png"},
            {"id": "2", "filename": "b.ogg", "url": "https://cdn/b.ogg", "content_type": "audio/ogg"},
            {"id": "3", "filename": "c.zip", "url": "https://cdn/c.zip", "content_type": "application/zip"},
            {"id": "4", "filename": "d", "url": "https://cdn/d"},
        ]
    )
    assert [s.type for s in event.segments] == ["image", "record", "file", "file"]
    assert event.segments[0].data["url"] == "https://cdn/a.png" and event.segments[0].data["file"] == "1"
    assert event.text == "[图片][语音][文件][文件]"


def test_a_reply_knows_what_it_replies_to() -> None:
    event = event_of(content="对", type=19, message_reference={"message_id": "555", "channel_id": "11"})
    assert event.reply_to == "555" and event.segments[0] == Segment("reply", {"id": "555"})
    assert event.text == "对"
    forwarded = event_of(content="", message_reference={"message_id": "555", "type": 1})
    assert forwarded.reply_to is None


def test_stickers_read_as_stickers() -> None:
    assert event_of(sticker_items=[{"id": "1", "name": "哈"}]).text == "[表情]"


def test_system_notices_are_nobodys_words() -> None:
    assert normalize(incoming(type=7), GROUP, directory()) is None  # someone joined
    assert normalize(incoming(type=6), GROUP, directory()) is None  # a pin


def test_a_bot_or_webhook_is_marked_and_its_mentions_mean_nothing() -> None:
    called = {"content": f"<@{BOT}>", "mentions": [{"id": BOT, "username": "n"}]}
    other_bot = event_of(author={"id": "50", "username": "b", "bot": True}, **called)
    assert other_bot.from_bot and other_bot.mentions == frozenset()
    webhook = event_of(webhook_id="60", **called)
    assert webhook.from_bot and webhook.mentions == frozenset()
    assert not event_of(**called).from_bot
    ours = event_of(author={"id": BOT, "username": "n", "bot": True})
    assert ours.from_self and not ours.from_bot


async def test_a_deleted_message_is_a_recall() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.push("MESSAGE_DELETE", {"id": "5", "channel_id": "11", "guild_id": "1"})
        await discord.push("MESSAGE_DELETE_BULK", {"ids": ["6", "7"], "channel_id": "11", "guild_id": "1"})
        await discord.push("MESSAGE_DELETE", {"id": "8", "channel_id": "dm-unknown"})
        recalled = await eventually(lambda: [e for e in run.events if isinstance(e, RecallEvent)])
        await eventually(lambda: len([e for e in run.events if isinstance(e, RecallEvent)]) == 3)
        assert recalled[0].channel == GROUP and recalled[0].message_id == "5"


async def test_a_frame_that_makes_no_sense_is_skipped() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        await discord.raw("不是 JSON")
        await discord.push("MESSAGE_CREATE", {"channel_id": "11", "guild_id": "1"})  # no author, no id
        await discord.push("GUILD_CREATE", {"name": "没有 id 的服务器"})
        await discord.push_message(discord.message(content="还活着"))
        await eventually(lambda: [m for m in run.messages() if m.text == "还活着"])
        assert run.adapter._gateway.skipped_frames == 2 and run.state == "connected"


# -- channels ----------------------------------------------------------------------------------------------


async def test_channels_are_named_when_their_server_arrives() -> None:
    async with FakeDiscord() as discord:
        discord.guilds = [discord.guild()]
        async with running(discord) as run:
            seen = await eventually(lambda: [e for e in run.events if isinstance(e, ChannelsSeen)])
            assert [(i.channel, i.name) for i in seen[0].infos] == [(GROUP, "测试服 / #闲聊")]
            assert await run.adapter.list_channels() == list(seen[0].infos)


def test_only_channels_the_bot_can_see_and_post_in_are_listed() -> None:
    view = str(1 << 10)
    known = Directory(BOT)
    known.add_guild(
        {
            "id": "1",
            "name": "服",
            "roles": [
                {"id": "1", "name": "@everyone", "permissions": view},
                {"id": "5", "name": "bot", "permissions": "0"},
            ],
            "channels": [
                {"id": "20", "name": "公开", "type": 0},
                {"id": "21", "name": "公告", "type": 5},
                {"id": "22", "name": "语音", "type": 2},
                {"id": "23", "name": "分类", "type": 4},
                {"id": "24", "name": "论坛", "type": 15},
                {"id": "25", "name": "隐藏", "type": 0, "permission_overwrites": [{"id": "1", "deny": view}]},
                {
                    "id": "26",
                    "name": "给 bot 的",
                    "type": 0,
                    "permission_overwrites": [{"id": "1", "deny": view}, {"id": "5", "allow": view}],
                },
                {
                    "id": "27",
                    "name": "单独挡",
                    "type": 0,
                    "permission_overwrites": [{"id": BOT, "deny": view}],
                },
            ],
            "threads": [
                {"id": "30", "name": "讨论", "type": 11, "parent_id": "20"},
                {"id": "31", "name": "看不见的", "type": 11, "parent_id": "25"},
            ],
            "members": [{"user": {"id": BOT}, "roles": ["5"]}],
        }
    )
    assert known.listed() == ["20", "21", "26", "30"]
    assert known.name("30") == "服 / #公开 / 讨论"
    assert known.name("20") == "服 / #公开"


def test_an_administrator_sees_everything() -> None:
    known = Directory(BOT)
    known.add_guild(
        {
            "id": "1",
            "roles": [
                {"id": "1", "permissions": "0"},
                {"id": "5", "permissions": str(1 << 3), "tags": {"bot_id": BOT}},
            ],
            "channels": [{"id": "20", "name": "x", "type": 0}],
            "members": [],  # not told about itself: its own role still counts
        }
    )
    assert known.listed() == ["20"]


async def test_a_new_thread_is_its_own_group_and_named_at_once() -> None:
    async with FakeDiscord() as discord:
        discord.guilds = [discord.guild()]
        async with running(discord) as run:
            thread = {"id": "30", "name": "讨论", "type": 11, "parent_id": "11", "guild_id": "1"}
            await discord.push("THREAD_CREATE", thread)
            await discord.push_message(discord.message(channel_id="30", content="在线程里"))
            [event] = await eventually(run.messages)
            assert event.channel == ChannelRef("discord", BOT, "group", "30")
            names = [i.name for e in run.events if isinstance(e, ChannelsSeen) for i in e.infos]
            assert "测试服 / #闲聊 / 讨论" in names


async def test_a_message_from_a_channel_nobody_mentioned_finds_its_name_once() -> None:
    async with FakeDiscord() as discord:
        discord.guilds = [discord.guild()]
        discord.channel_details["31"] = {"id": "31", "name": "老线程", "type": 11, "parent_id": "11"}
        async with running(discord) as run:
            await discord.push_message(discord.message(channel_id="31", content="一"))
            await discord.push_message(discord.message(channel_id="32", content="二"))  # 404
            await discord.push_message(discord.message(channel_id="32", content="三"))
            await eventually(lambda: len(run.messages()) == 3)
            assert ("GET", "/channels/31") in discord.calls
            assert discord.calls.count(("GET", "/channels/32")) == 1
            names = [i.name for e in run.events if isinstance(e, ChannelsSeen) for i in e.infos]
            assert "测试服 / #闲聊 / 老线程" in names


async def test_a_server_the_bot_left_takes_its_channels_along() -> None:
    async with FakeDiscord() as discord:
        discord.guilds = [discord.guild()]
        async with running(discord) as run:
            await eventually(lambda: [e for e in run.events if isinstance(e, ChannelsSeen)])
            await discord.push("GUILD_DELETE", {"id": "1"})
            await eventually(lambda: run.adapter._directory.channels == {})


# -- catching up -------------------------------------------------------------------------------------------


def old_message(discord: FakeDiscord, seconds_ago: float, content: str) -> dict[str, Any]:
    at = time.time() - seconds_ago
    return discord.message(
        content=content,
        id=snowflake(int(at * 1000)),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(at)),
    )


async def test_a_new_session_catches_up_and_says_so() -> None:
    async with FakeDiscord() as discord:
        since = time.time() - 100
        history = [old_message(discord, 200, "太早了"), old_message(discord, 50, "漏掉的")]
        for message in history:
            del message["guild_id"]  # REST answers have no guild_id
        discord.history["11"] = history

        async def source() -> list[tuple[ChannelRef, float]]:
            return [(GROUP, since), (PERSON, since)]

        async with running(discord, backfill_source=source) as run:
            caught = await eventually(lambda: [e for e in run.events if isinstance(e, CaughtUp)])
            [event] = run.messages()
            assert event.text == "漏掉的" and event.backfilled
            assert caught[0].account == BOT and caught[0].gaps == ((GROUP, since),)  # groups only


async def test_a_resumed_session_has_nothing_to_catch_up_on() -> None:
    async with FakeDiscord() as discord:
        asked: list[int] = []

        async def source() -> list[tuple[ChannelRef, float]]:
            asked.append(1)
            return []

        async with running(discord, backfill_source=source) as run:
            await eventually(lambda: asked)
            await discord.close(4000)
            await eventually(lambda: discord.resumes)
            await eventually(lambda: run.state == "connected")
            assert asked == [1]


async def test_history_pages_forward_from_the_gap() -> None:
    async with FakeDiscord() as discord:
        since = time.time() - 1000
        discord.history["11"] = [old_message(discord, 900 - n, str(n)) for n in range(250)]

        async def source() -> list[tuple[ChannelRef, float]]:
            return [(GROUP, since)]

        async with running(discord, backfill_source=source) as run:
            await eventually(lambda: [e for e in run.events if isinstance(e, CaughtUp)])
            assert [m.text for m in run.messages()] == [str(n) for n in range(250)]
            assert discord.calls.count(("GET", "/channels/11/messages")) == 3


async def test_a_channel_it_may_not_read_is_skipped() -> None:
    async with FakeDiscord() as discord:
        discord.unreadable.add("11")
        discord.history["12"] = [old_message(discord, 50, "别的频道")]
        other = ChannelRef("discord", BOT, "group", "12")
        discord.history["12"][0]["channel_id"] = "12"

        async def source() -> list[tuple[ChannelRef, float]]:
            return [(GROUP, 0.0), (other, 0.0)]

        async with running(discord, backfill_source=source) as run:
            await eventually(lambda: [e for e in run.events if isinstance(e, CaughtUp)])
            assert [m.text for m in run.messages()] == ["别的频道"]


def test_a_moment_becomes_the_smallest_id_of_that_moment() -> None:
    assert snowflake_at(1420070400) == 0
    assert snowflake_at(1420070401) == 1000 << 22
    assert snowflake_at(0) == 0


# -- pictures ----------------------------------------------------------------------------------------------


async def test_media_comes_from_the_cdn_without_the_token() -> None:
    async with FakeDiscord() as discord, running(discord) as run:
        discord.files["a.png"] = b"picture"
        assert await run.adapter.fetch_media(discord.cdn_url("a.png"), 100) == b"picture"
        with pytest.raises(MediaError, match="不收"):
            await run.adapter.fetch_media(discord.cdn_url("a.png"), 3)
        with pytest.raises(MediaError, match="404"):
            await run.adapter.fetch_media(discord.cdn_url("gone.png"), 100)


async def test_media_only_comes_from_discords_hosts() -> None:
    adapter = DiscordAdapter(account=BOT, token="t")
    with pytest.raises(MediaError, match=r"不从 evil\.example 下载"):
        await adapter.fetch_media("https://evil.example/a.png", 100)
    await adapter.stop()


# -- no token anywhere -------------------------------------------------------------------------------------


async def test_the_token_shows_up_in_no_log_no_status_and_no_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with FakeDiscord(token="very-secret-token-value") as discord, running(discord) as run:
        discord.answer_once(400, {"message": "bad very-secret-token-value", "code": 1})
        refused = await run.adapter.perform(send(text("嗨")))
        discord.answer_once(500, {"message": "oops"})
        await run.adapter.perform(send(text("嗨")))
        await discord.close(4000)
        await eventually(lambda: discord.resumes)
        await eventually(lambda: run.state == "connected")
        await discord.close(4004)
        await eventually(lambda: run.state == "auth_failed")
        assert "very-secret-token-value" not in str(refused.error)
        logged = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("uvicorn"))
        everything = logged + " ".join(s.detail for s in run.statuses) + repr(run.adapter.status())
        assert "very-secret-token-value" not in everything


# -- trying a token ----------------------------------------------------------------------------------------


async def test_probe_finds_out_who_the_token_belongs_to() -> None:
    async with FakeDiscord() as discord:
        found = await probe({"token": discord.token}, api_base=discord.api_base)
        assert found.ok and found.self_id == BOT and found.name == discord.username
        wrong = await probe({"token": "nope"}, api_base=discord.api_base)
        assert not wrong.ok and "Token 不对" in wrong.detail and "nope" not in wrong.detail


async def test_probe_tells_a_dead_proxy_from_a_bad_token() -> None:
    async with FakeDiscord() as discord:
        found = await probe({"token": "t", "proxy": "http://127.0.0.1:1"}, api_base=discord.api_base)
        assert not found.ok and "代理" in found.detail and "不是 Token" in found.detail


async def test_probe_refuses_what_it_cannot_use() -> None:
    assert not (await probe({})).ok
    assert "socks" in (await probe({"token": "t", "proxy": "socks5://127.0.0.1:1080"})).detail
    with pytest.raises(ValueError):
        check_proxy("ftp://x")
    assert check_proxy("") == "" and check_proxy(" http://127.0.0.1:7890 ") == "http://127.0.0.1:7890"
