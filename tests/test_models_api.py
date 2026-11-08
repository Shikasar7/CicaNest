"""The models API: validation, keys kept in .env and nowhere else, hot swaps, deletion, config lock."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest.api.routes_live import sse
from nest.app import create_app
from nest.config import Settings
from nest.core.brain import Policy
from nest.core.events import ChannelRef
from nest.secrets import SecretReader, SecretStore
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}
KEY = "sk-test-LEAKCHECK-123"
PLAIN_KEY = "zz-LEAKCHECK-456"  # does not look like a key: only explicit scrubbing hides it
NOT_LOADED = "还没有加载，保存一次或重启 nest"

SEEDED = [
    ProviderConfig(
        "ark",
        "openai_responses",
        "https://ark.example/api/plan/v3",
        "deepseek",
        "NEST_KEY_ARK",
        {"extra_body": {"thinking": {"type": "disabled"}}},
    ),
    ProviderConfig(
        "grok", "openai_chat", "http://127.0.0.1:8180/v1", "grok", "NEST_KEY_GROK", {"temperature": 0.8}
    ),
    # Two models sharing one key.
    ProviderConfig("share-a", "openai_chat", "http://a.example/v1", "m", "NEST_KEY_SHARED"),
    ProviderConfig("share-b", "openai_chat", "http://b.example/v1", "m", "NEST_KEY_SHARED"),
    # Added by hand with a QQ connection's token name.
    ProviderConfig("borrow", "openai_chat", "http://c.example/v1", "m", "NEST_QQ_10001_WS_TOKEN"),
    ProviderConfig("other", "openai_chat", "http://d.example/v1", "m", "NEST_KEY_CLASH"),
    # `timeout: null` from the CLI: fails to build at startup.
    ProviderConfig("broken", "openai_chat", "http://e.example/v1", "m", "NEST_KEY_BROKEN", {"timeout": None}),
]


class Factory:
    """Builds a new fake per call (like build_provider) and remembers the key each build was given."""

    def __init__(self) -> None:
        self.builds: list[tuple[ProviderConfig, FakeProvider]] = []
        self.seen_keys: dict[str, str] = {}
        self.fail: Callable[[ProviderConfig, SecretReader], BaseException] | None = None
        self.fail_for: set[str] = set()  # empty: every build fails while `fail` is set

    def __call__(self, config: ProviderConfig, secrets: SecretReader) -> FakeProvider:
        if self.fail is not None and (not self.fail_for or config.id in self.fail_for):
            raise self.fail(config, secrets)
        float(config.options.get("timeout", 40))  # what build_provider does: TypeError on None
        self.seen_keys[config.id] = secrets.get(config.key_ref)
        fake = FakeProvider(name=config.id, default="在")
        self.builds.append((config, fake))
        return fake

    def latest(self, provider_id: str) -> FakeProvider:
        return next(fake for config, fake in reversed(self.builds) if config.id == provider_id)


@dataclass
class Nest:
    http: httpx.AsyncClient
    app: FastAPI
    factory: Factory
    env: Path
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    bodies: list[str] = field(default_factory=list)

    async def call(self, method: str, path: str, body: Any = None) -> httpx.Response:
        response = await self.http.request(method, path, json=body)
        self.bodies.append(response.text)
        return response

    async def view(self, provider_id: str) -> dict[str, Any]:
        views = (await self.call("GET", "/api/providers")).json()
        return next(v for v in views if v["id"] == provider_id)

    @property
    def state(self) -> Any:
        return self.app.state

    def file_value(self, ref: str) -> str | None:
        return SecretStore(self.env).file_value(ref)


async def seed(home: Path) -> None:
    db = await Database.open(home / "data" / "nest.db")
    secrets = SecretStore(home / ".env")
    repo = BotRepo(db)
    try:
        for uin, name in (("10001", "阿鲸"), ("10002", "阿马"), ("10003", "阿龙")):
            await upsert_account(
                db,
                platform="qq",
                self_id=uin,
                name=name,
                http_url="http://x",
                ws_url="ws://x/",
                http_token_ref="NEST_KEY_TAKEN" if uin == "10003" else "",
                ws_token_ref=f"NEST_QQ_{uin}_WS_TOKEN",
                enabled=False,  # no adapters: these tests are about configuration
            )
            secrets.set(f"NEST_QQ_{uin}_WS_TOKEN", f"qq-token-{uin}")
        for config in SEEDED:
            await repo.upsert_provider(config)
        for ref, value in (
            ("NEST_KEY_ARK", "ark-secret"),
            ("NEST_KEY_GROK", "grok-secret"),
            ("NEST_KEY_SHARED", "shared-secret"),
            ("NEST_KEY_FRESH", "leftover-from-an-old-model"),
        ):
            secrets.set(ref, value)
        await repo.upsert_bot(account_id="qq:10001", name="阿鲸", aliases=["阿鲸"], provider_id="ark")
        await repo.upsert_bot(account_id="qq:10002", name="阿马", aliases=[], provider_id="grok")
        channel = await MessageStore(db).channel_id(ChannelRef("qq", "10001", "group", "42"))
        await repo.set_channel("qq:10001", channel, enabled=True)
    finally:
        await db.close()


@pytest.fixture
async def nest(tmp_path: Path) -> AsyncIterator[Nest]:
    await seed(tmp_path)
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    factory = Factory()
    app = create_app(settings, token=TOKEN, provider_factory=factory, policy=Policy(quiet_seconds=0.01))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as http:
            world = Nest(http, app, factory, tmp_path / ".env")
            queue = app.state.broadcaster.subscribe()

            async def drain() -> None:
                while True:
                    world.events.append(await queue.get())

            drainer = asyncio.create_task(drain())
            try:
                yield world
            finally:
                drainer.cancel()


async def sql(nest: Nest, statement: str) -> None:
    async with nest.state.db.transaction() as conn:
        await conn.execute(statement)


def chat(provider_id: str = "fresh", **extra: Any) -> dict[str, Any]:
    return {
        "id": provider_id,
        "kind": "openai_chat",
        "base_url": "https://api.example/v1",
        "model": "chat-model",
        **extra,
    }


# -- listing ------------------------------------------------------------------------------------------


async def test_listing_shows_every_row_with_key_status_and_users(nest: Nest) -> None:
    builds = len(nest.factory.builds)
    views = (await nest.call("GET", "/api/providers")).json()
    assert len(nest.factory.builds) == builds  # listing never builds
    assert [v["id"] for v in views] == sorted(c.id for c in SEEDED)
    ark = next(v for v in views if v["id"] == "ark")
    assert ark == {
        "id": "ark",
        "kind": "openai_responses",
        "base_url": "https://ark.example/api/plan/v3",
        "model": "deepseek",
        "options": {"extra_body": {"thinking": {"type": "disabled"}}},
        "key_ref": "NEST_KEY_ARK",
        "key_set": True,
        "key_from_env": False,
        "key_shared_with": [],
        "used_by": [{"bot_id": "qq:10001", "name": "阿鲸", "mode": "observe"}],
        "health": ark["health"],
        "build_error": None,
    }
    assert ark["health"]["id"] == "ark" and ark["health"]["last_ok_at"] is None
    shared = next(v for v in views if v["id"] == "share-a")
    assert shared["key_shared_with"] == ["share-b"] and shared["used_by"] == []
    other = next(v for v in views if v["id"] == "other")
    assert other["key_set"] is False


async def test_a_bad_row_at_startup_is_listed_and_can_be_fixed(nest: Nest) -> None:
    broken = await nest.view("broken")
    assert broken["health"] is None and broken["build_error"].startswith("TypeError: float()")
    tested = (await nest.call("POST", "/api/providers/broken/test")).json()
    assert tested == {"ok": False, "error": broken["build_error"]}
    # Saving once (stored options, `timeout: null` dropped) builds it.
    fixed = await nest.call("PATCH", "/api/providers/broken", {})
    assert fixed.status_code == 200 and fixed.json()["build_error"] is None and fixed.json()["options"] == {}
    assert nest.state.providers.get("broken") is not None and "broken" not in nest.state.providers.errors
    assert (await nest.call("POST", "/api/providers/broken/test")).json()["ok"] is True

    # A row added by the CLI while nest runs has neither a runtime nor an error.
    await nest.state.repo.upsert_provider(ProviderConfig("late", "openai_chat", "http://f.example", "m"))
    assert (await nest.view("late"))["build_error"] == NOT_LOADED
    assert (await nest.call("POST", "/api/providers/late/test")).json() == {"ok": False, "error": NOT_LOADED}
    assert (await nest.call("POST", "/api/providers/nope/test")).status_code == 404


# -- validation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        (chat("A"), 422, "名字只能用小写字母、数字和 -"),
        (chat("-x"), 422, "名字只能用小写字母、数字和 -"),
        (chat("x" * 41), 422, "名字只能用小写字母、数字和 -"),
        (chat("ark"), 409, "已经有叫 ark 的模型"),
        (chat(kind="nope"), 422, "接口类型只能是"),
        (chat(model="   "), 422, "模型名要填 1–100 个字"),
        (chat(model="m" * 101), 422, "模型名要填 1–100 个字"),
        (chat(base_url=""), 422, "地址要写成 http:// 或 https://"),
        (chat(base_url="ftp://x.example"), 422, "地址要写成 http:// 或 https://"),
        (chat(base_url="https://x .example"), 422, "地址要写成 http:// 或 https://"),
        (chat(base_url="https://" + "x" * 300), 422, "地址最多 300 个字"),
        (chat(base_url="https://user:pw@x.example/v1"), 422, "地址里不要带用户名、密码或 ? 参数"),
        (chat(base_url="https://x.example/v1?key=1"), 422, "地址里不要带用户名、密码或 ? 参数"),
        (chat(base_url="https://x.example/v1#a"), 422, "地址里不要带用户名、密码或 ? 参数"),
        (chat(base_url="https://x.example:99999/v1"), 422, "地址里的端口不对"),
        # Hosts the URL parser itself refuses: a bracket without its pair, no IP address in brackets, and
        # full-width characters that normalize to @ or /.
        (chat(base_url="http://[::1/v1"), 422, "地址要写成 http:// 或 https://"),
        (chat(base_url="http://[abc]/v1"), 422, "地址要写成 http:// 或 https://"),
        (chat(base_url="https://x.example／evil.example/v1"), 422, "地址要写成 http:// 或 https://"),
        (chat(model="deep\nseek"), 422, "模型名里不能有换行或控制字符"),
        (chat(options={"input_format": "text"}), 422, "「input_format」这个设置在这种接口里不起作用"),
        (chat(options={"auth_file": "~/.codex/auth.json"}), 422, "「auth_file」这个设置在这种接口里不起作用"),
        (chat(options={"top_p": 1}), 422, "「top_p」这个设置在这种接口里不起作用"),
        (chat(kind="openai_responses", options={"temperature": 1}), 422, "「temperature」这个设置"),
        (chat(options={"timeout": 0}), 422, "超时要在 1–300 秒之间"),
        (chat(options={"timeout": 301}), 422, "超时要在 1–300 秒之间"),
        (chat(options={"timeout": "30"}), 422, "超时要在 1–300 秒之间"),
        (chat(options={"timeout": True}), 422, "超时要在 1–300 秒之间"),
        (chat(options={"temperature": 2.5}), 422, "温度要在 0–2 之间"),
        (chat(options={"temperature": -0.1}), 422, "温度要在 0–2 之间"),
        (chat(options={"proxy": "socks5://127.0.0.1:1080"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://u:p@127.0.0.1:7890"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://127.0.0.1"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://127.0.0.1:7890/path"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://127.0.0.1:7890?x=1"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": 7890}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://[::1:7890"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://[abc]:80"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"proxy": "http://ex＠ample:8080"}), 422, "代理要写成 http://主机:端口"),
        (chat(options={"extra_body": ["x"]}), 422, "额外参数要是一个 JSON 对象"),
        (chat(options={"extra_body": {"model": "x"}}), 422, "额外参数里不能设置 model，它由 nest 控制"),
        (chat(options={"extra_body": {"stream": True}}), 422, "额外参数里不能设置 stream，它由 nest 控制"),
        (chat(options={"extra_body": {"max_tokens": 9}}), 422, "额外参数里不能设置 max_tokens"),
        (chat(options={"extra_body": {"x": "a" * 4000}}), 422, "额外参数最多 4000 字节"),
        (chat(options={"extra_body": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}}), 422, "最多嵌套 5 层"),
        (chat(options={"extra_body": {"a": [[[[1]]]]}}), 201, None),  # exactly five levels
        (chat(kind="openai_responses", options={"input_format": "xml"}), 422, "对话格式只能选"),
        (chat(kind="anthropic", options={"auth_header": "bearer"}), 201, None),
        (chat(kind="anthropic", options={"auth_header": "basic"}), 422, "密钥请求头只能选"),
        (chat(options={"auth_header": "bearer"}), 422, "「auth_header」这个设置在这种接口里不起作用"),
        (chat(kind="anthropic", options={"extra_body": {"system": "x"}}), 422, "额外参数里不能设置 system"),
        (chat(kind="anthropic", options={"effort": "medium"}), 201, None),
        (chat(kind="anthropic", options={"effort": "max"}), 422, "思考档只能选"),
        (chat(options={"effort": "medium"}), 422, "「effort」这个设置在这种接口里不起作用"),
        (
            chat(kind="anthropic", options={"effort": "high", "extra_body": {"output_config": {}}}),
            422,
            "两处留一处",
        ),
        (chat(key="sk-test with space"), 422, "密钥里有空格、换行或中文字符"),
        (chat(key="sk-测试"), 422, "密钥里有空格、换行或中文字符"),
        (chat(key="a\nb"), 422, "密钥里有空格、换行或中文字符"),
        (chat(key="x" * 501), 422, "密钥里有空格、换行或中文字符"),
        (chat(key=KEY, clear_key=True), 422, "填了新密钥就不要再勾选清除"),
        (chat(kind="codex", base_url="", key=KEY), 422, "Codex 订阅不用密钥，它用登录文件"),
        (chat(kind="codex", base_url="", clear_key=True), 422, "Codex 订阅不用密钥，它用登录文件"),
        (chat(kind="codex", base_url="http://chatgpt.com/backend-api/codex"), 422, "Codex 订阅的地址只能是"),
        (chat(kind="codex", base_url="https://evil.example/codex"), 422, "Codex 订阅的地址只能是"),
        (chat(kind="codex", base_url="https://chatgpt.com.evil.example/x"), 422, "Codex 订阅的地址只能是"),
        (chat(kind="codex", base_url="", options={"temperature": 1}), 422, "「temperature」这个设置"),
        (
            chat(kind="codex", base_url="", options={"auth_file": "missing.json"}),
            422,
            "找不到 Codex 登录文件",
        ),
        (chat(clear=True), 422, "「clear」不认识这个设置"),
        ({"kind": "openai_chat", "model": "m"}, 422, "「id」必须填写"),
    ],
)
async def test_create_validation(nest: Nest, body: dict, status: int, detail: str | None) -> None:
    before = nest.env.read_bytes()
    response = await nest.call("POST", "/api/providers", body)
    assert response.status_code == status, response.text
    if detail is not None:
        assert isinstance(response.json()["detail"], str) and detail in response.json()["detail"]
        assert nest.env.read_bytes() == before
        assert body.get("id") == "ark" or body.get("id") not in nest.state.providers.runtimes


def raw_chat(model: str = "m", extra_body: str = "{}") -> dict[str, Any]:
    """A request body as raw JSON text: it can hold what httpx's encoder refuses to write (NaN, \\ud800)."""
    text = (
        '{"id": "odd", "kind": "openai_chat", "base_url": "https://api.example/v1", '
        f'"model": "{model}", "options": {{"extra_body": {extra_body}}}}}'
    )
    return {"content": text.encode(), "headers": {"content-type": "application/json"}}


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (raw_chat(extra_body='{"top_p": NaN}'), "额外参数里不能有 NaN 或无穷大"),
        (raw_chat(extra_body='{"top_p": Infinity}'), "额外参数里不能有 NaN 或无穷大"),
        (raw_chat(extra_body='{"a": {"b": [-Infinity]}}'), "额外参数里不能有 NaN 或无穷大"),
        (raw_chat(extra_body='{"top_p": 1e999}'), "额外参数里不能有 NaN 或无穷大"),  # too big: infinity
        (raw_chat(extra_body=r'{"a": "x\ud800"}'), "额外参数里有不能保存的字符"),  # a lone surrogate
        (raw_chat(extra_body=r'{"\ud800": 1}'), "额外参数里有不能保存的字符"),
        (raw_chat(model=r"m\ud800"), "模型名里不能有换行或控制字符"),
    ],
)
async def test_values_json_cannot_carry_are_refused(nest: Nest, body: dict[str, Any], detail: str) -> None:
    before = nest.env.read_bytes()
    response = await nest.http.post("/api/providers", **body)
    assert response.status_code == 422 and response.json() == {"detail": detail}
    assert await nest.state.repo.get_provider("odd") is None and nest.env.read_bytes() == before


