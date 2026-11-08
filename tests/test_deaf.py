"""假在线: an account that stays connected but stops getting messages is found by comparing what our accounts
stored of a shared group, or by the send failure that comes with it. Quiet hours, small groups, new
connections and groups it left are not mistaken for it; only a message that arrives live clears it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nest.adapters.base import AdapterStatus
from nest.core.deaf import MISSED, QUIET, SETTLE, WINDOW, DeafWatch
from nest.core.events import ChannelInfo, ChannelRef, MessageEvent, Segment
from nest.core.hub import Hub
from nest.core.offline_alert import OfflineAlert
from nest.storage.accounts import Account
from nest.storage.db import Database
from nest.storage.messages import MessageStore

WHALE, DRAGON = "qq:10001", "qq:20002"
START = 1_000_000.0  # both connected long before anything here happens


class FakeAdapter:
    capabilities = frozenset({"list_channels"})

    def __init__(self, self_id: str) -> None:
        self.self_id = self_id
        self.state = "connected"
        self.since = START - 86400
        self.stuck_send_at = 0.0
        self.groups: list[str] | None = ["42"]  # None: QQ does not answer

    def status(self) -> AdapterStatus:
        return AdapterStatus(self.state, "", self.since)  # type: ignore[arg-type]

    async def list_channels(self) -> list[ChannelInfo]:
        if self.groups is None:
            raise ConnectionError("连不上")
        return [ChannelInfo(ChannelRef("qq", self.self_id, "group", g), "") for g in self.groups]


class Events:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        self.seen.append((kind, data))


@dataclass
class World:
    store: MessageStore
    hub: Hub
    events: Events
    watch: DeafWatch
    now: list[float]
    count: list[int] = field(default_factory=lambda: [0])

    def adapter(self, account: str) -> FakeAdapter:
        return self.hub.adapters[account]  # type: ignore[return-value]

    def state(self, account: str) -> str:
        return next(v for v in self.hub.account_views() if v["id"] == account)["status"]["state"]

    async def said(
        self,
        accounts: tuple[str, ...],
        *,
        at: float | None = None,
        sender: str = "555",
        group: str = "42",
        kind: str = "group",
        backfilled: bool = False,
    ) -> None:
        """One message as each of `accounts` stored it."""
        self.count[0] += 1
        for account in accounts:
            event = MessageEvent(
                channel=ChannelRef("qq", account.removeprefix("qq:"), kind, group),  # type: ignore[arg-type]
                message_id=str(self.count[0]),
                sender_id=sender,
                sender_name="群友",
                segments=(Segment("text", {"text": "hi"}),),
                text="hi",
                timestamp=self.now[0] if at is None else at,
                backfilled=backfilled,
            )
            await self.store.save(event)

    async def chatter(self, accounts: tuple[str, ...], count: int, *, sender: str = "555") -> None:
        for _ in range(count):
            self.now[0] += 5
            await self.said(accounts, sender=sender)


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    db = await Database.open(tmp_path / "nest.db")
    store = MessageStore(db)
    events = Events()
    hub = Hub(db, store, None, events)  # type: ignore[arg-type]
    hub.brain = SimpleNamespace(bots={})  # type: ignore[assignment]
    for account, name in ((WHALE, "阿鱼"), (DRAGON, "阿龙")):
        self_id = account.removeprefix("qq:")
        hub.accounts[account] = Account(account, "qq", self_id, name, "http://x", "ws://x/", "", "", True)
        hub.adapters[account] = FakeAdapter(self_id)  # type: ignore[assignment]
        async with db.transaction() as conn:
            await conn.execute(
                """INSERT INTO accounts (id, platform, self_id, name, http_url, ws_url,
                                         created_at, updated_at)
                   VALUES (?, 'qq', ?, ?, 'http://x', 'ws://x/', 0, 0)""",
                (account, self_id, name),
            )
        await store.channel_id(ChannelRef("qq", self_id, "group", "42"))
    now = [START]
    try:
        yield World(store, hub, events, DeafWatch(hub, store, clock=lambda: now[0]), now)
    finally:
        await db.close()


async def test_missing_a_shared_groups_messages_until_one_arrives_live(world: World) -> None:
    both = (WHALE, DRAGON)
    await world.chatter(both, 3)
    await world.chatter((WHALE,), MISSED - 1)
    assert await world.watch.check() == []  # could still be chance
    await world.chatter((WHALE,), 1)
    assert await world.watch.check() == [DRAGON]
    assert world.state(DRAGON) == "deaf" and world.state(WHALE) == "connected"
    view = next(v for v in world.hub.account_views() if v["id"] == DRAGON)
    assert view["status"]["detail"] == f"收不到消息：「群 42」里别的 bot 收到了 {MISSED} 条，它一条也没收到"
    assert world.events.seen[-1] == ("status", {"account_id": DRAGON, **view["status"]})
    assert await world.watch.check() == []  # marked once

    # History is still served to a client in this state: fetched messages say nothing.
    world.now[0] += 60
    await world.said((DRAGON,), backfilled=True)
    await world.watch.check()
    assert world.state(DRAGON) == "deaf"

    # A reconnection neither: it stays marked, and shows while connected.
    world.adapter(DRAGON).state = "reconnecting"
    assert world.state(DRAGON) == "reconnecting"
    world.adapter(DRAGON).state = "connected"
    assert world.state(DRAGON) == "deaf"

    world.now[0] += 60
    await world.said(both)
    await world.watch.check()
    assert world.state(DRAGON) == "connected"
    assert world.events.seen[-1][1]["state"] == "connected"


async def test_quiet_and_small_groups_and_a_slow_trickle_are_not_it(world: World) -> None:
    for _ in range(20):  # a message a minute it did get, then nothing at all for hours
        world.now[0] += 60
        await world.said((WHALE, DRAGON))
    world.now[0] += 6 * 3600
    assert await world.watch.check() == []
    await world.chatter((WHALE,), 3)  # just now: too few to say
    assert await world.watch.check() == []
    for _ in range(MISSED):  # as many, but spread over more than the window
        world.now[0] += WINDOW / 4
        await world.said((WHALE,))
    assert await world.watch.check() == []


async def test_what_it_got_elsewhere_counts_and_its_own_messages_do_not(world: World) -> None:
    await world.chatter((WHALE,), MISSED - 2)
    await world.said((DRAGON,), group="900000009", kind="private")  # a private message got through
    await world.chatter((WHALE,), MISSED - 2)
    assert await world.watch.check() == []

    await world.chatter((WHALE,), MISSED, sender="20002")  # it talking: the others see it, it is not sent it
    assert await world.watch.check() == []


async def test_a_new_connection_gets_time_to_settle(world: World) -> None:
    world.adapter(DRAGON).since = world.now[0]
    await world.chatter((WHALE,), MISSED)  # e.g. fetched by the other account after a restart
    assert await world.watch.check() == []
    world.now[0] += SETTLE + 1
    assert await world.watch.check() == []  # those came before it had settled
    await world.chatter((WHALE,), MISSED)
    assert await world.watch.check() == [DRAGON]


async def test_a_group_it_left_is_not_it(world: World) -> None:
    world.adapter(DRAGON).groups = ["77"]
    await world.chatter((WHALE,), MISSED + 2)
    assert await world.watch.check() == []

    world.adapter(DRAGON).groups = None  # QQ does not answer: as far as nest knows
    assert await world.watch.check() == [DRAGON]


async def test_a_stuck_send_with_nothing_received(world: World) -> None:
    adapter = world.adapter(DRAGON)
    await world.said((DRAGON,))
    world.now[0] += QUIET / 2
    adapter.stuck_send_at = world.now[0]
    assert await world.watch.check() == []  # it got something moments ago
    world.now[0] += QUIET / 2 + 1
    assert await world.watch.check() == [DRAGON]  # no group to compare with needed
    assert "sendMsg" in world.hub.deaf[DRAGON].detail

    world.now[0] += 60
    await world.said((DRAGON,))
    await world.watch.check()
    assert world.state(DRAGON) == "connected"
    world.now[0] += QUIET + 1
    assert await world.watch.check() == []  # it worked after that failure
    world.now[0] += WINDOW  # and once the message is too old to see, so is the failure
    assert await world.watch.check() == []


async def test_stopping_it_on_purpose_ends_the_mark(world: World) -> None:
    await world.chatter((WHALE,), MISSED)
    await world.watch.check()
    world.hub.held[DRAGON] = "QQ 容器已停止"
    await world.watch.check()
    assert DRAGON not in world.hub.deaf
    del world.hub.held[DRAGON]
    assert world.state(DRAGON) == "connected"


async def test_the_alert_and_its_return_come_from_another_bot(world: World) -> None:
    sent: list[tuple[str, str, str]] = []
    refused: set[str] = set()

    async def send(account: str, user: str, text: str) -> bool:
        if account in refused:
            return False
        sent.append((account, user, text))
        return True

    async def read() -> tuple[str, int]:
        return "123456789", 5

    alert = OfflineAlert(world.hub, send, read, clock=lambda: world.now[0])
    await world.chatter((WHALE,), MISSED)
    await world.watch.check()
    refused.add(WHALE)
    assert await alert.check() == []  # only it could send: tried again
    refused.clear()
    [text] = await alert.check()
    assert sent == [(WHALE, "123456789", text)]  # never the account that is deaf itself
    assert text.startswith("nest 提醒：阿龙（20002）显示在线，其实是假在线。收不到消息：")
    world.now[0] += 3600
    assert await alert.check() == []  # told once, and not as a bot that is down

    await world.said((WHALE, DRAGON))
    await world.watch.check()
    [back] = await alert.check()
    assert back == "nest 提醒：阿龙（20002）又能收到消息了，从发现到现在大约 60 分钟。"
    assert sent[-1][0] == DRAGON  # the one that came back says so itself
    world.now[0] += 3600
    assert await alert.check() == []


async def test_down_and_back_while_marked_is_said_once_each(world: World) -> None:
    sent: list[str] = []

    async def send(account: str, user: str, text: str) -> bool:
        sent.append(text)
        return True

    async def read() -> tuple[str, int]:
        return "123456789", 1

    alert = OfflineAlert(world.hub, send, read, clock=lambda: world.now[0])
    await world.chatter((WHALE,), MISSED)
    await world.watch.check()
    await alert.check()
    world.adapter(DRAGON).state = "reconnecting"
    await alert.check()
    world.now[0] += 120
    await alert.check()
    world.adapter(DRAGON).state = "connected"
    await alert.check()
    assert ["假在线" in t for t in sent] == [True, False, False]
    assert "掉线 2 分钟" in sent[1] and "又连上了" in sent[2]  # connected again, still not hearing anything


async def test_the_account_shows_the_bots_new_name(world: World) -> None:
    views = {v["id"]: v["name"] for v in world.hub.account_views()}
    assert views[WHALE] == "阿鱼"
    bot = SimpleNamespace(account_id=WHALE, name="新名字")
    world.hub.brain.bots = {WHALE: bot}  # type: ignore[union-attr]
    views = {v["id"]: v["name"] for v in world.hub.account_views()}
    assert views == {WHALE: "新名字", DRAGON: "阿龙"}  # no bot for it: the account's own
