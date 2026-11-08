"""The contract every platform adapter implements; nest.adapters.registry lists the platforms."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from nest.core.events import Action, ActionResult, ChannelInfo, ChannelRef, Event

AdapterState = Literal[
    "connecting",
    "connected",
    "reconnecting",
    "auth_failed",  # token rejected
    "misconfigured",  # e.g. the endpoint belongs to a different account
    "offline",  # connected to the bridge, but the platform client itself is offline
    "deaf",  # connected and online by every report, but no message reaches it (set by nest.core.deaf)
    "stopped",
]


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    state: AdapterState
    detail: str = ""  # human-readable reason, shown as-is in the console
    since: float = 0.0


class MediaError(ValueError):
    """Why a picture could not be fetched, in Chinese."""


def parse_recipient(value: str) -> tuple[str, str]:
    """Who gets told something, as stored in a setting: "discord:123" -> ("discord", "123"). Before there were
    other platforms it was a bare QQ number, and that still means QQ."""
    platform, colon, user = value.partition(":")
    return (platform, user) if colon else ("qq", value)


EventSink = Callable[[Event], Awaitable[None]]
StatusSink = Callable[[AdapterStatus], None]
# Channels worth backfilling after a reconnect, each with the timestamp of the newest message already stored:
# only history from that moment on counts as missed.
BackfillSource = Callable[[], Awaitable[Iterable[tuple[ChannelRef, float]]]]


@dataclass(frozen=True)
class FormField:
    """One field of a platform's form in the console."""

    name: str
    label: str
    secret: bool = False  # goes to .env under the account's ref, never comes back to the console
    required: bool = False
    default: str = ""
    hint: str = ""
    identity: bool = False  # it is the account's self id: fixed once the account exists


@dataclass(frozen=True)
class Probed:
    """What trying a connection found. `detail` is one sentence for the console, free of secrets."""

    ok: bool
    self_id: str = ""
    name: str = ""
    detail: str = ""


@runtime_checkable
class Adapter(Protocol):
    platform: str
    account: str
    capabilities: frozenset[str]  # class constant, known while offline; see nest.adapters.registry
    max_text: int  # the longest text of one message, 0 = no limit; a longer one is the adapter's to split
    stuck_send_at: float  # when a send last failed in a way that points at the client itself; 0 = not lately

    async def start(self, emit: EventSink) -> None:
        """Connect and keep reconnecting until stop(); deliver every event through `emit`."""
        ...

    async def stop(self) -> None: ...

    async def perform(self, action: Action) -> ActionResult:
        """Send. Segments the platform has no use for are skipped, not an error: what to send is decided
        upstream by capability, this is the last line."""
        ...

    def sent_by_us(self, message_id: str) -> bool:
        """Whether a message from our own account came from nest, not from another program using it."""
        ...

    async def fetch_media(self, url: str, limit: int) -> bytes:
        """A picture a message pointed at, at most `limit` bytes, the way this account reaches the internet
        (its proxy). Raises MediaError."""
        ...

    async def list_channels(self) -> list[ChannelInfo]: ...

    def status(self) -> AdapterStatus: ...
