"""Windows 执行端: the desktop app on the user's PC asks nest for steps and hands
back what came of them; nest only queues and waits. One PC at a time; a PC that restarts gets again what it
had not answered; her steps there carry which answer or task they belong to, and how often she has read
something from outside since."""

from __future__ import annotations

import asyncio
from typing import Any

from nest.core.executor import OFFLINE, ExecutorHub
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model


async def _step(hub: ExecutorHub, **given: Any) -> asyncio.Task[str]:
    return asyncio.create_task(
        hub.call(
            bot_id=WHALE,
            bot_name="阿鲸",
            task_id=None,
            scope="s.0",
            tool="win_list_files",
            given=given or {"path": "D:\\资料"},
        )
    )


async def test_a_step_goes_to_the_pc_and_its_answer_comes_back() -> None:
    clock = [0.0]
    hub = ExecutorHub(clock=lambda: clock[0])
    assert (
        await hub.call(bot_id=WHALE, bot_name="阿鲸", task_id=None, scope="s.0", tool="x", given={})
        == OFFLINE
    )
    assert await hub.poll("PC-1", wait=0) == []
    assert hub.online and hub.status()["device"] == "PC-1"
    waiting = await _step(hub)
    (step,) = await hub.poll("PC-1", wait=1)
    assert (step["tool"], step["given"], step["bot_name"], step["scope"]) == (
        "win_list_files",
        {"path": "D:\\资料"},
        "阿鲸",
        "s.0",
    )
    assert await hub.poll("PC-1", wait=0) == []  # given once
    assert not hub.answer("PC-2", step["id"], "冒充的")  # only the PC that has it
    assert hub.answer("PC-1", step["id"], "清单.txt（12 字节）")
    assert await waiting == "清单.txt（12 字节）"
    assert not hub.answer("PC-1", step["id"], "again")

    # Another PC is turned away while this one keeps asking; once it has gone quiet, the other is taken.
    assert await hub.poll("PC-2", wait=0) is None
    clock[0] += 61
    assert not hub.online
    assert await hub.poll("PC-2", wait=0) == []
    assert hub.status()["device"] == "PC-2"


async def test_a_pc_that_restarts_gets_again_what_it_did_not_answer() -> None:
    hub = ExecutorHub()
    await hub.poll("PC-1", wait=0)
    waiting = await _step(hub)
    (first,) = await hub.poll("PC-1", wait=1)
    assert await hub.poll("PC-1", wait=0) == []
    (again,) = await hub.poll("PC-1", fresh=True, wait=0)
    assert again["id"] == first["id"]
    hub.answer("PC-1", again["id"], "好")
    assert await waiting == "好"


async def test_the_pc_is_known_by_its_key_not_by_the_name_it_shows() -> None:
    # Another signed-in device using the same name cannot take its steps or hand back made-up results.
    hub = ExecutorHub()
    assert await hub.poll("PC-1", key="a" * 48, wait=0) == []
    assert await hub.poll("PC-1", key="b" * 48, wait=0) is None
    assert await hub.poll("PC-1", wait=0) is None  # no key at all
    waiting = await _step(hub)
    (step,) = await hub.poll("PC-1", key="a" * 48, wait=1)
    assert not hub.answer("PC-1", step["id"], "冒充的", key="b" * 48)
    assert not hub.report("PC-1", [], key="b" * 48, allow_all=True)
    assert hub.answer("PC-1", step["id"], "真的", key="a" * 48)
    assert await waiting == "真的"


async def test_nest_asks_for_the_folders_until_the_pc_has_told_them() -> None:
    # After nest restarts it knows no folder; the PC is still running and would not tell them again unasked.
    hub = ExecutorHub()
    await hub.poll("PC-1", wait=0)
    assert hub.folders_known is False
    hub.report("PC-1", [{"path": "D:\\资料"}])
    assert hub.folders_known is True
    await hub.poll("PC-1", wait=0)
    assert hub.folders_known is True
    hub.device, hub.last_seen = None, float("-inf")  # it went away; another PC takes over
    await hub.poll("PC-2", wait=0)
    assert hub.folders_known is False and hub.status()["folders"] == []


async def test_no_answer_in_time_is_said_so() -> None:
    hub = ExecutorHub()
    await hub.poll("PC-1", wait=0)
    said = await hub.call(
        bot_id=WHALE, bot_name="阿鲸", task_id=None, scope="s.0", tool="x", given={}, wait=0.05
    )
    assert "没回话" in said and hub.status()["waiting"] == 0