async def test_edits_are_checked_like_new_models(nest: Nest) -> None:
    row = await nest.state.repo.get_provider("grok")
    runtime = nest.state.providers.get("grok")
    for body, detail in (
        ({"options": {"proxy": "http://[::1:7890"}}, "代理要写成 http://主机:端口"),
        ({"base_url": "http://[abc]/v1"}, "地址要写成 http:// 或 https://"),
    ):
        response = await nest.call("PATCH", "/api/providers/grok", body)
        assert response.status_code == 422 and detail in response.json()["detail"]
    for raw in (r'{"model": "m\ud800"}', '{"options": {"extra_body": {"top_p": NaN}}}'):
        response = await nest.http.patch(
            "/api/providers/grok", content=raw.encode(), headers={"content-type": "application/json"}
        )
        assert response.status_code == 422, raw
    assert await nest.state.repo.get_provider("grok") == row and nest.state.providers.get("grok") is runtime
    # A malformed proxy saved by the CLI: saving the model again says what is wrong.
    await nest.state.repo.upsert_provider(
        ProviderConfig(
            "cli", "openai_chat", "http://g.example/v1", "m", options={"proxy": "http://[::1:7890"}
        )
    )
    response = await nest.call("PATCH", "/api/providers/cli", {"model": "m2"})
    assert response.status_code == 422 and "代理要写成 http://主机:端口" in response.json()["detail"]


