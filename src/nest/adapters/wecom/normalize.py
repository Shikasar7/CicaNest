"""企业微信 callbacks → nest's events. Pure functions; raise KeyError, TypeError or ValueError on a malformed
one.

A group only ever sends what @s the bot, so every group message calls it: self_id goes into `mentions` without
looking. The callback has no nickname and no group name, and no message id in a quote: the sender is shown by
userid, the group gets its name from the user, and a quote can only be text.

Pictures and files are encrypted with a key per url, which needs a package nest does not have: they stay
placeholders for now. A private voice message arrives with its words, which go where QQ's do.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.core.segments import render_text

PLATFORM = "wecom"
LEADING_AT = re.compile(r"@(\S+)\s*(.*)", re.DOTALL)


def channel_of(body: Mapping[str, Any], account: str) -> ChannelRef:
    if body.get("chattype") == "group":
        return ChannelRef(PLATFORM, account, "group", str(body["chatid"]))
    return ChannelRef(PLATFORM, account, "private", str(body["from"]["userid"]))


def _text(content: str, account: str, *, group: bool) -> list[Segment]:
    """In a group the text starts "@RobotA ": that is the @, not something said."""
    if group:
        found = LEADING_AT.fullmatch(content)
        if found:
            rest = [Segment("text", {"text": found[2]})] if found[2] else []
            return [Segment("at", {"id": account, "name": found[1]}), *rest]
    return [Segment("text", {"text": content})] if content else []


def _part(kind: str, data: Mapping[str, Any], account: str, *, group: bool) -> list[Segment]:
    if kind == "text":
        return _text(str((data.get("text") or {}).get("content") or ""), account, group=group)
    if kind == "voice":
        words = str((data.get("voice") or {}).get("content") or "")
        return [Segment("record", {"words": words} if words else {})]
    if kind in ("image", "file", "video"):
        return [Segment(kind)]
    return []


def _quote(quote: Mapping[str, Any]) -> Segment | None:
    kind = str(quote.get("msgtype") or "")
    parts = _part(kind, quote, "", group=False)
    if kind == "mixed":
        items = (quote.get("mixed") or {}).get("msg_item") or []
        parts = [s for item in items for s in _part(str(item.get("msgtype")), item, "", group=False)]
    words = render_text(tuple(parts))
    return Segment("text", {"text": f"[引用] {words}\n"}) if words else None


def normalize(body: Mapping[str, Any], account: str) -> MessageEvent | None:
    """One aibot_msg_callback body. None for a kind nest has nothing to say about."""
    kind = str(body.get("msgtype") or "")
    group = body.get("chattype") == "group"
    if kind == "mixed":
        items = (body.get("mixed") or {}).get("msg_item") or []
        segments = [s for item in items for s in _part(str(item.get("msgtype")), item, account, group=group)]
    else:
        segments = _part(kind, body, account, group=group)
    if not segments:
        return None
    quoted = _quote(body["quote"]) if isinstance(body.get("quote"), Mapping) else None
    if quoted is not None:
        segments.insert(1 if segments[0].type == "at" else 0, quoted)
    sender = str(body["from"]["userid"])
    shaped = tuple(segments)
    return MessageEvent(
        channel=channel_of(body, account),
        message_id=str(body["msgid"]),
        sender_id=sender,
        sender_name=sender,
        segments=shaped,
        text=render_text(shaped),
        timestamp=float(body.get("create_time") or time.time()),
        mentions=frozenset({account}) if group else frozenset(),
    )
