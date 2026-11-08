"""看源码: when a project question is not answered by the help docs, the bot that was asked reads the
project's source (nest.knowledge.source) to work out the answer, a step at a time.

Each step is one call to the bot's own model with the steps so far. It answers with one line of JSON: look
in a folder, search, read part of a file, or give the answer. Plain text in and out rather than a provider's
own tool calling, so every model nest supports can do it (as with picking sections, nest.knowledge.choose).
The tools only read, and only inside the checkout. The answer says whether it rests on code the model read
("sure"; it may still ask the asker for details: the asker's own case is often unknown, which is no reason
to call an explanation of the code unsure). Only such an answer is posted in the group (by the brain); every
one goes to whoever keeps the docs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nest.core.tones import PERSONA, Tone
from nest.knowledge.source import SourceError, SourceTree
from nest.llm.base import ChatMessage

log = logging.getLogger(__name__)

Ask = Callable[[list[ChatMessage], int], Awaitable[str]]

STEPS = 10  # model calls at most, the answer included
STEP_TOKENS = 700
RESULT_CHARS = 3500  # one tool result, cut to this
HISTORY_CHARS = 40000  # all results together: older ones are shortened past this
OLD_RESULT_CHARS = 300
ANSWER_CHARS = 400
# The brain puts 「翻了下源码：」 in front of the answer: the model's own version of it goes.
OPENER = re.compile(r"^(?:我|刚)*(?:去|又)?翻了(?:一?下|一遍)?(?:源码|代码)[，,：:。.\s]*")

RULES = (
    "你是{name}，在 {project} 的群里答疑。下面这个问题帮助资料里没写到，你刚才已经先回了一句。"
    "现在去读 {project} 的源码（公开仓库{revision}），弄清楚是怎么回事，再给提问的人一个解释。\n\n"
    "每次只输出一行 JSON，不要别的字，从下面四种里选一种：\n"
    '{{"list": "文件夹"}}  看文件夹里有什么，"" 是最外层\n'
    '{{"search": "正则或关键词", "in": "文件夹（可不填）"}}  在源码里搜，返回匹配的行，最多 40 条\n'
    '{{"read": "文件路径", "from": 起始行, "to": 结束行}}  读文件的一段，一次最多 200 行\n'
    '{{"answer": "给提问的人的解释", "sure": true 或 false, "files": ["你依据的文件"]}}\n\n'
    "做法：\n"
    "- 先搜再读，照代码说话，不要凭印象猜。界面上的中文文字可以直接搜，顺着找到处理它的代码。\n"
    "- 最多 {steps} 步，用完之前一定要给 answer。\n"
    "- answer 用中文口语，像在群里说话，200 字以内：程序实际是怎么做的、为什么会出现提问里的情况、"
    "用户能怎么办。不贴代码，最多提一两个文件名。不要用「我翻了源码」之类的话开头，直接说内容；"
    "不分点、不加小标题和客套话。{manner}\n"
    "- 只说你在代码里确认过的；资料和代码说法不一样时以代码为准。\n"
    '- "sure" 说的是你讲的程序行为有没有在代码里确认过，不是你能不能断定提问的人具体是哪种情况：'
    "讲的都在代码里看到了，就是 true，哪怕还要对方补信息（把要补的说清楚）。"
    "没找到相关的代码、或者要靠猜才能讲下去，才是 false。"
)


@dataclass
class Finding:
    answer: str = ""
    sure: bool = False
    files: list[str] = field(default_factory=list)  # the files the answer rests on, as the model named them
    steps: int = 0  # model calls made
    read: list[str] = field(default_factory=list)  # files actually read
    problem: str = ""  # why there is no answer, or ""

    @property
    def summary(self) -> str:
        """For the decision's reason: how much was looked at."""
        files = f"，读了 {len(self.read)} 个文件" if self.read else ""
        return f"看源码 {self.steps} 步{files}"


