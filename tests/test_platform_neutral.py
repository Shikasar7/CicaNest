"""What holds on any platform: a QQ number never reaches a Discord bot, a platform that cannot do something is
never asked to, and one that sees only what calls it or never echoes its own messages still behaves."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nest.adapters.base import AdapterStatus, MediaError, parse_recipient
from nest.adapters.onebot11.adapter import OneBotAdapter
from nest.adapters.registry import PLATFORMS, check_recipient, check_user
from nest.api.routes_models import _account_refs
from nest.core.brain import Draft, _opening
from nest.core.broadcast import Broadcaster
from nest.core.events import ActionResult, ChannelInfo, ChannelRef, ChannelsSeen, MessageEvent, Segment
from nest.core.hub import Hub
from nest.core.offline_alert import OfflineAlert
from nest.core.screenshots import ScreenshotReader
from nest.core.stickers import StickerError, StickerKeeper
from nest.storage.accounts import get_account, list_accounts, update_config, upsert_account
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.watch import fetch as watch_fetch
from tests.test_brain import World, world  # noqa: F401
from tests.test_sending import FakeAdapter, go_live


def without(monkeypatch: pytest.MonkeyPatch, *capabilities: str) -> None:
    """QQ as a platform that cannot do these."""
    spec = PLATFORMS["qq"]
    less = dataclasses.replace(spec, capabilities=spec.capabilities - set(capabilities))
    monkeypatch.setitem(PLATFORMS, "qq", less)


# -- accounts ----------------------------------------------------------------------------------------------


async def test_an_account_keeps_its_settings_and_a_qq_account_needs_none(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    try:
        qq = await upsert_account(
            db, platform="qq", self_id="1", name="q", http_url="http://x", ws_url="ws://x"
        )
        assert qq.config == {} and qq.refs == {}
        config = {"proxy": "http://127.0.0.1:7890", "refs": {"token": "NEST_DISCORD_2_TOKEN"}}
        discord = await upsert_account(
            db, platform="discord", self_id="2", name="d", http_url="", ws_url="", config=config
        )
        assert discord.config == config and discord.refs == {"token": "NEST_DISCORD_2_TOKEN"}
        await update_config(db, discord.id, {**config, "proxy": ""})
        saved = await get_account(db, discord.id)
        assert saved is not None and saved.config["proxy"] == "" and saved.refs == discord.refs
        assert [a.platform for a in await list_accounts(db)] == ["qq", "discord"]
        # A model must not take a name an account's secret lives under.
        assert "NEST_DISCORD_2_TOKEN" in await _account_refs(SimpleNamespace(db=db))
    finally:
        await db.close()


# -- who is who --------------------------------------------------------------------------------------------


async def test_a_bot_only_lists_peers_of_its_own_platform(world: World) -> None:  # noqa: F811
    for self_id, name in (("900", "洋鲸"), ("901", "洋马")):
        await upsert_account(world.db, platform="discord", self_id=self_id, name="", http_url="", ws_url="")
        await world.repo.upsert_bot(account_id=f"discord:{self_id}", name=name, aliases=[], provider_id=None)
    await world.brain.load()
    bots = world.brain.bots
    assert world.brain.peers_of(bots["discord:900"]) == {"901": "洋马"}
    assert set(world.brain.peers_of(bots["qq:10001"])) == {"10002", "10003"}


def test_a_bare_number_is_qq_and_others_say_their_platform() -> None:
    assert parse_recipient("123456") == ("qq", "123456")
    assert parse_recipient("qq:123456") == ("qq", "123456")
    assert parse_recipient("discord:123") == ("discord", "123")
    assert check_recipient(" 123456 ") == "123456" and check_recipient("") == ""
    assert check_recipient("qq:123456") == "qq:123456"
    for bad in ("abc", "qq:12", "nobody:1"):
        with pytest.raises(ValueError):
            check_recipient(bad)
    assert check_user("qq", " 123456 ") == "123456"
    with pytest.raises(ValueError, match="QQ 号要填"):
        check_user("qq", "12a45")


async def test_a_private_message_never_crosses_platforms(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    assert await world.brain.send_private("qq:10001", "123456", "嗨")
    assert await world.brain.send_private("qq:10001", "qq:123456", "嗨")
    assert [a.channel.id for a in adapter.actions] == ["123456", "123456"]
    assert not await world.brain.send_private("qq:10001", "discord:9", "嗨")
    assert len(adapter.actions) == 2


class Online(FakeAdapter):
    def status(self) -> AdapterStatus:
        return AdapterStatus("connected")


async def test_forwarding_finds_a_bot_on_the_platform_of_the_person(world: World) -> None:  # noqa: F811
    asked, other = Online(), Online()
    world.brain.adapters = {"qq:10001": asked, "qq:10002": other}  # type: ignore[dict-item]
    assert await world.brain.forward("qq:10001", "123456", "问题")
    assert len(asked.actions) == 1 and not other.actions
    asked.result = ActionResult("failed", error="不让发")  # the one asked cannot: another may
    assert await world.brain.forward("qq:10001", "123456", "问题")
    assert len(other.actions) == 1
    assert not await world.brain.forward("qq:10001", "discord:9", "问题")  # nobody there
    assert len(asked.actions) == 2 and len(other.actions) == 1


def views(states: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "id": account,
            "self_id": account.partition(":")[2],
            "name": "",
            "enabled": True,
            "status": {"state": state, "detail": "" if state == "connected" else "断了"},
        }
        for account, state in states.items()
    ]


async def alerts(states: dict[str, str], to: str) -> list[tuple[str, str, str]]:
    """What is sent once the accounts in `states` have been in that state for two minutes."""
    sent: list[tuple[str, str, str]] = []

    async def send(account: str, user: str, text: str) -> bool:
        sent.append((account, user, text))
        return True

    async def settings() -> tuple[str, int]:
        return to, 1

    now = [0.0]
    alert = OfflineAlert(
        SimpleNamespace(account_views=lambda: views(states)), send, settings, clock=lambda: now[0]
    )
    await alert.check()
    now[0] = 120
    await alert.check()
    return sent


async def test_an_alert_goes_only_through_the_platform_of_the_person() -> None:
    down = {"qq:1": "reconnecting", "discord:2": "connected", "qq:3": "connected"}
    [(account, user, _)] = await alerts(down, "123456")
    assert (account, user) == ("qq:3", "123456")  # the Discord bot is not asked to DM a QQ number
    [(account, user, _)] = await alerts(down, "discord:123456789012345678")
    assert (account, user) == ("discord:2", "discord:123456789012345678")


async def test_the_advice_to_scan_a_code_is_for_qq_only() -> None:
    [(_, _, qq)] = await alerts({"qq:1": "reconnecting", "qq:3": "connected"}, "123456")
    assert "扫码登录" in qq
    [(_, _, other)] = await alerts({"discord:1": "reconnecting", "qq:3": "connected"}, "123456")
    assert "扫码登录" not in other


# -- what a platform can do --------------------------------------------------------------------------------


def test_an_opening_leaves_out_what_the_platform_cannot_do() -> None:
    known = {"external_id": "5", "sender_id": "7"}
    missed = {"external_id": "", "sender_id": "7"}
    assert _opening(Draft("would_reply", target=known, quote=True)) == [Segment("reply", {"id": "5"})]
    assert _opening(Draft("would_reply", target=known, quote=True), ("mention",)) == []
    assert _opening(Draft("would_reply", target=missed), ("reply",)) == []


async def test_a_reply_says_which_message_it_answers(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    world.providers["10001"].queue("来了")
    mid = await world.say("阿鲸在吗", accounts=["10001"])
    await world.settle(1)
    assert [a.trigger for a in adapter.actions] == [mid]


async def hear(world: World, sender: str, from_bot: bool, mid: str) -> None:  # noqa: F811
    event = MessageEvent(
        channel=ChannelRef("qq", "10001", "group", "42"),
        message_id=mid,
        sender_id=sender,
        sender_name="别家 bot",
        segments=(Segment("text", {"text": "阿鲸你好"}),),
        text="阿鲸你好",
        timestamp=time.time(),
        from_bot=from_bot,
    )
    row = await world.store.save(event)
    assert row is not None
    await world.brain.on_message(event, row)


async def test_another_programs_bot_is_context_and_never_a_trigger(world: World) -> None:  # noqa: F811
    await hear(world, "777", True, "b1")
    await asyncio.sleep(0.2)
    assert await world.decisions() == [] and not world.providers["10001"].requests
    [channel] = world.brain.bots["qq:10001"].channels
    assert len(await world.store.recent(channel)) == 1  # kept all the same
    world.providers["10001"].queue("嗯")
    await hear(world, "888", False, "b2")  # a person says the same
    [decision] = await world.settle(1)
    assert decision["bot_id"] == "qq:10001"


async def test_one_of_our_own_bots_on_such_a_platform_stays_one_of_ours(world: World) -> None:  # noqa: F811
    world.providers["10001"].queue("嗯")
    await hear(world, "10002", True, "b1")  # it called 阿鲸 by name: our bots may, up to the chain limit
    [decision] = await world.settle(1)
    assert decision["bot_id"] == "qq:10001"


async def test_no_echo_the_adapters_own_event_still_counts_as_ours(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    adapter.ours.add("self:1")
    event = MessageEvent(
        channel=ChannelRef("qq", "10001", "group", "42"),
        message_id="self:1",
        sender_id="10001",
        sender_name="阿鲸",
        segments=(Segment("text", {"text": "我说的"}),),
        text="我说的",
        timestamp=time.time(),
        from_self=True,
    )
    row = await world.store.save(event)
    assert row is not None
    await world.brain.on_message(event, row)
    assert "qq:10001" not in world.brain.foreign_at
    await asyncio.sleep(0.6)  # past the cooldown its own message started
    world.providers["10001"].queue("嗯")
    await world.say("回你", reply_to="self:1", accounts=["10001"])  # replying to the bot calls it
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied"


async def test_no_voice_where_the_platform_has_none(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = world.brain.bots["qq:10001"]
    channel = dataclasses.replace(next(iter(bot.channels.values())), voice_mode="on")
    world.brain.speech = SimpleNamespace(ready=lambda voice: True)  # type: ignore[assignment]
    assert world.brain._can_speak(bot, channel)
    without(monkeypatch, "send_voice")
    assert not world.brain._can_speak(bot, channel)


def test_a_platform_without_sticker_source_collects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    keeper = StickerKeeper.__new__(StickerKeeper)
    keeper.features = {"qq:group:1": SimpleNamespace(collect_stickers=True)}  # type: ignore[dict-item]
    assert keeper.collecting("qq:group:1")
    without(monkeypatch, "sticker_source")
    assert not keeper.collecting("qq:group:1")


# -- pictures go through the account -----------------------------------------------------------------------


class Fetcher:
    def __init__(self) -> None:
        self.data: bytes | Exception = b"png"
        self.asked: list[tuple[str, int]] = []

    async def fetch_media(self, url: str, limit: int) -> bytes:
        self.asked.append((url, limit))
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


async def test_screenshots_download_through_the_account_that_saw_them() -> None:
    fetcher = Fetcher()
    reader = ScreenshotReader(lambda: None, adapters={"discord:9": fetcher})  # type: ignore[dict-item]
    assert await reader._download("https://cdn/a.png", "discord:9") == b"png"
    assert fetcher.asked[0][0] == "https://cdn/a.png"
    fetcher.data = MediaError("下载失败（HTTP 404）")
    with pytest.raises(ValueError, match="404"):
        await reader._download("https://cdn/a.png", "discord:9")


async def test_stickers_download_through_the_account_that_saw_them(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    keeper = StickerKeeper(db, SimpleNamespace(), Broadcaster(), tmp_path / "s")  # type: ignore[arg-type]
    try:
        fetcher = Fetcher()
        keeper.adapters = {"qq:1": fetcher}  # type: ignore[dict-item]
        assert await keeper._download("https://cdn/a.png", "qq:1") == b"png"
        assert fetcher.asked == [("https://cdn/a.png", keeper.config.file_max_bytes)]
        fetcher.data = MediaError("超过 5 MB，不收")
        with pytest.raises(StickerError, match="不收"):
            await keeper._download("https://cdn/a.png", "qq:1")
    finally:
        await db.close()


async def test_a_qq_adapter_fetches_pictures_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 10))
    monkeypatch.setattr(watch_fetch, "network_transport", lambda proxy: transport)
    adapter = OneBotAdapter(account="1", http_url="http://x", ws_url="ws://x")
    assert await adapter.fetch_media("https://cdn/a.png", 100) == b"x" * 10
    with pytest.raises(MediaError):
        await adapter.fetch_media("https://cdn/a.png", 5)
    await adapter.stop()


# -- the hub -----------------------------------------------------------------------------------------------


async def test_the_hub_does_not_ask_a_platform_without_a_group_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await Database.open(tmp_path / "nest.db")
    hub = Hub(db, MessageStore(db), None, Broadcaster())  # type: ignore[arg-type]
    asked: list[int] = []

    async def list_channels() -> list[ChannelInfo]:
        asked.append(1)
        return []

    hub.adapters["qq:1"] = SimpleNamespace(list_channels=list_channels)  # type: ignore[assignment]
    await hub._refresh_channels("qq:1")
    assert asked == [1]
    without(monkeypatch, "list_channels")
    await hub._refresh_channels("qq:1")
    assert asked == [1]
    await db.close()


async def test_channels_named_after_connecting_reach_storage(tmp_path: Path) -> None:
    db = await Database.open(tmp_path / "nest.db")
    await upsert_account(db, platform="qq", self_id="1", name="", http_url="", ws_url="", enabled=False)
    store = MessageStore(db)
    hub = Hub(db, store, None, Broadcaster())  # type: ignore[arg-type]
    await hub.start()
    await hub._queue.put(ChannelsSeen((ChannelInfo(ChannelRef("qq", "1", "group", "5"), "服务器 / #闲聊"),)))
    await hub._queue.join()
    assert [c["name"] for c in await store.channels()] == ["服务器 / #闲聊"]
    await hub.stop()
    await db.close()


async def test_a_picture_of_an_account_that_went_away_is_fetched_directly() -> None:
    cdn = httpx.MockTransport(lambda request: httpx.Response(200, content=b"p"))
    reader = ScreenshotReader(lambda: None, transport=cdn)
    assert await reader._download("https://cdn/a.png", "qq:gone") == b"p"
