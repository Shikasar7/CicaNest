"""Model providers on mocked HTTP: request shape, parsing, errors, token refresh, retry, breaker."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from nest.llm.anthropic import THINKING_ROOM, Anthropic
from nest.llm.base import ChatMessage, Completion, ProviderError
from nest.llm.codex import INSTRUCTIONS, Codex, parse_sse
from nest.llm.models_list import list_models
from nest.llm.openai_chat import OpenAIChat
from nest.llm.openai_responses import OpenAIResponses, output_text
from nest.llm.runtime import ProviderRegistry, ProviderRuntime, ProviderUnavailable, build_provider
from nest.secrets import SecretStore
from nest.storage.bots import ProviderConfig
from tests.fakes.llm import FakeProvider

PROMPT = [ChatMessage("system", "你是测试"), ChatMessage("user", "在吗")]


def mock(handler) -> httpx.MockTransport:  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


async def test_openai_chat_request_and_parse() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "在的"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2},
            },
        )

    provider = OpenAIChat(
        name="grok",
        base_url="http://127.0.0.1:8180/v1/",
        model="grok-chat-fast",
        api_key="k-123",
        temperature=0.8,
        transport=mock(handler),
    )
    result = await provider.complete(PROMPT, max_tokens=80)
    assert seen["url"] == "http://127.0.0.1:8180/v1/chat/completions" and seen["auth"] == "Bearer k-123"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "你是测试"}
    assert (
        seen["body"]["temperature"] == 0.8 and seen["body"]["max_tokens"] == 80 and not seen["body"]["stream"]
    )
    assert result.text == "在的" and result.prompt_tokens == 12 and result.completion_tokens == 2


@pytest.mark.parametrize(("status", "retryable"), [(502, True), (429, True), (401, False), (400, False)])
async def test_errors_are_mapped_and_never_leak_the_key(status: int, retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "bad key sk-SECRETSECRET123 for k-123"}})

    provider = OpenAIChat(name="x", base_url="http://x", model="m", api_key="k-123", transport=mock(handler))
    with pytest.raises(ProviderError) as failure:
        await provider.complete(PROMPT, max_tokens=10)
    assert failure.value.retryable is retryable
    assert "SECRET" not in str(failure.value) and "k-123" not in str(failure.value)
    assert str(status) in str(failure.value)


async def test_network_errors_are_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OpenAIChat(name="x", base_url="http://x", model="m", transport=mock(handler))
    with pytest.raises(ProviderError) as failure:
        await provider.complete(PROMPT, max_tokens=10)
    assert failure.value.retryable


async def test_responses_flattens_input_and_parses_output() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "reasoning", "content": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "好"}]},
                ],
                "usage": {"input_tokens": 30, "output_tokens": 1},
            },
        )

    provider = OpenAIResponses(
        name="ark",
        base_url="https://ark.example/api/plan/v3",
        model="deepseek",
        api_key="k",
        transport=mock(handler),
    )
    result = await provider.complete(PROMPT, max_tokens=80)
    assert seen["url"] == "https://ark.example/api/plan/v3/responses"
    assert seen["body"] == {"model": "deepseek", "input": "你是测试\n\n在吗", "max_output_tokens": 80}
    assert result.text == "好" and result.prompt_tokens == 30
    assert output_text({"output_text": "直接给的"}) == "直接给的"


def test_codex_sse_prefers_completed_response_and_falls_back_to_deltas() -> None:
    completed = (
        'data: {"type":"response.output_text.delta","delta":"半"}\n'
        'data: {"type":"response.completed","response":{"output_text":"完整","usage":{"input_tokens":5}}}\n'
        "data: [DONE]\n"
    )
    assert parse_sse(completed) == ("完整", {"input_tokens": 5})
    deltas = (
        'data: {"type":"response.output_text.delta","delta":"你"}\n'
        'data: {"type":"response.output_text.delta","delta":"好"}\n'
    )
    assert parse_sse(deltas) == ("你好", {})


async def test_codex_refreshes_an_expired_token_and_writes_it_back(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": "old",
                    "refresh_token": "r1",
                    "account_id": "acct",
                    "id_token": "i",
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), request.headers.get("authorization")))
        if request.url.path.endswith("/oauth/token"):
            assert json.loads(request.content)["refresh_token"] == "r1"
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})
        assert request.headers["chatgpt-account-id"] == "acct"
        if request.headers["authorization"] == "Bearer old":
            return httpx.Response(401, json={"error": {"message": "expired"}})
        body = json.loads(request.content)
        assert body["stream"] is True and body["input"][0]["content"][0]["text"] == "在吗"
        assert body["instructions"] == "你是测试"  # the prompt's system message, not a fixed one
        return httpx.Response(
            200, text='data: {"type":"response.completed","response":{"output_text":"在"}}\n'
        )

    provider = Codex(
        name="codex",
        model="gpt",
        base_url="https://codex.example",
        auth_file=auth,
        token_url="https://auth.example/oauth/token",
        transport=mock(handler),
    )
    result = await provider.complete(PROMPT, max_tokens=80)
    assert result.text == "在"
    assert [c[1] for c in calls] == ["Bearer old", None, "Bearer new"]
    saved = json.loads(auth.read_text(encoding="utf-8"))["tokens"]
    assert saved == {"access_token": "new", "refresh_token": "r2", "account_id": "acct", "id_token": "i"}


async def test_codex_without_login_is_a_clear_error(tmp_path: Path) -> None:
    provider = Codex(name="codex", model="gpt", auth_file=tmp_path / "missing.json")
    with pytest.raises(ProviderError) as failure:
        await provider.complete(PROMPT, max_tokens=10)
    assert "Codex" in str(failure.value) and not failure.value.retryable


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **call) -> int:  # type: ignore[no-untyped-def]
        self.calls.append(call)
        return len(self.calls)


CONFIG = ProviderConfig(id="fake", kind="openai_chat", base_url="http://x", model="m")


async def test_runtime_retries_once_then_succeeds_and_logs_both_attempts() -> None:
    recorder = Recorder()
    runtime = ProviderRuntime(
        CONFIG, FakeProvider(ProviderError("502", retryable=True), "好"), recorder, retry_delay=0
    )
    completion, call_id = await runtime.complete(PROMPT, bot_id="b", max_tokens=10)
    assert completion.text == "好" and call_id == 2
    assert [c["status"] for c in recorder.calls] == ["error", "ok"]
    assert recorder.calls[1]["output"] == "好" and recorder.calls[1]["bot_id"] == "b"


async def test_runtime_does_not_retry_permanent_errors() -> None:
    recorder = Recorder()
    provider = FakeProvider(ProviderError("401", retryable=False), "不该用到")
    runtime = ProviderRuntime(CONFIG, provider, recorder, retry_delay=0)
    with pytest.raises(ProviderError):
        await runtime.complete(PROMPT, bot_id=None, max_tokens=10)
    assert len(provider.requests) == 1


async def test_circuit_breaker_opens_after_repeated_failures() -> None:
    recorder = Recorder()
    failures = [ProviderError("502", retryable=False)] * 3
    runtime = ProviderRuntime(
        CONFIG,
        FakeProvider(*failures, "恢复"),
        recorder,
        breaker_threshold=3,
        breaker_cooldown=60,
        retry_delay=0,
    )
    for _ in range(3):
        with pytest.raises(ProviderError):
            await runtime.complete(PROMPT, bot_id=None, max_tokens=10)
    with pytest.raises(ProviderUnavailable) as unavailable:
        await runtime.complete(PROMPT, bot_id=None, max_tokens=10)
    assert "连续失败 3 次" in str(unavailable.value)
    assert runtime.health_view()["available"] is False and len(recorder.calls) == 3


def test_build_provider_kinds_and_registry_skips_bad_configs(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("KEY", "v")
    assert isinstance(
        build_provider(ProviderConfig("a", "openai_chat", "http://x", "m", "KEY"), secrets), OpenAIChat
    )
    assert isinstance(
        build_provider(ProviderConfig("b", "openai_responses", "http://x", "m"), secrets), OpenAIResponses
    )
    assert isinstance(build_provider(ProviderConfig("c", "codex", "http://x", "m"), secrets), Codex)
    registry = ProviderRegistry.build(
        [
            ProviderConfig("ok", "openai_chat", "http://x", "m"),
            ProviderConfig("bad", "nope", "http://x", "m"),
        ],
        secrets,
        Recorder(),
    )
    assert set(registry.runtimes) == {"ok"}


def test_completion_type() -> None:
    assert Completion("x", 1.0).prompt_tokens is None


async def test_extra_body_is_sent_with_every_request() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json={"output_text": "在"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "在"}}]})

    no_thinking = {"thinking": {"type": "disabled"}}
    responses = OpenAIResponses(
        name="ark", base_url="https://ark/v3", model="m", extra_body=no_thinking, transport=mock(handler)
    )
    chat = OpenAIChat(
        name="c", base_url="http://x/v1", model="m", extra_body={"top_p": 0.9}, transport=mock(handler)
    )
    await responses.complete(PROMPT, max_tokens=60)
    await chat.complete(PROMPT, max_tokens=60)
    assert bodies[0]["thinking"] == {"type": "disabled"} and bodies[0]["max_output_tokens"] == 60
    assert bodies[1]["top_p"] == 0.9 and bodies[1]["max_tokens"] == 60


async def test_codex_may_search_the_web_and_reports_the_pages_it_cites(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "a", "account_id": "acct"}}), encoding="utf-8")
    bodies = []
    stream = "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n"
        for event in [
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": "completed"}},
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": "completed"}},
            {"type": "response.output_item.done", "item": {"type": "message"}},
            *(
                {"type": "response.output_text.annotation.added", "annotation": note}
                for note in [
                    {"type": "url_citation", "title": "Python", "url": "https://python.org/a"},
                    {"type": "url_citation", "title": "Python again", "url": "https://python.org/a"},
                    {"type": "url_citation", "title": "News", "url": "https://news.example/b"},
                ]
            ),
            {"type": "response.completed", "response": {"output_text": "3.14"}},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text=stream)

    provider = Codex(name="codex", model="gpt", auth_file=auth, transport=mock(handler))
    result = await provider.complete(PROMPT, max_tokens=80, search=True)
    assert result.text == "3.14" and result.searches == 2
    assert result.sources == (("Python", "https://python.org/a"), ("News", "https://news.example/b"))
    assert bodies[0]["tools"] == [{"type": "web_search"}] and bodies[0]["tool_choice"] == "auto"
    assert "联网搜索" in bodies[0]["instructions"]
    plain = await provider.complete(PROMPT, max_tokens=80)  # chatting: no tools, nothing searched
    assert "tools" not in bodies[1] and bodies[1]["tool_choice"] == "none"
    assert plain.searches == 0 and plain.sources == ()


async def test_anthropic_request_and_parse() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        content = [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "在"},
            {"type": "text", "text": "的"},
        ]
        usage = {"input_tokens": 3, "cache_read_input_tokens": 9, "output_tokens": 2}
        return httpx.Response(200, json={"content": content, "stop_reason": "end_turn", "usage": usage})

    provider = Anthropic(
        name="claude",
        base_url="https://api.anthropic.com/v1/",
        model="claude-opus-5-5",
        api_key="k-123",
        transport=mock(handler),
    )
    picture = "data:image/png;base64,iVBORw0KGgo="
    prompt = [
        ChatMessage("system", "你是测试"),
        ChatMessage("assistant", "早"),
        ChatMessage("system", "规则二"),
        ChatMessage("user", "看图", images=(picture,)),
    ]
    result = await provider.complete(prompt, max_tokens=80, temperature=0.9)
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "k-123" and "authorization" not in seen["headers"]
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["system"] == "你是测试\n\n规则二" and "temperature" not in body
    assert body["max_tokens"] == 80 + THINKING_ROOM and body["output_config"] == {"effort": "low"}
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]  # starts with the user
    image, words = body["messages"][2]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}
    assert words == {"type": "text", "text": "看图"}
    assert result.text == "在的" and result.prompt_tokens == 12 and result.completion_tokens == 2


async def test_anthropic_for_a_relay_and_its_failures() -> None:
    answers = iter(
        [
            httpx.Response(200, json={"content": [], "stop_reason": "refusal"}),
            httpx.Response(
                529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
            ),
        ]
    )
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return next(answers)

    provider = Anthropic(
        name="relay",
        base_url="https://relay.example/v1",
        model="claude-sonnet-5-5",
        api_key="k-123",
        auth_header="bearer",
        effort="medium",
        transport=mock(handler),
    )
    with pytest.raises(ProviderError) as refused:
        await provider.complete(PROMPT, max_tokens=10)
    assert not refused.value.retryable and "拒绝" in str(refused.value)
    assert seen["headers"]["authorization"] == "Bearer k-123" and "x-api-key" not in seen["headers"]
    assert seen["body"]["output_config"] == {"effort": "medium"}
    with pytest.raises(ProviderError) as busy:
        await provider.complete(PROMPT, max_tokens=10)
    assert busy.value.retryable and "529" in str(busy.value) and "busy" in str(busy.value)


def test_anthropic_is_built_from_config(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("CLAUDE_KEY", "k-1")
    options = {"auth_header": "bearer", "timeout": 60, "effort": "high"}
    config = ProviderConfig(
        "claude", "anthropic", "https://api.anthropic.com/v1", "claude-opus-5-5", "CLAUDE_KEY", options
    )
    provider = build_provider(config, secrets)
    assert isinstance(provider, Anthropic) and provider.model == "claude-opus-5-5"
    assert provider._effort == "high"


async def test_listing_models() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        if "relay" in str(request.url):
            return httpx.Response(401, json={"error": {"message": "bad key k-123"}})
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}, {"x": 1}]})

    names = await list_models("openai_chat", "https://api.example/v1/", "k-1", transport=mock(handler))
    assert names == ["a", "b"] and seen["url"] == "https://api.example/v1/models"
    assert seen["headers"]["authorization"] == "Bearer k-1"

    await list_models("anthropic", "https://api.anthropic.com/v1", "k-2", transport=mock(handler))
    assert seen["url"] == "https://api.anthropic.com/v1/models?limit=1000"
    assert seen["headers"]["x-api-key"] == "k-2" and seen["headers"]["anthropic-version"] == "2023-06-01"

    with pytest.raises(ProviderError) as failure:
        await list_models(
            "anthropic", "https://relay.example/v1", "k-123", auth_header="bearer", transport=mock(handler)
        )
    assert seen["headers"]["authorization"] == "Bearer k-123" and "x-api-key" not in seen["headers"]
    assert "401" in str(failure.value) and "k-123" not in str(failure.value)

    missing = mock(lambda request: httpx.Response(404, text="Not Found"))
    with pytest.raises(ProviderError, match="不提供模型列表"):
        await list_models("openai_responses", "https://ark.example/api/plan/v3", transport=missing)


async def test_codex_draws_a_picture_from_the_event_stream(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "old", "refresh_token": "r", "account_id": "a"}}))
    bodies: list[dict] = []
    picture = base64.b64encode(b"png bytes").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        item = {"type": "image_generation_call", "result": picture}
        return httpx.Response(
            200,
            text=f"data: {json.dumps({'type': 'response.output_item.done', 'item': {'type': 'reasoning'}})}\n"
            f"data: {json.dumps({'type': 'response.output_item.done', 'item': item})}\n",
        )

    provider = Codex(name="codex", model="gpt", auth_file=auth, transport=mock(handler))
    assert await provider.draw("一只猫", ["data:image/png;base64,AA"]) == b"png bytes"
    assert bodies[0]["tools"] == [{"type": "image_generation"}] and bodies[0]["tool_choice"] == "auto"
    assert bodies[0]["input"][0]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AA",
    }
    await provider.aclose()


async def test_codex_says_so_when_no_picture_comes_back(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "old", "refresh_token": "r", "account_id": "a"}}))
    said = {"type": "response.output_text.delta", "delta": "这个不能画"}
    provider = Codex(
        name="codex",
        model="gpt",
        auth_file=auth,
        transport=mock(lambda _: httpx.Response(200, text=f"data: {json.dumps(said, ensure_ascii=False)}\n")),
    )
    with pytest.raises(ProviderError, match="这个不能画") as failure:
        await provider.draw("一只猫")
    assert not failure.value.retryable
    await provider.aclose()


def test_a_codex_prompt_without_a_system_message_is_not_held_to_one_sentence() -> None:
    assert "一句" not in INSTRUCTIONS  # 表情 descriptions are JSON, 截图读字 many lines
