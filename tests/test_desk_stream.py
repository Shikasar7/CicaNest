"""逐字流式: an answer in the desk chat shown while it is written, and the model calls that stream."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest.core import desk_stream
from nest.core.desk_stream import DeskStream, visible
from nest.llm.anthropic import Anthropic
from nest.llm.base import ChatMessage, ProviderError, Tool
from nest.llm.codex import Codex
from nest.llm.openai_chat import OpenAIChat
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat, rows
from tests.test_desk_edits import model, say

PROMPT = [ChatMessage("system", "你是测试"), ChatMessage("user", "在吗")]
LONG = "今天天气很好，适合出门走走，顺便买点水果回来，晚上可以做一个简单的水果沙拉，再泡一壶茶慢慢喝。"
TOOL = Tool("look", "看一眼")


def sse(*events: dict[str, Any], done: bool = True) -> httpx.Response:
    body = "".join(f"event: x\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    return httpx.Response(
        200, text=body + ("data: [DONE]\n\n" if done else ""), headers={"content-type": "text/event-stream"}
    )


# -- what is shown ----------------------------------------------------------------------------------------


def test_what_of_an_answer_may_be_shown() -> None:
    assert visible(LONG[:20], answer=False) == ""  # a short answer comes in one go
    assert visible(LONG, answer=False) == LONG
    assert visible("SKIP" + LONG, answer=False) == ""
    assert visible(f"<think>先想想{LONG}", answer=False) == ""
    assert visible(f"<think>先想想</think>{LONG}", answer=False) == LONG
    # Marks are never seen, not even half written.
    assert visible(f"{LONG} [表情1] [语音]", answer=False) == LONG
    assert visible(f"{LONG} [表", answer=False) == LONG
    assert visible(f"{LONG}\n\n第二行 [拉群：吃货群｜阿", answer=False) == f"{LONG}\n第二行"
    # A draft in the open: only what comes after its mark.
    assert visible(f"{LONG}\n输出：", answer=False) == ""
    assert visible(f"盘算一下\n输出：\n{LONG}", answer=False) == LONG
    assert "**" not in visible(f"**重点**：{LONG}", answer=True)


def test_a_stream_is_looked_at_now_and_then(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desk_stream, "EVERY", 0.0)
    seen: list[str] = []
    stream = DeskStream(seen.append, answer=False)
    stream.feed(None)
    for start in range(0, len(LONG), 10):
        stream.feed(LONG[start : start + 10])
    assert seen[-1] == LONG and all(LONG.startswith(s) for s in seen) and len(seen) > 1
    stream.feed(None)  # a tool was called: not the answer after all
    assert seen[-1] == "" and stream.shown == ""


async def test_an_answer_is_shown_while_it_is_written(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desk_stream, "EVERY", 0.0)
    chat = await private_chat(world)
    model(world).streams = True
    model(world).letters = 0.01
    model(world).queue(LONG)
    await say(world, "今天做什么")
    decision = await eventually(lambda: [d for kind, d in world.events if kind == "decision"])
    assert decision[0]["outcome"] == "replied"
    await eventually(lambda: [d for kind, d in world.events if kind == "message" and d["from_self"]])
    deltas = [d for kind, d in world.events if kind == "desk_delta"]
    assert deltas and all(d["channel_id"] == chat and LONG.startswith(d["text"]) for d in deltas)
    kinds = [kind for kind, _ in world.events]
    assert kinds.index("desk_delta") < len(kinds) - 1 - kinds[::-1].index("message")
    assert [r["text"] for r in await rows(world, chat) if r["from_self"]] == [LONG]


async def test_nothing_is_shown_of_what_is_not_said(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desk_stream, "EVERY", 0.0)
    await private_chat(world)
    model(world).streams = True
    model(world).queue("SKIP")
    await say(world, "嗯嗯")
    await eventually(lambda: [d for kind, d in world.events if kind == "decision"])
    assert [d for kind, d in world.events if kind == "desk_delta"] == []


# -- the model calls --------------------------------------------------------------------------------------


async def test_chat_completions_streamed_with_a_tool() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            part = {"index": 0, "id": "c1", "function": {"name": "look", "arguments": '{"a"'}}
            rest = {"index": 0, "function": {"arguments": ": 1}"}}
            return sse(
                {"choices": [{"delta": {"content": "我看看"}}]},
                {"choices": [{"delta": {"tool_calls": [part]}}]},
                {"choices": [{"delta": {"tool_calls": [rest]}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
            )
        return sse(
            {"choices": [{"delta": {"content": "看"}}]},
            {"choices": [{"delta": {"content": "完了"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        )

    async def run(name: str, given: dict[str, Any]) -> str:
        assert (name, given) == ("look", {"a": 1})
        return "有一只猫"

    told: list[str | None] = []
    provider = OpenAIChat(name="g", base_url="http://x/v1", model="m", transport=httpx.MockTransport(handler))
    done = await provider.complete(PROMPT, max_tokens=80, tools=[TOOL], run=run, on_text=told.append)
    assert done.text == "看完了" and not done.cut and done.tools_used == (("look", '{"a": 1}'),)
    assert (done.prompt_tokens, done.completion_tokens) == (12, 5)
    assert told == ["我看看", None, "看", "完了"]
    assert bodies[0]["stream"] and bodies[1]["messages"][-1]["content"] == "有一只猫"


async def test_a_gateway_that_does_not_stream_is_asked_the_usual_way() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if bodies[-1]["stream"]:
            return httpx.Response(400, json={"error": {"message": "stream_options"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "在的"}}]})

    provider = OpenAIChat(name="g", base_url="http://x/v1", model="m", transport=httpx.MockTransport(handler))
    for _ in range(2):
        assert (await provider.complete(PROMPT, max_tokens=80, on_text=lambda _: None)).text == "在的"
    assert [b["stream"] for b in bodies] == [True, False, False]

    def plain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "在的"}}]})

    provider = OpenAIChat(name="g", base_url="http://x/v1", model="m", transport=httpx.MockTransport(plain))
    assert (await provider.complete(PROMPT, max_tokens=80, on_text=lambda _: None)).text == "在的"


async def test_anthropic_streamed_with_a_tool() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        start = {"type": "message_start", "message": {"usage": {"input_tokens": 9, "output_tokens": 1}}}
        if len(bodies) == 1:
            return sse(
                start,
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "嗯"},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig"},
                },
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "我看看"},
                },
                {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {"type": "tool_use", "id": "t1", "name": "look", "input": {}},
                },
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {"type": "input_json_delta", "partial_json": '{"a": 1}'},
                },
                {"type": "content_block_stop", "index": 2},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 4},
                },
                done=False,
            )
        return sse(
            start,
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "看"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "完了"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
            done=False,
        )

    async def run(name: str, given: dict[str, Any]) -> str:
        assert (name, given) == ("look", {"a": 1})
        return "有一只猫"

    told: list[str | None] = []
    provider = Anthropic(
        name="c", base_url="http://x/v1", model="claude-opus-5-5", transport=httpx.MockTransport(handler)
    )
    done = await provider.complete(PROMPT, max_tokens=80, tools=[TOOL], run=run, on_text=told.append)
    assert done.text == "看完了" and done.tools_used == (("look", '{"a": 1}'),)
    assert (done.prompt_tokens, done.completion_tokens) == (18, 7)
    assert told == ["我看看", None, "看", "完了"]
    sent = bodies[1]["messages"][-2]["content"]
    assert sent[0] == {"type": "thinking", "thinking": "嗯", "signature": "sig"}
    assert sent[2]["input"] == {"a": 1}

    broken = Anthropic(
        name="c",
        base_url="http://x/v1",
        model="claude-opus-5-5",
        transport=httpx.MockTransport(
            lambda _: sse({"type": "error", "error": {"message": "overloaded"}}, done=False)
        ),
    )
    with pytest.raises(ProviderError, match="overloaded") as failed:
        await broken.complete(PROMPT, max_tokens=80, on_text=lambda _: None)
    assert failed.value.retryable


async def test_codex_streamed(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    tokens = {"access_token": "old", "refresh_token": "r", "account_id": "acc"}
    auth.write_text(json.dumps({"tokens": tokens}), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "new"})
        if request.headers["authorization"] == "Bearer old":
            return httpx.Response(401)
        return sse(
            {"type": "response.output_text.delta", "delta": "在"},
            {"type": "response.output_text.delta", "delta": "的"},
            {"type": "response.completed", "response": {"output": [], "usage": {"output_tokens": 2}}},
            done=False,
        )

    provider = Codex(name="c", model="m", auth_file=auth, transport=httpx.MockTransport(handler))
    told: list[str | None] = []
    done = await provider.complete(PROMPT, max_tokens=80, on_text=told.append)
    assert done.text == "在的" and told == ["在", "的"] and done.completion_tokens == 2
