"""End to end: fake SnowLuma → adapter → hub → storage → console API and live stream."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from nest.app import create_app
from nest.config import Settings
from nest.secrets import SecretStore
from nest.storage.accounts import upsert_account
from nest.storage.db import Database
from tests.fakes.onebot import FakeOneBot
from tests.helpers import eventually, fast_adapter, free_socket, serve, sse_events

TOKEN = "pipeline-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}


async def seed_account(home: Path, bot: FakeOneBot) -> None:
    SecretStore(home / ".env").set("NEST_QQ_10001_TOKEN", bot.token)
    db = await Database.open(home / "data" / "nest.db")
    try:
        await upsert_account(
            db,
            platform="qq",
            self_id="10001",
            name="阿鲸",
            http_url=bot.http_url,
            ws_url=bot.ws_url,
            http_token_ref="NEST_QQ_10001_TOKEN",
            ws_token_ref="NEST_QQ_10001_TOKEN",
        )
    finally:
        await db.close()


async def test_messages_flow_to_storage_api_and_live_stream(tmp_path: Path) -> None:
    async with FakeOneBot(self_id=10001, nickname="阿鲸") as bot:
        bot.groups = {42: "Demo群"}
        await seed_account(tmp_path, bot)
        sock = free_socket()
        settings = Settings(home=tmp_path, port=sock.getsockname()[1], _env_file=None)
        app = create_app(settings, token=TOKEN, adapter_factory=fast_adapter)

        async with (
            serve(app, sock) as base,
            httpx.AsyncClient(base_url=base, headers=AUTH, trust_env=False) as api,
        ):

            async def account_state() -> str:
                return (await api.get("/api/accounts")).json()[0]["status"]["state"]

            assert (await eventually(_awaitable(account_state, "connected"))) is True

            received: list[tuple[str, dict]] = []

            async def listen() -> None:
                async with api.stream("GET", "/api/events", timeout=None) as response:
                    assert response.headers["content-type"].startswith("text/event-stream")
                    assert "content-encoding" not in response.headers  # never buffered for compression
                    async for item in sse_events(response):
                        received.append(item)

            listener = asyncio.create_task(listen())
            try:
                await eventually(lambda: any(kind == "hello" for kind, _ in received))
                hello = next(data for kind, data in received if kind == "hello")
                assert hello["accounts"][0]["status"]["state"] == "connected"

                first = bot.group_message(group_id=42, user_id=7, text="有人吗", nickname="小明")
                await bot.push(first)
                live = await eventually(lambda: [d for k, d in received if k == "message"])
                assert live[0]["text"] == "有人吗" and live[0]["source"] == "live"

                channels = (await api.get("/api/channels")).json()
                await eventually(_awaitable(lambda: _channel_name(api), "Demo群"))
                channel_id = channels[0]["id"]
                assert channels[0]["account_name"] == "阿鲸"

                # Drop the bridge connection; one message arrives while nobody listens, then backfill.
                await bot.drop_connections()
                await eventually(lambda: bot.connected_clients == 0)
                await bot.push(bot.group_message(group_id=42, user_id=8, text="断线时说的", nickname="小红"))
                await eventually(lambda: bot.connected_clients == 1)

                async def texts() -> list[tuple[str, str]]:
                    rows = (await api.get(f"/api/channels/{channel_id}/messages")).json()
                    return [(m["text"], m["source"]) for m in rows]

                await eventually(_awaitable(texts, [("有人吗", "live"), ("断线时说的", "backfill")]))
                assert any(k == "status" and d["state"] == "reconnecting" for k, d in received)

                await bot.push(bot.recall(group_id=42, message_id=first["message_id"], operator_id=7))
                recall = await eventually(lambda: [d for k, d in received if k == "recall"])
                assert recall[0]["external_id"] == str(first["message_id"]) and recall[0]["recalled_at"]
            finally:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

        # After shutdown the live stream has no subscribers left behind.
        assert app.state.broadcaster.subscriber_count == 0


async def test_live_api_requires_token(tmp_path: Path) -> None:
    sock = free_socket()
    settings = Settings(home=tmp_path, port=sock.getsockname()[1], _env_file=None)
    app = create_app(settings, token=TOKEN, adapter_factory=fast_adapter)
    async with serve(app, sock) as base, httpx.AsyncClient(base_url=base, trust_env=False) as api:
        for path in ("/api/accounts", "/api/channels", "/api/channels/1/messages", "/api/events"):
            assert (await api.get(path)).status_code == 401, path
        assert (await api.get("/api/accounts", headers=AUTH)).json() == []
        assert (await api.get("/api/channels/999/messages", headers=AUTH)).status_code == 404


async def _channel_name(api: httpx.AsyncClient) -> str:
    return (await api.get("/api/channels")).json()[0]["name"]


def _awaitable(fetch, expected):  # type: ignore[no-untyped-def]
    """eventually() helper: async check that `await fetch()` equals `expected`."""

    async def check() -> bool:
        return (await fetch()) == expected

    return check
