"""Message segments as nest keeps them, whichever platform they came from.

An @ is Segment("at", {"id", "name"}); `id` is "all" for 全体成员. Messages stored before this form have the
target under "qq" and stay as they are, so readers go through at_target. What a segment renders to when a
model or the console reads it as text lives here too: nothing past the adapters may depend on one of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nest.core.events import Segment

PLACEHOLDERS = {
    "face": "[表情]",
    "mface": "[表情]",
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "forward": "[合并转发]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "poke": "[戳一戳]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
    "music": "[音乐]",
    "share": "[分享]",
    "location": "[位置]",
    "markdown": "[消息]",
}


def at_target(data: Mapping[str, Any]) -> Any:
    """Who an at segment points at, in either form; None when it says nothing."""
    target = data.get("id")
    return target if target is not None else data.get("qq")


def render_text(segments: tuple[Segment, ...]) -> str:
    parts = []
    for segment in segments:
        if segment.type == "text":
            parts.append(str(segment.data.get("text", "")))
        elif segment.type == "at":
            target = at_target(segment.data)
            parts.append("@全体成员" if target == "all" else f"@{segment.data.get('name') or target}")
        elif segment.type == "reply":
            continue
        elif segment.type == "record" and segment.data.get("words"):
            parts.append(f"（语音）{segment.data['words']}")  # the same as a bot's own voice in its context
        elif segment.type == "image" and segment.data.get("summary"):
            parts.append(str(segment.data["summary"]))  # e.g. "[动画表情]"
        else:
            parts.append(PLACEHOLDERS.get(segment.type, f"[{segment.type}]"))
    return "".join(parts).strip()
