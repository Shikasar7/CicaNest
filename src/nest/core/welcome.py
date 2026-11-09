"""欢迎新人: when someone joins a group that has it on, one bot there says a short welcome, spoken if it can.

Every one of our accounts in the group is told of the join; the brain welcomes once (nest.core.brain). These
are the parts that need no brain: what the model is asked, and how its answer becomes one short line.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from nest.llm.base import ChatMessage

DELAY = (3.0, 8.0)  # seconds after the join, like someone noticing; random in between
STALE = 300.0  # a join older than this when it arrives (a replay after reconnecting) is left alone
ONCE = 600.0  # the same person joining the same group again within this long is welcomed once
MAX_PER_WINDOW = 3  # a group welcomes at most this many people per ONCE (a wave of joins: the first few)
MAX_CHARS = 40
MAX_TOKENS = 600  # a thinking model thinks first, inside this limit; one short line still fits after

# The welcome says 欢迎 and calls the person by their name, or 新朋友 when it is not known; never 新人.
ASK = (
    "群里刚来了{who}。用你自己的口吻欢迎ta：一定要说“欢迎”，称呼ta{call}，不要说“新人”。像在群里随口打招呼："
    "二十个字以内，口语，不要表情符号，不要加引号，不要解释，只输出这一句。"
)
# A persona with a catchphrase says the same line every time; the bot's last few welcomes are shown to it.
AGAIN = "你前几次欢迎别人是这么说的：{said}。这次换个说法，不要重复这些句子。"
RECENT = 3
FRIEND = "新朋友"
QUOTES = "\"'“”‘’「」『』"
NAME_CHARS = 16
LEAD = "，,、。.！!？? "


def spoken(name: str) -> str:
    """A group name as it can be said aloud: without emoji and symbols (「招财纳福✨✨」 is 招财纳福), "" when
    nothing sayable is left (「😏」)."""
    kept = "".join(ch for ch in name if not _unsayable(ch))
    return re.sub(r"\s+", " ", kept).strip()[:NAME_CHARS]


def _unsayable(ch: str) -> bool:
    # So: emoji and pictographs; Sk/Sm: modifier and math symbols; Cf/Cs/Co/Mn: joiners, variation selectors,
    # private use (QQ's own faces).
    return unicodedata.category(ch) in ("So", "Sk", "Sm", "Cf", "Cs", "Co", "Mn")


def prompt(persona: str, bot_name: str, name: str, said: Sequence[str] = ()) -> list[ChatMessage]:
    """`name` as spoken(); `said`: the bot's latest welcomes, newest first."""
    who, call = (f"「{name}」", f"「{name}」") if name else (f"一位{FRIEND}", f"“{FRIEND}”")
    ask = ASK.format(who=who, call=call)
    if said:
        ask += AGAIN.format(said="；".join(f"“{s}”" for s in said[:RECENT]))
    return [
        ChatMessage("system", persona.strip() or f"你是群里的{bot_name}。"),
        ChatMessage("user", ask),
    ]


def line(text: str, name: str = "") -> str:
    """The model's answer as one short line that welcomes: the first non-empty line, unquoted, without
    emoji, 新人 said as 新朋友, 欢迎 put in front when the model left it out, cut to MAX_CHARS."""
    first = next((part.strip() for part in text.splitlines() if part.strip()), "")
    first = first.strip(QUOTES).strip()
    if first.upper() == "SKIP":  # some personas teach the model to stay quiet; a welcome is never quiet
        return ""
    first = "".join(ch for ch in first if not _unsayable(ch))
    first = re.sub(r"\s+", " ", first).strip().lstrip(LEAD).strip(QUOTES).strip()
    if not first:
        return ""
    first = first.replace("新人", FRIEND)
    if "欢迎" not in first:
        call = name or FRIEND
        first = f"欢迎{first}" if first.startswith(call) else f"欢迎{call}，{first}"
    return first[:MAX_CHARS]


def fallback(name: str) -> str:
    return f"欢迎{name or FRIEND}！"
