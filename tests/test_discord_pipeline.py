"""A Discord account through the hub: built from the database, a message stored once whatever comes back, the
bot's own line in its context, channels named, nothing said without being switched on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from nest.adapters.base import Adapter, BackfillSource, StatusSink
from nest.adapters.discord.adapter import DiscordAdapter
from nest.adapters.registry import PLATFORMS, build_adapter, place_of
from nest.core.broadcast import Broadcaster
from nest.core.events import ChannelRef, Segment, SendMessage
from nest.core.hub import Hub
from nest.secrets import SecretStore
from nest.storage.accounts import Account, upsert_account
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.discord_gateway import FakeDiscord
from tests.helpers import eventually

BOT = "900000000000000001"
ACCOUNT = f"discord:{BOT}"
GROUP = ChannelRef("discord", BOT, "group", "11")


@dataclass
class World:
    discord: FakeDiscord
    hub: Hub
    store: MessageStore

    @property
    def adapter(self) -> Adapter:
        return self.hub.adapters[ACCOUNT]

    async def rows(self) -> list[dict]:
        channel = await self.store.channel_id(GROUP)
        return await self.store.recent(channel)


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    db = await Database.open(tmp_path / "nest.db")
    secrets = SecretStore(tmp_path / ".env")
    store = MessageStore(db)
    refs = PLATFORMS["discord"].refs(BOT)
    async with FakeDiscord(bot_id=BOT) as discord:
        secrets.set(refs["token"], discord.token)
        discord.guilds = [discord.guild()]
        await upsert_account(
            db,
            platform="discord",
            self_id=BOT,
            name="洋鲸",
            http_url="",
            ws_url="",
            config={"proxy": "", "refs": refs},
        )

        def factory(
            account: Account, reader: SecretStore, on_status: StatusSink, backfill: BackfillSource
        ) -> Adapter:
            return DiscordAdapter(
                account=account.self_id,
                token=reader.get(account.refs["token"]),
                api_base=discord.api_base,
                on_status=on_status,
                backfill_source=backfill,
            )

        hub = Hub(db, store, secrets, Broadcaster(), factory=factory)
        await hub.start()
        w = World(discord, hub, store)
        try:
            await eventually(lambda: hub.status_of(ACCOUNT).state == "connected")
            yield w
        finally:
            await hub.stop()
            await db.close()


async def test_the_real_builder_reads_the_token_and_proxy_of_the_account(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path / ".env")
    secrets.set("NEST_DISCORD_1_TOKEN", "t0ken")
    config = {"proxy": "http://127.0.0.1:7890", "refs": {"token": "NEST_DISCORD_1_TOKEN"}}
    account = Account(f"discord:{BOT}", "discord", BOT, "洋鲸", "", "", "", "", True, config)
    adapter = build_adapter(account, secrets, lambda status: None, None)  # type: ignore[arg-type]
    assert isinstance(adapter, DiscordAdapter)
    assert adapter._token == "t0ken" and adapter._proxy == "http://127.0.0.1:7890"
    await adapter.stop()


def test_discord_has_its_place_in_the_prompts_and_a_user_id_of_its_own() -> None:
    assert place_of("discord") == ("Discord 频道", "Discord 私聊")
    spec = PLATFORMS["discord"]
    assert spec.user_id.fullmatch("123456789012345678") and not spec.user_id.fullmatch("10086")
    assert {"reply", "mention", "sees_all", "fetch_history", "list_channels"} <= spec.capabilities
    assert not {"sticker_source", "self_echo"} & spec.capabilities


async def test_a_message_reaches_storage_and_its_channel_gets_its_name(world: World) -> None:
    await world.discord.push_message(world.discord.message(content="你好"))
    await eventually(world.rows)
    [row] = await world.rows()
    assert row["text"] == "你好" and row["sender_name"] == "小明" and row["source"] == "live"

    async def named() -> bool:
        return any(c["name"] == "测试服 / #闲聊" for c in await world.store.channels())

    await eventually(named)


@pytest.mark.parametrize("echo", [False, True])
async def test_what_the_bot_says_is_stored_once_echo_or_not(world: World, echo: bool) -> None:
    world.discord.echo_own = echo
    action = SendMessage(GROUP, (Segment("text", {"text": "我说的"}),), "k1")
    result = await world.adapter.perform(action)
    assert result.state == "succeeded"
    [row] = await eventually(world.rows)
    assert row["from_self"] and row["external_id"] == result.message_id and row["text"] == "我说的"


async def test_a_deleted_message_is_recalled_in_storage(world: World) -> None:
    message = world.discord.message(content="说错了")
    await world.discord.push_message(message)
    await eventually(world.rows)
    await world.discord.push("MESSAGE_DELETE", {"id": message["id"], "channel_id": "11", "guild_id": "1"})

    async def recalled() -> bool:
        return bool((await world.rows())[0]["recalled_at"])

    await eventually(recalled)
