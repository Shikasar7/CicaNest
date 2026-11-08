"""M3 live sending: the OneBot send call, its failure modes, and the brain's live path."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time

from nest.adapters.onebot11.adapter import OneBotAdapter, send_timeout
from nest.core.brain import _fit
from nest.core.events import ActionResult, ChannelRef, MessageEvent, Segment, SendMessage
from nest.llm.base import Completion
from nest.napcat import onebot_config
from tests.fakes.onebot import ActionFailed, FakeOneBot
from tests.helpers import eventually
from tests.test_brain import World, set_activity, world  # noqa: F401 (world is a fixture)

GROUP = ChannelRef("qq", "10001", "group", "42")


async def connected(bot: FakeOneBot, **kwargs) -> OneBotAdapter:  # type: ignore[no-untyped-def]
    adapter = OneBotAdapter(
        account="10001",
        http_url=bot.http_url,
        ws_url=bot.ws_url,
        http_token=bot.token,
        ws_token=bot.token,
        backoff_initial=0.05,
        **kwargs,
    )
    events: list = []

    async def emit(event) -> None:  # type: ignore[no-untyped-def]
        events.append(event)

    adapter.events = events  # type: ignore[attr-defined]
    await adapter.start(emit)
    await eventually(lambda: adapter.status().state == "connected" and bot.connected_clients == 1)
    return adapter


async def test_send_with_quote_reaches_the_bridge_and_echo_counts_as_ours() -> None:
    async with FakeOneBot(self_id=10001) as bot:
        adapter = await connected(bot)
        try:
            segments = (Segment("reply", {"id": "55"}), Segment("text", {"text": "嗨"}))
            result = await adapter.perform(SendMessage(GROUP, segments, "k1"))
            assert result.state == "succeeded" and result.message_id
            assert bot.calls_to("send_group_msg") == [
                {
                    "group_id": 42,
                    "message": [
                        {"type": "reply", "data": {"id": "55"}},
                        {"type": "text", "data": {"text": "嗨"}},
                    ],
                }
            ]
            echo = await eventually(lambda: [e for e in adapter.events if getattr(e, "from_self", False)])
            assert echo[0].message_id == result.message_id and adapter.sent_by_us(result.message_id)
            adapter._sending_until = 0  # the in-flight window has passed
            assert adapter.sent_by_us(result.message_id) and not adapter.sent_by_us("someone-else")
        finally:
            await adapter.stop()


async def test_rejected_send_is_failed_with_the_bridge_reason() -> None:
    async with FakeOneBot(self_id=10001) as bot:

        def muted(_params):  # type: ignore[no-untyped-def]
            raise ActionFailed(1200, "群里被禁言了")

        bot.handlers["send_group_msg"] = muted
        adapter = await connected(bot)
        try:
            result = await adapter.perform(SendMessage(GROUP, (Segment("text", {"text": "x"}),), "k"))
            assert result.state == "failed" and "被禁言" in (result.error or "")
        finally:
            await adapter.stop()


async def test_a_stuck_send_is_remembered_until_one_works() -> None:
    async with FakeOneBot(self_id=10001) as bot:
        answers = [
            ActionFailed(200, "EventChecker Failed: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg"),
            ActionFailed(1200, "群里被禁言了"),
        ]

        def send(_params):  # type: ignore[no-untyped-def]
            if answers:
                raise answers.pop(0)
            return {"message_id": 1}

        bot.handlers["send_group_msg"] = send
        adapter = await connected(bot)
        message = SendMessage(GROUP, (Segment("text", {"text": "x"}),), "k")
        try:
            assert (await adapter.perform(message)).state == "failed"
            stuck = adapter.stuck_send_at
            assert stuck > 0
            assert (await adapter.perform(message)).state == "failed"
            assert adapter.stuck_send_at == stuck  # another refusal says nothing either way
            assert (await adapter.perform(message)).state == "succeeded"
            assert adapter.stuck_send_at == 0
        finally:
            await adapter.stop()


async def test_slow_bridge_makes_the_result_unknown_not_failed() -> None:
    async with FakeOneBot(self_id=10001) as bot:

        async def slow(_params):  # type: ignore[no-untyped-def]
            await asyncio.sleep(1)
            return {"message_id": 1}

        bot.handlers["send_group_msg"] = slow
        adapter = await connected(bot, request_timeout=0.3)
        try:
            result = await adapter.perform(SendMessage(GROUP, (Segment("text", {"text": "x"}),), "k"))
            assert result.state == "unknown" and "不确定" in (result.error or "")
        finally:
            await adapter.stop()


async def test_offline_account_does_not_send() -> None:
    async with FakeOneBot(self_id=10001) as bot:
        adapter = OneBotAdapter(account="10001", http_url=bot.http_url, ws_url=bot.ws_url)  # never started
        result = await adapter.perform(SendMessage(GROUP, (Segment("text", {"text": "x"}),), "k"))
        assert result.state == "failed" and "不在线" in (result.error or "")
        assert not bot.calls_to("send_group_msg")
        await adapter.stop()


def test_a_big_picture_gets_longer_to_go_out() -> None:
    words = (Segment("text", {"text": "x" * 500}),)
    picture = (Segment("image", {"file": "base64://" + "x" * 13_000_000}),)
    assert send_timeout(10, words) == 10
    assert send_timeout(10, picture) > 30


def test_a_napcat_nest_sets_up_reports_what_the_bot_says() -> None:
    [server] = onebot_config("http", "ws")["network"]["websocketServers"]
    assert server["reportSelfMessage"] is True


class FakeAdapter:
    def __init__(self, result: ActionResult | None = None) -> None:
        self.result = result or ActionResult("succeeded", message_id="900")
        self.actions: list[SendMessage] = []
        self.ours: set[str] = set()

    async def perform(self, action: SendMessage) -> ActionResult:
        self.actions.append(action)
        return self.result

    def sent_by_us(self, message_id: str) -> bool:
        return message_id in self.ours


async def go_live(w: World, uin: str = "10001", result: ActionResult | None = None) -> FakeAdapter:
    adapter = FakeAdapter(result)
    w.brain.adapters = {f"qq:{uin}": adapter}
    await w.repo.set_mode(f"qq:{uin}", "live")
    await w.brain.load()
    return adapter


async def test_live_mention_is_sent_and_recorded_as_replied(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    world.providers["10001"].queue("来了来了")
    await world.say("阿鲸在吗", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied" and decision["reason"] == "已发送"
    assert decision["reply_message_id"] == "900"
    [action] = adapter.actions
    assert action.channel == GROUP and [s.type for s in action.segments] == ["text"]
    assert action.segments[0].data["text"] == "来了来了"


async def lines_sent(w: World, adapter: FakeAdapter) -> list[str]:
    await w.say("阿鲸在吗", accounts=["10001"])
    await w.settle(1)
    return [s.data["text"] for action in adapter.actions for s in action.segments if s.type == "text"]


async def test_a_reply_never_comes_as_more_messages_than_the_group_allows(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    channel = await world.store.channel_id(GROUP)
    await world.repo.set_channel("qq:10001", channel, reply_lines=2)
    await world.brain.load()
    world.providers["10001"].queue("来了\n刚刚在打哈欠\n找我干嘛")
    assert await lines_sent(world, adapter) == ["来了", "刚刚在打哈欠 找我干嘛"]  # nothing said is lost
    assert "可以分两行" in world.providers["10001"].requests[0][0].content
    assert "一行一条，最多两条" in world.providers["10001"].requests[0][1].content


async def test_only_the_share_of_replies_set_may_come_as_several_messages(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    channel = await world.store.channel_id(GROUP)
    await world.repo.set_channel("qq:10001", channel, split_rate=30)
    await world.brain.load()
    world.brain.chance = lambda: 0.3
    world.providers["10001"].queue("来了\n找我干嘛")
    assert await lines_sent(world, adapter) == ["来了 找我干嘛"]
    assert "这次只说一句" in world.providers["10001"].requests[0][0].content
    assert "只写一行" in world.providers["10001"].requests[0][1].content


async def test_within_the_share_a_reply_may_still_be_split(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    channel = await world.store.channel_id(GROUP)
    await world.repo.set_channel("qq:10001", channel, split_rate=30)
    await world.brain.load()
    world.brain.chance = lambda: 0.29
    world.providers["10001"].queue("来了\n找我干嘛")
    assert await lines_sent(world, adapter) == ["来了", "找我干嘛"]


def test_a_sticker_counts_as_one_of_the_messages() -> None:
    sticker = {"id": 1}
    assert _fit("一\n二\n三", sticker, 3, 3) == ("一\n二 三", sticker)
    assert _fit("一\n二", sticker, 1, 3) == ("一 二", sticker)  # one sentence, and its sticker
    assert _fit("一\n二", sticker, 1, 1) == ("一 二", None)  # room for one: the words
    assert _fit("", sticker, 1, 1) == ("", sticker)  # a sticker alone is one message
    assert _fit("一\n二\n三", None, 3, 3) == ("一\n二\n三", None)


async def test_a_reply_of_several_lines_goes_out_as_several_messages(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    world.providers["10001"].queue("来了\n刚刚在打哈欠\n找我干嘛")
    await world.say("阿鲸在吗", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied" and decision["reason"] == "已发送 3 条"
    assert decision["reply"] == "来了\n刚刚在打哈欠\n找我干嘛"
    texts = [[s.data["text"] for s in action.segments if s.type == "text"] for action in adapter.actions]
    assert texts == [["来了"], ["刚刚在打哈欠"], ["找我干嘛"]]


async def test_a_later_line_that_fails_leaves_the_reply_sent(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    results = iter(
        [ActionResult("succeeded", message_id="900"), ActionResult("failed", error="群里被禁言了")]
    )

    async def perform(action: SendMessage) -> ActionResult:
        adapter.actions.append(action)
        return next(results)

    adapter.perform = perform  # type: ignore[method-assign]
    world.providers["10001"].queue("第一条\n第二条\n第三条")
    await world.say("阿鲸", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied" and decision["reply_message_id"] == "900"
    assert decision["reason"] == "已发送前 1 条；第 2 条没发出：群里被禁言了"
    assert len(adapter.actions) == 2


async def test_live_reply_quotes_its_target_after_unrelated_chatter(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    provider = world.providers["10001"]
    provider.delay = 0.3
    provider.queue("回你")
    target = await world.say("阿鲸 你怎么看", accounts=["10001"])
    await eventually(lambda: len(provider.requests) == 1)
    for i, who in enumerate(("8", "9", "11")):
        await world.say(f"别人的话{i}", sender=who, name=f"路人{i}", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied" and decision["quote"]
    first = adapter.actions[0].segments[0]
    assert (first.type, dict(first.data)) == ("reply", {"id": target})


async def test_failed_and_unknown_sends_are_failed_decisions(world: World) -> None:  # noqa: F811
    await go_live(world, result=ActionResult("failed", error="群里被禁言了"))
    world.providers["10001"].queue("一句话")
    await world.say("阿鲸", accounts=["10001"])
    [first] = await world.settle(1)
    assert first["outcome"] == "failed" and first["reason"] == "发送失败：群里被禁言了"

    await go_live(world, result=ActionResult("unknown", error="发送请求中途出错"))
    world.providers["10001"].queue("又一句")
    await asyncio.sleep(0.6)  # past the cooldown
    await world.say("阿鲸！", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[1]["outcome"] == "failed" and decisions[1]["reason"].startswith(
        "发送结果不确定，没有重发"
    )


async def test_switching_back_to_observe_mid_generation_does_not_send(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    provider = world.providers["10001"]
    provider.delay = 0.3
    provider.queue("差点发出去")
    await world.say("阿鲸在吗", accounts=["10001"])
    await eventually(lambda: len(provider.requests) == 1)
    await world.repo.set_mode("qq:10001", "observe")
    await world.brain.load()
    [decision] = await world.settle(1)
    assert decision["outcome"] == "would_reply" and "切回了只看不说" in decision["reason"]
    assert not adapter.actions


async def test_own_messages_not_sent_by_nest_are_flagged_as_another_program(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    adapter.ours.add("m1")  # world.say numbers messages m1, m2…
    await world.say("nest 自己发的", sender="10001", name="阿鲸", accounts=["10001"])
    assert "qq:10001" not in world.brain.foreign_at
    await world.say("别的程序发的", sender="10001", name="阿鲸", accounts=["10001"])
    assert "qq:10001" in world.brain.foreign_at
    await eventually(lambda: any(kind == "foreign" and data["live"] for kind, data in world.events))


async def test_occasional_live_chime_in_still_goes_to_one_bot(world: World) -> None:  # noqa: F811
    await set_activity(world, "occasional")
    adapters = {f"qq:{uin}": FakeAdapter() for uin in ("10001", "10002", "10003")}
    world.brain.adapters = adapters
    for uin in ("10001", "10002", "10003"):
        await world.repo.set_mode(f"qq:{uin}", "live")
        # Twice: on a slow machine the next message may arrive while the reply is being written, and the
        # bot writes it again with that message in view.
        world.providers[uin].queue("我来接", "我来接")
    await world.brain.load()
    for text in ("今天吃啥", "火锅吧", "又是火锅"):
        await world.say(text)
    decisions = await world.settle(3)
    assert sorted(d["outcome"] for d in decisions) == ["replied", "suppressed", "suppressed"]
    assert sum(len(a.actions) for a in adapters.values()) == 1


async def test_observing_bots_never_take_a_live_bots_chime_in_round(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    await set_activity(world, "occasional")
    for provider in world.providers.values():
        provider.queue("我来接一句")
    for text in ("今天好热", "热死了", "空调开起来"):
        await world.say(text, accounts=["10002", "10003", "10001"])  # the observing bots come first
    decisions = await world.settle(3)
    outcomes = {d["bot_id"]: d["outcome"] for d in decisions}
    assert outcomes["qq:10001"] == "replied" and len(adapter.actions) == 1
    # The two observing bots still take turns among themselves.
    assert sorted([outcomes["qq:10002"], outcomes["qq:10003"]]) == ["suppressed", "would_reply"]


# -- private chats ----------------------------------------------------------------------------------


async def private_with(w: World, user: str = "555", uin: str = "10001") -> ChannelRef:
    """Let the bot answer `user`'s private messages, the way 添加私聊对象 does."""
    ref = ChannelRef("qq", uin, "private", user)
    channel = await w.store.channel_id(ref)
    await w.repo.set_channel(f"qq:{uin}", channel, enabled=True, activity="mentions", cooldown_seconds=0)
    await w.brain.load()
    return ref


