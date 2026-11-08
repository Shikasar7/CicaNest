"""欢迎新人: a join reported by every account becomes one welcome from one live bot, spoken when it can."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from nest.adapters.onebot11.normalize import normalize
from nest.core import welcome
from nest.core.events import JOINED, ChannelRef, MemberJoined
from nest.core.speech import KEY_REFS, SpeechKeeper
from nest.llm.base import ProviderError
from nest.secrets import SecretStore
from nest.storage.slang import GroupFeatureStore
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import BOTS, GROUP, World, world  # noqa: F401 (fixture)
from tests.test_sending import FakeAdapter, go_live
from tests.test_speech import Net

BOT = "qq:10001"
KEY = f"qq:group:{GROUP}"


@dataclass
class Kit:
    w: World
    net: Net
    adapter: FakeAdapter

    async def join(self, user: str = "555", name: str = "小新", accounts: list[str] | None = None) -> None:
        """A join as every one of our accounts in the group reports it."""
        for account in accounts or list(BOTS):
            event = MemberJoined(ChannelRef("qq", account, "group", GROUP), user, name, time.time())
            row = await self.w.store.save(event.as_message())
            assert row is not None
            await self.w.brain.on_member_joined(event, row)

    async def welcomes(self, n: int) -> list[dict]:
        await self.w.settle(n)
        await asyncio.sleep(0.1)  # and no more come
        return [d for d in await self.w.decisions() if d["trigger"] == "welcome"]

    def sent(self) -> list[list[str]]:
        return [[s.type for s in action.segments] for action in self.adapter.actions]


@pytest.fixture
async def kit(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Kit]:  # noqa: F811
    monkeypatch.setattr(welcome, "DELAY", (0.0, 0.0))
    secrets = SecretStore(tmp_path / ".env")
    secrets.set(KEY_REFS["fish"], "fish-key-123456")
    net = Net()
    world.brain.speech = SpeechKeeper(world.db, secrets, transport=httpx.MockTransport(net.handler))
    await world.repo.update_bot(BOT, voice="fish:abc")
    adapter = await go_live(world)  # only 阿鲸 is live: the others only watch
    channel = next(iter(world.brain.bots[BOT].channels))
    await world.repo.set_channel(BOT, channel, voice_mode="on")
    await world.brain.load()
    await GroupFeatureStore(world.db).set(KEY, welcome=True)
    yield Kit(world, net, adapter)


async def test_a_join_every_account_reports_is_welcomed_once_by_voice(kit: Kit) -> None:
    kit.w.providers["10001"].queue("「欢迎小新，进来坐！」\n多余的一行")
    await kit.join()
    [decision] = await kit.welcomes(1)
    assert (
        decision["bot_id"] == BOT and decision["outcome"] == "replied" and decision["voice_state"] == "sent"
    )
    assert decision["reply"] == "欢迎小新，进来坐！" and decision["trigger_text"] == JOINED
    assert kit.sent() == [["record"]] and kit.net.texts == ["欢迎小新，进来坐！"]
    system, ask = kit.w.providers["10001"].requests[0]
    assert "你是阿鲸" in system.content and "「小新」" in ask.content


async def test_without_a_voice_the_welcome_is_text(kit: Kit) -> None:
    await kit.w.repo.update_bot(BOT, voice="")
    await kit.w.brain.load()
    kit.w.providers["10001"].queue("欢迎欢迎")
    await kit.join()
    [decision] = await kit.welcomes(1)
    assert decision["outcome"] == "replied" and decision["voice_state"] is None
    assert kit.sent() == [["text"]] and kit.adapter.actions[0].segments[0].data["text"] == "欢迎欢迎"


async def test_a_model_that_fails_still_welcomes_with_the_plain_line(kit: Kit) -> None:
    kit.w.providers["10001"].queue(
        ProviderError("down", retryable=False),
        ProviderError("down", retryable=False),
        ProviderError("down", retryable=False),
    )
    await kit.join(name="")
    [decision] = await kit.welcomes(1)
    assert decision["reply"] == welcome.fallback("") and "默认的一句" in decision["reason"]


async def test_switched_off_our_own_account_or_an_old_join_gets_nothing(kit: Kit) -> None:
    await GroupFeatureStore(kit.w.db).set(KEY, welcome=False)
    await kit.join()
    await GroupFeatureStore(kit.w.db).set(KEY, welcome=True)
    await kit.join(user="10002", name="阿马")  # one of our bots
    old = MemberJoined(ChannelRef("qq", "10001", "group", GROUP), "777", "", time.time() - welcome.STALE - 1)
    row = await kit.w.store.save(old.as_message())
    assert row is not None
    await kit.w.brain.on_member_joined(old, row)
    await asyncio.sleep(0.2)
    assert kit.adapter.actions == [] and await kit.w.decisions() == []


async def test_a_wave_of_joins_welcomes_only_the_first_few(kit: Kit) -> None:
    for n in range(welcome.MAX_PER_WINDOW + 2):
        await kit.join(user=str(600 + n), name=f"新人{n}")
    welcomed = await kit.welcomes(welcome.MAX_PER_WINDOW)
    assert len(welcomed) == welcome.MAX_PER_WINDOW


async def test_nobody_live_in_the_group_means_no_welcome(kit: Kit) -> None:
    await kit.w.repo.set_mode(BOT, "observe")
    await kit.w.brain.load()
    await kit.join()
    await asyncio.sleep(0.2)
    assert kit.adapter.actions == [] and await kit.w.decisions() == []


def test_the_model_answer_becomes_one_short_line() -> None:
    assert welcome.line("\n  “欢迎！”  \n二") == "欢迎！"
    assert len(welcome.line("长" * 100)) == welcome.MAX_CHARS
    assert welcome.line("SKIP") == "" and welcome.fallback("小新") == "欢迎小新！"


def test_qq_reports_a_join() -> None:
    payload = {
        "post_type": "notice",
        "notice_type": "group_increase",
        "sub_type": "approve",
        "group_id": 42,
        "user_id": 555,
        "operator_id": 0,
        "self_id": 10001,
        "time": 1700000000,
    }
    event = normalize(payload, "10001")
    assert event == MemberJoined(ChannelRef("qq", "10001", "group", "42"), "555", "", 1700000000.0)
    message = event.as_message()
    assert (
        message.message_id == "join:555:1700000000"
        and message.text == JOINED
        and message.sender_name == "555"
    )


async def test_the_switch_is_on_the_speech_page(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    assert (await http.get("/api/speech")).json()["groups"][0]["welcome"] is False
    response = await http.patch(f"/api/groups/{KEY}/features", json={"welcome": True})
    assert response.status_code == 200 and response.json()["welcome"] is True
    [group] = [g for g in (await http.get("/api/speech")).json()["groups"] if g["key"] == KEY]
    assert group["welcome"] is True
