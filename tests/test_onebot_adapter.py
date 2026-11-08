"""OneBot adapter against a real-socket fake bridge: receive, survive bad input, reconnect, backfill."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from nest.adapters.base import AdapterStatus
from nest.adapters.onebot11 import adapter as onebot
from nest.adapters.onebot11.adapter import OneBotAdapter
from nest.core.events import CaughtUp, ChannelRef, Event, MessageEvent, RecallEvent
from tests.fakes.onebot import ActionFailed, FakeOneBot
from tests.helpers import eventually

GROUP = ChannelRef("qq", "10001", "group", "42")


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.statuses: list[AdapterStatus] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    def messages(self) -> list[MessageEvent]:
        return [e for e in self.events if isinstance(e, MessageEvent)]

    def states(self) -> list[str]:
        return [s.state for s in self.statuses]


@asynccontextmanager
async def running(
    bot: FakeOneBot,
    *,
    account: str = "10001",
    token: str | None = None,
    backfill: list[ChannelRef] | None = None,
) -> AsyncIterator[tuple[OneBotAdapter, Recorder]]:
    recorder = Recorder()

    async def targets() -> list[tuple[ChannelRef, float]]:
        # Like MessageStore.backfill_targets: only channels with received messages, from the newest one on.
        newest = {}
        for m in recorder.messages():
            newest[m.channel] = max(newest.get(m.channel, 0.0), m.timestamp)
        return [(c, newest[c]) for c in backfill or [] if c in newest]

    adapter = OneBotAdapter(
        account=account,
        http_url=bot.http_url,
        ws_url=bot.ws_url,
        http_token=bot.token if token is None else token,
        ws_token=bot.token if token is None else token,
        on_status=recorder.statuses.append,
        backfill_source=targets if backfill is not None else None,
        backoff_initial=0.05,
        backoff_max=0.2,
        stable_after=0.5,
    )
    await adapter.start(recorder.emit)
    try:
        yield adapter, recorder
    finally:
        await adapter.stop()


async def test_connects_verifies_account_and_delivers_messages() -> None:
    async with FakeOneBot(self_id=10001, nickname="阿鲸") as bot, running(bot) as (adapter, rec):
        await eventually(lambda: adapter.status().state == "connected")
        assert adapter.status().detail == "阿鲸"
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, text="阿鲸在吗", at=[10001]))
        message = (await eventually(rec.messages))[0]
        assert message.channel == GROUP and message.mentions == {"10001"} and not message.backfilled
        assert rec.states()[:2] == ["connecting", "connected"]


async def test_at_targets_get_their_group_names() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_adapter, rec):
        bot.members = {10001: "阿鲸", 9: "阿星"}
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, nickname="阿月", text=" 在吗", at=[10001]))
        await bot.push(bot.group_message(group_id=42, user_id=9, nickname="阿星", text="好"))
        await bot.push(bot.group_message(group_id=42, user_id=8, text=" 你好", at=[10001, 7, 9, 555]))
        first, _, last = await eventually(lambda: rec.messages() if len(rec.messages()) == 3 else None)
        assert first.text == "@阿鲸 在吗" and first.mentions == {"10001"}
        # 7 and 9 are known from their own messages, 555 is no member: the number stays.
        assert last.text == "@阿鲸@阿月@阿星@555 你好"
        looked_up = [int(params["user_id"]) for params in bot.calls_to("get_group_member_info")]
        assert sorted(looked_up) == [555, 10001]  # each asked once, then cached


async def test_a_failing_name_lookup_keeps_the_message() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_adapter, rec):
        del bot.handlers["get_group_member_info"]
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, text=" 在吗", at=[10001]))
        [message] = await eventually(rec.messages)
        assert message.text == "@10001 在吗" and message.mentions == {"10001"}


async def test_own_echo_and_recall() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (adapter, rec):
        await eventually(lambda: bot.connected_clients == 1)
        sent = await adapter.call(
            "send_group_msg", group_id=42, message=[{"type": "text", "data": {"text": "嗨"}}]
        )
        echo = (await eventually(rec.messages))[0]
        assert echo.from_self and echo.message_id == str(sent["message_id"])
        await bot.push(bot.recall(group_id=42, message_id=sent["message_id"], operator_id=10001))
        recall = await eventually(lambda: [e for e in rec.events if isinstance(e, RecallEvent)])
        assert recall[0].message_id == str(sent["message_id"])


async def test_bad_frames_are_skipped_without_dropping_the_connection() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (adapter, rec):
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push_raw("{这不是 JSON")
        await bot.push(bot.group_message(group_id=42, user_id=7, text="x") | {"message": 123})
        await bot.push(bot.group_message(group_id=42, user_id=7, text="正常的一条"))
        await eventually(rec.messages)
        assert [m.text for m in rec.messages()] == ["正常的一条"]
        assert adapter.skipped_frames == 2
        assert adapter.status().state == "connected" and rec.states().count("connected") == 1


def voice(bot: FakeOneBot, *, user_id: int = 7, **fields: object) -> dict:
    record = {"type": "record", "data": {"file": "a.amr"}}
    return bot.group_message(group_id=42, user_id=user_id) | {"message": [record], **fields}


def qq_hears(bot: FakeOneBot, *answers: str) -> None:
    """QQ's speech-to-text: each ask gets the next answer, "" being its "failed"."""
    left = list(answers)

    def fetch(_: dict) -> dict:
        answer = left.pop(0)
        if not answer:
            raise ActionFailed(200, "获取语音转文字结果失败")
        return {"text": answer}

    bot.handlers["fetch_ptt_text"] = fetch


