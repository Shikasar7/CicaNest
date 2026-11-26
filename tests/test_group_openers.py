"""冷场时主动开口 and 群里的小游戏: a companion starts talking in a quiet group, and plays 猜数字 / 海龟汤
there with the answer kept by nest."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from nest.adapters.base import AdapterStatus
from nest.core.group_games import GroupGames
from nest.core.openers import OpenerKeeper, ignored
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401
from tests.test_brain import GROUP, World, world  # noqa: F401
from tests.test_sending import FakeAdapter

NOON = time.mktime((*time.localtime()[:3], 12, 0, 0, 0, 0, -1))
WHALE = "qq:10001"


class Connected(FakeAdapter):
    state = "connected"

    def status(self) -> AdapterStatus:
        return AdapterStatus(self.state, "", 0.0)  # type: ignore[arg-type]


def chances(*values: float) -> Iterator[float]:
    yield from values
    while True:
        yield 0.0


async def setup(w: World, *, daily: int = 3, at: float = NOON) -> tuple[Connected, GroupGames, int]:
    adapter = Connected()
    w.brain.adapters = {WHALE: adapter}
    await w.repo.set_mode(WHALE, "live")
    [channel_id] = w.brain.bots[WHALE].channels
    await w.repo.set_channel(WHALE, channel_id, opener=True, opener_quiet=30, opener_daily=daily)
    await w.brain.load()
    games = GroupGames(w.db, w.brain)
    w.brain.group_games = games
    await w.say("今天谁也不说话", accounts=["10001"])
    async with w.db.transaction() as conn:
        await conn.execute("UPDATE messages SET sent_at = ?", (at - 3 * 3600,))
    return adapter, games, channel_id


def keeper(w: World, games: GroupGames, *picks: float, at: float = NOON) -> OpenerKeeper:
    rolls = chances(*picks)
    return OpenerKeeper(w.db, w.brain, games, clock=lambda: at, chance=lambda: next(rolls))


async def test_a_quiet_group_gets_a_line_from_her(world: World) -> None:  # noqa: F811
    adapter, games, channel_id = await setup(world, daily=1)
    world.providers["10001"].queue("好安静啊……有没有人陪本喵聊会儿")
    said = await keeper(world, games, 0.0).check()
    assert said == [(WHALE, channel_id, "chat")]
    [action] = adapter.actions
    assert action.channel.id == GROUP and action.segments[0].data["text"] == "好安静啊……有没有人陪本喵聊会儿"
    system, user = world.providers["10001"].requests[0]
    assert "你是阿鲸" in system.content and "群里有3 个小时没人说话了" in user.content
    assert "小明：今天谁也不说话" in user.content
    [decision] = await world.decisions("10001")
    assert decision["trigger"] == "opener" and decision["outcome"] == "replied"
    assert "主动找话题" in decision["reason"]
    # Once a day here: no more today.
    assert await keeper(world, games, 0.0).check() == []


async def test_she_keeps_to_the_daytime_and_a_connected_account(world: World) -> None:  # noqa: F811
    night = NOON - 9 * 3600  # 03:00
    adapter, games, _ = await setup(world, at=night)
    assert await keeper(world, games, at=night).check() == []
    adapter.state = "connecting"
    assert await keeper(world, games).check() == []
    assert adapter.actions == []
    # Said nothing (SKIP, the default): nothing sent, nothing counted.
    adapter.state = "connected"
    assert await keeper(world, games).check() == []
    assert adapter.actions == [] and world.providers["10001"].requests


async def test_not_after_two_of_hers_nobody_answered() -> None:
    hers = [{"from_self": True, "sent_at": 100.0}, {"from_self": True, "sent_at": 200.0}]
    assert ignored(hers, [100.0, 200.0])
    assert not ignored([*hers, {"from_self": False, "sent_at": 150.0}], [100.0, 200.0])
    assert not ignored(hers, [200.0])


async def test_she_invites_the_group_to_guess_her_number_and_runs_the_game(world: World) -> None:  # noqa: F811
    adapter, games, channel_id = await setup(world)
    games.chance = lambda: 0.41  # the number: 42
    model = world.providers["10001"]
    model.queue("好无聊！来玩猜数字吧，1 到 100，猜中有奖喵")
    # a game, and 猜数字 (the first of the kinds)
    said = await keeper(world, games, 0.9, 0.1).check()
    assert said == [(WHALE, channel_id, "game:number")]
    game = games.active(WHALE, channel_id)
    assert game is not None and game["number"] == 42 and game["status"] == "invited"
    _, invite = model.requests[0]
    assert "你心里的数是 42" in invite.content and "猜数字" in invite.content

    # While it is on, a guess is for her without her name, and she is told how it compares.
    model.queue("小了喵")
    await world.say("40", accounts=["10001"])
    await world.settle(2)
    _, user = model.requests[1]
    assert "小明 猜了 40：小了" in user.content and "你心里的数是 42" in user.content
    assert games.active(WHALE, channel_id)["status"] == "on"  # type: ignore[index]

    model.queue("哼，居然被你猜中了！[游戏结束]")
    await world.say("42", accounts=["10001"])
    await world.settle(3)
    _, user = model.requests[2]
    assert "小明 猜了 42：猜中了！" in user.content
    assert adapter.actions[-1].segments[0].data["text"] == "哼，居然被你猜中了！"
    assert games.active(WHALE, channel_id) is None
    # Over: a plain message is not for her any more.
    await world.say("好玩", accounts=["10001"])
    await world.settle(3)
    assert len(model.requests) == 3


async def test_asked_for_a_turtle_soup_she_makes_one_up_and_keeps_the_answer(world: World) -> None:  # noqa: F811
    adapter, games, channel_id = await setup(world)
    model = world.providers["10001"]
    puzzle = {
        "title": "半夜的猫",
        "surface": "她每天半夜都对着空墙叫。",
        "bottom": "墙里有只老鼠。",
        "keys": ["老鼠", "墙里"],
        "hints": ["不是鬼"],
    }
    model.queue(json.dumps(puzzle, ensure_ascii=False))
    model.queue("来喝汤！她每天半夜都对着空墙叫，为什么？")
    await world.say("阿鲸 来玩海龟汤", accounts=["10001"])
    await world.settle(1)
    _, user = model.requests[-1]
    assert "汤底（只有你知道" in user.content and "墙里有只老鼠" in user.content
    assert games.active(WHALE, channel_id)["kind"] == "soup"  # type: ignore[index]
    assert adapter.actions[-1].segments[0].data["text"].startswith("来喝汤")
    # A game nobody plays for half an hour is over.
    games.clock = lambda: time.time() + 31 * 60
    assert games.active(WHALE, channel_id) is None


async def test_the_switch_and_its_numbers_are_set_per_group(api_world: ApiWorld) -> None:  # noqa: F811
    body = {"opener": True, "opener_quiet": 60, "opener_daily": 2}
    assert (await api_world.patch_channel("whale@42", body)).status_code == 200
    channel = api_world.channel("whale@42")
    assert (channel.opener, channel.opener_quiet, channel.opener_daily) == (True, 60, 2)
    bots = (await api_world.http.get("/api/bots")).json()
    [listed] = [c for b in bots if b["id"] == WHALE for c in b["channels"] if c["group_id"] == "42"]
    assert (listed["opener"], listed["opener_quiet"], listed["opener_daily"]) == (True, 60, 2)
    for wrong in ({"opener_quiet": 10}, {"opener_daily": 0}, {"opener_daily": 11}):
        assert (await api_world.patch_channel("whale@42", wrong)).status_code == 422
