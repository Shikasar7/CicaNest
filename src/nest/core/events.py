"""Platform-neutral events and actions.

Adapters translate platform payloads into these types and back; nothing past the adapter layer sees
OneBot (or Slack, Bilibili…) shapes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Segment:
    """One piece of a rich message: "text", "at", "reply", "image", "face"…"""

    type: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """A place where messages happen, as seen from one of our accounts."""

    platform: str  # "qq", "discord", "wecom"
    account: str  # our account id on that platform, e.g. the bot's QQ number; never holds a colon
    kind: Literal["group", "private"]
    id: str  # group number / peer id

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.account}:{self.kind}:{self.id}"


@dataclass(frozen=True, slots=True)
class MessageEvent:
    channel: ChannelRef
    message_id: str
    sender_id: str
    sender_name: str
    segments: tuple[Segment, ...]
    text: str  # plain-text rendering of segments
    timestamp: float
    from_self: bool = False
    mentions: frozenset[str] = frozenset()
    reply_to: str | None = None
    # Fetched from history after a reconnect rather than received live; never a reply trigger.
    backfilled: bool = False
    # Another program's bot or webhook: kept as context, never a reason to speak (two would talk forever).
    from_bot: bool = False


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    channel: ChannelRef
    name: str


@dataclass(frozen=True, slots=True)
class ChannelsSeen:
    """An adapter learned channel names on its own time: Discord tells them after connecting."""

    infos: tuple[ChannelInfo, ...]


@dataclass(frozen=True, slots=True)
class RecallEvent:
    channel: ChannelRef
    message_id: str
    timestamp: float


JOINED = "（加入了群聊）"


@dataclass(frozen=True, slots=True)
class MemberJoined:
    """Someone joined a group one of our accounts is in. `name`: their name there, "" when not known."""

    channel: ChannelRef
    user_id: str
    name: str
    timestamp: float

    def as_message(self) -> MessageEvent:
        """The join as a line in the group's history, so the console and later prompts see it."""
        return MessageEvent(
            channel=self.channel,
            message_id=f"join:{self.user_id}:{int(self.timestamp)}",
            sender_id=self.user_id,
            sender_name=self.name or self.user_id,
            segments=(Segment("text", {"text": JOINED}),),
            text=JOINED,
            timestamp=self.timestamp,
        )


@dataclass(frozen=True, slots=True)
class CaughtUp:
    """An account finished fetching what it missed. `gaps`: each channel with the newest message it had
    stored before; `until`: when this connection started (later messages arrive live)."""

    account: str
    gaps: tuple[tuple[ChannelRef, float], ...]
    until: float


Event = MessageEvent | RecallEvent | CaughtUp | ChannelsSeen | MemberJoined


@dataclass(frozen=True, slots=True)
class SendMessage:
    channel: ChannelRef
    segments: tuple[Segment, ...]
    idempotency_key: str
    trigger: str | None = None  # the message this answers: some platforms only allow an answer to one


Action = SendMessage


@dataclass(frozen=True, slots=True)
class ActionResult:
    # "unknown": the request may have reached the platform; callers must not blindly retry.
    state: Literal["succeeded", "failed", "unknown"]
    message_id: str | None = None
    error: str | None = None