def build_messages(
    *,
    name: str,
    project: str,
    revision: str,
    question: str,
    replied: str,
    seen: list[str],
    top: str,
    history: list[tuple[str, str]],
    left: int,
    tone: Tone | None = None,
) -> list[ChatMessage]:
    manner = "" if tone is None or tone.key == PERSONA else f"语气：{tone.manner}"
    system = RULES.format(name=name, project=project, revision=revision, steps=STEPS, manner=manner)
    parts = [f"问题：\n{question}"]
    if replied:
        parts.append(f"你刚才回的：\n{replied}")
    if seen:
        parts.append("资料里给你看过的几节（都没写到）：\n" + "\n".join(f"- {title}" for title in seen))
    parts.append(f"源码最外层：\n{top}")
    for number, (action, result) in enumerate(history, 1):
        parts.append(f"第 {number} 步：{action}\n结果：\n{result}")
    if left <= 1:
        parts.append("这是最后一步，必须给 answer。")
    else:
        parts.append(f"现在是第 {len(history) + 1} 步（连这步还剩 {left} 步），输出一行 JSON：")
    return [ChatMessage("system", system), ChatMessage("user", "\n\n".join(parts))]


def parse_action(text: str) -> dict | None:
    """The first JSON object in the model's text, or None."""
    start = text.find("{")
    while start != -1:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return value if isinstance(value, dict) else None
    return None


def _shorten(history: list[tuple[str, str]]) -> None:
    """Keep all results within HISTORY_CHARS by shortening the oldest first (in place)."""
    total = sum(len(result) for _, result in history)
    for n, (action, result) in enumerate(history[:-1]):
        if total <= HISTORY_CHARS:
            return
        if len(result) > OLD_RESULT_CHARS:
            history[n] = (action, result[:OLD_RESULT_CHARS] + "……（前面看过，已省略）")
            total -= len(result) - len(history[n][1])


def _run_tool(tree: SourceTree, action: dict, read: list[str]) -> str:
    """Blocking: what the action finds, or why it could not."""
    try:
        if "list" in action:
            return tree.list(str(action.get("list") or ""))
        if "search" in action:
            found = tree.search(str(action.get("search") or ""), str(action.get("in") or ""))
            return "\n".join(found) if found else "没有搜到"
        if "read" in action:
            path = str(action.get("read") or "")
            start = int(action.get("from") or 1)
            end = int(action["to"]) if action.get("to") is not None else None
            text = tree.read(path, start, end)
            if path not in read:
                read.append(path)
            return text
    except SourceError as exc:
        return f"出错：{exc}"
    except (TypeError, ValueError):
        return "出错：参数不对，行号要是数字"
    return "出错：看不懂这一步，只能用 list、search、read 或 answer"


async def investigate(
    tree: SourceTree,
    ask: Ask,
    *,
    name: str,
    project: str,
    question: str,
    replied: str = "",
    seen: list[str] | None = None,
    tone: Tone | None = None,
) -> Finding:
    """Read the source a step at a time until the model gives an answer or STEPS run out."""
    revision = await asyncio.to_thread(tree.revision)
    label = f"，{revision.commit}，{revision.date}" if revision.commit else ""
    try:
        top = await asyncio.to_thread(tree.list, "")
    except SourceError as exc:
        return Finding(problem=str(exc))
    history: list[tuple[str, str]] = []
    finding = Finding()
    for step in range(STEPS):
        messages = build_messages(
            name=name,
            project=project,
            revision=label,
            question=question,
            replied=replied,
            seen=seen or [],
            top=top,
            history=history,
            left=STEPS - step,
            tone=tone,
        )
        text = await ask(messages, STEP_TOKENS)
        finding.steps += 1
        action = parse_action(text)
        if action is None:
            history.append((text.strip()[:200], "出错：要输出一行 JSON"))
            continue
        if "answer" in action:
            text = OPENER.sub("", " ".join(str(action.get("answer") or "").split()))
            finding.answer = text[:ANSWER_CHARS]
            finding.sure = action.get("sure") is True and bool(finding.answer)
            files = action.get("files")
            finding.files = [str(f) for f in files][:5] if isinstance(files, list) else []
            if not finding.answer:
                finding.problem = "模型给的结论是空的"
            return finding
        result = await asyncio.to_thread(_run_tool, tree, action, finding.read)
        history.append((json.dumps(action, ensure_ascii=False), result[:RESULT_CHARS]))
        _shorten(history)
    finding.problem = f"{STEPS} 步内没有给出结论"
    return finding
