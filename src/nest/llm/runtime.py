"""Model providers at runtime: built from config, with concurrency limit, retry, circuit breaker, call log.

Models can be edited while bots are talking. An edit builds a new ProviderRuntime and swaps it into the
registry; the old one stops taking calls at once ("closing") but stays open until the calls it already
accepted have finished, then closes in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from nest.llm.anthropic import DEFAULT_EFFORT, Anthropic
from nest.llm.base import ChatMessage, Completion, Painter, Provider, ProviderError, without_thought
from nest.llm.codex import Codex
from nest.llm.http import scrub
from nest.llm.openai_chat import OpenAIChat
from nest.llm.openai_responses import OpenAIResponses
from nest.secrets import SecretReader
from nest.storage.bots import ProviderConfig

log = logging.getLogger(__name__)

KINDS = ("openai_chat", "openai_responses", "anthropic", "codex")
CallRecorder = Callable[..., Awaitable[int]]  # BotRepo.record_llm_call
ProviderFactory = Callable[[ProviderConfig, SecretReader], Provider]
DEFAULT_TIMEOUT = 40.0
SHUTDOWN_GRACE = 2.0  # nest stop: how long retired runtimes may finish their calls before being closed
CLOSE_TIMEOUT = 0.5  # nest stop: per runtime, for closing its HTTP client


class ProviderUnavailable(Exception):
    """The call was not attempted: the circuit breaker is open, or the model's settings just changed."""


def error_text(exc: BaseException, *secrets: str) -> str:
    """A short, secret-free description of why building a model failed."""
    if isinstance(exc, ValueError) and str(exc):
        text = str(exc)  # our own messages
    else:
        text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return scrub(text, *secrets)[:200]


def build_provider(config: ProviderConfig, secrets: SecretReader) -> Provider:
    options = config.options
    common: dict[str, Any] = {
        "name": config.id,
        "model": config.model,
        "proxy": options.get("proxy") or None,
        "timeout": float(options.get("timeout", DEFAULT_TIMEOUT)),
    }
    if config.kind == "openai_chat":
        return OpenAIChat(
            base_url=config.base_url,
            api_key=secrets.get(config.key_ref),
            temperature=options.get("temperature"),
            extra_body=options.get("extra_body"),
            **common,
        )
    if config.kind == "openai_responses":
        return OpenAIResponses(
            base_url=config.base_url,
            api_key=secrets.get(config.key_ref),
            input_format=options.get("input_format", "text"),
            extra_body=options.get("extra_body"),
            **common,
        )
    if config.kind == "anthropic":
        return Anthropic(
            base_url=config.base_url,
            api_key=secrets.get(config.key_ref),
            extra_body=options.get("extra_body"),
            auth_header=options.get("auth_header", "x-api-key"),
            effort=options.get("effort", DEFAULT_EFFORT),
            **common,
        )
    if config.kind == "codex":
        return Codex(
            base_url=config.base_url, auth_file=options.get("auth_file", "~/.codex/auth.json"), **common
        )
    raise ValueError(f"未知的模型接口类型：{config.kind}（可选 {', '.join(KINDS)}）")


@dataclass
class Health:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    last_ok_at: float = 0.0


