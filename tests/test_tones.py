"""语气 (nest.core.tones, the 「语气」 page): every answer shares the rules against sounding like a
document; a tone's manner goes into the answer, chat and 看源码 prompts and is repeated at the end."""

from __future__ import annotations

import time
from typing import Any

import pytest

from nest.core import tones
from nest.core.prompt import build_answer_prompt, build_private_prompt, build_prompt
from nest.knowledge.investigate import build_messages

ROW = {
    "id": 1,
    "sender_id": "7",
    "sender_name": "小明",
    "text": "阿鲸 朗读怎么开",
    "segments": [],
    "sent_at": time.time(),
    "from_self": False,
}


def answer(tone: tones.Tone) -> tuple[str, str]:
    system, user = build_answer_prompt(
        persona="你是阿鲸，说话很短。",
        bot_name="阿鲸",
        context=[],
        batch=[ROW],
        called={1},
        peers={},
        private=False,
        search=False,
        now=time.time(),
        tone=tone,
    )
    return system.content, user.content


@pytest.mark.parametrize("tone", tones.BUILTIN, ids=lambda t: t.key)
def test_each_tone_reaches_the_answer_prompt(tone: tones.Tone) -> None:
    system, user = answer(tone)
    assert tones.PLAIN in system and tone.manner in system
    assert system.startswith("你是阿鲸，说话很短。")  # the persona still says who the bot is
    assert "分点就用" not in system  # the old document-like rules are gone
    if tone.key == "persona":
        assert "的语气说" not in user
    else:
        assert f"如果是正经问题，用「{tone.label}」的语气说" in user


def test_chat_takes_a_tone_and_the_persona_tone_changes_nothing() -> None:
    tsundere = tones.BUILTIN_BY_KEY["tsundere"]
    kwargs: dict[str, Any] = {"persona": "你是阿鲸。", "bot_name": "阿鲸", "context": [], "batch": [ROW]}
    group = {**kwargs, "called": {1}, "peers": {}}
    plain = build_prompt(**group)
    assert build_prompt(**group, tone=tones.BUILTIN_BY_KEY["persona"]) == plain
    system, user = build_prompt(**group, tone=tsundere)
    assert system.content.endswith(f"说话的语气：{tsundere.manner}")
    assert user.content.endswith(
        "用「傲娇可爱」的语气说。\n\n只输出你要发的话（一行一条，最多三条），或 SKIP。"
    )
    system, user = build_private_prompt(**kwargs, tone=tsundere)
    assert tsundere.manner in system.content and "用「傲娇可爱」的语气说。" in user.content
    assert build_private_prompt(**kwargs) == build_private_prompt(
        **kwargs, tone=tones.BUILTIN_BY_KEY["persona"]
    )


def test_a_missing_tone_falls_back() -> None:
    own = tones.Tone("custom-1", "海盗", "说话像海盗")
    assert tones.resolve({"custom-1": own}, "custom-1", "crisp") is own
    assert tones.resolve({}, "custom-1", "crisp") == tones.BUILTIN_BY_KEY["crisp"]  # deleted: the default


def test_the_source_reading_prompt_takes_the_tone_too() -> None:
    onee = tones.BUILTIN_BY_KEY["onee"]
    system, _ = build_messages(
        name="阿龙",
        project="DemoApp",
        revision="",
        question="q",
        replied="",
        seen=[],
        top="web/",
        history=[],
        left=10,
        tone=onee,
    )
    assert onee.manner in system.content and "不分点、不加小标题和客套话" in system.content