async def test_a_voice_message_arrives_with_what_was_said() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        qq_hears(bot, "", "阿鱼你在吗")  # QQ's first answer is often not ready
        event = voice(bot)
        await bot.push(event)
        message = (await eventually(rec.messages))[0]
        assert message.text == "（语音）阿鱼你在吗"
        assert message.segments[0].data == {"file": "a.amr", "words": "阿鱼你在吗"}
        assert bot.calls_to("fetch_ptt_text") == [{"message_id": str(event["message_id"])}] * 2


async def test_a_voice_qq_cannot_make_out_stays_a_voice() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        qq_hears(bot, "", "", "")
        await bot.push(voice(bot))
        assert (await eventually(rec.messages))[0].text == "[语音]"
        assert len(bot.calls_to("fetch_ptt_text")) == onebot.VOICE_TRIES


async def test_a_slow_answer_does_not_hold_up_the_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onebot, "VOICE_TIMEOUT", 0.05)

    async def slow(_: dict) -> dict:
        await asyncio.sleep(1)
        return {"text": "太晚了"}

    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        bot.handlers["fetch_ptt_text"] = slow
        await bot.push(voice(bot))
        await bot.push(bot.group_message(group_id=42, user_id=7, text="下一条"))
        await eventually(lambda: len(rec.messages()) == 2)
        assert [m.text for m in rec.messages()] == ["[语音]", "下一条"]


async def test_the_bot_does_not_ask_what_it_said_itself() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        qq_hears(bot, "不该问到这里")
        await bot.push(voice(bot, user_id=10001, post_type="message_sent"))
        assert (await eventually(rec.messages))[0].text == "[语音]"
        assert not bot.calls_to("fetch_ptt_text")


async def test_a_voice_missed_while_offline_is_heard_too() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot, backfill=[GROUP]) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, text="断线前"))
        await eventually(rec.messages)
        await bot.drop_connections()
        await eventually(lambda: bot.connected_clients == 0)
        qq_hears(bot, "断线时说的")
        await bot.push(voice(bot))  # nobody listening
        await eventually(lambda: any(m.text == "（语音）断线时说的" for m in rec.messages()))


async def test_events_for_another_account_are_ignored() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, text="别人的") | {"self_id": 999})
        await bot.push(bot.group_message(group_id=42, user_id=7, text="我的"))
        await eventually(rec.messages)
        assert [m.text for m in rec.messages()] == ["我的"]


async def test_wrong_token_reports_auth_failed_and_backs_off() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot, token="wrong") as (adapter, _):
        await eventually(lambda: adapter.status().state == "auth_failed")
        assert "令牌" in adapter.status().detail
        started = bot.rejected_requests
        await asyncio.sleep(0.6)
        # It keeps retrying, but with exponential backoff (0.05 → 0.2 s), not in a hot loop.
        assert 1 <= bot.rejected_requests - started <= 8
        assert bot.connected_clients == 0 and not bot.calls