def _timeout(config: ProviderConfig) -> float:
    try:
        return float(config.options.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


class ProviderRuntime:
    def __init__(
        self,
        config: ProviderConfig,
        provider: Provider,
        record: CallRecorder,
        *,
        concurrency: int = 2,
        retries: int = 1,
        retry_delay: float = 1.0,
        breaker_threshold: int = 5,
        breaker_cooldown: float = 60.0,
    ) -> None:
        self.config = config
        self.provider = provider
        self._record = record
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._retry_delay = retry_delay
        self._threshold = breaker_threshold
        self._cooldown = breaker_cooldown
        self.health = Health()
        # Calls accepted and not finished (queued, retrying or running). A swapped-out runtime closes
        # its client only once this is back to 0.
        self._in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._closing = False
        self.closed = False

    @property
    def can_search(self) -> bool:
        """Whether this model can search the web while answering: the ChatGPT subscription (Codex) can."""
        return self.config.kind == "codex"

    @property
    def can_draw(self) -> bool:
        """Whether this model can draw pictures: the ChatGPT subscription (Codex) can."""
        return self.config.kind == "codex"

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def health_view(self) -> dict[str, Any]:
        now = time.time()
        return {
            "id": self.config.id,
            "kind": self.config.kind,
            "model": self.config.model,
            "available": self.health.open_until <= now,
            "retry_in": max(0.0, round(self.health.open_until - now)),
            "consecutive_failures": self.health.consecutive_failures,
            "last_error": self.health.last_error,
            "last_ok_at": self.health.last_ok_at or None,
        }

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        bot_id: str | None,
        max_tokens: int,
        temperature: float | None = None,
        search: bool = False,
    ) -> tuple[Completion, int]:
        """Returns (completion, llm_calls id). Raises ProviderUnavailable or ProviderError. `search`: let
        the model search the web (only a Codex model can; see `can_search`)."""
        self._in_flight += 1
        self._idle.clear()
        try:
            if self._closing:
                raise ProviderUnavailable("模型设置刚改过，这次没有调用")
            if self.health.open_until > time.time():
                raise ProviderUnavailable(
                    f"模型 {self.config.id} 连续失败 {self.health.consecutive_failures} 次，"
                    f"{self.health.open_until - time.time():.0f} 秒后再试"
                )
            async with self._semaphore:
                return await self._attempts(messages, bot_id, max_tokens, temperature, search)
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._idle.set()

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes:
        """One picture, never retried (a try costs a minute and the subscription's quota). Only for a model
        that `can_draw`. Raises ProviderUnavailable or ProviderError."""
        self._in_flight += 1
        self._idle.clear()
        try:
            if self._closing:
                raise ProviderUnavailable("模型设置刚改过，这次没有调用")
            async with self._semaphore:
                started = time.time()
                try:
                    picture = await cast(Painter, self.provider).draw(prompt, images)
                except ProviderError as exc:
                    await self._log(None, started, None, "error", str(exc), len(prompt))
                    raise
                await self._log(
                    None,
                    started,
                    (time.time() - started) * 1000,
                    "ok",
                    None,
                    len(prompt),
                    output="[画了一张图]",
                )
                return picture
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._idle.set()

    async def _attempts(
        self,
        messages: Sequence[ChatMessage],
        bot_id: str | None,
        max_tokens: int,
        temperature: float | None,
        search: bool = False,
    ) -> tuple[Completion, int]:
        prompt_chars = sum(len(m.content) for m in messages)
        attempt = 0
        while True:
            started = time.time()
            try:
                completion = await self.provider.complete(
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **({"search": True} if search else {}),
                )
            except ProviderError as exc:
                await self._log(bot_id, started, None, "error", str(exc), prompt_chars)
                if exc.retryable and attempt < self._retries:
                    attempt += 1
                    await asyncio.sleep(self._retry_delay * attempt)
                    continue
                self._failed(str(exc))
                raise
            except Exception as exc:
                # Not a mapped model error (a closed client, a bad header…): still a recorded failure,
                # never a lost decision or a 500.
                detail = scrub(f"{type(exc).__name__}: {str(exc)[:160]}")
                await self._log(bot_id, started, None, "error", detail, prompt_chars)
                failure = ProviderError(f"模型调用出错：{type(exc).__name__}", retryable=False)
                self._failed(str(failure))
                raise failure from None
            call_id = await self._log(
                bot_id,
                started,
                completion.latency_ms,
                "ok",
                None,
                prompt_chars,
                completion.prompt_tokens,
                completion.completion_tokens,
                completion.text,
            )
            self.health.consecutive_failures = 0
            self.health.last_ok_at = time.time()
            # The call log keeps what the model really said; nobody after this point sees its reasoning.
            return replace(completion, text=without_thought(completion.text)), call_id

    def _failed(self, error: str) -> None:
        self.health.consecutive_failures += 1
        self.health.last_error = error
        if self.health.consecutive_failures >= self._threshold:
            self.health.open_until = time.time() + self._cooldown
            log.warning(
                "模型 %s 连续失败 %d 次，暂停 %.0f 秒",
                self.config.id,
                self.health.consecutive_failures,
                self._cooldown,
            )

    async def _log(
        self,
        bot_id: str | None,
        started: float,
        latency_ms: float | None,
        status: str,
        error: str | None,
        prompt_chars: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        output: str | None = None,
    ) -> int:
        return await self._record(
            bot_id=bot_id,
            provider_id=self.config.id,
            model=self.config.model,
            started_at=started,
            latency_ms=latency_ms,
            status=status,
            error=error,
            prompt_chars=prompt_chars,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            output=output,
        )

    def aclose_when_idle(self) -> Coroutine[Any, Any, None]:
        """Stop taking calls now; the returned coroutine closes once the accepted calls have finished."""
        self._closing = True  # synchronously: no call may start between the swap and the closer task
        return self._close_when_idle()

    async def _close_when_idle(self) -> None:
        # Long enough for every accepted call: each attempt may make up to three requests of `timeout`
        # seconds (Codex: call, token refresh, call again).
        limit = max(300.0, (self._retries + 1) * 3 * _timeout(self.config) + 30)
        try:
            await asyncio.wait_for(self._idle.wait(), limit)
        except TimeoutError:
            log.warning(
                "模型 %s 的旧设置还有 %d 个调用 %.0f 秒没结束，直接关闭",
                self.config.id,
                self._in_flight,
                limit,
            )
        await self.aclose()

    async def aclose(self) -> None:
        closer = getattr(self.provider, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
        self.closed = True


class ProviderRegistry:
    """The current runtime of every model that built, and why the others did not.

    `runtimes` is mutated in place, never rebound: the brain and the API hold this object.
    """

    def __init__(self, record: CallRecorder | None = None, **runtime_options: Any) -> None:
        self.runtimes: dict[str, ProviderRuntime] = {}
        self.errors: dict[str, str] = {}  # provider id -> why it did not build (secret-free)
        self._record = record
        self._runtime_options = runtime_options
        self._closers: set[asyncio.Task] = set()
        self._retired: set[ProviderRuntime] = set()  # swapped out, not closed yet

    @classmethod
    def build(
        cls,
        configs: list[ProviderConfig],
        secrets: SecretReader,
        record: CallRecorder,
        factory: ProviderFactory = build_provider,
        **runtime_options: Any,
    ) -> ProviderRegistry:
        registry = cls(record, **runtime_options)
        for config in configs:
            # Any exception: one bad row (`timeout: null` from the CLI, a socks5 proxy…) must never stop
            # nest from starting. The row stays listed, with the reason, and can be fixed in the console.
            try:
                provider = factory(config, secrets)
            except Exception as exc:
                registry.set_error(config.id, error_text(exc, secrets.get(config.key_ref)))
                log.warning("模型 %s 配置有误，已跳过：%s", config.id, registry.errors[config.id])
                continue
            registry.runtimes[config.id] = ProviderRuntime(config, provider, record, **runtime_options)
        return registry

    def get(self, provider_id: str | None) -> ProviderRuntime | None:
        return self.runtimes.get(provider_id or "")

    def drawing(self) -> ProviderRuntime | None:
        """The first model that can draw, if any."""
        return next((r for _, r in sorted(self.runtimes.items()) if r.can_draw), None)

    def replace(self, config: ProviderConfig, provider: Provider) -> ProviderRuntime:
        """Swap in a freshly built provider; the previous runtime finishes its calls, then closes."""
        if self._record is None:
            raise RuntimeError("ProviderRegistry.replace needs a call recorder")
        runtime = ProviderRuntime(config, provider, self._record, **self._runtime_options)
        old = self.runtimes.get(config.id)
        self.runtimes[config.id] = runtime
        self.errors.pop(config.id, None)
        if old is not None:
            self._retire(old)
        return runtime

    def remove(self, provider_id: str) -> None:
        old = self.runtimes.pop(provider_id, None)
        self.errors.pop(provider_id, None)
        if old is not None:
            self._retire(old)

    def set_error(self, provider_id: str, message: str) -> None:
        self.errors[provider_id] = message

    def _retire(self, runtime: ProviderRuntime) -> None:
        self._retired.add(runtime)
        task = asyncio.create_task(runtime.aclose_when_idle(), name=f"provider-close:{runtime.config.id}")
        self._closers.add(task)

        def done(finished: asyncio.Task) -> None:
            self._closers.discard(finished)
            if runtime.closed:
                self._retired.discard(runtime)

        task.add_done_callback(done)

    async def aclose(self) -> None:
        """Shutdown, in bounded time even with a hung call: retired runtimes get a short grace period to
        finish, then every runtime still open is closed."""
        if self._closers:
            _, pending = await asyncio.wait(set(self._closers), timeout=SHUTDOWN_GRACE)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        remaining = [r for r in (*self.runtimes.values(), *self._retired) if not r.closed]

        async def close(runtime: ProviderRuntime) -> None:
            try:
                await asyncio.wait_for(runtime.aclose(), CLOSE_TIMEOUT)
            except Exception:  # the timeout included: one stuck client must not hold up the others
                log.warning("模型 %s 没能正常关闭", runtime.config.id)

        await asyncio.gather(*(close(r) for r in remaining))
        self._retired.clear()
