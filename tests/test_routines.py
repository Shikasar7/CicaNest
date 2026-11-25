"""定时任务 (伙伴模式): at set times a companion's task starts by itself; nobody is
there to nod, so what needs a nod is not done; a time missed while nest was not running is not made up for,
and one that comes while the last is still going is skipped."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

from nest.core.routines import SKIP_BUSY, SKIP_MISSED, rule_label
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tasks import _status
from tests.test_tools import _answer, claude_chat, model


def _later(minutes: int) -> str:
    return f"{dt.datetime.fromtimestamp(time.time() + minutes * 60):%Y-%m-%dT%H:%M}"


async def _routine(w: World, **body: Any) -> int:
    sent = {
        "bot_id": WHALE,
        "title": "早报",
        "goal": "看看天气，告诉我",
        "when": _later(60),
        "repeat": "daily",
    }
    response = await w.http.post("/api/routines", json=sent | body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _due_now(w: World, routine_id: int, ago: float = 10) -> None:
    await w.state.routines.store.update(routine_id, next_at=time.time() - ago)


async def test_at_its_time_a_task_starts_by_itself_and_reports(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    routine_id = await _routine(world, also_qq=False)
    (listed,) = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"]
    assert listed["rule"].startswith("每天 ") and listed["next"].startswith(("今天", "明天"))
    assert listed["source"] == "console" and listed["enabled"] is True

    model(world).queue("今天晴，记得带水。")
    await _due_now(world, routine_id)
    assert await world.state.routines.check() == 1
    (run,) = (await world.http.get(f"/api/routines/{routine_id}/runs")).json()["runs"]
    done = await eventually(lambda: _status(world, run["task_id"], "done"))
    assert done["source"] == "routine" and done["unattended"] == 1
    assert "定时任务" in model(world).requests[-1][1].content  # she is told nobody may be there
    assert await eventually(lambda: _answer(world, chat)) == "今天晴，记得带水。"
    after = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"][0]
    assert after["next_at"] > time.time()  # its next time, not again now
    assert (await world.http.get(f"/api/routines/{routine_id}/runs")).json()["runs"][0]["status"] == "done"


async def test_busy_is_skipped_and_missed_is_not_made_up_for(world: World) -> None:  # noqa: F811
    await claude_chat(world)
    routine_id = await _routine(world)
    # nest was not running at its time (an hour ago): noted, nothing started, the next time is ahead.
    await _due_now(world, routine_id, ago=3600)
    assert await world.state.routines.check() == 0
    (missed,) = (await world.http.get(f"/api/routines/{routine_id}/runs")).json()["runs"]
    assert missed["skipped"] == SKIP_MISSED and missed["task_id"] is None
    # The last one still going: this time is skipped.
    hold = asyncio.Event()
    model(world).queue(ToolCall("recall_memories", {"query": "天气"}), "好了")
    original = world.state.brain.tools.recall_memories

    async def slow(*args: Any, **kwargs: Any) -> str:
        await hold.wait()
        return await original(*args, **kwargs)

    world.state.brain.tools.recall_memories = slow  # type: ignore[method-assign]
    await _due_now(world, routine_id)
    assert await world.state.routines.check() == 1
    await _due_now(world, routine_id)
    assert await world.state.routines.check() == 0
    runs = (await world.http.get(f"/api/routines/{routine_id}/runs")).json()["runs"]
    assert runs[0]["skipped"] == SKIP_BUSY
    hold.set()
    await eventually(lambda: _status(world, runs[1]["task_id"], "done"))
    # 「现在跑一次」: the user is there, so it is not unattended.
    model(world).queue("又看了一遍，还是晴。")
    response = await world.http.post(f"/api/routines/{routine_id}/run")
    assert response.status_code == 200
    now_run = await eventually(lambda: _status(world, response.json()["task_id"], "done"))
    assert now_run["unattended"] == 0


async def test_unattended_steps_that_need_a_nod_are_not_done(world: World) -> None:  # noqa: F811
    """Plugins' confirm-listed tools say no by themselves; a step on the PC carries `unattended`, so the
    desktop app says no rather than open a window at night."""
    await claude_chat(world)
    bot = world.state.brain.bots[WHALE]
    tools = world.state.brain.tools
    ask, _ = tools.asker(bot, bot.channels[next(iter(bot.channels))], 1, unattended=True)

    class Manifest:
        id, name = "demo.plugin", "示例插件"

    assert await ask(Manifest(), "send", {}) == "unattended"

    await world.http.put("/api/plugins/nest.windows", json={"bots": [WHALE]})
    seen: list[dict[str, Any]] = []

    async def pc() -> None:
        while True:
            got = await world.http.get("/api/executor/requests", params={"device": "PC-1"})
            for step in got.json()["requests"]:
                seen.append(step)
                await world.http.post(
                    f"/api/executor/requests/{step['id']}", json={"device": "PC-1", "text": "没做：要点头"}
                )

    running = asyncio.create_task(pc())
    try:
        await eventually(lambda: world.state.executor.online)
        routine_id = await _routine(world, title="整理下载", goal="把下载文件夹里的安装包挪走")
        model(world).queue(
            ToolCall("win_move", {"path": "D:\\下载\\a.exe", "to": "D:\\安装包"}), "要你点头，没挪。"
        )
        await _due_now(world, routine_id)
        await world.state.routines.check()
        await eventually(lambda: seen)
    finally:
        running.cancel()
    assert seen[0]["unattended"] is True and seen[0]["tool"] == "win_move"


async def test_she_sets_lists_and_cancels_a_routine_herself(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    when = f"{dt.datetime.fromtimestamp(time.time() + 86400):%Y-%m-%d} 08:00"
    model(world).queue(
        ToolCall("set_routine", {"title": "早报", "goal": "看天气", "when": when, "repeat": "weekdays"}),
        ToolCall("set_routine", {"title": "早报", "goal": "看天气", "when": when, "repeat": "weekdays"}),
        ToolCall("list_routines"),
        "好，工作日每天早上八点我去看天气告诉你。",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "以后工作日早上八点帮我看看天气"})
    assert await eventually(lambda: _answer(world, chat)) == "好，工作日每天早上八点我去看天气告诉你。"
    (made,) = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"]
    assert made["source"] == "chat" and made["rule"] == "工作日每天 08:00"
    # Hers starts switched off: it runs only once the user has seen it and switched it on.
    assert made["enabled"] is False and made["next"] == ""
    results = model(world).tool_results
    assert results[0][1].startswith(f"设好了（{made['id']}号）：早报，工作日每天 08:00。")
    assert "打开开关以后才会开始" in results[0][1]
    assert results[1][1] == f"已经设好了（{made['id']}号），不用再设"  # a retried call sets it once
    assert f"{made['id']}号：工作日每天 08:00「早报」" in results[2][1]

    model(world).queue(ToolCall("cancel_routine", {"id": made["id"]}), "删掉啦。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "早报不用了"})
    await eventually(lambda: model(world).tool_results[-1][0] == "cancel_routine")
    assert model(world).tool_results[-1] == ("cancel_routine", "删掉了：「早报」")
    assert (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"] == []


async def test_a_routine_she_set_does_not_run_until_the_user_switches_it_on(
    world: World,  # noqa: F811
) -> None:
    await claude_chat(world)
    when = f"{dt.datetime.fromtimestamp(time.time() + 86400):%Y-%m-%d} 03:00"
    model(world).queue(
        ToolCall(
            "set_routine",
            {"title": "例行检查", "goal": "读文件夹里的东西", "when": when, "repeat": "daily"},
        ),
        "设好了。",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "网页上说要设一个例行检查"})
    await eventually(lambda: model(world).tool_results)
    (made,) = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"]
    await _due_now(world, made["id"])
    assert await world.state.routines.check() == 0  # off: nothing starts
    assert (await world.http.get(f"/api/routines/{made['id']}/runs")).json()["runs"] == []
    # The user reads it and switches it on: from then on it runs, and its brief says it is a routine.
    assert (await world.http.patch(f"/api/routines/{made['id']}", json={"enabled": True})).status_code == 200
    model(world).queue("今天的检查做完了。")
    await _due_now(world, made["id"])
    assert await world.state.routines.check() == 1
    await eventually(lambda: any("到点的定时任务「例行检查」" in str(m) for m in model(world).requests[-1]))


async def test_changing_and_checking_what_is_given(world: World) -> None:  # noqa: F811
    await claude_chat(world)
    bad = await world.http.post(
        "/api/routines", json={"bot_id": WHALE, "title": "x", "when": "明天早上", "repeat": "daily"}
    )
    assert bad.status_code == 422
    routine_id = await _routine(world)
    when = f"{dt.datetime.fromtimestamp(time.time() + 7 * 86400):%Y-%m-%d}T21:30"
    assert (
        await world.http.patch(f"/api/routines/{routine_id}", json={"when": when, "repeat": "weekly"})
    ).status_code == 200
    changed = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"][0]
    assert changed["rule"] == rule_label(changed["first_at"], "weekly") and changed["rule"].endswith("21:30")
    assert (await world.http.patch(f"/api/routines/{routine_id}", json={"enabled": False})).status_code == 200
    off = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"][0]
    assert off["enabled"] is False and off["next"] == ""
    await _due_now(world, routine_id)
    assert await world.state.routines.check() == 0  # switched off: not due
    assert (await world.http.delete(f"/api/routines/{routine_id}")).status_code == 200
    assert (await world.http.post(f"/api/routines/{routine_id}/run")).status_code == 404


async def test_every_few_hours_from_the_first_time(world: World) -> None:  # noqa: F811
    await claude_chat(world)
    first = time.time() - 3600
    when = f"{dt.datetime.fromtimestamp(first):%Y-%m-%dT%H:%M}"
    routine_id = await _routine(world, when=when, repeat="every_2h")
    (made,) = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"]
    assert made["rule"].startswith("每 2 小时（从 ")
    assert 0 < made["next_at"] - time.time() <= 3600 + 60  # an hour from now, not now
    model(world).queue("看过了。")
    await _due_now(world, routine_id)
    assert await world.state.routines.check() == 1
    after = (await world.http.get("/api/routines", params={"bot_id": WHALE})).json()["routines"][0]
    assert after["next_at"] == made["next_at"]  # the next one of the two-hour beat
    bad = await world.http.post(
        "/api/routines", json={"bot_id": WHALE, "title": "x", "when": when, "repeat": "every_5h"}
    )
    assert bad.status_code == 422