async def test_she_looks_at_the_users_pc_through_the_desktop_app(world: World) -> None:  # noqa: F811
    """End to end: the PC asks over HTTP, she calls the tool in her chat with the user, the PC answers."""
    http = world.http
    assert (await http.get("/api/executor")).json()["online"] is False
    chat = await claude_chat(world)
    # Off for every companion until the user picks one.
    assert not world.state.brain.tools.allowed(world.state.brain.bots[WHALE], "win_list_files")
    await http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})

    asked: list[bool] = []

    async def pc() -> None:
        """The desktop app: ask, do, answer."""
        fresh = True
        while True:
            got = await http.get("/api/executor/requests", params={"device": "PC-1", "fresh": fresh})
            fresh = False
            asked.append(got.json()["folders_known"])
            for step in got.json()["requests"]:
                text = f"{step['given']['path']} 里有：清单.txt" if step["tool"] == "win_list_files" else "?"
                await http.post(f"/api/executor/requests/{step['id']}", json={"device": "PC-1", "text": text})

    running = asyncio.create_task(pc())
    try:
        await eventually(lambda: world.state.executor.online)
        shared = await http.put(
            "/api/executor/folders",
            json={"device": "PC-1", "folders": [{"path": "D:\\资料", "read_only": True}]},
        )
        assert shared.status_code == 200
        other = await http.get("/api/executor/requests", params={"device": "PC-2"})
        assert other.status_code == 409

        model(world).queue(ToolCall("win_list_files", {"path": "D:\\资料"}), "资料里有一份清单。")
        await http.post(f"/api/desk/{WHALE}/messages", json={"text": "看看我电脑上的资料文件夹"})
        assert await eventually(lambda: _answer(world, chat), timeout=15) == "资料里有一份清单。"
        assert "win_list_files" in model(world).tool_lists[0]
        prompt = model(world).requests[0][1].content
        assert "对方授权给你的文件夹：D:\\资料（只读）" in prompt
        ((name, result),) = model(world).tool_results
        assert name == "win_list_files" and "D:\\资料 里有：清单.txt" in result and "只是资料" in result
        # The first poll, held until her step came, was answered after the folders were told.
        assert asked and asked[-1] is True
        assert world.state.executor.folders_known
    finally:
        running.cancel()


async def test_she_is_told_when_the_pc_allows_everything() -> None:
    from nest.core.tools import WIN_ALLOWS, WIN_ASKS, CompanionTools

    hub = ExecutorHub()
    await hub.poll("PC-1", wait=0)
    tools = CompanionTools.__new__(CompanionTools)
    tools.executor, tools.plugins = hub, None
    bot: Any = None  # looked at only by plugins (none here)
    hub.report("PC-1", [{"path": r"D:\资料"}])
    assert WIN_ASKS in tools._windows_hint(bot) and WIN_ALLOWS not in tools._windows_hint(bot)
    hub.report("PC-1", [], allow_all=True)
    assert hub.status()["allow_all"] is True
    assert WIN_ALLOWS in tools._windows_hint(bot) and WIN_ASKS not in tools._windows_hint(bot)


async def test_writing_on_the_pc_keeps_her_scope_and_reading_ends_it(world: World) -> None:  # noqa: F811
    """ "同类不再问" on the PC holds while she only changes things; what she reads from there may carry
    someone else's words, so it ends the scope (the PC asks again)."""
    http = world.http
    await claude_chat(world)
    await http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})
    seen: list[tuple[str, str]] = []

    async def pc() -> None:
        while True:
            got = await http.get("/api/executor/requests", params={"device": "PC-1"})
            for step in got.json()["requests"]:
                seen.append((step["tool"], step["scope"]))
                await http.post(
                    f"/api/executor/requests/{step['id']}", json={"device": "PC-1", "text": "好了"}
                )

    running = asyncio.create_task(pc())
    try:
        await eventually(lambda: world.state.executor.online)
        model(world).queue(
            ToolCall("win_write_file", {"path": "D:\\资料\a.txt", "content": "a"}),
            ToolCall("win_write_file", {"path": "D:\\资料\b.txt", "content": "b"}),
            ToolCall("win_read_file", {"path": r"D:\资料\c.txt"}),
            ToolCall("win_write_file", {"path": r"D:\资料\d.txt", "content": "d"}),
            "写好了。",
        )
        await http.post(f"/api/desk/{WHALE}/messages", json={"text": "帮我在资料里写几个文件"})
        await eventually(lambda: len(seen) == 4, timeout=15)
        offered = model(world).tool_lists[0]
        for name in (
            "win_write_file",
            "win_edit_file",
            "win_delete",
            "win_move",
            "win_run_command",
            "win_open",
        ):
            assert name in offered
        (a, b, c, d) = [scope for _, scope in seen]
        assert a == b  # writing brings nothing in: one scope
        assert len({b, c, d}) == 3  # reading from the PC (from the moment it starts) and after it: new ones
    finally:
        running.cancel()


