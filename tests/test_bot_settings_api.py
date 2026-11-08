"""Bot settings (name, aliases, model, per-group cooldown and persona) and the personas API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest.adapters.base import AdapterStatus
from nest.app import create_app
from nest.config import Settings
from nest.core.brain import Policy
from nest.core.events import ChannelInfo, ChannelRef
from nest.secrets import SecretReader
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}
WHALE, PONY, DRAGON = "qq:10001", "qq:10002", "qq:10003"


def factory(config: ProviderConfig, secrets: SecretReader) -> FakeProvider:
    float(config.options.get("timeout", 40))  # what build_provider does: TypeError on None
    return FakeProvider(name=config.id, default="在")


@dataclass
class World:
    http: httpx.AsyncClient
    app: FastAPI
    channels: dict[str, int]  # "<bot>@<group>" -> channel id
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def state(self) -> Any:
        return self.app.state

    async def patch_bot(self, bot_id: str, body: Any) -> httpx.Response:
        return await self.http.patch(f"/api/bots/{bot_id}", json=body)

    async def patch_channel(self, key: str, body: Any) -> httpx.Response:
        bot_id = {"whale": WHALE, "pony": PONY}[key.split("@")[0]]
        return await self.http.patch(f"/api/bots/{bot_id}/channels/{self.channels[key]}", json=body)

    async def row(self, statement: str, *params: Any) -> Any:
        return await self.state.db.fetchone(statement, params)

    async def sql(self, statement: str, *params: Any) -> None:
        async with self.state.db.transaction() as conn:
            await conn.execute(statement, params)

    def bot(self, bot_id: str) -> Any:
        return self.state.brain.bots[bot_id]

    def channel(self, key: str) -> Any:
        bot_id = {"whale": WHALE, "pony": PONY}[key.split("@")[0]]
        return self.bot(bot_id).channels[self.channels[key]]


async def seed(home: Path) -> dict[str, int]:
    db = await Database.open(home / "data" / "nest.db")
    repo = BotRepo(db)
    store = MessageStore(db)
    try:
        for uin, name in (("10001", "阿鲸"), ("10002", "阿马"), ("10003", "阿龙")):
            await upsert_account(
                db,
                platform="qq",
                self_id=uin,
                name=name,
                http_url="http://x",
                ws_url="ws://x/",
                enabled=False,  # no adapters: these tests are about configuration
            )
        await repo.upsert_provider(ProviderConfig("ark", "openai_chat", "http://a.example/v1", "m"))
        await repo.upsert_provider(ProviderConfig("grok", "openai_chat", "http://b.example/v1", "m"))
        # `timeout: null` from the CLI: a row that never builds.
        await repo.upsert_provider(
            ProviderConfig("broken", "openai_chat", "http://c.example/v1", "m", options={"timeout": None})
        )
        await repo.upsert_bot(account_id=WHALE, name="阿鲸", aliases=["鲸鱼"], provider_id="ark")
        await repo.upsert_bot(account_id=PONY, name="阿马", aliases=[], provider_id="grok")
        await repo.upsert_bot(account_id=DRAGON, name="阿龙", aliases=[], provider_id=None)
        channels = {}
        for key, uin, group in (
            ("whale@42", "10001", "42"),
            ("whale@77", "10001", "77"),
            ("pony@42", "10002", "42"),
        ):
            channel = await store.channel_id(ChannelRef("qq", uin, "group", group))
            await repo.set_channel(f"qq:{uin}", channel, enabled=True)
            channels[key] = channel
        async with db.transaction() as conn:  # group 77's name is not known yet
            await conn.execute(
                "UPDATE channels SET name = 'Demo群' WHERE id IN (?, ?)",
                (channels["whale@42"], channels["pony@42"]),
            )
    finally:
        await db.close()
    return channels


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    channels = await seed(tmp_path)
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN, provider_factory=factory, policy=Policy(quiet_seconds=0.01))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as http:
            w = World(http, app, channels)
            queue = app.state.broadcaster.subscribe()

            async def drain() -> None:
                while True:
                    w.events.append(await queue.get())

            drainer = asyncio.create_task(drain())
            try:
                yield w
            finally:
                drainer.cancel()


async def persona(world: World, name: str, content: str = "你是一只鲸鱼。") -> dict[str, Any]:
    response = await world.http.post("/api/personas", json={"name": name, "content": content})
    assert response.status_code == 201, response.text
    return response.json()


# -- the bots listing ---------------------------------------------------------------------------------


async def test_listing_shows_the_model_id_and_each_groups_persona(world: World) -> None:
    await world.sql("UPDATE bots SET provider_id = 'broken' WHERE id = ?", PONY)
    await world.state.brain.load()
    whale_persona = await persona(world, "鲸")
    assert (await world.patch_channel("whale@42", {"persona_id": whale_persona["id"]})).status_code == 200
    bots = {b["id"]: b for b in (await world.http.get("/api/bots")).json()}
    assert bots[WHALE]["provider_id"] == "ark" and bots[WHALE]["provider"]["id"] == "ark"
    # A model that failed to load: the id is still there, the health is not.
    assert bots[PONY]["provider_id"] == "broken" and bots[PONY]["provider"] is None
    assert bots[DRAGON]["provider_id"] is None and bots[DRAGON]["provider"] is None
    by_group = {c["group_id"]: c for c in bots[WHALE]["channels"]}
    assert by_group["42"]["persona_id"] == whale_persona["id"] and by_group["42"]["persona_name"] == "鲸"
    assert by_group["77"]["persona_id"] is None and by_group["77"]["persona"] == ""


# -- bot PATCH ----------------------------------------------------------------------------------------


async def test_name_and_aliases_are_cleaned_saved_and_applied_at_once(world: World) -> None:
    response = await world.patch_bot(
        WHALE, {"name": "  大鲸鱼  ", "aliases": [" 鲸鱼 ", "", "小 鲸", "鲸鱼", "   ", "🐋"]}
    )
    assert response.status_code == 200 and response.json() == {"ok": True}
    bot = world.bot(WHALE)
    assert bot.name == "大鲸鱼" and bot.aliases == ("鲸鱼", "小 鲸", "🐋")  # spaces inside are kept
    row = await world.row("SELECT name, aliases, provider_id, mode FROM bots WHERE id = ?", WHALE)
    assert tuple(row) == ("大鲸鱼", '["鲸鱼", "小 鲸", "🐋"]', "ark", "observe")
    # An UPDATE, not a REPLACE: the bot's groups (ON DELETE CASCADE) are all still there.
    assert set(bot.channels) == {world.channels["whale@42"], world.channels["whale@77"]}
    assert ("bots", {"bot_id": WHALE}) in world.events
    # Twenty code points, outside the BMP too; a one-character name is allowed (the console warns).
    assert (await world.patch_bot(WHALE, {"name": "🐋" * 20, "aliases": ["🐳" * 20] * 3})).status_code == 200
    assert world.bot(WHALE).aliases == ("🐳" * 20,)
    assert (await world.patch_bot(WHALE, {"name": "鲸", "aliases": []})).status_code == 200
    assert world.bot(WHALE).name == "鲸" and world.bot(WHALE).aliases == ()


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({}, "没有要改的内容"),
        ({"name": None, "aliases": None}, "没有要改的内容"),
        ({"name": "   "}, "名字要填 1–20 个字"),
        ({"name": "鲸" * 21}, "名字要填 1–20 个字"),
        ({"name": "阿\n鲸"}, "名字里不能有换行或控制字符"),
        ({"name": "阿 鲸"}, "名字里不能有换行或控制字符"),
        ({"aliases": [f"鲸{i}" for i in range(11)]}, "别名最多 10 个，每个最多 20 字"),
        ({"aliases": ["鲸" * 21]}, "别名最多 10 个，每个最多 20 字"),
        ({"aliases": ["鲸\t鱼"]}, "别名里不能有换行或控制字符"),
        ({"aliases": "鲸鱼"}, "别名格式不对"),
        ({"aliases": [1]}, "别名格式不对"),
        ({"name": "x", "self_id": "1"}, "「self_id」不认识这个设置"),
        ({"mode": "loud"}, "模式格式不对"),
    ],
)
async def test_bot_patch_validation(world: World, body: dict, detail: str) -> None:
    response = await world.patch_bot(WHALE, body)
    assert response.status_code == 422 and response.json() == {"detail": detail}
    assert world.bot(WHALE).name == "阿鲸" and world.bot(WHALE).aliases == ("鲸鱼",)
    row = await world.row("SELECT name, aliases FROM bots WHERE id = ?", WHALE)
    assert tuple(row) == ("阿鲸", '["鲸鱼"]')  # nothing written either


async def test_eleven_aliases_with_duplicates_are_ten(world: World) -> None:
    aliases = [f"鲸{i}" for i in range(10)] + ["鲸0", " 鲸1 ", ""]
    assert (await world.patch_bot(WHALE, {"aliases": aliases})).status_code == 200
    assert len(world.bot(WHALE).aliases) == 10


async def test_model_assignment(world: World) -> None:
    assert (await world.patch_bot("qq:404", {"name": "x"})).status_code == 404
    unknown = await world.patch_bot(WHALE, {"provider_id": "nope"})
    assert unknown.status_code == 400 and unknown.json() == {"detail": "没有这个模型"}
    assert (await world.patch_bot(WHALE, {"provider_id": "grok"})).status_code == 200
    assert world.bot(WHALE).provider_id == "grok"
    # null clears it (not "not sent"), written as NULL.
    assert (await world.patch_bot(WHALE, {"provider_id": None})).status_code == 200
    assert world.bot(WHALE).provider_id is None
    assert (await world.row("SELECT provider_id FROM bots WHERE id = ?", WHALE))[0] is None
    # A row that never built exists in the table: an observing bot may use it (and gets told why it fails).
    assert (await world.patch_bot(WHALE, {"provider_id": "broken"})).status_code == 200
    assert world.bot(WHALE).provider_id == "broken"
    # Not sent: unchanged.
    assert (await world.patch_bot(WHALE, {"name": "阿鲸"})).status_code == 200
    assert world.bot(WHALE).provider_id == "broken"


async def test_a_live_bot_must_keep_a_working_model(world: World) -> None:
    refused = "正式发言的 bot 必须有可用的模型"
    for body in ({"mode": "live", "provider_id": None}, {"mode": "live", "provider_id": "broken"}):
        response = await world.patch_bot(WHALE, body | {"name": "改名"})
        assert response.status_code == 400 and response.json() == {"detail": refused}
    # A rejected edit writes nothing, not even the name sent with it.
    assert world.bot(WHALE).name == "阿鲸" and world.bot(WHALE).mode == "observe"
    bot_row = "SELECT name, aliases, provider_id, mode FROM bots WHERE id = ?"
    assert tuple(await world.row(bot_row, WHALE)) == ("阿鲸", '["鲸鱼"]', "ark", "observe")
    assert (await world.patch_bot(DRAGON, {"mode": "live"})).json() == {"detail": refused}

    assert (await world.patch_bot(WHALE, {"mode": "live"})).status_code == 200
    for body in ({"provider_id": None}, {"provider_id": "broken"}, {"mode": "live", "provider_id": "broken"}):
        response = await world.patch_bot(WHALE, body)
        assert response.status_code == 400 and response.json() == {"detail": refused}
    assert world.bot(WHALE).provider_id == "ark" and world.bot(WHALE).mode == "live"
    assert tuple(await world.row(bot_row, WHALE)) == ("阿鲸", '["鲸鱼"]', "ark", "live")
    # Switching a live bot to another working model is fine; so is leaving live and dropping the model.
    assert (await world.patch_bot(WHALE, {"provider_id": "grok"})).status_code == 200
    assert world.bot(WHALE).provider_id == "grok"
    assert (await world.patch_bot(PONY, {"mode": "live"})).status_code == 200
    assert (await world.patch_bot(PONY, {"mode": "observe", "provider_id": None})).status_code == 200
    assert world.bot(PONY).provider_id is None and world.bot(PONY).mode == "observe"


async def test_a_live_bot_whose_model_broke_can_still_be_renamed(world: World) -> None:
    assert (await world.patch_bot(WHALE, {"mode": "live"})).status_code == 200
    # The model breaks behind the bot's back (e.g. the CLI pointed it at a bad row).
    await world.sql("UPDATE bots SET provider_id = 'broken' WHERE id = ?", WHALE)
    await world.state.brain.load()
    assert (await world.patch_bot(WHALE, {"name": "大鲸", "aliases": ["鲸"]})).status_code == 200
    # Sending the same model is not a switch.
    assert (await world.patch_bot(WHALE, {"provider_id": "broken"})).status_code == 200
    assert (await world.patch_bot(WHALE, {"mode": "live"})).status_code == 400
    assert world.bot(WHALE).name == "大鲸" and world.bot(WHALE).mode == "live"
    assert (await world.patch_bot(WHALE, {"mode": "paused"})).status_code == 200


# -- channel PATCH ------------------------------------------------------------------------------------


async def test_cooldown_bounds(world: World) -> None:
    for value in (0, 600, 45):
        assert (await world.patch_channel("whale@42", {"cooldown_seconds": value})).status_code == 200
        assert world.channel("whale@42").cooldown_seconds == value
    for value, detail in (
        (601, "冷却超出范围"),
        (-1, "冷却超出范围"),
        (1.5, "冷却格式不对"),
        ("12", "冷却格式不对"),
        (True, "冷却格式不对"),
    ):
        response = await world.patch_channel("whale@42", {"cooldown_seconds": value})
        assert response.status_code == 422 and response.json() == {"detail": detail}, value
    assert world.channel("whale@42").cooldown_seconds == 45
    assert world.channel("whale@77").cooldown_seconds == 12  # other groups untouched
    channels = (await world.http.get("/api/bots")).json()[0]["channels"]
    assert {c["group_id"]: c["cooldown_seconds"] for c in channels} == {"42": 45, "77": 12}


async def test_persona_assign_and_clear(world: World) -> None:
    whale = await persona(world, "鲸", "你是一只话少的鲸鱼。")
    assigned = await world.patch_channel("whale@42", {"persona_id": whale["id"]})
    assert assigned.status_code == 200 and assigned.json() == {"ok": True}
    assert world.channel("whale@42").persona == "你是一只话少的鲸鱼。"
    assert world.channel("whale@42").persona_id == whale["id"]
    # Not sent: kept.
    assert (await world.patch_channel("whale@42", {"activity": "active"})).status_code == 200
    assert world.channel("whale@42").persona_id == whale["id"]
    # null: no persona, the brain's default.
    assert (await world.patch_channel("whale@42", {"persona_id": None})).status_code == 200
    assert world.channel("whale@42").persona == "" and world.channel("whale@42").persona_id is None
    row = await world.row(
        "SELECT persona_id FROM bot_channels WHERE bot_id = ? AND channel_id = ?",
        WHALE,
        world.channels["whale@42"],
    )
    assert row[0] is None
    unknown = await world.patch_channel("whale@42", {"persona_id": 999})
    assert unknown.status_code == 400 and unknown.json() == {"detail": "没有这个人设"}
    largest = await world.patch_channel("whale@42", {"persona_id": 2**63 - 1})
    assert largest.status_code == 400 and largest.json() == {"detail": "没有这个人设"}
    # Beyond what the database can hold (or not an id at all): refused before it is looked up.
    for value in (2**63, 2**70, 0, -1):
        response = await world.patch_channel("whale@42", {"persona_id": value})
        assert response.status_code == 422 and response.json() == {"detail": "人设超出范围"}, value
    assert world.channel("whale@42").persona_id is None
    assert (
        await world.http.patch(f"/api/bots/{WHALE}/channels/9999", json={"persona_id": None})
    ).status_code == 404


# -- personas -----------------------------------------------------------------------------------------


async def test_persona_crud(world: World) -> None:
    assert (await world.http.get("/api/personas")).json() == []
    created = await world.http.post("/api/personas", json={"name": "  鲸 ", "content": "\n你是一只鲸鱼。\n"})
    assert created.status_code == 201
    whale = created.json()
    assert whale == {
        "id": whale["id"],
        "name": "鲸",
        "content": "你是一只鲸鱼。",
        "examples": "",
        "updated_at": whale["updated_at"],
        "used_by": [],
    }
    assert ("bots", {"bot_id": None}) in world.events
    await persona(world, "小马", "你是一匹阿马。")
    await persona(world, "a-persona")
    assert [p["name"] for p in (await world.http.get("/api/personas")).json()] == ["a-persona", "小马", "鲸"]

    duplicate = await world.http.post("/api/personas", json={"name": "鲸", "content": "别的"})
    assert duplicate.status_code == 409 and duplicate.json() == {"detail": "已经有叫 鲸 的人设"}
    assert (await world.state.repo.get_persona(whale["id"]))["content"] == "你是一只鲸鱼。"  # never upserted

    # Editing the text applies at once to every group using it.
    for key in ("whale@42", "whale@77"):
        assert (await world.patch_channel(key, {"persona_id": whale["id"]})).status_code == 200
    edited = await world.http.patch(f"/api/personas/{whale['id']}", json={"content": "你是一只胖鲸鱼。"})
    assert edited.status_code == 200 and edited.json()["content"] == "你是一只胖鲸鱼。"
    assert edited.json()["updated_at"] >= whale["updated_at"]
    assert world.channel("whale@42").persona == world.channel("whale@77").persona == "你是一只胖鲸鱼。"
    renamed = await world.http.patch(f"/api/personas/{whale['id']}", json={"name": "胖鲸"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "胖鲸"
    assert world.channel("whale@42").persona_name == "胖鲸"
    onto = await world.http.patch(f"/api/personas/{whale['id']}", json={"name": "小马"})
    assert onto.status_code == 409 and onto.json() == {"detail": "已经有叫 小马 的人设"}
    # Renaming onto its own name is not a clash.
    assert (await world.http.patch(f"/api/personas/{whale['id']}", json={"name": "胖鲸"})).status_code == 200

    empty = await world.http.patch(f"/api/personas/{whale['id']}", json={})
    assert empty.status_code == 422 and empty.json() == {"detail": "没有要改的内容"}
    assert (await world.http.patch("/api/personas/999", json={"name": "x"})).status_code == 404
    assert (await world.http.delete("/api/personas/999")).status_code == 404
    for path in (f"/api/personas/{2**70}", "/api/personas/0"):
        for response in (await world.http.patch(path, json={"name": "x"}), await world.http.delete(path)):
            assert response.status_code == 422 and response.json() == {"detail": "人设超出范围"}, path

    spare = await persona(world, "备用")
    deleted = await world.http.delete(f"/api/personas/{spare['id']}")
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    assert spare["id"] not in [p["id"] for p in (await world.http.get("/api/personas")).json()]


async def test_used_by_and_delete_while_in_use(world: World) -> None:
    whale = await persona(world, "鲸")
    for key in ("whale@42", "whale@77", "pony@42"):
        assert (await world.patch_channel(key, {"persona_id": whale["id"]})).status_code == 200
    [view] = (await world.http.get("/api/personas")).json()
    assert view["used_by"] == [
        {
            "bot_id": WHALE,
            "bot_name": "阿鲸",
            "channel_id": world.channels["whale@42"],
            "channel_name": "Demo群",
        },
        # A group whose name is not known yet.
        {
            "bot_id": WHALE,
            "bot_name": "阿鲸",
            "channel_id": world.channels["whale@77"],
            "channel_name": "群 77",
        },
        {
            "bot_id": PONY,
            "bot_name": "阿马",
            "channel_id": world.channels["pony@42"],
            "channel_name": "Demo群",
        },
    ]
    refused = await world.http.delete(f"/api/personas/{whale['id']}")
    assert refused.status_code == 409
    assert refused.json() == {"detail": "还用在：阿鲸（Demo群）、阿鲸（群 77）、阿马（Demo群）"}

    # Decided from the database, not from what the brain has loaded.
    spare = await persona(world, "备用")
    await world.sql(
        "UPDATE bot_channels SET persona_id = ? WHERE channel_id = ?", spare["id"], world.channels["pony@42"]
    )
    refused = await world.http.delete(f"/api/personas/{spare['id']}")
    assert refused.status_code == 409 and refused.json() == {"detail": "还用在：阿马（Demo群）"}
    assert await world.state.repo.get_persona(spare["id"]) is not None
    # Disabled groups count too: deleting would silently drop the assignment.
    await world.sql("UPDATE bot_channels SET enabled = 0 WHERE channel_id = ?", world.channels["pony@42"])
    assert (await world.http.delete(f"/api/personas/{spare['id']}")).status_code == 409


async def test_persona_limits_count_code_points(world: World) -> None:
    assert (
        await world.http.post("/api/personas", json={"name": "🐋" * 40, "content": "🐋" * 4000})
    ).status_code == 201
    cases = [
        ({"name": "🐋" * 41, "content": "x"}, "人设名字要填 1–40 个字"),
        ({"name": "  ", "content": "x"}, "人设名字要填 1–40 个字"),
        ({"name": "鲸\n", "content": "x"}, None),  # trimmed: fine
        ({"name": "鲸\n鱼", "content": "x"}, "人设名字里不能有换行或控制字符"),
        ({"name": "长", "content": "🐋" * 4001}, "人设内容最多 4000 个字"),
        ({"name": "空", "content": " \n "}, "人设内容不能是空的"),
        ({"name": "零", "content": "a\x00b"}, "人设内容里有不能保存的控制字符"),
        ({"name": "多行", "content": "第一行\n\t第二行"}, None),
        ({"name": "x", "content": "y", "extra": 1}, "「extra」不认识这个设置"),
        ({"name": "x"}, "人设内容必须填写"),
    ]
    for body, detail in cases:
        response = await world.http.post("/api/personas", json=body)
        if detail is None:
            assert response.status_code == 201, body
        else:
            assert response.status_code == 422 and response.json() == {"detail": detail}, body
    # A lone surrogate: valid JSON, but it cannot be stored as UTF-8.
    surrogate = await world.http.post(
        "/api/personas",
        content=b'{"name": "x", "content": "a\\ud800b"}',
        headers={"content-type": "application/json"},
    )
    assert surrogate.status_code == 422 and surrogate.json() == {"detail": "人设内容里有不能保存的控制字符"}


async def test_a_name_only_edit_does_not_recheck_stored_text(world: World) -> None:
    # Stored before the length limit (an older version, a direct database write).
    persona_id = await world.state.repo.upsert_persona("导入的", "长" * 5000)
    renamed = await world.http.patch(f"/api/personas/{persona_id}", json={"name": "导入的人设"})
    assert renamed.status_code == 200 and renamed.json()["content"] == "长" * 5000
    too_long = await world.http.patch(f"/api/personas/{persona_id}", json={"content": "长" * 5000})
    assert too_long.status_code == 422 and too_long.json() == {"detail": "人设内容最多 4000 个字"}


# -- the config lock ----------------------------------------------------------------------------------


async def test_settings_and_persona_writes_wait_for_the_config_lock(world: World) -> None:
    whale = await persona(world, "鲸")
    spare = await persona(world, "备用")
    channel = world.channels["whale@42"]
    writes = [
        ("PATCH", f"/api/bots/{WHALE}", {"name": "大鲸", "provider_id": "grok"}),
        (
            "PATCH",
            f"/api/bots/{WHALE}/channels/{channel}",
            {"cooldown_seconds": 30, "persona_id": whale["id"]},
        ),
        ("POST", "/api/personas", {"name": "新的", "content": "x"}),
        ("PATCH", f"/api/personas/{whale['id']}", {"content": "改过"}),
        ("DELETE", f"/api/personas/{spare['id']}", None),
    ]
    async with world.state.config_lock:
        tasks = [asyncio.create_task(world.http.request(m, p, json=b)) for m, p, b in writes]
        listed = await asyncio.wait_for(world.http.get("/api/personas"), 2)
        assert listed.status_code == 200
        assert (await asyncio.wait_for(world.http.get("/api/usage"), 2)).status_code == 200
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
    results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200, 200, 201, 200, 200]
    assert world.bot(WHALE).name == "大鲸" and world.channel("whale@42").persona == "改过"


async def test_cross_site_writes_are_refused(world: World) -> None:
    whale = await persona(world, "鲸")
    cross = {"sec-fetch-site": "cross-site"}
    writes = [
        ("PATCH", f"/api/bots/{WHALE}", {"name": "被改了"}),
        ("PATCH", f"/api/bots/{WHALE}/channels/{world.channels['whale@42']}", {"cooldown_seconds": 0}),
        ("POST", "/api/personas", {"name": "被加了", "content": "x"}),
        ("PATCH", f"/api/personas/{whale['id']}", {"content": "被改了"}),
        ("DELETE", f"/api/personas/{whale['id']}", None),
    ]
    for method, path, body in writes:
        response = await world.http.request(method, path, json=body, headers=cross)
        assert response.status_code == 403 and response.json() == {"detail": "跨站请求被拒绝"}, path
    assert world.bot(WHALE).name == "阿鲸" and world.channel("whale@42").cooldown_seconds == 12
    assert [
        (p["name"], p["content"]) for p in (await world.http.get("/api/personas", headers=cross)).json()
    ] == [("鲸", "你是一只鲸鱼。")]


# -- taking part in another group -----------------------------------------------------------------------


class GroupLister:
    """Stands in for a connected QQ account: only what the group list needs."""

    capabilities = frozenset({"list_channels"})

    def __init__(self, groups: dict[str, str], delay: float = 0.0, state: str = "connected") -> None:
        self.groups, self.delay, self.state = groups, delay, state
        self.asked = 0

    def status(self) -> AdapterStatus:
        return AdapterStatus(self.state, "", 0.0)  # type: ignore[arg-type]

    async def stop(self) -> None:
        pass

    async def list_channels(self) -> list[ChannelInfo]:
        self.asked += 1
        await asyncio.sleep(self.delay)
        return [
            ChannelInfo(ChannelRef("qq", "10001", "group", gid), name) for gid, name in self.groups.items()
        ]


async def test_the_groups_a_bot_could_join(world: World) -> None:
    other = await world.state.store.channel_id(ChannelRef("qq", "10001", "group", "88"))
    await world.state.store.channel_id(ChannelRef("qq", "10002", "group", "99"))  # another account's group
    offline = (await world.http.get(f"/api/bots/{WHALE}/groups")).json()
    assert offline["fresh"] is False
    assert [(g["group_id"], g["joined"]) for g in offline["groups"]] == [
        ("88", False),
        ("42", True),
        ("77", True),
    ]
    assert offline["groups"][0]["channel_id"] == other

    # Online: QQ's own list, which knows a new group (55) and no longer has 88; 77 stays, the bot is in it.
    world.state.hub.adapters[WHALE] = GroupLister({"42": "Demo群", "55": "新群"})
    online = (await world.http.get(f"/api/bots/{WHALE}/groups")).json()
    assert online["fresh"] is True
    assert [(g["group_id"], g["name"], g["joined"]) for g in online["groups"]] == [
        ("55", "新群", False),
        ("42", "Demo群", True),
        ("77", "", True),
    ]
    assert (await world.http.get("/api/bots/qq:404/groups")).status_code == 404
    # An account that is not connected is not asked at all: the stored list answers at once.
    offline_lister = world.state.hub.adapters[WHALE] = GroupLister({"55": "新群"}, state="reconnecting")
    assert (await world.http.get(f"/api/bots/{WHALE}/groups")).json()["fresh"] is False
    assert offline_lister.asked == 0


async def test_a_slow_qq_group_list_falls_back_to_the_stored_one(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nest.api import routes_bots

    monkeypatch.setattr(routes_bots, "GROUP_LIST_TIMEOUT", 0.05)
    world.state.hub.adapters[WHALE] = GroupLister({"55": "新群"}, delay=1)
    response = (await world.http.get(f"/api/bots/{WHALE}/groups")).json()
    assert response["fresh"] is False and {g["group_id"] for g in response["groups"]} == {"42", "77"}


async def test_joining_another_group(world: World) -> None:
    new = await world.state.store.channel_id(ChannelRef("qq", "10001", "group", "88"))
    whale_persona = await persona(world, "新群人设")
    response = await world.http.post(
        f"/api/bots/{WHALE}/channels",
        json={"channel_id": new, "activity": "mentions", "persona_id": whale_persona["id"]},
    )
    assert response.status_code == 201 and response.json() == {"ok": True, "channel_id": new}
    channel = world.bot(WHALE).channels[new]
    assert channel.enabled and channel.activity == "mentions" and channel.persona_name == "新群人设"
    assert channel.cooldown_seconds == 12  # the usual default
    await eventually(lambda: ("bots", {"bot_id": WHALE}) in world.events)

    for body, status, detail in (
        ({"channel_id": new, "activity": "mentions"}, 409, "阿鲸 已经在这个群了"),
        ({"channel_id": world.channels["pony@42"], "activity": "mentions"}, 400, "阿鲸 的号不在这个群里"),
        ({"channel_id": 999, "activity": "mentions"}, 400, "阿鲸 的号不在这个群里"),
    ):
        response = await world.http.post(f"/api/bots/{WHALE}/channels", json=body)
        assert (response.status_code, response.json()["detail"]) == (status, detail)
    another = await world.state.store.channel_id(ChannelRef("qq", "10001", "group", "89"))
    for body, status in (
        ({"channel_id": another, "activity": "mentions", "persona_id": 999}, 400),
        ({"channel_id": another, "activity": "sometimes"}, 422),
        ({"channel_id": another}, 422),
        ({"channel_id": another, "activity": "mentions", "enabled": True}, 422),
    ):
        assert (await world.http.post(f"/api/bots/{WHALE}/channels", json=body)).status_code == status
    assert another not in world.bot(WHALE).channels
    assert (
        await world.http.post(
            "/api/bots/qq:404/channels", json={"channel_id": another, "activity": "mentions"}
        )
    ).status_code == 404


async def test_answering_one_persons_private_chat(world: World) -> None:
    whale_persona = await persona(world, "私聊人设")
    response = await world.http.post(
        f"/api/bots/{WHALE}/private", json={"user_id": " 900000009 ", "persona_id": whale_persona["id"]}
    )
    assert response.status_code == 201
    new = response.json()["channel_id"]
    channel = world.bot(WHALE).channels[new]
    assert channel.channel == ChannelRef("qq", "10001", "private", "900000009")
    assert channel.enabled and channel.cooldown_seconds == 0 and channel.persona_name == "私聊人设"
    listed = next(b for b in (await world.http.get("/api/bots")).json() if b["id"] == WHALE)
    row = next(c for c in listed["channels"] if c["channel_id"] == new)
    assert row["kind"] == "private" and row["group_id"] == "900000009"
    assert all(c["kind"] == "group" for c in listed["channels"] if c["channel_id"] != new)
    # Settings change like a group's; the private chat is not offered as a group to join.
    patch = await world.http.patch(f"/api/bots/{WHALE}/channels/{new}", json={"enabled": False})
    assert patch.status_code == 200 and not world.bot(WHALE).channels[new].enabled
    assert row["answer_questions"] is False  # 答题 is off until turned on, here as in a group
    patch = await world.http.patch(f"/api/bots/{WHALE}/channels/{new}", json={"answer_questions": True})
    assert patch.status_code == 200 and world.bot(WHALE).channels[new].answer_questions
    groups = (await world.http.get(f"/api/bots/{WHALE}/groups")).json()["groups"]
    assert new not in [g["channel_id"] for g in groups]

    for body, status, detail in (
        ({"user_id": "900000009"}, 409, "阿鲸 已经会回 900000009 的私聊了"),
        ({"user_id": "12a45"}, 422, "QQ 号要填 5–12 位数字"),
        ({"user_id": "10002"}, 422, "这是我们自己的 bot 号"),
        ({"user_id": "123456", "persona_id": 999}, 400, "没有这个人设"),
    ):
        response = await world.http.post(f"/api/bots/{WHALE}/private", json=body)
        assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert (await world.http.post("/api/bots/qq:404/private", json={"user_id": "123456"})).status_code == 404
    assert (await world.http.post(f"/api/bots/{WHALE}/private", json={})).status_code == 422


async def test_tones_are_chosen_per_group_for_answers_and_chat(world: World) -> None:
    channels = (await world.http.get("/api/bots")).json()[0]["channels"]
    assert {(c["answer_tone"], c["chat_tone"]) for c in channels} == {("crisp", "persona")}  # as before tones
    response = await world.patch_channel("whale@42", {"answer_tone": "tsundere", "chat_tone": "onee"})
    assert response.status_code == 200
    assert (world.channel("whale@42").answer_tone, world.channel("whale@42").chat_tone) == (
        "tsundere",
        "onee",
    )
    assert world.channel("whale@77").answer_tone == "crisp"  # other groups untouched
    response = await world.patch_channel("whale@42", {"answer_tone": "rude"})
    assert response.status_code == 400 and response.json()["detail"] == "没有这个语气"
    assert world.state.brain.tone("onee", "persona").label == "温柔大姐姐"  # seeded at startup


async def test_tones_are_added_rewritten_reset_and_deleted(world: World) -> None:
    listed = (await world.http.get("/api/tones")).json()
    assert [t["key"] for t in listed] == ["crisp", "tsundere", "amasado", "onee", "persona"]
    assert all(t["builtin"] and not t["edited"] for t in listed)
    for body, detail in (
        ({"label": "", "manner": "x"}, "语气名字"),
        ({"label": "海盗", "manner": " "}, "不能是空的"),
    ):
        response = await world.http.post("/api/tones", json=body)
        assert response.status_code == 422 and detail in response.json()["detail"]
    pirate = (
        await world.http.post("/api/tones", json={"label": "海盗", "manner": "说话像海盗，啊哈"})
    ).json()
    assert pirate["key"] == "custom-1" and not pirate["builtin"]
    taken = await world.http.post("/api/tones", json={"label": "海盗", "manner": "x"})
    assert taken.status_code == 409
    assert (
        world.state.brain.tone("custom-1", "persona").manner == "说话像海盗，啊哈"
    )  # the brain has it at once

    await world.patch_channel("whale@42", {"chat_tone": "custom-1"})
    used = await world.http.delete("/api/tones/custom-1")
    assert used.status_code == 409 and used.json()["detail"].startswith("还用在：")
    uses = next(t for t in (await world.http.get("/api/tones")).json() if t["key"] == "custom-1")["used_by"]
    assert [u["as"] for u in uses] == ["chat"]
    crisp = next(t for t in (await world.http.get("/api/tones")).json() if t["key"] == "crisp")
    assert crisp["used_by"] == []  # every group's answer tone, but 答题 is off everywhere
    await world.patch_channel("whale@42", {"chat_tone": "persona"})
    assert (await world.http.delete("/api/tones/custom-1")).json() == {"ok": True}

    edited = (await world.http.patch("/api/tones/amasado", json={"manner": "更坏一点"})).json()
    assert edited["manner"] == "更坏一点" and edited["edited"]
    assert world.state.brain.tone("amasado", "crisp").manner == "更坏一点"
    builtin = await world.http.delete("/api/tones/amasado")
    assert builtin.status_code == 409 and "内置的语气不能删" in builtin.json()["detail"]
    reset = (await world.http.post("/api/tones/amasado/reset")).json()
    assert not reset["edited"] and reset["manner"].startswith("说话要明显是有点坏心眼的抖S")
    assert (await world.http.post("/api/tones/custom-9/reset")).status_code == 404


async def test_accounts_show_the_bots_new_name(world: World) -> None:
    assert (await world.patch_bot(WHALE, {"name": "阿鱼"})).status_code == 200
    accounts = (await world.http.get("/api/accounts")).json()
    assert next(a for a in accounts if a["id"] == WHALE)["name"] == "阿鱼"
    connections = (await world.http.get("/api/accounts/connections")).json()
    assert next(c for c in connections if c["id"] == WHALE)["name"] == "阿鱼"
