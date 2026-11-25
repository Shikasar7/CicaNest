"""小窝和她的日子: profiles, the house's day, what she is doing in her chats with the user."""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from nest.core.life import (
    block_at,
    clean_blocks,
    clean_profile,
    default_day,
    festival_of,
    parse_plan,
    pose_of,
)
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat


def at(hour: int, minute: int = 0) -> float:
    return time.mktime((2026, 11, 24, hour, minute, 0, 0, 0, -1))


DAY = [
    {"from": "07:30", "to": "08:00", "room": "卧室", "doing": "赖床", "mood": "迷糊", "share": ""},
    {"from": "08:00", "to": "12:00", "room": "书房", "doing": "复习", "mood": "专心", "share": ""},
    {"from": "13:00", "to": "23:00", "room": "客厅", "doing": "打游戏", "mood": "开心", "share": "赢了阿马"},
]


def test_what_she_is_doing_at_a_time() -> None:
    assert block_at(DAY, at(9))["doing"] == "复习"
    assert block_at(DAY, at(12, 30))["doing"] == "打游戏"  # between two: on her way to the next
    assert block_at(DAY, at(23, 30))["doing"] == "睡着了" and block_at(DAY, at(6))["room"] == "卧室"
    assert block_at([], at(9)) is None


def test_the_models_day_is_cleaned() -> None:
    raw = [
        {"from": "08:00", "to": "09:00", "room": "她的卧室", "doing": "  看书 ", "mood": "安静"},
        {"from": "08:30", "to": "10:00", "room": "厨房", "doing": "重叠的", "mood": ""},
        {"from": "25:00", "to": "26:00", "room": "厨房", "doing": "不存在的时间"},
        {"from": "10:00", "to": "09:00", "room": "厨房", "doing": "倒着的"},
        {"from": "10:00", "to": "11:00", "room": "地下室", "doing": "不认识的房间"},
        {"from": "11:00", "to": "12:00", "room": "阳台", "doing": ""},
    ]
    cleaned = clean_blocks(raw)
    assert [(b["room"], b["doing"]) for b in cleaned] == [("卧室", "看书"), ("客厅", "不认识的房间")]
    text = "好的：\n" + json.dumps({"阿鲸": DAY, "不认识": DAY, "阿马": []}, ensure_ascii=False)
    assert list(parse_plan(text, {"阿鲸": WHALE, "阿马": PONY})) == [WHALE]
    assert parse_plan("不是 JSON", {"阿鲸": WHALE}) == {}


def test_how_she_is_seen_at_home() -> None:
    assert pose_of("书房", "站在书架前找书", "站") == "stand"  # as the plan says
    said = [
        ("卧室", "赖床", "lie"),
        ("卧室", "睡着了", "lie"),
        ("卧室", "起床，收拾自己", "stand"),
        ("卧室", "梳洗叠被，写便签", "stand"),
        ("厨房", "做早饭、吃早饭", "stand"),
        ("厨房", "吃午饭", "sit"),
        ("客厅", "泡茶翻书", "sit"),
        ("客厅", "打游戏", "sit"),
        ("客厅", "忙自己的事", "sit"),
        ("书房", "复习", "sit"),
        ("阳台", "给花浇水", "stand"),
        ("客厅", "眯一会儿，靠着沙发", "sit"),
    ]
    assert [(room, doing, pose_of(room, doing)) for room, doing, _ in said] == said
    raw = [{"from": "08:00", "to": "09:00", "room": "书房", "doing": "看书", "pose": "躺着"}]
    assert clean_blocks(raw)[0]["pose"] == "lie"
    assert clean_blocks([DAY[1]])[0]["pose"] == "sit"


def test_profiles_and_the_plain_day() -> None:
    profile = clean_profile(
        {"wake": "6:5", "sleep": "23:00", "rooms": ["书房", "地下室"], "lately": "学做蛋糕"}
    )
    assert profile["wake"] == "07:30"  # not a time: the default
    assert profile["sleep"] == "23:00" and profile["rooms"] == ["书房"] and profile["lately"] == "学做蛋糕"
    day = default_day(profile)
    assert day[0]["from"] == "07:30" and day[-1]["to"] == "23:00" and day[2]["room"] == "书房"
    assert festival_of(dt.date(2026, 12, 25)) == "圣诞节" and festival_of(dt.date(2026, 9, 25)) == "中秋节"
    assert festival_of(dt.date(2026, 11, 24)) == ""


