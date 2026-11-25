"""有时间概念: in a private chat she is told the time, and long pauses are marked."""

from __future__ import annotations

import time

from nest.core.prompt import build_private_prompt, clock_label, gap_label


def at(month: int, day: int, hour: int, minute: int = 0) -> float:
    return time.mktime((2026, month, day, hour, minute, 0, 0, 0, -1))


def row(text: str, sent_at: float, *, hers: bool = False) -> dict[str, object]:
    return {"text": text, "sent_at": sent_at, "from_self": hers, "sender_name": "阿星"}


def test_the_clock_reads_like_a_person_would_say_it() -> None:
    assert clock_label(at(11, 24, 15, 20)) == "11月24日 星期二 15:20"


def test_pauses() -> None:
    assert gap_label(at(11, 24, 15), at(11, 24, 15, 29)) == ""
    assert gap_label(at(11, 24, 15), at(11, 24, 15, 45)) == "（过了 45 分钟）"
    assert gap_label(at(11, 24, 9), at(11, 24, 12, 30)) == "（过了 3 小时）"
    # Past midnight it is another day, however short the pause.
    assert gap_label(at(11, 24, 23, 20), at(11, 25, 0, 10)) == "（第二天，11月25日 星期三 00:10）"
    assert gap_label(at(11, 20, 9), at(11, 24, 9, 5)) == "（过了 4 天，11月24日 星期二 09:05）"


def test_the_private_prompt_knows_the_time_and_the_pauses() -> None:
    context = [
        row("晚安", at(11, 23, 23, 50)),
        row("晚安，明天见", at(11, 23, 23, 51), hers=True),
    ]
    batch = [row("早呀", at(11, 24, 8, 30)), row("我起来了", at(11, 24, 8, 31))]
    _, user = build_private_prompt(
        persona="你是阿鲸。", bot_name="阿鲸", context=context, batch=batch, now=at(11, 24, 8, 31)
    )
    text = user.content
    assert "阿星：晚安\n你（阿鲸）：晚安，明天见" in text
    # The pause between yesterday's last words and this morning's sits between the two.
    assert "新消息：\n（第二天，11月24日 星期二 08:30）\n阿星：早呀\n阿星：我起来了" in text
    assert "现在是 11月24日 星期二 08:31。" in text


def test_without_now_nothing_changes() -> None:
    kwargs = {
        "persona": "你是阿鲸。",
        "bot_name": "阿鲸",
        "context": [row("在吗", at(11, 23, 9))],
        "batch": [row("在吗？", at(11, 24, 9))],
    }
    _, user = build_private_prompt(**kwargs)
    assert "现在是" not in user.content and "过了" not in user.content and "第二天" not in user.content