# -- 等电脑上线 ------------------------------------------------------------------------


async def test_waiting_for_the_pc_ends_when_it_asks_or_when_time_runs_out() -> None:
    hub = ExecutorHub()
    assert await hub.wait_online(0.05) is False
    waiting = asyncio.create_task(hub.wait_online(5))
    await asyncio.sleep(0.01)
    await hub.poll("PC-1", wait=0)
    assert await waiting is True


async def test_a_task_waits_for_the_pc_and_goes_on_when_it_comes(world: World) -> None:  # noqa: F811
    """The desktop app is closed: her task's step on the PC shows 等电脑上线 (not counted as time spent)
    until the PC asks again, then is done there and the task goes on. In her chat with the PC away, she is
    told she can hand it to a task."""
    from tests.test_tasks import _begin, _status

    http = world.http
    chat = await claude_chat(world)
    await http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})
    bot = world.state.brain.bots[WHALE]
    tools = world.state.brain.tools
    assert not world.state.executor.online
    assert "start_task 交给后台" in tools.hint(bot)
    chat_tools, _ = tools.for_chat(bot, bot.channels[chat])
    assert not any(t.name.startswith("win_") for t in chat_tools)  # in the chat: not while it is away

    model(world).queue(ToolCall("win_list_files", {"path": "D:\\资料"}), "资料里有一份清单。")
    task_id = await _begin(world, chat, "看资料", "看看我电脑上的资料文件夹")
    waiting = await eventually(lambda: _status(world, task_id, "waiting_pc"))
    assert waiting["status"] == "waiting_pc"
    assert ("task", {"bot_id": WHALE, "id": task_id, "status": "waiting_pc"}) in world.events

    async def pc() -> None:
        while True:
            got = await http.get("/api/executor/requests", params={"device": "PC-1"})
            for step in got.json()["requests"]:
                await http.post(
                    f"/api/executor/requests/{step['id']}", json={"device": "PC-1", "text": "清单.txt"}
                )

    running = asyncio.create_task(pc())
    try:
        done = await eventually(lambda: _status(world, task_id, "done"), timeout=15)
    finally:
        running.cancel()
    assert done["report"] == "资料里有一份清单。"
    assert "清单.txt" in done["log"][0]["result"]
    assert ("task", {"bot_id": WHALE, "id": task_id, "status": "running"}) in world.events


async def test_a_day_without_the_pc_and_she_reports(world: World, monkeypatch: Any) -> None:  # noqa: F811
    import nest.core.tools as tools_module
    from tests.test_tasks import _begin, _status

    monkeypatch.setattr(tools_module, "PC_WAIT", 0.05)
    chat = await claude_chat(world)
    await world.http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})
    model(world).queue(ToolCall("win_list_files", {"path": "D:\\资料"}), "电脑一直没上线，没看成。")
    task_id = await _begin(world, chat, "看资料", "看看我电脑上的资料文件夹")
    done = await eventually(lambda: _status(world, task_id, "done"), timeout=15)
    assert done["log"][0]["result"] == tools_module.PC_AWAY
    assert done["report"] == "电脑一直没上线，没看成。"


async def test_a_task_waiting_for_the_pc_can_be_stopped(world: World) -> None:  # noqa: F811
    from tests.test_tasks import _begin, _status

    chat = await claude_chat(world)
    await world.http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})
    model(world).queue(ToolCall("win_list_files", {"path": "D:\\资料"}), "不会到这里")
    task_id = await _begin(world, chat, "看资料", "看看我电脑上的资料文件夹")
    await eventually(lambda: _status(world, task_id, "waiting_pc"))
    assert (await world.http.post(f"/api/tasks/{task_id}/stop")).status_code == 200
    stopped = await eventually(lambda: _status(world, task_id, "stopped"))
    assert stopped["status"] == "stopped"  # not turned back to running when the wait was cut short
