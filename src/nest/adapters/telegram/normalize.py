"""Telegram messages → nest's events, nest's segments → what Telegram takes. Pure functions.

Message ids count per chat, so nest's id of one is "<chat>.<id>". A group is one nest group by its chat id
(negative); a private chat is the person's id. The topics of a forum group are one group: an answer goes into
the topic of what it answers.

Entities count in UTF-16 units, as Telegram counts everything.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nest.adapters.telegram.api import FILE_SCHEME, Upload
from nest.core.events import ChannelRef, MemberJoined, MessageEvent, Segment
from nest.core.segments import render_text

PLATFORM = "telegram"
GROUPS = ("group", "supergroup")
MAX_PHOTO = 10 * 1024 * 1024
MAX_FILE = 50 * 1024 * 1024
MAX_TEXT = 4096
MAX_CAPTION = 1024


@dataclass(frozen=True)
class Me:
    id: str
    username: str = ""
    name: str = ""


def units(text: str) -> int:
    """Its length as Telegram counts."""
    return len(text.encode("utf-16-le")) // 2


def message_key(chat_id: Any, message_id: Any) -> str:
    return f"{chat_id}.{message_id}"


def split_key(key: str) -> tuple[str, int] | None:
    chat, dot, number = key.rpartition(".")
    if not dot or not number.isdigit():
        return None
    return chat, int(number)


def person_name(user: Mapping[str, Any]) -> str:
    full = " ".join(str(user.get(k) or "") for k in ("first_name", "last_name")).strip()
    return full or str(user.get("username") or user.get("id") or "")


def channel_of(message: Mapping[str, Any], account: str) -> ChannelRef | None:
    """Where it was said; None for a broadcast channel, which nobody talks in."""
    chat = message["chat"]
    kind = chat.get("type")
    if kind in GROUPS:
        return ChannelRef(PLATFORM, account, "group", str(chat["id"]))
    if kind == "private":
        return ChannelRef(PLATFORM, account, "private", str(chat["id"]))
    return None


def _sender(message: Mapping[str, Any], me: Me) -> tuple[str, str, bool]:
    """Who said it: (id, name, another program). Posts in a group's or channel's own name have no person."""
    posted = message.get("sender_chat")
    if isinstance(posted, Mapping):
        linked = bool(message.get("is_automatic_forward"))  # a channel's post, copied into its group
        return str(posted["id"]), str(posted.get("title") or posted["id"]), linked
    user = message["from"]
    return str(user["id"]), person_name(user), bool(user.get("is_bot")) and str(user["id"]) != me.id


def parse_text(text: str, entities: Iterable[Mapping[str, Any]], me: Me) -> tuple[list[Segment], set[str]]:
    """Its text with the @s of people as segments, and who is called: the bot by its @name or /command@name,
    people Telegram names by id. Someone else's @name stays text: Telegram does not say whose it is."""
    raw = text.encode("utf-16-le")
    segments: list[Segment] = []
    called: set[str] = set()
    last = 0

    def piece(start: int, end: int) -> str:
        return raw[start * 2 : end * 2].decode("utf-16-le", errors="ignore")

    own = f"@{me.username}".lower() if me.username else ""
    for entity in sorted(entities, key=lambda e: int(e.get("offset") or 0)):
        start = int(entity.get("offset") or 0)
        end = start + int(entity.get("length") or 0)
        if start < last:
            continue
        words = piece(start, end)
        kind = entity.get("type")
        target = ""
        if kind == "mention" and own and words.lower() == own:
            target = me.id
        elif kind == "text_mention" and isinstance(entity.get("user"), Mapping):
            target = str(entity["user"].get("id") or "")
        elif kind == "bot_command" and own and words.lower().endswith(own):
            called.add(me.id)
            continue  # the command's own words stay in the text
        if not target:
            continue
        called.add(target)
        if start > last:
            segments.append(Segment("text", {"text": piece(last, start)}))
        name = me.name if target == me.id else person_name(entity.get("user") or {})
        segments.append(Segment("at", {"id": target, "name": name or words.lstrip("@")}))
        last = end
    rest = piece(last, len(raw) // 2)
    if rest:
        segments.append(Segment("text", {"text": rest}))
    return segments, called


def _media(message: Mapping[str, Any]) -> list[Segment]:
    def ref(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "url": f"{FILE_SCHEME}{item['file_id']}",
            "file": str(item.get("file_unique_id") or item["file_id"]),
        }

    if photos := message.get("photo"):
        return [
            Segment(
                "image", ref(max(photos, key=lambda p: int(p.get("width") or 0) * int(p.get("height") or 0)))
            )
        ]
    if sticker := message.get("sticker"):
        return [Segment("face", {"name": sticker.get("emoji") or ""})]
    if voice := message.get("voice") or message.get("audio"):
        return [Segment("record", ref(voice))]
    if message.get("animation") or message.get("video") or message.get("video_note"):
        return [Segment("video", {})]
    if document := message.get("document"):
        if str(document.get("mime_type") or "").startswith("image/"):
            return [Segment("image", ref(document))]
        return [Segment("file", {**ref(document), "name": document.get("file_name")})]
    if message.get("location") or message.get("venue"):
        return [Segment("location", {})]
    if dice := message.get("dice"):
        return [Segment("text", {"text": f"{dice.get('emoji') or '🎲'} {dice.get('value')}"})]
    if poll := message.get("poll"):
        options = "、".join(str(o.get("text") or "") for o in poll.get("options") or [])
        return [Segment("text", {"text": f"[投票] {poll.get('question')}（{options}）"})]
    return []


def joined(message: Mapping[str, Any], channel: ChannelRef) -> list[MemberJoined]:
    return [
        MemberJoined(channel, str(user["id"]), person_name(user), float(message.get("date") or 0))
        for user in message.get("new_chat_members") or []
        if not user.get("is_bot")
    ]


def normalize(
    message: Mapping[str, Any], channel: ChannelRef, me: Me, *, backfilled: bool = False
) -> MessageEvent | None:
    """One message; None for what is no one's words (joins, pins, a changed title). Raises KeyError, TypeError
    or ValueError on a malformed one."""
    if message.get("text") is not None:
        segments, called = parse_text(str(message["text"]), message.get("entities") or [], me)
    else:
        caption = str(message.get("caption") or "")
        words, called = parse_text(caption, message.get("caption_entities") or [], me)
        segments = [*_media(message), *words]
    if not segments:
        return None
    reply_to = None
    answered = message.get("reply_to_message")
    if isinstance(answered, Mapping) and not answered.get("forum_topic_created"):
        # In a topic everything "answers" the message that made the topic: that is no quote.
        reply_to = message_key(message["chat"]["id"], answered["message_id"])
        segments.insert(0, Segment("reply", {"id": reply_to}))
    sender_id, sender_name, other_bot = _sender(message, me)
    shaped = tuple(segments)
    return MessageEvent(
        channel=channel,
        message_id=message_key(message["chat"]["id"], message["message_id"]),
        sender_id=sender_id,
        sender_name=sender_name or sender_id,
        segments=shaped,
        text=render_text(shaped),
        timestamp=float(message["date"]),
        from_self=sender_id == me.id,
        mentions=frozenset() if other_bot else frozenset(called),
        reply_to=reply_to,
        backfilled=backfilled,
        from_bot=other_bot,
    )


# -- what goes out -----------------------------------------------------------------------------------------


@dataclass
class Outgoing:
    text: str = ""
    uploads: list[tuple[str, Upload]] = field(default_factory=list)  # (method, file)
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


def _picture(data: bytes) -> tuple[str, Upload]:
    if data.startswith(b"GIF8"):
        return "sendAnimation", Upload("animation", "picture.gif", data, "image/gif")
    name, kind = "picture.jpg", "image/jpeg"
    if data.startswith(b"\x89PNG"):
        name, kind = "picture.png", "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        name, kind = "picture.webp", "image/webp"
    if len(data) > MAX_PHOTO:  # as a file it may be larger
        return "sendDocument", Upload("document", name, data, kind)
    return "sendPhoto", Upload("photo", name, data, kind)


def outgoing(segments: Iterable[Segment], usernames: Mapping[str, str]) -> Outgoing:
    """Segments as one Telegram message. `usernames`: person -> their @name; someone without one cannot be
    called and is only named. ValueError when a file is too large; a segment Telegram has no use for is
    skipped."""
    out = Outgoing()
    parts: list[str] = []
    for segment in segments:
        if segment.type == "text":
            parts.append(str(segment.data.get("text", "")))
        elif segment.type == "at":
            target = str(segment.data.get("id") or "")
            handle = usernames.get(target) or str(segment.data.get("name") or "")
            if handle:
                parts.append(f"@{handle} ")
        elif segment.type == "reply":
            out.reply_to = str(segment.data.get("id") or "")
        elif segment.type in ("image", "record"):
            data = _inline(segment)
            if data is None:
                continue
            if len(data) > MAX_FILE:
                raise ValueError(f"文件超过 {MAX_FILE // 1024 // 1024} MB，Telegram 不收")
            if segment.type == "image":
                out.uploads.append(_picture(data))
            else:
                out.uploads.append(("sendVoice", Upload("voice", "voice.mp3", data, "audio/mpeg")))
    out.text = "".join(parts).strip()
    return out
