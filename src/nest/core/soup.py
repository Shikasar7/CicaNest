"""海龟汤 (小游戏): she makes up a puzzle — a strange 汤面 the user sees, and the 汤底 that explains it, which
only she knows — and the user asks yes-or-no questions until they can tell what happened. Her model writes the
puzzle and judges each question (是 / 不是 / 无关 / 是也不是) with a word of her own; a guess at the 汤底 that
gets its key points right solves it. The 汤底 is never shown in the app while the game goes on.

The pure parts are here (what she is asked, reading what she answers); nest.core.games plays it.
"""

from __future__ import annotations

import json
import re
from typing import Any

FLAVORS = {
    "clear": "清汤：不涉及死亡、犯罪和恐怖，温馨、脑洞或生活里的小误会",
    "red": "红汤：可以有悬疑、死亡和反转，但不写血腥细节",
    "funny": "搞笑：结局让人哭笑不得，越离谱越好，但要讲得通",
}
FLAVOR_NAMES = {"clear": "清汤", "red": "红汤", "funny": "搞笑"}
LEVELS = {"easy": "简单：关键点少，问十来个问题就能猜到", "normal": "普通", "hard": "烧脑：反转多一点"}
LEVEL_NAMES = {"easy": "简单", "normal": "普通", "hard": "烧脑"}
ANSWERS = ("是", "不是", "无关", "是也不是")
QUESTION_MAX = 200
QUESTIONS_MOST = 80
COOK_TOKENS = 1500
JUDGE_TOKENS = 400
LINE_MAX = 60

COOK = """你要和对方玩海龟汤，你来出题。请原创一道海龟汤（不要用网上流传的现成题目）。
口味：{flavor}
难度：{level}
汤面是一段让人摸不着头脑的离奇结果（40 到 120 字），只写发生了什么，不解释原因；
汤底是完整的真相（80 到 250 字），要能合理解释汤面里的每一个奇怪之处；
关键点是猜中汤底必须说到的 2 到 4 件事；提示 3 条，由浅到深，不能直接说出汤底。

只输出一个 JSON 对象，不要别的话：
{{"title": "4 到 8 个字的题目", "surface": "汤面", "bottom": "汤底", "keys": ["关键点"],
"hints": ["提示1", "提示2", "提示3"]}}"""

JUDGE = """你在和对方玩海龟汤，你是出题的人。
题目：{title}
汤面（对方看得到）：{surface}
汤底（只有你知道，绝对不能说出来或暗示太多）：{bottom}
猜中必须说到的关键点：{keys}
之前问过的：
{asked}

对方这次{what}：{question}

{task}
再用你自己的语气说一句很短的话（不超过 25 个字），像边玩边随口说的，不能泄露汤底。{stickers}
只输出一个 JSON 对象，不要别的话：
{shape}"""
ASK_TASK = (
    "按海龟汤的规则判断这个问题：符合汤底答「是」，不符合答「不是」，和真相无关答「无关」，"
    "部分对答「是也不是」。问到关键点附近时 close 为 true。"
)
ASK_SHAPE = '{"answer": "是/不是/无关/是也不是", "close": true 或 false, "line": "你说的话"}'
GUESS_TASK = (
    "判断对方是不是猜中了汤底：关键点基本都说到了才算猜中（solved 为 true）；"
    "只说中一部分时 solved 为 false，close 为 true。"
)
GUESS_SHAPE = '{"solved": true 或 false, "close": true 或 false, "line": "你说的话"}'
STICKERS = "\n这句话最后可以带一个表情（写它的编号，比如 [表情1]）：\n{listed}"

HINT = (
    "（你们正在桌面上玩海龟汤，你出的题「{title}」。汤面：{surface}\n"
    "汤底只有你知道：{bottom}\n对方已经问了 {count} 个问题。聊天时绝对不能说出汤底；"
    "对方在聊天里问是不是的问题，也照规则只答是、不是、无关或是也不是。）"
)
HINT_AFTER = "（你们刚在桌面上玩完一局海龟汤「{title}」：{result}。汤底是：{bottom}）"

_OBJECT = re.compile(r"\{.*\}", re.S)


def read_json(text: str) -> dict[str, Any] | None:
    """The JSON object in what the model wrote (it may wrap it in words or a code block)."""
    found = _OBJECT.search(text or "")
    if not found:
        return None
    try:
        value = json.loads(found.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any, most: int) -> str:
    return " ".join(str(value or "").split())[:most]


def read_puzzle(text: str) -> dict[str, Any] | None:
    """The puzzle she wrote, cleaned up; None when it is not one."""
    found = read_json(text)
    if found is None:
        return None
    title, surface, bottom = (
        _text(found.get("title"), 20),
        _text(found.get("surface"), 400),
        _text(found.get("bottom"), 800),
    )
    keys = [_text(k, 80) for k in found.get("keys") or [] if _text(k, 80)][:5]
    hints = [_text(h, 120) for h in found.get("hints") or [] if _text(h, 120)][:4]
    if not (title and surface and bottom):
        return None
    return {"title": title, "surface": surface, "bottom": bottom, "keys": keys, "hints": hints}


def read_judgement(text: str, *, guess: bool) -> dict[str, Any] | None:
    found = read_json(text)
    if found is None:
        return None
    line = _text(found.get("line"), LINE_MAX * 2)
    close = bool(found.get("close"))
    if guess:
        return {"solved": bool(found.get("solved")), "close": close, "line": line}
    answer = _text(found.get("answer"), 10)
    answer = next((a for a in sorted(ANSWERS, key=len, reverse=True) if answer.startswith(a)), "")
    if not answer:
        return None
    return {"answer": answer, "close": close, "line": line}


def asked_lines(questions: list[dict[str, Any]]) -> str:
    lines = []
    for q in questions:
        if q.get("answer") is None and q.get("solved") is None:
            continue
        said = ("猜：" if q.get("guess") else "问：") + q["q"]
        got = ("猜中了" if q.get("solved") else "没猜中") if q.get("guess") else q.get("answer")
        lines.append(f"- {said} → {got}")
    return "\n".join(lines[-30:]) or "（还没有）"


def shown_state(state: dict[str, Any], *, ended: bool) -> dict[str, Any]:
    """What the app may see: never the 汤底 (nor the key points, nor hints not given yet) while it goes on."""
    hints = state.get("hints") or []
    given = int(state.get("given", 0))
    out = {
        "flavor": state.get("flavor", "clear"),
        "title": state.get("title", ""),
        "surface": state.get("surface", ""),
        "questions": state.get("questions", []),
        "hints": hints[:given],
        "hints_left": max(0, len(hints) - given),
        "preparing": bool(state.get("preparing")),
        "error": state.get("error", ""),
    }
    if ended:
        out["bottom"] = state.get("bottom", "")
        out["keys"] = state.get("keys", [])
    return out