async def live(w: World, *bots: str) -> None:
    for bot in bots:
        assert (await w.patch_bot(bot, {"mode": "live"})).status_code == 200


async def test_off_until_you_turn_it_on_then_one_plan_for_the_house(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    life = world.state.life
    view = (await world.http.get("/api/life")).json()
    assert view["on"] is False and view["planned"] is False
    assert await life.line(world.bot(WHALE)) == ""
    assert (await world.http.post("/api/life/plan")).status_code == 409

    planner = world.state.providers.runtimes["ark"].provider  # 阿鲸's model plans for the house
    plan = {"阿鲸": DAY, "阿马": [{**DAY[2], "doing": "和阿鲸打游戏"}]}
    planner.queue(json.dumps(plan, ensure_ascii=False))
    view = (await world.http.put("/api/life", json={"on": True})).json()
    assert view["on"] and view["planned"]
    people = {p["bot_id"]: p for p in view["people"]}
    assert [b["doing"] for b in people[WHALE]["day"]] == ["赖床", "复习", "打游戏"]
    assert people[PONY]["day"][0]["doing"] == "和阿鲸打游戏" and people[PONY]["home"]
    _, ask = planner.requests[-1]
    assert "客厅、厨房、书房、阳台" in ask.content and "阿马" in ask.content

    # The model gives nothing usable: a plain day from each profile.
    planner.queue("今天不排了")
    view = (await world.http.post("/api/life/plan")).json()
    assert all(p["day"] for p in view["people"] if p["home"])
    assert (await world.state.db.fetchone("SELECT made_by FROM life_days"))["made_by"] == "default"


async def test_her_chat_knows_where_she_is(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    await live(world, PONY)
    life = world.state.life
    await world.http.put("/api/life", json={"on": True})
    await life.remember(WHALE, "蛋糕烤糊了", "share")
    line = await life.line(world.bot(WHALE))
    assert line.startswith("（你在小窝里的生活：你现在在") and "今天：" in line and "蛋糕烤糊了" in line
    model = world.state.providers.runtimes["ark"].provider
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "在干嘛"})

    async def answered() -> Any:
        rows = (await world.http.get(f"/api/channels/{chat}/messages")).json()
        return any(r["from_self"] for r in rows)

    await eventually(answered)
    assert "你在小窝里的生活" in model.requests[-1][1].content

    moment = (await world.http.get("/api/life")).json()["moments"][0]
    assert moment["text"] == "蛋糕烤糊了"
    assert (await world.http.delete(f"/api/life/moments/{moment['id']}")).status_code == 200
    assert "蛋糕烤糊了" not in await life.line(world.bot(WHALE))


async def test_profiles_are_drafted_and_edited(world: World) -> None:  # noqa: F811
    model = world.state.providers.runtimes["ark"].provider
    model.queue(
        '{"wake": "09:00", "sleep": "01:30", "rooms": ["阳台"], "hobbies": ["发呆"], "lately": "追一部番"}'
    )
    drafted = (await world.http.post(f"/api/life/profiles/{WHALE}/draft")).json()
    assert drafted["wake"] == "09:00" and drafted["rooms"] == ["阳台"] and drafted["lately"] == "追一部番"
    edited = await world.http.put(f"/api/life/profiles/{WHALE}", json={"profile": {**drafted, "home": False}})
    assert edited.status_code == 200 and edited.json()["home"] is False
    people = {p["bot_id"]: p for p in (await world.http.get("/api/life")).json()["people"]}
    assert people[WHALE]["home"] is False and people[WHALE]["profile"]["hobbies"] == ["发呆"]
    assert (await world.http.post("/api/life/profiles/qq:404/draft")).status_code == 404
