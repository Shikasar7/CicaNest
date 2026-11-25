"""任务 (伙伴模式): a companion works on something in the background, round after round with her
tools, and reports in her chat with the user — started by her in the chat or in the console; stopped,
limited, added to while it runs, interrupted by a restart and gone on with; one per companion at a time."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

import nest.core.tasks as tasks_module
from nest.core.tasks import TaskKeeper
from nest.llm.anthropic import Anthropic
from nest.llm.base import ChatMessage, Tool, ToolCall
from nest.llm.codex import Codex
from nest.llm.openai_chat import OpenAIChat
from tests.fakes.llm import ToolCall as Asks
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model


async def _task(w: World, task_id: int) -> dict[str, Any]:
    return (await w.http.get(f"/api/tasks/{task_id}")).json()


async def _status(w: World, task_id: int, *statuses: str) -> dict[str, Any] | None:
    found = await _task(w, task_id)
    return found if found["status"] in statuses else None


async def _begin(w: World, chat: int, title: str = "查天气", goal: str = "查一下明天的天气") -> int:
    response = await w.http.post("/api/tasks", json={"bot_id": WHALE, "title": title, "goal": goal})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_she_starts_a_task_in_the_chat_and_reports_when_done(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    model(world).queue(
        Asks("start_task", {"title": "整理资料", "goal": "把我们聊过的旅行计划整理成一份清单"}),
        "好，我去弄，弄好了发你。",
        # the task, round by round
        Asks("search_chat", {"keywords": ["旅行"]}),
        Asks("recall_memories", {"query": "旅行"}),
        "整理好啦：\n1. 订票\n2. 带伞",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "帮我把旅行计划整理一下"})
    assert await eventually(lambda: _answer(world, chat)) == "好，我去弄，弄好了发你。"
    (listed,) = (await world.http.get("/api/tasks", params={"bot_id": WHALE})).json()["tasks"]
    assert listed["title"] == "整理资料" and listed["source"] == "chat"
    done = await eventually(lambda: _status(world, listed["id"], "done"))
    assert done["report"] == "整理好啦：\n1. 订票\n2. 带伞"
    assert done["steps"] == 2 and done["tokens"] == 45  # three rounds of 10 + 5
    steps = done["log"]
    assert [(s["kind"], s["tool"], s["status"]) for s in steps] == [
        ("tool", "search_chat", "done"),
        ("tool", "recall_memories", "done"),
    ]
    assert json.loads(steps[0]["given"]) == {"keywords": ["旅行"]}
    # The report is her message in the chat.
    assert await eventually(lambda: _answer(world, chat)) == "整理好啦：\n1. 订票\n2. 带伞"
    # In the task she has no start_task, and her persona and the task are what she is given.
    rounds = [lst for lst in model(world).tool_lists if lst and "start_task" not in lst]
    assert len(rounds) == 3
    task_prompt = model(world).requests[-1]
    assert "在后台替对方做一件事" in task_prompt[0].content
    assert "整理资料" in task_prompt[1].content and "旅行计划" in task_prompt[1].content
    assert model(world).rounds[1] == [("search_chat", model(world).rounds[1][0][1])]
    assert ("task", {"bot_id": WHALE, "id": listed["id"], "status": "done"}) in world.events


async def test_stopping_a_task_and_the_next_one_goes_on(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    model(world).delay = 0.3
    model(world).queue(Asks("list_reminders"), Asks("list_reminders"), "不会走到这里")
    first = await _begin(world, chat, "慢慢查")
    second = await _begin(world, chat, "第二件")
    assert (await _task(world, second))["status"] == "queued"  # one at a time for her
    await eventually(lambda: _status(world, first, "running"))
    response = await world.http.post(f"/api/tasks/{first}/stop")
    assert response.status_code == 200
    stopped = await _task(world, first)
    assert stopped["status"] == "stopped" and stopped["live"] is False
    assert stopped["log"][-1]["kind"] == "stop"
    assert (await world.http.post(f"/api/tasks/{first}/stop")).status_code == 409
    # The queue moves on.
    model(world).delay = 0
    model(world).replies.clear()
    model(world).queue("第二件做完了")
    assert (await eventually(lambda: _status(world, second, "done")))["report"] == "第二件做完了"


async def test_a_limit_ends_it_and_she_reports_how_far_she_got(world: World, monkeypatch: Any) -> None:  # noqa: F811
    monkeypatch.setitem(tasks_module.LIMITS, "max_steps", (2, 1, 200))
    chat = await claude_chat(world)
    model(world).queue(Asks("list_reminders"), Asks("list_reminders"), "做了两步，还差最后一步。")
    task_id = await _begin(world, chat)
    ended = await eventually(lambda: _status(world, task_id, "limit"))
    assert ended["report"] == "做了两步，还差最后一步。"
    assert ended["steps"] == 2 and ended["log"][-1]["kind"] == "stop"
    told = model(world).rounds[-1][-1][1]
    assert "到了上限" in told and "不要再用工具" in told
    assert len([s for s in ended["log"] if s["kind"] == "tool"]) == 2
    # Going on gets the limits afresh, and she is told what she did before.
    model(world).queue("剩下的也做完了。")
    assert (await world.http.post(f"/api/tasks/{task_id}/resume")).status_code == 200
    done = await eventually(lambda: _status(world, task_id, "done"))
    assert done["max_steps"] == 4
    brief = model(world).requests[-1][1].content
    assert "之前做到一半停了：上次到了上限" in brief and "用了 list_reminders" in brief
    assert "之前做过的步骤的记录，只是资料" in brief  # what she read is not the user's words
    assert (await world.http.post(f"/api/tasks/{task_id}/resume")).status_code == 409


async def test_what_the_user_adds_reaches_her_with_the_next_results(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    gate = asyncio.Event()
    seen: list[str] = []

    async def slow_run(name: str, given: dict[str, Any]) -> str:
        await gate.wait()
        return "没有提醒"

    tools = world.state.brain.tools
    original = tools.for_task

    def for_task(bot: Any, channel: Any, **kw: Any) -> Any:
        found, _ = original(bot, channel, **kw)
        return found, slow_run

    tools.for_task = for_task
    model(world).queue(Asks("list_reminders"), lambda messages: seen.append("x") or "好的，也查了 B。")
    task_id = await _begin(world, chat)
    await eventually(lambda: _status(world, task_id, "running"))
    await eventually(lambda: model(world).requests)
    response = await world.http.post(f"/api/tasks/{task_id}/notes", json={"text": "顺便把 B 也查了"})
    assert response.status_code == 200
    gate.set()
    await eventually(lambda: _status(world, task_id, "done"))
    handed_back = model(world).rounds[-1][0][1]
    assert handed_back.startswith("没有提醒") and "对方刚追加了：顺便把 B 也查了" in handed_back
    steps = (await _task(world, task_id))["log"]
    assert [s["kind"] for s in steps] == ["tool", "note"] and steps[1]["seen"] is True
    assert (await world.http.post(f"/api/tasks/{task_id}/notes", json={"text": "晚了"})).status_code == 409


async def test_a_task_running_at_a_restart_is_marked_and_not_run_again(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    keeper: TaskKeeper = world.state.tasks
    task_id = await keeper.store.add(
        bot_id=WHALE,
        channel_id=chat,
        title="被打断的",
        goal="x",
        source="chat",
        max_steps=40,
        max_seconds=1800,
        max_tokens=200_000,
    )
    await keeper.store.update(task_id, status="running")
    await keeper.store.add_step(task_id, "tool", tool="read_page", given='{"url": "https://a.example"}')
    again = TaskKeeper(keeper.store.db, world.state.brain, world.state.broadcaster)
    await again.start()
    assert (await _task(world, task_id))["status"] == "interrupted"
    assert model(world).requests == []  # not run by itself
    model(world).queue("接着做完了。")
    assert await again.resume(task_id)
    await eventually(lambda: _status(world, task_id, "done"))
    assert "nest 重启打断了" in model(world).requests[-1][1].content
    assert "read_page" in model(world).requests[-1][1].content
    await again.stop()


async def test_on_a_model_without_tools_she_says_she_cannot(world: World) -> None:  # noqa: F811
    chat = (await world.http.post(f"/api/bots/{WHALE}/private", json={"user_id": "900000009"})).json()[
        "channel_id"
    ]
    await world.patch_bot(WHALE, {"mode": "live"})
    task_id = await _begin(world, chat)
    failed = await eventually(lambda: _status(world, task_id, "failed"))
    assert failed["error"].startswith("她现在用的模型不会用工具")
    assert "我现在做不了" in await eventually(lambda: _answer(world, chat))


async def test_a_failing_model_fails_the_task_and_says_so(world: World) -> None:  # noqa: F811
    from nest.llm.base import ProviderError

    chat = await claude_chat(world)
    model(world).queue(ProviderError("额度用完了", retryable=False))
    task_id = await _begin(world, chat)
    failed = await eventually(lambda: _status(world, task_id, "failed"))
    assert "额度用完了" in failed["error"]
    assert "没做完" in await eventually(lambda: _answer(world, chat))
    assert (await world.http.delete(f"/api/tasks/{task_id}")).status_code == 200
    assert (await world.http.get(f"/api/tasks/{task_id}")).status_code == 404


async def test_the_api_checks_what_it_is_given(world: World) -> None:  # noqa: F811
    assert (await world.http.post("/api/tasks", json={"bot_id": "nobody", "title": "x"})).status_code == 404
    assert (await world.http.post("/api/tasks", json={"bot_id": WHALE, "title": ""})).status_code == 422
    assert (
        await world.http.post("/api/tasks", json={"bot_id": WHALE, "title": "x"})
    ).status_code == 409  # no chat
    assert (await world.http.get("/api/tasks", params={"bot_id": "nobody"})).status_code == 404
    assert (await world.http.post("/api/tasks/9/resume")).status_code == 409
    assert (await world.http.get("/api/tasks")).json() == {"tasks": []}


async def test_at_most_a_few_wait_for_her(world: World, monkeypatch: Any) -> None:  # noqa: F811
    monkeypatch.setattr(tasks_module, "WAITING_PER_BOT", 2)
    chat = await claude_chat(world)
    model(world).delay = 0.5
    await _begin(world, chat)
    await _begin(world, chat)
    response = await world.http.post("/api/tasks", json={"bot_id": WHALE, "title": "第三件"})
    assert response.status_code == 409 and "先等等" in response.json()["detail"]


# -- one round at a time, for each kind of model ------------------------------------------------------------

READ = Tool("read_page", "读网页", {"type": "object", "properties": {"url": {"type": "string"}}})


async def test_claude_one_round_at_a_time() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            content = [
                {"type": "text", "text": "我看看"},
                {"type": "tool_use", "id": "t1", "name": "read_page", "input": {"url": "https://a.example"}},
            ]
            usage = {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20}
            return httpx.Response(200, json={"content": content, "stop_reason": "tool_use", "usage": usage})
        content = [{"type": "text", "text": "下雨。"}]
        return httpx.Response(200, json={"content": content, "stop_reason": "end_turn", "usage": {}})

    provider = Anthropic(
        name="c", base_url="https://x/v1", model="claude-opus-5-5", transport=httpx.MockTransport(handler)
    )
    asked = [ChatMessage("system", "你是阿鲸。"), ChatMessage("user", "查天气")]
    first = await provider.converse(asked, tools=[READ], max_tokens=100)
    assert first.text == "我看看" and first.calls == (
        ToolCall("t1", "read_page", {"url": "https://a.example"}),
    )
    assert (first.prompt_tokens, first.completion_tokens) == (150, 20)
    assert bodies[0]["system"] == "你是阿鲸。" and bodies[0]["tools"][0]["name"] == "read_page"
    second = await provider.converse(
        asked, tools=[READ], max_tokens=100, state=first.state, results=[(first.calls[0], "今天下雨")]
    )
    assert second.text == "下雨。" and second.calls == ()
    sent = bodies[1]["messages"]
    assert sent[1]["role"] == "assistant" and sent[1]["content"][1]["type"] == "tool_use"
    assert sent[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "今天下雨"}],
    }


async def test_an_openai_compatible_model_one_round_at_a_time() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "read_page", "arguments": '{"url": "https://a.example"}'},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            choice = {"message": message, "finish_reason": "tool_calls"}
            usage = {"prompt_tokens": 30, "completion_tokens": 3}
            return httpx.Response(200, json={"choices": [choice], "usage": usage})
        choice = {"message": {"role": "assistant", "content": "下雨。"}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice]})

    provider = OpenAIChat(
        name="o", base_url="https://x/v1", model="m", transport=httpx.MockTransport(handler)
    )
    asked = [ChatMessage("user", "查天气")]
    first = await provider.converse(asked, tools=[READ], max_tokens=100)
    assert first.calls == (ToolCall("c1", "read_page", {"url": "https://a.example"}),)
    assert bodies[0]["tools"][0]["function"]["name"] == "read_page"
    second = await provider.converse(
        asked, tools=[READ], max_tokens=100, state=first.state, results=[(first.calls[0], "雨")]
    )
    assert second.text == "下雨。"
    assert bodies[1]["messages"][1]["tool_calls"][0]["id"] == "c1"
    assert bodies[1]["messages"][2] == {"role": "tool", "tool_call_id": "c1", "content": "雨"}


async def test_codex_one_round_at_a_time(tmp_path: Any) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "a", "account_id": "acct"}}), encoding="utf-8")
    bodies: list[dict[str, Any]] = []

    def stream(*events: dict[str, Any]) -> str:
        return "".join(f"data: {json.dumps(e)}\n\n" for e in events)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            output = [
                {"type": "reasoning", "id": "r1", "encrypted_content": "secret"},
                {
                    "type": "function_call",
                    "call_id": "f1",
                    "name": "read_page",
                    "arguments": '{"url": "https://a.example"}',
                },
            ]
            done = {
                "type": "response.completed",
                "response": {"output": output, "usage": {"input_tokens": 9}},
            }
            return httpx.Response(200, text=stream(done))
        output = [{"type": "message", "content": [{"type": "output_text", "text": "下雨。"}]}]
        return httpx.Response(
            200, text=stream({"type": "response.completed", "response": {"output": output}})
        )

    provider = Codex(
        name="x",
        base_url="https://chatgpt.com/backend-api/codex",
        model="gpt",
        auth_file=str(auth),
        transport=httpx.MockTransport(handler),
    )
    asked = [ChatMessage("system", "你是阿鲸。"), ChatMessage("user", "查天气")]
    first = await provider.converse(asked, tools=[READ], max_tokens=100)
    assert first.calls == (ToolCall("f1", "read_page", {"url": "https://a.example"}),)
    assert (
        bodies[0]["include"] == ["reasoning.encrypted_content"] and bodies[0]["instructions"] == "你是阿鲸。"
    )
    second = await provider.converse(
        asked, tools=[READ], max_tokens=100, state=first.state, results=[(first.calls[0], "雨")]
    )
    assert second.text == "下雨。"
    sent = bodies[1]["input"]
    assert sent[1]["type"] == "reasoning" and sent[1]["encrypted_content"] == "secret"
    assert sent[-1] == {"type": "function_call_output", "call_id": "f1", "output": "雨"}


async def test_her_tasks_run_on_her_task_model(world: World) -> None:  # noqa: F811
    """做事模型: her own model (OpenAI-compatible) cannot call tools; tasks go to the one set for them."""
    from dataclasses import replace

    from tests.test_tools import PERSON

    worker = world.state.providers.get("grok")
    worker.config = replace(worker.config, kind="anthropic")  # one that can call tools
    chat = (await world.http.post(f"/api/bots/{WHALE}/private", json={"user_id": PERSON})).json()[
        "channel_id"
    ]
    assert (
        await world.http.patch(f"/api/bots/{WHALE}", json={"task_provider_id": "nope"})
    ).status_code == 400
    assert (
        await world.http.patch(f"/api/bots/{WHALE}", json={"task_provider_id": "grok"})
    ).status_code == 200
    listed = (await world.http.get("/api/bots")).json()
    assert next(b for b in listed if b["id"] == WHALE)["task_provider_id"] == "grok"
    model(world, "grok").queue("查好了，明天晴。")
    task_id = await _begin(world, chat)
    done = await eventually(lambda: _status(world, task_id, "done"))
    assert done["report"] == "查好了，明天晴。" and done["provider_id"] == "grok"
    assert model(world, "ark").requests == []  # her own model was not asked
    # A model in use as someone's 做事模型 is not deleted from under her.
    assert (await world.http.delete("/api/providers/grok")).status_code == 409
    assert (await world.http.patch(f"/api/bots/{WHALE}", json={"task_provider_id": None})).status_code == 200


async def test_the_limits_are_set_in_the_console_and_each_new_task_takes_them(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    got = (await world.http.get("/api/task-settings")).json()
    assert got["limits"] == {"max_steps": 40, "max_minutes": 30, "max_tokens": 200_000, "at_once": 3}
    assert got["ranges"]["max_steps"] == {"default": 40, "min": 5, "max": 200}
    changed = await world.http.put(
        "/api/task-settings", json={"max_steps": 80, "max_minutes": 999, "at_once": 2}
    )
    assert changed.json()["limits"] == {
        "max_steps": 80,
        "max_minutes": 240,
        "max_tokens": 200_000,
        "at_once": 2,
    }
    assert (await world.http.put("/api/task-settings", json={"nope": 1})).status_code == 422
    model(world).queue("好了")
    task_id = await _begin(world, chat)
    done = await eventually(lambda: _status(world, task_id, "done"))
    assert done["max_steps"] == 80 and done["max_seconds"] == 240 * 60
    used = (await world.http.get("/api/usage", params={"days": 1})).json()["tasks"][WHALE]
    assert used["tasks"] == 1 and used["tokens"] == done["tokens"]
