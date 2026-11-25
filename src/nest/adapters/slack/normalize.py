"""Slack's message events → nest's events, nest's segments → what Slack takes. Pure functions.

A message's id in Slack is its time, unique in its channel: nest's id of one is "<channel>.<ts>". A channel
(public, private, or a chat of several) is one nest group; a direct message is the person's id. What is said
in a thread answers the thread's first message, and an answer to it goes into the thread.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nest.core.events import ChannelRef, MessageEvent, Segment
from nest.core.segments import render_text

PLATFORM = "slack"
MAX_FILE = 50 * 1024 * 1024
MAX_TEXT = 4000
# People's words; the other subtypes are notices (joins, a new topic, a pinned message…).
WORDS = ("", "file_share", "thread_broadcast", "bot_message", "me_message")
MARKUP = re.compile(r"<(?P<kind>[@#!]?)(?P<target>[^<>|]*)(?:\|(?P<label>[^<>]*))?>")
EVERYONE = ("here", "channel", "everyone")


def message_key(channel: str, ts: str) -> str:
    return f"{channel}.{ts}"


def split_key(key: str) -> tuple[str, str] | None:
    channel, dot, ts = key.partition(".")
    return (channel, ts) if dot and re.fullmatch(r"\d+\.\d+", ts) else None


def person_name(user: Mapping[str, Any]) -> str:
    profile = user.get("profile") if isinstance(user.get("profile"), Mapping) else {}
    return str(
        profile.get("display_name")
        or user.get("real_name")
        or profile.get("real_name")
        or user.get("name")
        or ""
    )


def is_direct(event: Mapping[str, Any]) -> bool:
    return event.get("channel_type") == "im" or str(event.get("channel") or "").startswith("D")


def mentioned(text: str) -> set[str]:
    return {m["target"] for m in MARKUP.finditer(text) if m["kind"] == "@"}


def parse_text(text: str, names: Mapping[str, str]) -> list[Segment]:
    """Slack's markup: <@U1> becomes an at segment; channels, links and <!here> readable text."""
    segments: list[Segment] = []
    words = ""
    last = 0
    for match in MARKUP.finditer(text):
        words += html.unescape(text[last : match.start()])
        last = match.end()
        kind, target, label = match["kind"], match["target"], match["label"]
        if kind == "@":
            if words:
                segments.append(Segment("text", {"text": words}))
                words = ""
            segments.append(Segment("at", {"id": target, "name": label or names.get(target, "")}))
        elif kind == "#":
            words += f"#{html.unescape(label or target)}"
        elif kind == "!":
            called = target.partition("^")[0]
            words += "@所有人" if called in EVERYONE else html.unescape(label or f"@{called}")
        else:
            words += html.unescape(label or target.removeprefix("mailto:"))
    words += html.unescape(text[last:])
    if words:
        segments.append(Segment("text", {"text": words}))
    return segments


def _files(event: Mapping[str, Any]) -> list[Segment]:
    found = []
    for item in event.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("mimetype") or "")
        data = {
            "url": str(item.get("url_private") or ""),
            "file": str(item.get("id") or ""),
            "name": item.get("name"),
        }
        if kind.startswith("image/"):
            found.append(Segment("image", data))
        elif kind.startswith("audio/"):
            found.append(Segment("record", data))
        elif kind.startswith("video/"):
            found.append(Segment("video", {}))
        else:
            found.append(Segment("file", data))
    return found


def normalize(
    event: Mapping[str, Any],
    channel: ChannelRef,
    names: Mapping[str, str],
    *,
    own_bot: str = "",
    backfilled: bool = False,
) -> MessageEvent | None:
    """One message; None for what is no one's words. `own_bot`: our app's bot id, which some of our own
    messages carry in place of the user id. Raises KeyError, TypeError or ValueError on a malformed one."""
    if str(event.get("subtype") or "") not in WORDS:
        return None
    ts = str(event["ts"])
    bot_id = str(event.get("bot_id") or "")
    sender = str(event.get("user") or "")
    from_self = sender == channel.account or bool(own_bot and bot_id == own_bot)
    if from_self:
        sender = channel.account
    sender = sender or bot_id
    if not sender:
        return None
    text = str(event.get("text") or "")
    segments = [*parse_text(text, names), *_files(event)]
    if not segments:
        return None
    thread = str(event.get("thread_ts") or "")
    reply_to = message_key(str(event["channel"]), thread) if thread and thread != ts else None
    if reply_to:
        segments.insert(0, Segment("reply", {"id": reply_to}))
    other_bot = bool(bot_id) and not from_self
    profile = event.get("bot_profile") if isinstance(event.get("bot_profile"), Mapping) else {}
    name = names.get(sender) or str(event.get("username") or profile.get("name") or "") or sender
    shaped = tuple(segments)
    return MessageEvent(
        channel=channel,
        message_id=message_key(str(event["channel"]), ts),
        sender_id=sender,
        sender_name=name,
        segments=shaped,
        text=render_text(shaped),
        timestamp=float(ts),
        from_self=from_self,
        mentions=frozenset() if other_bot else frozenset(mentioned(text)),
        reply_to=reply_to,
        backfilled=backfilled,
        from_bot=other_bot,
    )


# -- what goes out -----------------------------------------------------------------------------------------


@dataclass
class Outgoing:
    text: str = ""
    files: list[tuple[str, bytes]] = field(default_factory=list)  # (name, data)
    reply_to: str = ""


def _inline(segment: Segment) -> bytes | None:
    """nest's send segments carry files as base64://; anything else is not nest's to read."""
    source = str(segment.data.get("file") or "")
    if not source.startswith("base64://"):
        return None
    try:
        return base64.b64decode(source.removeprefix("base64://"), validate=True)
    except (binascii.Error, ValueError):
        return None


def _picture_name(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "picture.png"
    if data.startswith(b"GIF8"):
        return "picture.gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "picture.webp"
    return "picture.jpg"


def escape(text: str) -> str:
    """So that nothing a model writes is markup: no <!channel> that notifies everyone, no made-up link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def outgoing(segments: Iterable[Segment]) -> Outgoing:
    """Segments as one Slack message. ValueError when a file is too large; a segment Slack has no use for is
    skipped."""
    out = Outgoing()
    parts: list[str] = []
    for segment in segments:
        if segment.type == "text":
            parts.append(escape(str(segment.data.get("text", ""))))
        elif segment.type == "at":
            target = str(segment.data.get("id") or "")
            if re.fullmatch(r"[UW][A-Z0-9]+", target):
                parts.append(f"<@{target}> ")
        elif segment.type == "reply":
            out.reply_to = str(segment.data.get("id") or "")
        elif segment.type in ("image", "record"):
            data = _inline(segment)
            if data is None:
                continue
            if len(data) > MAX_FILE:
                raise ValueError(f"文件超过 {MAX_FILE // 1024 // 1024} MB，太大了")
            out.files.append((_picture_name(data) if segment.type == "image" else "voice.mp3", data))
    out.text = "".join(parts).strip()
    return out
