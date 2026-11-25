"""One Telegram bot: long polling for what is said, so no public address is needed.

Telegram keeps what a bot did not take for a day: after a break it arrives with the first poll. What was said
well before this connection is kept as context and not answered, like caught-up history elsewhere.
Only one program may poll a bot: another one, or a webhook, makes Telegram answer 409. A webhook is someone's
setting and is left alone; the user is told.

Telegram never sends a bot its own messages: what goes out is delivered as a from_self event here. Two
Telegram bots do not see each other's words either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx

from nest.adapters.base import AdapterStatus, EventSink, MediaError, StatusSink
from nest.adapters.common import Backoff, SentIds, StatusKeeper, split_text
from nest.adapters.telegram.api import (
    API_BASE,
    FILE_SCHEME,
    Conflict,
    Refused,
    TelegramApi,
    TelegramError,
    Unauthorized,
    Upload,
)
from nest.adapters.telegram.normalize import (
    GROUPS,
    MAX_CAPTION,
    MAX_TEXT,
    PLATFORM,
    Me,
    Outgoing,
    channel_of,
    joined,
    message_key,
    normalize,
    outgoing,
    person_name,
    split_key,
    units,
)
from nest.core.events import (
    Action,
    ActionResult,
    ChannelInfo,
    ChannelRef,
    ChannelsSeen,
    Event,
    SendMessage,
)
from nest.llm.http import scrub

log = logging.getLogger(__name__)

POLL_SECONDS = 50
KINDS = ["message", "my_chat_member"]
LATE_SECONDS = 120.0  # said this long before connecting: too late to answer
CONFLICTS = 6  # in a row: not the last connection lingering, but another program
CONFLICT_WAIT = 5.0
REMEMBERED = 4096
KEPT_PEOPLE = 5000
ELSEWHERE = "这个 Telegram bot 在别处也在收消息，两边会互相抢：停掉另一处，或到 @BotFather 换一个新 Token"
WEBHOOK = "这个 Telegram bot 设了 webhook，消息都发到那边去了：不用它的话先去掉 webhook，再保存一次连接设置"
MIGRATED = "这个群升级成了超级群，换了一个 ID：到 bot 的「群」里打开新的那个"


class Wrong(Exception):
    """The connection cannot work until the user changes something."""


class TelegramAdapter:
    platform = PLATFORM
    # sees_all: once the bot's privacy mode is off; until then a group only shows it what calls it.
    capabilities = frozenset(
        {"mention", "proactive", "reply", "sees_all", "send_image", "send_text", "send_voice"}
    )
    max_text = MAX_TEXT
    stuck_send_at = 0.0  # a NapCat thing

    def __init__(
        self,
        *,
        account: str,
        token: str,
        proxy: str = "",
        on_status: StatusSink | None = None,
        api_base: str = API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_seconds: int = POLL_SECONDS,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
        stable_after: float = 60.0,
        conflict_wait: float = CONFLICT_WAIT,
        max_wait: float = 30.0,
    ) -> None:
        self.account = account
        self._token = token
        self._api = TelegramApi(
            token, proxy=proxy or None, api_base=api_base, transport=transport, max_wait=max_wait
        )
        self._status = StatusKeeper(f"Telegram {account}", on_status)
        self._backoff = Backoff(backoff_initial, backoff_max, stable_after)
        self._poll_seconds = poll_seconds
        self._conflict_wait = conflict_wait
        self._task: asyncio.Task | None = None
        self._emit: EventSink | None = None
        self._stopping = False
        self._me = Me(account)
        self._offset = 0
        self._connected_at = 0.0
        self._sent = SentIds()
        self._names: dict[str, str] = {}  # group -> its title, as last told
        self._people: OrderedDict[str, str] = OrderedDict()  # person -> their @name
        self._topics: OrderedDict[str, int] = OrderedDict()  # message -> the topic it is in

    # -- Adapter protocol --------------------------------------------------------------------------

    async def start(self, emit: EventSink) -> None:
        if self._task is None:
            self._stopping = False
            self._emit = emit
            self._task = asyncio.create_task(self._run(), name=f"telegram:{self.account}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._api.aclose()
        self._status.set("stopped", "已停止")

    def status(self) -> AdapterStatus:
        return self._status.current

    def sent_by_us(self, message_id: str) -> bool:
        return message_id in self._sent

    async def list_channels(self) -> list[ChannelInfo]:
        return []  # no such list: a group turns up when someone speaks in it, or the bot is added

    async def fetch_media(self, url: str, limit: int) -> bytes:
        if not url.startswith(FILE_SCHEME):
            raise MediaError("这不是 Telegram 的文件")
        return await self._api.fetch(url.removeprefix(FILE_SCHEME), limit)

    async def perform(self, action: Action) -> ActionResult:
        """Send a message, in pieces if it is long. "failed" = certainly nothing went out; "unknown" = some of
        it may have. Never retried, except a 429, which Telegram says it did not act on."""
        if not isinstance(action, SendMessage) or action.channel.account != self.account:
            return ActionResult("failed", error="这条消息不属于这个账号")
        current = self._status.current
        if current.state != "connected":
            reason = current.detail or current.state
            return ActionResult("failed", error=f"Telegram 当前不在线（{reason}），没有发送")
        try:
            out = outgoing(action.segments, self._people)
        except ValueError as exc:
            return ActionResult("failed", error=str(exc))
        calls = self._calls(out)
        if not calls:
            return ActionResult("failed", error="这条消息里没有 Telegram 能发的内容")
        where = self._where(action)
        quoting = self._quoting(action, out)
        sent: list[str] = []
        for method, body, upload in calls:
            try:
                message = await self._api.send(method, {**where, **quoting, **body}, upload)
            except TelegramError as exc:
                return self._stopped_at(exc, sent)
            quoting = {}  # only the first piece quotes
            key = message_key(action.channel.id, message["message_id"])
            sent.append(key)
            self._sent.add(key)
            await self._deliver_own(message, action.channel)
        return ActionResult("succeeded", message_id=sent[0])

    @staticmethod
    def _calls(out: Outgoing) -> list[tuple[str, dict[str, Any], Upload | None]]:
        text = out.text
        calls: list[tuple[str, dict[str, Any], Upload | None]] = []
        caption = ""
        if out.uploads and text and units(text) <= MAX_CAPTION and out.uploads[0][0] != "sendVoice":
            caption, text = text, ""
        for piece in split_text(text, MAX_TEXT, units):
            calls.append(("sendMessage", {"text": piece}, None))
        for method, upload in out.uploads:
            calls.append((method, {"caption": caption} if caption else {}, upload))
            caption = ""
        return calls

    def _where(self, action: SendMessage) -> dict[str, Any]:
        place: dict[str, Any] = {"chat_id": int(action.channel.id)}
        topic = self._topics.get(action.trigger or "")
        if topic is not None:
            place["message_thread_id"] = topic
        return place

    @staticmethod
    def _quoting(action: SendMessage, out: Outgoing) -> dict[str, Any]:
        key = split_key(out.reply_to)
        if key is None or key[0] != action.channel.id:
            return {}
        return {"reply_parameters": {"message_id": key[1], "allow_sending_without_reply": True}}

    def _stopped_at(self, exc: TelegramError, sent: list[str]) -> ActionResult:
        error = scrub(str(exc), self._token)
        if isinstance(exc, Refused) and exc.parameters.get("migrate_to_chat_id"):
            error = MIGRATED
        if sent:  # what went out went out; the decision should not read as "nothing happened"
            error = f"长消息只发出了前 {len(sent)} 段：{error}"
            return ActionResult("unknown", message_id=sent[0], error=error)
        return ActionResult("failed" if exc.sure else "unknown", error=error)

    async def _deliver_own(self, message: dict[str, Any], channel: ChannelRef) -> None:
        try:
            event = normalize(message, channel, self._me)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Telegram %s 看不懂自己刚发的消息：%s", self.account, type(exc).__name__)
            return
        if event is not None:
            await self._deliver(event)

    async def _deliver(self, event: Event) -> None:
        assert self._emit is not None
        try:
            await self._emit(event)
        except Exception:
            log.exception("Telegram %s 处理事件出错，已跳过这一条", self.account)

    # -- polling -----------------------------------------------------------------------------------

    async def _run(self) -> None:
        self._status.set("connecting", "正在连接")
        if not self._token:
            self._status.set("auth_failed", "还没有填 Token")
            return
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._session()
                detail = "连接被关闭"
            except asyncio.CancelledError:
                raise
            except Wrong as exc:
                self._status.set("misconfigured", str(exc))
                return
            except Unauthorized:
                self._status.set("auth_failed", "Telegram 不认这个 Token：到「系统」页填新的")
                return
            except TelegramError as exc:
                detail = scrub(str(exc), self._token)
            except Exception as exc:
                log.exception("Telegram %s 连接出现未预期的错误", self.account)
                detail = f"内部错误：{type(exc).__name__}"
            if self._stopping:
                break
            wait = self._backoff.next(time.monotonic() - started)
            self._status.set("reconnecting", f"{detail}；{wait:.0f} 秒后重试" if wait >= 1 else detail)
            await asyncio.sleep(wait)

    async def _session(self) -> None:
        me = await self._api.me()
        if str(me.get("id")) != self.account:
            raise Wrong(f"这个 Token 属于 bot {me.get('id')}，不是 {self.account}")
        self._me = Me(self.account, str(me.get("username") or ""), person_name(me))
        self._connected_at = time.time()
        connected = False
        conflicts = 0
        while not self._stopping:
            # The first poll does not wait: it proves the connection.
            wait = self._poll_seconds if connected else 0
            try:
                found = await self._api.updates(self._offset, wait=wait, kinds=KINDS)
            except Conflict as exc:
                if exc.webhook:
                    raise Wrong(WEBHOOK) from None
                conflicts += 1
                if conflicts >= CONFLICTS:  # tried again after the usual wait: the other one may have stopped
                    raise TelegramError(ELSEWHERE) from None
                await asyncio.sleep(self._conflict_wait)
                continue
            conflicts = 0
            if not connected:
                connected = True
                self._status.set("connected", self._me.name)
            for update in found:
                await self._update(update)
                self._offset = max(self._offset, int(update.get("update_id") or 0) + 1)

    async def _update(self, update: dict[str, Any]) -> None:
        try:
            if isinstance(update.get("my_chat_member"), dict):
                await self._membership(update["my_chat_member"])
            elif isinstance(update.get("message"), dict):
                await self._message(update["message"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("Telegram %s 跳过一条无法识别的消息：%s", self.account, type(exc).__name__)

    async def _membership(self, change: dict[str, Any]) -> None:
        """The bot was added to a group (put out of one: what was said there stays)."""
        if (change.get("new_chat_member") or {}).get("status") in ("member", "administrator"):
            await self._named(change["chat"])

    async def _named(self, chat: dict[str, Any]) -> None:
        if chat.get("type") not in GROUPS:
            return
        group, title = str(chat["id"]), str(chat.get("title") or "")
        if not title or self._names.get(group) == title:
            return
        self._names[group] = title
        ref = ChannelRef(PLATFORM, self.account, "group", group)
        await self._deliver(ChannelsSeen((ChannelInfo(ref, title),)))

    async def _message(self, message: dict[str, Any]) -> None:
        channel = channel_of(message, self.account)
        if channel is None:
            return
        await self._named(message["chat"])
        self._know(message)
        backfilled = float(message["date"]) < self._connected_at - LATE_SECONDS
        if message.get("new_chat_members"):
            if channel.kind == "group" and not backfilled:
                for event in joined(message, channel):
                    await self._deliver(event)
            return
        event = normalize(message, channel, self._me, backfilled=backfilled)
        if event is not None:
            await self._deliver(event)

    def _know(self, message: dict[str, Any]) -> None:
        user = message.get("from")
        if isinstance(user, dict) and user.get("username"):
            person = str(user["id"])
            self._people[person] = str(user["username"])
            self._people.move_to_end(person)
            while len(self._people) > KEPT_PEOPLE:
                self._people.popitem(last=False)
        if message.get("is_topic_message") and message.get("message_thread_id"):
            key = message_key(message["chat"]["id"], message["message_id"])
            self._topics[key] = int(message["message_thread_id"])
            while len(self._topics) > REMEMBERED:
                self._topics.popitem(last=False)
