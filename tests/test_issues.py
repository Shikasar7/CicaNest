"""问题记录: the 记录官 reads its group's new messages with its model and writes down what the bots could
not solve, each with who said it and when; the digest goes by private message at the set times; one bot
per group is the 记录官."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest.core.brain import Brain
from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.core.issues import RECORDER_HINT, IssueKeeper, digest_text, due_slots, parse_issues
from nest.llm.base import ChatMessage, Completion
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotChannel, BotConfig, BotRepo
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from tests.conftest import TOKEN

GROUP = ChannelRef("qq", "20000", "group", "42")


@dataclass
class FakeRuntime:
    answers: list[str]
    requests: list[list[ChatMessage]] = field(default_factory=list)

    async def complete(self, messages: list[ChatMessage], **_: Any) -> tuple[Completion, int]:
        self.requests.append(messages)
        return Completion(self.answers.pop(0), 1.0), 1


@dataclass
class FakeBrain:
    bots: dict[str, BotConfig]
    runtime: FakeRuntime
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    our_accounts: set[str] = field(default_factory=lambda: {"20000", "30000"})
    default_provider: str | None = "m"

    @property
    def providers(self) -> dict[str, FakeRuntime]:
        return {"m": self.runtime}

    async def send_private(self, account: str, user: str, text: str) -> bool:
        self.sent.append((account, user, text))
        return True


class FakeHub:
    def __init__(self) -> None:
        self.states = {"30000": "connected", "20000": "connected"}

    def account_views(self) -> list[dict[str, Any]]:
        return [
            {"id": f"qq:{n}", "self_id": n, "enabled": True, "status": {"state": state}}
            for n, state in self.states.items()
        ]


class Events:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []

    def publish(self, kind: str, data: dict) -> None:
        self.seen.append((kind, data))


async def say(store: MessageStore, n: int, who: str, text: str, at: float) -> dict[str, Any]:
    event = MessageEvent(
        GROUP,
        f"m{n}",
        who,
        {"20000": "阿克", "30000": "阿鲸"}.get(who, f"群友{who}"),
        (Segment("text", {"text": text}),),
        text,
        at,
        from_self=who == "20000",
    )
    row = await store.save(event)
    assert row is not None
    return row


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = await Database.open(tmp_path / "nest.db")
    yield database
    await database.close()


async def keeper_for(db: Database, answers: list[str]) -> tuple[IssueKeeper, FakeBrain, MessageStore]:
    store = MessageStore(db)
    await upsert_account(
        db, platform="qq", self_id="20000", name="阿克", http_url="http://x", ws_url="ws://x/"
    )
    channel_id = await store.channel_id(GROUP)
    channel = BotChannel(channel_id, GROUP, "Demo群", True, "occasional", "", "", 12, recorder=True)
    bot = BotConfig("qq:20000", "qq:20000", "20000", "阿克", (), "m", "live", {channel_id: channel})
    brain = FakeBrain({bot.id: bot}, FakeRuntime(answers))
    settings = ["123456789", ["21:00"]]

    async def read() -> tuple[str, list[str]]:
        return settings[0], settings[1]  # type: ignore[return-value]

    keeper = IssueKeeper(db, store, brain, FakeHub(), Events(), read)
    return keeper, brain, store


async def test_the_recorder_writes_down_what_the_bots_could_not_solve(db: Database) -> None:
    now = time.time()
    first = json.dumps({"issues": []})
    keeper, brain, store = await keeper_for(db, [first])
    bot, channel = keeper.recorders()[0]
    old = await say(store, 0, "555", "两天前的事", now - 3 * 86400)
    asked = await say(store, 1, "555", "@阿鲸 DemoApp 怎么导出聊天记录？", now - 300)
    await say(store, 2, "30000", "这个我不太清楚诶", now - 290)
    await say(store, 3, "666", "哈哈哈", now - 200)
    noted = await say(store, 4, "666", "@阿克 记一下：图片上传老是失败", now - 100)

    assert await keeper.review(bot, channel) == 0  # the model found nothing this time
    prompt = brain.runtime.requests[0][1].content
    assert "两天前的事" not in prompt.split("新消息：")[1]  # a new 记录官 starts with the last day
    assert f"[{asked['id']}]" in prompt and "阿鲸（bot）：这个我不太清楚诶" in prompt
    assert await keeper.repo.read_to(channel.channel_id) is not None

    await say(store, 5, "777", "没人知道吗", now - 50)
    brain.runtime.answers.append(
        json.dumps(
            {
                "issues": [
                    {
                        "message_id": asked["id"],
                        "kind": "unsolved",
                        "summary": "问怎么导出聊天记录，没答上",
                        "ai_reply": "说不清楚",
                    },
                    {"message_id": noted["id"], "kind": "bug", "summary": "图片识别经常超时"},
                    {"message_id": old["id"], "kind": "bug", "summary": "不在这批里"},
                    {"message_id": asked["id"], "kind": "joke", "summary": "不认识的类型"},
                ]
            },
            ensure_ascii=False,
        )
    )
    await keeper.repo.set_read_to(channel.channel_id, old["id"])  # read again from the start
    assert await keeper.review(bot, channel) == 2
    issues = await keeper.repo.issues()
    assert [(i["kind"], i["speaker"], i["said_at"]) for i in issues] == [
        ("bug", "群友666", noted["sent_at"]),
        ("unsolved", "群友555", asked["sent_at"]),
    ]
    assert issues[1]["quote"] == "@阿鲸 DemoApp 怎么导出聊天记录？" and issues[1]["ai_reply"] == "说不清楚"
    assert issues[0]["group_name"] == "Demo群" and issues[0]["bot_id"] == "qq:20000"

    calls = len(brain.runtime.requests)
    assert await keeper.review(bot, channel) == 0
    assert len(brain.runtime.requests) == calls  # nothing new: no call

    await say(store, 6, "555", "还有一个问题", now - 10)
    brain.runtime.answers.append("我看不出来")  # not JSON: read again next time
    mark = await keeper.repo.read_to(channel.channel_id)
    assert await keeper.review(bot, channel) == 0
    assert await keeper.repo.read_to(channel.channel_id) == mark
    brain.runtime.answers.append(json.dumps({"issues": []}))
    await keeper.review(bot, channel)
    assert "图片识别经常超时" in brain.runtime.requests[-1][1].content  # already written down: shown


async def test_the_digest_goes_from_the_recorder_and_only_once(db: Database) -> None:
    now = time.time()
    keeper, brain, store = await keeper_for(db, [json.dumps({"issues": []})])
    asked = await say(store, 1, "555", "怎么导出聊天记录？", now - 60)
    await keeper.repo.add(
        group_id="42",
        group_name="Demo群",
        bot_id="qq:20000",
        message_id=asked["id"],
        said_at=asked["sent_at"],
        speaker="群友555",
        speaker_id="555",
        kind="unsolved",
        summary="问怎么导出聊天记录，没答上",
        quote="怎么导出聊天记录？",
        ai_reply="说不清楚",
    )
    sent, detail = await keeper.send_digest()
    assert sent and "共 1 条" in detail
    [(account, to, text)] = brain.sent
    assert (account, to) == ("qq:20000", "123456789")  # the 记录官 first
    assert "【没解决】" in text and "Demo群，群友555：问怎么导出聊天记录，没答上（bot 回：说不清楚）" in text
    brain.runtime.answers.append(json.dumps({"issues": []}))
    sent, detail = await keeper.send_digest()
    assert not sent and "没有新记下的问题" in detail and len(brain.sent) == 1


async def test_a_recorder_that_gets_no_messages_leaves_the_digest_to_another_bot(db: Database) -> None:
    now = time.time()
    keeper, brain, store = await keeper_for(db, [json.dumps({"issues": []})])
    asked = await say(store, 1, "555", "怎么导出聊天记录？", now - 60)
    await keeper.repo.add(
        group_id="42",
        group_name="Demo群",
        bot_id="qq:20000",
        message_id=asked["id"],
        said_at=asked["sent_at"],
        speaker="群友555",
        speaker_id="555",
        kind="unsolved",
        summary="问怎么导出聊天记录，没答上",
        quote="怎么导出聊天记录？",
        ai_reply="",
    )
    keeper._hub.states["20000"] = "deaf"  # type: ignore[attr-defined]  # 假在线: nest.core.deaf
    sent, _ = await keeper.send_digest()
    assert sent and [account for account, _, _ in brain.sent] == ["qq:30000"]


def test_digest_times_and_parsing() -> None:
    day = time.localtime()
    at = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 21, 5, 0, 0, 0, -1))
    today = time.strftime("%Y-%m-%d", time.localtime(at))
    assert due_slots(["08:00", "21:00"], at, set()) == [f"{today} 21:00"]
    assert due_slots(["21:00"], at, {f"{today} 21:00"}) == []
    assert due_slots(["20:00"], at, set()) == []  # missed by more than half an hour
    assert parse_issues('```json\n{"issues": [{"message_id": 1}, "x"]}\n```') == [{"message_id": 1}]
    issue = {
        "said_at": at,
        "group_name": "",
        "group_id": "42",
        "speaker": "",
        "kind": "request",
        "summary": "想要夜间模式",
        "ai_reply": "",
    }
    text = digest_text([issue] * 17)
    assert "群 42，有人：想要夜间模式" in text and "……还有 2 条" in text


async def test_one_recorder_per_group(db: Database) -> None:
    repo, store = BotRepo(db), MessageStore(db)
    channels = {}
    for uin in ("20000", "30000"):
        await upsert_account(db, platform="qq", self_id=uin, name=uin, http_url="http://x", ws_url="ws://x/")
        await repo.upsert_bot(account_id=f"qq:{uin}", name=uin, aliases=[], provider_id=None)
        channels[uin] = await store.channel_id(ChannelRef("qq", uin, "group", "42"))
    other = await store.channel_id(ChannelRef("qq", "30000", "group", "43"))
    await repo.set_channel("qq:30000", other, recorder=True)
    await repo.set_channel("qq:20000", channels["20000"], recorder=True)
    await repo.set_channel("qq:30000", channels["30000"], recorder=True)
    recorders = {
        (b.id, ch.channel.id) for b in await repo.bots() for ch in b.channels.values() if ch.recorder
    }
    assert recorders == {("qq:30000", "42"), ("qq:30000", "43")}  # group 42 changed hands; 43 untouched


async def test_the_issues_api(client: httpx.AsyncClient) -> None:
    auth = {"authorization": f"Bearer {TOKEN}"}
    view = (await client.get("/api/issues", headers=auth)).json()
    assert view["issues"] == [] and view["digest"] == {"to": "", "times": ["21:00"]}
    body = {"to": "123456789", "times": ["21:00", "08:30", "08:30"]}
    saved = (await client.put("/api/issues/digest", json=body, headers=auth)).json()
    assert saved == {"to": "123456789", "times": ["08:30", "21:00"]}
    for bad in ({"times": ["25:00"]}, {"times": []}, {"to": "12ab"}):
        assert (await client.put("/api/issues/digest", json=bad, headers=auth)).status_code == 422
    review = (await client.post("/api/issues/review", headers=auth)).json()
    assert not review["ok"] and "问题记录官" in review["detail"]
    digest = (await client.post("/api/issues/digest", headers=auth)).json()
    assert digest == {"ok": False, "detail": "上次汇总以来没有新记下的问题，没有发"}
    repo = client.app.state.issues.repo  # type: ignore[attr-defined]
    issue_id = await repo.add(
        group_id="42",
        group_name="",
        bot_id="qq:1",
        message_id=7,
        said_at=time.time(),
        speaker="甲",
        speaker_id="1",
        kind="feedback",
        summary="希望回复快一点",
        quote="",
        ai_reply="",
    )
    assert (await client.patch(f"/api/issues/{issue_id}", json={"status": "done"}, headers=auth)).json()["ok"]
    view = (await client.get("/api/issues?status=open", headers=auth)).json()
    assert view["issues"] == [] and view["counts"] == {"open": 0, "done": 1, "ignored": 0}
    assert view["groups"] == [{"group_id": "42", "name": ""}]
    assert (await client.delete(f"/api/issues/{issue_id}", headers=auth)).json()["ok"]
    assert (await client.delete(f"/api/issues/{issue_id}", headers=auth)).status_code == 404


def test_the_recorder_knows_it_in_its_prompt() -> None:
    channel = BotChannel(1, GROUP, "Demo群", True, "occasional", "", "你是阿克。", 12)
    bot = BotConfig("qq:20000", "qq:20000", "20000", "阿克", (), "m", "live", {1: channel})
    assert Brain._persona(bot, channel) == "你是阿克。"
    recorder = replace(channel, recorder=True, persona="")
    assert Brain._persona(bot, recorder) == f"你是群里的阿克。\n{RECORDER_HINT}"
