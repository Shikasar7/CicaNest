"""Model runtimes under edits: in-flight tracking, hot swap, closing, unexpected errors, shutdown."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from nest.llm.base import ChatMessage, Completion, ProviderError, without_thought
from nest.llm.codex import Codex
from nest.llm.runtime import ProviderRegistry, ProviderRuntime, ProviderUnavailable
from nest.secrets import SecretStore
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually
from tests.test_providers import PROMPT, Recorder

CONFIG = ProviderConfig(id="m", kind="openai_chat", base_url="http://x", model="m")


class Hung:
    """A provider whose calls never return (until cancelled)."""

    name = "hung"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False

    async def complete(self, messages, *, max_tokens, temperature=None) -> Completion:  # type: ignore[no-untyped-def]
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


async def test_in_flight_counts_queued_calls_and_returns_to_idle() -> None:
    runtime = ProviderRuntime(CONFIG, FakeProvider("一", "二", delay=0.1), Recorder(), concurrency=1)
    calls = [asyncio.create_task(runtime.complete(PROMPT, bot_id=None, max_tokens=10)) for _ in range(2)]
    await asyncio.sleep(0.02)
    assert runtime.in_flight == 2  # one running, one waiting for the semaphore
    await asyncio.gather(*calls)
    assert runtime.in_flight == 0 and runtime._idle.is_set()


async def test_cancelled_call_leaves_the_count() -> None:
    hung = Hung()
    runtime = ProviderRuntime(CONFIG, hung, Recorder())
    call = asyncio.create_task(runtime.complete(PROMPT, bot_id=None, max_tokens=10))
    await hung.started.wait()
    assert runtime.in_flight == 1
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert runtime.in_flight == 0 and runtime._idle.is_set()


async def test_closing_runtime_refuses_new_calls() -> None:
    provider = FakeProvider("不该用到")
    runtime = ProviderRuntime(CONFIG, provider, Recorder())
    closer = runtime.aclose_when_idle()  # flag set synchronously, before the coroutine runs
    with pytest.raises(ProviderUnavailable, match="模型设置刚改过"):
        await runtime.complete(PROMPT, bot_id=None, max_tokens=10)
    await closer
    assert provider.closed and runtime.closed and not provider.requests


async def test_unexpected_exceptions_become_recorded_failures() -> None:
    recorder = Recorder()
    provider = FakeProvider(RuntimeError("boom Bearer sk-test-LEAKCHECK-123"), "好")
    runtime = ProviderRuntime(CONFIG, provider, recorder, retry_delay=0)
    with pytest.raises(ProviderError) as failure:
        await runtime.complete(PROMPT, bot_id="b", max_tokens=10)
    assert str(failure.value) == "模型调用出错：RuntimeError" and not failure.value.retryable
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    [row] = recorder.calls
    assert row["status"] == "error" and row["error"].startswith("RuntimeError: boom")
    assert "LEAKCHECK" not in row["error"]  # scrubbed like any model error
    assert runtime.health.consecutive_failures == 1 and len(provider.requests) == 1  # not retried


async def test_hot_swap_old_call_finishes_then_old_provider_closes() -> None:
    recorder = Recorder()
    registry = ProviderRegistry(recorder, retry_delay=0)
    old = FakeProvider("旧的回答", delay=0.3)
    registry.replace(CONFIG, old)
    first = registry.get("m")
    assert first is not None
    slow = asyncio.create_task(first.complete(PROMPT, bot_id=None, max_tokens=10))
    await eventually(lambda: old.requests)

    new = FakeProvider("新的回答")
    registry.replace(ProviderConfig("m", "openai_chat", "http://y", "m2"), new)
    current = registry.get("m")
    assert current is not None and current is not first and current.provider is new
    with pytest.raises(ProviderUnavailable):
        await first.complete(PROMPT, bot_id=None, max_tokens=10)  # the old one takes no new calls
    await asyncio.sleep(0.1)
    assert not old.closed  # its slow call is still running

    completion, _ = await slow
    assert completion.text == "旧的回答"
    await eventually(lambda: old.closed)
    assert (await current.complete(PROMPT, bot_id=None, max_tokens=10))[0].text == "新的回答"
    assert not new.closed
    await registry.aclose()
    assert new.closed


async def test_shutdown_is_bounded_with_a_hung_call() -> None:
    registry = ProviderRegistry(Recorder())
    hung = Hung()
    registry.replace(CONFIG, hung)
    runtime = registry.get("m")
    assert runtime is not None
    call = asyncio.create_task(runtime.complete(PROMPT, bot_id=None, max_tokens=10))
    await hung.started.wait()
    fresh = FakeProvider()
    registry.replace(CONFIG, fresh)  # the hung runtime is retired and waits for its call
    started = time.monotonic()
    await registry.aclose()
    assert time.monotonic() - started < 3
    assert hung.closed and fresh.closed
    call.cancel()
    await asyncio.gather(call, return_exceptions=True)


async def test_remove_retires_and_replace_needs_a_recorder() -> None:
    registry = ProviderRegistry(Recorder())
    provider = FakeProvider()
    registry.replace(CONFIG, provider)
    registry.set_error("m", "旧错误")  # a later failure note is cleared by remove
    registry.remove("m")
    assert registry.get("m") is None and "m" not in registry.errors
    await eventually(lambda: provider.closed)
    with pytest.raises(RuntimeError):
        ProviderRegistry().replace(CONFIG, FakeProvider())


def test_build_keeps_going_past_any_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    secrets = SecretStore(tmp_path / ".env")
    # Not shaped like a key (no sk-): only passing the secret itself to the scrubber hides it.
    secrets.set("NEST_KEY_BAD", "zz-LEAKCHECK-456")

    def factory(config: ProviderConfig, reader) -> FakeProvider:  # type: ignore[no-untyped-def]
        if config.id == "bad":
            raise TypeError(f"float() argument must be a number, not 'NoneType' {reader.get(config.key_ref)}")
        if config.id == "socks":
            raise ImportError("socksio is not installed")
        return FakeProvider()

    configs = [
        ProviderConfig("bad", "openai_chat", "http://x", "m", "NEST_KEY_BAD", {"timeout": None}),
        ProviderConfig("ok", "openai_chat", "http://x", "m"),
        ProviderConfig("socks", "openai_chat", "http://x", "m", "", {"proxy": "socks5://h:1"}),
    ]
    registry = ProviderRegistry.build(configs, secrets, Recorder(), factory=factory)
    assert set(registry.runtimes) == {"ok"}
    assert (
        registry.errors["bad"].startswith("TypeError: float()") and "LEAKCHECK" not in registry.errors["bad"]
    )
    assert registry.errors["socks"].startswith("ImportError")
    assert "模型 bad 配置有误" in caplog.text and "LEAKCHECK" not in caplog.text  # the startup warning
    # The real factory on the row the CLI can write.
    real = ProviderRegistry.build(configs[:2], secrets, Recorder())
    assert set(real.runtimes) == {"ok"} and real.errors["bad"].startswith("TypeError: float()")


async def test_codex_runtimes_on_one_login_refresh_once(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    tokens = {"access_token": "old", "refresh_token": "r1", "account_id": "acct"}
    auth.write_text(json.dumps({"tokens": tokens}), encoding="utf-8")
    refreshes = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            refreshes.append(json.loads(request.content)["refresh_token"])
            await asyncio.sleep(0.05)
            if refreshes.count("r1") > 1:
                return httpx.Response(400, json={"error": "refresh token already used"})
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})
        if request.headers["authorization"] == "Bearer old":
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(
            200, text='data: {"type":"response.completed","response":{"output_text":"在"}}\n'
        )

    def codex() -> Codex:
        return Codex(
            name="codex",
            model="gpt",
            base_url="https://codex.example",
            auth_file=auth,
            token_url="https://auth.example/oauth/token",
            transport=httpx.MockTransport(handler),
        )

    old_runtime, new_runtime = codex(), codex()  # e.g. both sides of a settings change
    prompt = [ChatMessage("user", "在吗")]
    results = await asyncio.gather(
        old_runtime.complete(prompt, max_tokens=10), new_runtime.complete(prompt, max_tokens=10)
    )
    assert [r.text for r in results] == ["在", "在"] and refreshes == ["r1"]
    await old_runtime.aclose()
    await new_runtime.aclose()


async def test_reasoning_in_the_text_never_leaves_the_runtime_but_stays_in_the_call_log(
    tmp_path: Path,
) -> None:
    db = await Database.open(tmp_path / "nest.db")
    repo = BotRepo(db)
    config = ProviderConfig(id="m", kind="openai_chat", base_url="http://x", model="m")
    await repo.upsert_provider(config)
    fake = FakeProvider("<think>先想想[表情1]</think>你好")
    runtime = ProviderRuntime(config, fake, repo.record_llm_call, retry_delay=0)
    completion, call_id = await runtime.complete([ChatMessage("user", "hi")], bot_id=None, max_tokens=10)
    assert completion.text == "你好"
    row = await db.fetchone("SELECT output FROM llm_calls WHERE id = ?", (call_id,))
    assert row is not None and "先想想" in row["output"]
    await db.close()


def test_what_counts_as_reasoning() -> None:
    assert without_thought("<THINK>x</THINK>\n你好") == "你好"
    assert without_thought("<thinking>a\nb</thinking>你好") == "你好"
    assert without_thought("想了半天</think>你好") == "你好"  # the opening was cut off
    assert without_thought("<think>写到一半被截断") == ""
    assert without_thought("a <b> 不是思考") == "a <b> 不是思考"
