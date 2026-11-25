"""桌面聊天: taking back what you said, another answer, looking through the chat, short lines as messages."""

from __future__ import annotations

from typing import Any

from nest.core.desk import asked_before, bubbles
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat, rows
from tests.test_desk_groups import live, new_group
from tests.test_stickers import picture


def model(w: World) -> Any:
    return w.state.providers.runtimes["ark"].provider


async def say(w: World, text: str) -> str:
    response = await w.http.post(f"/api/desk/{WHALE}/messages", json={"text": text})
    assert response.status_code == 202, response.text
    return response.json()["message_id"]  # type: ignore[no-any-return]


async def talked(w: World, chat: int, hers: int) -> list[dict[str, Any]]:
    async def check() -> list[dict[str, Any]] | None:
        found = [r for r in await rows(w, chat) if r["from_self"] and not r["recalled_at"]]
        return found if len(found) >= hers and not w.state.brain.busy(WHALE, chat) else None

    return await eventually(check)  # type: ignore[no-any-return]


async def test_taking_back_something_you_said(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    await say(world, "别告诉别人，秘密是香菜")
    await talked(world, chat, 1)
    mine, hers = await rows(world, chat)
    assert (await world.http.delete(f"/api/desk/{WHALE}/messages/{hers['id']}")).status_code == 409
    assert (await world.http.delete(f"/api/desk/{PONY}/messages/{mine['id']}")).status_code == 404
    assert (await world.http.delete(f"/api/desk/{WHALE}/messages/{mine['id']}")).status_code == 200
    recalled = await eventually(lambda: [d for kind, d in world.events if kind == "recall"])
    assert recalled[0]["id"] == mine["id"] and recalled[0]["text"] == "" and recalled[0]["recalled_at"]
    # Gone for good: not in the chat log, not in what she is told, not found.
    assert (await rows(world, chat))[0]["text"] == ""
    await say(world, "在吗")
    await talked(world, chat, 2)
    prompt = "\n".join(m.content for m in model(world).requests[-1])
    assert "香菜" not in prompt and "撤回" not in prompt
    assert (await world.http.get(f"/api/desk/{WHALE}/search?q=香菜")).json()["items"] == []


async def test_taken_back_before_she_answers(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    world.state.brain.policy = type(world.state.brain.policy)(quiet_seconds=0.4)
    await say(world, "说错了")
    mine = (await eventually(lambda: rows(world, chat)))[0]
    assert (await world.http.delete(f"/api/desk/{WHALE}/messages/{mine['id']}")).status_code == 200
    await eventually(lambda: [d for kind, d in world.events if kind == "recall"])
    await eventually(lambda: not world.state.brain.busy(WHALE, chat))
    assert model(world).requests == [] and len(await rows(world, chat)) == 1


async def test_another_answer(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    model(world).queue("先吃面", "那吃火锅")
    await say(world, "吃什么")
    hers = (await talked(world, chat, 1))[0]
    retry = f"/api/desk/{WHALE}/messages/{hers['id']}/retry"
    assert (await world.http.post(retry)).status_code == 202
    answers = await eventually(lambda: _said(world, chat, "那吃火锅"))
    assert len(answers) == 1
    asked = "\n".join(m.content for m in model(world).requests[-1])
    assert "你刚才回的是：\n先吃面" in asked and asked.count("先吃面") == 1 and "：吃什么" in asked
    # Only the newest answer, and only what still stands at the end.
    assert (await world.http.post(retry)).status_code == 409


async def _said(w: World, chat: int, text: str) -> list[dict[str, Any]] | None:
    found = [r for r in await rows(w, chat) if r["from_self"] and not r["recalled_at"]]
    return found if found and found[-1]["text"] == text else None


def test_what_an_answer_is() -> None:
    def row(n: int, hers: bool, kind: str = "", **more: Any) -> dict[str, Any]:
        return {"id": n, "from_self": hers, "external_id": f"app:{kind}{n:032x}", "recalled_at": None} | more

    answers, asked = asked_before([row(1, False), row(2, True), row(3, False), row(4, False), row(5, True)])
    assert [r["id"] for r in answers] == [5] and [r["id"] for r in asked] == [3, 4]
    # A reminder after it is not part of it; her own line alone is no answer.
    answers, _ = asked_before([row(1, False), row(2, True), row(3, True, "n:")])
    assert [r["id"] for r in answers] == [2]
    assert asked_before([row(1, False), row(2, True, "c:")]) == ([], [])
    assert asked_before([row(1, False), row(2, True), row(3, False)]) == ([], [])
    # Asked on the platform: answered there.
    assert asked_before([row(1, False, external_id="777"), row(2, True)]) == ([], [])


async def test_looking_through_the_chat(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    for n, text in enumerate(("周末去海边 看日落", "海边冷不冷", "100%可以"), 1):
        await say(world, text)
        await talked(world, chat, n)
    found = (await world.http.get(f"/api/desk/{WHALE}/search?q=海边")).json()["items"]
    assert [r["text"] for r in found] == ["海边冷不冷", "周末去海边 看日落"]
    found = (await world.http.get(f"/api/desk/{WHALE}/search", params={"q": "海边 日落"})).json()["items"]
    assert [r["text"] for r in found] == ["周末去海边 看日落"]
    found = (await world.http.get(f"/api/desk/{WHALE}/search", params={"q": "%"})).json()["items"]
    assert [r["text"] for r in found] == ["100%可以"]
    assert (await world.http.get(f"/api/desk/{PONY}/search?q=海边")).json()["items"] == []


def test_short_lines_are_messages_of_their_own() -> None:
    assert bubbles("在呀\n刚睡醒") == ["在呀", "刚睡醒"]
    assert bubbles("一句") == ["一句"]
    long = "这一句很长" * 10
    assert bubbles(f"好\n{long}") == [f"好\n{long}"]
    assert bubbles("可以：\n1. 先洗\n2. 再切") == ["可以：\n1. 先洗\n2. 再切"]
    assert bubbles("一\n二\n三\n四\n五") == ["一\n二\n三\n四\n五"]


async def test_she_answers_in_a_few_messages(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    model(world).queue("在呀\n刚睡醒")
    await say(world, "在吗")
    hers = await talked(world, chat, 2)
    assert [r["text"] for r in hers] == ["在呀", "刚睡醒"]
    assert "一句一行" in model(world).requests[0][0].content
    # Both are one answer: another one takes both back.
    model(world).queue("来啦")
    assert (await world.http.post(f"/api/desk/{WHALE}/messages/{hers[0]['id']}/retry")).status_code == 202
    assert len(await eventually(lambda: _said(world, chat, "来啦"))) == 1


async def test_taking_back_in_a_group(world: World) -> None:  # noqa: F811
    await live(world, WHALE)
    group = await new_group(world)
    said = await world.http.post(f"/api/desk/groups/{group['id']}/messages", json={"text": "说漏嘴了"})
    assert said.status_code == 202
    views = [m["channel_id"] for m in group["members"]]
    for view in views:
        await eventually(lambda view=view: rows(world, view))
    mine = (await rows(world, group["view"]))[0]
    gone = await world.http.delete(f"/api/desk/groups/{group['id']}/messages/{mine['id']}")
    assert gone.status_code == 200
    await eventually(lambda: len([d for kind, d in world.events if kind == "recall"]) == 2)
    for view in views:
        assert (await rows(world, view))[0]["text"] == ""


async def test_starred_stickers(world: World) -> None:  # noqa: F811
    added = await world.http.put("/api/stickers/upload", content=picture("blue"))
    assert added.status_code == 200, added.text
    sticker = added.json()["sticker"]["id"]
    assert (await world.http.put(f"/api/stickers/{sticker}/star", json={"on": True})).status_code == 200
    picked = (await world.http.get(f"/api/sticker-packs/bots/{WHALE}/picker")).json()
    assert [s["id"] for s in picked["starred"]] == [sticker] and picked["starred"][0]["starred_at"]
    assert await world.state.stickers.store.evictable(10) == []
    assert (await world.http.put(f"/api/stickers/{sticker}/star", json={"on": False})).status_code == 200
    picked = (await world.http.get(f"/api/sticker-packs/bots/{WHALE}/picker")).json()
    assert picked["starred"] == []
    assert (await world.http.put("/api/stickers/99999/star", json={"on": True})).status_code == 404
