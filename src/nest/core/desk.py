"""桌面聊天: talking with a bot from the console or the desktop app instead of on its platform.

It is the bot's private chat with one person, seen through a second window. What is typed here is stored in
that chat as that person's message (its id starts with "app:"), the bot answers as in any private chat, and
an answer to a message typed here is stored and shown here instead of being sent to the platform. So there is
one history, one note of older talk and one long-term memory, whichever side the talking happened on; and the
chat goes on while the bot's account is offline.

A bot nobody talks with in private gets a chat that only exists here: its other side is DESK_PEER, an id no
platform hands out. So does a companion that lives only here (platform DESK, 伙伴模式): it has no account on
any platform, and that chat is all it has.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from nest.core.desk_files import file_line
from nest.core.desk_media import picture_url
from nest.core.events import ActionResult, ChannelRef, MessageEvent, RecallEvent, Segment
from nest.storage.bots import UNSET, BotChannel, BotConfig, BotRepo
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.storage.settings import SettingsRepo

APP = "app:"  # how the id of a message typed or shown here starts
DESK_PEER = "desk"
MAX_CHARS = 4000
NAME_LOOKBACK = 50
DEFAULT_NAME = "我"
CHOSEN = "desk_chats"  # setting: bot id -> the private chat (channel id) its desk window shows
ORDER = "desk_order"  # setting: bot ids in the order the desk lists them

Emit = Callable[[MessageEvent | RecallEvent], Awaitable[None]]

if TYPE_CHECKING:
    from nest.core.groups import DeskGroups


def from_app(message_id: Any) -> bool:
    return isinstance(message_id, str) and message_id.startswith(APP)


# Her lines of her own accord in her chat with the user: the pet's 碎碎念 (nest.core.chatter) and 主动找你
# (nest.core.outreach). Their message ids start APP + the kind.
PROACTIVE = ("c:", "o:")
PICTURE = "p:"  # a picture she drew for the user
NOTICE = "n:"  # a reminder that is due, a report of a background task: hers, but not an answer
PLAIN = re.compile(rf"{APP}[0-9a-f]+")  # an answer (or something you typed): none of the kinds above
# 分条: an answer of a few short lines is shown as that many messages, the way people type them. The chat
# window draws an answer still being written by the same rule (web/src/lib/desk-stream.ts).
BUBBLES = 4
BUBBLE_CHARS = 40
LISTED = re.compile(r"\s*(?:[-•·*]|\d+[.、)）])")


def unanswered(rows: Sequence[dict[str, Any]], since: float) -> list[str]:
    """The kinds of her own-accord lines since `since` that nobody has answered yet: the ones after the user's
    last word in `rows` (her chat, oldest first), newest first. Her other lines (an answer, a reminder) are
    passed over; the user's ends it. So the pet and her messages can tell when the other was not answered:
    nobody tries the other way to get through."""
    kinds: list[str] = []
    for row in reversed(rows):
        if not row["from_self"] or row["sent_at"] < since:
            break
        said = str(row.get("external_id") or "")
        kinds += [kind for kind in PROACTIVE if said.startswith(APP + kind)]
    return kinds


def bubbles(reply: str) -> list[str]:
    """What of a reply is shown as messages of its own: its lines when they are few and short talk, else the
    whole."""
    lines = reply.split("\n")
    short = all(len(line) <= BUBBLE_CHARS and not LISTED.match(line) for line in lines)
    return lines if len(lines) <= BUBBLES and short else [reply]


def asked_before(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Her newest answer at the end of her chat (`rows`, oldest first) and what it answers: (her messages, the
    user's just before them). Both empty when the chat does not end with an answer to something typed here
    (her own-accord lines and reminders are no answers; what was taken back does not count)."""
    here = [row for row in rows if not row.get("recalled_at")]
    cut = len(here)
    while cut and here[cut - 1]["from_self"]:
        cut -= 1
    answers = [row for row in here[cut:] if PLAIN.fullmatch(str(row["external_id"] or ""))]
    start = cut
    while start and not here[start - 1]["from_self"]:
        start -= 1
    asked = here[start:cut]
    if not answers or not asked or not from_app(asked[-1]["external_id"]):
        return [], []
    return answers, asked


def sticker_segment(sticker_id: int, label: str) -> Segment:
    """A 表情包 in the desk chat: the picture is the collection's (shown from /api/stickers/<id>/image), the
    label is how it reads in the chat log."""
    return Segment("sticker", {"id": sticker_id, "label": label})


