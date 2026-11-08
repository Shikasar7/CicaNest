"""The usage API: attempts, latency (average and nearest-rank p95), tokens, outcomes, local days."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest.app import create_app
from nest.config import Settings
from nest.core.events import ChannelRef
from nest.llm.base import ProviderError
from nest.secrets import SecretReader
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.llm import FakeProvider

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}
WHALE, PONY = "qq:10001", "qq:10002"


def at(days_ago: int, hour: int = 12, minute: int = 0) -> float:
    """A local time `days_ago` days back."""
    return datetime.combine(date.today() - timedelta(days=days_ago), clock(hour, minute)).timestamp()


def midnight(days_ago: int) -> int:
    return int(at(days_ago, 0))


def day(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


@dataclass
class World:
    http: httpx.AsyncClient
    app: FastAPI
    channel: int
    fakes: dict[str, FakeProvider]

    @property
    def repo(self) -> BotRepo:
        return self.app.state.repo

    async def usage(self, days: int | None = None) -> dict[str, Any]:
        response = await self.http.get("/api/usage", params={} if days is None else {"days": days})
        assert response.status_code == 200, response.text
        return response.json()

    async def call(
        self,
        provider_id: str,
        when: float,
        *,
        bot_id: str | None = WHALE,
        latency: float | None = 100.0,
        tokens: tuple[int | None, int | None] = (None, None),
        error: str | None = None,
    ) -> None:
        await self.repo.record_llm_call(
            bot_id=bot_id,
            provider_id=provider_id,
            model="m",
            started_at=when,
            latency_ms=None if error else latency,
            status="error" if error else "ok",
            error=error,
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
        )

    async def decision(self, bot_id: str, outcome: str, when: float) -> None:
        row = await self.repo.record_decision(
            bot_id=bot_id, channel_id=self.channel, trigger_id=None, trigger="mention", outcome=outcome
        )
        async with self.app.state.db.transaction() as conn:
            await conn.execute("UPDATE decisions SET created_at = ? WHERE id = ?", (when, row["id"]))


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    db = await Database.open(tmp_path / "data" / "nest.db")
    repo = BotRepo(db)
    try:
        for uin, name in (("10001", "阿鲸"), ("10002", "阿马")):
            await upsert_account(
                db,
                platform="qq",
                self_id=uin,
                name=name,
                http_url="http://x",
                ws_url="ws://x/",
                enabled=False,
            )
        for provider_id in ("ark", "grok", "idle"):
            await repo.upsert_provider(ProviderConfig(provider_id, "openai_chat", "http://x.example/v1", "m"))
        await repo.upsert_bot(account_id=WHALE, name="阿鲸", aliases=[], provider_id="ark")
        await repo.upsert_bot(account_id=PONY, name="阿马", aliases=[], provider_id="grok")
        channel = await MessageStore(db).channel_id(ChannelRef("qq", "10001", "group", "42"))
        await repo.set_channel(WHALE, channel, enabled=True)
    finally:
        await db.close()

    fakes: dict[str, FakeProvider] = {}

    def factory(config: ProviderConfig, secrets: SecretReader) -> FakeProvider:
        fakes[config.id] = FakeProvider(name=config.id, default="在")
        return fakes[config.id]

    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN, provider_factory=factory)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as http:
            yield World(http, app, channel, fakes)


async def seed_calls(world: World) -> None:
    # ark, today: 20 successes (100…2000 ms, the first five report tokens), two failures, one failed test.
    for i in range(20):
        await world.call(
            "ark", at(0, 8, i), latency=100.0 * (i + 1), tokens=(10, 2) if i < 5 else (None, None)
        )
    await world.call("ark", at(0, 9), error="超时")
    await world.call("ark", at(0, 10), error="服务器出错（HTTP 502）")
    await world.call("ark", at(0, 11), bot_id=None, error="连不上")
    # ark, one second before today's midnight, and three days ago (from the other bot).
    await world.call("ark", midnight(0) - 1, latency=9000.0)
    await world.call("ark", at(3), bot_id=PONY, latency=5000.0, tokens=(100, 20))
    # grok, yesterday: one failure only.
    await world.call("grok", at(1), bot_id=PONY, error="密钥不对")
    # A model deleted since, used by a test two days ago; and a call older than any range.
    await world.call("gone", at(2), bot_id=None, latency=700.0)
    await world.call("ark", at(20), latency=1.0)


async def test_today(world: World) -> None:
    await seed_calls(world)
    await world.decision(WHALE, "replied", at(0))
    await world.decision(WHALE, "skipped", at(0))
    await world.decision(WHALE, "skipped", at(0))
    await world.decision(PONY, "failed", at(1))
    usage = await world.usage(1)
    assert usage["since"] == midnight(0) and usage["days"] == 1

    providers = {p["provider_id"]: p for p in usage["providers"]}
    assert [p["provider_id"] for p in usage["providers"]] == ["ark", "grok", "idle"]  # gone: not in range
    assert providers["ark"] == {
        "provider_id": "ark",
        "deleted": False,
        "calls": 23,  # attempts, the test call included
        "ok": 20,
        "errors": 3,
        "error_rate": 3 / 23,
        "avg_latency_ms": 1050.0,
        "p95_latency_ms": 1900.0,  # sorted[ceil(0.95 × 20) − 1] = sorted[18]
        "prompt_tokens": 50,
        "completion_tokens": 10,
        "token_calls": 5,
    }
    zero = {
        "deleted": False,
        "calls": 0,
        "ok": 0,
        "errors": 0,
        "error_rate": None,
        "avg_latency_ms": None,
        "p95_latency_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "token_calls": 0,
    }
    assert providers["grok"] == {"provider_id": "grok", **zero}
    assert providers["idle"] == {"provider_id": "idle", **zero}

    whale, pony = usage["bots"]
    assert whale == {
        "bot_id": WHALE,
        "name": "阿鲸",
        "calls": 22,  # the test call is not the bot's
        "prompt_tokens": 50,
        "completion_tokens": 10,
        "outcomes": {"replied": 1, "would_reply": 0, "skipped": 2, "suppressed": 0, "failed": 0},
    }
    assert pony["calls"] == 0 and set(pony["outcomes"].values()) == {0} and len(pony["outcomes"]) == 5

    assert usage["daily"] == [
        {
            "day": day(0),
            "provider_id": "ark",
            "calls": 23,
            "errors": 3,
            "prompt_tokens": 50,
            "completion_tokens": 10,
        }
    ]
    assert usage["recent_errors"] == [
        {"at": at(0, 11), "provider_id": "ark", "bot_id": None, "bot_name": None, "error": "连不上"},
        {
            "at": at(0, 10),
            "provider_id": "ark",
            "bot_id": WHALE,
            "bot_name": "阿鲸",
            "error": "服务器出错（HTTP 502）",
        },
        {"at": at(0, 9), "provider_id": "ark", "bot_id": WHALE, "bot_name": "阿鲸", "error": "超时"},
    ]


async def test_seven_days(world: World) -> None:
    await seed_calls(world)
    await world.decision(PONY, "failed", at(1))
    await world.decision(PONY, "would_reply", at(10))  # out of range
    usage = await world.usage()  # 7 is the default
    assert usage["since"] == midnight(6) and usage["days"] == 7

    providers = {p["provider_id"]: p for p in usage["providers"]}
    assert [p["provider_id"] for p in usage["providers"]] == ["ark", "gone", "grok", "idle"]
    ark = providers["ark"]
    assert (ark["calls"], ark["ok"], ark["errors"]) == (25, 22, 3)
    assert ark["p95_latency_ms"] == 5000.0  # n = 22: sorted[ceil(20.9) − 1] = sorted[20]
    assert ark["avg_latency_ms"] == round(35000 / 22, 1)
    assert (ark["prompt_tokens"], ark["completion_tokens"], ark["token_calls"]) == (150, 30, 6)
    # Only failures: a rate of 1, no latency at all.
    assert providers["grok"] == {
        "provider_id": "grok",
        "deleted": False,
        "calls": 1,
        "ok": 0,
        "errors": 1,
        "error_rate": 1.0,
        "avg_latency_ms": None,
        "p95_latency_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "token_calls": 0,
    }
    gone = providers["gone"]
    assert gone["deleted"] is True and gone["calls"] == 1 and gone["p95_latency_ms"] == 700.0
    assert gone["token_calls"] == 0  # this model does not report tokens
    assert providers["idle"]["deleted"] is False and providers["idle"]["calls"] == 0

    # Every day for every model with calls, zero-filled, oldest first.
    days = [day(back) for back in range(6, -1, -1)]
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in usage["daily"]:
        by_model.setdefault(row["provider_id"], []).append(row)
    assert sorted(by_model) == ["ark", "gone", "grok"]
    for provider_id, rows in by_model.items():
        assert [r["day"] for r in rows] == days
        assert sum(r["calls"] for r in rows) == providers[provider_id]["calls"]
    ark_days = {r["day"]: r["calls"] for r in by_model["ark"]}
    assert (ark_days[day(0)], ark_days[day(1)], ark_days[day(2)], ark_days[day(3)]) == (23, 1, 0, 1)
    assert by_model["grok"][-2] == {
        "day": day(1),
        "provider_id": "grok",
        "calls": 1,
        "errors": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    whale, pony = usage["bots"]
    assert whale["calls"] == 23 and pony["calls"] == 2
    assert (pony["prompt_tokens"], pony["completion_tokens"]) == (100, 20)
    assert pony["outcomes"] == {"replied": 0, "would_reply": 0, "skipped": 0, "suppressed": 0, "failed": 1}
    assert [e["error"] for e in usage["recent_errors"]] == [
        "连不上",
        "服务器出错（HTTP 502）",
        "超时",
        "密钥不对",
    ]


async def test_fourteen_days_and_the_days_parameter(world: World) -> None:
    await world.call("ark", at(13))
    await world.call("ark", at(14))
    usage = await world.usage(14)
    assert usage["since"] == midnight(13) and usage["providers"][0]["calls"] == 1
    assert len(usage["daily"]) == 14 and usage["daily"][0] == {
        "day": day(13),
        "provider_id": "ark",
        "calls": 1,
        "errors": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    for days in (0, 2, 3, 30, -1):
        response = await world.http.get("/api/usage", params={"days": days})
        assert response.status_code == 422 and response.json() == {"detail": "天数只能是 1、7 或 14"}
    response = await world.http.get("/api/usage", params={"days": "week"})
    assert response.status_code == 422 and response.json() == {"detail": "天数格式不对"}


async def test_nothing_recorded_yet(world: World) -> None:
    usage = await world.usage(7)
    assert [p["provider_id"] for p in usage["providers"]] == ["ark", "grok", "idle"]
    assert all(p["calls"] == 0 and p["error_rate"] is None for p in usage["providers"])
    assert usage["daily"] == [] and usage["recent_errors"] == []
    assert [b["bot_id"] for b in usage["bots"]] == [WHALE, PONY]


async def test_at_most_twenty_recent_errors_newest_first(world: World) -> None:
    for minute in range(25):
        await world.call("ark", at(0, 9, minute), error=f"错误 {minute}")
    errors = (await world.usage(1))["recent_errors"]
    assert [e["error"] for e in errors] == [f"错误 {minute}" for minute in range(24, 4, -1)]


async def test_retries_count_as_attempts_and_breaker_refusals_write_nothing(world: World) -> None:
    runtime = world.app.state.providers.get("ark")
    runtime._retry_delay = 0
    world.fakes["ark"].queue(ProviderError("超时", retryable=True), "在")
    tested = (await world.http.post("/api/providers/ark/test")).json()
    assert tested["ok"] is True
    usage = await world.usage(1)
    ark = usage["providers"][0]
    assert (ark["calls"], ark["ok"], ark["errors"], ark["error_rate"]) == (2, 1, 1, 0.5)
    assert usage["bots"][0]["calls"] == 0  # a test call
    # Listed although the retry then worked.
    [error] = usage["recent_errors"]
    assert error["error"] == "超时" and error["bot_id"] is None and error["bot_name"] is None

    runtime.health.open_until = time.time() + 60
    refused = (await world.http.post("/api/providers/ark/test")).json()
    assert refused["ok"] is False
    assert (await world.usage(1))["providers"][0]["calls"] == 2
