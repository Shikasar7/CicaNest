"""A one-character name is called only when said to her, not when it is part of a word."""

from __future__ import annotations

import pytest

from nest.core.brain import calls


@pytest.mark.parametrize(
    "text",
    [
        "星",
        "星，帮我看看这段",
        "星！",
        "@星 看看这个",
        "叫一下@星",
        "星你看看",
        "星在吗",
        "星帮我查一下",
        "那就交给 星 吧",
        "（星）你觉得呢",
        "好的，星？",
    ],
)
def test_said_to_her(text: str) -> None:
    assert calls("星", text)


@pytest.mark.parametrize(
    "text",
    [
        "星期五见",
        "明星也会累",
        "今天看到了流星",
        "星期天你在吗",
        "火星人",
        "abc星def",
        "第3星",
    ],
)
def test_part_of_a_word(text: str) -> None:
    assert not calls("星", text)


def test_a_later_call_counts_after_a_word() -> None:
    assert calls("星", "星期五的事，星你记一下")


def test_longer_names_match_anywhere() -> None:
    assert calls("阿星", "我觉得阿星说得对")
    assert calls("阿星", "阿星们")
    assert not calls("阿星", "阿月说得对")
