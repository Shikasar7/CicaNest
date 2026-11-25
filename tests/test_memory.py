"""长期记忆: bots that have it on in a place learn what people like, how they are and what they agreed with a
bot, from the chat there, and are told it when those people talk — only there, a 约定 only to its bot."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

import pytest

from nest.core.desk import MAX_CHARS
from nest.core.memory import LINE_MAX, READ_MAX, MemoryKeeper, clean_content, clean_due, render_recall
from nest.core.prompt import build_prompt, first_part
from nest.storage.memories import MemoryStore
from nest.storage.settings import SettingsRepo
from tests.test_bot_settings_api import WHALE as API_WHALE
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import GROUP, World, world  # noqa: F401 (fixture)

WHALE, PONY = "qq:10001", "qq:10002"
KEY = f"qq:group:{GROUP}"
TODAY = date(2026, 5, 20)
STAR, MOON = "70001", "70002"


def ops(*items: dict) -> str:
    return json.dumps({"ops": list(items)}, ensure_ascii=False)


@dataclass
class Kit:
    w: World
    keeper: MemoryKeeper
    store: MemoryStore

    async def remember(self, bot: str = WHALE, on: bool = True) -> None:
        channel = next(iter(self.w.brain.bots[bot].channels))
        await self.w.repo.set_channel(bot, channel, remember=on)
        await self.w.brain.load()

    async def chat(self, n: int = 20) -> None:
        await self.w.say("我超喜欢猫，家里有只橘猫", sender=STAR, name="阿星", accounts=["10001"])
        await self.w.say("阿鲸周五记得提醒我交报告", sender=STAR, name="阿星", accounts=["10001"])
        await self.w.say("好的，周五提醒你", sender="10001", name="阿鲸", accounts=["10001"])
        for i in range(n):
            await self.w.say(f"今天第{i}条", sender=MOON, name="阿月", accounts=["10001"])

    def model(self) -> object:
        return self.w.providers["10001"]


@pytest.fixture
async def kit(world: World) -> AsyncIterator[Kit]:  # noqa: F811
    store = MemoryStore(world.db)
    keeper = MemoryKeeper(
        store,
        world.store,
        world.brain.providers,
        world.broadcaster,
        SettingsRepo(world.db),
        bots=world.brain,
        today=lambda: TODAY,
    )
    world.brain.memory = keeper
    k = Kit(world, keeper, store)
    await k.remember(WHALE)
    yield k


# -- learning -------------------------------------------------------------------------------------


async def test_learning_keeps_only_checked_memories(kit: Kit) -> None:
    await kit.chat()
    assert await kit.keeper.due() == [KEY]
    kit.model().queue(  # type: ignore[attr-defined]
        ops(
            {"op": "add", "person": "P1", "kind": "like", "content": "喜欢猫，家里有只橘猫"},
            {
                "op": "add",
                "person": "P1",
                "kind": "promise",
                "content": "周五提醒交报告",
                "bot": "阿鲸",
                "due": "2026-05-22",
            },
            {"op": "add", "person": "P9", "kind": "fact", "content": "没出现过的人"},
            {"op": "add", "person": "P2", "kind": "fact", "content": "手机号 13800001234"},
            {"op": "add", "person": "P2", "kind": "promise", "content": "约了一起吃饭", "bot": "不认识的"},
            {"op": "add", "person": "P2", "kind": "mood", "content": "今天很开心"},
            {"op": "delete", "id": "M7"},
        )
    )
    result = await kit.keeper.learn(KEY)
    assert (result["added"], result["updated"], result["removed"]) == (2, 0, 0)
    assert "丢掉 5 条" in result["detail"] and not result["error"]
    kept = await kit.store.of_place(KEY)
    assert [(m["person_id"], m["kind"], m["content"], m["bot_id"], m["due"]) for m in kept] == [
        (STAR, "like", "喜欢猫，家里有只橘猫", None, None),
        (STAR, "promise", "周五提醒交报告", WHALE, "2026-05-22"),
    ]
    system, ask = kit.model().requests[-1]  # type: ignore[attr-defined]
    assert "阿鲸、阿马、阿龙" in system.content and "2026年5月20日（周三）" in system.content
    assert "P1 阿星：我超喜欢猫" in ask.content and "阿鲸（bot）：好的，周五提醒你" in ask.content
    assert "P2 阿月：今天第0条" in ask.content and "已经记下的" not in ask.content
    assert await kit.keeper.due() == []  # read up to here, and just ran


async def test_the_next_run_reads_only_new_messages_and_may_change_what_it_learned(kit: Kit) -> None:
    await kit.chat()
    kit.model().queue(  # type: ignore[attr-defined]
        ops(
            {"op": "add", "person": "P1", "kind": "like", "content": "喜欢猫"},
            {"op": "add", "person": "P1", "kind": "fact", "content": "在雾港上班"},
        )
    )
    await kit.keeper.learn(KEY)
    [fact] = [m for m in await kit.store.of_place(KEY) if m["kind"] == "fact"]
    await kit.store.update(fact["id"], content="在雾港上班（主人改过）", source="manual", at=time.time())
    await kit.w.say("其实我现在更喜欢狗了", sender=STAR, name="阿星", accounts=["10001"])
    await kit.w.say("狗多好", sender=MOON, name="阿月", accounts=["10001"])
    await kit.w.say("是吧", sender=STAR, name="阿星", accounts=["10001"])
    kit.model().queue(  # type: ignore[attr-defined]
        ops(
            {"op": "update", "id": "M1", "content": "现在更喜欢狗"}, {"op": "delete", "id": "M2"}
        )  # the owner's: left alone
    )
    result = await kit.keeper.learn(KEY, force=True)
    assert (result["updated"], result["removed"]) == (1, 0)
    _, ask = kit.model().requests[-1]  # type: ignore[attr-defined]
    assert "M1 P1 喜好：喜欢猫" in ask.content and "主人写的，不要改" in ask.content
    assert "今天第0条" not in ask.content and "更喜欢狗了" in ask.content
    assert {m["content"] for m in await kit.store.of_place(KEY)} == {"现在更喜欢狗", "在雾港上班（主人改过）"}


async def test_long_messages_of_the_desk_chat_are_read_to_their_end(kit: Kit) -> None:
    long = "字" * (MAX_CHARS - 1) + "完"
    await kit.w.say(long, sender=STAR, name="阿星", accounts=["10001"])
    await kit.chat()
    kit.model().queue(ops())  # type: ignore[attr-defined]
    await kit.keeper.learn(KEY, force=True)
    said = kit.model().requests[-1][1].content  # type: ignore[attr-defined]
    assert said.count("字") == LINE_MAX and "完" not in said  # elsewhere only the beginning


async def test_a_lot_of_long_talk_takes_several_runs(kit: Kit) -> None:
    for n in range(20):
        await kit.w.say(f"第{n}段" + "字" * 3900 + "完", sender=STAR, name="阿星", accounts=["10001"])
    async with kit.w.db.transaction() as conn:
        await conn.execute("UPDATE messages SET external_id = 'app:' || external_id")
    read = []
    for _ in range(2):
        kit.model().queue(ops())  # type: ignore[attr-defined]
        assert not (await kit.keeper.learn(KEY, force=True))["error"]
        read.append(kit.model().requests[-1][1].content)  # type: ignore[attr-defined]
    assert read[0].count("完") == 10 and len(read[0]) < READ_MAX + 500
    assert read[1].count("完") == 10 and "第10段" in read[1] and "第9段" not in read[1]


def test_what_was_said_in_one_moment_is_read_together() -> None:
    rows = [{"text": "字" * 10, "sent_at": at} for at in (1.0, 2.0, 2.0, 3.0)]
    assert len(first_part(rows, lambda _: 10, 20)) == 3
    assert len(first_part(rows, lambda _: 10, 1)) == 1 and first_part([], lambda _: 10, 1) == []
    assert len(first_part(rows, lambda _: 5, 20)) == 4


async def test_a_bad_answer_is_read_again_next_time(kit: Kit) -> None:
    await kit.chat()
    kit.model().queue("我看不出什么")  # type: ignore[attr-defined]
    result = await kit.keeper.learn(KEY)
    assert result["error"] == "模型的回答看不懂"
    kit.model().queue(ops())  # type: ignore[attr-defined]
    await kit.keeper.learn(KEY, force=True)
    _, ask = kit.model().requests[-1]  # type: ignore[attr-defined]
    assert "我超喜欢猫" in ask.content


async def test_nothing_is_learned_where_no_bot_remembers(kit: Kit) -> None:
    await kit.remember(WHALE, on=False)
    await kit.chat()
    assert await kit.keeper.due() == [] and kit.keeper.places() == {}


# -- using ----------------------------------------------------------------------------------------


async def test_a_bot_is_told_what_it_remembers_of_who_talks(kit: Kit) -> None:
    now = time.time()
    await kit.store.add(KEY, STAR, "阿星", "like", "喜欢猫", at=now)
    await kit.store.add(
        KEY, STAR, "阿星", "promise", "周五提醒交报告", bot_id=WHALE, due="2026-05-22", at=now
    )
    await kit.store.add(KEY, STAR, "阿星", "promise", "下次一起唱歌", bot_id=PONY, at=now)
    await kit.store.add("qq:group:999", STAR, "阿星", "fact", "别的群里说的事", at=now)
    kit.w.providers["10001"].queue("在呢")
    await kit.w.say("阿鲸在吗", sender=STAR, name="阿星", to=["10001"])
    await kit.w.settle(1)
    _, ask = kit.w.providers["10001"].requests[0]
    assert "你记得的这些人的事" in ask.content and "今天是2026年5月20日（周三）" in ask.content
    assert "- 阿星：和你约好：周五提醒交报告（5月22日（周五））；喜欢猫" in ask.content
    assert "一起唱歌" not in ask.content and "别的群" not in ask.content
    assert all(m["used_at"] for m in await kit.store.of_place(KEY) if m["bot_id"] != PONY)


async def test_a_bot_that_does_not_remember_here_is_told_nothing(kit: Kit) -> None:
    await kit.store.add(KEY, STAR, "阿星", "like", "喜欢猫", at=time.time())
    pony = kit.w.brain.bots[PONY]
    channel = next(iter(pony.channels.values()))
    assert await kit.keeper.recall(pony, channel, [{"sender_id": STAR, "sender_name": "阿星"}]) == ""


def test_the_prompt_part_and_the_checks() -> None:
    memories = [
        {"id": 1, "person_id": "a", "kind": "fact", "content": "在上学", "due": None, "updated_at": 1.0},
        {"id": 2, "person_id": "a", "kind": "like", "content": "喜欢猫", "due": None, "updated_at": 2.0},
        {"id": 3, "person_id": "b", "kind": "like", "content": "爱吃辣", "due": None, "updated_at": 1.0},
    ]
    text, used = render_recall([("b", "阿乙"), ("a", "阿甲")], memories, limit=1, today=TODAY)
    assert text.splitlines()[1:] == ["- 阿乙：爱吃辣", "- 阿甲：喜欢猫"] and used == [3, 2]
    assert "今天是" not in text  # no 约定, no date
    assert render_recall([("c", "阿丙")], memories, limit=5, today=TODAY) == ("", [])
    assert clean_content("  喜欢  猫 ") == "喜欢 猫"
    assert clean_content("密码是 abc") is None and clean_content("x" * 61) is None
    assert clean_due("2026-05-22", TODAY) == "2026-05-22" and clean_due("下周", TODAY) is None
    assert clean_due("2020-01-01", TODAY) is None
    plain = build_prompt(persona="你是阿鲸", bot_name="阿鲸", context=[], batch=[], called=set(), peers={})
    told = build_prompt(
        persona="你是阿鲸", bot_name="阿鲸", context=[], batch=[], called=set(), peers={}, people="记得"
    )
    assert told[1].content == "记得\n\n" + plain[1].content


# -- cleanup --------------------------------------------------------------------------------------


async def test_old_promises_and_too_many_memories_are_forgotten(kit: Kit) -> None:
    now = time.time()
    add = kit.store.add
    await add(KEY, STAR, "阿星", "promise", "早就过了", bot_id=WHALE, due="2026-05-10", at=now)
    await add(KEY, STAR, "阿星", "promise", "刚过两天", bot_id=WHALE, due="2026-05-18", at=now)
    await add(KEY, STAR, "阿星", "promise", "很久没提", bot_id=WHALE, at=now - 61 * 86400)
    await add(
        KEY, STAR, "阿星", "promise", "主人写的", bot_id=WHALE, due="2026-01-01", source="manual", at=now
    )
    for i in range(14):
        await add(KEY, MOON, "阿月", "like", f"喜好{i}", at=now + i)
    removed = await kit.keeper.tidy(now)
    left = {m["content"] for m in await kit.store.of_place(KEY)}
    assert removed == 4 and {"刚过两天", "主人写的"} <= left
    assert "早就过了" not in left and "很久没提" not in left
    assert "喜好0" not in left and "喜好1" not in left and "喜好13" in left


async def test_what_the_user_added_does_not_take_the_room_of_what_she_learns(kit: Kit) -> None:
    now = time.time()
    add = kit.store.add
    for i in range(12):  # a companion brought in with a dozen points of the user's own
        await add(KEY, MOON, "阿月", "fact", f"主人写的{i}", source="manual", at=now - 1000)
    for i in range(9):
        await add(KEY, MOON, "阿月", "promise", f"约好的{i}", bot_id=WHALE, source="manual", at=now - 1000)
    for i in range(3):
        await add(KEY, MOON, "阿月", "like", f"学到的{i}", at=now + i)
    await add(KEY, MOON, "阿月", "promise", "学到的约定", bot_id=WHALE, at=now)
    assert await kit.keeper.tidy(now) == 0
    left = {m["content"] for m in await kit.store.of_place(KEY)}
    assert {"学到的0", "学到的2", "学到的约定", "主人写的0", "约好的8"} <= left


# -- console --------------------------------------------------------------------------------------


async def test_the_console_lists_edits_and_forgets(api_world: ApiWorld) -> None:  # noqa: F811
    w = api_world
    assert (await w.patch_channel("whale@42", {"remember": True})).status_code == 200
    assert w.channel("whale@42").remember and not w.channel("pony@42").remember
    store = w.state.memory.store
    star = await store.add("qq:group:42", STAR, "阿星", "like", "喜欢猫", at=time.time())
    listing = (await w.http.get("/api/memories")).json()
    [place] = listing["places"]
    assert place["key"] == "qq:group:42" and place["name"] == "Demo群" and place["bots"] == ["阿鲸"]
    assert [m["content"] for m in listing["memories"]] == ["喜欢猫"]

    edited = await w.http.patch(f"/api/memories/{star}", json={"content": "更喜欢狗"})
    assert edited.status_code == 200 and edited.json()["source"] == "manual"
    added = await w.http.post(
        "/api/memories",
        json={
            "place": "qq:group:42",
            "person_id": STAR,
            "kind": "promise",
            "content": "周五提醒",
            "bot_id": API_WHALE,
            "due": "2026-05-22",
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["person"] == "阿星" and added.json()["due"] == "2026-05-22"
    nobody = await w.http.post(
        "/api/memories", json={"place": "qq:group:42", "person_id": "999", "kind": "like", "content": "x"}
    )
    assert nobody.status_code == 404
    bad = await w.http.post(
        "/api/memories",
        json={"place": "qq:group:42", "person_id": STAR, "kind": "promise", "content": "x", "bot_id": "nope"},
    )
    assert bad.status_code == 422

    forgot = await w.http.post("/api/memories/forget", json={"place": "qq:group:42"})
    assert forgot.json() == {"removed": 2}
    assert (await w.http.get("/api/memories")).json()["memories"] == []
    bots = (await w.http.get("/api/bots")).json()
    remembers = {c["channel_id"]: c["remember"] for b in bots if b["id"] == API_WHALE for c in b["channels"]}
    assert remembers == {w.channels["whale@42"]: True, w.channels["whale@77"]: False}
