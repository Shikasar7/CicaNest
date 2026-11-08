"""项目资料: reading help sections (the fixed docs/help format) and finding them by keywords or by a
pasted error message."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from nest.knowledge.choose import MAX_CHOSEN, build_choose_prompt, parse_choice
from nest.knowledge.dense import DenseIndex, EmbeddingClient, EmbeddingModel
from nest.knowledge.lexical import ErrorIndex, LexicalIndex, rrf, tokens
from nest.knowledge.loader import load, parse
from nest.knowledge.rerank import RerankClient, RerankModel

FEATURES = """# 功能

开头的说明。

## 功能清单 {#feature-list}

<!-- 功能清单 -->
- [朗读](#tts)：…
<!-- /功能清单 -->

# 声音

## 朗读 {#tts}

- 也叫：语音、TTS、念出来
- 在哪：设置 → 声音 → 朗读
- 平台：Windows、安卓

打开后回复会读出来，音色可以换。

- 这一行在正文里：不是字段

# 模型

## 最大输出 token {#provider-max-tokens}

- 也叫：回复被截断、输出上限
- 在哪：设置 → 高级 → 输出

回复写到一半停了，调大这里。
"""

ERRORS = """# 报错

## 同步失败 {#err-sync-failed}

- 原文：同步 {0} 失败：检查网络和登录状态，再试一次。（{1}）
- 英文：Syncing {0} failed: check your network and sign-in, then try again. ({1})
- 原因：请求没到达服务器。
- 解决：看网络和代理。
"""


def test_sections_carry_their_fields_area_and_body() -> None:
    sections = parse(FEATURES, "features.md")
    assert [s.id for s in sections] == ["tts", "provider-max-tokens"]  # the 功能清单 is no section
    tts = sections[0]
    assert tts.area == "声音" and tts.title == "朗读"
    assert tts.values("也叫") == ("语音、TTS、念出来",)
    assert tts.values("平台") == ("Windows、安卓",)
    assert tts.body.startswith("打开后回复") and "- 这一行在正文里：不是字段" in tts.body
    where = "设置 → 声音 → 朗读"
    assert tts.header() == f"功能 · 声音 · 朗读（也叫：语音、TTS、念出来） · 在哪：{where}"
    assert tts.text().splitlines()[:3] == ["## 朗读", "- 也叫：语音、TTS、念出来", f"- 在哪：{where}"]
    error = parse(ERRORS, "errors.md")[0]
    assert error.area == ""  # the file's own title is not an area
    assert error.values("原文") == ("同步 {0} 失败：检查网络和登录状态，再试一次。（{1}）",)


def test_a_folder_loads_in_file_order_and_duplicate_ids_are_refused(tmp_path: Path) -> None:
    (tmp_path / "features.md").write_text(FEATURES, encoding="utf-8")
    (tmp_path / "errors.md").write_text(ERRORS, encoding="utf-8")
    assert [s.file for s in load(tmp_path)] == ["features.md", "features.md", "errors.md"]
    (tmp_path / "faq.md").write_text("# 常见问题\n\n## 又一个 {#tts}\n\n正文\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tts"):
        load(tmp_path)


def test_chinese_is_cut_into_character_pairs_and_latin_into_words() -> None:
    assert tokens("朗读没声音 TTS-v2") == ["朗读", "读没", "没声", "声音", "tts", "v2"]
    assert tokens("好") == ["好"]


def test_keywords_find_the_section_titles_and_aliases_count_most() -> None:
    index = LexicalIndex(parse(FEATURES, "features.md") + parse(ERRORS, "errors.md"))
    assert index.search("怎么让她念出来")[0][0].id == "tts"
    assert index.search("回复被截断了咋办")[0][0].id == "provider-max-tokens"
    assert index.search("完全无关的东西") == []


def test_a_pasted_error_is_matched_with_its_blanks_filled_in() -> None:
    errors = ErrorIndex(parse(FEATURES, "features.md") + parse(ERRORS, "errors.md"))
    found = errors.search("报错：同步 云端 失败：检查网络和登录状态，再试一次。（请求超时）")
    assert [s.id for s, _ in found] == ["err-sync-failed"]
    assert errors.search("Syncing Cloud failed: check your network")[0][0].id == "err-sync-failed"
    assert errors.search("网络不好") == []


def test_rank_fusion_rewards_sections_both_lists_rank_high() -> None:
    a, b = parse(FEATURES, "features.md")
    fused = rrf([[a, b], [b]])
    assert [s.id for s, _ in fused] == ["provider-max-tokens", "tts"]


async def test_section_vectors_are_cached_and_the_question_gets_the_models_prefix() -> None:
    seen: list[list[str]] = []

    def vector(text: str) -> list[float]:  # a toy model: one dimension per topic word
        return [float("朗读" in text or "念" in text), float("截断" in text or "输出上限" in text), 0.1]

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        seen.append(texts)
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": vector(t)} for i, t in enumerate(texts)]}
        )

    model = EmbeddingModel("toy", "http://embed", query_prefix="Query:")
    client = EmbeddingClient(model, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    sections = parse(FEATURES, "features.md")
    cache: dict = {}
    index = await DenseIndex.build(sections, client, cache=cache)
    assert len(seen) == 1 and seen[0][0].startswith("功能 · 声音 · 朗读")  # the header comes first
    await DenseIndex.build(sections, client, cache=cache)
    assert len(seen) == 1  # nothing embedded twice
    found = index.search(await client.embed_query("怎么让她念出来"))
    assert seen[-1] == ["Query:怎么让她念出来"] and found[0][0].id == "tts"
    await client.aclose()


async def test_rerank_scores_come_back_in_the_documents_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "q" and body["top_n"] == 3
        return httpx.Response(
            200,
            json={"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]},
        )

    client = RerankClient(
        RerankModel("toy", "http://rerank/"), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert await client.scores("q", ["a", "b", "c"]) == [0.1, 0.0, 0.9]
    assert await client.scores("q", []) == []
    await client.aclose()


async def test_forwarding_merges_questions_and_stops_at_the_daily_limit(tmp_path: Path) -> None:
    import asyncio

    from nest.knowledge.gaps import GapForwarder
    from nest.storage.db import Database
    from nest.storage.knowledge import GapRepo, KnowledgeRepo

    db = await Database.open(tmp_path / "nest.db")
    try:
        base = await KnowledgeRepo(db).add("Cica", str(tmp_path))
        gaps = GapRepo(db)
        sent: list[tuple[str, str, str]] = []

        async def send(account: str, user: str, text: str) -> bool:
            sent.append((account, user, text))
            return True

        forwarder = GapForwarder(gaps, send, gap=0.2, daily=2)

        async def gap(question: str) -> None:
            await gaps.add_gap(
                base_id=base,
                group_key="qq:group:42",
                account_id="qq:1",
                message_id=None,
                asker="小明",
                question=question,
                answer="",
                reason="not_found",
            )
            forwarder.notify(base, "Cica")

        await gap("一")  # nobody to forward to yet
        await asyncio.sleep(0.05)
        assert sent == []
        await gaps.set_forward_to(base, "555")
        await gap("二")
        await asyncio.sleep(0.05)
        assert len(sent) == 1 and "有 2 个问题" in sent[0][2] and sent[0][:2] == ("qq:1", "555")
        await gap("三")
        await gap("四")  # within the gap: merged into one message after it
        await asyncio.sleep(0.05)
        assert len(sent) == 1
        await asyncio.sleep(0.3)
        assert len(sent) == 2 and "有 2 个问题" in sent[1][2] and "1. 小明问：三" in sent[1][2]
        await asyncio.sleep(0.25)
        await gap("五")  # past the daily limit: recorded, not forwarded
        await asyncio.sleep(0.05)
        assert len(sent) == 2
        await forwarder.stop()
    finally:
        await db.close()


def test_parsing_the_models_pick() -> None:
    known = ["tts", "provider-max-tokens", "overview-what"]
    assert parse_choice('好的 {"ids": ["provider-max-tokens", "made-up", "tts", "tts"]}', known) == [
        "provider-max-tokens",
        "tts",
    ]  # in the model's order, only real candidates, each once
    assert parse_choice('{"ids": []}', known) == []  # none of them answers it: a real answer
    assert parse_choice("朗读那节", known) is None and parse_choice('{"ids": "tts"}', known) is None
    many = [f"s{i}" for i in range(10)]
    assert parse_choice(json.dumps({"ids": many}), many) == many[:MAX_CHOSEN]


def test_the_pick_prompt_lists_each_candidate_briefly() -> None:
    text = "# 功能\n\n## 朗读 {#tts}\n\n- 也叫：语音、念出来\n\n" + "打开就能听。" * 30 + "\n"
    [section] = parse(text, "features.md")
    system, user = build_choose_prompt("DemoApp", "朗读怎么开", [section])
    assert system.content.startswith("你是 DemoApp 帮助资料的排序助手。") and "最多 6 个" in system.content
    line = user.content.splitlines()[1]
    assert line.startswith("[tts] 朗读（也叫：语音、念出来）：打开就能听。") and len(line) < 160
    assert user.content.endswith("问题：朗读怎么开")