async def test_everything_validation_accepts_builds_with_the_real_factory(tmp_path: Path) -> None:
    await seed(tmp_path)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN)  # build_provider; nothing here calls a model
    full = {"proxy": "http://127.0.0.1:7890", "timeout": 1.5}
    bodies = [
        chat("c1", options=full | {"temperature": 2, "extra_body": {"top_p": 1}}, key=KEY),
        chat("r1", kind="openai_responses", options=full | {"input_format": "messages", "extra_body": {}}),
        chat(
            "x1",
            kind="codex",
            base_url="https://chatgpt.com/backend-api/codex/",
            options=full | {"auth_file": str(auth)},
        ),
    ]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as http:
            for body in bodies:
                response = await http.post("/api/providers", json=body)
                assert response.status_code == 201, response.text
                assert app.state.providers.get(body["id"]) is not None
            kinds = {pid: type(app.state.providers.get(pid).provider).__name__ for pid in ("c1", "r1", "x1")}
            assert kinds == {"c1": "OpenAIChat", "r1": "OpenAIResponses", "x1": "Codex"}
            # The CLI's `timeout: null` row ("broken") failed to build with the real factory too.
            assert app.state.providers.errors["broken"].startswith("TypeError")


async def test_codex_login_file_must_exist_as_json(nest: Nest, tmp_path: Path) -> None:
    folder = tmp_path / "codex"
    folder.mkdir()
    (folder / "auth.txt").write_text("{}", encoding="utf-8")
    (folder / "dir.json").mkdir()
    for path in (folder / "missing.json", folder / "auth.txt", folder / "dir.json"):
        body = chat("cx", kind="codex", base_url="", options={"auth_file": str(path)})
        response = await nest.call("POST", "/api/providers", body)
        assert response.status_code == 422 and response.json()["detail"].startswith("找不到 Codex 登录文件")
    auth = folder / "auth.json"
    auth.write_text("not even json", encoding="utf-8")  # only existence is checked, never content
    created = await nest.call(
        "POST",
        "/api/providers",
        {"id": "cx", "kind": "codex", "base_url": " ", "model": "gpt-5", "options": {"auth_file": str(auth)}},
    )
    assert created.status_code == 201, created.text
    view = created.json()
    assert view["base_url"] == "https://chatgpt.com/backend-api/codex" and view["key_ref"] == ""
    assert view["key_set"] is False and view["options"] == {"auth_file": str(auth)}


