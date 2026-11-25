"""桌面聊天: talking with a bot from the console or desktop app. It is the bot's private chat with a person
seen through a second window: one history, and the answer to what was typed there is shown there instead of
being sent to the platform (the accounts in these tests have no connection at all)."""

from __future__ import annotations

import time
from typing import Any

from nest.core.desk import DESK_PEER, from_app
from nest.core.events import ChannelRef, MessageEvent, Segment
from tests.helpers import eventually
from tests.test_bot_settings_api import DRAGON, PONY, WHALE, World, world  # noqa: F401

PERSON = "900000009"


async def private_chat(w: World, bot_id: str = WHALE) -> int:
    response = await w.http.post(f"/api/bots/{bot_id}/private", json={"user_id": PERSON})
    assert response.status_code == 201
    assert (await w.patch_bot(bot_id, {"mode": "live"})).status_code == 200
    return response.json()["channel_id"]


async def rows(w: World, channel: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{channel}/messages")).json()


async def test_what_is_typed_here_is_answered_here_in_the_same_private_chat(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    # Something said on the platform earlier: the same history, and where the person's name comes from.
    await world.state.store.save(
        MessageEvent(
            channel=ChannelRef("qq", "10001", "private", PERSON),
            message_id="m1",
            sender_id=PERSON,
            sender_name="阿月",
            segments=(Segment("text", {"text": "晚上好"}),),
            text="晚上好",
            timestamp=time.time() - 60,
        )
    )
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[WHALE]["channel_id"] == chat and listed[WHALE]["chats"][0]["peer"] == PERSON
    assert listed[PONY]["channel_id"] is None and listed[PONY]["chats"] == []

    response = await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "  在吗  "})
    assert response.status_code == 202 and response.json()["channel_id"] == chat
    said = await eventually(lambda: _answered(world, chat))
    earlier, mine, hers = said
    assert earlier["text"] == "晚上好"
    assert (mine["text"], mine["sender_id"], mine["sender_name"]) == ("在吗", PERSON, "阿月")
    assert mine["external_id"] == response.json()["message_id"] and from_app(mine["external_id"])
    assert hers["from_self"] and hers["text"] == "在" and from_app(hers["external_id"])
    decision = await eventually(lambda: [d for kind, d in world.events if kind == "decision"])
    assert (decision[0]["outcome"], decision[0]["reason"]) == ("replied", "已在桌面聊天里回复")
    # Shown here is not "another program speaking as the bot".
    assert WHALE not in world.state.brain.foreign_at


async def _answered(w: World, chat: int) -> list[dict[str, Any]] | None:
    found = await rows(w, chat)
    return found if len(found) == 3 else None


async def test_the_list_shows_the_last_thing_said(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[WHALE]["last"] is None and listed[PONY]["last"] is None  # nothing said / no chat
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "在吗"})
    await eventually(lambda: _replied(world, chat))
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    last = listed[WHALE]["last"]
    assert (last["text"], last["from_self"], last["sticker"]) == ("在", True, False)


async def _replied(w: World, chat: int) -> bool:
    return any(r["from_self"] for r in await rows(w, chat))


async def test_a_message_from_the_platform_is_still_answered_on_the_platform(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    await world.state.hub.emit(
        MessageEvent(
            channel=ChannelRef("qq", "10001", "private", PERSON),
            message_id="m1",
            sender_id=PERSON,
            sender_name="阿月",
            segments=(Segment("text", {"text": "在吗"}),),
            text="在吗",
            timestamp=time.time(),
        )
    )
    decision = await eventually(lambda: [d for kind, d in world.events if kind == "decision"])
    # No connection in these tests: it went to the platform's way out, not to the desk.
    assert (decision[0]["outcome"], decision[0]["reason"]) == ("failed", "这个账号没有在运行，没有发出")
    assert [r["text"] for r in await rows(world, chat)] == ["在吗"]


async def test_a_bot_nobody_talks_with_in_private_gets_a_chat_that_only_exists_here(world: World) -> None:  # noqa: F811
    assert (await world.http.post(f"/api/desk/{PONY}/messages", json={"text": "嗨"})).status_code == 409
    opened = await world.http.post(f"/api/desk/{PONY}/open", json={})
    assert opened.status_code == 201
    chat = opened.json()["channel_id"]
    assert world.bot(PONY).channels[chat].channel == ChannelRef("qq", "10002", "private", DESK_PEER)
    assert (await world.http.post(f"/api/desk/{PONY}/open", json={})).status_code == 409
    assert (await world.http.post(f"/api/desk/{PONY}/open", json={"persona_id": 999})).status_code == 400
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[PONY]["channel_id"] == chat and listed[PONY]["chats"][0]["desk_only"]

    refused = await world.http.post(f"/api/desk/{PONY}/messages", json={"text": "嗨"})
    assert (refused.status_code, refused.json()["detail"]) == (409, "阿马 现在只看不说，不会回话")
    await world.patch_bot(PONY, {"mode": "live"})
    assert (await world.http.post(f"/api/desk/{PONY}/messages", json={"text": "嗨"})).status_code == 202
    said = await eventually(lambda: _pair(world, chat))
    assert [(r["sender_name"], r["text"]) for r in said] == [("我", "嗨"), ("阿马", "在")]


async def _pair(w: World, chat: int) -> list[dict[str, Any]] | None:
    found = await rows(w, chat)
    return found if len(found) == 2 else None


async def test_choosing_which_private_chat_the_window_shows(world: World) -> None:  # noqa: F811
    first = await private_chat(world)
    other = (await world.http.post(f"/api/desk/{WHALE}/open", json={})).json()["channel_id"]
    chosen = await world.http.put(f"/api/desk/{WHALE}/chat", json={"channel_id": other})
    assert chosen.status_code == 200 and chosen.json()["channel_id"] == other
    listed = {b["bot_id"]: b for b in (await world.http.get("/api/desk")).json()}
    assert listed[WHALE]["channel_id"] == other
    assert [c["channel_id"] for c in listed[WHALE]["chats"]] == [first, other]  # the desk-only one last
    group = world.channels["whale@42"]
    assert (await world.http.put(f"/api/desk/{WHALE}/chat", json={"channel_id": group})).status_code == 404


async def test_what_cannot_be_said(world: World) -> None:  # noqa: F811
    await private_chat(world)
    for body, status in (
        ({"text": "   "}, 422),
        ({"text": "a\x00b"}, 422),
        ({}, 422),
        ({"text": "好" * 4001}, 422),
    ):
        assert (await world.http.post(f"/api/desk/{WHALE}/messages", json=body)).status_code == status
    assert (await world.http.post("/api/desk/qq:404/messages", json={"text": "嗨"})).status_code == 404
    assert (await world.http.post(f"/api/desk/{DRAGON}/messages", json={"text": "嗨"})).status_code == 409
    await world.patch_bot(WHALE, {"mode": "paused"})
    paused = await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "嗨"})
    assert (paused.status_code, paused.json()["detail"]) == (409, "阿鲸 已暂停，不会回话")
