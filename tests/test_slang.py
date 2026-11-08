"""黑话: learning a group's terms (checked against what was really said), when learning runs, explaining the
terms in a prompt, and the console API. No real model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from nest.core.slang import SlangLearner, check_term, pick_terms
from nest.llm.base import ProviderError
from nest.storage.slang import GroupFeatureStore
from tests.fakes.llm import FakeProvider
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import GROUP, World, world  # noqa: F401 (fixture)

GROUP_KEY = f"qq:group:{GROUP}"


def terms(*items: dict[str, Any]) -> str:
    return json.dumps({"terms": list(items)}, ensure_ascii=False)


@dataclass
class Kit:
    world: World
    learner: SlangLearner


@pytest.fixture
async def kit(world: World) -> AsyncIterator[Kit]:  # noqa: F811
    learner = SlangLearner(
        world.db,
        world.store,
        world.brain.providers,
        world.broadcaster,
        default_provider=world.brain.default_provider,
        our_accounts=world.brain.our_accounts,
        first_delay=999,
    )
    learner.config = learner.config.__class__(min_new=4, forced_min=2, min_hours=12)
    world.brain.slang = learner
    yield Kit(world, learner)


def whale(world: World) -> FakeProvider:  # noqa: F811
    return world.providers["10001"]  # the first bot's model: what learning uses by default


async def chat(world: World, *lines: str) -> None:  # noqa: F811
    for i, line in enumerate(lines):
        await world.say(line, sender=str(100 + i % 3), name=f"群友{i % 3}", accounts=["10001"])


# -- checking what the model proposes ------------------------------------------------------------------


def test_a_term_must_really_occur_twice_and_get_a_real_example() -> None:
    lines = ["今天又摸鱼了", "摸鱼使我快乐", "老板来了"]
    good, _ = check_term({"term": "摸鱼", "meaning": "上班偷懒", "example": "编的句子摸鱼"}, lines)
    assert good == {"term": "摸鱼", "meaning": "上班偷懒", "example": "今天又摸鱼了", "hits": 2}
    kept, _ = check_term({"term": "摸鱼", "meaning": "偷懒", "example": "摸鱼使我快乐"}, lines)
    assert kept["example"] == "摸鱼使我快乐"
    for item, why in (
        ({"term": "老板", "meaning": "上司"}, "出现不到 2 次"),
        ({"term": "外星人", "meaning": "x"}, "出现不到 2 次"),
        ({"term": "摸鱼", "meaning": "偷懒", "confidence": "low"}, "没把握"),
        ({"term": "摸鱼", "meaning": ""}, "没有解释"),
        ({"term": "一" * 13, "meaning": "x"}, "太长"),
    ):
        checked, reason = check_term(item, lines)
        assert checked is None and why in reason


def test_longer_terms_come_first_and_only_present_ones() -> None:
    entries = [("鲸", "阿鲸"), ("打哈欠", "发呆"), ("阿星", "群主"), ("不在", "x")]
    assert pick_terms(entries, "阿星在打哈欠", 2) == [("打哈欠", "发呆"), ("阿星", "群主")]


# -- learning ------------------------------------------------------------------------------------------


async def test_learning_adds_checked_terms_and_records_the_run(kit: Kit) -> None:
    w, learner = kit.world, kit.learner
    await chat(w, "今天又摸鱼了", "摸鱼使我快乐", "打瞌睡来了", "又在打瞌睡", "晚安")
    whale(w).queue(
        terms(
            {"term": "摸鱼", "meaning": "上班偷懒", "example": "今天又摸鱼了", "confidence": "high"},
            {"term": "打瞌睡", "meaning": "阿鲸在发呆", "example": "x", "confidence": "medium"},
            {"term": "晚安", "meaning": "睡觉", "confidence": "high"},
            {"term": "编的", "meaning": "不存在", "confidence": "high"},
        )
    )
    run = await learner.learn(GROUP_KEY)
    assert run["added"] == 2 and run["dropped"] == 2 and run["messages"] == 5 and run["error"] is None
    assert "新增 2 条" in run["detail"] and "「晚安」在聊天里出现不到 2 次" in run["detail"]
    entries = {e["term"]: e for e in await learner.slang.entries(GROUP_KEY)}
    assert set(entries) == {"摸鱼", "打瞌睡"} and entries["摸鱼"]["hits"] == 2
    assert entries["打瞌睡"]["example"] == "打瞌睡来了" and entries["摸鱼"]["source"] == "auto"
    # The model read who said what, and only people's messages.
    sent = whale(w).requests[-1]
    assert "群友0：今天又摸鱼了" in sent[1].content and sent[1].content.count("\n") == 4
    assert (await GroupFeatureStore(w.db).get(GROUP_KEY)).slang_learned_until is not None


async def test_edited_terms_keep_their_meaning_and_learning_reads_only_new_messages(kit: Kit) -> None:
    w, learner = kit.world, kit.learner
    await chat(w, "摸鱼1", "摸鱼2", "开黑吗", "开黑走起")
    whale(w).queue(
        terms(
            {"term": "摸鱼", "meaning": "偷懒", "confidence": "high"},
            {"term": "开黑", "meaning": "一起打游戏", "confidence": "high"},
        )
    )
    await learner.learn(GROUP_KEY)
    entries = {e["term"]: e for e in await learner.slang.entries(GROUP_KEY)}
    await learner.slang.update(entries["摸鱼"]["id"], meaning="在群里潜水")  # edited by hand
    await chat(w, "又摸鱼", "摸鱼吧", "开黑开黑", "开黑啦")
    whale(w).queue(
        terms(
            {"term": "摸鱼", "meaning": "模型的新说法", "confidence": "high"},
            {"term": "开黑", "meaning": "组队打游戏", "confidence": "high"},
        )
    )
    run = await learner.learn(GROUP_KEY, force=True)
    assert run["updated"] == 2 and run["added"] == 0 and run["messages"] == 4
    entries = {e["term"]: e for e in await learner.slang.entries(GROUP_KEY)}
    assert entries["摸鱼"]["meaning"] == "在群里潜水" and entries["摸鱼"]["source"] == "edited"
    assert entries["摸鱼"]["hits"] == 4
    assert entries["开黑"]["meaning"] == "组队打游戏" and entries["开黑"]["hits"] == 5
    second = whale(w).requests[-1]
    assert "摸鱼1" not in second[1].content  # already read last time
    assert "已经记下的词：" in second[0].content and "摸鱼：在群里潜水" in second[0].content


async def test_too_few_messages_no_model_and_model_errors_are_runs_too(kit: Kit) -> None:
    w, learner = kit.world, kit.learner
    await chat(w, "只有一句")
    run = await learner.learn(GROUP_KEY, force=True)
    assert "新消息太少（1 条，至少要 2 条）" in run["detail"] and not whale(w).requests
    await chat(w, "第二句", "第三句", "第四句")
    whale(w).queue(ProviderError("502", retryable=False), "我不知道")
    assert "模型调用失败" in (await learner.learn(GROUP_KEY))["error"]
    assert (await learner.learn(GROUP_KEY))["error"] == "模型的回答看不懂"
    await learner.save_config("missing")
    assert "还没有选学习用的模型" in (await learner.learn(GROUP_KEY))["error"]
    assert len(await learner.slang.runs(GROUP_KEY)) == 4


async def test_learning_is_due_after_enough_new_messages_and_hours(kit: Kit) -> None:
    w, learner = kit.world, kit.learner
    groups = GroupFeatureStore(w.db)
    await chat(w, *[f"消息{i}" for i in range(5)])
    assert await learner.due() == []  # the switch is off
    await groups.set(GROUP_KEY, learn_slang=True)
    assert await learner.due() == [GROUP_KEY]
    await learner.learn(GROUP_KEY)
    await chat(w, *[f"新消息{i}" for i in range(5)])
    assert await learner.due() == []  # learned less than 12 hours ago
    assert await learner.due(now=__import__("time").time() + 13 * 3600) == [GROUP_KEY]


async def test_too_many_terms_drop_the_learned_ones_seen_longest_ago(kit: Kit) -> None:
    store = kit.learner.slang
    for i in range(4):
        await store.add(GROUP_KEY, f"词{i}", "意思", at=float(i))
    await store.add(GROUP_KEY, "手加", "意思", source="manual", at=0.0)
    assert await store.trim(GROUP_KEY, 3) == 2
    assert {e["term"] for e in await store.entries(GROUP_KEY)} == {"手加", "词2", "词3"}
    assert await store.purge_stale(2.5) == 1  # 词2; hand-added terms never go stale
    assert {e["term"] for e in await store.entries(GROUP_KEY)} == {"手加", "词3"}


# -- explaining terms in the prompt -------------------------------------------------------------------


async def test_terms_said_in_the_messages_are_explained_when_the_switch_is_on(kit: Kit) -> None:
    w, learner = kit.world, kit.learner
    await learner.slang.add(GROUP_KEY, "打哈欠", "发呆、无聊")
    await learner.slang.add(GROUP_KEY, "开黑", "一起打游戏")
    learner.changed(GROUP_KEY)
    await w.say("阿鲸 你又在打哈欠", to=["10001"], accounts=["10001"])
    plain = (await w.settle(1))[0]
    assert "这个群里的说法" not in whale(w).requests[0][0].content and plain["slang_terms"] == []

    bot = w.brain.bots["qq:10001"]
    await w.repo.set_channel(bot.id, next(iter(bot.channels)), use_slang=True)
    await w.brain.load()
    await w.say("阿鲸 还在打哈欠吗", to=["10001"], accounts=["10001"])
    decision = (await w.settle(2))[1]
    system = whale(w).requests[1][0].content
    assert system.endswith("这个群里的说法（帮你看懂，不用刻意去用）：\n- 打哈欠：发呆、无聊")
    assert "开黑" not in system and decision["slang_terms"] == ["打哈欠"]
    # Nothing in this batch or its context is a known term: the prompt is the plain one.
    await learner.slang.delete((await learner.slang.find(GROUP_KEY, "打哈欠"))["id"])
    learner.changed(GROUP_KEY)
    await w.say("阿鲸 早", to=["10001"], accounts=["10001"])
    await w.settle(3)
    assert whale(w).requests[2][0].content == whale(w).requests[0][0].content


# -- console API --------------------------------------------------------------------------------------


async def test_api_terms_add_edit_confirm_delete(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    listing = (await http.get("/api/slang")).json()
    assert [g["key"] for g in listing["groups"]] == ["qq:group:42", "qq:group:77"]
    assert listing["group"] == "qq:group:42" and listing["entries"] == []
    assert listing["settings"] == {"provider_id": None, "default_provider": "ark", "provider_loaded": True}

    body = {"group_key": "qq:group:42", "term": " 摸鱼 ", "meaning": "上班偷懒"}
    created = await http.post("/api/slang", json=body)
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["term"] == "摸鱼" and entry["source"] == "manual"
    assert (await http.post("/api/slang", json=body)).status_code == 409
    assert (await http.post("/api/slang", json=body | {"group_key": "qq:group:1"})).status_code == 404
    assert (await http.post("/api/slang", json=body | {"term": "x" * 13})).status_code == 422
    assert (await http.post("/api/slang", json=body | {"meaning": "  "})).status_code == 422

    learned = await api_world.state.slang.slang.add("qq:group:42", "开黑", "打游戏")
    confirmed = (await http.patch(f"/api/slang/{learned}", json={})).json()
    assert confirmed["source"] == "edited" and confirmed["meaning"] == "打游戏"
    assert (await http.patch(f"/api/slang/{learned}", json={"term": "摸鱼"})).status_code == 409
    assert (await http.patch(f"/api/slang/{entry['id']}", json={"meaning": "潜水"})).json()[
        "source"
    ] == "manual"

    shown = (await http.get("/api/slang", params={"group": "qq:group:42"})).json()
    assert {e["term"] for e in shown["entries"]} == {"摸鱼", "开黑"}
    assert next(g for g in shown["groups"] if g["key"] == "qq:group:42")["terms"] == 2
    assert (await http.delete(f"/api/slang/{entry['id']}")).json() == {"ok": True}
    assert (await http.delete(f"/api/slang/{entry['id']}")).status_code == 404


async def test_api_learning_switch_model_and_learn_now(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    response = await http.patch("/api/groups/qq:group:42/features", json={"learn_slang": True})
    assert response.json() == {
        "key": "qq:group:42",
        "collect_stickers": False,
        "learn_slang": True,
        "welcome": False,
    }
    assert (await http.get("/api/slang")).json()["groups"][0]["learn"] is True
    assert (await http.patch("/api/slang/settings", json={"provider_id": "nope"})).status_code == 400
    assert (await http.patch("/api/slang/settings", json={"provider_id": "grok"})).json() == {
        "provider_id": "grok"
    }
    run = (await http.post("/api/slang/learn", json={"group_key": "qq:group:42"})).json()
    assert "新消息太少" in run["detail"]
    assert (await http.post("/api/slang/learn", json={"group_key": "qq:group:5"})).status_code == 404
    assert (await http.patch("/api/slang/settings", json={"provider_id": None})).json() == {
        "provider_id": None
    }