async def test_endpoint_of_another_account_is_misconfigured() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot, account="99999") as (adapter, rec):
        await eventually(lambda: adapter.status().state == "misconfigured")
        assert "10001" in adapter.status().detail and "99999" in adapter.status().detail
        await bot.push(bot.group_message(group_id=42, user_id=7, text="不该收到"))
        assert bot.connected_clients == 0 and not rec.events


async def test_unreachable_bridge_keeps_retrying_with_a_reason() -> None:
    async with FakeOneBot() as bot:
        url = bot.http_url
    # The bridge is gone (like SnowLuma with the QQ account logged out): ports refuse connections.
    rec = Recorder()
    adapter = OneBotAdapter(
        account="10001",
        http_url=url,
        ws_url=url.replace("http", "ws") + "/",
        on_status=rec.statuses.append,
        backoff_initial=0.05,
        backoff_max=0.1,
    )
    await adapter.start(rec.emit)
    try:
        await eventually(lambda: adapter.attempts >= 3)
        assert adapter.status().state == "reconnecting" and "连不上" in adapter.status().detail
        # The reason stays put across retries instead of flickering back to "正在连接".
        assert rec.states() == ["connecting", "reconnecting"]
    finally:
        await adapter.stop()
    assert adapter.status().state == "stopped"


async def test_reconnect_backfills_only_what_was_missed() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot, backfill=[GROUP]) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.group_message(group_id=42, user_id=7, text="断线前"))
        await eventually(rec.messages)

        await bot.drop_connections()
        await eventually(lambda: bot.connected_clients == 0)
        await bot.push(bot.group_message(group_id=42, user_id=7, text="断线期间"))  # nobody listening
        await eventually(lambda: bot.connected_clients == 1)
        await eventually(lambda: any(m.text == "断线期间" for m in rec.messages()))

        await bot.push(bot.group_message(group_id=42, user_id=7, text="重连之后"))
        await eventually(lambda: any(m.text == "重连之后" for m in rec.messages()))

        by_text = {}
        for m in rec.messages():
            by_text.setdefault(m.text, []).append(m.backfilled)
        assert by_text["断线期间"] == [True]
        assert by_text["重连之后"] == [False]
        assert by_text["断线前"][0] is False  # a backfilled copy may follow; storage dedupes by message id
        assert "reconnecting" in rec.states() and rec.states()[-1] == "connected"
        assert bot.calls_to("get_group_msg_history")[-1]["group_id"] == 42
        done = [e for e in rec.events if isinstance(e, CaughtUp)]
        assert done[-1].account == "10001" and done[-1].gaps[0][0] == GROUP
        # After what it fetched, so the brain finds those messages stored.
        assert rec.events.index(done[-1]) > [m.text for m in rec.messages()].index("断线期间")


async def test_backfill_ignores_history_older_than_what_was_stored() -> None:
    # Found on the real bridge: history returns the group's latest N messages, including ones from hours
    # before nest ever ran. Those are not "missed while disconnected".
    async with FakeOneBot(self_id=10001) as bot, running(bot, backfill=[GROUP]) as (_, rec):
        await eventually(lambda: bot.connected_clients == 1)
        old = bot.group_message(group_id=42, user_id=7, text="几小时前的旧消息")
        old["time"] -= 3 * 3600
        bot.history[42].append(old)  # in history, never delivered live
        await bot.push(bot.group_message(group_id=42, user_id=7, text="最近一条"))
        await eventually(rec.messages)

        await bot.drop_connections()
        await eventually(lambda: bot.connected_clients == 0)
        await bot.push(bot.group_message(group_id=42, user_id=8, text="断线期间"))
        await eventually(lambda: any(m.text == "断线期间" for m in rec.messages()))
        assert "几小时前的旧消息" not in [m.text for m in rec.messages()]


async def test_offline_heartbeat_and_group_list() -> None:
    async with FakeOneBot(self_id=10001) as bot, running(bot) as (adapter, _):
        bot.groups = {42: "测试群"}
        await eventually(lambda: bot.connected_clients == 1)
        await bot.push(bot.heartbeat(good=False))
        await eventually(lambda: adapter.status().state == "offline")
        await bot.push(bot.heartbeat(good=True))
        await eventually(lambda: adapter.status().state == "connected")
        [info] = await adapter.list_channels()
        assert info.channel == GROUP and info.name == "测试群"