def picture_segment(name: str) -> Segment:
    """A picture you sent her here (nest.core.desk_media): read by the vision model like any picture."""
    return Segment("image", {"url": picture_url(name), "file": name})


def voice_segment(name: str) -> Segment:
    """Her words as she said them aloud: shown with the words, played from the desk chat."""
    return Segment("voice", {"media": name})


def sticker_line(label: str, *, hers: bool) -> str:
    """How a sticker reads in the history her prompt is made from. Her own read differently, so she does not
    take them for words to type (as nest.core.stickers.relabel does in the groups)."""
    return f"（发了表情：{label}）" if hers else f"[表情：{label}]"


def chats_of(bot: BotConfig) -> list[BotChannel]:
    """The private chats a bot answers, the one that only exists here last."""
    chats = [c for c in bot.channels.values() if c.channel.kind == "private" and c.enabled]
    return sorted(chats, key=lambda c: (c.channel.id == DESK_PEER, c.channel_id))


def chat_of(bot: BotConfig, chosen: int | None = None) -> BotChannel | None:
    """The private chat the desk window of this bot shows: the chosen one, else the first."""
    chats = chats_of(bot)
    return next((c for c in chats if c.channel_id == chosen), chats[0] if chats else None)


def desk_ref(bot: BotConfig) -> ChannelRef:
    return ChannelRef(bot.account_id.partition(":")[0], bot.self_id, "private", DESK_PEER)


async def chosen_chats(db: Database) -> dict[str, int]:
    stored = (await SettingsRepo(db).all()).get(CHOSEN)
    if not isinstance(stored, dict):
        return {}
    return {str(bot): chat for bot, chat in stored.items() if isinstance(chat, int)}


async def order(db: Database) -> list[str]:
    stored = (await SettingsRepo(db).all()).get(ORDER)
    return [str(b) for b in stored if isinstance(b, str)] if isinstance(stored, list) else []


def sort_bots(bots: list[BotConfig], wanted: list[str]) -> list[BotConfig]:
    """The order chosen on the desk; bots not in it after, in the order they came."""
    rank = {bot_id: n for n, bot_id in enumerate(wanted)}
    return sorted(bots, key=lambda b: rank.get(b.id, len(rank)))


async def open_chat(
    repo: BotRepo, store: MessageStore, bot: BotConfig, *, persona_id: int | None, remember: bool
) -> int:
    """The bot's chat that only exists here, made now (the caller reloads the brain). Its channel id."""
    channel_id = await store.channel_id(desk_ref(bot))
    await repo.set_channel(
        bot.id,
        channel_id,
        enabled=True,
        activity="mentions",  # unused in a private chat: every message there calls the bot
        persona_id=UNSET if persona_id is None else persona_id,
        cooldown_seconds=0,
        remember=remember or None,
    )
    return channel_id


