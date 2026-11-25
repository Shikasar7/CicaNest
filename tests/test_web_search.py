"""联网搜索 through Tavily for models that cannot search by themselves: a web_search tool when
the key is set, and results fetched beforehand when such a model answers a question (答题)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import nest.core.web_search as web_search
from nest.core.prompt import SEARCHED_HINT
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model


async def test_the_search_service_is_asked_with_the_key_and_read() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.headers["authorization"] != "Bearer k-123":
            return httpx.Response(401)
        results = [
            {"title": "明天天气", "url": "https://w.example/1", "content": "晴，  18 度"},
            {"title": "", "url": "https://w.example/2", "content": "x" * 2000},
            {"title": "没有地址"},
        ]
        return httpx.Response(200, json={"results": results})

    transport = httpx.MockTransport(handler)
    found = await web_search.search("  明天  天气 ", key="k-123", transport=transport)
    assert json.loads(sent[0].content)["query"] == "明天 天气"
    assert found[0] == {"title": "明天天气", "url": "https://w.example/1", "content": "晴， 18 度"}
    assert len(found) == 2 and len(found[1]["content"]) == web_search.SNIPPET_CHARS
    assert "1. 明天天气\nhttps://w.example/1" in web_search.render(found)
    with pytest.raises(web_search.SearchError, match="key 不对"):
        await web_search.search("天气", key="wrong", transport=transport)


def _fake_search(monkeypatch: pytest.MonkeyPatch, asked: list[str]) -> None:
    async def fake(query: str, **_: Any) -> list[dict[str, str]]:
        asked.append(query)
        return [{"title": "某某发布会", "url": "https://n.example/a", "content": "发布会定在 12 月 3 日"}]

    monkeypatch.setattr(web_search, "search", fake)


async def test_with_a_key_she_can_search_and_gets_material(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = await claude_chat(world)
    asked: list[str] = []
    _fake_search(monkeypatch, asked)
    model(world).queue("在呢")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "在吗"})
    await eventually(lambda: _answer(world, chat))
    assert "web_search" not in model(world).tool_lists[-1]  # no key: not offered

    monkeypatch.setenv(web_search.KEY, "k-123")
    model(world).queue(ToolCall("web_search", {"query": "发布会 日期"}), "12 月 3 日。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "发布会哪天"})

    async def answered() -> str | None:
        said = await _answer(world, chat)
        return said if said == "12 月 3 日。" else None

    assert await eventually(answered) == "12 月 3 日。"
    assert "web_search" in model(world).tool_lists[1], model(world).tool_lists
    assert asked == ["发布会 日期"]
    (name, result) = model(world).tool_results[-1]
    assert name == "web_search" and "只是资料" in result and "12 月 3 日" in result


async def test_a_model_that_cannot_search_gets_results_before_it_answers(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = await claude_chat(world)  # Claude without its own search option: cannot search by itself
    await world.http.patch(f"/api/bots/{WHALE}/channels/{chat}", json={"answer_questions": True})
    monkeypatch.setenv(web_search.KEY, "k-123")
    asked: list[str] = []
    _fake_search(monkeypatch, asked)
    model(world).queue('{"kind": "question", "topic": "发布会是哪天"}', "12 月 3 日。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "那个发布会是哪天来着？"})
    assert await eventually(lambda: _answer(world, chat)) == "12 月 3 日。"
    assert asked == ["发布会是哪天"]
    prompt = "\n".join(m.content for m in model(world).requests[-1])
    assert SEARCHED_HINT in prompt and "发布会定在 12 月 3 日" in prompt and "只是资料" in prompt
