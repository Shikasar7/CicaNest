"""Discord payloads → nest's events, nest's segments → what Discord takes. Pure, apart from the Directory,
which remembers what GUILD_CREATE told about servers, channels and roles.

A text channel, or a thread, is one nest group: channel ids are unique across Discord, so "discord:group:<id>"
needs no server id, and each has its own persona, cooldown and history, like a QQ group.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nest.adapters.discord.rest import Upload
from nest.core.events import ChannelRef, MessageEvent, RecallEvent, Segment
from nest.core.segments import render_text

PLATFORM = "discord"
EPOCH_MS = 1420070400000
# Text channels, announcement channels, and the three kinds of thread. Forums (15, 16) cannot be posted in.
TEXT_CHANNELS = (0, 5)
THREADS = (10, 11, 12)
USER_MESSAGES = (0, 19, 20, 23)  # default, reply, and the two kinds of command: the rest are system notices
VIEW_CHANNEL = 1 << 10
ADMINISTRATOR = 1 << 3
MAX_FILE = 20 * 1024 * 1024  # per attachment; a whole request may carry 25 MiB
MAX_USERS = 100  # allowed_mentions takes at most this many ids
MARKUP = re.compile(r"<@!?(?P<user>\d+)>|<@&(?P<role>\d+)>|<#(?P<channel>\d+)>|<a?:(?P<emoji>\w+):\d+>")


def snowflake_at(seconds: float) -> int:
    """The smallest id a message of that moment can have, for "messages after"."""
    return max(0, (int(seconds * 1000) - EPOCH_MS) << 22)


def sent_at(payload: Mapping[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(payload["timestamp"])).timestamp()
    except (KeyError, ValueError):
        return ((int(payload["id"]) >> 22) + EPOCH_MS) / 1000


def display_name(user: Mapping[str, Any], member: Mapping[str, Any] | None = None) -> str:
    nick = (member or {}).get("nick") or (user.get("member") or {}).get("nick")
    return str(nick or user.get("global_name") or user.get("username") or user.get("id") or "")


@dataclass
class Guild:
    name: str = ""
    everyone: int = 0  # permissions of the @everyone role
    roles: dict[str, tuple[str, int]] = field(default_factory=dict)  # role id -> (name, permissions)
    own_roles: set[str] = field(default_factory=set)  # the bot's roles, its managed one included
    managed: set[str] = field(default_factory=set)  # the role Discord made for the bot itself


class Directory:
    def __init__(self, self_id: str) -> None:
        self.self_id = self_id
        self.guilds: dict[str, Guild] = {}
        self.channels: dict[str, dict[str, Any]] = {}

    def add_guild(self, payload: Mapping[str, Any]) -> list[str]:
        """A GUILD_CREATE; the channels and threads it listed."""
        guild_id = str(payload["id"])
        guild = Guild(name=str(payload.get("name") or ""))
        for role in payload.get("roles") or []:
            role_id = str(role.get("id"))
            permissions = int(role.get("permissions") or 0)
            guild.roles[role_id] = (str(role.get("name") or ""), permissions)
            if role_id == guild_id:
                guild.everyone = permissions
            if str((role.get("tags") or {}).get("bot_id") or "") == self.self_id:
                guild.managed.add(role_id)
        member = next(
            (m for m in payload.get("members") or [] if str((m.get("user") or {}).get("id")) == self.self_id),
            None,
        )
        guild.own_roles = {str(r) for r in (member or {}).get("roles") or []} | guild.managed
        self.guilds[guild_id] = guild
        found = []
        for channel in [*(payload.get("channels") or []), *(payload.get("threads") or [])]:
            self.channels[str(channel["id"])] = {**channel, "guild_id": guild_id}
            found.append(str(channel["id"]))
        return found

    def remove_guild(self, guild_id: str) -> None:
        self.guilds.pop(guild_id, None)
        self.channels = {k: v for k, v in self.channels.items() if v.get("guild_id") != guild_id}

    def put_channel(self, payload: Mapping[str, Any]) -> None:
        known = self.channels.get(str(payload["id"]), {})
        self.channels[str(payload["id"])] = {**known, **payload}

    def drop_channel(self, channel_id: str) -> None:
        self.channels.pop(channel_id, None)

    def name(self, channel_id: str) -> str:
        """服务器 / #频道, or 服务器 / #父频道 / 线程名."""
        channel = self.channels.get(channel_id)
        if channel is None:
            return ""
        guild = self.guilds.get(str(channel.get("guild_id")))
        parts = [guild.name] if guild is not None and guild.name else []
        title = str(channel.get("name") or channel_id)
        if channel.get("type") in THREADS:
            parent = self.channels.get(str(channel.get("parent_id")))
            if parent is not None:
                parts.append(f"#{parent.get('name')}")
            parts.append(title)
        else:
            parts.append(f"#{title}")
        return " / ".join(parts)

    def listed(self) -> list[str]:
        """The channels and threads to show: only ones the bot can see, or nest.core.deaf would take the
        messages it never gets for messages it missed."""
        return [
            channel_id
            for channel_id, channel in self.channels.items()
            if channel.get("type") in (*TEXT_CHANNELS, *THREADS) and self._visible(channel)
        ]

    def _visible(self, channel: Mapping[str, Any]) -> bool:
        guild_id = str(channel.get("guild_id"))
        guild = self.guilds.get(guild_id)
        if guild is None:
            return False
        if channel.get("type") in THREADS:  # a thread shows if its channel does; a private one only if joined
            parent = self.channels.get(str(channel.get("parent_id")))
            return parent is None or self._visible(parent)
        permissions = guild.everyone
        for role in guild.own_roles:
            permissions |= guild.roles.get(role, ("", 0))[1]
        if permissions & ADMINISTRATOR:
            return True
        overwrites = {str(o.get("id")): o for o in channel.get("permission_overwrites") or []}
        permissions = _overwritten(permissions, [overwrites.get(guild_id)])
        permissions = _overwritten(permissions, [overwrites.get(role) for role in guild.own_roles])
        permissions = _overwritten(permissions, [overwrites.get(self.self_id)])
        return bool(permissions & VIEW_CHANNEL)

    def role_name(self, guild_id: str, role_id: str) -> str:
        guild = self.guilds.get(guild_id)
        return guild.roles.get(role_id, ("", 0))[0] if guild is not None else ""


