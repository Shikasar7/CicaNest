"""主动找你: she comes to the user herself, with a reason and within gentle limits."""

from __future__ import annotations

import json
import time
from typing import Any

from nest.core.events import Segment
from nest.core.life import today_key
from nest.core.outreach import OutreachConfig, OutreachKeeper, from_outreach
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat

TEN = time.mktime((2026, 11, 24, 10, 0, 0, 0, 0, -1))  # a Tuesday morning
SHARE = {
    "from": "08:00",
    "to": "09:00",
    "room": "厨房",
    "doing": "烤饼干",
    "mood": "开心",
    "share": "饼干烤糊了一盘",
}


class Clock:
    def __init__(self, at: float) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


async def keeper(w: World, clock: Clock, **config: Any) -> OutreachKeeper:
    state = w.state
    await state.life.set_on(True)
    state.life.clock = clock
    async with state.db.transaction() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO life_days (day, plan, made_by, created_at) VALUES (?, ?, 'test', ?)",
            (today_key(clock()), json.dumps({WHALE: [SHARE]}, ensure_ascii=False), clock()),
        )
    found = OutreachKeeper(
        state.brain,
        state.desk,
        state.life,
        state.groups,
        desk_open=lambda: True,
        config=OutreachConfig(**config),
        clock=clock,
    )
    found.seen(10)
    return found


async def on_the_clock(w: World, at: float) -> None:
    """Her messages are stored at the real time; the test's clock is another day's: put them on it."""
    async with w.state.db.transaction() as conn:
        await conn.execute(
            "UPDATE messages SET sent_at = ? WHERE external_id LIKE 'app:o:%' AND sent_at > ?", (at, at)
        )


async def hers(w: World, chat: int) -> list[dict[str, Any]]:
    rows = (await w.http.get(f"/api/channels/{chat}/messages")).json()
    return [r for r in rows if from_outreach(r["external_id"])]


