"""监控 in the running app: the API, runs, pushing, the model, the schedule, shutdown.

The internet is an httpx.MockTransport (`Web`) and every QQ account is a fake adapter that records what
it would send: nothing here reaches the network or QQ.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest.adapters.base import AdapterStatus
from nest.app import create_app
from nest.config import Settings
from nest.core.events import ActionResult, ChannelInfo, ChannelRef, SendMessage
from nest.llm.base import ProviderError
from nest.secrets import SecretReader
from nest.storage.accounts import Account, upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.storage.watches import WatchStore
from nest.watch.watcher import Watcher, WatchPolicy
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}
WHALE, PONY = "qq:10001", "qq:10002"
FEED = "https://news.example/feed.xml"
PAGE = "https://news.example/notice"
SHOP = "https://shop.example/item/1"
QUIET = WatchPolicy(tick=60, startup_delay=0, stagger=0.05)  # nothing runs unless due or asked


def rss(*keys: str) -> str:
    items = "".join(
        f"<item><title>标题{key}</title><link>https://news.example/{key}</link><guid>{key}</guid></item>"
        for key in keys
    )
    head = '<?xml version="1.0" encoding="utf-8"?><rss version="2.0">'
    return f"{head}<channel><title>t</title>{items}</channel></rss>"


class Web:
    """Stands in for the internet: address -> (body, content type), or a handler."""

    def __init__(self) -> None:
        self.pages: dict[str, Any] = {}
        self.requests: list[str] = []
        self.active = 0
        self.most_active = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        try:
            page = self.pages.get(url)
            if page is None:
                return httpx.Response(404)
            if callable(page):
                page = await page(request)
                if isinstance(page, httpx.Response):
                    return page
            body, content_type = page
            return httpx.Response(200, content=body.encode(), headers={"content-type": content_type})
        finally:
            self.active -= 1

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


class FakeAdapter:
    """A connected QQ account that records what it is asked to send."""

    platform = "qq"
    capabilities = frozenset({"send_text"})

    def __init__(self, account: Account) -> None:
        self.account = account.self_id
        self.actions: list[SendMessage] = []
        self.result = ActionResult("succeeded", message_id="900")
        self.delay = 0.0

    async def start(self, emit: Any) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def perform(self, action: SendMessage) -> ActionResult:
        self.actions.append(action)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result

    async def list_channels(self) -> list[ChannelInfo]:
        return []

    def status(self) -> AdapterStatus:
        return AdapterStatus("connected", "", 0.0)


@dataclass
class World:
    http: httpx.AsyncClient
    app: FastAPI
    web: Web
    adapters: dict[str, FakeAdapter]
    models: dict[str, FakeProvider]
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def state(self) -> Any:
        return self.app.state

    @property
    def store(self) -> WatchStore:
        return self.state.watcher.store

    def sent(self, bot: str = WHALE) -> list[SendMessage]:
        return self.adapters[bot].actions

    async def add(self, **fields: Any) -> dict[str, Any]:
        body = {
            "name": "新闻",
            "kind": "feed",
            "url": FEED,
            "bot_id": WHALE,
            "target_kind": "group",
            "target_id": "42",
            "enabled": False,  # checked only when a test asks
        } | fields
        response = await self.http.post("/api/watches", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    async def patch(self, watch_id: int, body: dict[str, Any]) -> httpx.Response:
        return await self.http.patch(f"/api/watches/{watch_id}", json=body)

    async def run(self, watch_id: int) -> dict[str, Any]:
        response = await self.http.post(f"/api/watches/{watch_id}/run")
        assert response.status_code == 200, response.text
        return response.json()

    async def sql(self, statement: str, *params: Any) -> None:
        async with self.state.db.transaction() as conn:
            await conn.execute(statement, params)

    def watch_events(self) -> list[dict[str, Any]]:
        return [data for kind, data in self.events if kind == "watch"]


async def seed(home: Path) -> None:
    db = await Database.open(home / "data" / "nest.db")
    repo, store = BotRepo(db), MessageStore(db)
    try:
        for uin, name in (("10001", "阿鲸"), ("10002", "阿马")):
            await upsert_account(
                db, platform="qq", self_id=uin, name=name, http_url="http://x", ws_url="ws://x/"
            )
        await repo.upsert_provider(ProviderConfig("ark", "openai_chat", "http://a.example/v1", "m"))
        await repo.upsert_bot(account_id=WHALE, name="阿鲸", aliases=[], provider_id="ark", mode="live")
        await repo.upsert_bot(account_id=PONY, name="阿马", aliases=[], provider_id="ark")
        persona = await repo.upsert_persona("鲸", "你是一只爱冒泡的鲸鱼。")
        for uin, group, name in (("10001", "42", "Demo群"), ("10001", "77", ""), ("10002", "99", "测试群三")):
            channel = await store.channel_id(ChannelRef("qq", uin, "group", group))
            await store.name_channels([ChannelInfo(ChannelRef("qq", uin, "group", group), name)])
            if group == "42":
                await repo.set_channel(f"qq:{uin}", channel, enabled=True, persona_id=persona)
    finally:
        await db.close()


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with running(tmp_path, QUIET) as w:
        yield w


def running(home: Path, policy: WatchPolicy) -> Any:
    """The app on a seeded home (seeded now unless it already is), as an async context manager."""

    @asynccontextmanager
    async def context() -> AsyncIterator[World]:
        if not (home / "data" / "nest.db").exists():
            await seed(home)
        web = Web()
        adapters: dict[str, FakeAdapter] = {}
        models: dict[str, FakeProvider] = {}

        def adapter_factory(account: Account, secrets: SecretReader, on_status: Any, backfill: Any) -> Any:
            return adapters.setdefault(account.id, FakeAdapter(account))

        def provider_factory(config: ProviderConfig, secrets: SecretReader) -> FakeProvider:
            return models.setdefault(config.id, FakeProvider(name=config.id, default="鲸鱼播报：有新消息"))

        settings = Settings(home=home, port=18800, _env_file=None)
        app = create_app(
            settings,
            token=TOKEN,
            adapter_factory=adapter_factory,
            provider_factory=provider_factory,
            watch_policy=policy,
            watch_transport=web.transport(),
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url=settings.base_url, headers=AUTH
            ) as http:
                w = World(http, app, web, adapters, models)
                queue = app.state.broadcaster.subscribe()

                async def drain() -> None:
                    while True:
                        w.events.append(await queue.get())

                drainer = asyncio.create_task(drain())
                try:
                    yield w
                finally:
                    drainer.cancel()

    return context()


# -- the API ----------------------------------------------------------------------------------------------


async def test_create_list_and_view(world: World) -> None:
    created = await world.add(name="  公告  ", kind="page", url=PAGE, selector=" #news ", interval_minutes=30)
    assert created == {
        "id": created["id"],
        "name": "公告",
        "kind": "page",
        "url": PAGE,
        "selector": "#news",
        "threshold": None,
        "interval_minutes": 30,
        "bot_id": WHALE,
        "bot_name": "阿鲸",
        "target_kind": "group",
        "target_id": "42",
        "target_name": "Demo群",
        "provider_id": None,
        "instructions": "",
        "use_proxy": False,
        "mode": "preview",  # 试运行 by default
        "enabled": False,
        "last_run_at": None,
        "next_run_at": created["next_run_at"],
        "running": False,
        "last_run": None,
        "last_message": None,
        "counts": {"runs_7d": 0, "changes_7d": 0, "errors_7d": 0},
    }
    await world.add(name="甲 私聊", target_kind="private", target_id="10086")
    await world.add(name="乙", target_id="77")
    listed = (await world.http.get("/api/watches")).json()
    assert [(w["name"], w["target_name"]) for w in listed] == [
        ("乙", "群 77"),  # a group whose name is not known yet
        ("公告", "Demo群"),
        ("甲 私聊", "私聊 10086"),
    ]


async def test_targets(world: World) -> None:
    targets = (await world.http.get("/api/watches/targets")).json()
    assert targets == [
        {
            "bot_id": WHALE,
            "bot_name": "阿鲸",
            "groups": [{"group_id": "42", "name": "Demo群"}, {"group_id": "77", "name": ""}],
        },
        {"bot_id": PONY, "bot_name": "阿马", "groups": [{"group_id": "99", "name": "测试群三"}]},
    ]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"name": " "}, "名字要填 1–40 个字"),
        ({"name": "x" * 41}, "名字要填 1–40 个字"),
        ({"name": "a\nb"}, "名字里不能有换行或控制字符"),
        ({"kind": "rss"}, "类型只能是订阅、网页变化或价格"),
        ({"url": "news.example/feed"}, "地址要写成 http:// 或 https:// 开头的完整网址"),
        ({"url": "ftp://news.example/feed"}, "地址要写成 http:// 或 https:// 开头的完整网址"),
        ({"url": "https://news.example/a b"}, "地址要写成 http:// 或 https:// 开头的完整网址"),
        ({"url": "https://news.example:99999/"}, "地址要写成 http:// 或 https:// 开头的完整网址"),
        ({"url": "https://me:secret@news.example/"}, "地址里不要带用户名或密码"),
        ({"url": "https://news.example/" + "x" * 1000}, "地址最多 1000 个字"),
        ({"url": ""}, "请填写地址"),
        ({"kind": "page", "selector": "x" * 301}, "选择器最多 300 个字"),
        ({"kind": "page", "selector": "div[["}, "选择器写得不对，可以在浏览器里右键元素 → 检查 → 复制选择器"),
        ({"kind": "price"}, "价格监控要填选择器（价格在网页上的位置）"),
        ({"kind": "page", "threshold": 10}, "只有价格监控能设「低于这个价格才推送」"),
        ({"kind": "price", "selector": ".p", "threshold": 0}, "提醒价格要大于 0"),
        ({"kind": "price", "selector": ".p", "threshold": True}, "提醒价格格式不对"),
        ({"interval_minutes": 4}, "检查间隔要在 5–1440 分钟之间"),
        ({"interval_minutes": 1441}, "检查间隔要在 5–1440 分钟之间"),
        ({"interval_minutes": 30.0}, "检查间隔格式不对"),
        ({"bot_id": "qq:404"}, "没有这个 bot"),
        ({"target_kind": "channel"}, "推送到只能选群或私聊"),
        ({"target_id": "12345"}, "阿鲸 的号不在这个群里"),
        ({"target_id": "99"}, "阿鲸 的号不在这个群里"),  # 阿马's group
        ({"bot_id": PONY}, "阿马 的号不在这个群里"),
        ({"target_kind": "private", "target_id": "1234"}, "QQ 号要填 5–12 位数字"),
        ({"target_kind": "private", "target_id": "１２３４５"}, "QQ 号要填 5–12 位数字"),
        ({"provider_id": "nope"}, "没有这个模型"),
        ({"instructions": "x" * 501}, "额外要求最多 500 字"),
        ({"instructions": "a\x00"}, "额外要求里有不能保存的字符"),
        ({"mode": "live"}, "模式只能是试运行或推送"),
        ({"state": {}}, "「state」不认识这个设置"),
        ({"target_id": 42}, "群号或 QQ 号格式不对"),
    ],
)
async def test_validation(world: World, body: dict[str, Any], detail: str) -> None:
    base = {
        "name": "新闻",
        "kind": "feed",
        "url": FEED,
        "bot_id": WHALE,
        "target_kind": "group",
        "target_id": "42",
    }
    response = await world.http.post("/api/watches", json=base | body)
    assert response.status_code == 422 and response.json() == {"detail": detail}, response.text
    assert "secret" not in response.text
    assert await world.store.all() == []


async def test_kinds_without_a_selector_or_threshold_drop_them(world: World) -> None:
    feed = await world.add(selector=".ignored")
    assert feed["selector"] == ""
    price = await world.add(kind="price", url=SHOP, selector=".price", threshold=99.5)
    assert price["threshold"] == 99.5
    page = (await world.patch(price["id"], {"kind": "page"})).json()
    assert page["kind"] == "page" and page["selector"] == ".price" and page["threshold"] is None
    response = await world.patch(price["id"], {"kind": "price", "selector": ""})
    assert response.json() == {"detail": "价格监控要填选择器（价格在网页上的位置）"}
    again = (await world.patch(price["id"], {"kind": "price", "threshold": 80})).json()
    assert again["threshold"] == 80
    cleared = (await world.patch(price["id"], {"threshold": None, "provider_id": "ark"})).json()
    assert cleared["threshold"] is None and cleared["provider_id"] == "ark"
    assert (await world.patch(price["id"], {"provider_id": None})).json()["provider_id"] is None


async def test_patch_and_delete(world: World) -> None:
    watch = await world.add()
    response = await world.patch(watch["id"], {})
    assert response.status_code == 422 and response.json() == {"detail": "没有要改的内容"}
    response = await world.patch(watch["id"], {"name": None, "mode": None})
    assert response.json() == {"detail": "没有要改的内容"}  # null means "not sent"
    changed = (
        await world.patch(watch["id"], {"name": "早报", "mode": "push", "instructions": " 语气活泼 "})
    ).json()
    assert (changed["name"], changed["mode"], changed["instructions"]) == ("早报", "push", "语气活泼")
    moved = (await world.patch(watch["id"], {"target_kind": "private", "target_id": "10086"})).json()
    assert moved["target_name"] == "私聊 10086"
    response = await world.patch(watch["id"], {"bot_id": PONY, "target_kind": "group", "target_id": "99"})
    assert response.json()["target_name"] == "测试群三" and response.json()["bot_name"] == "阿马"
    response = await world.patch(watch["id"], {"target_id": "42"})
    assert response.status_code == 422 and response.json() == {"detail": "阿马 的号不在这个群里"}

    for method, path in (
        ("PATCH", "/api/watches/999"),
        ("DELETE", "/api/watches/999"),
        ("POST", "/api/watches/999/run"),
        ("GET", "/api/watches/999/runs"),
    ):
        response = await world.http.request(method, path, json={"name": "x"} if method == "PATCH" else None)
        assert response.status_code == 404 and response.json() == {"detail": "没有这个监控"}, path
    assert (await world.http.delete(f"/api/watches/{watch['id']}")).json() == {"ok": True}
    assert (await world.http.get("/api/watches")).json() == []
    assert (await world.http.delete(f"/api/watches/{watch['id']}")).status_code == 404


async def test_writes_wait_for_the_config_lock(world: World) -> None:
    watch = await world.add()
    async with world.state.config_lock:
        tasks = [
            asyncio.create_task(world.http.post("/api/watches", json={**_body(), "name": "新的"})),
            asyncio.create_task(world.patch(watch["id"], {"name": "改了"})),
            asyncio.create_task(world.http.delete(f"/api/watches/{watch['id']}")),
        ]
        assert (await asyncio.wait_for(world.http.get("/api/watches"), 2)).status_code == 200
        await asyncio.sleep(0.1)
        assert not any(task.done() for task in tasks)
    assert [r.status_code for r in await asyncio.gather(*tasks)] == [201, 200, 200]


def _body(**fields: Any) -> dict[str, Any]:
    return {
        "name": "新闻",
        "kind": "feed",
        "url": FEED,
        "bot_id": WHALE,
        "target_kind": "group",
        "target_id": "42",
        "enabled": False,
    } | fields


# -- runs -------------------------------------------------------------------------------------------------


async def test_feed_baseline_then_new_entries_in_preview(world: World) -> None:
    world.web.pages[FEED] = (rss("a", "b"), "application/rss+xml")
    watch = await world.add()
    first = await world.run(watch["id"])
    assert set(first) == {
        "id",
        "started_at",
        "duration_ms",
        "outcome",
        "changes",
        "detail",
        "message",
        "delivered",
        "error",
    }
    assert (first["outcome"], first["changes"], first["message"], first["delivered"], first["error"]) == (
        "baseline",
        0,
        None,
        None,
        None,
    )
    assert first["detail"] == "第一次检查：记下了现在的 2 条内容，以后有新的才推送"
    world.web.pages[FEED] = (rss("c", "a", "b"), "application/rss+xml")
    second = await world.run(watch["id"])
    assert second["outcome"] == "changed" and second["changes"] == 1
    assert second["message"] == "「新闻」有 1 条新内容：\n• 标题c https://news.example/c"
    assert (
        second["delivered"] == "preview" and second["detail"] == "有 1 条新内容；试运行，没有真的发出\n标题c"
    )
    assert world.sent() == []  # 试运行 never sends
    third = await world.run(watch["id"])
    assert third["outcome"] == "unchanged" and third["message"] is None and third["delivered"] is None

    await eventually(lambda: len(world.watch_events()) == 3)
    assert world.watch_events()[1] == {"watch_id": watch["id"], "run": second}
    runs = (await world.http.get(f"/api/watches/{watch['id']}/runs")).json()
    assert [r["id"] for r in runs] == [third["id"], second["id"], first["id"]]  # newest first
    assert (await world.http.get(f"/api/watches/{watch['id']}/runs", params={"limit": 1})).json() == [third]
    view = (await world.http.get("/api/watches")).json()[0]
    assert view["last_run"] == third and view["last_message"] == second
    assert view["counts"] == {"runs_7d": 3, "changes_7d": 1, "errors_7d": 0}
    assert view["last_run_at"] == third["started_at"]
    assert view["next_run_at"] == pytest.approx(third["started_at"] + 60 * 60)


async def test_push_sends_to_the_target_and_maps_outcomes(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(mode="push")
    assert (await world.run(watch["id"]))["delivered"] is None  # a baseline pushes nothing
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    run = await world.run(watch["id"])
    assert (
        run["delivered"] == "sent"
        and run["detail"] == "有 1 条新内容；已推送\n标题b"
        and run["error"] is None
    )
    [action] = world.sent()
    assert action.channel == ChannelRef("qq", "10001", "group", "42")
    assert [(s.type, dict(s.data)) for s in action.segments] == [("text", {"text": run["message"]})]

    adapter = world.adapters[WHALE]
    for result, delivered, detail in (
        (ActionResult("failed", error="群里被禁言了"), "failed", "推送失败：群里被禁言了"),
        (ActionResult("unknown", error="超时"), "unknown", "推送结果不确定，没有重发：超时"),
    ):
        adapter.result = result
        world.web.pages[FEED] = (rss(f"new-{delivered}", "b", "a"), "application/rss+xml")
        run = await world.run(watch["id"])
        assert run["delivered"] == delivered and run["detail"].split("\n")[0] == f"有 1 条新内容；{detail}"
        assert run["error"] == result.error
    assert len(world.sent()) == 3  # not at once: a failed push waits for resend_gap

    # A private chat.
    adapter.result = ActionResult("succeeded", message_id="901")
    await world.patch(watch["id"], {"target_kind": "private", "target_id": "10086"})
    world.web.pages[FEED] = (rss("p", "new-unknown", "new-failed", "b", "a"), "application/rss+xml")
    assert (await world.run(watch["id"]))["delivered"] == "sent"
    assert world.sent()[-1].channel == ChannelRef("qq", "10001", "private", "10086")


async def failed_push(world: World, **fields: Any) -> tuple[dict[str, Any], dict[str, Any], Watcher]:
    """A push that QQ refused, and a watcher that may try it again at once."""
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(mode="push", **fields)
    await world.run(watch["id"])
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    world.adapters[WHALE].result = ActionResult("failed", error="QQ 当前不在线")
    run = await world.run(watch["id"])
    assert run["delivered"] == "failed" and len(world.sent()) == 1
    world.adapters[WHALE].result = ActionResult("succeeded", message_id="901")
    await world.sql("UPDATE watches SET enabled = 1")
    watcher = world.state.watcher
    await watcher.stop()  # only these tests' own calls send
    watcher.policy = replace(watcher.policy, resend_gap=0.0)
    return watch, run, watcher


async def test_a_failed_push_goes_out_once_the_bot_can_send(world: World) -> None:
    watch, run, watcher = await failed_push(world)
    await watcher._resend()
    await watcher._resend()
    first, second = world.sent()
    assert second.segments == first.segments and first.idempotency_key != second.idempotency_key
    done = (await world.store.runs(watch["id"]))[0]
    assert done["id"] == run["id"] and done["delivered"] == "sent" and done["error"] is None
    assert done["detail"] == "有 1 条新内容；已补发\n标题b"
    assert await world.store.pushes_since(0.0)


async def test_a_failed_push_waits_for_the_bot_and_is_not_tried_too_soon(world: World) -> None:
    _, _, watcher = await failed_push(world)
    adapter = world.adapters[WHALE]
    adapter.status = lambda: AdapterStatus("reconnecting", "", 0.0)  # type: ignore[method-assign]
    await watcher._resend()
    assert len(world.sent()) == 1
    adapter.status = lambda: AdapterStatus("connected", "", 0.0)  # type: ignore[method-assign]
    watcher.policy = replace(watcher.policy, resend_gap=300.0)
    await watcher._resend()
    assert len(world.sent()) == 1
    watcher.policy = replace(watcher.policy, resend_gap=0.0)
    await watcher._resend()
    assert len(world.sent()) == 2


async def test_resending_stops_after_its_tries_and_when_it_may_have_arrived(world: World) -> None:
    watch, _, watcher = await failed_push(world)
    world.adapters[WHALE].result = ActionResult("failed", error="还是不行")
    watcher.policy = replace(watcher.policy, resend_tries=2)
    for _ in range(4):
        await watcher._resend()
    assert len(world.sent()) == 3  # the push and two tries
    assert (await world.store.runs(watch["id"]))[0]["delivered"] == "failed"

    watcher.policy = replace(watcher.policy, resend_tries=9)
    world.adapters[WHALE].result = ActionResult("unknown", error="超时")
    await watcher._resend()
    await watcher._resend()
    assert len(world.sent()) == 4
    done = (await world.store.runs(watch["id"]))[0]
    assert done["delivered"] == "unknown" and "没有重发" in done["detail"]


async def test_a_failed_push_is_left_alone_when_old_switched_off_or_paused(world: World) -> None:
    _, _, watcher = await failed_push(world)
    await world.sql("UPDATE watches SET mode = 'preview'")
    await watcher._resend()
    await world.sql("UPDATE watches SET mode = 'push', enabled = 0")
    await watcher._resend()
    watcher.policy = replace(watcher.policy, resend_hours=0.0)
    await world.sql("UPDATE watches SET enabled = 1")
    await watcher._resend()
    assert len(world.sent()) == 1


async def test_a_paused_bot_never_sends(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(mode="push", provider_id="ark")
    await world.run(watch["id"])
    assert (await world.http.post("/api/bots/pause-all")).status_code == 200  # 全部暂停
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    run = await world.run(watch["id"])
    assert run["delivered"] == "paused" and run["detail"] == "有 1 条新内容；bot 已暂停，没有推送\n标题b"
    assert run["message"] == "「新闻」有 1 条新内容：\n• 标题b https://news.example/b"  # no model call either
    assert world.sent() == [] and world.models["ark"].requests == []


async def test_the_hourly_push_limit(world: World) -> None:
    world.state.watcher.policy = replace(world.state.watcher.policy, hourly_push_limit=2)
    world.web.pages[FEED] = (rss("0"), "application/rss+xml")
    watch = await world.add(mode="push")
    await world.run(watch["id"])
    world.adapters[WHALE].result = ActionResult("failed", error="不在线")
    world.web.pages[FEED] = (rss("1", "0"), "application/rss+xml")
    assert (await world.run(watch["id"]))["delivered"] == "failed"  # did not go out: does not count
    world.adapters[WHALE].result = ActionResult("succeeded", message_id="1")
    delivered = []
    for i in range(2, 5):
        world.web.pages[FEED] = (rss(*(str(k) for k in range(i, -1, -1))), "application/rss+xml")
        run = await world.run(watch["id"])
        delivered.append(run["delivered"])
    assert delivered == ["sent", "sent", None]
    assert run["detail"].split("\n")[0] == "有 1 条新内容；这个小时推送太多了，先不发"
    assert len(world.sent()) == 3

    # The count survives a restart: it is read back from the runs of the last hour.
    restarted = type(world.state.watcher)(
        world.state.db,
        brain=world.state.brain,
        hub=world.state.hub,
        providers=world.state.providers,
        broadcaster=world.state.broadcaster,
    )
    await restarted.start()
    try:
        assert len(restarted._pushes) == 2
    finally:
        await restarted.stop()


async def test_model_rewrites_the_message(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(provider_id="ark", instructions="结尾加一个鲸鱼表情")
    await world.run(watch["id"])
    model = world.models["ark"]
    assert model.requests == []  # a baseline has nothing to say
    model.queue("  鲸鱼来报：新闻更新了 https://news.example/b  ")
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    run = await world.run(watch["id"])
    assert run["message"] == "鲸鱼来报：新闻更新了 https://news.example/b"
    assert run["detail"] == "有 1 条新内容；试运行，没有真的发出\n标题b"
    [(system, user)] = model.requests
    assert system.role == "system" and system.content == (
        "你是一只爱冒泡的鲸鱼。\n"
        "把下面的更新写成一条发到 QQ 群的简短消息，保留链接；附加要求里没说长度就不超过 150 字。\n"
        "结尾加一个鲸鱼表情"
    )
    assert user.content == "「新闻」有 1 条新内容：\n• 标题b https://news.example/b"
    call = await world.state.db.fetchone("SELECT bot_id, status FROM llm_calls ORDER BY id DESC LIMIT 1")
    assert tuple(call) == (WHALE, "ok")

    # A failed call, an empty answer, a model that did not load: the template, with a note.
    for trouble in (ProviderError("HTTP 500：坏了", retryable=False), "   "):
        model.queue(trouble)
        key = f"x{len(model.requests)}"
        world.web.pages[FEED] = (rss(key, "b", "a"), "application/rss+xml")
        run = await world.run(watch["id"])
        assert run["message"] == f"「新闻」有 1 条新内容：\n• 标题{key} https://news.example/{key}"
        assert (
            run["detail"].split("\n")[0] == "有 1 条新内容；模型没写出来，用了默认格式；试运行，没有真的发出"
        )
    world.state.providers.remove("ark")
    world.web.pages[FEED] = (rss("y", "x2", "x1", "b", "a"), "application/rss+xml")
    run = await world.run(watch["id"])
    assert "模型没写出来，用了默认格式" in run["detail"] and run["message"].startswith("「新闻」")


async def test_model_prompt_for_a_group_without_persona_and_a_private_chat(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    group = await world.add(provider_id="ark", target_id="77")
    private = await world.add(provider_id="ark", target_kind="private", target_id="10086")
    for watch in (group, private):
        await world.run(watch["id"])
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    for watch in (group, private):
        await world.run(watch["id"])
    first, second = (request[0].content for request in world.models["ark"].requests)
    assert first.startswith("你是群里的阿鲸。\n把下面的更新写成一条发到 QQ 群的简短消息")
    assert second.startswith("你是阿鲸。\n把下面的更新写成一条发到 QQ 私聊的简短消息")


async def test_a_long_message_is_capped(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(provider_id="ark")
    await world.run(watch["id"])
    world.models["ark"].queue("长" * 1600)
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    message = (await world.run(watch["id"]))["message"]
    assert len(message) == 1500 and message.endswith("…")


async def test_the_model_reads_the_full_text_of_new_entries(world: World) -> None:
    def issue(*keys: str) -> str:
        items = "".join(
            f"<item><title>早报{key}</title><link>https://news.example/{key}</link><guid>{key}</guid>"
            f"<description>短摘要{key}</description>"
            f"<content:encoded><![CDATA[<h2>要闻{key}</h2><ul><li>第一条{key}</li><li>第二条{key}</li></ul>]]>"
            "</content:encoded></item>"
            for key in keys
        )
        head = '<?xml version="1.0" encoding="utf-8"?><rss version="2.0" '
        head += 'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        return f"{head}<channel><title>t</title>{items}</channel></rss>"

    world.web.pages[FEED] = (issue("a"), "application/rss+xml")
    watch = await world.add(provider_id="ark")
    await world.run(watch["id"])
    world.models["ark"].queue("今天的早报：……")
    world.web.pages[FEED] = (issue("b", "a"), "application/rss+xml")
    run = await world.run(watch["id"])
    [(_, user)] = world.models["ark"].requests
    assert user.content == (
        "「新闻」有 1 条新内容：\n• 早报b https://news.example/b\n\n"
        "新内容的正文，供你概括：\n\n【早报b】\n要闻b\n第一条b\n第二条b"
    )
    assert run["message"] == "今天的早报：……"  # the full text is only for the model


async def test_errors_are_recorded_and_keep_the_state(world: World) -> None:
    watch = await world.add(kind="page", url=PAGE, selector="#news")
    world.web.pages[PAGE] = ('<div id="news">一</div>', "text/html")
    await world.run(watch["id"])
    world.web.pages[PAGE] = ("<div>改版了</div>", "text/html")
    run = await world.run(watch["id"])
    assert (run["outcome"], run["detail"], run["error"], run["delivered"]) == (
        "error",
        "选择器没有找到内容：#news",
        "选择器没有找到内容：#news",
        None,
    )
    del world.web.pages[PAGE]
    assert (await world.run(watch["id"]))["error"] == "网站返回了 HTTP 404"
    world.web.pages[PAGE] = ('<div id="news">一</div>', "text/html")
    assert (await world.run(watch["id"]))["outcome"] == "unchanged"  # the remembered text survived
    counts = (await world.http.get("/api/watches")).json()[0]["counts"]
    assert counts == {"runs_7d": 4, "changes_7d": 0, "errors_7d": 2}


async def test_the_proxy(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(use_proxy=True)
    run = await world.run(watch["id"])
    assert run["outcome"] == "error" and run["error"] == "还没有在「系统」页设置外网代理"
    assert world.web.requests == []
    response = await world.http.patch("/api/system/settings", json={"fetch_proxy": "http://127.0.0.1:7890"})
    assert response.status_code == 200
    proxies = []
    real = world.state.watcher.fetch

    async def spy(url: str, **kwargs: Any) -> Any:
        proxies.append(kwargs["proxy"])
        return await real(url, **kwargs)

    world.state.watcher.fetch = spy
    assert (await world.run(watch["id"]))["outcome"] == "baseline"
    await world.patch(watch["id"], {"use_proxy": False})
    await world.run(watch["id"])
    assert proxies == ["http://127.0.0.1:7890", None]


async def test_changing_the_source_starts_a_new_baseline(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    other = "https://other.example/rss"
    world.web.pages[other] = (rss("z"), "application/rss+xml")
    watch = await world.add()
    await world.run(watch["id"])
    await world.patch(watch["id"], {"name": "改名", "interval_minutes": 60, "mode": "push"})
    assert (await world.store.get(watch["id"])).state != {}  # name, mode: the state stays
    await world.patch(watch["id"], {"url": other})
    assert (await world.store.get(watch["id"])).state == {}
    run = await world.run(watch["id"])
    assert run["outcome"] == "baseline" and world.sent() == []
    await world.patch(watch["id"], {"kind": "page"})
    assert (await world.store.get(watch["id"])).state == {}


async def test_enabling_or_a_new_interval_checks_soon(world: World) -> None:
    watch = await world.add()
    await world.sql("UPDATE watches SET next_run_at = 0 WHERE id = ?", watch["id"])
    renamed = (await world.patch(watch["id"], {"name": "x"})).json()
    assert renamed["next_run_at"] == 0
    before = time.time()
    world.state.watcher.wake = lambda: None  # keep the scheduler out of it
    enabled = (await world.patch(watch["id"], {"enabled": True})).json()
    assert enabled["next_run_at"] >= before
    await world.sql("UPDATE watches SET next_run_at = 0 WHERE id = ?", watch["id"])
    assert (await world.patch(watch["id"], {"enabled": True})).json()["next_run_at"] == 0  # already on
    assert (await world.patch(watch["id"], {"interval_minutes": 15})).json()["next_run_at"] >= before


async def test_an_edit_during_a_check_voids_it(world: World) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> tuple[str, str]:
        await gate.wait()
        return rss("a"), "application/rss+xml"

    world.web.pages[FEED] = slow
    watch = await world.add()
    task = asyncio.create_task(world.run(watch["id"]))
    await eventually(lambda: world.web.requests)
    assert (await world.http.post(f"/api/watches/{watch['id']}/run")).status_code == 409
    assert (await world.http.post(f"/api/watches/{watch['id']}/run")).json() == {"detail": "正在检查，稍等"}
    assert (await world.http.get("/api/watches")).json()[0]["running"] is True
    await world.patch(watch["id"], {"url": "https://other.example/rss"})
    gate.set()
    run = await task
    assert (
        run["outcome"] == "error"
        and run["error"] == "检查的时候地址、类型或选择器被改了，这次结果不算，下次重新开始"
    )
    assert (await world.store.get(watch["id"])).state == {}  # still a fresh baseline next time


async def test_a_watch_deleted_during_a_check(world: World) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> tuple[str, str]:
        await gate.wait()
        return rss("a"), "application/rss+xml"

    world.web.pages[FEED] = slow
    watch = await world.add()
    task = asyncio.create_task(world.http.post(f"/api/watches/{watch['id']}/run"))
    await eventually(lambda: world.web.requests)
    assert (await world.http.delete(f"/api/watches/{watch['id']}")).status_code == 200
    gate.set()
    response = await task
    assert response.status_code == 404
    assert (await world.state.db.fetchone("SELECT count(*) FROM watch_runs"))[0] == 0


async def test_retention_purges_old_runs(world: World) -> None:
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add()
    await world.run(watch["id"])
    await world.run(watch["id"])
    old = time.time() - 31 * 86400
    await world.sql("UPDATE watch_runs SET started_at = ? WHERE id = (SELECT min(id) FROM watch_runs)", old)
    await world.state.hub.purge(time.time())
    assert len(await world.store.runs(watch["id"])) == 1


# -- the schedule -----------------------------------------------------------------------------------------


async def test_due_watches_run_and_others_wait(tmp_path: Path) -> None:
    async with running(tmp_path, WatchPolicy(tick=0.05, startup_delay=0)) as world:
        world.web.pages[FEED] = (rss("a"), "application/rss+xml")
        due = await world.add(name="到时间")
        later = await world.add(name="还没到", url="https://later.example/rss")
        enable = "UPDATE watches SET enabled = 1, next_run_at = ? WHERE id = ?"
        await world.sql(enable, time.time() + 3600, later["id"])
        await world.sql(enable, time.time() - 1, due["id"])
        world.state.watcher.wake()
        await eventually(lambda: world.watch_events())
        await asyncio.sleep(0.3)  # several ticks
        assert [e["watch_id"] for e in world.watch_events()] == [due["id"]]  # once: the next is in an hour
        assert "https://later.example/rss" not in world.web.requests
        disabled = await world.add(name="关着")
        await world.sql("UPDATE watches SET next_run_at = 0 WHERE id = ?", disabled["id"])
        await world.sql("UPDATE watches SET next_run_at = ? WHERE id = ?", time.time() - 1, due["id"])
        world.state.watcher.wake()
        await eventually(lambda: len(world.watch_events()) == 2)
        await asyncio.sleep(0.2)
        assert [e["watch_id"] for e in world.watch_events()] == [due["id"], due["id"]]


async def test_overdue_watches_run_once_staggered_after_a_restart(tmp_path: Path) -> None:
    await seed(tmp_path)
    db = await Database.open(tmp_path / "data" / "nest.db")
    store = WatchStore(db)
    ids = []
    for i in range(3):
        ids.append(
            await store.create(
                _body(name=f"w{i}", url=f"https://n{i}.example/rss", enabled=True, interval_minutes=5)
                | {"next_run_at": time.time() - 86400 * (i + 1), "state": {}}  # missed many checks
            )
        )
    await db.close()
    restarted = time.time()
    async with running(tmp_path, WatchPolicy(tick=0.05, startup_delay=0.1, stagger=0.3)) as world:
        for i in range(3):
            world.web.pages[f"https://n{i}.example/rss"] = (rss("a"), "application/rss+xml")
        await eventually(lambda: len(world.watch_events()) == 3, timeout=5)
        await asyncio.sleep(0.4)
        events = world.watch_events()
        assert len(events) == 3  # no backlog of missed checks
        started = sorted(e["run"]["started_at"] for e in events)
        # None starts before its slot. Not the gaps between starts: a busy loop can start one late, close to
        # the next (it failed that way on the Windows CI runner).
        assert all(at >= restarted + 0.1 + i * 0.3 for i, at in enumerate(started))
        assert {e["watch_id"] for e in events} == set(ids)


async def test_at_most_two_at_once_and_never_one_watch_twice(tmp_path: Path) -> None:
    async with running(tmp_path, WatchPolicy(tick=0.02, startup_delay=0)) as world:
        gate = asyncio.Event()

        async def slow(request: httpx.Request) -> tuple[str, str]:
            await gate.wait()
            return rss("a"), "application/rss+xml"

        for i in range(3):
            world.web.pages[f"https://n{i}.example/rss"] = slow
            await world.add(name=f"w{i}", url=f"https://n{i}.example/rss", enabled=True)
        await eventually(lambda: world.web.active == 2)
        await asyncio.sleep(0.2)  # many ticks: still two, and nobody started twice
        assert world.web.active == 2 and len(world.web.requests) == 2
        busy = (await world.http.get("/api/watches")).json()
        running_ids = [w["id"] for w in busy if w["running"]]
        assert len(running_ids) == 2
        response = await world.http.post(f"/api/watches/{running_ids[0]}/run")
        assert response.status_code == 409
        gate.set()
        await eventually(lambda: len(world.watch_events()) == 3)
        assert world.web.most_active == 2 and len(world.web.requests) == 3


async def test_shutdown_cancels_checks_cleanly(tmp_path: Path) -> None:
    async def never(request: httpx.Request) -> Any:
        await asyncio.sleep(3600)

    context = running(tmp_path, WatchPolicy(tick=0.02, startup_delay=0))
    world = await context.__aenter__()
    world.web.pages[FEED] = never
    await world.add(enabled=True)
    await eventually(lambda: world.web.active == 1)
    started = time.monotonic()
    await context.__aexit__(None, None, None)
    assert time.monotonic() - started < 3
    db = await Database.open(tmp_path / "data" / "nest.db")
    try:
        assert (await db.fetchone("SELECT count(*) FROM watch_runs"))[0] == 0  # nothing half-written
        row = await db.fetchone("SELECT state, last_run_at FROM watches")
        assert row["state"] == "{}" and row["last_run_at"] is not None
    finally:
        await db.close()


async def test_shutdown_during_a_send_leaves_an_honest_record(tmp_path: Path) -> None:
    context = running(tmp_path, WatchPolicy(tick=0.02, startup_delay=0))
    world = await context.__aenter__()
    world.web.pages[FEED] = (rss("a"), "application/rss+xml")
    watch = await world.add(mode="push")
    await world.run(watch["id"])
    world.adapters[WHALE].delay = 3600
    world.web.pages[FEED] = (rss("b", "a"), "application/rss+xml")
    await world.patch(watch["id"], {"enabled": True})
    await eventually(lambda: world.sent())
    await context.__aexit__(None, None, None)
    db = await Database.open(tmp_path / "data" / "nest.db")
    try:
        row = await db.fetchone("SELECT delivered, detail FROM watch_runs ORDER BY id DESC LIMIT 1")
        assert tuple(row) == ("unknown", "有 1 条新内容；推送时 nest 停止了，不确定是否已发出\n标题b")
    finally:
        await db.close()
