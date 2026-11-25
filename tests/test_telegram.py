"""The Telegram adapter against a fake Bot API: polling, who is called, what goes out, another program
polling, rate limits, and that the token stays out of everything nest says."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from nest.adapters.base import AdapterStatus, MediaError
from nest.adapters.registry import PLATFORMS, can, check_user
from nest.adapters.telegram.adapter import MIGRATED, TelegramAdapter
from nest.adapters.telegram.normalize import Me, channel_of, normalize, outgoing, parse_text, split_key, units
from nest.adapters.telegram.probe import probe
from nest.core.events import (
    ChannelRef,
    ChannelsSeen,
    Event,
    MemberJoined,
    MessageEvent,
    Segment,
    SendMessage,
)
from tests.helpers import eventually

TOKEN = "900000001:fixture-token-that-is-long-enough"
BOT = "900000001"
ME = Me(BOT, "jing_bot", "阿鲸")
GROUP_ID = -1009000001
GROUP = ChannelRef("telegram", BOT, "group", str(GROUP_ID))
PERSON = ChannelRef("telegram", BOT, "private", "90000042")
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class FakeTelegram:
    """Answers Bot API calls; `updates` is what getUpdates gives (once, until the offset passes it)."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.uploads: list[str] = []
        self.answers: dict[str, list[tuple[int, dict[str, Any]]]] = {}  # method -> what to answer first
        self.me: dict[str, Any] = {
            "id": int(BOT),
            "is_bot": True,
            "first_name": "阿鲸",
            "username": "jing_bot",
        }
        self.next_id = 500
        self.files: dict[str, bytes] = {}
        self.transport = httpx.MockTransport(self.answer)

    async def answer(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.002)  # a poll that answers at once would leave no turn for the test
        return self.handle(request)

    def said(self, method: str) -> list[dict[str, Any]]:
        return [body for name, body in self.calls if name == method]

    def push(self, **message: Any) -> None:
        number = len(self.updates) + 1
        self.updates.append({"update_id": number, **message})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith(f"/file/bot{TOKEN}/"):
            data = self.files.get(path.rsplit("/", 1)[1])
            return httpx.Response(200 if data else 404, content=data or b"")
        prefix = f"/bot{TOKEN}/"
        if not path.startswith(prefix):
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        method = path.removeprefix(prefix)
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        else:
            body = {"multipart": request.content}
        self.calls.append((method, body))
        if self.answers.get(method):
            status, answer = self.answers[method].pop(0)
            return httpx.Response(status, json=answer)
        return httpx.Response(200, json={"ok": True, "result": self.result(method, body)})

    def result(self, method: str, body: dict[str, Any]) -> Any:
        if method == "getMe":
            return self.me
        if method == "getUpdates":
            return [u for u in self.updates if u["update_id"] >= body["offset"]]
        if method == "getFile":
            return {"file_path": f"photos/{body['file_id']}"}
        self.next_id += 1
        chat = body.get("chat_id", GROUP_ID)
        return {
            "message_id": self.next_id,
            "date": int(time.time()),
            "chat": {"id": chat, "type": "supergroup" if chat < 0 else "private"},
            "from": self.me,
            **({"text": body["text"]} if "text" in body else {"photo": [{"file_id": "own", "width": 1}]}),
        }


def message(text: str = "", *, chat: int = GROUP_ID, number: int = 7, **extra: Any) -> dict[str, Any]:
    kind = "supergroup" if chat < 0 else "private"
    return {
        "message_id": number,
        "date": int(time.time()),
        "chat": {"id": chat, "type": kind, **({"title": "Demo群"} if chat < 0 else {})},
        "from": {"id": 90000042, "first_name": "阿星", "username": "axing"},
        **({"text": text} if text else {}),
        **extra,
    }


def entity(kind: str, text: str, part: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "offset": units(text[: text.index(part)]), "length": units(part), **extra}


@dataclass
class Run:
    adapter: TelegramAdapter
    events: list[Event] = field(default_factory=list)
    statuses: list[AdapterStatus] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.adapter.status().state

    def messages(self) -> list[MessageEvent]:
        return [e for e in self.events if isinstance(e, MessageEvent)]