async def test_she_shares_her_day_once_and_it_is_remembered(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(TEN)
    outreach = await keeper(world, clock)
    model = world.state.providers.runtimes["ark"].provider
    model.queue("我早上烤饼干烤糊了一盘…你今天吃早饭没？")
    assert await outreach.tick() == 1
    [said] = await hers(world, chat)
    assert said["text"].startswith("我早上烤饼干") and said["from_self"]
    ask = model.requests[-1][1].content
    assert "饼干烤糊了一盘" in ask and "现在是 11月24日 星期二 10:00" in ask
    assert (await world.state.life.moments(WHALE))[0]["text"] == "饼干烤糊了一盘"

    clock.at += 10 * 60  # too soon after her last one
    assert await outreach.tick() == 0
    clock.at += 60 * 60  # the same share is not told twice
    model.queue("不会被用到")
    assert await outreach.tick() == 0


async def test_nothing_while_off_away_or_quiet(world: World) -> None:  # noqa: F811
    await private_chat(world)
    clock = Clock(TEN)
    outreach = await keeper(world, clock)
    outreach.seen(3600)  # an hour away from the computer
    assert await outreach.tick() == 0
    outreach.seen(5)
    await world.state.life.set_on(False)
    assert await outreach.tick() == 0
    await world.state.life.set_on(True)
    clock.at = time.mktime((2026, 11, 24, 23, 30, 0, 0, 0, -1))  # quiet hours, and no late-night 关心 yet
    outreach.seen(600)
    assert await outreach.tick() == 0


async def test_she_may_choose_to_say_nothing(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    outreach = await keeper(world, Clock(TEN))
    world.state.providers.runtimes["ark"].provider.queue("SKIP")
    assert await outreach.tick() == 0
    assert await hers(world, chat) == []
    assert outreach.why_not(WHALE, TEN) == ""  # a SKIP costs no budget


async def test_two_unanswered_and_she_leaves_it_for_the_day(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(time.mktime((2026, 11, 24, 12, 10, 0, 0, 0, -1)))
    outreach = await keeper(world, clock, quiet_chat=0, gap=60)
    model = world.state.providers.runtimes["ark"].provider
    model.queue("分享一下：饼干烤糊了")
    assert await outreach.tick() == 1  # the share first
    await on_the_clock(world, clock.at)
    clock.at += 120
    model.queue("吃午饭了吗？")
    assert await outreach.tick() == 1  # then the meal-time 关心
    await on_the_clock(world, clock.at)
    clock.at = time.mktime((2026, 11, 24, 18, 20, 0, 0, 0, -1))
    model.queue("晚饭呢？")
    assert await outreach.tick() == 0  # two in a row unanswered
    assert len(await hers(world, chat)) == 2


async def test_a_quiet_group_gets_a_topic(world: World) -> None:  # noqa: F811
    for bot in (WHALE, PONY):
        assert (await world.patch_bot(bot, {"mode": "live"})).status_code == 200
    group = (
        await world.http.post("/api/desk/groups", json={"name": "吃货群", "bot_ids": [WHALE, PONY]})
    ).json()
    clock = Clock(TEN)
    outreach = await keeper(world, clock, group_cold=60)
    view = group["view"]
    async with world.state.db.transaction() as conn:  # someone said something long ago
        await conn.execute(
            """INSERT INTO messages (channel_id, external_id, sender_id, sender_name, from_self, text,
                   segments, mentions, sent_at, received_at, source)
               VALUES (?, 'old', 'desk', '我', 0, '早', '[]', '[]', ?, ?, 'live')""",
            (view, TEN - 3600, TEN - 3600),
        )
    outreach.seen(5)
    for model in ("ark", "grok"):  # whichever of them starts it
        world.state.providers.runtimes[model].provider.queue("大家今天打算干嘛呀")
    sent = await outreach._groups(TEN)
    assert sent == 1
    rows = (await world.http.get(f"/api/channels/{view}/messages")).json()
    assert any(from_outreach(r["external_id"]) and r["text"] == "大家今天打算干嘛呀" for r in rows)
    assert await outreach._groups(TEN + 60) == 0  # just did


async def test_she_asks_about_what_you_told_her_and_invites_you_to_play(world: World) -> None:  # noqa: F811
    from nest.core.outreach import games_hint

    chat = await private_chat(world)
    clock = Clock(time.mktime((2026, 11, 25, 15, 0, 0, 0, 0, -1)))
    outreach = await keeper(world, clock, gap=60)
    outreach._done.add((today_key(clock.at), WHALE, SHARE["share"]))  # today's share told already
    async with world.state.db.transaction() as conn:  # a 约定 with a day, learned in her chat with the user
        await conn.execute(
            """INSERT INTO memories
                   (place, person_id, person, bot_id, kind, content, due, created_at, updated_at)
               VALUES (?, '900000009', '阿月', ?, 'promise', '对方要考驾照科目二', '2026-11-24', ?, ?)""",
            (f"chat:{chat}", WHALE, clock.at, clock.at),
        )
    model = world.state.providers.runtimes["ark"].provider
    model.queue("昨天科目二考得怎么样呀？")
    assert await outreach.tick() == 1
    assert "对方要考驾照科目二（昨天）" in model.requests[-1][1].content

    await on_the_clock(world, clock.at)
    clock.at += 120
    outreach.chance = lambda: 0.0  # the dice say: let us play
    model.queue("好无聊呀，来下五子棋吗？[一起玩：五子棋]")
    assert await outreach.tick() == 1
    assert "成语接龙" in model.requests[-1][1].content

    # A game of 「一起玩」 comes as a card after her words, which it is taken out of.
    async def card() -> list[dict[str, Any]] | None:
        rows = (await world.http.get(f"/api/channels/{chat}/messages")).json()
        return rows[-2:] if rows[-1]["text"] == "[邀请你一起玩五子棋]" else None

    said = await eventually(card)
    assert said[0]["text"] == "好无聊呀，来下五子棋吗？"
    assert said[1]["segments"] == [
        {"type": "game", "data": {"kind": "gomoku", "mode": "you", "name": "五子棋"}}
    ]

    # The rules come with her prompt once a game is being talked about.
    assert games_hint([{"text": "来玩成语接龙吧"}]).startswith("（要是你们在玩小游戏")
    assert games_hint([{"text": "今天好累"}]) == ""


async def test_the_count_survives_a_restart(world: World) -> None:  # noqa: F811
    await private_chat(world)
    clock = Clock(TEN)
    outreach = await keeper(world, clock, per_bot_daily=1)
    world.state.providers.runtimes["ark"].provider.queue("分享一下：饼干烤糊了")
    assert await outreach.tick() == 1
    again = await keeper(world, clock, per_bot_daily=1)
    await again._load()
    assert again.why_not(WHALE, clock.at + 3600) == "她今天已经主动找过好几次了"


async def test_quiet_on_the_tray_holds_her_back(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(TEN)
    outreach = await keeper(world, clock)
    outreach.seen(10, 1800)  # 安静一会儿, half an hour left
    assert await outreach.tick() == 0
    response = await world.http.post("/api/desk/presence", json={"idle": 10, "quiet": 0})
    assert response.status_code == 200
    outreach.seen(10, 0)  # talk again
    world.state.providers.runtimes["ark"].provider.queue("早上好呀")
    assert await outreach.tick() == 1
    assert len(await hers(world, chat)) == 1


async def test_an_unanswered_pet_line_holds_her_message_back(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    clock = Clock(TEN)
    outreach = await keeper(world, clock, gap=60)
    bot = world.state.brain.bots[WHALE]
    await world.state.desk.deliver(bot, bot.channels[chat], [Segment("text", {"text": "在忙吗"})], kind="c:")
    async with world.state.db.transaction() as conn:
        await conn.execute("UPDATE messages SET sent_at = ? WHERE external_id LIKE 'app:c:%'", (TEN - 600,))
    world.state.providers.runtimes["ark"].provider.queue("不会被用到")
    assert await outreach.tick() == 0  # her pet was not answered: no message instead
    assert await hers(world, chat) == []


async def test_the_tray_quiet_reaches_nest(world: World) -> None:  # noqa: F811
    response = await world.http.post("/api/desk/presence", json={"idle": 3, "quiet": 900})
    assert response.status_code == 200
    outreach = world.state.outreach
    assert outreach.idle == 3 and outreach.quiet_until > time.time() + 800
    await world.http.post("/api/desk/presence", json={"idle": 3, "quiet": "很久"})  # not a number: not quiet
    assert outreach.quiet_until == 0