class Desk:
    def __init__(self, store: MessageStore, emit: Emit) -> None:
        self.store = store
        self.emit = emit  # into the hub's queue: stored, shown, and heard by the brain like any message
        self.groups: DeskGroups | None = None  # 桌面群: what a member says is copied to the others

    async def name_of(self, channel: BotChannel) -> str:
        rows = await self.store.recent(channel.channel_id, limit=NAME_LOOKBACK)
        named = (r["sender_name"] for r in reversed(rows) if not r["from_self"] and r["sender_name"])
        return next(named, DEFAULT_NAME)

    async def say(
        self,
        channel: BotChannel,
        text: str,
        sticker: tuple[int, str] | None = None,
        pictures: Sequence[str] = (),
    ) -> str:
        """The person's message, typed here, with the pictures sent along (their files in desk-media; with or
        without words), or a sticker they picked (its id and label). Returns its id."""
        message_id = f"{APP}{uuid.uuid4().hex}"
        if sticker is not None:
            segments: tuple[Segment, ...] = (sticker_segment(*sticker),)
            text = sticker_line(sticker[1], hers=False)
        else:
            words = (Segment("text", {"text": text}),) if text else ()
            segments = (*(picture_segment(name) for name in pictures), *words)
            text = "\n".join(part for part in ("[图片]" * len(pictures), text) if part)
        await self.emit(
            MessageEvent(
                channel=channel.channel,
                message_id=message_id,
                sender_id=channel.channel.id,
                sender_name=await self.name_of(channel),
                segments=segments,
                text=text,
                timestamp=time.time(),
            )
        )
        return message_id

    async def say_group(
        self,
        refs: Sequence[ChannelRef],
        name: str,
        text: str,
        sticker: tuple[int, str] | None = None,
        pictures: Sequence[str] = (),
        files: Sequence[Segment] = (),
    ) -> str:
        """The person's message in a 桌面群: one message, stored in every member's channel under one id.
        `pictures` as in `say`; `files`: nest.core.desk_files.file_segment."""
        message_id = f"{APP}{uuid.uuid4().hex}"
        if sticker is not None:
            segments: tuple[Segment, ...] = (sticker_segment(*sticker),)
            text = sticker_line(sticker[1], hers=False)
        else:
            words = (Segment("text", {"text": text}),) if text else ()
            segments = (*(picture_segment(p) for p in pictures), *files, *words)
            sent = "[图片]" * len(pictures) + "".join(file_line(str(f.data["name"])) for f in files)
            text = "\n".join(part for part in (sent, text) if part)
        now = time.time()
        for ref in refs:
            await self.emit(
                MessageEvent(
                    channel=ref,
                    message_id=message_id,
                    sender_id=DESK_PEER,
                    sender_name=name,
                    segments=segments,
                    text=text,
                    timestamp=now,
                )
            )
        return message_id

    async def note(self, bot: BotConfig, channel: BotChannel, text: str) -> None:
        """A word from her that is no answer (a picture that could not be drawn)."""
        await self.deliver(bot, channel, [Segment("text", {"text": text})], kind=NOTICE)

    async def show_picture(self, bot: BotConfig, channel: BotChannel, name: str, idea: str) -> None:
        """A picture she drew (its file in desk-media), shown in her chat with the user."""
        await self.emit(
            MessageEvent(
                channel=channel.channel,
                message_id=f"{APP}{PICTURE}{uuid.uuid4().hex}",
                sender_id=bot.self_id,
                sender_name=bot.name,
                segments=(picture_segment(name),),
                text=f"（发了一张图：{idea[:80]}）",
                timestamp=time.time(),
                from_self=True,
            )
        )

    async def take_back(self, refs: Sequence[ChannelRef], message_id: str) -> None:
        """撤回 a message of the desk chat, wherever it is stored: its words are removed."""
        for ref in refs:
            await self.emit(RecallEvent(channel=ref, message_id=message_id, timestamp=time.time()))

    async def deliver(
        self, bot: BotConfig, channel: BotChannel, segments: Sequence[Segment], *, kind: str = ""
    ) -> ActionResult:
        """The bot's answer to a message typed here (or a reminder, a line of her own: `kind` marks its id,
        "c:" for 主动搭话): stored and shown here, never sent to the platform. Words and 表情包 (sticker
        segments) are shown, with her voice (a voice segment) beside the words; anything else is left out."""
        shown = tuple(s for s in segments if s.type in ("text", "sticker", "voice", "game"))
        text = "".join(str(s.data.get("text", "")) for s in shown if s.type == "text")
        if not text:
            text = "".join(
                sticker_line(str(s.data.get("label", "")), hers=True) for s in shown if s.type == "sticker"
            )
        if not text:  # 她邀请你玩 (nest.core.games): a card
            text = "".join(f"[邀请你一起玩{s.data.get('name', '')}]" for s in shown if s.type == "game")
        if not text:
            return ActionResult("failed", error="桌面聊天现在只能显示文字和表情包")
        message_id = f"{APP}{kind}{uuid.uuid4().hex}"
        await self.emit(
            MessageEvent(
                channel=channel.channel,
                message_id=message_id,
                sender_id=bot.self_id,
                sender_name=bot.name,
                segments=shown,
                text=text,
                timestamp=time.time(),
                from_self=True,
            )
        )
        # In a 桌面群 the others hear her (as another of our bots: the brain's chain limit applies).
        if self.groups is not None:
            heard = text
            if not any(s.type == "text" for s in shown):
                heard = "".join(
                    sticker_line(str(s.data.get("label", "")), hers=False)
                    for s in shown
                    if s.type == "sticker"
                )
            for ref in await self.groups.copies(channel.channel):
                await self.emit(
                    MessageEvent(
                        channel=ref,
                        message_id=message_id,
                        sender_id=bot.self_id,
                        sender_name=bot.name,
                        segments=shown,
                        text=heard,
                        timestamp=time.time(),
                    )
                )
        return ActionResult("succeeded", message_id=message_id)
