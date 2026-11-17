"""生日祝福: on a member's birthday (as their profile shows it), one bot of the group that has it on wishes
them once a year, @ing them, in its own words; profiles are kept and asked again only now and then."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from nest.core import birthday
from nest.core.birthday import BirthdayKeeper, born_on, line, recheck_after
from nest.core.events import ActionResult, Member
from nest.llm.base import Completion, ProviderError
from nest.storage.birthdays import BirthdayStore
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import GROUP, World, world  # noqa: F401 (fixture)
from tests.test_sending import FakeAdapter

WHALE, PONY = "qq:10001", "qq:10002"
KEY = f"qq:group:{GROUP}"
MORNING = dt.datetime(2026, 5, 20, 10, 0)  # the 20th of May, local time, after WISH_HOUR
STAR, MOON, CLOUD = "70001", "70002", "70003"


class Profiles(FakeAdapter):
    """An account that can list the group and read profiles: (month, day), (0, 0) when not shown."""

    def __init__(self, members: list[Member], birthdays: dict[str, tuple[int, int]]) -> None:
        super().__init__()
        self.members = members
        self.birthdays = birthdays
        self.asked: list[str] = []
        self.fails = False

    async def group_members(self, group: str) -> list[Member]:
        assert group == GROUP
        return self.members

    async def birthday(self, user: str) -> tuple[int, int]:
        self.asked.append(user)
        if self.fails:
            raise ConnectionError("连不上")
        return self.birthdays.get(user, (0, 0))


@dataclass
class Kit:
    w: World
    keeper: BirthdayKeeper
    store: BirthdayStore
    adapters: dict[str, Profiles]
    now: list[float]
    slept: list[float] = field(default_factory=list)

    async def turn_on(self, bot: str = WHALE, on: bool = True) -> None:
        channel = next(iter(self.w.brain.bots[bot].channels))
        await self.w.repo.set_channel(bot, channel, birthday=on)
        await self.w.brain.load()

    async def mode(self, bot: str, mode: str) -> None:
        await self.w.repo.set_mode(bot, mode)
        await self.w.brain.load()

    def at(self, when: dt.datetime) -> None:
        self.now[0] = when.timestamp()

    async def decisions(self) -> list[dict]:
        return [d for d in await self.w.decisions() if d["trigger"] == "birthday"]

    def sent(self, bot: str = WHALE) -> list[list[tuple[str, str]]]:
        return [
            [(s.type, str(s.data.get("id") or s.data.get("text"))) for s in action.segments]
            for action in self.adapters[bot].actions
        ]


@pytest.fixture
async def kit(world: World) -> AsyncIterator[Kit]:  # noqa: F811
    members = [
        Member(STAR, "阿星✨"),
        Member(MOON, "阿月"),
        Member(CLOUD, "阿云"),
        Member("10002", "阿马"),  # one of ours
        Member("70009", "别家机器人", robot=True),
    ]
    birthdays = {STAR: (5, 20), MOON: (0, 0), CLOUD: (11, 3), "70009": (5, 20), "10002": (5, 20)}
    adapters = {bot: Profiles(members, birthdays) for bot in (WHALE, PONY)}
    adapters[PONY].birthdays = adapters[WHALE].birthdays
    world.brain.adapters = adapters
    await world.repo.set_mode(WHALE, "live")
    now = [MORNING.timestamp()]
    store = BirthdayStore(world.db)
    k = Kit(world, None, store, adapters, now)  # type: ignore[arg-type]

    async def sleep(seconds: float) -> None:
        k.slept.append(seconds)

    k.keeper = BirthdayKeeper(store, world.brain, clock=lambda: now[0], sleep=sleep, chance=lambda: 0.0)
    await k.turn_on(WHALE)
    yield k


async def test_a_member_is_wished_on_their_birthday_with_an_at_in_the_bots_words(kit: Kit) -> None:
    kit.w.providers["10001"].queue("「生日快乐呀，又长大一岁啦」\n多余的一行")
    made = await kit.keeper.check()
    assert made == [(KEY, STAR, "replied")]
    assert kit.sent() == [[("at", STAR), ("text", " 生日快乐呀，又长大一岁啦")]]
    [decision] = await kit.decisions()
    assert decision["bot_id"] == WHALE and decision["outcome"] == "replied"
    assert decision["reply"] == "@阿星 生日快乐呀，又长大一岁啦"
    system, ask = kit.w.providers["10001"].requests[0]
    assert "你是阿鲸" in system.content and "「阿星」的生日" in ask.content and "✨" not in ask.content


async def test_no_birthday_shown_ours_and_robots_are_left_alone(kit: Kit) -> None:
    await kit.keeper.check()
    # Every person was looked up once (not ours, not robots); the one born today once more before the wish.
    assert sorted(kit.adapters[WHALE].asked) == sorted([STAR, MOON, CLOUD, STAR])
    assert [d["reply"] for d in await kit.decisions()] == ["@阿星 生日快乐！"]  # the model said SKIP


async def test_each_person_is_wished_once_a_year(kit: Kit) -> None:
    await kit.keeper.check()
    assert await kit.keeper.check() == []
    kit.at(MORNING + dt.timedelta(hours=3))
    assert await kit.keeper.check() == []
    assert len(kit.adapters[WHALE].actions) == 1
    kit.at(MORNING.replace(year=2027))
    assert await kit.keeper.check() == [(KEY, STAR, "replied")]


async def test_profiles_are_kept_and_asked_again_only_after_a_while(kit: Kit) -> None:
    kit.at(MORNING - dt.timedelta(days=1))
    await kit.keeper.check()
    assert sorted(kit.adapters[WHALE].asked) == sorted([STAR, MOON, CLOUD])
    kit.adapters[WHALE].asked.clear()
    kit.at(MORNING - dt.timedelta(hours=12))
    await kit.keeper.check()
    assert kit.adapters[WHALE].asked == []
    kit.at(MORNING + dt.timedelta(days=60))
    await kit.keeper.check()
    assert sorted(kit.adapters[WHALE].asked) == sorted([STAR, MOON, CLOUD])


async def test_a_big_group_is_looked_up_slowly_and_over_several_passes(
    kit: Kit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(birthday, "MAX_LOOKUPS", 2)
    kit.at(MORNING - dt.timedelta(days=1))
    await kit.keeper.check()
    assert len(kit.adapters[WHALE].asked) == 2 and kit.slept == [birthday.LOOKUP_GAP] * 2
    await kit.keeper.check()
    assert len(kit.adapters[WHALE].asked) == 3  # the one left
    assert len(set(kit.adapters[WHALE].asked)) == 3


async def test_nothing_is_said_before_the_morning_but_profiles_are_read(kit: Kit) -> None:
    kit.at(MORNING.replace(hour=birthday.WISH_HOUR - 1))
    assert await kit.keeper.check() == []
    assert len(kit.adapters[WHALE].asked) == 3 and await kit.decisions() == []
    kit.at(MORNING)
    assert await kit.keeper.check() == [(KEY, STAR, "replied")]


async def test_a_birthday_hidden_since_it_was_read_is_not_wished(kit: Kit) -> None:
    kit.at(MORNING - dt.timedelta(days=2))
    await kit.keeper.check()
    kit.adapters[WHALE].birthdays[STAR] = (0, 0)
    kit.at(MORNING)
    assert await kit.keeper.check() == []
    assert kit.adapters[WHALE].actions == []


async def test_one_bot_wishes_though_several_have_it_on(kit: Kit) -> None:
    await kit.mode(PONY, "live")
    await kit.turn_on(PONY)
    assert {bot.id for bot, _ in kit.w.brain.birthday_wishers()[KEY]} == {WHALE, PONY}
    await kit.keeper.check()
    await kit.keeper.check()
    assert len(kit.adapters[WHALE].actions) + len(kit.adapters[PONY].actions) == 1
    assert len(await kit.decisions()) == 1


async def test_an_observing_bot_only_notes_it_and_a_live_one_still_wishes_that_day(kit: Kit) -> None:
    await kit.mode(WHALE, "observe")
    assert await kit.keeper.check() == [(KEY, STAR, "would_reply")]
    assert kit.adapters[WHALE].actions == []
    [noted] = await kit.decisions()
    assert noted["outcome"] == "would_reply" and "只看不说" in noted["reason"]
    assert await kit.keeper.check() == []  # noted once
    await kit.mode(PONY, "live")
    await kit.turn_on(PONY)
    assert await kit.keeper.check() == [(KEY, STAR, "replied")]
    assert len(kit.adapters[PONY].actions) == 1


async def test_a_live_bot_is_chosen_over_an_observing_one(kit: Kit) -> None:
    await kit.mode(WHALE, "observe")
    await kit.mode(PONY, "live")
    await kit.turn_on(PONY)
    await kit.keeper.check()
    assert kit.adapters[WHALE].actions == [] and len(kit.adapters[PONY].actions) == 1


async def test_off_paused_or_not_running_means_no_wish(kit: Kit) -> None:
    await kit.turn_on(WHALE, on=False)
    assert kit.w.brain.birthday_wishers() == {}
    await kit.turn_on(WHALE)
    await kit.mode(WHALE, "paused")
    assert kit.w.brain.birthday_wishers() == {}
    await kit.mode(WHALE, "live")
    del kit.w.brain.adapters[WHALE]  # type: ignore[attr-defined]
    assert await kit.keeper.check() == []


async def test_a_failed_send_is_tried_again_a_few_times(kit: Kit) -> None:
    kit.adapters[WHALE].result = ActionResult("failed", error="被禁言")
    for _ in range(birthday.TRIES + 2):
        await kit.keeper.check()
    assert len(kit.adapters[WHALE].actions) == birthday.TRIES
    assert all(d["outcome"] == "failed" for d in await kit.decisions())


async def test_an_account_that_does_not_answer_is_left_for_the_next_check(kit: Kit) -> None:
    kit.adapters[WHALE].fails = True
    assert await kit.keeper.check() == []
    kit.adapters[WHALE].fails = False
    assert await kit.keeper.check() == [(KEY, STAR, "replied")]


async def test_a_failing_model_still_wishes_with_the_plain_line(kit: Kit) -> None:
    kit.w.providers["10001"].queue(*(ProviderError("挂了", retryable=False) for _ in range(3)))
    await kit.keeper.check()
    [decision] = await kit.decisions()
    assert decision["reply"] == "@阿星 生日快乐！" and "默认的一句" in decision["reason"]


async def test_a_line_cut_off_is_not_said_half(kit: Kit) -> None:
    kit.w.providers["10001"].queue(Completion(text="祝你生日快乐，愿你", latency_ms=1.0, cut=True))
    await kit.keeper.check()
    assert [d["reply"] for d in await kit.decisions()] == ["@阿星 生日快乐！"]


async def test_the_bot_is_shown_its_last_wishes_so_it_says_something_new(kit: Kit) -> None:
    kit.w.providers["10001"].queue("生日快乐，多吃块蛋糕", "生日快乐，今天你最大")
    await kit.keeper.check()
    kit.adapters[WHALE].birthdays[CLOUD] = (7, 19)  # read again: the old one is long due
    kit.at(dt.datetime(2026, 7, 19, 11, 0))
    await kit.keeper.check()
    first, again = (request[1].content for request in kit.w.providers["10001"].requests)
    assert "前几次" not in first
    assert "“生日快乐，多吃块蛋糕”" in again and "换个说法" in again and "「阿云」" in again


def test_the_answer_becomes_one_line_that_wishes() -> None:
    assert line("「生日快乐呀」\n第二行") == "生日快乐呀"
    assert line("@阿星 今天你最大！") == "生日快乐！今天你最大！"
    assert line("SKIP") == "" and line("  \n") == ""
    assert len(line("生日快乐" + "啊" * 200)) == birthday.MAX_CHARS


def test_born_on_and_29_february() -> None:
    assert born_on(5, 20, dt.date(2026, 5, 20)) and not born_on(5, 21, dt.date(2026, 5, 20))
    assert not born_on(0, 0, dt.date(2026, 5, 20))
    assert born_on(2, 29, dt.date(2027, 2, 28)) and not born_on(2, 29, dt.date(2028, 2, 28))
    assert born_on(2, 29, dt.date(2028, 2, 29))


def test_lookups_fall_due_spread_out() -> None:
    days = [recheck_after(roll) / birthday.DAY for roll in (0.0, 0.5, 0.999)]
    assert days[0] < days[1] == birthday.FRESH_DAYS < days[2]


async def test_the_switch_is_in_the_bots_group_settings(api_world: ApiWorld) -> None:  # noqa: F811
    assert (await api_world.patch_channel("whale@42", {"birthday": True})).status_code == 200
    assert api_world.channel("whale@42").birthday
    [channel] = [
        c
        for b in (await api_world.http.get("/api/bots")).json()
        if b["id"] == WHALE
        for c in b["channels"]
        if c["channel_id"] == api_world.channels["whale@42"]
    ]
    assert channel["birthday"] is True
    pony = [
        c for b in (await api_world.http.get("/api/bots")).json() if b["id"] == PONY for c in b["channels"]
    ]
    assert [c["birthday"] for c in pony] == [False]  # off by default
