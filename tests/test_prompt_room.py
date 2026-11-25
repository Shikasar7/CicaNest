"""Long messages: what is typed in the desk chat is read to its end; elsewhere they keep their length."""

from __future__ import annotations

from typing import Any

import pytest

from nest.core.desk import APP, MAX_CHARS
from nest.core.prompt import DESK_ID, DESK_NEW, build_answer_prompt, build_private_prompt, build_prompt

LONG = "字" * (MAX_CHARS - 1) + "完"


def row(n: int, text: str, *, desk: bool = True, mine: bool = False) -> dict[str, Any]:
    return {
        "id": n,
        "external_id": f"{APP if desk else 'qq'}{n}",
        "from_self": mine,
        "sender_id": "bot" if mine else "desk",
        "sender_name": "阿鲸" if mine else "阿月",
        "sent_at": float(n),
        "text": text,
        "segments": [],
    }


def asked(kind: str, context: list[dict[str, Any]], batch: list[dict[str, Any]]) -> str:
    if kind == "private":
        return build_private_prompt(persona="", bot_name="阿鲸", context=context, batch=batch)[1].content
    common: dict[str, Any] = {"persona": "", "bot_name": "阿鲸", "context": context, "batch": batch}
    common |= {"called": {r["id"] for r in batch}, "peers": {}}
    if kind == "group":
        return build_prompt(**common)[1].content
    return build_answer_prompt(**common, private=True, search=False, now=0.0)[1].content


def test_it_is_the_length_of_the_box() -> None:
    assert APP == DESK_ID and MAX_CHARS <= DESK_NEW


@pytest.mark.parametrize("kind", ["private", "group", "answer"])
def test_what_is_typed_in_the_desk_chat_is_read_to_its_end(kind: str) -> None:
    assert LONG in asked(kind, [], [row(1, LONG)])
    # Just before, hers too: enough to go on talking about it.
    earlier = asked(kind, [row(1, "问" * 1500), row(2, "答" * 1500, mine=True)], [row(3, "第二点再说说")])
    assert earlier.count("问") >= 1500 and earlier.count("答") >= 1500
    # Long ago: the usual length.
    old = [row(1, "旧" * 1500), *(row(n, "嗯") for n in range(2, 12))]
    assert asked(kind, old, [row(20, "在吗")]).count("旧") <= 500


@pytest.mark.parametrize("kind", ["private", "group"])
def test_elsewhere_messages_keep_their_length(kind: str) -> None:
    said = asked(kind, [row(1, "旧" * 1500, desk=False)], [row(2, "新" * 1500, desk=False)])
    usual = 500 if kind == "private" else 180
    assert said.count("旧") == usual and usual <= said.count("新") <= usual + 5


def test_all_together_there_is_an_end_to_it() -> None:
    many = [row(n, "长" * 4000) for n in range(20)]
    said = asked("private", many, many)
    # The new ones 12k, the earlier ones 8k, and the rest at their usual 500.
    assert said.count("长") <= 12_000 + 17 * 500 + 8000 + 20 * 500
    assert LONG in asked("private", [], [*many[:5], row(99, LONG)])  # the newest first
