"""Editing where a QQ account is reached (the 系统 page's 「QQ 连接」): viewing, testing and saving its OneBot
addresses and tokens, and the reconnect. Adapters are fakes; the HTTP side is an httpx.MockTransport and
the WebSocket check is replaced: nothing connects anywhere."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest.adapters.base import AdapterStatus
from nest.adapters.onebot11 import probe
from nest.app import create_app
from nest.config import Settings
from nest.secrets import SecretStore
from nest.storage.accounts import Account, get_account, upsert_account
from nest.storage.db import Database
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}
WHALE = "qq:10001"
PATH = "/api/accounts/qq%3A10001/connection"


class FakeAdapter:
    platform = "qq"
    capabilities = frozenset({"send_text"})

    def __init__(self, account: Account, tokens: tuple[str, str]) -> None:
        self.account = account
        self.tokens = tokens
        self.stopped = False

    async def start(self, emit: Any) -> None:
        pass

    async def stop(self) -> None:
        self.stopped = True

    def status(self) -> AdapterStatus:
        return AdapterStatus("connected", self.account.name, 0.0)


@dataclass
class World:
    http: httpx.AsyncClient
    app: FastAPI
    home: Path
    made: list[FakeAdapter]
    http_seen: list[tuple[str, str]] = field(default_factory=list)  # (url, authorization)
    ws_seen: list[tuple[str, str]] = field(default_factory=list)  # (url, token)
    login: dict[str, Any] = field(default_factory=lambda: {"user_id": 10001, "nickname": "阿鲸"})
    http_status: int = 200

    def env(self) -> str:
        return (self.home / ".env").read_text(encoding="utf-8")


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    db = await Database.open(tmp_path / "data" / "nest.db")
    await upsert_account(
        db,
        platform="qq",
        self_id="10001",
        name="阿鲸",
        http_url="http://127.0.0.1:3010",
        ws_url="ws://127.0.0.1:3011/",
        http_token_ref="NEST_QQ_10001_HTTP_TOKEN",
        ws_token_ref="NEST_QQ_10001_WS_TOKEN",
    )
    await db.close()
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("NEST_QQ_10001_HTTP_TOKEN", "h-old")
    secrets.set("NEST_QQ_10001_WS_TOKEN", "w-old")

    made: list[FakeAdapter] = []

    def factory(account: Account, secrets: SecretStore, on_status: Any, backfill: Any) -> FakeAdapter:
        adapter = FakeAdapter(
            account, (secrets.get(account.http_token_ref), secrets.get(account.ws_token_ref))
        )
        made.append(adapter)
        return adapter

    holder: dict[str, World] = {}

    def onebot(request: httpx.Request) -> httpx.Response:
        w = holder["world"]
        w.http_seen.append((str(request.url), request.headers.get("authorization", "")))
        if w.http_status != 200:
            return httpx.Response(w.http_status)
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": w.login})

    async def check_ws(url: str, token: str) -> probe.Result:
        holder["world"].ws_seen.append((url, token))
        return probe.Result(True, "WebSocket 连上了")

    monkeypatch.setattr(probe, "check_ws", check_ws)
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(
        settings, token=TOKEN, adapter_factory=factory, watch_transport=httpx.MockTransport(onebot)
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            w = World(client, app, tmp_path, made)
            holder["world"] = w
            yield w


async def test_the_list_shows_addresses_and_whether_tokens_are_set(world: World) -> None:
    response = await world.http.get("/api/accounts/connections")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": WHALE,
            "self_id": "10001",
            "name": "阿鲸",
            "http_url": "http://127.0.0.1:3010",
            "ws_url": "ws://127.0.0.1:3011/",
            "http_token": True,
            "ws_token": True,
            "state": "connected",
            "detail": "阿鲸",
        }
    ]
    assert "h-old" not in response.text and "w-old" not in response.text


async def test_testing_with_the_saved_settings(world: World) -> None:
    response = await world.http.post(f"{PATH}/test", json={})
    assert response.json() == {
        "http": {"ok": True, "detail": "连上了：阿鲸（10001）"},
        "ws": {"ok": True, "detail": "WebSocket 连上了"},
    }
    assert world.http_seen == [("http://127.0.0.1:3010/get_login_info", "Bearer h-old")]
    assert world.ws_seen == [("ws://127.0.0.1:3011/", "w-old")]
    assert len(world.made) == 1  # testing never reconnects


async def test_testing_tells_what_is_wrong(world: World) -> None:
    world.login = {"user_id": 99999, "nickname": "别人"}
    assert (await world.http.post(f"{PATH}/test", json={})).json()["http"] == {
        "ok": False,
        "detail": "这个地址登录的是 QQ 99999，不是 10001",
    }
    world.http_status = 401
    detail = (await world.http.post(f"{PATH}/test", json={"http_token": "wrong"})).json()["http"]["detail"]
    assert detail == "令牌不对：HTTP 接口拒绝了它"
    assert world.http_seen[-1][1] == "Bearer wrong"


async def test_a_saved_token_never_goes_to_another_host(world: World) -> None:
    body = {"http_url": "http://192.168.1.20:3010", "ws_url": "ws://192.168.1.20:3011/"}
    response = await world.http.post(f"{PATH}/test", json=body)
    assert response.status_code == 422
    assert "换到了 192.168.1.20:3010" in response.json()["detail"]
    response = await world.http.put(PATH, json={"ws_url": "ws://127.0.0.1:4011/"})  # another port too
    assert response.status_code == 422
    assert world.http_seen == [] and world.ws_seen == []
    body |= {"http_token": "h-new", "ws_token": "w-new"}
    assert (await world.http.post(f"{PATH}/test", json=body)).status_code == 200
    assert world.http_seen == [("http://192.168.1.20:3010/get_login_info", "Bearer h-new")]


async def test_saving_reconnects_with_the_new_settings(world: World) -> None:
    hub = world.app.state.hub
    adapters = hub.adapters
    body = {
        "http_url": " http://127.0.0.1:13010/ ",
        "ws_url": "ws://127.0.0.1:13011/",
        "http_token": "h-new",
        "ws_token": "w-new",
    }
    response = await world.http.put(PATH, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["http_url"] == "http://127.0.0.1:13010"
    assert response.json()["ws_url"] == "ws://127.0.0.1:13011/"
    old, new = world.made
    assert old.stopped and not new.stopped
    assert new.account.http_url == "http://127.0.0.1:13010" and new.tokens == ("h-new", "w-new")
    assert hub.adapters is adapters and adapters[WHALE] is new  # the brain and 监控 share this dict
    assert world.app.state.brain.adapters[WHALE] is new
    stored = await get_account(world.app.state.db, WHALE)
    assert stored is not None and stored.ws_url == "ws://127.0.0.1:13011/"
    assert "h-new" in world.env() and "h-old" not in world.env()


async def test_saving_only_the_tokens_keeps_the_addresses(world: World) -> None:
    response = await world.http.put(PATH, json={"ws_token": "w-new"})
    assert response.status_code == 200
    assert world.made[-1].tokens == ("h-old", "w-new")
    response = await world.http.put(PATH, json={"http_token": ""})  # "" removes it
    assert response.json()["http_token"] is False
    assert world.made[-1].tokens == ("", "w-new")


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"http_url": "ftp://127.0.0.1:3010"}, "HTTP 地址要写成"),
        ({"http_url": "javascript:alert(1)"}, "HTTP 地址要写成"),
        ({"ws_url": "http://127.0.0.1:3011/"}, "WebSocket 地址要写成"),
        ({"http_url": "http://127.0.0.1:99999"}, "HTTP 地址写得不对"),
        ({"http_url": "http://u:p@127.0.0.1:3010"}, "不能带用户名"),
        ({"ws_url": "ws://127.0.0.1:3011/?access_token=x"}, "不能带用户名"),
        ({"http_token": "a\nb"}, "不能保存的字符"),
    ],
)
async def test_bad_settings(world: World, body: dict[str, str], problem: str) -> None:
    response = await world.http.put(PATH, json=body)
    assert response.status_code == 422
    assert problem in response.json()["detail"]
    assert len(world.made) == 1


async def test_unknown_accounts(world: World) -> None:
    assert (await world.http.put("/api/accounts/qq%3A999/connection", json={})).status_code == 404
    assert (await world.http.post("/api/accounts/qq%3A999/connection/test", json={})).status_code == 404


async def test_the_real_http_check_reads_onebot_answers() -> None:
    def answer(body: Any, status: int = 200) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: httpx.Response(status, content=json.dumps(body).encode()))

    ok = await probe.check_http("http://x:1", "t", self_id="1", transport=answer({"data": {"user_id": 1}}))
    assert ok == probe.Result(True, "连上了：1（1）")
    assert not (await probe.check_http("http://x:1", "", self_id="1", transport=answer({"data": {}}))).ok
    assert (await probe.check_http("http://x:1", "", self_id="1", transport=answer("<html>"))).detail == (
        "这个地址回答的不是 OneBot 接口"
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    detail = (
        await probe.check_http("http://x:1", "", self_id="1", transport=httpx.MockTransport(refuse))
    ).detail
    assert detail == "连不上 x:1：SnowLuma 没开，或者端口不对"
