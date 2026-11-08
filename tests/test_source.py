"""看源码's pieces: reading a source checkout (nest.knowledge.source) only inside it, and the step-by-step
loop (nest.knowledge.investigate) with a scripted model."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nest.knowledge import investigate as inv
from nest.knowledge.source import SourceError, SourceTree
from nest.llm.base import ChatMessage

VARS = "\n".join(
    [
        "// 设置更新",
        "export function applyUpdate(reply) {",
        "  const block = reply.match(/<SaveSettings>([\\s\\S]*?)<\\/SaveSettings>/)",
        "  if (!block) return null  // 模型没写保存块：什么也不改",
        "  return parse(block[1])",
        "}",
    ]
)


ZH = "export default { autoSave: '自动保存设置' }"


@pytest.fixture
def tree(tmp_path: Path) -> SourceTree:
    root = tmp_path / "src"
    (root / "web").mkdir(parents=True)
    (root / "web" / "settings.js").write_text(VARS, encoding="utf-8")
    (root / "web" / "i18n").mkdir()
    (root / "web" / "i18n" / "zh.js").write_text(ZH + "\n", encoding="utf-8")
    (root / "README.md").write_text("# DemoApp\n", encoding="utf-8")
    for skipped in (".git", "node_modules", "dist"):
        (root / skipped).mkdir()
        (root / skipped / "x.js").write_text("applyUpdate()\n", encoding="utf-8")
    (root / "web" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0")
    (root / "web" / "big.js").write_text("x\n" * 400_000, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("token=abc", encoding="utf-8")
    return SourceTree(root)


def test_only_source_text_is_listed(tree: SourceTree) -> None:
    assert tree.files() == ["README.md", "web/i18n/zh.js", "web/settings.js"]
    assert tree.list("") == "README.md\nweb/（2 个文件）"
    assert tree.list("web") == "i18n/（1 个文件）\nsettings.js"


def test_search_finds_lines_and_takes_bad_patterns_literally(tree: SourceTree) -> None:
    assert tree.search("自动保存") == [f"web/i18n/zh.js:1: {ZH}"]
    assert tree.search("APPLYUPDATE") == ["web/settings.js:2: export function applyUpdate(reply) {"]
    assert tree.search("match(/<Save") == [  # not a valid expression: searched as it is
        "web/settings.js:3: const block = reply.match(/<SaveSettings>([\\s\\S]*?)<\\/SaveSettings>/)"
    ]
    assert tree.search("export", under="web/i18n") == [f"web/i18n/zh.js:1: {ZH}"]
    with pytest.raises(SourceError):
        tree.search("  ")


def test_read_numbers_lines_and_stays_inside(tree: SourceTree) -> None:
    text = tree.read("web/settings.js", 3, 4)
    assert text.startswith("web/settings.js 第 3–4 行（共 6 行）\n3:   const block")
    assert text.endswith("4:   if (!block) return null  // 模型没写保存块：什么也不改")
    assert tree.read("/web/settings.js").count("\n") == 6  # the whole file, from line 1
    for outside in ("../secret.txt", "web/../../secret.txt", ".git/x.js", "node_modules/x.js", "web/big.js"):
        with pytest.raises(SourceError):
            tree.read(outside)
    with pytest.raises(SourceError, match="只有 6 行"):
        tree.read("web/settings.js", 50)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_a_symlink_out_of_the_source_is_not_followed(tree: SourceTree, tmp_path: Path) -> None:
    (tree.root / "web" / "leak.js").symlink_to(tmp_path / "secret.txt")
    assert "web/leak.js" not in SourceTree(tree.root).files()


def scripted(*replies: str):
    """A model that answers each step in turn, recording what it was shown."""
    shown: list[list[ChatMessage]] = []
    queue = list(replies)

    async def ask(messages: list[ChatMessage], max_tokens: int) -> str:
        shown.append(messages)
        return queue.pop(0)

    return ask, shown


async def test_the_model_searches_reads_and_answers(tree: SourceTree) -> None:
    answer = {
        "answer": "设置只有在模型回复里带了 <SaveSettings> 才会改，模型没写就不动。",
        "sure": True,
        "files": ["web/settings.js"],
    }
    ask, shown = scripted(
        '{"search": "自动保存"}',
        '好的 {"search": "applyUpdate", "in": "web"}',  # words around the JSON are tolerated
        '{"read": "web/settings.js", "from": 1, "to": 6}',
        json.dumps(answer, ensure_ascii=False),
    )
    question = "为什么设置老是不自动保存"
    found = await inv.investigate(
        tree, ask, name="阿龙", project="DemoApp", question=question, replied="我不确定"
    )
    assert found.sure and found.answer.startswith("设置只有在模型回复里带了")
    assert found.files == ["web/settings.js"]
    assert found.steps == 4 and found.read == ["web/settings.js"] and found.problem == ""
    assert found.summary == "看源码 4 步，读了 1 个文件"
    system, user = shown[0]
    assert "你是阿龙，在 DemoApp 的群里答疑" in system.content and "最多 10 步" in system.content
    assert "问题：\n为什么设置老是不自动保存" in user.content and "你刚才回的：\n我不确定" in user.content
    assert "源码最外层：\nREADME.md\nweb/（2 个文件）" in user.content
    last = shown[-1][1].content
    assert '第 1 步：{"search": "自动保存"}\n结果：\nweb/i18n/zh.js:1:' in last
    assert "第 3 步：" in last and "6:   return parse(block[1])" not in last  # file has 6 lines: 6 is "}"
    assert "5:   return parse(block[1])" in last


async def test_mistakes_are_told_back_and_steps_run_out(tree: SourceTree) -> None:
    ask, shown = scripted(
        "我先看看",
        '{"read": "../secret.txt"}',
        '{"read": "web/settings.js", "from": "x"}',
        '{"delete": "web"}',
        *['{"list": ""}'] * 6,
    )
    found = await inv.investigate(tree, ask, name="阿龙", project="DemoApp", question="q")
    assert found.problem == "10 步内没有给出结论" and not found.sure and found.steps == 10
    last = shown[-1][1].content
    assert "结果：\n出错：要输出一行 JSON" in last and "出错：../secret.txt 不在源码里" in last
    assert "出错：参数不对，行号要是数字" in last and "出错：看不懂这一步" in last
    assert "这是最后一步，必须给 answer。" in last


async def test_an_unsure_or_empty_answer(tree: SourceTree) -> None:
    ask, _ = scripted('{"answer": "可能是模型漏写了保存块，要看那轮的原始回复", "sure": false}')
    found = await inv.investigate(tree, ask, name="阿龙", project="DemoApp", question="q")
    assert not found.sure and found.answer.startswith("可能是模型漏写") and found.problem == ""
    ask, _ = scripted('{"answer": "", "sure": true}')
    found = await inv.investigate(tree, ask, name="阿龙", project="DemoApp", question="q")
    assert not found.sure and found.problem == "模型给的结论是空的"


def test_old_results_are_shortened_first() -> None:
    history = [(f"step {n}", "x" * 15000) for n in range(4)]
    inv._shorten(history)
    assert [len(result) for _, result in history[:2]] == [len("x" * 300 + "……（前面看过，已省略）")] * 2
    assert len(history[2][1]) == 15000 and len(history[3][1]) == 15000


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        ("我翻了源码：设置只在回复带了保存块时才改", "设置只在回复带了保存块时才改"),
        ("翻了下代码，缓存不保证命中", "缓存不保证命中"),
        ("源码里写着要等保存块", "源码里写着要等保存块"),
    ],
)
async def test_the_models_own_opener_is_dropped(tree: SourceTree, said: str, kept: str) -> None:
    ask, _ = scripted(json.dumps({"answer": said, "sure": True}, ensure_ascii=False))
    found = await inv.investigate(tree, ask, name="阿龙", project="DemoApp", question="q")
    assert found.answer == kept and found.sure
    assert "不要用「我翻了源码」之类的话开头" in inv.RULES and '"sure" 说的是你讲的程序行为' in inv.RULES