def _overwritten(permissions: int, overwrites: Iterable[Mapping[str, Any] | None]) -> int:
    allow = deny = 0
    for overwrite in overwrites:
        if overwrite is not None:
            allow |= int(overwrite.get("allow") or 0)
            deny |= int(overwrite.get("deny") or 0)
    return (permissions & ~deny) | allow


def parse_content(
    content: str, mentioned: Mapping[str, str], directory: Directory, guild_id: str
) -> list[Segment]:
    """A message's text: <@id> becomes an at segment, roles, channels and custom emoji readable text."""
    segments: list[Segment] = []
    text = ""
    last = 0
    for match in MARKUP.finditer(content):
        text += content[last : match.start()]
        last = match.end()
        if match["user"]:
            if text:
                segments.append(Segment("text", {"text": text}))
                text = ""
            user = match["user"]
            segments.append(Segment("at", {"id": user, "name": mentioned.get(user, "")}))
        elif match["role"]:
            text += f"@{directory.role_name(guild_id, match['role']) or '身份组'}"
        elif match["channel"]:
            channel = directory.channels.get(match["channel"])
            text += f"#{channel.get('name')}" if channel else "#频道"
        else:
            text += f":{match['emoji']}:"
    text += content[last:]
    if text:
        segments.append(Segment("text", {"text": text}))
    return segments


def _attachment(item: Mapping[str, Any]) -> Segment:
    """The id, not the url, names a picture: the url is signed and changes."""
    content_type = str(item.get("content_type") or "")
    data = {
        "url": str(item.get("url") or ""),
        "file": str(item.get("id") or ""),
        "name": item.get("filename"),
    }
    if content_type.startswith("image/"):
        return Segment("image", data)
    if content_type.startswith("audio/"):
        return Segment("record", data)
    return Segment("file", data)


def channel_of(payload: Mapping[str, Any], account: str, peers: Mapping[str, str]) -> ChannelRef | None:
    """Where a gateway message happened. `peers`: DM channel id -> the person. Our own DM to someone nest has
    not opened a channel to is not placed; it was stored when it was sent."""
    if payload.get("guild_id"):
        return ChannelRef(PLATFORM, account, "group", str(payload["channel_id"]))
    author = str((payload.get("author") or {}).get("id"))
    peer = peers.get(str(payload.get("channel_id"))) if author == account else author
    return ChannelRef(PLATFORM, account, "private", peer) if peer else None