async def dm(w: World, text: str, *, user: str = "555", uin: str = "10001") -> None:
    w.counter += 1
    event = MessageEvent(
        channel=ChannelRef("qq", uin, "private", user),
        message_id=f"p{w.counter}",
        sender_id=user,
        sender_name="小明",
        segments=(Segment("text", {"text": text}),),
        text=text,
        timestamp=time.time(),
        from_self=False,
    )
    row = await w.store.save(event)
    assert row is not None
    await w.brain.on_message(event, row)


async def test_a_private_message_needs_no_name_and_gets_one_long_answer(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    ref = await private_with(world)
    answer = "能的。\n" + "第一步先看官方文档，" * 12 + "\n有问题再来问我"
    world.providers["10001"].queue(answer)
    await dm(world, "今天的模型新闻能讲讲吗")  # no name, no @: a private message is always to the bot
    [decision] = await world.settle(1)
    assert decision["trigger"] == "mention" and decision["outcome"] == "replied"
    [action] = adapter.actions  # several lines, one message, no quote
    assert action.channel == ref and [s.type for s in action.segments] == ["text"]
    assert action.segments[0].data["text"] == answer
    system, user = world.providers["10001"].requests[0]
    assert "现在是 QQ 私聊" in system.content and "要接的是「新消息」" not in system.content
    assert user.content.endswith("新消息：\n小明：今天的模型新闻能讲讲吗\n\n只输出你要回的话，或 SKIP。")
    assert "【点到你】" not in user.content


async def test_only_people_added_get_private_replies(world: World) -> None:  # noqa: F811
    await go_live(world)
    await private_with(world, user="555")
    await dm(world, "你好", user="666")  # a stranger
    await asyncio.sleep(0.4)
    assert await world.decisions() == [] and not world.providers["10001"].requests
    # The bot's own words about tokens are fine in a private chat; an over-long answer is still dropped.
    world.providers["10001"].queue("token 就是模型读字的单位")
    await dm(world, "token 是什么")
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied" and decision["reply"] == "token 就是模型读字的单位"
    world.providers["10001"].queue(
        "长" * 250 + "\n" + "长" * 250 + "\n" + "长" * 250 + "\n" + "长" * 250 + "\n长"
    )
    await dm(world, "再详细点")
    decisions = await world.settle(2)
    assert [(d["outcome"], d["reason"][:4]) for d in decisions] == [
        ("replied", "已发送"),
        ("skipped", "回复太长"),
    ]


async def answering(w: World, *, kind: str = "codex", uin: str = "10001") -> None:
    """Turn 答题 on for the bot in the group; `kind`: its model's kind (only codex can search)."""
    runtime = w.brain.providers.get(f"model-{uin}")
    assert runtime is not None
    runtime.config = dataclasses.replace(runtime.config, kind=kind)
    bot = w.brain.bots[f"qq:{uin}"]
    for channel_id in bot.channels:
        await w.repo.set_channel(bot.id, channel_id, answer_questions=True)
    await w.brain.load()


QUESTION = '{"kind": "question", "topic": "Python 最新版本是多少"}'


async def test_a_question_is_searched_and_answered_in_one_quoted_message(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    await answering(world)
    world.providers["10001"].queue(
        f"好的：{QUESTION}",  # sorting: chatter around the JSON is tolerated
        Completion(
            text="**结论**：最新的是 3.14。([python.org](https://python.org/x))\n"
            "- 10 月 7 日发布，来源：[官网](https://python.org/x)\n"
            "- 3.15 还在测试",
            latency_ms=1,
            searches=2,
            sources=(("Python", "https://python.org/x?utm_source=openai"),),
        ),
    )
    mid = await world.say("阿鲸 Python 最新版本是多少？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied"
    assert decision["reason"] == "已发送（正经问题；联网查了 2 次）"
    [action] = adapter.actions  # several lines, one message, quoting the question
    assert [s.type for s in action.segments] == ["reply", "text"]
    assert action.segments[0].data["id"] == mid
    # Markdown and the reference links are gone.
    assert action.segments[1].data["text"] == "结论：最新的是 3.14。\n· 10 月 7 日发布，\n· 3.15 还在测试"
    assert world.providers["10001"].searched == [False, True]  # sorting never searches
    sort_system, _ = world.providers["10001"].requests[0]
    assert '"kind": "question"' in sort_system.content
    system, user = world.providers["10001"].requests[1]
    assert "先联网搜索再回答" in system.content and "现在是 20" in system.content
    assert "要回答的是：Python 最新版本是多少" in user.content


async def test_with_answering_on_chat_stays_chat(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    await answering(world)
    # No question word: chat, without asking the model what it is.
    world.providers["10001"].queue("在这儿\n找我干嘛")
    await world.say("阿鲸 出来玩", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["reason"] == "已发送 2 条（当闲聊：没有问句）"  # a chat line, sent the usual way
    assert [a.segments[0].data["text"] for a in adapter.actions] == ["在这儿", "找我干嘛"]
    assert world.providers["10001"].searched == [False]
    system, user = world.providers["10001"].requests[0]
    assert "点名找你" not in system.content and "这批消息点到你了" in user.content  # the plain group prompt
    # A question word, but the model sorts it as chat: the plain prompt again, nothing searched.
    await asyncio.sleep(0.6)  # past the cooldown
    world.providers["10001"].queue('{"kind": "chat"}', "在呢")
    await world.say("阿鲸在吗", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[-1]["reason"] == "已发送（当闲聊）"
    assert world.providers["10001"].searched == [False, False, False]
    system, _ = world.providers["10001"].requests[2]
    assert "点名找你" not in system.content
    # Unprompted, a bot with 答题 on chats as before: no sorting, the plain prompt.
    await set_activity(world, "active", ["10001"])
    await asyncio.sleep(0.6)
    await world.say("今天好冷", accounts=["10001"])
    await world.settle(3)
    assert len(world.providers["10001"].requests) == 4
    system, _ = world.providers["10001"].requests[3]
    assert "点名找你" not in system.content


async def test_a_model_without_search_answers_from_memory_and_unreadable_sorting_lets_it_decide(
    world: World,  # noqa: F811
) -> None:
    adapter = await go_live(world)
    await answering(world, kind="openai_chat")
    world.providers["10001"].queue(QUESTION, "我记得是 3.14，拿不准")
    await world.say("阿鲸 Python 最新版本是多少？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["reason"] == "已发送（正经问题）"
    assert [s.type for s in adapter.actions[0].segments] == ["reply", "text"]  # an answer quotes
    assert world.providers["10001"].searched == [False, False]
    system, _ = world.providers["10001"].requests[1]
    assert "你现在不能联网" in system.content
    # The model's sorting makes no sense: the answer prompt lets it tell chat from a question itself.
    await asyncio.sleep(0.6)
    world.providers["10001"].queue("这是个问题吧", "就是 3.14")
    await world.say("阿鲸 那 3.13 呢？", accounts=["10001"])
    decisions = await world.settle(2)
    assert decisions[-1]["reason"] == "已发送（没分出是不是正经问题）"
    system, user = world.providers["10001"].requests[3]
    assert "点名找你" in system.content and "要回答的是" not in user.content


async def test_a_question_about_the_chat_log_is_answered_from_what_was_said(world: World) -> None:  # noqa: F811
    adapter = await go_live(world)
    await answering(world)
    card = {
        "app": "com.tencent.tuwen.lua",
        "prompt": "[分享]一个好笑的视频",
        "meta": {"news": {"title": "一个好笑的视频", "jumpUrl": "https://www.xiaohongshu.com/item/1"}},
    }
    await world.say(
        "[卡片]",
        sender="8",
        name="小红",
        accounts=["10001"],
        segments=(Segment("json", {"data": json.dumps(card, ensure_ascii=False)}),),
    )
    await world.say("看这个 https://example.com/a", sender="9", name="小蓝", accounts=["10001"])
    await world.say("小红书上那个真好笑", sender="9", name="小蓝", accounts=["10001"])
    await world.say("今天吃什么", sender="9", name="小蓝", accounts=["10001"])
    world.providers["10001"].queue(
        '{"kind": "history", "topic": "谁发的小红书链接", "keywords": ["小红书"], "links": true}',
        "是小红发的：https://www.xiaohongshu.com/item/1",
    )
    await world.say("阿鲸 上次谁发的那个小红书链接？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied"
    assert decision["reason"] == "已发送（问以前的聊天，翻到 3 条记录）"
    assert adapter.actions[0].segments[1].data["text"] == "是小红发的：https://www.xiaohongshu.com/item/1"
    assert world.providers["10001"].searched == [False, False]  # the chat log, not the web
    system, user = world.providers["10001"].requests[1]
    assert "只根据下面翻到的聊天记录回答" in system.content
    found = user.content.split("\n\n")[0]
    assert found.startswith("按「小红书」和链接翻了以前的聊天记录")
    assert "小红：[分享] 一个好笑的视频 https://www.xiaohongshu.com/item/1" in found
    assert "小蓝：看这个 https://example.com/a" in found and "小蓝：小红书上那个真好笑" in found
    assert "今天吃什么" not in found


async def test_searching_stops_at_the_hourly_limit(world: World) -> None:  # noqa: F811
    await answering(world)
    now = time.time()
    world.brain._searched["qq:10001"] = [now - 4000] + [now - 60] * world.brain.policy.answer_hourly_limit
    world.providers["10001"].queue(QUESTION, "我记得是 3.14，拿不准")
    await world.say("阿鲸 Python 最新版本是多少？", accounts=["10001"])
    await world.settle(1)
    assert world.providers["10001"].searched == [False, False]
    assert len(world.brain._searched["qq:10001"]) == world.brain.policy.answer_hourly_limit


async def test_a_private_chat_keeps_a_note_of_what_left_the_context(world: World) -> None:  # noqa: F811
    await go_live(world)
    ref = await private_with(world)
    channel = await world.store.channel_id(ref)
    world.brain.policy = dataclasses.replace(world.brain.policy, context_messages=3, memory_batch=2)
    world.providers["10001"].default = "好"
    for n in range(4):
        await dm(world, f"第{n}件事")
        await world.settle(n + 1)
    world.providers["10001"].default = "对方喜欢爬山"
    await dm(world, "第5件事")
    summary = await eventually(lambda: note_of(world, channel))
    assert summary == "对方喜欢爬山"
    asked = next(r for r in world.providers["10001"].requests if "新的聊天" in r[-1].content)
    assert "第0件事" in asked[-1].content
    await dm(world, "还记得吗")
    await eventually(lambda: prompted_with_note(world))


async def note_of(world: World, channel: int) -> str:  # noqa: F811
    return (await world.store.memory(channel))[0]


async def prompted_with_note(world: World) -> bool:  # noqa: F811
    return any(
        "更早聊过的" in r[-1].content and "喜欢爬山" in r[-1].content
        for r in world.providers["10001"].requests
    )
