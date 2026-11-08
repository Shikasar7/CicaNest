"""The platform table: what the rest of nest may ask without importing an adapter."""

from __future__ import annotations

from nest.adapters.base import Adapter
from nest.adapters.onebot11.adapter import OneBotAdapter
from nest.adapters.registry import PLATFORMS, can, capabilities_of, label_of, place_of
from nest.core.prompt import build_answer_prompt, build_private_prompt, build_prompt, build_sort_prompt


def test_qq_keeps_every_capability_it_had() -> None:
    assert capabilities_of("qq") == {
        "fetch_history",
        "list_channels",
        "member_joins",
        "mention",
        "proactive",
        "reply",
        "sees_all",
        "self_echo",
        "send_image",
        "send_text",
        "send_voice",
        "sticker_source",
    }
    assert can("qq:10001", "sees_all") and not can("nobody:1", "sees_all")
    assert capabilities_of("nobody") == frozenset()


def test_an_unknown_platform_still_has_a_name_and_a_place() -> None:
    assert label_of("qq") == "QQ" and label_of("nobody") == "nobody"
    assert place_of("qq") == ("QQ 群", "QQ 私聊") and place_of("nobody") == ("群", "私聊")


async def test_a_qq_number_is_all_that_can_be_looked_at_before_the_scan() -> None:
    assert (await PLATFORMS["qq"].probe({"self_id": "10001"})).ok
    assert not (await PLATFORMS["qq"].probe({"self_id": "abc"})).ok


def test_the_onebot_adapter_keeps_the_contract() -> None:
    assert isinstance(OneBotAdapter(account="1", http_url="http://x", ws_url="ws://x"), Adapter)


def test_prompts_name_the_platform_they_are_on() -> None:
    common = {"persona": "", "bot_name": "鲸", "context": [], "batch": []}
    qq = build_prompt(**common, called=set(), peers={})[0].content
    assert "你在看 QQ 群聊天" in qq
    discord = build_prompt(**common, called=set(), peers={}, place="Discord 频道")[0].content
    assert "你在看 Discord 频道聊天" in discord and "QQ" not in discord
    private = build_private_prompt(**common, place="Discord 私聊")[0].content
    assert "现在是 Discord 私聊：" in private and "QQ" not in private
    place = ("Discord 频道", "Discord 私聊")
    answer = build_answer_prompt(
        **common, called=set(), peers={}, private=False, search=False, now=0, place=place
    )[0].content
    assert "你在 Discord 频道里，" in answer and "QQ" not in answer
    sort = build_sort_prompt(
        bot_name="鲸", context=[], batch=[], called=set(), peers={}, private=True, place=place
    )[0].content
    assert "Discord 私聊里" in sort and "QQ" not in sort
