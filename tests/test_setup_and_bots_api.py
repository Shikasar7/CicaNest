"""Configuration commands and the bots/groups API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from nest.app import create_app
from nest.config import Settings
from nest.core.brain import Policy
from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.secrets import SecretStore
from nest.setup_cmds import add_bot, add_provider, read_credential
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.llm import FakeProvider

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}


def credentials(tmp_path: Path) -> Path:
    path = tmp_path / ".credentials.yaml"
    path.write_text("OTHER: x\nARK_KEY: 'ark-secret'\nGROK_KEY: grok-secret\n", encoding="utf-8")
    return path


def test_read_credential(tmp_path: Path) -> None:
    path = credentials(tmp_path)
    assert read_credential(f"{path}:ARK_KEY") == "ark-secret"
    with pytest.raises(ValueError):
        read_credential(f"{path}:MISSING")


async def seed(home: Path) -> None:
    """Two bot accounts, both in group 42, with one provider each."""
    db = await Database.open(home / "data" / "nest.db")
    secrets = SecretStore(home / ".env")
    try:
        for uin, name in (("10001", "阿鲸"), ("10002", "阿马")):
            await upsert_account(
                db, platform="qq", self_id=uin, name=name, http_url="http://x", ws_url="ws://x/"
            )
        await add_provider(
            db,
            secrets,
            provider_id="ark",
            kind="openai_responses",
            base_url="https://ark/v3",
            model="deepseek",
            key_from=f"{credentials(home)}:ARK_KEY",
        )
        await add_provider(
            db,
            secrets,
            provider_id="grok",
            kind="openai_chat",
            base_url="http://127.0.0.1:8180/v1",
            model="grok",
            key_from=f"{credentials(home)}:GROK_KEY",
            options=["temperature=0.8", "proxy=http://127.0.0.1:7890"],
        )
        await add_bot(db, uin="10001", provider_id="ark", aliases=["阿鲸", "鲸鱼娘"], groups=["42"])
        await add_bot(db, uin="10002", provider_id="grok", groups=["42"], activity="mentions")
        store = MessageStore(db)
        for uin in ("10001", "10002"):
            await store.save(
                MessageEvent(
                    ChannelRef("qq", uin, "group", "42"),
                    "m1",
                    "7",
                    "小明",
                    (Segment("text", {"text": "大家好"}),),
                    "大家好",
                    1000.0,
                )
            )
    finally:
        await db.close()


async def test_setup_commands_store_config_and_keep_keys_in_env(tmp_path: Path) -> None:
    await seed(tmp_path)
    db = await Database.open(tmp_path / "data" / "nest.db")
    try:
        repo = BotRepo(db)
        providers = {p.id: p for p in await repo.providers()}
        assert providers["grok"].options == {"temperature": 0.8, "proxy": "http://127.0.0.1:7890"}
        assert providers["ark"].key_ref == "NEST_KEY_ARK"
        assert SecretStore(tmp_path / ".env").get("NEST_KEY_ARK") == "ark-secret"
        dumped = [tuple(r) for r in await db.fetchall("SELECT * FROM providers")]
        assert not any("secret" in str(v) for row in dumped for v in row)
        bots = {b.self_id: b for b in await repo.bots()}
        assert bots["10001"].aliases == ("阿鲸", "鲸鱼娘") and bots["10001"].mode == "observe"
        [channel] = bots["10002"].channels.values()
        assert channel.enabled and channel.activity == "mentions" and channel.channel.id == "42"
        with pytest.raises(ValueError, match="还没有 QQ"):
            await add_bot(db, uin="99999", provider_id="ark")
        with pytest.raises(ValueError, match="没有模型"):
            await add_bot(db, uin="10001", provider_id="nope")
        with pytest.raises(ValueError, match="未知的模型接口类型"):
            await add_provider(
                db, SecretStore(tmp_path / ".env"), provider_id="x", kind="?", base_url="", model=""
            )
    finally:
        await db.close()


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    await seed(tmp_path)
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    fake = FakeProvider("在")
    app = create_app(
        settings,
        token=TOKEN,
        provider_factory=lambda config, secrets: fake,
        policy=Policy(quiet_seconds=0.01),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_bots_listing_and_mode_changes(api: httpx.AsyncClient) -> None:
    bots = (await api.get("/api/bots")).json()
    assert [b["name"] for b in bots] == ["阿鲸", "阿马"]
    assert bots[0]["mode"] == "observe" and bots[0]["provider"]["id"] == "ark"
    assert bots[0]["channels"][0]["group_id"] == "42" and bots[0]["channels"][0]["activity"] == "occasional"

    assert (await api.patch("/api/bots/qq:10001", json={"mode": "live"})).status_code == 200
    assert api.app.state.brain.bots["qq:10001"].mode == "live"  # type: ignore[attr-defined]
    assert bots[0]["foreign_at"] is None
    paused = (await api.post("/api/bots/pause-all")).json()
    assert paused == {"ok": True, "paused": 2}
    assert {b.mode for b in api.app.state.brain.bots.values()} == {"paused"}  # type: ignore[attr-defined]

    channel_id = bots[0]["channels"][0]["channel_id"]
    patched = await api.patch(f"/api/bots/qq:10001/channels/{channel_id}", json={"activity": "active"})
    assert patched.status_code == 200
    assert (await api.get("/api/bots")).json()[0]["channels"][0]["activity"] == "active"
    assert (await api.patch("/api/bots/qq:10001/channels/9999", json={"enabled": False})).status_code == 404
    bad = await api.patch(f"/api/bots/qq:10001/channels/{channel_id}", json={"activity": "loud"})
    assert bad.status_code == 422


async def test_provider_test_endpoint(api: httpx.AsyncClient) -> None:
    result = (await api.post("/api/providers/ark/test")).json()
    assert result["ok"] and result["text"] == "在"
    assert (await api.post("/api/providers/nope/test")).status_code == 404


async def test_groups_merge_accounts_and_timeline_includes_decisions(api: httpx.AsyncClient) -> None:
    [group] = (await api.get("/api/groups")).json()
    assert group["key"] == "qq:group:42" and len(group["channels"]) == 2
    repo = api.app.state.repo  # type: ignore[attr-defined]
    channel_id = group["channels"][1]["channel_id"]
    row = await api.app.state.store.recent(channel_id)  # type: ignore[attr-defined]
    await repo.record_decision(
        bot_id="qq:10002",
        channel_id=channel_id,
        trigger_id=row[0]["id"],
        trigger="mention",
        outcome="would_reply",
        reply="你好呀",
    )
    timeline = (await api.get("/api/groups/qq:group:42/timeline")).json()
    assert [m["text"] for m in timeline["messages"]] == ["大家好"]
    [decision] = timeline["decisions"]
    assert (
        decision["bot_name"] == "阿马"
        and decision["reply"] == "你好呀"
        and decision["trigger_text"] == "大家好"
    )
    assert (await api.get("/api/groups/qq:group:999/timeline")).status_code == 404
