"""欢迎新人: when someone joins a group that has it on, one bot there says a short welcome, spoken if it can.

Every one of our accounts in the group is told of the join; the brain welcomes once (nest.core.brain). These
are the parts that need no brain: what the model is asked, and how its answer becomes one short line.
"""

from __future__ import annotations

import re

from nest.llm.base import ChatMessage

DELAY = (3.0, 8.0)  # seconds after the join, like someone noticing; random in between
STALE = 300.0  # a join older than this when it arrives (a replay after reconnecting) is left alone
ONCE = 600.0  # the same person joining the same group again within this long is welcomed once
MAX_PER_WINDOW = 3  # a group welcomes at most this many people per ONCE (a wave of joins: the first few)
MAX_CHARS = 40
MAX_TOKENS = 600  # a thinking model thinks first, inside this limit; one short line still fits after

# The welcome calls the person by their name, or 新朋友 when it is not known; never 新人.
ASK = (
    "群里刚来了{who}。用你自己的口吻欢迎ta，称呼ta{call}，不要说“新人”。像在群里随口打招呼："
    "二十个字以内，口语，不要表情符号，不要加引号，不要解释，只输出这一句。"
)
FRIEND = "新朋友"
QUOTES = "\"'“”‘’「」『』"


def prompt(persona: str, bot_name: str, name: str) -> list[ChatMessage]:
    who, call = (f"「{name}」", f"「{name}」") if name else (f"一位{FRIEND}", f"“{FRIEND}”")
    return [
        ChatMessage("system", persona.strip() or f"你是群里的{bot_name}。"),
        ChatMessage("user", ASK.format(who=who, call=call)),
    ]


def line(text: str) -> str:
    """The model's answer as one short line: the first non-empty line, unquoted, 新人 said as 新朋友, cut to
    MAX_CHARS."""
    first = next((part.strip() for part in text.splitlines() if part.strip()), "")
    first = first.strip(QUOTES).strip()
    first = re.sub(r"\s+", " ", first)
    if first.upper() == "SKIP":  # some personas teach the model to stay quiet; a welcome is never quiet
        return ""
    return first.replace("新人", FRIEND)[:MAX_CHARS]


def fallback(name: str) -> str:
    return f"欢迎{name or FRIEND}！"
