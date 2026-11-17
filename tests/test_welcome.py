"""欢迎新人: a join reported by every account becomes one welcome from one live bot that has it on in the
group, spoken when it can and the bot allows, with the bot's own line after it."""

from __future__ import annotations

import asyncio
import sqlite3
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
from nest.llm.base import Completion, ProviderError
from nest.secrets import SecretStore
from nest.storage.db import Database, load_migrations
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
    channel: int  # 阿鲸's channel id in the group

    async def welcome(self, bot: str = BOT, **settings: object) -> None:
        channel = next(iter(self.w.brain.bots[bot].channels))
        await self.w.repo.set_channel(bot, channel, **settings)  # type: ignore[arg-type]
        await self.w.brain.load()

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
    secrets = SecretStore(tmp_path / ".env")
    secrets.set(KEY_REFS["fish"], "fish-key-123456")
    net = Net()
    world.brain.speech = SpeechKeeper(world.db, secrets, transport=httpx.MockTransport(net.handler))
    await world.repo.update_bot(BOT, voice="fish:abc")
    adapter = await go_live(world)  # only 阿鲸 is live: the others only watch
    channel = next(iter(world.brain.bots[BOT].channels))
    await world.repo.set_channel(BOT, channel, voice_mode="on", welcome=True, welcome_delay=0)
    await world.brain.load()
    yield Kit(world, net, adapter, channel)


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
    assert "你是阿鲸" in system.content and "称呼ta「小新」" in ask.content


async def test_someone_without_a_known_name_is_a_new_friend_never_a_newcomer(kit: Kit) -> None:
    kit.w.providers["10001"].queue("欢迎新人！")
    await kit.join(name="")
    [decision] = await kit.welcomes(1)
    assert decision["reply"] == "欢迎新朋友！"
    [(_, ask)] = kit.w.providers["10001"].requests
    assert "一位新朋友" in ask.content and "称呼ta“新朋友”" in ask.content


async def test_a_line_cut_off_by_the_output_limit_is_not_said_half(kit: Kit) -> None:
    kit.w.providers["10001"].queue(Completion(text="阿星，来了就", latency_ms=1.0, cut=True))
    await kit.join(name="阿星")
    [decision] = await kit.welcomes(1)
    assert decision["reply"] == "欢迎阿星！" and "默认的一句" in decision["reason"]


async def test_a_catchphrase_without_welcome_gets_it_and_an_emoji_name_is_said_without_emoji(
    kit: Kit,
) -> None:
    kit.w.providers["10001"].queue("✨，来了就坐，别拘着。")
    await kit.join(name="阿星✨✨")
    [decision] = await kit.welcomes(1)
    assert decision["reply"] == "欢迎阿星，来了就坐，别拘着。"
    [(_, ask)] = kit.w.providers["10001"].requests
    assert "称呼ta「阿星」" in ask.content and "✨" not in ask.content and "一定要说“欢迎”" in ask.content


async def test_the_bot_is_shown_its_last_welcomes_so_it_says_something_new(kit: Kit) -> None:
    kit.w.providers["10001"].queue("欢迎阿星，来了就坐", "欢迎阿月，随便聊")
    await kit.join(user="501", name="阿星")
    await kit.welcomes(1)
    await kit.join(user="502", name="😏")
    second = await kit.welcomes(2)
    assert [d["reply"] for d in second] == ["欢迎阿星，来了就坐", "欢迎阿月，随便聊"]
    first_ask, again = (request[1].content for request in kit.w.providers["10001"].requests)
    assert "前几次" not in first_ask
    assert "“欢迎阿星，来了就坐”" in again and "换个说法" in again and "一位新朋友" in again


async def test_text_only_is_text_though_the_bot_could_speak(kit: Kit) -> None:
    await kit.welcome(welcome_voice=False)
    kit.w.providers["10001"].queue("欢迎阿星")
    await kit.join(name="阿星")
    [decision] = await kit.welcomes(1)
    assert decision["voice_state"] is None and kit.sent() == [["text"]] and kit.net.texts == []


async def test_the_bots_own_line_follows_the_welcome(kit: Kit) -> None:
    await kit.welcome(welcome_note="记得看群公告")
    kit.w.providers["10001"].queue("欢迎阿星")
    await kit.join(name="阿星")
    [decision] = await kit.welcomes(1)
    assert kit.sent() == [["record"], ["text"]]
    assert kit.adapter.actions[1].segments[0].data["text"] == "记得看群公告"
    assert decision["reply"] == "欢迎阿星" and "接着发了设好的那句话" in decision["reason"]


