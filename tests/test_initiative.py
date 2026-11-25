"""主动性的设置: limits and quiet hours set by the user, a cheaper model for small talk and day plans,
the user's own hours (作息), 纪念日, and two companions starting a group of their own; a 桌面群's memory and
黑话 switches."""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from nest.core.events import ChannelRef
from nest.core.groups import PREFIX
from nest.core.initiative import Rhythm, day_of, quiet_now
from nest.core.life import milestone_of, today_key
from nest.llm.base import ProviderError
from nest.llm.purpose import using
from nest.storage.slang import GroupFeatureStore, group_key_of
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat
from tests.test_desk_groups import live, new_group
from tests.test_outreach import TEN, Clock, hers, keeper, on_the_clock

HOUR = 3600.0


def at(day: int, hour: int, minute: int = 0) -> float:
    return time.mktime((2026, 11, day, hour, minute, 0, 0, 0, -1))


async def test_the_limits_and_quiet_hours_are_the_users(world: World) -> None:  # noqa: F811
    http = world.http
    view = (await http.get("/api/life/settings")).json()
    assert view["values"]["quiet_from"] == 23 and view["values"]["chatter_provider"] is None
    assert view["ranges"]["outreach_daily"] == {"default": 6, "min": 0, "max": 50}
    body = {"quiet_from": 22, "quiet_until": 9, "outreach_per_bot": 1, "chatter_per_day": 0}
    assert (await http.put("/api/life/settings", json=body)).status_code == 200
    outreach, chatter = world.state.outreach.config, world.state.chatter.config
    assert (outreach.quiet_from, outreach.quiet_until, outreach.per_bot_daily) == (22, 9, 1)
    assert (chatter.quiet_from, chatter.per_day) == (22, 0)
    assert outreach.daily == 6  # what was not sent stays
    for bad in ({"quiet_from": 24}, {"outreach_daily": 51}, {"life_provider": "nope"}, {"x": 1}):
        assert (await http.put("/api/life/settings", json=bad)).status_code == 422
    # Kept: as it is taken at the next start.
    await world.state.initiative.start()
    assert world.state.outreach.config.per_bot_daily == 1
    assert quiet_now(23, 22, 9) and quiet_now(8, 22, 9) and not quiet_now(9, 22, 9)
    assert quiet_now(13, 12, 14) and not quiet_now(3, 12, 14) and not quiet_now(5, 8, 8)


async def test_small_talk_on_a_cheaper_model(world: World) -> None:  # noqa: F811
    state = world.state
    # Another model, like the ones there.
    made = await world.http.post(
        "/api/providers",
        json={"id": "mini", "kind": "openai_chat", "base_url": "http://m.example/v1", "model": "mini"},
    )
    assert made.status_code == 201, made.text
    put = await world.http.put("/api/life/settings", json={"chatter_provider": "mini"})
    assert put.status_code == 200 and put.json()["values"]["chatter_provider"] == "mini"
    bot = world.bot(WHALE)
    brain = state.brain
    assert brain._model(bot).config.id == "ark"
    with using("chatter"):
        assert brain._model(bot).config.id == "mini"
    with using("life"):
        assert brain._model(bot).config.id == "ark"  # only what it was chosen for
    # It fails: her own answers instead.
    state.providers.get("mini").provider.queue(ProviderError("坏了", retryable=False))
    state.providers.get("ark").provider.queue("我来")
    with using("outreach"):
        from nest.llm.base import ChatMessage

        completion, _, used = await brain._complete(bot, [ChatMessage("user", "x")], max_tokens=10)
    assert completion.text == "我来" and used.config.id == "ark"
    back = await world.http.put("/api/life/settings", json={"chatter_provider": None})
    assert back.json()["values"]["chatter_provider"] is None


# -- 作息 -------------------------------------------------------------------------------------------------


async def test_the_users_usual_hours(world: World) -> None:  # noqa: F811
    rhythm = Rhythm(world.state.db)
    assert rhythm.usual(at(25, 12)) is None
    for day in (16, 17, 18, 19):  # four weekdays, 10 to 1 in the night (the 1 o'clock hour not in it)
        for hour in (*range(10, 24),):
            rhythm.note(at(day, hour, 30), 40)
        rhythm.note(at(day + 1, 0, 30), 30)
        rhythm.note(at(day, 8), 2)  # two minutes are no presence
    assert day_of(at(17, 0, 30)).day == 16  # after midnight belongs to the day before
    usual = rhythm.usual(at(24, 12))
    assert usual is not None and usual.said == "对方平时大约 10 点开电脑，1 点离开。"
    assert rhythm.usual(at(22, 12)) is None  # a Sunday: nothing known of those
    await rhythm.save()
    again = Rhythm(world.state.db)
    await again.load()
    assert again.usual(at(24, 12)) == usual
    await again.forget()
    assert again.usual(at(24, 12)) is None


async def test_late_is_late_for_them(world: World) -> None:  # noqa: F811
    await private_chat(world)
    clock = Clock(at(24, 0, 30))
    found = await keeper(world, clock)
    assert found._late(clock.at, 0)  # nothing known: past midnight
    found.rhythm = Rhythm(world.state.db)
    for day in (16, 17, 18, 19):
        for hour in range(10, 24):
            found.rhythm.note(at(day, hour, 30), 40)
        for hour in (0, 1):
            found.rhythm.note(at(day + 1, hour, 30), 30)
    # They usually leave at 2: half past midnight is not late, half past 3 is, 11 in the morning is not.
    assert not found._late(at(24, 0, 30), 0)
    assert found._late(at(24, 3, 30), 3) and not found._late(at(24, 11), 11)
    found.learns = False
    assert found._late(at(24, 0, 30), 0)


