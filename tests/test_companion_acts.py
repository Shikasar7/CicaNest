"""做事: the tools she has of her own besides the first ones — changing a reminder, writing something down, a
record for her 回忆档案, drawing a picture, downloading a file — and what her calls and the user's nods are
kept as."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import httpx

from nest.core.drawing import PER_DAY
from nest.llm import purpose
from nest.llm.purpose import for_purpose, using
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401 (fixture)
from tests.test_sticker_packs import AVATAR, painter
from tests.test_tools import claude_chat, model


async def ask(w: World, chat: int, *replies: Any, text: str = "帮个忙") -> list[tuple[str, str]]:
    """She is asked something and answers with the scripted calls; what each tool told her."""
    before = len(model(w).tool_results)
    last = f"好了{time.monotonic()}"
    model(w).queue(*replies, last)
    await w.http.post(f"/api/desk/{WHALE}/messages", json={"text": text})
    await eventually(lambda: _is(w, chat, last))
    return model(w).tool_results[before:]


async def _is(w: World, chat: int, text: str) -> bool:
    return any(r["text"] == text for r in (await w.http.get(f"/api/channels/{chat}/messages")).json())


async def test_she_changes_a_reminder(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    noon = dt.datetime.fromtimestamp(time.time() + 86400).replace(hour=12, minute=0, second=0, microsecond=0)
    ((_, made),) = await ask(
        world, chat, ToolCall("set_reminder", {"when": f"{noon:%Y-%m-%d %H:%M}", "what": "开会"})
    )
    assert made.startswith("设好了")
    (row,) = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()["reminders"]
    later = noon.replace(hour=16)
    results = await ask(
        world,
        chat,
        ToolCall("change_reminder", {"id": row["id"], "when": f"{later:%Y-%m-%d %H:%M}"}),
        ToolCall("change_reminder", {"id": row["id"], "what": "交报告", "repeat": "weekly"}),
        ToolCall("change_reminder", {"id": row["id"], "when": "昨天"}),
        ToolCall("change_reminder", {"id": 999, "what": "x"}),
    )
    assert [r for _, r in results] == [
        "改好了：明天 16:00「开会」",
        "改好了：明天 16:00「交报告」，每周",
        "没改成：时间要写成 YYYY-MM-DD HH:MM 且不能是过去",
        "没有这个提醒（看编号对不对，取消了的要重新设）",
    ]
    (row,) = (await world.http.get("/api/reminders", params={"bot_id": WHALE})).json()["reminders"]
    assert (row["text"], row["repeat"]) == ("交报告", "weekly")


async def test_she_writes_something_down(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    ((_, refused),) = await ask(
        world, chat, ToolCall("remember", {"content": "喜欢草莓蛋糕", "kind": "like"})
    )
    assert "没有开" in refused  # not asked to remember in this chat
    assert "remember" not in model(world).tool_lists[-1]
    path = f"/api/bots/{WHALE}/channels/{chat}"
    assert (await world.http.patch(path, json={"remember": True})).status_code == 200
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    results = await ask(
        world,
        chat,
        ToolCall("remember", {"content": "喜欢草莓蛋糕", "kind": "like"}),
        ToolCall("remember", {"content": "喜欢草莓蛋糕", "kind": "like"}),
        ToolCall("remember", {"content": "周末一起看电影", "kind": "promise", "due": soon}),
        ToolCall("remember", {"content": "密码是 1234", "kind": "fact"}),
    )
    assert "remember" in model(world).tool_lists[-1]
    assert results[0][1] == "记下了：喜欢草莓蛋糕" and results[1][1] == "已经记着了"
    assert results[2][1] == f"记下了：周末一起看电影（{soon}）" and results[3][1].startswith("没记")
    kept = await world.state.memory.store.of_place(f"chat:{chat}")
    assert sorted((m["kind"], m["content"], m["bot_id"]) for m in kept) == [
        ("like", "喜欢草莓蛋糕", None),
        ("promise", "周末一起看电影", WHALE),
    ]


async def test_what_she_writes_into_her_records_is_a_draft(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    ((_, told),) = await ask(
        world, chat, ToolCall("write_memory", {"title": "第一次看海", "text": "那天我们说好了要去看海。"})
    )
    assert "草稿" in told
    (record,) = (await world.http.get(f"/api/bots/{WHALE}/keepsakes")).json()
    assert (record["title"], record["draft"]) == ("第一次看海", True)
    # Not hers to look up until the user has kept it.
    ((_, found),) = await ask(world, chat, ToolCall("recall_memories", {"query": "看海"}))
    assert found == "回忆档案里没有找到"


async def test_she_draws_a_picture(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    assert (await ask(world, chat, ToolCall("draw_picture", {"idea": "一只猫"}))) == [
        ("draw_picture", "没画：现在没有能画图的模型")
    ]
    provider = await painter(world)
    await world.http.put(f"/api/bots/{WHALE}/avatar/custom", content=AVATAR)
    ((_, told),) = await ask(world, chat, ToolCall("draw_picture", {"idea": "海边的你", "with_me": True}))
    assert told.startswith("开始画了")
    shown = await eventually(lambda: _picture(world, chat))
    assert shown["text"] == "（发了一张图：海边的你）" and shown["from_self"]
    name = shown["segments"][0]["data"]["file"]
    assert (await world.http.get(f"/api/desk/media/{name}")).status_code == 200
    prompt, images = provider.drawn[0]
    assert "海边的你" in prompt and "「阿鲸」照参考图" in prompt and len(images) == 1
    files = world.state.workspace.entries(WHALE, "给你的")
    assert [f.name.startswith("画-") for f in files] == [True]
    # A few a day.
    keeper = world.state.drawing
    keeper._drawn = {key: PER_DAY for key in keeper._drawn}
    assert (await ask(world, chat, ToolCall("draw_picture", {"idea": "再来一张"})))[0][1].startswith(
        "没画：今天已经画了"
    )


async def _picture(w: World, chat: int) -> dict[str, Any] | None:
    found = (await w.http.get(f"/api/channels/{chat}/messages")).json()
    return next((r for r in found if r["external_id"].startswith("app:p:")), None)


async def test_a_download_is_asked_about_every_time(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})

    world.state.brain.tools._transport = httpx.MockTransport(handler)
    url = "http://93.184.216.34/data/%E8%A1%A8.csv"

    async def nod(choice: str) -> dict[str, Any]:
        waiting = await eventually(lambda: _waiting(world))
        assert (await world.http.post(f"/api/approvals/{waiting['id']}", json={"choice": choice})).is_success
        return waiting

    import asyncio

    for choice, expected in (("kind", "下载好了：收到的/表.csv（1 KB）"), ("deny", "没下：你没有允许")):
        answered = asyncio.create_task(nod(choice))
        ((_, told),) = await ask(world, chat, ToolCall("download_file", {"url": url}))
        waiting = await answered
        assert told == expected
        assert '"大小": "1 KB"' in waiting["given"] and "93.184.216.34" in waiting["given"]
    assert world.state.workspace.read(WHALE, "收到的/表.csv") == "a,b\n1,2\n"
    assert len(fetched) == 3  # looked at, got; looked at
    refused = await ask(
        world,
        chat,
        ToolCall("download_file", {"url": "http://127.0.0.1/x.exe"}),
        ToolCall("download_file", {"url": "file:///etc/passwd"}),
    )
    assert "本机或内网" in refused[0][1] and refused[1][1].startswith("只能")


async def _waiting(w: World) -> dict[str, Any] | None:
    rows = (await w.http.get("/api/approvals")).json()["approvals"]
    return rows[0] if rows else None


# -- what is kept -----------------------------------------------------------------------------------------


async def test_a_nod_given_on_the_pc_is_kept_with_the_others(world: World) -> None:  # noqa: F811
    import asyncio

    hub = world.state.executor
    await hub.poll("PC-1", key="k", wait=0)
    waiting = asyncio.create_task(
        hub.call(
            bot_id=WHALE,
            bot_name="阿鲸",
            task_id=None,
            scope="s.0",
            tool="win_write_file",
            given={"path": "D:\\a.txt"},
        )
    )
    (step,) = await hub.poll("PC-1", key="k", wait=1)
    answer = {"device": "PC-1", "key": "k", "text": "写好了", "decided": "once"}
    assert (await world.http.post(f"/api/executor/requests/{step['id']}", json=answer)).status_code == 200
    assert await waiting == "写好了"
    (row,) = (await world.http.get("/api/approvals?everything=true")).json()["approvals"]
    assert (row["status"], row["tool"], row["decided_by"]) == ("allowed", "win_write_file", "PC-1")
    assert "a.txt" in row["given"] and row["plugin_name"] == "电脑上做事"
    assert (await world.http.get("/api/approvals")).json()["approvals"] == []


async def test_what_calls_are_for(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    await ask(world, chat)
    runtime = world.state.providers.get("ark")

    @for_purpose("life")
    async def plan() -> None:
        from nest.llm.base import ChatMessage

        await runtime.complete([ChatMessage("user", "x")], bot_id=WHALE, max_tokens=10)

    await plan()
    assert purpose.current() == ""
    with using("draw"):
        assert purpose.current() == "draw"
    usage = (await world.http.get("/api/usage?days=1")).json()
    found = {(p["bot_id"], p["purpose"]): (p["label"], p["calls"]) for p in usage["purposes"]}
    assert found[(WHALE, "chat")][0] == "聊天" and found[(WHALE, "life")] == ("她们的日子", 1)