@asynccontextmanager
async def running(telegram: FakeTelegram, *, connect: bool = True, token: str = TOKEN) -> AsyncIterator[Run]:
    run = Run(None)  # type: ignore[arg-type]
    run.adapter = TelegramAdapter(
        account=BOT,
        token=token,
        transport=telegram.transport,
        on_status=run.statuses.append,
        poll_seconds=0,
        backoff_initial=0.01,
        backoff_max=0.05,
        conflict_wait=0.01,
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


def send(channel: ChannelRef, *segments: Segment, trigger: str | None = None) -> SendMessage:
    return SendMessage(channel, segments, "key", trigger)


def say(text: str, channel: ChannelRef = GROUP) -> SendMessage:
    return send(channel, Segment("text", {"text": text}))


# -- what comes in ---------------------------------------------------------------------------------


async def test_a_group_message_arrives_once_with_the_groups_name() -> None:
    telegram = FakeTelegram()
    telegram.push(message=message("大家好"))
    async with running(telegram) as run:
        await eventually(lambda: len(telegram.said("getUpdates")) > 3)
        [event] = run.messages()
        assert (event.channel, event.text, event.sender_id, event.sender_name) == (
            GROUP,
            "大家好",
            "90000042",
            "阿星",
        )
        assert event.message_id == f"{GROUP_ID}.7" and not event.mentions and not event.backfilled
        [seen] = [e for e in run.events if isinstance(e, ChannelsSeen)]
        assert (seen.infos[0].channel, seen.infos[0].name) == (GROUP, "Demo群")
        assert telegram.said("getUpdates")[-1]["offset"] == 2  # taken: not given again


async def test_old_words_are_context_not_something_to_answer() -> None:
    telegram = FakeTelegram()
    telegram.push(message=message("昨天说的", date=int(time.time()) - 3600))
    async with running(telegram) as run:
        await eventually(lambda: run.messages() != [])
        assert run.messages()[0].backfilled


async def test_joins_membership_and_odd_updates() -> None:
    telegram = FakeTelegram()
    chat = {"id": -55, "type": "group", "title": "小群"}
    telegram.push(my_chat_member={"chat": chat, "new_chat_member": {"status": "member"}})
    telegram.push(message=message(new_chat_members=[{"id": 90000043, "first_name": "阿月"}, ME_USER]))
    telegram.push(message={"broken": True})
    telegram.push(message=message("频道里的", chat=-77) | {"chat": {"id": -77, "type": "channel"}})
    telegram.push(message=message("还在"))
    async with running(telegram) as run:
        await eventually(lambda: run.messages() != [])
        assert [e.text for e in run.messages()] == ["还在"]
        names = [i.name for e in run.events if isinstance(e, ChannelsSeen) for i in e.infos]
        assert names == ["小群", "Demo群"]
        assert [(e.user_id, e.name) for e in run.events if isinstance(e, MemberJoined)] == [
            ("90000043", "阿月")
        ]


ME_USER = {"id": int(BOT), "is_bot": True, "first_name": "阿鲸"}


def test_who_is_called_counts_in_utf16() -> None:
    text = "😀😀 @Jing_Bot 看看 阿龙 和 @someone"
    entities = [
        entity("mention", text, "@Jing_Bot"),
        entity("text_mention", text, "阿龙", user={"id": 90000044, "first_name": "阿龙"}),
        entity("mention", text, "@someone"),
    ]
    segments, called = parse_text(text, entities, ME)
    assert called == {BOT, "90000044"}
    assert [s.type for s in segments] == ["text", "at", "text", "at", "text"]
    assert segments[0].data["text"] == "😀😀 " and segments[1].data == {"id": BOT, "name": "阿鲸"}
    assert segments[-1].data["text"] == " 和 @someone"


def test_a_command_for_the_bot_calls_it_and_others_do_not() -> None:
    ours, theirs = "/help@jing_bot 呢", "/help@other_bot"
    assert parse_text(ours, [entity("bot_command", ours, "/help@jing_bot")], ME)[1] == {BOT}
    assert parse_text(theirs, [entity("bot_command", theirs, "/help@other_bot")], ME)[1] == set()


def test_quotes_topics_bots_and_channel_posts() -> None:
    quoted = normalize(message("对", reply_to_message={"message_id": 3}), GROUP, ME)
    assert quoted is not None and quoted.reply_to == f"{GROUP_ID}.3"
    topic = {"message_id": 2, "forum_topic_created": {"name": "闲聊"}}
    in_topic = normalize(message("在话题里", reply_to_message=topic), GROUP, ME)
    assert in_topic is not None and in_topic.reply_to is None
    text = "@jing_bot 你好"
    robot = {"id": 90000050, "is_bot": True, "first_name": "别的 bot"}
    other = normalize(
        message(text, entities=[entity("mention", text, "@jing_bot")]) | {"from": robot}, GROUP, ME
    )
    assert other is not None and other.from_bot and not other.mentions
    post = normalize(
        message("公告", sender_chat={"id": -900, "title": "某频道"}, is_automatic_forward=True), GROUP, ME
    )
    assert post is not None and post.sender_name == "某频道" and post.from_bot
    assert normalize(message(pinned_message={"message_id": 1}), GROUP, ME) is None


def test_pictures_keep_a_file_id_and_never_the_token() -> None:
    sizes = [{"file_id": "small", "width": 90, "height": 90}, {"file_id": "big", "width": 900, "height": 900}]
    event = normalize(message(photo=sizes, caption="看这个"), GROUP, ME)
    assert event is not None
    assert event.segments[0] == Segment("image", {"url": "tgfile:big", "file": "big"})
    assert event.text == "[图片]看这个"
    kinds = {
        "sticker": ({"emoji": "😺"}, "[表情]"),
        "voice": ({"file_id": "v"}, "[语音]"),
        "document": ({"file_id": "d", "file_name": "a.pdf"}, "[文件]"),
        "video": ({"file_id": "x"}, "[视频]"),
    }
    for key, (value, shown) in kinds.items():
        found = normalize(message(**{key: value}), GROUP, ME)
        assert found is not None and found.text == shown
    assert channel_of(message("hi", chat=90000042), BOT) == PERSON


async def test_a_picture_is_fetched_by_its_id() -> None:
    telegram = FakeTelegram()
    telegram.files["big"] = PNG
    async with running(telegram) as run:
        assert await run.adapter.fetch_media("tgfile:big", 1000) == PNG
        with pytest.raises(MediaError):
            await run.adapter.fetch_media("tgfile:big", 5)
        with pytest.raises(MediaError):
            await run.adapter.fetch_media("https://example.com/a.png", 1000)


# -- what goes out ---------------------------------------------------------------------------------


async def test_a_reply_quotes_names_and_comes_back_as_our_own() -> None:
    telegram = FakeTelegram()
    telegram.push(message=message("在吗"))
    async with running(telegram) as run:
        await eventually(lambda: run.messages() != [])
        action = send(
            GROUP,
            Segment("reply", {"id": f"{GROUP_ID}.7"}),
            Segment("at", {"id": "90000042"}),
            Segment("text", {"text": "在呢"}),
        )
        result = await run.adapter.perform(action)
        assert result.state == "succeeded" and result.message_id == f"{GROUP_ID}.501"
        [body] = telegram.said("sendMessage")
        assert body["text"] == "@axing 在呢" and body["chat_id"] == GROUP_ID
        assert body["reply_parameters"] == {"message_id": 7, "allow_sending_without_reply": True}
        own = run.messages()[-1]
        assert own.from_self and own.sender_id == BOT and run.adapter.sent_by_us(own.message_id)


async def test_an_answer_goes_into_the_topic_of_what_it_answers() -> None:
    telegram = FakeTelegram()
    telegram.push(message=message("话题里问", is_topic_message=True, message_thread_id=12))
    async with running(telegram) as run:
        await eventually(lambda: run.messages() != [])
        await run.adapter.perform(send(GROUP, Segment("text", {"text": "答"}), trigger=f"{GROUP_ID}.7"))
        await run.adapter.perform(say("别处"))
        first, second = telegram.said("sendMessage")
        assert first["message_thread_id"] == 12 and "message_thread_id" not in second


async def test_long_text_is_cut_by_telegrams_count_and_only_the_first_piece_quotes() -> None:
    telegram = FakeTelegram()
    async with running(telegram) as run:
        text = "😀" * 3000  # 6000 units
        await run.adapter.perform(
            send(GROUP, Segment("reply", {"id": f"{GROUP_ID}.1"}), Segment("text", {"text": text}))
        )
        bodies = telegram.said("sendMessage")
        assert "".join(b["text"] for b in bodies) == text
        assert all(units(b["text"]) <= 4096 for b in bodies) and len(bodies) == 2
        assert ["reply_parameters" in b for b in bodies] == [True, False]


async def test_a_picture_goes_out_with_its_words() -> None:
    telegram = FakeTelegram()
    async with running(telegram) as run:
        data = "base64://" + base64.b64encode(PNG).decode()
        result = await run.adapter.perform(
            send(GROUP, Segment("text", {"text": "给你"}), Segment("image", {"file": data}))
        )
        assert result.state == "succeeded"
        [body] = telegram.said("sendPhoto")
        assert b"picture.png" in body["multipart"] and "给你".encode() in body["multipart"]
        assert telegram.said("sendMessage") == []


def test_what_cannot_go_out() -> None:
    assert outgoing([Segment("image", {"file": "C:/secret.png"})], {}).uploads == []
    assert outgoing([Segment("at", {"id": "1", "name": "阿星"})], {}).text == "@阿星"
    assert split_key("-100.5") == ("-100", 5) and split_key("app:x") is None


async def test_a_rate_limit_is_waited_out_once() -> None:
    telegram = FakeTelegram()
    limited = (429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 0.01}})
    async with running(telegram) as run:
        telegram.answers["sendMessage"] = [limited]
        assert (await run.adapter.perform(say("一"))).state == "succeeded"
        telegram.answers["sendMessage"] = [limited, limited]
        assert (await run.adapter.perform(say("二"))).state == "failed"
        long = (429, {"ok": False, "parameters": {"retry_after": 900}})
        telegram.answers["sendMessage"] = [long]
        result = await run.adapter.perform(say("三"))
        assert result.state == "failed" and "900" in (result.error or "")