async def test_presence_is_noted_and_a_hello_when_they_are_back(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(TEN)
    found = await keeper(world, clock, play_chance=0.0)
    found._done.add((today_key(TEN), WHALE, "饼干烤糊了一盘"))
    found.rhythm = Rhythm(world.state.db)
    found.seen(5)
    assert found.rhythm.days == {}  # not before it has looked whether 她们的日子 is on
    found.on = True
    clock.at += 60
    found.seen(5)
    clock.at += 60
    found.seen(700)  # idle for long: not at the computer
    assert found.rhythm.days == {"2026-11-24": [1.0 if h == 10 else 0.0 for h in range(24)]}
    assert await found.tick() == 0
    clock.at += 4 * HOUR
    found.seen(3)  # back
    model = world.state.providers.get("ark").provider
    model.queue("回来啦")
    assert await found.tick() == 1
    assert [r["text"] for r in await hers(world, chat)] == ["回来啦"]
    assert "刚回到电脑前" in model.requests[-1][-1].content


# -- 纪念日 -----------------------------------------------------------------------------------------------


def test_the_days_that_are_marked() -> None:
    since = dt.date(2026, 1, 1)
    assert milestone_of(since, dt.date(2026, 4, 11)) == "认识 100 天"
    assert milestone_of(since, dt.date(2026, 4, 12)) == ""
    assert milestone_of(since, dt.date(2028, 1, 1)) == "认识 2 周年"
    assert milestone_of(since, since) == ""
    assert milestone_of(dt.date(2024, 2, 29), dt.date(2025, 3, 1)) == "认识 1 周年"


async def test_a_hundred_days(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(TEN)
    found = await keeper(world, clock, play_chance=0.0)
    life = world.state.life
    first = (await life.view())["people"][0]
    assert first["since"] == dt.date.today().isoformat() and first["milestone"] == ""  # the day she was added
    since = (dt.date.fromtimestamp(TEN) - dt.timedelta(days=100)).isoformat()
    await life.set_profile(WHALE, {"since": since})
    assert await life.milestone(WHALE) == "认识 100 天"
    assert "今天是你和对方认识 100 天的日子" in await life.line(world.bot(WHALE))
    model = world.state.providers.get("ark").provider
    model.queue("一百天啦")
    assert await found.tick() == 1
    assert "认识 100 天" in model.requests[-1][-1].content
    await on_the_clock(world, clock.at)
    clock.at += 2 * HOUR
    model.queue("SKIP")
    await found.tick()
    assert len(await hers(world, chat)) == 1  # once
    assert (await life.set_profile(WHALE, {"since": "去年"}))["since"] == ""


# -- together -----------------------------------------------------------------------------------------------


async def test_two_of_them_start_a_group_of_their_own(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    clock = Clock(TEN)
    found = await keeper(world, clock, play_chance=0.0)
    found.chance = lambda: 0.0
    block = {"from": "09:30", "to": "11:00", "room": "客厅", "doing": "打游戏", "mood": "", "share": ""}
    async with world.state.db.transaction() as conn:
        await conn.execute(
            "UPDATE life_days SET plan = ?",
            (json.dumps({WHALE: [block], PONY: [block]}, ensure_ascii=False),),
        )
    model = world.state.providers.get("ark").provider
    model.queue("这关怎么过呀")
    assert await found._together({WHALE: [block], PONY: [block]}, TEN) == 1
    (group,) = await world.state.groups.groups()
    assert {m["bot_id"] for m in group["members"]} == {WHALE, PONY} and group["created_by"] in (WHALE, PONY)
    ask = model.requests[-1][-1].content
    assert "都在客厅" in ask and "打游戏" in ask
    # Once a day; not from the bedroom, nor alone.
    assert await found._together({WHALE: [block], PONY: [block]}, TEN) == 0
    found._done.clear()
    alone = {WHALE: [block], PONY: [block | {"room": "卧室"}]}
    assert await found._together(alone, TEN) == 0
    # The next time, in the group they have.
    found._sent.clear()
    model.queue("再来一局")
    assert await found._together({WHALE: [block], PONY: [block]}, TEN) == 1
    assert len(await world.state.groups.groups()) == 1


async def test_a_desk_group_remembers_and_learns_when_told(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    group = await new_group(world)
    assert (group["remember"], group["slang"]) == (False, False)
    path = f"/api/desk/groups/{group['id']}"
    changed = (await world.http.patch(path, json={"remember": True, "slang": True})).json()
    assert (changed["remember"], changed["slang"]) == (True, True)
    for member in changed["members"]:
        channel = world.bot(member["bot_id"]).channels[member["channel_id"]]
        assert channel.remember and channel.use_slang
    # One group, whichever account hears it.
    key = f"desk:group:{PREFIX}{group['id']}"
    assert group_key_of(ChannelRef("qq", "10001", "group", f"{PREFIX}{group['id']}")) == key
    assert (await GroupFeatureStore(world.state.db).all())[key].learn_slang
    assert await world.state.memory._viewer(key) in {m["channel_id"] for m in changed["members"]}
    off = (await world.http.patch(path, json={"remember": False})).json()
    assert (off["remember"], off["slang"]) == (False, True)


async def test_the_settings_answer_tells_what_was_noticed(world: World) -> None:  # noqa: F811
    rhythm = world.state.initiative.rhythm
    now = time.time()
    for back in range(1, 15):
        rhythm.note(now - back * 86400, 30)
    view: dict[str, Any] = (await world.http.get("/api/life/settings")).json()
    assert view["rhythm_days"] >= 13 and view["usual"]["weekday"].startswith("你平时大约")
    forgotten = (await world.http.delete("/api/life/settings/rhythm")).json()
    assert forgotten["rhythm_days"] == 0 and forgotten["usual"]["weekday"] == ""