def normalize(
    payload: Mapping[str, Any],
    channel: ChannelRef,
    directory: Directory,
    *,
    backfilled: bool = False,
) -> MessageEvent | None:
    """One message. None for what is no one's words: joins, pins, boosts. Raises KeyError, ValueError or
    TypeError on a malformed one."""
    if payload.get("type", 0) not in USER_MESSAGES:
        return None
    author = payload["author"]
    sender_id = str(author["id"])
    from_self = sender_id == channel.account
    from_bot = bool(author.get("bot") or payload.get("webhook_id")) and not from_self
    guild_id = str(payload.get("guild_id") or directory.channels.get(channel.id, {}).get("guild_id") or "")
    mentioned = {str(u["id"]): display_name(u) for u in payload.get("mentions") or []}
    segments = parse_content(str(payload.get("content") or ""), mentioned, directory, guild_id)
    reference = payload.get("message_reference") or {}
    reply_to = (
        str(reference["message_id"]) if reference.get("message_id") and not reference.get("type") else None
    )
    if reply_to:
        segments.insert(0, Segment("reply", {"id": reply_to}))
    segments += [_attachment(item) for item in payload.get("attachments") or []]
    segments += [Segment("face", {"name": s.get("name")}) for s in payload.get("sticker_items") or []]
    mentions = set(mentioned)
    guild = directory.guilds.get(guild_id)
    if guild is not None and guild.managed & {str(r) for r in payload.get("mention_roles") or []}:
        mentions.add(channel.account)  # the role Discord made for the bot is the bot
    shaped = tuple(segments)
    return MessageEvent(
        channel=channel,
        message_id=str(payload["id"]),
        sender_id=sender_id,
        sender_name=display_name(author, payload.get("member")) or sender_id,
        segments=shaped,
        text=render_text(shaped),
        timestamp=sent_at(payload),
        from_self=from_self,
        # A bot that @s ours is not asking for anything, not even after a reconnect, when only this is left.
        mentions=frozenset() if from_bot else frozenset(mentions),
        reply_to=reply_to,
        backfilled=backfilled,
        from_bot=from_bot,
    )


def recalls(
    payload: Mapping[str, Any], account: str, peers: Mapping[str, str], at: float
) -> list[RecallEvent]:
    """MESSAGE_DELETE or MESSAGE_DELETE_BULK; nothing for a DM whose person nest does not know."""
    channel_id = str(payload.get("channel_id"))
    if payload.get("guild_id"):
        channel = ChannelRef(PLATFORM, account, "group", channel_id)
    elif channel_id in peers:
        channel = ChannelRef(PLATFORM, account, "private", peers[channel_id])
    else:
        return []
    ids = payload.get("ids") or [payload.get("id")]
    return [RecallEvent(channel, str(message_id), at) for message_id in ids if message_id]


# -- what goes out -----------------------------------------------------------------------------------------


@dataclass
class Outgoing:
    text: str = ""
    uploads: list[Upload] = field(default_factory=list)
    reply_to: str = ""
    users: list[str] = field(default_factory=list)  # the only people this message may notify


def _image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG"):
        return "picture.png", "image/png"
    if data.startswith(b"GIF8"):
        return "picture.gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "picture.webp", "image/webp"
    return "picture.jpg", "image/jpeg"


def _inline(segment: Segment) -> bytes | None:
    """nest's send segments carry files as base64://; anything else is not nest's to read."""
    source = str(segment.data.get("file") or "")
    if not source.startswith("base64://"):
        return None
    try:
        return base64.b64decode(source.removeprefix("base64://"), validate=True)
    except (binascii.Error, ValueError):
        return None


def outgoing(segments: Iterable[Segment]) -> Outgoing:
    """Segments as one Discord message. ValueError when a file is over the limit; a segment Discord has no use
    for (QQ's sub_type, summary…) is skipped."""
    out = Outgoing()
    parts: list[str] = []
    for segment in segments:
        if segment.type == "text":
            parts.append(str(segment.data.get("text", "")))
        elif segment.type == "at":
            target = str(segment.data.get("id") or "")
            if target.isdigit():
                parts.append(f"<@{target}>")
                if target not in out.users:
                    out.users.append(target)
        elif segment.type == "reply":
            out.reply_to = str(segment.data.get("id") or "")
        elif segment.type in ("image", "record"):
            data = _inline(segment)
            if data is None:
                continue
            if len(data) > MAX_FILE:
                raise ValueError(f"文件超过 {MAX_FILE // 1024 // 1024} MB，Discord 不收")
            if segment.type == "image":
                name, content_type = _image_type(data)
                out.uploads.append(Upload(name, data, content_type))
            else:
                out.uploads.append(Upload("voice.mp3", data, "audio/mpeg"))
    out.text = "".join(parts)
    return out


def message_body(text: str, out: Outgoing, nonce: str, *, first: bool) -> dict[str, Any]:
    """`allowed_mentions` is what stops a model that writes @everyone from notifying the whole server: by
    default Discord would parse every kind of mention in the text."""
    quoting = first and bool(out.reply_to)
    body: dict[str, Any] = {
        "allowed_mentions": {"parse": [], "users": out.users[:MAX_USERS], "replied_user": quoting},
        "nonce": nonce,
        "enforce_nonce": True,
    }
    if text:
        body["content"] = text
    if quoting:
        body["message_reference"] = {"message_id": out.reply_to, "fail_if_not_exists": False}
    return body
