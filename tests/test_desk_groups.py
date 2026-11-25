"""桌面群: the user and several companions chatting together in the desk chat."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from tests.helpers import eventually
from tests.test_bot_settings_api import DRAGON, PONY, WHALE, World, world  # noqa: F401 (fixture)


async def live(w: World, *bots: str) -> None:
    for bot in bots:
        assert (await w.patch_bot(bot, {"mode": "live"})).status_code == 200


async def new_group(w: World, name: str = "吃货群", bots: tuple[str, ...] = (WHALE, PONY)) -> dict[str, Any]:
    response = await w.http.post("/api/desk/groups", json={"name": name, "bot_ids": list(bots)})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def rows(w: World, channel: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{channel}/messages")).json()  # type: ignore[no-any-return]


async def test_a_group_of_you_and_two_companions(world: World) -> None:  # noqa: F811
    group = await new_group(world)
    assert group["name"] == "吃货群" and group["created_by"] == "user"
    assert [m["bot_id"] for m in group["members"]] == [WHALE, PONY]
    assert group["view"] == group["members"][0]["channel_id"] and group["last"] is None
    listed = (await world.http.get("/api/desk/groups")).json()
    assert [g["id"] for g in listed] == [group["id"]]
    # Each member hears it through her own channel, a group one, in the desk only.
    for member in group["members"]:
        channel = world.bot(member["bot_id"]).channels[member["channel_id"]]
        assert (
            channel.enabled
            and channel.channel.kind == "group"
            and channel.channel.id == f"desk-g{group['id']}"
        )

    refused = await world.http.post("/api/desk/groups", json={"name": "一个人", "bot_ids": [WHALE]})
    assert refused.status_code == 409 and "两个" in refused.json()["detail"]
    assert (
        await world.http.post("/api/desk/groups", json={"name": "  ", "bot_ids": [WHALE, PONY]})
    ).status_code == 409
    unknown = await world.http.post("/api/desk/groups", json={"name": "x", "bot_ids": [WHALE, "qq:404"]})
    assert unknown.status_code == 409


async def test_one_picks_it_up_the_other_answers_her_and_then_they_wait_for_you(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    group = await new_group(world)
    whale_view = group["view"]
    pony_view = group["members"][1]["channel_id"]
    said = await world.http.post(f"/api/desk/groups/{group['id']}/messages", json={"text": "大家晚上吃什么"})
    assert said.status_code == 202 and said.json()["channel_id"] == whale_view

    # Everyone's words reach both views (one id for the copies), and the bot turns stop at the chain limit.
    async def settled() -> list[dict[str, Any]] | None:
        found = await rows(world, whale_view)
        await asyncio.sleep(0.3)
        return found if found == await rows(world, whale_view) and len(found) > 2 else None

    talk = await eventually(settled, timeout=15)
    assert talk[0]["text"] == "大家晚上吃什么" and not talk[0]["from_self"]
    speakers = [r["sender_id"] for r in talk[1:]]
    assert 2 <= len(speakers) <= 4  # one picked it up, the other answered her, until the chain limit
    assert all(a != b for a, b in itertools.pairwise(speakers))  # nobody answers herself
    assert [r["external_id"] for r in await rows(world, pony_view)] == [r["external_id"] for r in talk]

    # Her group prompt calls the place 桌面小群 and the other companion another bot.
    system, user = world.state.providers.runtimes["ark"].provider.requests[0]
    assert "桌面小群" in system.content and "QQ 群" not in system.content
    # The user spoke to all of them: whoever takes it is not left to SKIP it. Her answer is picked up freely.
    assert "轮到你先接" in user.content and "接不上就 SKIP" not in user.content
    later = world.state.providers.runtimes["ark"].provider.requests[-1][1].content
    assert "轮到你先接" not in later and "接不上就 SKIP" in later
    assert "阿马（另一个 bot）" in later or "阿马（另一个 bot）" in user.content


async def test_settings_and_dissolving(world: World) -> None:  # noqa: F811
    group = await new_group(world)
    path = f"/api/desk/groups/{group['id']}"
    renamed = await world.http.patch(path, json={"name": "周末出去玩", "activity": {PONY: "mentions"}})
    assert renamed.status_code == 200 and renamed.json()["name"] == "周末出去玩"
    assert {m["bot_id"]: m["activity"] for m in renamed.json()["members"]}[PONY] == "mentions"
    bad = await world.http.patch(path, json={"activity": {PONY: "loud"}})
    assert bad.status_code == 409

    grown = await world.http.patch(path, json={"bot_ids": [WHALE, PONY, DRAGON]})
    assert [m["bot_id"] for m in grown.json()["members"]] == [WHALE, PONY, DRAGON]
    pony_channel = next(m["channel_id"] for m in grown.json()["members"] if m["bot_id"] == PONY)
    shrunk = await world.http.patch(path, json={"bot_ids": [WHALE, DRAGON]})
    assert [m["bot_id"] for m in shrunk.json()["members"]] == [WHALE, DRAGON]
    assert not world.bot(PONY).channels[pony_channel].enabled
    assert (await world.http.patch(path, json={"bot_ids": [WHALE]})).status_code == 409

    assert (await world.http.delete(path)).status_code == 200
    assert (await world.http.get("/api/desk/groups")).json() == []
    assert all(not c.enabled for c in world.bot(WHALE).channels.values() if c.channel.id.startswith("desk-g"))
    assert (await world.http.post(f"{path}/messages", json={"text": "还在吗"})).status_code == 404


async def test_nobody_to_answer(world: World) -> None:  # noqa: F811
    group = await new_group(world)  # both only watch (not live)
    said = await world.http.post(f"/api/desk/groups/{group['id']}/messages", json={"text": "在吗"})
    assert said.status_code == 409 and "不会回话" in said.json()["detail"]


async def test_she_can_make_a_group_herself(world: World) -> None:  # noqa: F811
    from nest.core.groups import create_group_tool
    from tests.test_desk import private_chat

    groups = world.state.groups
    said = await create_group_tool(groups, world.bot(WHALE), {"name": "周末吃什么", "members": ["阿马"]})
    assert said.startswith("建好了「周末吃什么」") and "阿马" in said
    [made] = (await world.http.get("/api/desk/groups")).json()
    assert made["created_by"] == WHALE and [m["bot_id"] for m in made["members"]] == [WHALE, PONY]
    unknown = await create_group_tool(groups, world.bot(WHALE), {"name": "x", "members": ["阿星"]})
    assert unknown.startswith("没找到这些伙伴：阿星") and "阿马" in unknown
    alone = await create_group_tool(groups, world.bot(WHALE), {"name": "x", "members": []})
    assert alone.startswith("没建成")

    # It is one of her tools in her chat with the user.
    chat = await private_chat(world)
    tools, _ = world.state.brain.tools.for_chat(world.bot(WHALE), world.bot(WHALE).channels[chat])
    assert "create_group" in [t.name for t in tools]


async def test_without_tools_she_makes_one_with_a_mark(world: World) -> None:  # noqa: F811
    from nest.core.groups import split_group
    from tests.test_desk import private_chat

    assert split_group("好呀 [拉群：周末吃什么｜阿马、 阿星]") == (
        "好呀",
        {"name": "周末吃什么", "members": ["阿马", "阿星"]},
    )
    assert split_group("没有标记")[1] is None

    chat = await private_chat(world)
    runtime = world.state.providers.runtimes["ark"]
    assert not runtime.can_use_tools  # Ark's Responses endpoint
    model = runtime.provider
    model.queue("那我们拉上阿马一起商量吧 [拉群：周末吃什么｜阿马]")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "周末吃什么好"})

    async def answered() -> list[dict[str, Any]] | None:
        found = [r for r in await rows(world, chat) if r["from_self"]]
        return found or None

    [hers] = await eventually(answered)
    assert hers["text"] == "那我们拉上阿马一起商量吧"  # the mark is not shown
    assert any("[拉群：群名｜伙伴1、伙伴2]" in m.content for m in model.requests[-1])
    [made] = (await world.http.get("/api/desk/groups")).json()
    assert made["name"] == "周末吃什么" and made["created_by"] == WHALE
    assert [m["bot_id"] for m in made["members"]] == [WHALE, PONY]
