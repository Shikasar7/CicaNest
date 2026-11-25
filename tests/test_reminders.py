"""提醒 (伙伴模式): reminders made in the console or asked for in the chat with the user, going off in the
companion's own words at their time — on the desk, or on QQ when the desktop app is away — once more if
unanswered, and again at the next time when they repeat."""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

import pytest

from nest.core.reminders import ASKS, next_due, parse_request, when_label
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401

PERSON = "900000009"


def at(text: str) -> float:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M").astimezone().timestamp()


def model(w: World, provider: str = "ark") -> Any:
    return w.state.providers.get(provider).provider


async def users_chat(w: World) -> int:
    chat = (await w.http.post(f"/api/bots/{WHALE}/private", json={"user_id": PERSON})).json()["channel_id"]
    await w.patch_bot(WHALE, {"mode": "live"})
    return chat


async def said(w: World, chat: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{chat}/messages")).json()


def local_input(seconds_from_now: float) -> str:
    return f"{dt.datetime.fromtimestamp(time.time() + seconds_from_now):%Y-%m-%dT%H:%M}"


# -- times ------------------------------------------------------------------------------------------------


def test_the_next_time_of_a_repeating_reminder() -> None:
    friday = at("2026-11-27 09:00")
    assert next_due(friday, "", friday + 1) is None
    assert next_due(friday, "daily", friday + 1) == at("2026-11-28 09:00")
    assert next_due(friday, "weekdays", friday + 1) == at("2026-11-30 09:00")  # Monday
    assert next_due(friday, "weekly", friday + 1) == at("2026-12-04 09:00")
    assert next_due(at("2027-01-31 08:00"), "monthly", at("2027-01-31 08:01")) == at("2027-02-28 08:00")
    # Missed for days (nest was down): the next one still to come, none made up.
    assert next_due(friday, "daily", at("2026-12-02 10:00")) == at("2026-12-03 09:00")


def test_how_a_time_is_said() -> None:
    now = at("2026-11-23 10:00")  # a Monday
    assert when_label(at("2026-11-23 15:00"), now) == "今天 15:00"
    assert when_label(at("2026-11-24 09:30"), now) == "明天 09:30"
    assert when_label(at("2026-11-27 20:00"), now) == "11月27日（星期五） 20:00"


def test_reading_what_the_model_made_of_a_request() -> None:
    now = at("2026-11-23 10:00")
    asked = parse_request(
        '```json\n{"remind": true, "when": "2026-11-23 15:00", "what": "开会", "repeat": "", '
        '"important": true}\n```',
        now,
    )
    assert asked == (at("2026-11-23 15:00"), "开会", "", True)
    assert parse_request('{"remind": false}', now) is None
    assert parse_request('{"remind": true, "when": "下午三点", "what": "开会"}', now) is None
    assert parse_request('{"remind": true, "when": "2026-11-22 15:00", "what": "开会"}', now) is None  # past
    every = parse_request(
        '{"remind": true, "when": "2026-11-22 08:00", "what": "吃药", "repeat": "daily"}', now
    )
    assert every == (at("2026-11-24 08:00"), "吃药", "daily", False)
    assert parse_request("我看不懂", now) is None


@pytest.mark.parametrize(
    ("text", "asks"),
    [("明天三点提醒我开会", True), ("半小时后叫我", True), ("记得周五交报告", True), ("今天好累啊", False)],
)
def test_which_messages_are_read_for_a_request(text: str, asks: bool) -> None:
    assert bool(ASKS.search(text)) is asks


# -- in the console --------------------------------------------------------------------------------------


async def test_making_changing_and_removing_reminders(world: World) -> None:  # noqa: F811
    chat = await users_chat(world)
    body = {"bot_id": WHALE, "text": "喝水", "due": local_input(3600), "repeat": "daily", "also_qq": True}
    made = await world.http.post("/api/reminders", json=body)
    assert made.status_code == 201
    listed = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()
    (row,) = listed["reminders"]
    assert (row["text"], row["repeat"], row["also_qq"], row["active"], row["channel_id"]) == (
        "喝水",
        "daily",
        True,
        True,
        chat,
    )
    assert row["due"] == body["due"] and row["when"].startswith(("今天", "明天"))
    assert listed["desk_open"] is False
    reminder = made.json()["id"]
    assert (await world.http.patch(f"/api/reminders/{reminder}", json={"active": False})).status_code == 200
    # A daily one switched back on after its time has passed goes off at its next time.
    past = local_input(-7200)
    patched = await world.http.patch(f"/api/reminders/{reminder}", json={"due": past, "active": True})
    assert patched.status_code == 200
    row = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()["reminders"][0]
    assert row["active"] and row["due_at"] > time.time()
    assert (await world.http.delete(f"/api/reminders/{reminder}")).status_code == 200
    assert (await world.http.delete(f"/api/reminders/{reminder}")).status_code == 404

    for wrong, status in (
        ({"bot_id": WHALE, "text": "喝水", "due": local_input(-7200)}, 422),  # a one-off in the past
        ({"bot_id": WHALE, "text": "喝水", "due": "明天"}, 422),
        ({"bot_id": WHALE, "text": "a\nb", "due": local_input(3600)}, 422),
        ({"bot_id": WHALE, "text": "喝水", "due": local_input(3600), "repeat": "hourly"}, 422),
        ({"bot_id": "qq:404", "text": "喝水", "due": local_input(3600)}, 404),
        ({"bot_id": PONY, "text": "喝水", "due": local_input(3600)}, 409),  # no chat with the user yet
    ):
        assert (await world.http.post("/api/reminders", json=wrong)).status_code == status, wrong


async def test_the_desktop_app_says_it_is_open(world: World) -> None:  # noqa: F811
    await users_chat(world)
    assert (await world.http.post("/api/desk/presence")).status_code == 200
    listed = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()
    assert listed["desk_open"] is True


# -- going off -------------------------------------------------------------------------------------------


async def test_a_reminder_goes_off_in_her_words_and_once_more_if_unanswered(world: World) -> None:  # noqa: F811
    chat = await users_chat(world)
    keeper = world.state.reminders
    store = keeper.store
    once = await store.add(bot_id=WHALE, channel_id=chat, text="开会", due_at=time.time() - 1)
    daily = await store.add(
        bot_id=WHALE, channel_id=chat, text="喝水", due_at=time.time() - 1, repeat="daily"
    )
    model(world).queue("该去开会啦，别迟到。", "喝口水吧~")
    # The desktop app is away and these accounts have no connection: QQ cannot take it, the desk keeps it.
    assert await keeper.check() == 2
    rows = await eventually(lambda: _count(world, chat, 2))
    assert [(r["from_self"], r["text"]) for r in rows] == [
        (True, "该去开会啦，别迟到。"),
        (True, "喝口水吧~"),
    ]
    assert "开会" in model(world).requests[0][1].content  # what to remind of, asked in her persona's voice
    assert (await store.get(once))["active"] is False
    after = await store.get(daily)
    assert after["active"] and after["due_at"] > time.time() + 3600
    assert any(kind == "reminder" for kind, _ in world.events)

    # Ten minutes on, no word from the user: once more (only for the ones not answered).
    await store.update(once, nudge_at=time.time() - 1)
    model(world).queue("开会别忘了哦。")
    assert await keeper.check() == 1
    await eventually(lambda: _count(world, chat, 3))
    await store.update(daily, nudge_at=time.time() - 1)
    await world.state.store.save(_from_user(chat_ref(world, chat), "好的"))
    assert await keeper.check() == 0
    assert (await store.get(daily))["nudge_at"] is None


async def test_a_reminder_whose_model_fails_still_goes_off(world: World) -> None:  # noqa: F811
    chat = await users_chat(world)
    await world.state.reminders.store.add(bot_id=WHALE, channel_id=chat, text="吃药", due_at=time.time() - 1)
    model(world).queue(RuntimeError("down"), RuntimeError("down"))
    model(world).default = ""
    world.state.providers.get("ark").retry_delay = 0
    await world.state.reminders.check()
    rows = await eventually(lambda: _count(world, chat, 1))
    assert rows[0]["text"] in ("该吃药了哦。", "在")


async def _count(w: World, chat: int, n: int) -> list[dict[str, Any]] | None:
    rows = await said(w, chat)
    return rows if len(rows) >= n else None


def chat_ref(w: World, chat: int) -> Any:
    return w.bot(WHALE).channels[chat].channel


def _from_user(ref: Any, text: str) -> Any:
    from nest.core.events import MessageEvent, Segment

    return MessageEvent(
        channel=ref,
        message_id=f"m{time.time()}",
        sender_id=PERSON,
        sender_name="阿月",
        segments=(Segment("text", {"text": text}),),
        text=text,
        timestamp=time.time(),
    )


# -- asked for in the chat --------------------------------------------------------------------------------


async def test_asking_for_a_reminder_in_the_chat(world: World) -> None:  # noqa: F811
    await users_chat(world)
    due = dt.datetime.fromtimestamp(time.time() + 86400).replace(hour=15, minute=0, second=0, microsecond=0)
    request = {
        "remind": True,
        "when": f"{due:%Y-%m-%d %H:%M}",
        "what": "开会",
        "repeat": "",
        "important": False,
    }
    model(world).queue(json.dumps(request, ensure_ascii=False), "好，明天三点叫你。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "明天下午三点提醒我开会"})
    await eventually(lambda: len(model(world).requests) >= 2)
    read, answer = model(world).requests[:2]
    assert "提醒" in read[0].content and "明天下午三点提醒我开会" in read[1].content
    assert "你刚帮对方记下了提醒：明天 15:00「开会」" in answer[1].content
    (row,) = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()["reminders"]
    assert (row["text"], row["source"], row["due_at"]) == ("开会", "chat", due.astimezone().timestamp())

    # Small talk is not read at all.
    model(world).requests.clear()
    model(world).queue("嗯嗯")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "今天好累啊"})
    await eventually(lambda: model(world).requests)
    assert len(model(world).requests) == 1 and "记下了提醒" not in model(world).requests[0][1].content