async def test_create_normalizes_writes_the_key_and_publishes(nest: Nest) -> None:
    body = chat(
        "deepseek",
        base_url=" https://api.deepseek.example/v1/ ",
        model=" deepseek-chat ",
        options={
            "timeout": 30,
            "temperature": 0,
            "proxy": "http://127.0.0.1:7890/",
            "extra_body": {"top_p": 0.9},
        },
        key=f"  {KEY}\n",
    )
    response = await nest.call("POST", "/api/providers", body)
    assert response.status_code == 201, response.text
    view = response.json()
    assert view["base_url"] == "https://api.deepseek.example/v1" and view["model"] == "deepseek-chat"
    assert view["options"] == {
        "timeout": 30,
        "temperature": 0,
        "proxy": "http://127.0.0.1:7890",
        "extra_body": {"top_p": 0.9},
    }
    assert view["key_ref"] == "NEST_KEY_DEEPSEEK" and view["key_set"] and view["build_error"] is None
    assert view["health"]["last_ok_at"] is None
    assert nest.file_value("NEST_KEY_DEEPSEEK") == KEY and nest.factory.seen_keys["deepseek"] == KEY
    assert ("providers", {"provider_id": "deepseek"}) in nest.events
    assert ("bots", {"bot_id": None}) in nest.events
    # Empty and null options mean the default and are not stored.
    other = await nest.call("POST", "/api/providers", chat("plain", options={"proxy": "", "timeout": None}))
    assert other.status_code == 201 and other.json()["options"] == {}