async def test_only_the_bots_with_it_on_welcome(kit: Kit) -> None:
    pony = FakeAdapter()
    kit.w.brain.adapters["qq:10002"] = pony
    await kit.w.repo.set_mode("qq:10002", "live")
    await kit.w.brain.load()
    kit.w.brain.chance = lambda: 0.99  # the last of those that may welcome
    kit.w.providers["10001"].queue("欢迎阿星")
    await kit.join(user="501", name="阿星")
    [first] = await kit.welcomes(1)
    assert first["bot_id"] == BOT and pony.actions == []  # 阿马 is live there, but has it off
    await kit.welcome("qq:10002", welcome=True, welcome_delay=0)
    kit.w.providers["10002"].queue("欢迎阿月")
    await kit.join(user="502", name="阿月")
    _, second = await kit.welcomes(2)
    assert second["bot_id"] == "qq:10002" and len(pony.actions) == 1


async def test_switched_off_while_waiting_means_no_welcome(kit: Kit) -> None:
    await kit.welcome(welcome_delay=1)
    kit.w.brain.chance = lambda: 0.0  # the shortest wait: 0.75 s
    await kit.join()
    await asyncio.sleep(0.1)
    await kit.welcome(welcome=False)
    await asyncio.sleep(1.0)
    assert kit.adapter.actions == [] and await kit.w.decisions() == []


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
    await kit.welcome(welcome=False)
    await kit.join()
    await kit.welcome(welcome=True)
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
    assert welcome.line("😏，来了就坐") == "欢迎新朋友，来了就坐"
    assert welcome.line("阿星，来了就坐", "阿星") == "欢迎阿星，来了就坐"
    assert welcome.spoken("招财✨纳福 ") == "招财纳福" and welcome.spoken("😏") == ""
    assert welcome.wait(10, 0.0) == 7.5 and welcome.wait(10, 1.0) == 12.5 and welcome.wait(0, 0.5) == 0


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


async def test_the_settings_are_in_the_bots_group_settings(api_world: ApiWorld) -> None:  # noqa: F811
    body = {"welcome": True, "welcome_voice": False, "welcome_delay": 12, "welcome_note": "  记得看群公告 "}
    assert (await api_world.patch_channel("whale@42", body)).status_code == 200
    [channel] = [
        c
        for b in (await api_world.http.get("/api/bots")).json()
        if b["id"] == BOT
        for c in b["channels"]
        if c["channel_id"] == api_world.channels["whale@42"]
    ]
    assert {k: channel[k] for k in body} == {**body, "welcome_note": "记得看群公告"}
    assert (await api_world.patch_channel("whale@42", {"welcome_delay": 121})).status_code == 422
    assert (await api_world.patch_channel("whale@42", {"welcome_note": "一\n二"})).status_code == 422
    assert "groups" not in (await api_world.http.get("/api/speech")).json()
    assert (
        await api_world.http.patch(f"/api/groups/{KEY}/features", json={"welcome": True})
    ).status_code == 422


async def test_a_group_that_had_it_on_keeps_it_for_the_bots_there(tmp_path: Path) -> None:
    path = tmp_path / "nest.db"
    with sqlite3.connect(path) as raw:
        for number, _, sql in load_migrations()[:23]:
            raw.executescript(f"{sql}\nPRAGMA user_version = {number};")
        for uin in ("10001", "10002"):
            raw.execute(
                """INSERT INTO accounts (id, platform, self_id, http_url, ws_url, created_at, updated_at)
                   VALUES (?, 'qq', ?, 'h', 'w', 0, 0)""",
                (f"qq:{uin}", uin),
            )
            raw.execute(
                "INSERT INTO bots (id, account_id, name, created_at, updated_at) VALUES (?, ?, ?, 0, 0)",
                (f"qq:{uin}", f"qq:{uin}", uin),
            )
            for group in ("42", "77"):
                cursor = raw.execute(
                    "INSERT INTO channels (account_id, kind, external_id) VALUES (?, 'group', ?)",
                    (f"qq:{uin}", group),
                )
                enabled = int(uin == "10001" or group == "77")
                raw.execute(
                    "INSERT INTO bot_channels (bot_id, channel_id, enabled) VALUES (?, ?, ?)",
                    (f"qq:{uin}", cursor.lastrowid, enabled),
                )
        raw.execute("INSERT INTO group_features (group_key, welcome) VALUES ('qq:group:42', 1)")
    db = await Database.open(path)
    try:
        rows = await db.fetchall(
            """SELECT bc.bot_id, c.external_id, bc.welcome, bc.welcome_voice, bc.welcome_delay,
                      bc.welcome_note
               FROM bot_channels bc JOIN channels c ON c.id = bc.channel_id"""
        )
        on = {(r["bot_id"], r["external_id"]) for r in rows if r["welcome"]}
        assert on == {("qq:10001", "42")}  # 阿马 is not switched on in group 42; 77 never had it
        assert {(r["welcome_voice"], r["welcome_delay"], r["welcome_note"]) for r in rows} == {(1, 5, "")}
        columns = await db.fetchall("PRAGMA table_info(group_features)")
        assert "welcome" not in {c["name"] for c in columns}
    finally:
        await db.close()