async def test_refusals_are_said_plainly() -> None:
    telegram = FakeTelegram()
    async with running(telegram) as run:
        moved = {
            "ok": False,
            "description": "group chat was upgraded",
            "parameters": {"migrate_to_chat_id": -9},
        }
        telegram.answers["sendMessage"] = [(400, moved)]
        assert (await run.adapter.perform(say("一"))).error == MIGRATED
        telegram.answers["sendMessage"] = [(403, {"ok": False, "description": f"blocked {TOKEN}"})]
        result = await run.adapter.perform(say("二", PERSON))
        assert result.state == "failed" and TOKEN not in (result.error or "")
        telegram.answers["sendMessage"] = [(502, {"ok": False})]
        assert (await run.adapter.perform(say("三"))).state == "unknown"


# -- the connection --------------------------------------------------------------------------------


async def test_a_wrong_token_stops_and_says_so() -> None:
    telegram = FakeTelegram()
    async with running(telegram, connect=False, token="900000001:another-token-that-is-long") as run:
        await eventually(lambda: run.state == "auth_failed")


async def test_someone_elses_token_is_not_used() -> None:
    telegram = FakeTelegram()
    telegram.me = {**telegram.me, "id": 900000002}
    async with running(telegram, connect=False) as run:
        await eventually(lambda: run.state == "misconfigured")
        assert "900000002" in run.adapter.status().detail


