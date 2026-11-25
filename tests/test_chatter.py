"""主动搭话 (伙伴模式): when the desktop app has a moment, a companion may say something of her own —
within her limits (not at night, a few a day, not on top of a conversation, not after being ignored) and
only if her model wants to."""

from __future__ import annotations

import time
from typing import Any

from nest.core.chatter import ChatterConfig, ChatterKeeper
from nest.core.events import Segment
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, World, world  # noqa: F401

NEVER_QUIET = ChatterConfig(quiet_from=0, quiet_until=0)


def model(w: World, provider: str = "ark") -> Any:
    return w.state.providers.get(provider).provider


async def companion(w: World) -> tuple[str, int]:
    made = (await w.http.post("/api/companions", json={"name": "阿星", "provider_id": "ark"})).json()
    return made["bot_id"], made["channel_id"]


def keeper(w: World, config: ChatterConfig = NEVER_QUIET, offset: list[float] | None = None) -> ChatterKeeper:
    shift = offset if offset is not None else [0.0]
    return ChatterKeeper(
        w.state.db, w.state.brain, w.state.desk, config=config, clock=lambda: time.time() + shift[0]
    )


async def lines(w: World, chat: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{chat}/messages")).json()


async def test_she_says_something_of_her_own(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    model(world).queue("在忙什么呀？")
    result = await keeper(world).maybe(bot_id)
    assert result == {"said": True, "reason": ""}
    (row,) = await eventually(lambda: _n(world, chat, 1))
    assert row["from_self"] and row["text"] == "在忙什么呀？" and row["external_id"].startswith("app:c:")
    system, user = model(world).requests[-1]
    assert "阿星" in system.content and "主动跟对方说一句话" in user.content

    # Right after it: not again.
    assert (await keeper(world).maybe(bot_id))["reason"] == "刚聊过或刚说过"


async def test_she_may_not_want_to(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    model(world).queue("SKIP")
    assert await keeper(world).maybe(bot_id) == {"said": False, "reason": "她这次没想说话"}
    assert await lines(world, chat) == []


async def test_ignored_three_times_she_stops_for_the_day(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    offset = [0.0]
    chatty = keeper(world, ChatterConfig(quiet_from=0, quiet_until=0, per_hour=10), offset)
    for n in range(3):
        model(world).queue(f"第{n}句")
        assert (await chatty.maybe(bot_id))["said"]
        await eventually(lambda n=n: _n(world, chat, n + 1))
        offset[0] += 700  # past the gap
    assert (await chatty.maybe(bot_id))["reason"] == "连着 3 句没人回，今天不说了"
    # Her last lines are shown to her so she says something new.
    assert "第0句" in model(world).requests[-1][1].content


async def test_her_limits(world: World) -> None:  # noqa: F811
    bot_id, _ = await companion(world)
    assert (await keeper(world, ChatterConfig(quiet_from=0, quiet_until=24)).maybe(bot_id))["reason"] == (
        "免打扰的时段不主动说话"
    )
    model(world).queue("嗨")
    once = ChatterConfig(quiet_from=0, quiet_until=0, per_day=1, gap_seconds=0)
    assert (await keeper(world, once).maybe(bot_id))["said"]
    await eventually(lambda: model(world).requests)
    assert (await keeper(world, once).maybe(bot_id))["reason"] == "今天已经主动说了 1 句"
    assert (await keeper(world).maybe("qq:404"))["reason"] == "没有这个 bot"
    assert (await keeper(world).maybe(PONY))["reason"] == "不是正式发言"


async def test_the_desktop_app_asks_through_the_api(world: World) -> None:  # noqa: F811
    bot_id, _ = await companion(world)
    response = await world.http.post(f"/api/desk/{bot_id}/chatter")
    assert response.status_code == 200 and set(response.json()) == {"said", "reason"}
    assert (await world.http.post("/api/desk/qq:404/chatter")).status_code == 404


async def _n(w: World, chat: int, n: int) -> list[dict[str, Any]] | None:
    rows = await lines(w, chat)
    return rows if len(rows) >= n else None


async def test_her_unanswered_message_holds_the_pet_back(world: World) -> None:  # noqa: F811
    bot_id, chat = await companion(world)
    bot = world.state.brain.bots[bot_id]
    await world.state.desk.deliver(
        bot, bot.channels[chat], [Segment("text", {"text": "吃饭了吗"})], kind="o:"
    )
    no_gap = ChatterConfig(quiet_from=0, quiet_until=0, gap_seconds=0)
    assert (await keeper(world, no_gap).maybe(bot_id))["reason"] == (
        "她主动发的消息还没人回，不换个法子追着说"
    )
