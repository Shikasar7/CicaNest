"""项目资料 in 答题: a group bound to a project's help docs gets project questions answered from them (the
sections retrieval finds, never the web), says so when the docs do not answer, and keeps answering from
keywords when the embedding and rerank servers are down."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from nest.knowledge.dense import EmbeddingModel
from nest.knowledge.rerank import RerankModel
from nest.knowledge.service import KnowledgeService
from nest.storage.knowledge import KnowledgeRepo
from tests.test_brain import World, world  # noqa: F401 (world is a fixture)
from tests.test_sending import answering, go_live

OVERVIEW = """# DemoApp 简介

## DemoApp 是什么 {#overview-what}

- 也叫：演示、Demo

一个演示用的聊天应用。
"""
FEATURES = """# 功能

# 声音

## 朗读 {#tts}

- 也叫：语音、念出来
- 在哪：设置 → 声音 → 朗读

打开后每条回复都会读出来，可以选音色。

# 模型

## 最大输出 token {#provider-max-tokens}

- 也叫：回复被截断、输出上限
- 在哪：设置 → 高级 → 输出

回复写到一半停了，调大这里。
"""
PROJECT = '{"kind": "project", "topic": "朗读怎么开", "keywords": ["朗读"], "error": ""}'
PICK_TTS = '{"ids": ["tts"]}'  # the bot's model picking the sections (nest.knowledge.choose)
PICK_NONE = '{"ids": []}'


def servers(*, down: bool = False) -> httpx.MockTransport:
    """A toy embedding model (one dimension per topic) and reranker (high when the topic matches)."""

    def topic(text: str) -> list[float]:
        return [float("朗读" in text or "念" in text), float("截断" in text or "输出" in text), 0.1]

    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content)
        if request.url.path == "/v1/embeddings":
            data = [{"index": i, "embedding": topic(t)} for i, t in enumerate(body["input"])]
            return httpx.Response(200, json={"data": data})
        query = topic(body["query"])
        results = []
        for i, doc in enumerate(body["documents"]):
            match = sum(a * b for a, b in zip(query[:2], topic(doc)[:2], strict=True))
            results.append({"index": i, "relevance_score": 4.0 if match else -6.0})  # raw scores, like bge
        return httpx.Response(200, json={"results": results})

    return httpx.MockTransport(handler)


async def bind_docs(w: World, tmp_path: Path, *, down: bool = False) -> None:
    folder = tmp_path / "help"
    folder.mkdir()
    (folder / "overview.md").write_text(OVERVIEW, encoding="utf-8")
    (folder / "features.md").write_text(FEATURES, encoding="utf-8")
    repo = KnowledgeRepo(w.db)
    base_id = await repo.add("DemoApp", str(folder))
    await repo.bind("qq:group:42", base_id)
    service = KnowledgeService(
        repo,
        embed=EmbeddingModel("toy", "http://embed"),
        rerank=RerankModel("toy", "http://rerank", logits=True),
        client=httpx.AsyncClient(transport=servers(down=down)),
    )
    await service.start()
    w.brain.knowledge = service


async def test_a_project_question_is_answered_from_the_docs_not_the_web(world: World, tmp_path: Path) -> None:  # noqa: F811
    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue(PROJECT, PICK_TTS, "在 设置 → 声音 → 朗读 里开，打开就能听。")
    await world.say("阿鲸 朗读怎么开啊", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["outcome"] == "replied"
    assert decision["reason"].startswith("已发送（问 DemoApp，模型从候选里挑了 1 节资料，重排最相关 0.98")
    assert world.providers["10001"].searched == [False, False, False]  # the docs, not the web
    sort_system, _ = world.providers["10001"].requests[0]
    assert '"kind": "project"' in sort_system.content and "DemoApp 的交流群" in sort_system.content
    pick_system, pick_user = world.providers["10001"].requests[1]
    assert "DemoApp 帮助资料的排序助手" in pick_system.content
    assert (
        "[tts] 朗读（也叫：语音、念出来）：" in pick_user.content
        and "问题：阿鲸 朗读怎么开啊" in pick_user.content
    )
    system, user = world.providers["10001"].requests[2]
    assert "只根据下面给的资料回答" in system.content
    assert "关于 DemoApp：\n## DemoApp 是什么" in user.content
    assert "DemoApp 的全部功能：\n声音：朗读\n模型：最大输出 token" in user.content
    assert "和这个问题相关的资料（以这些为准）：\n\n## 朗读\n- 也叫：语音、念出来" in user.content
    assert "最大输出 token\n- 也叫" not in user.content  # the unrelated section was not handed over
    assert adapter.actions[0].segments[0].type == "reply"  # an answer quotes the question


async def test_when_the_docs_do_not_answer_the_model_is_told_so(world: World, tmp_path: Path) -> None:  # noqa: F811
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue(
        '{"kind": "project", "topic": "能不能导出 PDF", "keywords": ["导出"], "error": ""}',
        PICK_NONE,
        "这个我不确定，等作者看看",
    )
    await world.say("阿鲸 能不能导出成 PDF？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert "问 DemoApp，模型从候选里挑了 0 节资料，重排最相关 0.00，认为资料里没有" in decision["reason"]
    _, user = world.providers["10001"].requests[2]
    assert "DemoApp 的资料里没找到和这个问题相关的内容。" in user.content


async def test_without_the_model_servers_keywords_still_find_the_section(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    await answering(world)
    await bind_docs(world, tmp_path, down=True)
    world.providers["10001"].queue(PROJECT, PICK_TTS, "设置 → 声音 → 朗读")
    await world.say("阿鲸 朗读在哪开？", accounts=["10001"])
    [decision] = await world.settle(1)
    reason = decision["reason"]
    assert (
        "问 DemoApp，模型从候选里挑了 1 节资料，没有重排；向量模型没开；重排模型没响应（ConnectError）"
        in reason
    )
    _, user = world.providers["10001"].requests[2]
    assert "相关的资料（以这些为准）：\n\n## 朗读" in user.content


async def test_other_groups_and_chat_are_untouched(world: World, tmp_path: Path) -> None:  # noqa: F811
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue('{"kind": "chat"}', "在呢")
    await world.say("阿鲸在吗", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["reason"].endswith("（当闲聊）")
    assert world.brain.knowledge is not None and world.brain.knowledge.base_for("qq:group:99") is None


async def forwarding(w: World, to: str = "555") -> int:
    from nest.knowledge.gaps import GapForwarder

    service = w.brain.knowledge
    assert service is not None
    service.forwarder = GapForwarder(service.gaps, w.brain.send_private, gap=0.2)
    base_id = next(iter(service.bases))
    await service.gaps.set_forward_to(base_id, to)
    return base_id


async def test_an_unanswered_question_is_recorded_and_forwarded_to_the_docs_keeper(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    from tests.helpers import eventually

    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    base_id = await forwarding(world)
    world.providers["10001"].queue(
        '{"kind": "project", "topic": "导出", "keywords": ["导出"], "error": ""}',
        PICK_NONE,
        "这个我不确定，等作者看看",
    )
    await world.say("阿鲸 能不能导出成 PDF？", accounts=["10001"])
    await world.settle(1)
    dm = await eventually(lambda: [a for a in adapter.actions if a.channel.kind == "private"])
    assert dm[0].channel.id == "555"
    text = dm[0].segments[0].data["text"]
    assert text.startswith(
        "DemoApp 有 1 个问题资料里没答上：\n1. 小明问：阿鲸 能不能导出成 PDF？——资料里没找到"
    )
    assert "（回了：这个我不确定，等作者看看）" in text and "「资料」页补一条回答" in text
    service = world.brain.knowledge
    assert service is not None

    async def forwarded() -> dict | None:  # marked right after the message went out, not before
        [gap] = await service.gaps.gaps(base_id)
        return gap if gap["forwarded_at"] is not None else None

    gap = await eventually(forwarded)
    assert gap["reason"] == "not_found" and gap["group_key"] == "qq:group:42"


async def test_the_unsure_mark_is_not_sent_and_observe_mode_only_records(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    await answering(world)  # observe mode: nothing is sent, so nothing is forwarded either
    await bind_docs(world, tmp_path)
    base_id = await forwarding(world)
    world.providers["10001"].queue(PROJECT, PICK_TTS, "朗读在设置里，具体音色我拿不准\n【不确定】")
    await world.say("阿鲸 朗读能换成男声吗？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert decision["reply"] == "朗读在设置里，具体音色我拿不准"  # the mark stays out of the reply
    [gap] = await world.brain.knowledge.gaps.gaps(base_id)  # type: ignore[union-attr]
    assert gap["reason"] == "uncertain" and gap["answer"] == "朗读在设置里，具体音色我拿不准"
    assert gap["forwarded_at"] is None


async def test_the_model_finds_a_section_the_reranker_turned_down(world: World, tmp_path: Path) -> None:  # noqa: F811
    """The reranker scores every candidate low (the docs do not answer it, by its count), the bot's model
    picks one anyway: the bot reads it, and the question is still recorded as not in the docs."""
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue(
        '{"kind": "project", "topic": "角色能开口吗", "keywords": ["语音"], "error": ""}',
        PICK_TTS,
        "能，在 设置 → 声音 → 朗读 里开。",
    )
    await world.say("阿鲸 能让它出声吗", accounts=["10001"])
    [decision] = await world.settle(1)
    assert "模型从候选里挑了 1 节资料，重排最相关 0.00，认为资料里没有" in decision["reason"]
    _, user = world.providers["10001"].requests[2]
    assert "相关的资料（以这些为准）：\n\n## 朗读" in user.content
    service = world.brain.knowledge
    assert service is not None
    [gap] = await service.gaps.gaps(next(iter(service.bases)))
    assert gap["reason"] == "not_found"  # the reranker's call, whatever the model picked


async def test_an_unreadable_pick_falls_back_to_the_reranker(world: World, tmp_path: Path) -> None:  # noqa: F811
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue(PROJECT, "朗读那节吧", "设置 → 声音 → 朗读")
    await world.say("阿鲸 朗读在哪开？", accounts=["10001"])
    [decision] = await world.settle(1)
    assert "用了 1 节资料（最相关 0.98）；模型挑资料的回答看不懂，用重排的结果" in decision["reason"]
    _, user = world.providers["10001"].requests[2]
    assert "相关的资料（以这些为准）：\n\n## 朗读" in user.content


# -- 看源码 ---------------------------------------------------------------------------------------------

NOT_IN_DOCS = '{"kind": "project", "topic": "设置不自动保存", "keywords": ["设置"], "error": ""}'
SOURCE_STEPS = (
    '{"search": "SaveSettings"}',
    '{"read": "web/settings.js", "from": 1, "to": 3}',
)


async def with_source(w: World, tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "web").mkdir(parents=True)
    (root / "web" / "settings.js").write_text(
        "export function applyUpdate(reply) {\n  const block = reply.match(/<SaveSettings>/)\n"
        "  if (!block) return null\n}\n",
        encoding="utf-8",
    )
    service = w.brain.knowledge
    assert service is not None
    base_id = next(iter(service.bases))
    await service.repo.update(base_id, source_dir=str(root))
    await service.refresh()
    return root


async def test_the_source_is_read_and_a_sure_answer_follows_in_the_group(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    from tests.helpers import eventually

    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    base_id = await forwarding(world)
    await with_source(world, tmp_path)
    found = "设置只有模型回复里带了 SaveSettings 才会改，模型没写就不动。把漏存那轮的原始回复发来看看。"
    world.providers["10001"].queue(
        NOT_IN_DOCS,
        PICK_NONE,
        "这个我不确定，等作者看看",
        *SOURCE_STEPS,
        json.dumps({"answer": found, "sure": True, "files": ["web/settings.js"]}, ensure_ascii=False),
    )
    question = await world.say("阿鲸 为什么设置老是不自动保存啊", accounts=["10001"])
    decisions = await world.settle(2)
    follow = next(d for d in decisions if d["trigger"] == "source")
    assert follow["outcome"] == "replied" and follow["reason"] == "看源码 3 步，读了 1 个文件，已发送"
    assert follow["reply"] == f"翻了下源码：{found}\n（看源码推断的，作者还没确认）"
    group = [a for a in adapter.actions if a.channel.kind == "group"]
    assert len(group) == 2 and group[1].segments[0].type == "reply"  # it quotes the question again
    assert group[1].segments[0].data["id"] == question
    system, user = world.providers["10001"].requests[3]
    assert "现在去读 DemoApp 的源码" in system.content
    assert "你刚才回的：\n这个我不确定，等作者看看" in user.content
    assert "结果：\nweb/settings.js:2:" in world.providers["10001"].requests[4][1].content
    dm = await eventually(lambda: [a for a in adapter.actions if a.channel.kind == "private"])
    text = dm[0].segments[0].data["text"]
    # Forwarded once the source was read, with what it showed.
    assert f"看了源码，已在群里补答：{found}" in text
    [gap] = await world.brain.knowledge.gaps.gaps(base_id)  # type: ignore[union-attr]
    assert (
        gap["analysis"] == found and gap["analysis_sure"] == 1 and gap["analysis_files"] == "web/settings.js"
    )


async def test_an_unsure_finding_stays_out_of_the_group(world: World, tmp_path: Path) -> None:  # noqa: F811
    from tests.helpers import eventually

    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    await forwarding(world)
    await with_source(world, tmp_path)
    world.providers["10001"].queue(
        NOT_IN_DOCS,
        PICK_NONE,
        "这个我不确定",
        '{"answer": "代码只在回复带了保存块时才改，这次可能是模型漏写，要看原始回复才能定", "sure": false}',
    )
    await world.say("阿鲸 为什么设置老是不自动保存啊", accounts=["10001"])
    decisions = await world.settle(2)
    follow = next(d for d in decisions if d["trigger"] == "source")
    assert follow["outcome"] == "skipped"
    assert follow["reason"] == "看源码 1 步：没在代码里找到依据，没在群里说"
    assert len([a for a in adapter.actions if a.channel.kind == "group"]) == 1  # only the first answer
    dm = await eventually(lambda: [a for a in adapter.actions if a.channel.kind == "private"])
    text = dm[0].segments[0].data["text"]
    assert "看了源码，没找到代码依据，没在群里说：代码只在回复带了保存块时才改" in text


async def test_without_source_or_in_the_docs_nothing_is_read(world: World, tmp_path: Path) -> None:  # noqa: F811
    await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    world.providers["10001"].queue(NOT_IN_DOCS, PICK_NONE, "这个我不确定")  # no source set
    await world.say("阿鲸 为什么设置老是不自动保存啊", accounts=["10001"])
    await world.settle(1)
    await with_source(world, tmp_path)
    world.providers["10001"].queue(PROJECT, PICK_TTS, "在 设置 → 声音 → 朗读 里开。")  # in the docs
    await world.say("阿鲸 朗读怎么开啊", accounts=["10001"])
    decisions = await world.settle(2)
    assert all(d["trigger"] == "mention" for d in decisions) and len(world.providers["10001"].requests) == 6


async def test_the_docs_keeper_can_ask_in_a_private_chat(world: World, tmp_path: Path) -> None:  # noqa: F811
    from tests.test_sending import dm, private_with

    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    base_id = await forwarding(world, to="555")
    await with_source(world, tmp_path)
    ref = await private_with(world, "555")
    for channel_id in world.brain.bots["qq:10001"].channels:
        await world.repo.set_channel("qq:10001", channel_id, answer_questions=True)
    await world.brain.load()
    found = "代码里只有回复带了 SaveSettings 才改设置。"
    world.providers["10001"].queue(
        NOT_IN_DOCS,
        PICK_NONE,
        "资料里没写，我去翻翻源码",
        *SOURCE_STEPS,
        json.dumps({"answer": found, "sure": True}, ensure_ascii=False),
    )
    await dm(world, "你帮我看看现在设置更新是怎么做的", user="555")
    decisions = await world.settle(2)
    assert {d["trigger"] for d in decisions} == {"mention", "source"}
    sent = [a for a in adapter.actions if a.channel == ref]
    assert [a.segments[0].data["text"] for a in sent] == [
        "资料里没写，我去翻翻源码",
        f"翻了下源码：{found}\n（看源码推断的，作者还没确认）",
    ]
    [gap] = await world.brain.knowledge.gaps.gaps(base_id)  # type: ignore[union-attr]
    assert gap["forwarded_at"] is None and gap["analysis"] == found  # the keeper asked: not forwarded back