# -- keys -----------------------------------------------------------------------------------------------


async def test_the_key_never_leaves_env(nest: Nest, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    created = await nest.call("POST", "/api/providers", chat("leak", key=KEY))
    assert created.status_code == 201 and nest.file_value("NEST_KEY_LEAK") == KEY
    rejected = [
        await nest.call("POST", "/api/providers", chat("BAD", key=KEY)),
        await nest.call("POST", "/api/providers", chat("leak", key=KEY)),
        await nest.call("POST", "/api/providers", chat("leak2", key=[KEY])),
        await nest.call("POST", "/api/providers", chat("leak2", key=KEY + " tail")),
        await nest.call("POST", "/api/providers", chat("leak2", note=KEY)),
        await nest.call("PATCH", "/api/providers/nope", {"key": KEY}),
        await nest.call("PATCH", "/api/providers/leak", {"key": KEY, "clear_key": True}),
        await nest.call("PATCH", "/api/providers/leak", {"key": {"value": KEY}}),
        await nest.call("PATCH", "/api/providers/leak", {"id": KEY}),
        await nest.call("PATCH", "/api/providers/borrow", {"key": KEY}),
        await nest.call("PATCH", "/api/providers/leak", {"kind": "codex", "base_url": "", "key": KEY}),
    ]
    assert [r.status_code for r in rejected] == [422, 409, 422, 422, 422, 404, 422, 422, 422, 409, 422]

    # A build that fails with the (new) key in its message.
    nest.factory.fail = lambda config, secrets: RuntimeError(f"bad header {secrets.get(config.key_ref)}")
    failed = await nest.call("PATCH", "/api/providers/leak", {"key": PLAIN_KEY})
    assert failed.status_code == 400 and failed.json()["detail"].startswith("模型设置有误：RuntimeError")
    nest.factory.fail = None

    assert (await nest.call("PATCH", "/api/providers/leak", {"model": "chat-2"})).status_code == 200
    assert (await nest.call("POST", "/api/providers/leak/test")).json()["ok"] is True
    await nest.call("GET", "/api/providers")
    assert (await nest.call("DELETE", "/api/providers/leak")).json() == {"ok": True}
    assert nest.file_value("NEST_KEY_LEAK") is None

    assert all("LEAKCHECK" not in body for body in nest.bodies)
    assert all("LEAKCHECK" not in sse(kind, data) for kind, data in nest.events)
    assert "LEAKCHECK" not in caplog.text
    db = nest.state.db
    for table in ("providers", "llm_calls", "decisions"):
        for row in await db.fetchall(f"SELECT * FROM {table}"):
            assert "LEAKCHECK" not in repr(tuple(row)), table


async def test_dollar_braces_in_a_key_stay_literal(nest: Nest) -> None:
    literal = "sk-${NEST_QQ_10001_WS_TOKEN}-$HOME"
    response = await nest.call("PATCH", "/api/providers/ark", {"key": literal})
    assert response.status_code == 200
    assert nest.factory.seen_keys["ark"] == literal
    assert nest.state.secrets.get("NEST_KEY_ARK") == literal
    assert SecretStore(nest.env).get("NEST_KEY_ARK") == literal  # as nest will read it after a restart


async def test_clear_key(nest: Nest) -> None:
    response = await nest.call("PATCH", "/api/providers/ark", {"clear_key": True})
    assert response.status_code == 200 and response.json()["key_set"] is False
    assert "NEST_KEY_ARK" not in nest.env.read_text(encoding="utf-8")
    assert nest.factory.seen_keys["ark"] == ""


async def test_a_key_from_the_environment_cannot_be_changed_here(
    nest: Nest, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = nest.env.read_bytes()
    monkeypatch.setenv("NEST_KEY_ARK", "from-env")
    view = await nest.view("ark")
    assert view["key_from_env"] is True and view["key_set"] is True
    for body in ({"key": KEY}, {"clear_key": True}):
        response = await nest.call("PATCH", "/api/providers/ark", body)
        assert response.status_code == 409
        assert response.json()["detail"] == "这个模型的密钥由环境变量 NEST_KEY_ARK 提供，在这里改不会生效"
    assert nest.env.read_bytes() == before
    # An empty variable does not override .env: the key can be changed.
    monkeypatch.setenv("NEST_KEY_ARK", "")
    assert (await nest.view("ark"))["key_from_env"] is False
    assert (await nest.call("PATCH", "/api/providers/ark", {"key": KEY})).status_code == 200
    assert nest.file_value("NEST_KEY_ARK") == KEY
    # A new model whose default key name is set in the environment.
    monkeypatch.setenv("NEST_KEY_ENVY", "from-env")
    assert (await nest.call("POST", "/api/providers", chat("envy", key=KEY))).status_code == 409
    created = await nest.call("POST", "/api/providers", chat("envy"))
    assert created.status_code == 201 and created.json()["key_from_env"] is True
    assert nest.factory.seen_keys["envy"] == "from-env"


async def test_a_shared_key_rebuilds_every_model_that_reads_it(nest: Nest) -> None:
    registry = nest.state.providers
    before_b = registry.get("share-b")
    response = await nest.call("PATCH", "/api/providers/share-a", {"key": KEY})
    assert response.status_code == 200 and response.json()["key_shared_with"] == ["share-b"]
    after_b = registry.get("share-b")
    assert after_b is not None and after_b is not before_b
    assert nest.factory.seen_keys["share-a"] == nest.factory.seen_keys["share-b"] == KEY
    assert ("providers", {"provider_id": "share-b"}) in nest.events

    # If any sharer fails to build, nothing is written and nothing is swapped.
    env, runtimes, builds = nest.env.read_bytes(), dict(registry.runtimes), len(nest.factory.builds)
    nest.factory.fail, nest.factory.fail_for = (lambda c, s: ValueError("坏了")), {"share-b"}
    failed = await nest.call("PATCH", "/api/providers/share-a", {"key": "sk-other-key"})
    assert (
        failed.status_code == 400 and failed.json()["detail"] == "模型设置有误（share-b 也用这个密钥）：坏了"
    )
    assert nest.env.read_bytes() == env and registry.runtimes == runtimes
    # share-a was built for this change before share-b failed: it is closed, not left open.
    made = nest.factory.builds[builds:]
    assert [config.id for config, _ in made] == ["share-a"] and all(fake.closed for _, fake in made)
    nest.factory.fail = None

    # Clearing it clears it for both; deleting one keeps the entry for the other.
    assert (await nest.call("PATCH", "/api/providers/share-b", {"clear_key": True})).status_code == 200
    assert (await nest.view("share-a"))["key_set"] is False and nest.factory.seen_keys["share-a"] == ""
    assert (await nest.call("PATCH", "/api/providers/share-a", {"key": KEY})).status_code == 200
    assert (await nest.call("DELETE", "/api/providers/share-a")).status_code == 200
    assert nest.file_value("NEST_KEY_SHARED") == KEY


async def test_qq_connection_tokens_are_never_written_or_removed(nest: Nest) -> None:
    before = nest.env.read_bytes()
    for body in ({"key": KEY}, {"clear_key": True}):
        response = await nest.call("PATCH", "/api/providers/borrow", body)
        assert response.status_code == 409 and response.json()["detail"] == "这个密钥名被 QQ 连接占用"
    assert nest.env.read_bytes() == before
    # A new model whose default key name a QQ connection uses.
    taken = await nest.call("POST", "/api/providers", chat("taken"))
    assert taken.status_code == 409 and taken.json()["detail"] == "这个密钥名被 QQ 连接占用"
    # Deleting a model never removes a QQ token from .env.
    assert (await nest.call("DELETE", "/api/providers/borrow")).status_code == 200
    assert nest.file_value("NEST_QQ_10001_WS_TOKEN") == "qq-token-10001"


async def test_a_qq_token_under_a_nest_key_name_is_never_removed(nest: Nest) -> None:
    # 10003's HTTP token is stored under a name that looks like one nest made (NEST_KEY_…).
    nest.state.secrets.set("NEST_KEY_TAKEN", "qq-http-token-10003")
    hand = ProviderConfig("hand", "openai_chat", "http://h.example/v1", "m", "NEST_KEY_TAKEN")
    await nest.state.repo.upsert_provider(hand)  # added by hand (the CLI) with that name
    assert (await nest.call("DELETE", "/api/providers/hand")).status_code == 200
    assert nest.file_value("NEST_KEY_TAKEN") == "qq-http-token-10003"
    # Switching such a model to Codex (which reads no key) keeps the token too.
    await nest.state.repo.upsert_provider(hand)
    codex = {"kind": "codex", "base_url": "", "model": "gpt-5", "options": {}}
    assert (await nest.call("PATCH", "/api/providers/hand", codex)).status_code == 200
    assert nest.file_value("NEST_KEY_TAKEN") == "qq-http-token-10003"


async def test_a_new_model_never_inherits_an_old_key(nest: Nest) -> None:
    created = await nest.call("POST", "/api/providers", chat("fresh"))
    assert created.status_code == 201 and created.json()["key_set"] is False
    assert nest.file_value("NEST_KEY_FRESH") is None and nest.factory.seen_keys["fresh"] == ""
    assert (await nest.call("DELETE", "/api/providers/fresh")).status_code == 200
    nest.state.secrets.set("NEST_KEY_FRESH", "leftover-again")
    replaced = await nest.call("POST", "/api/providers", chat("fresh", key=KEY))
    assert replaced.status_code == 201 and nest.file_value("NEST_KEY_FRESH") == KEY
    # The default key name of a new model belongs to another model.
    clash = await nest.call("POST", "/api/providers", chat("clash", key=KEY))
    assert clash.status_code == 409 and clash.json()["detail"] == "密钥名 NEST_KEY_CLASH 已被 other 使用"


async def test_a_key_the_cli_wrote_while_nest_runs_is_read_before_any_change(nest: Nest) -> None:
    # `nest provider-add` runs in its own process, with its own SecretStore and database handle.
    cli = SecretStore(nest.env)
    cli.set("NEST_KEY_LATE", "late-secret")
    await nest.state.repo.upsert_provider(
        ProviderConfig("late", "openai_chat", "http://f.example/v1", "m", "NEST_KEY_LATE")
    )
    assert (await nest.view("late"))["build_error"] == NOT_LOADED
    # Saving it once, with no key typed, builds it with the key that is in .env now.
    saved = await nest.call("PATCH", "/api/providers/late", {})
    assert saved.status_code == 200 and saved.json()["key_set"] is True
    assert nest.factory.seen_keys["late"] == "late-secret"
    # The CLI changes a running model's key: the next edit rebuilds it with the new one.
    cli.set("NEST_KEY_GROK", "grok-secret-2")
    assert (await nest.call("PATCH", "/api/providers/grok", {"model": "grok-2"})).status_code == 200
    assert nest.factory.seen_keys["grok"] == "grok-secret-2"
    # A value the CLI left under a new model's key name is removed, never inherited.
    cli.set("NEST_KEY_NEWER", "left-by-the-cli")
    created = await nest.call("POST", "/api/providers", chat("newer"))
    assert created.status_code == 201 and created.json()["key_set"] is False
    assert nest.factory.seen_keys["newer"] == "" and nest.file_value("NEST_KEY_NEWER") is None


async def test_a_failed_build_changes_nothing(nest: Nest) -> None:
    registry, repo = nest.state.providers, nest.state.repo
    env, runtimes, errors = nest.env.read_bytes(), dict(registry.runtimes), dict(registry.errors)
    row = await repo.get_provider("ark")
    nest.factory.fail = lambda config, secrets: TypeError("boom")
    patched = await nest.call("PATCH", "/api/providers/ark", {"model": "new", "key": KEY})
    assert patched.status_code == 400 and patched.json()["detail"] == "模型设置有误：TypeError: boom"
    created = await nest.call("POST", "/api/providers", chat("fresh", key=KEY))
    assert created.status_code == 400
    fixed = await nest.call("PATCH", "/api/providers/broken", {"options": {"timeout": 30}})
    assert fixed.status_code == 400
    assert nest.env.read_bytes() == env
    assert registry.runtimes == runtimes and registry.errors == errors
    assert await repo.get_provider("ark") == row and await repo.get_provider("fresh") is None
    assert not any(kind == "providers" for kind, _ in nest.events)


async def test_a_failed_database_write_restores_the_key(nest: Nest, monkeypatch: pytest.MonkeyPatch) -> None:
    before = nest.file_value("NEST_KEY_ARK")
    runtime = nest.state.providers.get("ark")

    async def broken_upsert(config: ProviderConfig) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(nest.state.repo, "upsert_provider", broken_upsert)
    response = await nest.call("PATCH", "/api/providers/ark", {"key": KEY})
    assert response.status_code == 500
    assert nest.file_value("NEST_KEY_ARK") == before and nest.state.providers.get("ark") is runtime
    assert nest.factory.latest("ark").closed  # the provider built for the change was closed


# -- edits and hot swaps --------------------------------------------------------------------------------


async def test_kind_changes_move_the_key_name(nest: Nest) -> None:
    mismatch = await nest.call("PATCH", "/api/providers/grok", {"kind": "openai_responses"})
    assert mismatch.status_code == 422 and mismatch.json()["detail"] == "换接口类型时请把参数一起提交"
    codex = await nest.call("PATCH", "/api/providers/grok", {"kind": "codex", "base_url": "", "options": {}})
    assert codex.status_code == 200, codex.text
    assert (
        codex.json()["key_ref"] == "" and codex.json()["base_url"] == "https://chatgpt.com/backend-api/codex"
    )
    assert nest.file_value("NEST_KEY_GROK") is None  # nobody reads it any more
    assert (await nest.state.repo.get_provider("grok")).key_ref == ""
    back = await nest.call(
        "PATCH",
        "/api/providers/grok",
        {"kind": "openai_chat", "base_url": "http://127.0.0.1:8180/v1/", "options": {}, "key": KEY},
    )
    assert back.status_code == 200 and back.json()["key_ref"] == "NEST_KEY_GROK"
    assert back.json()["base_url"] == "http://127.0.0.1:8180/v1" and nest.file_value("NEST_KEY_GROK") == KEY
    assert (await nest.call("PATCH", "/api/providers/grok", {"id": "renamed"})).status_code == 422
    assert (await nest.call("PATCH", "/api/providers/nope", {"model": "x"})).status_code == 404


async def test_hot_swap_through_the_api(nest: Nest) -> None:
    old = nest.factory.latest("ark")
    old.delay = 0.3
    slow = asyncio.create_task(nest.call("POST", "/api/providers/ark/test"))
    await eventually(lambda: old.requests)
    patched = await nest.call("PATCH", "/api/providers/ark", {"model": "deepseek-v2"})
    assert patched.status_code == 200 and patched.json()["model"] == "deepseek-v2"
    new = nest.factory.latest("ark")
    assert new is not old and not old.closed  # still answering the slow call
    result = (await slow).json()
    assert result["ok"] is True and result["text"] == "在"
    await eventually(lambda: old.closed)
    assert (await nest.call("POST", "/api/providers/ark/test")).json()["ok"] is True
    assert len(new.requests) == 1 and not new.closed
    assert nest.state.brain.providers.get("ark").config.model == "deepseek-v2"


async def test_delete_is_refused_while_a_bot_uses_the_model(nest: Nest) -> None:
    refused = await nest.call("DELETE", "/api/providers/ark")
    assert refused.status_code == 409 and refused.json()["detail"] == "还有 bot 在用这个模型：阿鲸"
    assert (await nest.call("POST", "/api/providers", chat("spare", key=KEY))).status_code == 201
    # Decided from the database, not from what the brain has loaded.
    await sql(nest, "UPDATE bots SET provider_id = 'spare' WHERE id = 'qq:10002'")
    refused = await nest.call("DELETE", "/api/providers/spare")
    assert refused.status_code == 409 and refused.json()["detail"] == "还有 bot 在用这个模型：阿马"
    await sql(nest, "UPDATE bots SET provider_id = 'grok' WHERE id = 'qq:10002'")

    spare = nest.factory.latest("spare")
    deleted = await nest.call("DELETE", "/api/providers/spare")
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    assert nest.state.providers.get("spare") is None and nest.file_value("NEST_KEY_SPARE") is None
    assert "spare" not in [v["id"] for v in (await nest.call("GET", "/api/providers")).json()]
    await eventually(lambda: spare.closed)
    assert (await nest.call("DELETE", "/api/providers/spare")).status_code == 404
    # A row that never built can be deleted too.
    assert (await nest.call("DELETE", "/api/providers/broken")).status_code == 200
    assert "broken" not in nest.state.providers.errors


# -- the config lock ------------------------------------------------------------------------------------


async def test_config_changes_wait_for_the_config_lock_but_model_tests_do_not(nest: Nest) -> None:
    [channel_id] = nest.state.brain.bots["qq:10001"].channels
    writes = [
        ("PATCH", "/api/bots/qq:10001", {"mode": "paused"}),
        ("POST", "/api/bots/pause-all", None),
        ("POST", "/api/providers", chat("locked")),
        ("PATCH", "/api/providers/ark", {"model": "m2"}),
        ("DELETE", "/api/providers/other", None),
        ("PATCH", f"/api/bots/qq:10001/channels/{channel_id}", {"enabled": False}),
    ]
    async with nest.state.config_lock:
        tasks = [asyncio.create_task(nest.call(*w)) for w in writes]
        tested = await asyncio.wait_for(nest.call("POST", "/api/providers/grok/test"), 2)
        assert tested.json()["ok"] is True
        listed = await asyncio.wait_for(nest.call("GET", "/api/providers"), 2)
        assert listed.status_code == 200
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
    results = await asyncio.gather(*tasks)
    assert [r.status_code for r in results] == [200, 200, 201, 200, 200, 200]


async def test_unsaveable_characters_in_option_names_and_login_paths_are_refused(nest: Nest) -> None:
    for raw, detail in (
        (r'{"options": {"temp\ud800": 1}}', "参数名里有不能保存的字符"),
        (
            r'{"kind": "codex", "base_url": "", "model": "m", "options": {"auth_file": "/tmp/a\ud800.json"}}',
            "登录文件路径里有不能保存的字符",
        ),
    ):
        response = await nest.http.patch(
            "/api/providers/grok", content=raw.encode(), headers={"content-type": "application/json"}
        )
        assert response.status_code == 422 and response.json() == {"detail": detail}, raw


async def test_an_unreadable_env_blocks_changes_but_not_the_listing(
    nest: Nest, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked() -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(nest.state.secrets, "reload", locked)
    listing = await nest.http.get("/api/providers")
    assert listing.status_code == 200 and listing.json()
    response = await nest.call("PATCH", "/api/providers/grok", {"model": "m2"})
    assert response.status_code == 500 and "读取 .env 失败" in response.json()["detail"]
    assert (await nest.state.repo.get_provider("grok")).model != "m2"


# -- 获取列表 -----------------------------------------------------------------------------------------


async def test_listing_models_reuses_a_saved_key_only_for_its_own_server(
    nest: Nest, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str, str, dict[str, Any]]] = []

    async def fake_list(kind: str, base_url: str, key: str = "", **options: Any) -> list[str]:
        asked.append((kind, base_url, key, options))
        return ["m-1", "m-2"]

    monkeypatch.setattr("nest.api.routes_models.list_models", fake_list)
    body = {"kind": "openai_responses", "base_url": "https://ark.example/api/plan/v3/", "provider_id": "ark"}
    response = await nest.call("POST", "/api/providers/models", body)
    assert response.json() == {"ok": True, "models": ["m-1", "m-2"]}
    assert asked[-1][:3] == ("openai_responses", "https://ark.example/api/plan/v3", "ark-secret")

    await nest.call("POST", "/api/providers/models", body | {"base_url": "https://elsewhere.example/v3"})
    assert asked[-1][2] == ""  # another server: the saved key is not sent there
    await nest.call("POST", "/api/providers/models", body | {"key": " typed-key "})
    assert asked[-1][2] == "typed-key"

    claude = {
        "kind": "anthropic",
        "base_url": "https://relay.example/v1",
        "options": {"auth_header": "bearer"},
    }
    await nest.call("POST", "/api/providers/models", claude)
    assert asked[-1][3]["auth_header"] == "bearer"

    codex = await nest.call("POST", "/api/providers/models", {"kind": "codex", "base_url": ""})
    assert codex.status_code == 422 and "Codex 订阅不能获取模型列表" in codex.text
    assert all("ark-secret" not in text for text in nest.bodies)
