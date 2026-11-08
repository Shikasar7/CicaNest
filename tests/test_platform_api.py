"""The console's way into other platforms: adding a bot through the API, its connection form, what its
platform refuses, and that no secret ever comes back."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest.adapters.base import AdapterStatus, Probed
from nest.adapters.registry import PLATFORMS
from nest.api.routes_system_admin import _hidden_names
from nest.app import create_app
from nest.config import Settings
from nest.core.events import ActionResult, ChannelInfo, ChannelRef
from nest.storage.bots import ProviderConfig
from tests.conftest import TOKEN
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SNOWFLAKE = "900000000000000001"
SECRET = "very-secret-value"


class FakeAdapter:
    capabilities = frozenset()
    max_text = 0
    stuck_send_at = 0.0

    def __init__(self, account: Any, on_status: Any) -> None:
        self.account = account
        self.on_status = on_status
        self.started = False

    async def start(self, emit: Any) -> None:
        self.started = True
        self.on_status(AdapterStatus("connected", "在线"))

    async def stop(self) -> None:
        self.started = False

    async def perform(self, action: Any) -> ActionResult:
        return ActionResult("succeeded", message_id="1")

    def sent_by_us(self, message_id: str) -> bool:
        return True

    async def fetch_media(self, url: str, limit: int) -> bytes:
        return b""

    async def list_channels(self) -> list[ChannelInfo]:
        return []

    def status(self) -> AdapterStatus:
        return AdapterStatus("connected", "在线")


@dataclass
class World:
    http: httpx.AsyncClient
    app: Any
    probes: list[Mapping[str, str]] = field(default_factory=list)
    built: list[Any] = field(default_factory=list)
    answer: Probed = field(default_factory=lambda: Probed(True, SNOWFLAKE, "洋鲸", "连上了"))

    @property
    def state(self) -> Any:
        return self.app.state

    def env(self) -> str:
        path = self.state.settings.home / ".env"
        return path.read_text("utf-8") if path.exists() else ""

    async def add_discord(self, **extra: Any) -> httpx.Response:
        body = {"name": "洋鲸", "platform": "discord", "fields": {"token": SECRET}, **extra}
        return await self.http.post("/api/bots", json=body)

    async def add_wecom(self, bot_id: str = "aibtest") -> httpx.Response:
        self.answer = Probed(True, bot_id, "", "连上了")
        fields = {"bot_id": bot_id, "secret": SECRET}
        return await self.http.post(
            "/api/bots", json={"name": "小助手", "platform": "wecom", "fields": fields}
        )


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    probes: list[Mapping[str, str]] = []
    built: list[Any] = []

    def factory(account: Any, secrets: Any, on_status: Any, backfill: Any) -> FakeAdapter:
        adapter = FakeAdapter(account, on_status)
        built.append((account, secrets.get(next(iter(account.refs.values()), ""))))
        return adapter

    settings = Settings(home=tmp_path / "nest", port=18800, _env_file=None)
    app = create_app(
        settings,
        token=TOKEN,
        adapter_factory=factory,
        provider_factory=lambda config, secrets: FakeProvider(name=config.id),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            w = World(client, app, probes, built)

            async def probe(fields: Mapping[str, str]) -> Probed:
                probes.append(dict(fields))
                return w.answer

            for key in set(PLATFORMS) - {"qq"}:
                monkeypatch.setitem(PLATFORMS, key, dataclasses.replace(PLATFORMS[key], probe=probe))
            yield w


async def test_the_platform_list_is_what_the_console_needs(world: World) -> None:
    views = {p["key"]: p for p in (await world.http.get("/api/platforms")).json()}
    assert {"qq", "discord"} <= set(views)
    assert [f["name"] for f in views["discord"]["fields"]] == ["token", "proxy"]
    token = views["discord"]["fields"][0]
    assert token["secret"] and token["required"] and token["default"] == ""
    assert views["discord"]["fields"][1]["default"] == "http://127.0.0.1:7890"
    assert not views["discord"]["probe_kicks"]
    assert views["qq"]["user_label"] == "QQ 号" and "sees_all" in views["discord"]["capabilities"]


async def test_trying_a_new_account_saves_nothing(world: World) -> None:
    fields = {"token": f" {SECRET} ", "proxy": ""}
    response = await world.http.post("/api/accounts/test", json={"platform": "discord", "fields": fields})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "self_id": SNOWFLAKE, "name": "洋鲸", "detail": "连上了"}
    assert world.probes == [{"token": SECRET, "proxy": ""}]  # trimmed; an emptied proxy stays empty
    assert world.env() == "" and (await world.http.get("/api/accounts/connections")).json() == []


@pytest.mark.parametrize(
    ("platform", "fields", "status", "detail"),
    [
        ("nothing", {}, 400, "不支持这个平台"),
        ("discord", {}, 422, "Bot Token要填"),
        ("discord", {"token": "t", "proxy": "socks5://x:1"}, 422, "不支持 socks"),
        ("discord", {"token": "t", "extra": "1"}, 422, "没有「extra」这一项"),
        ("discord", {"token": "a\nb"}, 422, "不能保存的字符"),
    ],
)
async def test_a_form_that_cannot_work_is_refused_before_any_connection(
    world: World, platform: str, fields: dict[str, str], status: int, detail: str
) -> None:
    response = await world.http.post("/api/accounts/test", json={"platform": platform, "fields": fields})
    assert response.status_code == status and detail in response.json()["detail"]
    assert not world.probes


async def test_a_discord_bot_is_added_watching_and_its_token_stays_in_env(world: World) -> None:
    config = ProviderConfig(id="m", kind="openai_chat", base_url="http://x", model="m")
    await world.state.repo.upsert_provider(config)
    response = await world.add_discord(aliases=["鲸鲸"], provider_id="m")
    assert response.status_code == 201
    assert response.json() == {"bot_id": f"discord:{SNOWFLAKE}", "container": ""}
    [bot] = (await world.http.get("/api/bots")).json()
    assert bot["mode"] == "observe" and bot["platform"] == "discord" and bot["self_id"] == SNOWFLAKE
    assert "sees_all" in bot["capabilities"] and "sticker_source" not in bot["capabilities"]
    assert f"NEST_DISCORD_{SNOWFLAKE}_TOKEN='{SECRET}'" in world.env()
    row = await world.state.db.fetchone("SELECT http_url, ws_url, config FROM accounts")
    assert SECRET not in row["config"] and row["http_url"] == "" and row["ws_url"] == ""
    [(account, token)] = world.built
    assert token == SECRET and account.config["proxy"] == "http://127.0.0.1:7890"
    assert account.refs == {"token": f"NEST_DISCORD_{SNOWFLAKE}_TOKEN"}


async def test_a_token_that_does_not_work_saves_nothing(world: World) -> None:
    world.answer = Probed(False, detail="Token 不对")
    response = await world.add_discord()
    assert response.status_code == 400 and response.json()["detail"] == "Token 不对"
    assert world.env() == "" and (await world.http.get("/api/bots")).json() == []


async def test_the_same_account_is_not_added_twice(world: World) -> None:
    assert (await world.add_discord()).status_code == 201
    response = await world.add_discord(name="又一个")
    assert response.status_code == 409 and SNOWFLAKE in response.json()["detail"]


async def test_connections_show_every_platform_and_no_secret(world: World) -> None:
    await world.add_discord()
    views = {c["id"]: c for c in (await world.http.get("/api/accounts/connections")).json()}
    assert views[f"discord:{SNOWFLAKE}"]["fields"] == {"proxy": "http://127.0.0.1:7890"}
    assert views[f"discord:{SNOWFLAKE}"]["secrets"] == {"token": True}
    assert SECRET not in str(views) and SECRET not in str((await world.http.get("/api/bots")).json())


async def test_saving_a_connection_keeps_what_was_left_empty(world: World) -> None:
    await world.add_discord()
    path = f"/api/accounts/discord:{SNOWFLAKE}/connection"
    response = await world.http.put(path, json={"fields": {"token": "", "proxy": ""}})
    assert response.status_code == 200 and response.json()["fields"] == {"proxy": ""}
    assert SECRET in world.env()
    assert world.built[-1][0].config["proxy"] == ""  # reconnected with the new settings
    response = await world.http.put(path, json={"fields": {"token": "new-token"}})
    assert response.status_code == 200 and response.json()["secrets"] == {"token": True}
    assert "new-token" in world.env() and SECRET not in world.env()
    assert world.built[-1][1] == "new-token"
    bad = await world.http.put(path, json={"fields": {"proxy": "not a proxy"}})
    assert bad.status_code == 422


async def test_testing_a_saved_discord_account_uses_the_saved_token_and_knows_the_bot(world: World) -> None:
    await world.add_discord()
    path = f"/api/accounts/discord:{SNOWFLAKE}/connection/test"
    assert (await world.http.post(path, json={})).json()["ok"]
    assert world.probes[-1]["token"] == SECRET
    world.answer = Probed(True, "900000000000000002", "别的 bot", "连上了")
    found = (await world.http.post(path, json={"fields": {"token": "other"}})).json()
    assert not found["ok"] and "别的 bot" in found["detail"]


async def test_a_started_program_does_not_inherit_a_bots_secret(world: World) -> None:
    await world.add_discord()
    assert f"NEST_DISCORD_{SNOWFLAKE}_TOKEN" in await _hidden_names(world.state)


async def test_removing_a_bot_forgets_its_secrets(world: World) -> None:
    await world.add_discord()
    response = await world.http.delete(f"/api/bots/discord:{SNOWFLAKE}")
    assert response.status_code == 200 and response.json()["ok"]
    assert SECRET not in world.env()
    assert (await world.add_discord()).status_code == 201  # and it can come back


# -- what a platform cannot do ----------------------------------------------------------------------------


async def joined(world: World, account: str, external: str = "wrgroup") -> tuple[str, int]:
    platform, self_id = account.split(":", 1)
    channel = await world.state.store.channel_id(ChannelRef(platform, self_id, "group", external))
    body = {"channel_id": channel, "activity": "mentions"}
    response = await world.http.post(f"/api/bots/{account}/channels", json=body)
    assert response.status_code == 201
    return f"/api/bots/{account}/channels/{channel}", channel


async def test_a_discord_channel_can_be_active(world: World) -> None:
    await world.add_discord()
    path, _ = await joined(world, f"discord:{SNOWFLAKE}", "11")
    assert (
        await world.http.patch(path, json={"activity": "active", "voice_mode": "trial"})
    ).status_code == 200
    assert (await world.http.patch(path, json={"sticker_mode": "trial"})).status_code == 200


async def test_group_features_follow_the_platform(world: World) -> None:
    await world.add_discord()
    await world.state.store.channel_id(ChannelRef("discord", SNOWFLAKE, "group", "11"))
    discord = "/api/groups/discord:group:11/features"
    assert (await world.http.patch(discord, json={"collect_stickers": True})).status_code == 400
    assert (await world.http.patch(discord, json={"learn_slang": True})).status_code == 200


async def test_a_private_chat_takes_the_platforms_own_ids(world: World) -> None:
    await world.add_discord()
    discord = f"/api/bots/discord:{SNOWFLAKE}/private"
    bad = await world.http.post(discord, json={"user_id": "10086"})
    assert bad.status_code == 422 and "Discord 用户 ID" in bad.json()["detail"]
    assert (await world.http.post(discord, json={"user_id": "123456789012345678"})).status_code == 201


async def test_recipients_may_be_on_any_platform(world: World) -> None:
    alert = "/api/system/settings"
    good = await world.http.patch(alert, json={"offline_alert_to": "discord:123456789012345678"})
    assert good.status_code == 200 and good.json()["offline_alert"]["to"] == "discord:123456789012345678"
    assert (await world.http.patch(alert, json={"offline_alert_to": "discord:10086"})).status_code == 422
    assert (await world.http.patch(alert, json={"offline_alert_to": "line:1"})).status_code == 422
    body = {"to": "discord:123456789012345678", "times": ["21:00"]}
    assert (await world.http.put("/api/issues/digest", json=body)).status_code == 200


# -- naming a group ----------------------------------------------------------------------------------------


async def test_only_a_group_whose_platform_does_not_name_it_is_named_here(world: World) -> None:
    await world.add_discord()
    _, channel = await joined(world, f"discord:{SNOWFLAKE}", "11")
    response = await world.http.patch(f"/api/channels/{channel}", json={"name": "别的名字"})
    assert response.status_code == 400
    assert (await world.http.patch("/api/channels/9999", json={"name": "x"})).status_code == 404


async def test_a_new_bot_is_connected_before_the_request_ends(world: World) -> None:
    await world.add_discord()
    await eventually(lambda: world.state.hub.status_of(f"discord:{SNOWFLAKE}").state == "connected")