async def test_another_program_polling_is_told_apart_from_a_lingering_connection() -> None:
    telegram = FakeTelegram()
    busy = (409, {"ok": False, "description": "Conflict: terminated by other getUpdates request"})
    telegram.answers["getUpdates"] = [busy, busy]
    async with running(telegram) as run:  # the old connection lingered: it connects all the same
        telegram.answers["getUpdates"] = [busy] * 6
        await eventually(lambda: any("在别处也在收消息" in s.detail for s in run.statuses))
        await eventually(lambda: run.state == "connected")  # and again once the other one is gone


async def test_a_webhook_is_left_alone() -> None:
    telegram = FakeTelegram()
    hooked = {"ok": False, "description": "Conflict: can't use getUpdates method while webhook is active"}
    telegram.answers["getUpdates"] = [(409, hooked)]
    async with running(telegram, connect=False) as run:
        await eventually(lambda: run.state == "misconfigured")
        assert "webhook" in run.adapter.status().detail
        assert {name for name, _ in telegram.calls} == {"getMe", "getUpdates"}


async def test_a_dropped_network_is_retried_and_the_token_stays_out_of_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    telegram = FakeTelegram()
    handle = telegram.answer
    down = [True]

    async def flaky(request: httpx.Request) -> httpx.Response:
        if down[0]:
            raise httpx.ConnectError(f"cannot reach {request.url}")
        return await handle(request)

    telegram.transport = httpx.MockTransport(flaky)
    async with running(telegram, connect=False) as run:
        await eventually(lambda: run.state == "reconnecting")
        assert (await run.adapter.perform(say("没发"))).state == "failed"
        down[0] = False
        await eventually(lambda: run.state == "connected")
        await run.adapter.perform(say("发了"))
    assert TOKEN not in caplog.text
    assert all(TOKEN not in s.detail for s in run.statuses)


# -- the console -----------------------------------------------------------------------------------


async def test_trying_a_token() -> None:
    telegram = FakeTelegram()
    found = await probe({"token": TOKEN, "proxy": ""}, transport=telegram.transport)
    assert found.ok and found.self_id == BOT and found.name == "阿鲸" and "setprivacy" in found.detail
    telegram.me = {**telegram.me, "can_read_all_group_messages": True}
    assert "setprivacy" not in (await probe({"token": TOKEN}, transport=telegram.transport)).detail
    wrong = await probe({"token": "900000001:another-token-that-is-long"}, transport=telegram.transport)
    assert not wrong.ok and "Token 不对" in wrong.detail
    for fields in ({}, {"token": "abc"}, {"token": TOKEN, "proxy": "socks5://127.0.0.1:1"}):
        assert not (await probe(fields, transport=telegram.transport)).ok


def test_the_platform_is_listed() -> None:
    spec = PLATFORMS["telegram"]
    assert spec.refs("9")["token"] == "NEST_TELEGRAM_9_TOKEN"
    assert can("telegram:9", "sees_all") and not can("telegram:9", "comparable")
    assert can("discord:9", "comparable") and can("qq:9", "comparable")
    assert check_user("telegram", " 90000042 ") == "90000042"
    with pytest.raises(ValueError, match="数字"):
        check_user("telegram", "@axing")
    with pytest.raises(ValueError, match="BotFather"):
        spec.clean({"token": "abc"})
