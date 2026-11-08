"""Reply decisions: triggers, collection, gates, coordination between bots, freshness, outcomes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nest.core.brain import Brain, Draft, Policy, _opening
from nest.core.broadcast import Broadcaster
from nest.core.events import CaughtUp, ChannelRef, MessageEvent, Segment
from nest.core.prompt import MISSED_NOTE, build_prompt, clean_reply
from nest.llm.base import ProviderError
from nest.llm.runtime import ProviderRegistry, ProviderRuntime
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually

BOTS = {"10001": "阿鲸", "10002": "阿马", "10003": "阿龙"}
GROUP = "42"
FAST = Policy(
    quiet_seconds=0.05,
    max_collect_seconds=0.3,
    occasional_messages=3,
    occasional_seconds=999,
    mention_wait_limit=2,
    claim_seconds=30,
    typing_max=0.01,
)


@dataclass
class World:
    db: Database
    store: MessageStore
    repo: BotRepo
    brain: Brain
    providers: dict[str, FakeProvider]
    broadcaster: Broadcaster
    events: list = field(default_factory=list)
    counter: int = 0

    async def say(
        self,
        text: str,
        *,
        sender: str = "7",
        name: str = "小明",
        to: list[str] | None = None,
        accounts: list[str] | None = None,
        reply_to: str | None = None,
        backfilled: bool = False,
        segments: tuple[Segment, ...] | None = None,
    ) -> str:
        """A group message as every bot account receives it (same message id, like SnowLuma)."""
        self.counter += 1
        mid = f"m{self.counter}"
        for account in accounts or list(BOTS):
            event = MessageEvent(
                channel=ChannelRef("qq", account, "group", GROUP),
                message_id=mid,
                sender_id=sender,
                sender_name=name,
                segments=segments or (Segment("text", {"text": text}),),
                text=text,
                timestamp=time.time(),
                from_self=sender == account,
                mentions=frozenset(to or ()),
                reply_to=reply_to,
                backfilled=backfilled,
            )
            row = await self.store.save(event)
            assert row is not None
            await self.brain.on_message(event, row)
        return mid

    async def decisions(self, bot: str | None = None) -> list[dict]:
        channels = [c["id"] for c in await self.store.channels()]
        rows = await self.repo.decisions(channels)
        return [d for d in rows if bot is None or d["bot_id"] == f"qq:{bot}"]

    async def settle(self, n: int, timeout: float = 5.0) -> list[dict]:  # noqa: ASYNC109
        return await eventually(lambda: self._at_least(n), timeout=timeout)

    async def _at_least(self, n: int) -> list[dict] | None:
        rows = await self.decisions()
        return rows if len(rows) >= n else None


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    db = await Database.open(tmp_path / "nest.db")
    store, repo, broadcaster = MessageStore(db), BotRepo(db), Broadcaster()
    providers: dict[str, FakeProvider] = {}
    registry = ProviderRegistry(repo.record_llm_call, retry_delay=0)
    for uin, name in BOTS.items():
        await upsert_account(db, platform="qq", self_id=uin, name=name, http_url="http://x", ws_url="ws://x/")
        config = ProviderConfig(id=f"model-{uin}", kind="openai_chat", base_url="http://x", model="m")
        await repo.upsert_provider(config)
        providers[uin] = FakeProvider(name=config.id, default="SKIP")
        registry.runtimes[config.id] = ProviderRuntime(
            config, providers[uin], repo.record_llm_call, retry_delay=0
        )
        await repo.upsert_bot(account_id=f"qq:{uin}", name=name, aliases=[name], provider_id=config.id)
        channel = await store.channel_id(ChannelRef("qq", uin, "group", GROUP))
        persona = await repo.upsert_persona(f"{name}人设", f"你是{name}，说话很短。")
        await repo.set_channel(
            f"qq:{uin}", channel, enabled=True, activity="mentions", persona_id=persona, cooldown_seconds=0.5
        )
    brain = Brain(repo, store, registry, broadcaster, policy=FAST, our_accounts=set(BOTS))
    await brain.load()
    w = World(db, store, repo, brain, providers, broadcaster)
    queue = broadcaster.subscribe()

    async def drain() -> None:
        while True:
            w.events.append(await queue.get())

    drainer = asyncio.create_task(drain())
    try:
        yield w
    finally:
        drainer.cancel()
        await brain.stop()
        await registry.aclose()
        await db.close()


async def set_activity(w: World, activity: str, bots: list[str] | None = None) -> None:
    for uin in bots or list(BOTS):
        bot = w.brain.bots[f"qq:{uin}"]
        for channel_id in bot.channels:
            await w.repo.set_channel(bot.id, channel_id, activity=activity)
    await w.brain.load()


async def test_mention_leads_to_a_would_reply_with_persona_and_context(world: World) -> None:
    world.providers["10001"].queue("在呢在呢")
    await world.say("今天好热")
    await world.say("阿鲸你在吗")  # called by name → only 阿鲸 considers it
    [decision] = await world.settle(1)
    assert decision["bot_id"] == "qq:10001" and decision["trigger"] == "mention"
    assert (
        decision["outcome"] == "would_reply" and decision["reply"] == "在呢在呢" and decision["llm_call_id"]
    )
    system, user = world.providers["10001"].requests[0]
    assert "你是阿鲸" in system.content and "没有合适的话也输出 SKIP" in system.content
    # Unanswered earlier chatter is part of the batch the bot considers, not just background.
    assert "新消息：\n小明：今天好热\n【点到你】小明：阿鲸你在吗" in user.content
    # The event is published right after the row is written; the drain task may not have run yet.
    await eventually(lambda: ("decision", decision | {"bot_name": "阿鲸"}) in world.events)
    assert not world.providers["10002"].requests and not world.providers["10003"].requests


async def test_at_mention_and_reply_to_own_message_count_as_calls(world: World) -> None:
    own = await world.say("我是阿马", sender="10002", name="阿马")
    await asyncio.sleep(0.6)  # past the cooldown its own message started
    await world.say("回你一句", reply_to=own)
    await world.say("@阿龙 看这个", to=["10003"])
    decisions = await world.settle(2)
    assert {d["bot_id"] for d in decisions} == {"qq:10002", "qq:10003"}
    assert all(d["trigger"] == "mention" for d in decisions)


async def test_an_at_name_containing_an_alias_is_not_a_call(world: World) -> None:
    # The adapter fills in @-names; "@阿鲸粉丝" @-s a person, it does not call 阿鲸 by name.
    fan = Segment("at", {"qq": "8", "name": "阿鲸粉丝"})
    await world.say("@阿鲸粉丝 早", segments=(fan, Segment("text", {"text": " 早"})))
    await asyncio.sleep(0.4)
    assert await world.decisions() == []
    whale = Segment("at", {"qq": "10001", "name": "阿鲸"})
    await world.say("@阿鲸 你在干嘛", to=["10001"], segments=(whale, Segment("text", {"text": " 你在干嘛"})))
    [decision] = await world.settle(1)
    assert decision["bot_id"] == "qq:10001" and decision["trigger"] == "mention"
    _, user = world.providers["10001"].requests[0]
    assert "小明：@阿鲸粉丝 早\n【点到你】小明：@阿鲸 你在干嘛" in user.content


async def test_mentions_only_ignores_unaddressed_chatter_without_calling_the_model(world: World) -> None:
    for text in ("一", "二", "三", "四", "五"):
        await world.say(text)
    await asyncio.sleep(0.5)
    assert not await world.decisions()
    assert not any(p.requests for p in world.providers.values())


async def test_occasional_chime_in_goes_to_exactly_one_bot(world: World) -> None:
    await set_activity(world, "occasional")
    for provider in world.providers.values():
        provider.queue("我来接一句")
    for text in ("今天好热", "热死了", "空调开起来"):
        await world.say(text)
    decisions = await world.settle(3)
    speakers = [d for d in decisions if d["outcome"] == "would_reply"]
    held = [d for d in decisions if d["outcome"] == "suppressed"]
    assert len(speakers) == 1 and speakers[0]["trigger"] == "chime_in"
    winner = BOTS[speakers[0]["bot_id"].split(":")[1]]
    assert len(held) == 2 and all(d["reason"] == f"这轮由 {winner} 插话" for d in held)
    assert sum(len(p.requests) for p in world.providers.values()) == 1


@pytest.mark.parametrize(
    ("text", "to", "considered"),
    [
        ("有没有什么解压的游戏推荐", None, True),
        ("周末去爬山吗", None, True),
        ("这个怎么弄？", None, True),
        ("今天加班到九点", None, False),  # chatter: waits for the message count
        ("你周末去爬山吗", ["7"], False),  # a question to one person, not to the group
    ],
)
async def test_occasional_bots_consider_an_open_question_at_once(
    world: World, text: str, to: list[str] | None, considered: bool
) -> None:
    await set_activity(world, "occasional")
    for provider in world.providers.values():
        provider.queue("我知道")
    await world.say(text, to=to)
    if considered:
        decisions = await world.settle(3)
        assert {d["trigger"] for d in decisions} == {"chime_in"}
        assert [d["outcome"] for d in decisions].count("would_reply") == 1  # still one bot per round
    else:
        await asyncio.sleep(0.5)
        assert not await world.decisions()


async def test_a_question_to_one_bot_by_name_is_not_open_to_the_others(world: World) -> None:
    await set_activity(world, "occasional")
    world.providers["10002"].queue("快得很")
    await world.say("阿马你跑得快吗")
    [decision] = await world.settle(1)
    await asyncio.sleep(0.5)
    assert len(await world.decisions()) == 1
    assert decision["bot_id"] == "qq:10002" and decision["trigger"] == "mention"


async def test_the_word_model_alone_is_not_filtered() -> None:
    assert clean_reply("这个模型挺好用的") == ("这个模型挺好用的", "")
    assert clean_reply("我是一个语言模型")[0] == ""


async def test_a_reply_keeps_up_to_its_line_limit() -> None:
    raw = "哈哈哈\n\n“笑死我了”\nSKIP\n第三条\n第四条"
    assert clean_reply(raw) == ("哈哈哈", "")
    assert clean_reply(raw, max_lines=3) == ("哈哈哈\n笑死我了\n第三条", "")
    assert clean_reply("短的\n" + "长" * 81, max_lines=3)[1] == "回复太长（81 字），已丢弃"
    assert clean_reply("没事\n我是AI啦", max_lines=3)[0] == ""


async def test_mentioned_bots_are_not_held_back_by_coordination(world: World) -> None:
    await set_activity(world, "occasional")
    world.providers["10001"].queue("我在")
    world.providers["10002"].queue("我也在")
    await world.say("阿鲸 阿马 你们俩在吗")
    decisions = await world.settle(2)
    assert {d["bot_id"]: d["outcome"] for d in decisions} == {
        "qq:10001": "would_reply",
        "qq:10002": "would_reply",
    }


async def test_skip_and_filtered_replies_are_skipped_with_a_reason(world: World) -> None:
    world.providers["10001"].queue("SKIP", "这是一句" + "非常" * 50 + "长的话", "我是AI助手")
    await world.say("阿鲸 1")
    await world.settle(1)
    await asyncio.sleep(0.6)
    await world.say("阿鲸 2")
    await world.settle(2)
    await asyncio.sleep(0.6)
    await world.say("阿鲸 3")
    decisions = await world.settle(3)
    assert [d["outcome"] for d in decisions] == ["skipped"] * 3
    assert decisions[0]["reason"] == "模型选择不回"
    assert decisions[1]["reason"].startswith("回复太长")
    assert "不该说的词" in decisions[2]["reason"]


async def test_cooldown_suppresses_chime_in_but_a_mention_waits_it_out(world: World) -> None:
    await set_activity(world, "active", bots=["10001"])
    world.providers["10001"].queue("第一句", "被点名后回")
    await world.say("随便说说", accounts=["10001"])
    first = await world.settle(1)
    assert first[0]["outcome"] == "would_reply"
    await world.say("又一句", accounts=["10001"])
    second = await world.settle(2)
    assert second[1]["outcome"] == "suppressed" and second[1]["reason"].startswith("刚说过话")
    started = time.time()
    await world.say("阿鲸！", accounts=["10001"])
    third = await world.settle(3)
    assert third[2]["outcome"] == "would_reply" and third[2]["reply"] == "被点名后回"
    assert time.time() - started >= 0.3  # waited for the 0.5 s cooldown instead of dropping the mention


async def test_messages_sent_by_another_program_count_as_speaking(world: World) -> None:
    await set_activity(world, "active", bots=["10001"])
    await world.say(
        "我刚说的", sender="10001", name="阿鲸", accounts=["10001"]
    )  # e.g. another program on the account
    await world.say("接着聊", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "suppressed" and decision["reason"].startswith("刚说过话")


async def test_same_sender_following_up_mid_generation_triggers_one_regeneration(world: World) -> None:
    provider = world.providers["10001"]
    provider.delay = 0.3
    provider.queue("先这么回", "看完补充再回")
    await world.say("阿鲸 我问个事", accounts=["10001"])
    await eventually(lambda: len(provider.requests) == 1)
    await world.say("就是那个昨天的事", accounts=["10001"])  # same sender, while the model is thinking
    [decision] = await world.settle(1)
    assert decision["regenerated"] and decision["reply"] == "看完补充再回"
    assert "就是那个昨天的事" in provider.requests[1][1].content


async def test_unrelated_messages_mid_generation_make_the_reply_quote_its_target(world: World) -> None:
    provider = world.providers["10001"]
    provider.delay = 0.3
    provider.queue("回你")
    await world.say("阿鲸 你怎么看", accounts=["10001"])
    await eventually(lambda: len(provider.requests) == 1)
    for i, who in enumerate(("8", "9", "11")):
        await world.say(f"别人的话{i}", sender=who, name=f"路人{i}", accounts=["10001"])
    decisions = await world.settle(1)
    assert (
        decisions[0]["outcome"] == "would_reply" and decisions[0]["quote"] and not decisions[0]["regenerated"]
    )


async def test_model_failure_is_recorded_as_failed(world: World) -> None:
    world.providers["10001"].queue(ProviderError("HTTP 502：上游挂了", retryable=False))
    await world.say("阿鲸？")
    [decision] = await world.settle(1)
    assert decision["outcome"] == "failed" and "上游挂了" in decision["reason"]


async def test_bots_do_not_trigger_each_other_and_chains_stop(world: World) -> None:
    await set_activity(world, "active")
    await world.say("我是阿龙，随便说", sender="10003", name="阿龙")
    await asyncio.sleep(0.4)
    assert not await world.decisions()  # another bot's plain message is context only
    for i in range(3):
        await world.say(f"bot 闲聊 {i}", sender="10002", name="阿马", accounts=["10001"])
    await world.say("阿鲸你说呢", sender="10002", name="阿马", accounts=["10001"])
    [decision] = await world.settle(1)
    # Several messages in a row from one bot are one turn: 阿龙, then 阿马 is two.
    assert decision["outcome"] == "skipped"
    for i in range(2):
        await world.say(f"bot 对聊 {i}", sender="10003", name="阿龙", accounts=["10001"])
        await world.say(f"bot 对聊 {i}", sender="10002", name="阿马", accounts=["10001"])
    await asyncio.sleep(0.6)  # past the cooldown
    await world.say("阿鲸你说呢", sender="10002", name="阿马", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[-1]["outcome"] == "suppressed" and "等真人说话" in decisions[-1]["reason"]


async def test_paused_bots_and_backfilled_history_never_trigger(world: World) -> None:
    await world.repo.set_mode("qq:10001", "paused")
    await world.brain.load()
    await world.say("阿鲸在吗")
    await world.say("阿龙在吗", backfilled=True)
    await asyncio.sleep(0.5)
    assert not await world.decisions()


def back_online(account: str, since: float) -> CaughtUp:
    return CaughtUp(account, ((ChannelRef("qq", account, "group", GROUP), since),), time.time() + 1)


async def test_back_online_a_bot_answers_the_ats_only_the_others_received(world: World) -> None:
    since = time.time() - 60
    await world.say("@阿龙 有敌人", to=["10003"], accounts=["10001", "10002"])
    await world.say("@阿龙 快来", to=["10003"], sender="8", name="小红", accounts=["10001"])
    await world.say("@阿鲸 你呢", to=["10001"], accounts=["10001"])  # not to her
    await world.say("@阿龙 我也在", to=["10003"], sender="10001", accounts=["10002"])  # one of ours
    await world.settle(1)  # 阿鲸 was online and looked at her own @
    world.providers["10003"].queue("来了")

    await world.brain.catch_up(back_online("10003", since))
    rows = await world.settle(2)

    decision = next(d for d in rows if d["bot_id"] == "qq:10003")
    assert decision["outcome"] == "would_reply" and decision["reply"] == "来了"
    assert decision["trigger"] == "mention"
    prompt = world.providers["10003"].requests[-1][-1].content
    assert MISSED_NOTE in prompt and "有敌人" in prompt and "快来" in prompt
    assert "你呢" not in prompt and "我也在" not in prompt
    assert prompt.count("有敌人") == 1  # two accounts stored it: one message


async def test_back_online_its_own_history_counts_and_what_it_saw_live_does_not(world: World) -> None:
    since = time.time() - 60
    await world.say("@阿龙 收到了吗", to=["10003"], accounts=["10003"])
    await world.settle(1)
    await world.brain.catch_up(back_online("10003", since))
    await asyncio.sleep(0.3)
    assert len(await world.decisions("10003")) == 1  # answered when it arrived, not again

    await world.say("@阿龙 断线时说的", to=["10003"], accounts=["10003"], backfilled=True)
    world.providers["10003"].queue("补上")
    await world.brain.catch_up(back_online("10003", since))
    await world.settle(2)
    assert "断线时说的" in world.providers["10003"].requests[-1][-1].content


async def stored(world: World, text: str, account: str, *, ago: float = 0.0) -> None:
    """An @ received live just before nest stopped: stored, and nobody got to look at it."""
    world.counter += 1
    await world.store.save(
        MessageEvent(
            channel=ChannelRef("qq", account, "group", GROUP),
            message_id=f"m{world.counter}",
            sender_id="7",
            sender_name="小明",
            segments=(Segment("text", {"text": text}),),
            text=text,
            timestamp=time.time() - ago,
            mentions=frozenset({account}),
        )
    )


async def test_an_at_a_restart_cut_off_is_answered_after_it(world: World) -> None:
    await stored(world, "@阿龙 唱首歌", "10003")
    world.providers["10003"].queue("来了")
    await world.brain.catch_up(back_online("10003", time.time() + 1))  # later messages came in after it
    [decision] = await world.settle(1)
    assert decision["bot_id"] == "qq:10003" and decision["reply"] == "来了"
    assert "唱首歌" in world.providers["10003"].requests[-1][-1].content

    await world.brain.catch_up(back_online("10003", time.time() + 1))  # decided now: not again
    await asyncio.sleep(0.3)
    assert len(await world.decisions("10003")) == 1


async def test_a_dropped_connection_does_not_repeat_an_at_still_being_looked_at(world: World) -> None:
    world.providers["10003"].delay = 0.3
    world.providers["10003"].queue("来了")
    await world.say("@阿龙 在吗", to=["10003"], accounts=["10003"])
    await eventually(lambda: world.providers["10003"].requests)
    await world.brain.catch_up(back_online("10003", time.time() + 1))
    await world.settle(1)
    await asyncio.sleep(0.3)
    assert len(world.providers["10003"].requests) == 1


async def test_an_at_cut_off_long_ago_is_left_alone(world: World) -> None:
    await stored(world, "@阿龙 早上好", "10003", ago=3600)
    await world.brain.catch_up(back_online("10003", time.time() + 1))
    await asyncio.sleep(0.3)
    assert not await world.decisions("10003")


def test_a_bot_is_told_which_lines_are_its_own_and_not_to_repeat_them() -> None:
    own = {"id": 1, "from_self": True, "sender_id": "1", "sender_name": "小马", "text": "今天也没干啥。"}
    new = {"id": 2, "from_self": False, "sender_id": "7", "sender_name": "小明", "text": "哈哈"}
    system, user = build_prompt(
        persona="", bot_name="小马", context=[own], batch=[new], called=set(), peers={}
    )
    assert "你（小马）：今天也没干啥。" in user.content
    assert "记录里写着「你」的是你自己说过的" in system.content


async def test_back_online_old_or_nothing_stays_quiet(world: World) -> None:
    await world.say("@阿龙 早就说过了", to=["10003"], accounts=["10001"])
    await world.brain.catch_up(back_online("10003", time.time() + 1))  # nothing newer than what it has
    world.brain.policy = Policy(**{**FAST.__dict__, "missed_hours": 0})
    await world.brain.catch_up(back_online("10003", 0.0))
    await asyncio.sleep(0.3)
    assert not await world.decisions("10003")


def test_a_message_this_account_never_got_is_answered_with_an_at() -> None:
    own = {"external_id": "9", "sender_id": "7"}
    other = {"external_id": "", "sender_id": "7"}
    assert _opening(Draft("would_reply", target=own, quote=True)) == [Segment("reply", {"id": "9"})]
    assert _opening(Draft("would_reply", target=own, quote=False)) == []
    assert _opening(Draft("would_reply", target=other, quote=False)) == [Segment("at", {"id": "7"})]
    assert _opening(Draft("would_reply")) == []


async def test_hourly_limit(world: World, tmp_path: Path) -> None:
    world.brain.policy = Policy(**{**FAST.__dict__, "hourly_limit": 1})
    world.providers["10001"].queue("一", "二")
    await world.say("阿鲸 1", accounts=["10001"])
    await world.settle(1)
    await asyncio.sleep(0.6)
    await world.say("阿鲸 2", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[1]["outcome"] == "suppressed" and "到上限了" in decisions[1]["reason"]


async def test_missing_model_is_a_failed_decision(world: World) -> None:
    await world.repo.upsert_bot(account_id="qq:10001", name="阿鲸", aliases=["阿鲸"], provider_id=None)
    await world.brain.load()
    await world.say("阿鲸", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "failed" and "没有给这个 bot 配置模型" in decision["reason"]


async def test_a_model_that_did_not_build_is_named_in_the_decision(world: World) -> None:
    registry = world.brain.providers
    registry.runtimes.pop("model-10001")
    registry.set_error("model-10001", "TypeError: 超时填错了")
    await world.say("阿鲸", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "failed"
    assert decision["reason"] == "模型 model-10001 设置有误：TypeError: 超时填错了"
    registry.errors.clear()
    await asyncio.sleep(0.6)  # past the cooldown
    await world.say("阿鲸 再来", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[1]["reason"] == "模型 model-10001 设置有误：没有加载"


async def test_a_regeneration_that_spans_a_model_swap_uses_the_new_model(world: World) -> None:
    old = world.providers["10001"]
    old.delay = 0.3
    old.queue("先这么回")
    await world.say("阿鲸 我问个事", accounts=["10001"])
    await eventually(lambda: len(old.requests) == 1)
    # The model is edited while it is thinking; then the sender adds more, so the bot regenerates.
    new = FakeProvider("换过设置再回", name="model-10001")
    old_runtime = world.brain.providers.get("model-10001")
    assert old_runtime is not None
    world.brain.providers.replace(old_runtime.config, new)
    await world.say("就是那个昨天的事", accounts=["10001"])
    [decision] = await world.settle(1)
    await asyncio.sleep(0.2)
    assert len(await world.decisions()) == 1  # exactly one decision for the whole evaluation
    assert decision["outcome"] == "would_reply" and decision["regenerated"]
    assert decision["reply"] == "换过设置再回"
    assert len(old.requests) == 1 and len(new.requests) == 1
    await eventually(lambda: old.closed)  # closed once its call had finished
    assert not new.closed


async def switch_model(w: World, bot_id: str = "qq:10001") -> FakeProvider:
    """What PATCH /api/bots/{id} with another provider_id does, after that model was added."""
    config = ProviderConfig(id="model-new", kind="openai_chat", base_url="http://y", model="m2")
    await w.repo.upsert_provider(config)
    new = FakeProvider("换了一边来回", name=config.id)
    w.brain.providers.replace(config, new)
    await w.repo.update_bot(bot_id, provider_id=config.id)
    await w.brain.load()
    return new


async def test_a_mention_waiting_out_the_cooldown_uses_the_model_the_bot_has_now(world: World) -> None:
    old = world.providers["10001"]
    old.queue("第一句")
    await world.say("阿鲸 你好", accounts=["10001"])
    await world.settle(1)
    await world.say("阿鲸 再说一句", accounts=["10001"])
    [channel_id] = world.brain.bots["qq:10001"].channels
    state = world.brain._states[("qq:10001", channel_id)]
    await eventually(lambda: state.evaluating)  # now waiting out the 0.5 s cooldown
    # Meanwhile the bot is switched to another model, and the old one is deleted (no bot uses it now).
    new = await switch_model(world)
    world.brain.providers.remove("model-10001")
    assert state.pending, "the evaluation must still be waiting, before it takes its batch"
    decisions = await world.settle(2)
    assert decisions[1]["outcome"] == "would_reply" and decisions[1]["reply"] == "换了一边来回"
    assert len(old.requests) == 1 and len(new.requests) == 1


async def test_a_regeneration_after_the_bot_switched_models_uses_the_new_model(world: World) -> None:
    old = world.providers["10001"]
    old.delay = 0.3
    old.queue("先这么回")
    await world.say("阿鲸 我问个事", accounts=["10001"])
    await eventually(lambda: len(old.requests) == 1)
    new = await switch_model(world)  # while the old model is thinking
    await world.say("就是那个昨天的事", accounts=["10001"])  # the sender adds more: regenerate
    [decision] = await world.settle(1)
    assert decision["regenerated"] and decision["reply"] == "换了一边来回"
    assert len(old.requests) == 1 and len(new.requests) == 1


async def test_a_model_removed_mid_generation_fails_the_regeneration(world: World) -> None:
    provider = world.providers["10001"]
    provider.delay = 0.3
    provider.queue("先这么回")
    await world.say("阿鲸 我问个事", accounts=["10001"])
    await eventually(lambda: len(provider.requests) == 1)
    world.brain.providers.remove("model-10001")
    await world.say("就是那个昨天的事", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "failed" and decision["reason"] == "这个 bot 的模型刚被移除"
    assert decision["regenerated"] and len(provider.requests) == 1


async def test_a_bot_whose_model_is_paused_does_not_take_the_round(world: World) -> None:
    await set_activity(world, "occasional", ["10001", "10002"])
    world.brain.providers.get("model-10002").health.open_until = time.time() + 60  # breaker open
    world.providers["10001"].queue("我来")
    for text in ("今天好热", "热死了", "空调开起来"):
        await world.say(text, accounts=["10002", "10001"])  # 10002 reaches the round first
    decisions = await world.settle(2)
    outcomes = {d["bot_id"]: (d["outcome"], d["reason"]) for d in decisions}
    assert outcomes["qq:10001"][0] == "would_reply"
    assert outcomes["qq:10002"][0] == "failed" and "暂停中" in outcomes["qq:10002"][1]


async def test_a_bot_that_says_nothing_releases_the_round(world: World) -> None:
    await set_activity(world, "occasional", ["10001", "10002"])
    world.providers["10001"].queue("SKIP")
    # Holds the round while 10002 asks for it: long enough for a slow CI runner to get 10002 there first.
    world.providers["10001"].delay = 1.5
    world.providers["10002"].queue("那我来说")
    texts = ("今天好热", "热死了", "空调开起来")
    # 10001 takes the first round, then skips. 10002 hears the messages only once 10001's model call is under
    # way: heard together, either bot may finish collecting first (on Windows the event loop clock ticks every
    # ~16 ms, so both wake in the same tick, in no particular order).
    for text in texts:
        await world.say(text, accounts=["10001"])
    await eventually(lambda: world.providers["10001"].requests)
    for text in texts:
        await world.say(text, accounts=["10002"])
    first = await world.settle(2)
    assert {d["bot_id"]: d["outcome"] for d in first} == {"qq:10001": "skipped", "qq:10002": "suppressed"}
    # Within the 30 s window 10002 asks again (alone, so nothing races it): had 10001 kept the round, 10002
    # would be refused.
    for text in ("还是热", "开了也热", "唉"):
        await world.say(text, accounts=["10002"])
    second = (await world.settle(3))[2:]
    assert [(d["bot_id"], d["outcome"]) for d in second] == [("qq:10002", "would_reply")]


async def test_each_bots_last_line_is_remembered_however_much_happened_since(world: World) -> None:
    world.providers["10002"].queue("我来啦")
    await world.say("阿马在吗")
    [said] = await world.settle(1)
    await world.say("阿鲸 你呢")  # another bot's decision since (it skips: the fake model says SKIP)
    await world.settle(2)
    lines = await world.repo.last_lines()
    assert set(lines) == {"qq:10002"} and lines["qq:10002"]["id"] == said["id"]
    assert lines["qq:10002"]["reply"] == "我来啦" and lines["qq:10002"]["trigger_text"] == "阿马在吗"


async def test_a_reply_shows_what_it_replies_to(world: World) -> None:
    await world.say("周末去爬山吗", sender="55", name="阿星")
    first = "m1"
    world.providers["10001"].queue("好")
    await world.say("阿鲸，你觉得呢？", sender="66", name="阿木", reply_to=first)
    await world.settle(1)
    prompt = world.providers["10001"].requests[-1][-1].content
    assert "阿木（回复 阿星：周末去爬山吗）：阿鲸，你觉得呢？" in prompt


async def backup_for(world: World, uin: str = "10001") -> FakeProvider:
    config = ProviderConfig(id="spare", kind="openai_chat", base_url="http://x", model="m")
    await world.repo.upsert_provider(config)
    spare = FakeProvider(name="spare", default="备用模型在")
    world.brain.providers.runtimes["spare"] = ProviderRuntime(
        config, spare, world.repo.record_llm_call, retry_delay=0
    )
    await world.repo.update_bot(f"qq:{uin}", backup_provider_id="spare")
    await world.brain.load()
    return spare


async def test_the_backup_model_speaks_when_the_bots_own_fails(world: World) -> None:
    spare = await backup_for(world)
    world.providers["10001"].queue(ProviderError("HTTP 429：额度用完", retryable=False))
    await world.say("阿鲸？")
    [decision] = await world.settle(1)
    assert decision["reply"] == "备用模型在" and len(spare.requests) == 1
    assert "用了备用模型 spare" in decision["reason"]


async def test_the_backup_model_takes_over_while_the_bots_own_is_resting(world: World) -> None:
    spare = await backup_for(world)
    own = world.brain.providers.runtimes["model-10001"]
    own.health.open_until = time.time() + 60
    await world.say("阿鲸？")
    [decision] = await world.settle(1)
    assert decision["reply"] == "备用模型在" and not world.providers["10001"].requests
    assert spare.requests


async def test_both_models_down_is_a_failure(world: World) -> None:
    spare = await backup_for(world)
    spare.queue(ProviderError("HTTP 500：也挂了", retryable=False))
    world.providers["10001"].queue(ProviderError("HTTP 429：额度用完", retryable=False))
    await world.say("阿鲸？")
    [decision] = await world.settle(1)
    assert decision["outcome"] == "failed" and "也挂了" in decision["reason"]


async def test_persona_examples_reach_the_prompt(world: World) -> None:
    persona = world.brain.bots["qq:10001"].channels
    persona_id = next(iter(persona.values())).persona_id
    await world.repo.update_persona(persona_id, examples="有人：累\n你：那就睡")
    await world.brain.load()
    world.providers["10001"].queue("好")
    await world.say("阿鲸？")
    await world.settle(1)
    system = world.providers["10001"].requests[-1][0].content
    assert "说话的样子" in system and "你：那就睡" in system
