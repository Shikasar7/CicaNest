"""The Slack adapter against a fake Web API and a real socket: envelopes acknowledged, who is called, threads,
files, catching up, a new connection when asked, and that no token shows up anywhere."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import websockets

from nest.adapters.base import AdapterStatus, MediaError
from nest.adapters.registry import PLATFORMS, can, check_user
from nest.adapters.slack.adapter import NO_SOCKET_MODE, SlackAdapter
from nest.adapters.slack.normalize import escape, normalize, outgoing, parse_text, split_key
from nest.adapters.slack.probe import probe
from nest.core.events import (
    CaughtUp,
    ChannelRef,
    ChannelsSeen,
    Event,
    MemberJoined,
    MessageEvent,
    RecallEvent,
    Segment,
    SendMessage,
)
from tests.helpers import eventually

BOT_TOKEN, APP_TOKEN = "xoxb-fixture-bot-token", "xapp-fixture-app-token"
BOT, PERSON = "U0NESTBOT", "U0AXING"
GROUP = ChannelRef("slack", BOT, "group", "C0DEMO")
PRIVATE = ChannelRef("slack", BOT, "private", PERSON)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class FakeSlack:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.answers: dict[str, list[tuple[int, dict[str, Any], dict[str, str]]]] = {}
        self.me = {"user_id": BOT, "user": "阿鲸", "bot_id": "B0NEST", "team": "Demo 工作区"}
        self.users = {PERSON: "阿星", "U0LONG": "阿龙"}
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[str, bytes] = {}
        self.uploads: list[bytes] = []
        self.hello: dict[str, Any] = {"type": "hello"}
        self.acks: list[str] = []
        self.sockets: list[Any] = []
        self.opened = 0
        self.url = ""
        self._n = 100
        self.transport = httpx.MockTransport(self.handle)

    def said(self, method: str) -> list[dict[str, Any]]:
        return [body for name, body, _ in self.calls if name == method]

    @asynccontextmanager
    async def serving(self) -> AsyncIterator[FakeSlack]:
        async def socket(ws: Any) -> None:
            self.opened += 1
            self.sockets.append(ws)
            await ws.send(json.dumps(self.hello))
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in ws:
                    self.acks.append(json.loads(raw)["envelope_id"])

        async with websockets.serve(socket, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            self.url = f"ws://127.0.0.1:{port}/?ticket=fixture-ticket"
            yield self

    async def push(self, event: dict[str, Any], envelope: str = "") -> None:
        self._n += 1
        frame = {
            "type": "events_api",
            "envelope_id": envelope or f"e{self._n}",
            "payload": {"event": event},
        }
        await self.sockets[-1].send(json.dumps(frame))

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.slack.com":
            if request.headers.get("authorization") != f"Bearer {BOT_TOKEN}":
                return httpx.Response(302, headers={"location": "https://slack.com/signin"})
            data = self.files.get(request.url.path)
            return httpx.Response(200 if data else 404, content=data or b"")
        if request.url.host == "upload.slack.test":
            assert "authorization" not in request.headers
            self.uploads.append(request.content)
            return httpx.Response(200)
        method = request.url.path.rsplit("/", 1)[1]
        kind = request.headers.get("content-type", "")
        body = (
            json.loads(request.content or b"{}")
            if kind.startswith("application/json")
            else dict(parse_qsl(request.content.decode()))
        )
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.calls.append((method, body, token))
        if self.answers.get(method):
            status, answer, headers = self.answers[method].pop(0)
            return httpx.Response(status, json=answer, headers=headers)
        wanted = APP_TOKEN if method == "apps.connections.open" else BOT_TOKEN
        if token != wanted:
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        return httpx.Response(200, json={"ok": True, **self.result(method, body)})

    def result(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        if method == "auth.test":
            return self.me
        if method == "apps.connections.open":
            return {"url": self.url}
        if method == "users.info":
            name = self.users.get(body["user"], "")
            return {"user": {"id": body["user"], "profile": {"display_name": name}}}
        if method == "users.conversations":
            return {"channels": [{"id": "C0DEMO", "name": "demo群"}]}
        if method == "conversations.history":
            return {"messages": self.history.get(body["channel"], [])}
        if method == "conversations.open":
            return {"channel": {"id": "D0DM"}}
        if method == "files.getUploadURLExternal":
            return {"upload_url": "https://upload.slack.test/put", "file_id": "F0NEW"}
        if method == "chat.postMessage":
            self._n += 1
            ts = f"{int(time.time())}.{self._n:06d}"
            return {"ts": ts, "channel": body["channel"], "message": {"text": body["text"]}}
        return {}


def message(text: str = "", *, channel: str = "C0DEMO", n: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "channel": channel,
        "channel_type": "im" if channel.startswith("D") else "channel",
        "user": PERSON,
        "ts": f"{int(time.time())}.{n:06d}",
        **({"text": text} if text else {}),
        **extra,
    }


@dataclass
class Run:
    adapter: SlackAdapter
    events: list[Event] = field(default_factory=list)
    statuses: list[AdapterStatus] = field(default_factory=list)

    @property
    def state(self) -> str:
        return self.adapter.status().state

    def messages(self) -> list[MessageEvent]:
        return [e for e in self.events if isinstance(e, MessageEvent)]


@asynccontextmanager
async def running(
    slack: FakeSlack, *, connect: bool = True, app_token: str = APP_TOKEN, **options: Any
) -> AsyncIterator[Run]:
    run = Run(None)  # type: ignore[arg-type]
    run.adapter = SlackAdapter(
        account=BOT,
        bot_token=BOT_TOKEN,
        app_token=app_token,
        transport=slack.transport,
        on_status=run.statuses.append,
        backoff_initial=0.01,
        backoff_max=0.05,
        **options,
    )

    async def emit(event: Event) -> None:
        run.events.append(event)

    async with slack.serving():
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


async def test_a_message_is_acknowledged_named_and_delivered() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        await slack.push(message(f"<@{BOT}> 在吗 &amp; <#C9|闲聊> <https://a.example|这里> <!here>"), "e-one")
        await eventually(lambda: run.messages() != [])
        [event] = run.messages()
        assert (event.channel, event.sender_id, event.sender_name) == (GROUP, PERSON, "阿星")
        assert event.mentions == {BOT}
        assert [s.type for s in event.segments] == ["at", "text"]
        assert event.text.endswith("在吗 & #闲聊 这里 @所有人")
        assert slack.acks == ["e-one"]
        [seen] = [e for e in run.events if isinstance(e, ChannelsSeen)]
        assert seen.infos[0].name == "#demo群"
        await slack.push(message("又一句", n=2))
        await eventually(lambda: len(run.messages()) == 2)
        assert len(slack.said("users.info")) == 1  # the name is kept


async def test_direct_messages_deletions_joins_and_odd_events() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        said = message("悄悄话", channel="D0DM")
        await slack.push(said)
        await slack.push({"type": "message", "channel": "C0DEMO"})  # malformed
        await slack.push(message(subtype="channel_topic", text="换了话题"))
        await slack.push(
            {
                "type": "message",
                "subtype": "message_deleted",
                "channel": "D0DM",
                "channel_type": "im",
                "deleted_ts": said["ts"],
                "ts": said["ts"],
            }
        )
        await slack.push(
            {"type": "member_joined_channel", "user": "U0LONG", "channel": "C0DEMO", "event_ts": "5.1"}
        )
        await eventually(lambda: any(isinstance(e, MemberJoined) for e in run.events))
        assert [e.channel for e in run.messages()] == [PRIVATE]
        [gone] = [e for e in run.events if isinstance(e, RecallEvent)]
        assert gone.channel == PRIVATE and gone.message_id == f"D0DM.{said['ts']}"
        [new] = [e for e in run.events if isinstance(e, MemberJoined)]
        assert (new.user_id, new.name) == ("U0LONG", "阿龙")


def test_threads_other_bots_files_and_our_own_words() -> None:
    names = {PERSON: "阿星"}
    threaded = normalize(message("对", thread_ts="5.000001"), GROUP, names)
    assert threaded is not None and threaded.reply_to == "C0DEMO.5.000001"
    starts = message("话头")
    first = normalize({**starts, "thread_ts": starts["ts"]}, GROUP, names)
    assert first is not None and first.reply_to is None
    robot = message(f"<@{BOT}> 部署好了", subtype="bot_message", bot_id="B0CI", username="部署机")
    robot.pop("user")
    other = normalize(robot, GROUP, names, own_bot="B0NEST")
    assert other is not None and other.from_bot and not other.mentions and other.sender_name == "部署机"
    ours = normalize(message("我说的", user=BOT, bot_id="B0NEST"), GROUP, names, own_bot="B0NEST")
    assert ours is not None and ours.from_self and not ours.from_bot
    picture = {"id": "F1", "mimetype": "image/png", "url_private": "https://files.slack.com/a.png"}
    shared = normalize(message("看", subtype="file_share", files=[picture]), GROUP, names)
    assert shared is not None and shared.text == "看[图片]"
    assert shared.segments[1].data["url"] == "https://files.slack.com/a.png"


def test_markup_both_ways() -> None:
    assert parse_text("<@U1|阿马>&lt;b&gt;", {}) == [
        Segment("at", {"id": "U1", "name": "阿马"}),
        Segment("text", {"text": "<b>"}),
    ]
    assert escape("<!channel> a&b") == "&lt;!channel&gt; a&amp;b"
    out = outgoing([Segment("at", {"id": PERSON}), Segment("text", {"text": "嗨 <!everyone>"})])
    assert out.text == f"<@{PERSON}> 嗨 &lt;!everyone&gt;"
    assert outgoing([Segment("at", {"id": "<!channel"})]).text == ""
    assert outgoing([Segment("image", {"file": "C:/secret.png"})]).files == []
    assert split_key("C1.5.000001") == ("C1", "5.000001") and split_key("app:x") is None


async def test_a_file_is_fetched_with_the_token_only_from_slack() -> None:
    slack = FakeSlack()
    slack.files["/a.png"] = PNG
    async with running(slack) as run:
        assert await run.adapter.fetch_media("https://files.slack.com/a.png", 1000) == PNG
        for url, limit in (
            ("https://files.slack.com/a.png", 5),
            ("https://files.slack.com/none.png", 1000),
            ("https://evil.example/a.png", 1000),
            ("http://files.slack.com/a.png", 1000),
        ):
            with pytest.raises(MediaError):
                await run.adapter.fetch_media(url, limit)


# -- what goes out ---------------------------------------------------------------------------------


async def test_what_is_posted_comes_back_once_as_our_own() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        result = await run.adapter.perform(say("你好"))
        assert result.state == "succeeded"
        [own] = run.messages()
        assert own.from_self and own.message_id == result.message_id and own.sender_id == BOT
        assert run.adapter.sent_by_us(own.message_id)
        ts = (result.message_id or "").partition(".")[2]
        await slack.push({**message("你好", user=BOT, bot_id="B0NEST"), "ts": ts})
        await slack.push(message("别人说的", n=9))
        await eventually(lambda: len(run.messages()) == 2)
        assert run.messages()[-1].text == "别人说的"  # Slack's copy of ours was dropped


async def test_an_answer_to_a_thread_goes_into_it() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        await slack.push(message("串里问", thread_ts="5.000001"))
        await eventually(lambda: run.messages() != [])
        await run.adapter.perform(
            send(GROUP, Segment("text", {"text": "答"}), trigger=run.messages()[0].message_id)
        )
        await run.adapter.perform(say("频道里说"))
        first, second = slack.said("chat.postMessage")
        assert first["thread_ts"] == "5.000001" and "thread_ts" not in second


async def test_a_direct_message_is_opened_once() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        await run.adapter.perform(say("一", PRIVATE))
        await run.adapter.perform(say("二", PRIVATE))
        assert len(slack.said("conversations.open")) == 1
        assert {b["channel"] for b in slack.said("chat.postMessage")} == {"D0DM"}
        assert [e.channel for e in run.messages()] == [PRIVATE, PRIVATE]


async def test_a_picture_is_uploaded_and_its_copy_is_the_event() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        data = "base64://" + base64.b64encode(PNG).decode()
        result = await run.adapter.perform(
            send(GROUP, Segment("text", {"text": "给你"}), Segment("image", {"file": data}))
        )
        assert result.state == "succeeded" and slack.uploads == [PNG]
        assert slack.said("files.completeUploadExternal")[0]["channel_id"] == "C0DEMO"
        shared = message(subtype="file_share", user=BOT, bot_id="B0NEST", n=40)
        shared["files"] = [
            {"id": "F0NEW", "mimetype": "image/png", "url_private": "https://files.slack.com/x"}
        ]
        await slack.push(shared)
        await eventually(lambda: len(run.messages()) == 2)
        own = run.messages()[-1]
        assert own.from_self and own.text == "[图片]" and run.adapter.sent_by_us(own.message_id)


async def test_long_text_is_cut_and_refusals_are_said_plainly() -> None:
    slack = FakeSlack()
    async with running(slack) as run:
        await run.adapter.perform(say("字" * 9000))
        assert [len(b["text"]) for b in slack.said("chat.postMessage")] == [4000, 4000, 1000]
        slack.answers["chat.postMessage"] = [(200, {"ok": False, "error": "not_in_channel"}, {})]
        result = await run.adapter.perform(say("一"))
        assert result.state == "failed" and "/invite" in (result.error or "")
        slack.answers["chat.postMessage"] = [(502, {}, {})]
        assert (await run.adapter.perform(say("二"))).state == "unknown"


async def test_a_rate_limit_is_waited_out_once() -> None:
    slack = FakeSlack()
    limited = (429, {"ok": False, "error": "ratelimited"}, {"retry-after": "0.01"})
    async with running(slack) as run:
        slack.answers["chat.postMessage"] = [limited]
        assert (await run.adapter.perform(say("一"))).state == "succeeded"
        slack.answers["chat.postMessage"] = [limited, limited]
        assert (await run.adapter.perform(say("二"))).state == "failed"
        slack.answers["chat.postMessage"] = [(429, {}, {"retry-after": "900"})]
        result = await run.adapter.perform(say("三"))
        assert result.state == "failed" and "900" in (result.error or "")


# -- the connection --------------------------------------------------------------------------------


async def test_a_new_connection_is_opened_when_slack_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nest.adapters.slack.adapter.QUICK_SECONDS", 0.0)
    slack = FakeSlack()
    async with running(slack) as run:
        await slack.sockets[-1].send(json.dumps({"type": "disconnect", "reason": "refresh_requested"}))
        await eventually(lambda: slack.opened == 2 and run.state == "connected")
        assert all(s.state != "reconnecting" for s in run.statuses)  # nothing was wrong
        await slack.sockets[-1].close()
        await eventually(lambda: slack.opened == 3 and run.state == "connected")
        await slack.push(message("还在"))
        await eventually(lambda: run.messages() != [])


async def test_a_socket_that_always_ends_at_once_is_not_hammered() -> None:
    slack = FakeSlack()
    slack.hello = {"type": "disconnect", "reason": "warning"}
    async with running(slack, connect=False) as run:
        await eventually(lambda: run.state == "reconnecting")


async def test_what_only_the_user_can_fix_stops_it() -> None:
    slack = FakeSlack()
    slack.hello = {"type": "disconnect", "reason": "link_disabled"}
    async with running(slack, connect=False) as run:
        await eventually(lambda: run.state == "misconfigured")
        assert run.adapter.status().detail == NO_SOCKET_MODE
    slack = FakeSlack()
    async with running(slack, connect=False, app_token="xapp-another") as run:
        await eventually(lambda: run.state == "auth_failed")
    slack = FakeSlack()
    slack.me = {**slack.me, "user_id": "U0OTHER"}
    async with running(slack, connect=False) as run:
        await eventually(lambda: run.state == "misconfigured")
        assert "U0OTHER" in run.adapter.status().detail
    slack = FakeSlack()
    slack.answers["apps.connections.open"] = [
        (200, {"ok": False, "error": "missing_scope", "needed": "connections:write"}, {})
    ]
    async with running(slack, connect=False) as run:
        await eventually(lambda: run.state == "misconfigured")
        assert "connections:write" in run.adapter.status().detail


async def test_what_was_missed_is_caught_up() -> None:
    slack = FakeSlack()
    now = time.time()
    slack.history["C0DEMO"] = [
        {"type": "message", "user": PERSON, "text": "第二句", "ts": f"{now - 20:.6f}"},
        {"type": "message", "user": PERSON, "text": "第一句", "ts": f"{now - 40:.6f}"},
        {"type": "message", "user": PERSON, "text": "早就有了", "ts": f"{now - 900:.6f}"},
    ]

    async def source() -> list[tuple[ChannelRef, float]]:
        return [(GROUP, now - 60)]

    async with running(slack, backfill_source=source) as run:
        await eventually(lambda: any(isinstance(e, CaughtUp) for e in run.events))
        assert [e.text for e in run.messages()] == ["第一句", "第二句"]
        assert all(e.backfilled for e in run.messages())


async def test_no_token_or_ticket_shows_up_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    slack = FakeSlack()
    async with running(slack) as run:
        await slack.push(message("你好"))
        await eventually(lambda: run.messages() != [])
        slack.answers["chat.postMessage"] = [
            (200, {"ok": False, "error": f"odd {BOT_TOKEN} {APP_TOKEN}"}, {})
        ]
        result = await run.adapter.perform(say("一"))
        assert BOT_TOKEN not in (result.error or "") and APP_TOKEN not in (result.error or "")
        await asyncio.sleep(0.05)
    ours = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith(("nest", "websockets.client"))
    )
    for secret in (BOT_TOKEN, APP_TOKEN, "fixture-ticket"):
        assert secret not in ours


# -- the console -----------------------------------------------------------------------------------


async def test_trying_the_tokens() -> None:
    slack = FakeSlack()
    slack.url = "wss://nowhere.test"
    good = {"token": BOT_TOKEN, "app_token": APP_TOKEN, "proxy": ""}
    found = await probe(good, transport=slack.transport)
    assert found.ok and found.self_id == BOT and "Demo 工作区" in found.detail
    for fields, why in (
        ({}, "都要填"),
        ({**good, "token": APP_TOKEN}, "xoxb-"),
        ({**good, "app_token": BOT_TOKEN}, "xapp-"),
        ({**good, "token": "xoxb-wrong"}, "Bot Token 不对"),
        ({**good, "app_token": "xapp-wrong"}, "App Token 不对"),
        ({**good, "proxy": "socks5://127.0.0.1:1"}, "socks"),
    ):
        tried = await probe(fields, transport=slack.transport)
        assert not tried.ok and why in tried.detail


def test_the_platform_is_listed() -> None:
    spec = PLATFORMS["slack"]
    assert spec.refs(BOT) == {
        "token": f"NEST_SLACK_{BOT}_TOKEN",
        "app_token": f"NEST_SLACK_{BOT}_APP_TOKEN",
    }
    assert can("slack:U1", "comparable") and not can("slack:U1", "reply")
    assert check_user("slack", f" {PERSON} ") == PERSON
    with pytest.raises(ValueError, match="成员 ID"):
        check_user("slack", "阿星")
    with pytest.raises(ValueError, match="xapp-"):
        spec.clean({"token": BOT_TOKEN, "app_token": "abc"})
