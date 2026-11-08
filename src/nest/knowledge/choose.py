"""项目资料, the second look: the answering bot's own model picks, from the retrieved candidates, the sections
that answer the question.

Why: a large model ordering the candidates puts the section that answers first more often than the
reranker, and it rescues answers the reranker scored under FLOOR or ranked past the sections handed over.
It is worse than the reranker at saying the docs do not answer a question, so whether the docs answer it
stays the reranker's call (Retrieval.found); the model only decides which sections the bot reads.

Each candidate is shown as its id, title, aliases and the first SUMMARY_CHARS of its body, far cheaper than
the whole table of contents.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from nest.knowledge.loader import Section
from nest.llm.base import ChatMessage

SUMMARY_CHARS = 120
MAX_CHOSEN = 6
CHOOSE_MAX_TOKENS = 400
CHOOSE_RULES = (
    "你是 {name} 帮助资料的排序助手。下面是检索找来的候选小节，每节一段：「[id] 标题 + 摘要」。"
    "按能不能回答用户的问题，从最相关到不相关排好，只保留真能回答的，最多 {most} 个。"
    '只输出一行 JSON：{{"ids": ["id1", "id2"]}}。一个都回答不了就输出 {{"ids": []}}。'
)


def summary(section: Section) -> str:
    aliases = "、".join(section.values("也叫"))
    body = " ".join(section.body.split())[:SUMMARY_CHARS]
    return f"[{section.id}] {section.title}" + (f"（也叫：{aliases}）" if aliases else "") + f"：{body}"


def build_choose_prompt(name: str, question: str, candidates: Sequence[Section]) -> list[ChatMessage]:
    listing = "\n".join(summary(s) for s in candidates)
    return [
        ChatMessage("system", CHOOSE_RULES.format(name=name, most=MAX_CHOSEN)),
        ChatMessage("user", f"候选：\n{listing}\n\n问题：{question}"),
    ]


def parse_choice(text: str, known: Sequence[str]) -> list[str] | None:
    """The chosen ids, best first, only those among the candidates and each once. None: no readable JSON."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    ids = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return None
    allowed = set(known)
    chosen = [str(i) for i in ids if str(i) in allowed]
    return list(dict.fromkeys(chosen))[:MAX_CHOSEN]
