"""Reply decisions for every bot: when to consider speaking, whether to, and what to say.

Per (bot, channel):
  message → trigger?   called by name / @ / reply to the bot → always; otherwise by activity level
          → collect    wait until the group is quiet for a moment (bounded), so bursts become one batch
          → gates      cooldown, hourly and global caps, bot-to-bot chains, one unprompted bot per group
          → model      persona + recent context + batch → one line or SKIP, then the reply filter
          → fresh?     same sender added more / target recalled while generating → regenerate once;
                       several unrelated messages since the target → quote it
          → outcome    observe mode records "would_reply" (never sends); live mode sends
A private chat is a channel too, enabled per person: every message there calls the bot, the prompt is the
private one, an answer may be longer and goes out as one message, without stickers or 黑话.
With 答题 on, what a bot is called with is sorted first: a call without any question word is chat; else a
short model call says chat, question, or a question about the chat log. Chat is answered as usual. A
question is answered properly (a Codex model searches the web first, at most so often an hour); for one
about the chat log nest looks through the stored messages by the model's keywords (or for links) and
hands over what it found. An answer goes out as one message that quotes the question, without reference
links. When the model's sorting is unreadable, the answer prompt lets it tell chat from a question itself.
Every evaluation and every suppressed trigger becomes a `decisions` row with a human-readable reason.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from nest.adapters.base import Adapter, parse_recipient
from nest.adapters.registry import DESK, DESK_PLACE, can, capabilities_of, place_of
from nest.core import birthday, welcome
from nest.core.broadcast import Broadcaster
from nest.core.desk import Desk, bubbles, from_app, sticker_segment, voice_segment
from nest.core.desk_files import with_files
from nest.core.desk_stream import DeskStream
from nest.core.events import CaughtUp, ChannelRef, MemberJoined, MessageEvent, Segment, SendMessage
from nest.core.games import INVITE_HINT, invite_name, split_invite
from nest.core.group_games import END_MARK, GroupGames
from nest.core.group_games import KINDS as GROUP_GAMES
from nest.core.groups import GROUP_MARK_HINT, create_group_tool, is_desk_group, split_group
from nest.core.issues import RECORDER_HINT
from nest.core.keepsakes import KeepsakeKeeper
from nest.core.memory import MemoryKeeper
from nest.core.outreach import games_hint
from nest.core.prompt import (
    AGAIN,
    MEMORY_CHARS,
    UNSURE,
    ProjectContext,
    Sorting,
    after_drafting,
    build_answer_prompt,
    build_memory_prompt,
    build_private_prompt,
    build_prompt,
    build_sort_prompt,
    clean_answer,
    clean_reply,
    fit_lines,
    parse_sorting,
    readable,
    speaker,
    split_sticker,
    split_voice,
    worth_sorting,
)
from nest.core.screenshots import ScreenshotReader, pictures
from nest.core.segments import render_text
from nest.core.slang import SlangLearner
from nest.core.speech import SpeechKeeper
from nest.core.sticker_packs import PackKeeper
from nest.core.stickers import StickerKeeper, relabel, sticker_keys
from nest.core.tones import DEFAULT_ANSWER, DEFAULT_CHAT, Tone, resolve
from nest.knowledge.service import KnowledgeService, LoadedBase
from nest.llm.base import ChatMessage, Completion, ProviderError, TextSink, Tool, ToolRunner
from nest.llm.purpose import begin, current, for_purpose
from nest.llm.runtime import ProviderRegistry, ProviderRuntime, ProviderUnavailable
from nest.llm.speech import SpeechError
from nest.storage.accounts import list_accounts
from nest.storage.bots import MAX_REPLY_LINES, BotChannel, BotConfig, BotRepo
from nest.storage.messages import MessageStore
from nest.storage.slang import group_key_of
from nest.storage.tones import ToneRepo

log = logging.getLogger(__name__)

SPOKEN_WAIT = 60.0  # seconds a voice message she sends on a platform waits for the platform to tell of it
# 桌面聊天: no sticker when one of her last this many replies there carried one (at most one in four).
DESK_STICKER_EVERY = 3


SONG_LINES = 3  # SING_HINT asks for three lines of lyrics
QUOTE_CHARS = 30  # of the message a reply replies to, in the prompt
MEMORY_MOST = 200  # older messages folded into a private chat's note at one time
SING_ASK = re.compile(r"唱|来一?首")


@dataclass(frozen=True)
class Policy:
    missed_hours: float = 6.0  # back online: @s older than this are left alone…
    missed_limit: int = 5  # …and at most this many, the newest, are answered (in one reply)
    interrupted_minutes: float = 30.0  # an @ a restart cut off is still answered this long after it came
    quiet_seconds: float = 2.0  # collect until the group has been quiet this long…
    max_collect_seconds: float = 8.0  # …but never longer than this after the trigger
    occasional_messages: int = 5  # "occasional": consider chiming in after this many new messages…
    occasional_seconds: float = (
        120.0  # …or when this long has passed since the last consideration (or at once
    )
    # for an open question to the group, see open_question)
    hourly_limit: int = 30  # per bot
    global_per_minute: int = 10  # all bots together
    bot_chain_limit: int = 4  # turns in a row by our bots before a human has to speak
    claim_seconds: float = 30.0  # one unprompted bot per group within this window
    mention_wait_limit: float = 60.0  # how long a mention may wait for the cooldown to end
    context_messages: int = 30
    memory_batch: int = 20  # a private chat's note grows once this many messages left the context
    memory_max_tokens: int = 700
    batch_limit: int = 20
    quote_after: int = 3
    max_reply_chars: int = 80  # per line
    max_reply_lines: int = MAX_REPLY_LINES  # a reply may come as a few messages in a row (一次最多发)
    typing_chars_per_second: float = 5.0  # the pause before each further line: as long as typing it…
    typing_max: float = 4.0  # …at most this long
    max_tokens: int = 200
    # A private chat (only with people the bot was enabled for): every message is to the bot, and an answer
    # may run longer. It goes out as one message.
    private_max_line_chars: int = 300
    private_max_lines: int = 12
    private_max_chars: int = 1000
    private_max_tokens: int = 1200
    # 答题 (per bot and group or private chat): a bot called with a real question answers it properly,
    # searching the web first when its model can. An answer goes out as one message.
    answer_max_chars: int = 700
    answer_max_tokens: int = 2000
    answer_hourly_limit: int = 20  # per bot, calls that searched; past it the bot answers from memory
    sort_max_tokens: int = 200  # sorting a call: one line of JSON
    sort_context: int = 10  # earlier messages shown when sorting
    history_matches: int = 30  # messages found in the chat log handed to the answer


class Arbiter:
    """At most one bot chimes in unprompted per group within a time window."""

    def __init__(self, window: float) -> None:
        self.window = window
        self._claims: dict[str, tuple[str, float]] = {}

    def claim(self, group: str, bot_id: str, now: float) -> str | None:
        """None if `bot_id` may go ahead, else the id of the bot holding this round."""
        holder = self._claims.get(group)
        if holder and holder[0] != bot_id and now - holder[1] < self.window:
            return holder[0]
        self._claims[group] = (bot_id, now)
        return None

    def release(self, group: str, bot_id: str) -> None:
        """The holder said nothing after all: the next chance to chime in is open to every bot again."""
        if (holder := self._claims.get(group)) and holder[0] == bot_id:
            del self._claims[group]


@dataclass
class ChannelState:
    pending: list[dict[str, Any]] = field(default_factory=list)
    mention: bool = False
    first_trigger_at: float = 0.0
    last_message_at: float = 0.0
    new_since_eval: int = 0
    last_eval_at: float = field(default_factory=time.time)
    last_spoke_at: float = 0.0
    evaluating: bool = False
    retrigger: bool = False
    task: asyncio.Task | None = None
    again: str = ""  # 重说: what she had answered, which the user took back


# 冷场时主动开口 (nest.core.openers): what she is asked, and how long her line may be.
OPENER_ASK = """群里有{gap}没人说话了。{what}
{game}最近的聊天：
{talk}

用你自己的语气在群里说一两句，自然一点，像群友随口说的，不要解释你为什么开口。觉得现在不合适就只输出 SKIP。"""
OPENER_MOODS = {
    "chat": "你想找大家闲聊，起个话头：说说你正在干什么、刚想到的事，或者问大家一个问题。",
    "grumble": "你有点无聊，想发几句小牢骚（嫌群里太安静、没人陪你之类），可爱一点，不要真的怪谁。",
    "cute": "你想卖个萌，让大家注意到你。",
    "game": "你有点无聊，想叫大家一起玩{game}，用你的语气邀请一下，说清楚怎么玩。",
}
OPENER_NAMES = {"chat": "找话题", "grumble": "发牢骚", "cute": "卖萌", "game": "叫大家玩小游戏"}
OPENER_CONTEXT = 12
OPENER_LINES = 3
OPENER_CHARS = 300
OPENER_TOKENS = 1200  # a thinking model thinks first, inside this limit


@dataclass
class Draft:
    outcome: str
    reason: str = ""
    reply: str | None = None
    quote: bool = False
    regenerated: bool = False
    llm_call_id: int | None = None
    target: dict[str, Any] | None = None
    reply_message_id: str | None = None
    maybe_sent: bool = False  # the send went out or may have: pace as if the bot spoke
    sticker: dict[str, Any] | None = None  # the 表情包 the reply carries (its stickers row)
    sticker_state: str | None = None  # "sent" | "trial" | "failed" | "unknown"; None: no sticker went out
    voice: bool = False  # 发语音: the model wants this reply spoken
    voice_asked: bool = False  # …or the person who called the bot asked for it
    sing: bool = False  # …to sing: the reply is lyrics
    voice_state: str | None = None  # "sent" | "trial" | "failed" | "unknown"; None: not spoken
    voice_note: str = ""  # why it went out as text after all, for the decision's reason
    slang_terms: list[str] = field(default_factory=list)  # 黑话 explained in the prompt
    answer: bool = False  # 答题: a real answer, sent as one message
    searches: int = 0  # web searches and page visits made for it
    sorted_as: str = ""  # 答题: what the call was taken for, for the decision's reason
    gap: dict[str, Any] | None = None  # 项目资料: a project question the docs did not answer (base_id, …)
    project: ProjectContext | None = None  # 项目资料: what the docs gave the answer (for 看源码)
    streamed: bool = False  # 逐字流式: seen being written, so its messages come without typing pauses
    invite: dict[str, str] | None = None  # 她邀请你玩: the game of the card that follows (nest.core.games)


class Brain:
    def __init__(
        self,
        repo: BotRepo,
        store: MessageStore,
        providers: ProviderRegistry,
        broadcaster: Broadcaster,
        *,
        policy: Policy | None = None,
        our_accounts: set[str] | None = None,
    ) -> None:
        self.repo = repo
        self.store = store
        self.providers = providers
        self.broadcaster = broadcaster
        self.policy = policy or Policy()
        self.arbiter = Arbiter(self.policy.claim_seconds)
        self.bots: dict[str, BotConfig] = {}
        self._by_account: dict[str, BotConfig] = {}
        self._our_accounts = set(our_accounts or ())
        self._platform_of: dict[str, str] = {}  # self id -> platform, of every account in the database
        self._states: dict[tuple[str, int], ChannelState] = {}
        # Account id -> adapter, set by the app once the hub exists. Needed to send (live mode) and to
        # tell nest's own messages from ones another program sent with the same account.
        self.adapters: Mapping[str, Adapter] = {}
        self.foreign_at: dict[str, float] = {}  # bot id -> last time another program spoke as this bot
        # Her voice messages on a platform on their way out: (when, audio file, words) per chat, for when the
        # platform tells of them (heard_own_voice).
        self._speaking: dict[ChannelRef, list[tuple[float, str | None, str]]] = {}
        # What small talk and day plans are written with (nest.core.initiative): purpose -> model id.
        self.small: dict[str, str | None] = {}
        # Optional modules, set by the app: 表情包 and 黑话. Off per bot and group by default.
        self.stickers: StickerKeeper | None = None
        self.packs: PackKeeper | None = None  # 表情包套装: what she sends in the desk chat
        self.group_games: GroupGames | None = None  # 群里的小游戏 (nest.core.group_games)
        self.desk_media: Any = None  # 桌面聊天的图片和语音 (nest.core.desk_media.DeskMedia)
        self.groups: Any = None  # 桌面群 (nest.core.groups.DeskGroups): 她拉群
        self.life: Any = (
            None  # 小窝 (nest.core.life.LifeKeeper): what she is doing, in the chats with the user
        )
        self.speech: SpeechKeeper | None = None
        self.chance = random.random  # 语音频率's dice
        self.slang: SlangLearner | None = None
        self.memory: MemoryKeeper | None = None  # 长期记忆
        self.desk: Desk | None = None  # 桌面聊天: where answers to messages typed in the app go
        self.keepsakes: KeepsakeKeeper | None = None  # 回忆档案 (伙伴模式)
        self.reminders: Any = None  # 提醒 (伙伴模式, nest.core.reminders.ReminderKeeper)
        self.tools: Any = None  # 做事 (伙伴模式, nest.core.tools.CompanionTools)
        self.games: Any = None  # 小游戏 (伙伴模式, nest.core.games.GameKeeper): a game on, in her answers
        self.tasks: Any = None  # 任务 (伙伴模式, nest.core.tasks.TaskKeeper)
        self.routines: Any = None  # 定时任务 (伙伴模式, nest.core.routines.RoutineKeeper)
        self.knowledge: KnowledgeService | None = None
        self.screenshots: ScreenshotReader | None = None
        self._foreign_logged: dict[str, float] = {}
        self._searched: dict[str, list[float]] = {}  # bot id -> when its 答题 calls searched (last hour)
        self._reading: set[asyncio.Task] = set()  # 看源码 running after an answer the docs could not give
        self._remembering: set[int] = set()  # private chats whose note is being written
        self._welcomed: dict[tuple[str, str], float] = {}  # (group key, person) -> when they were welcomed
        self._welcoming: set[asyncio.Task] = set()
        self.tones: dict[str, Tone] = {}  # 语气 by key (nest.core.tones), re-read by load()

    async def load(self) -> None:
        self.tones = await ToneRepo(self.repo.db).tones()
        self.bots = {bot.id: bot for bot in await self.repo.bots()}
        self._by_account = {bot.self_id: bot for bot in self.bots.values()}
        self._our_accounts |= set(self._by_account)
        self._platform_of = {a.self_id: a.platform for a in await list_accounts(self.repo.db)}
        for (bot_id, channel_id), state in list(self._states.items()):
            bot = self.bots.get(bot_id)
            channel = bot.channels.get(channel_id) if bot else None
            if bot is None or bot.mode == "paused" or channel is None or not channel.enabled:
                self._cancel(state)

    async def stop(self) -> None:
        for state in self._states.values():
            self._cancel(state)
        for task in (*self._reading, *self._welcoming):
            task.cancel()
        tasks = [s.task for s in self._states.values() if s.task is not None]
        await asyncio.gather(*tasks, *self._reading, *self._welcoming, return_exceptions=True)

    @property
    def our_accounts(self) -> set[str]:
        """Every account id of ours (mutated in place by load(), so holders of this set stay current)."""
        return self._our_accounts

    def tone(self, key: str, default: str) -> Tone:
        """The tone a channel names, or `default` when it is gone (nest.core.tones.resolve)."""
        return resolve(self.tones, key, default)

    def default_provider(self) -> str | None:
        """The model of the first bot that has one: what background jobs use unless told otherwise."""
        return next((bot.provider_id for bot in self.bots.values() if bot.provider_id), None)

    @staticmethod
    def _persona(bot: BotConfig, channel: BotChannel) -> str:
        """The persona, plus the 问题记录官's part where this bot writes down the group's issues."""
        if not channel.recorder:
            return channel.persona
        return (channel.persona.strip() or f"你是群里的{bot.name}。") + "\n" + RECORDER_HINT

    def peers_of(self, bot: BotConfig, anywhere: bool = False) -> dict[str, str]:
        """Our other accounts on this bot's platform: one in a QQ group cannot meet a Discord bot there.
        `anywhere`: in a 桌面群, where every companion can be, whatever her platform."""
        names = {b.self_id: b.name for b in self.bots.values()}
        platform = bot.account_id.partition(":")[0]
        return {
            uin: names.get(uin, uin)
            for uin in self._our_accounts
            if uin != bot.self_id and (anywhere or self._platform_of.get(uin, platform) == platform)
        }

    # -- intake ------------------------------------------------------------------------------------

    async def on_message(self, event: MessageEvent, row: dict[str, Any]) -> None:
        if event.backfilled:
            return  # history fetched after a reconnect is context, never a reason to speak now
        bot = self._by_account.get(event.channel.account)
        channel = bot.channels.get(row["channel_id"]) if bot else None
        if bot is None or channel is None or not channel.enabled or bot.mode == "paused":
            return
        state = self._states.setdefault((bot.id, channel.channel_id), ChannelState())
        now = time.time()
        if event.from_self:
            state.last_spoke_at = max(state.last_spoke_at, now)  # also counts messages sent by other programs
            if from_app(event.message_id):
                return  # 桌面聊天: our own answer, shown in the app
            adapter = self.adapters.get(bot.account_id)
            if adapter is None or not adapter.sent_by_us(event.message_id):
                self._foreign(bot, now)
            return
        if event.from_bot and event.sender_id not in self._our_accounts:
            return  # stored, so it is in the context, but another program's bot never sets ours off
        state.pending.append(row)
        del state.pending[: -self.policy.batch_limit * 2]
        state.last_message_at = now
        state.new_since_eval += 1
        mention = await self._mentions(bot, channel, event)
        if event.sender_id in self._our_accounts and not mention and not is_desk_group(channel.channel):
            return  # other bots' chatter is context, not a trigger (in a 桌面群 they talk with each other)
        if not (mention or self._wants_to_chime_in(channel, state, now, event)):
            return
        state.mention = state.mention or mention
        self._wake(bot, channel, state, now)

    def on_recall(self, row: dict[str, Any]) -> None:
        """Taken back before it was answered: nothing to answer. (One taken back while the answer is being
        written is noticed in _generate.)"""
        for (_, channel_id), state in self._states.items():
            if channel_id == row["channel_id"]:
                state.pending = [p for p in state.pending if p["id"] != row["id"]]

    def busy(self, bot_id: str, channel_id: int) -> bool:
        """Whether an answer in this chat is being thought of or on its way out."""
        state = self._states.get((bot_id, channel_id))
        return state is not None and state.task is not None and not state.task.done()

    def again(self, bot: BotConfig, channel: BotChannel, rows: list[dict[str, Any]], said: str) -> None:
        """重说: answer these messages of a private chat once more, not with what was `said`."""
        state = self._states.setdefault((bot.id, channel.channel_id), ChannelState())
        now = time.time()
        state.pending = [*rows, *state.pending]
        state.last_message_at = now
        state.mention, state.again = True, said
        self._wake(bot, channel, state, now)

    def _wake(self, bot: BotConfig, channel: BotChannel, state: ChannelState, now: float) -> None:
        if state.task is not None and not state.task.done():
            if state.evaluating:
                state.retrigger = True
            return
        state.first_trigger_at = now
        state.task = asyncio.create_task(
            self._run(bot.id, channel.channel_id), name=f"brain:{bot.id}:{channel.channel_id}"
        )

    async def catch_up(self, event: CaughtUp) -> None:
        """Back online: answer, once per group, the people who @ed this bot while it was away. The bot's own
        history may not reach back far enough, so what the group's other accounts received counts too. So does
        an @ it did receive, when a restart stopped it before it decided anything."""
        bot = self._by_account.get(event.account)
        if bot is None or bot.mode == "paused":
            return
        for ref, since in event.gaps:
            channel = next((c for c in bot.channels.values() if c.channel == ref), None)
            if channel is None or not channel.enabled:
                continue
            after = max(since, event.until - self.policy.missed_hours * 3600)
            start = min(after, event.until - self.policy.interrupted_minutes * 60)
            rows = await self.store.mentions_in_group(ref.platform, ref.id, bot.self_id, start, event.until)
            state = self._states.setdefault((bot.id, channel.channel_id), ChannelState())
            working = state.task is not None and not state.task.done()  # only the connection dropped
            missed = [
                row
                for row in _missed(rows, channel.channel_id, self._our_accounts, after)
                if row["source"] == "backfill"
                or not row["external_id"]
                or not (
                    working or await self.repo.decided_since(bot.id, channel.channel_id, row["received_at"])
                )
            ][-self.policy.missed_limit :]
            if not missed:
                continue
            log.info("%s 补看群 %s 里错过的 %d 条 @", bot.name, ref.id, len(missed))
            now = time.time()
            state.pending.extend(missed)
            state.mention, state.last_message_at = True, now
            self._wake(bot, channel, state, now)

    async def on_member_joined(self, event: MemberJoined, row: dict[str, Any]) -> None:
        """欢迎新人: someone joined a group. Each of our accounts there reports it; the first report starts
        one welcome, later ones and repeats within welcome.ONCE are ignored."""
        if event.user_id in self._our_accounts:
            return
        now = time.time()
        if now - event.timestamp > welcome.STALE:
            return
        key = group_key_of(event.channel)
        self._welcomed = {k: at for k, at in self._welcomed.items() if now - at < welcome.ONCE}
        if (key, event.user_id) in self._welcomed:
            return
        recent = sum(1 for (group, _), _at in self._welcomed.items() if group == key)
        self._welcomed[(key, event.user_id)] = now  # before any await: the next account's report sees it
        if not self._welcomers(key):
            return
        if recent >= welcome.MAX_PER_WINDOW:
            log.info("%s 短时间内进了好几个人，这个 %s 就不单独欢迎了", key, event.name or event.user_id)
            return
        task = asyncio.create_task(self._welcome(key, event, row), name=f"welcome:{key}")
        self._welcoming.add(task)
        task.add_done_callback(self._welcoming.discard)

    def _welcomers(self, key: str) -> list[tuple[BotConfig, BotChannel]]:
        """The live bots switched on in this group, with 欢迎新人 on there, whose accounts are running."""
        return [
            (bot, channel)
            for bot in self.bots.values()
            if bot.mode == "live" and self.adapters.get(bot.account_id) is not None
            for channel in bot.channels.values()
            if channel.enabled
            and channel.welcome
            and channel.channel.kind == "group"
            and group_key_of(channel.channel) == key
        ]

    @for_purpose("group")
    async def _welcome(self, key: str, event: MemberJoined, row: dict[str, Any]) -> None:
        pool = self._welcomers(key)
        if not pool:
            return
        bot, channel = pool[min(int(self.chance() * len(pool)), len(pool) - 1)]
        await asyncio.sleep(welcome.wait(channel.welcome_delay, self.chance()))
        # Settings may have changed while it waited: the fresh ones, or none when it was switched off.
        fresh = [
            (b, c) for b, c in self._welcomers(key) if (b.id, c.channel_id) == (bot.id, channel.channel_id)
        ]
        if not fresh:
            log.info("%s 等着欢迎的时候被关掉了，没有欢迎", bot.name)
            return
        [(bot, channel)] = fresh
        draft = Draft(
            outcome="would_reply",
            reason="有新人进群，欢迎一下",
            voice=channel.welcome_voice and self._can_speak(bot, channel),
        )
        name = welcome.spoken(event.name)
        try:
            before = await self.repo.welcomes_said(bot.id, welcome.RECENT)
            messages = welcome.prompt(self._persona(bot, channel), bot.name, name, before)
            completion, call_id, _ = await self._complete(bot, messages, max_tokens=welcome.MAX_TOKENS)
            # Cut off by the output limit, the line stops mid-sentence ("……来了就"): the plain one is better.
            said = "" if completion.cut else welcome.line(after_drafting(completion.text)[0], name)
            draft = replace(draft, llm_call_id=call_id)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 写欢迎词失败（%s），用默认的一句", bot.name, exc)
            said = ""
        plain = not said
        draft = replace(draft, reply=said or welcome.fallback(name), voice_asked=True)
        try:
            sent = await self._send(bot, channel, draft)
            if plain:
                sent = replace(sent, reason=f"{sent.reason}；模型没写出来，用了默认的一句")
            if channel.welcome_note and sent.outcome == "replied":
                after = await self._follow_up(bot, channel, sent, [channel.welcome_note])
                note = "接着发了设好的那句话" if after.reason == "已发送 2 条" else after.reason
                sent = replace(sent, reason=f"{sent.reason}；{note}")
            await self._record(bot, channel.channel_id, sent, "welcome", row)
        except Exception:
            log.exception("%s 欢迎新人出错", bot.name)

    @for_purpose("group")
    async def open_group(
        self,
        bot: BotConfig,
        channel: BotChannel,
        mood: str,
        rows: list[dict[str, Any]],
        *,
        quiet: float,
        game: str = "",
    ) -> str | None:
        """冷场时主动开口 (nest.core.openers): one line of hers in a quiet group — `mood` one of OPENER_MOODS
        ("game": inviting the group to `game`, a nest.core.group_games kind, set up here). `rows`: the chat
        there, the last message last; `quiet`: seconds since it. The line when it went out (or may have), None
        when she said nothing."""
        games = self.group_games
        invited = await games.invite(bot, channel, game) if mood == "game" and games and game else None
        if mood == "game" and invited is None:
            mood = "chat"
        gap = f"{quiet / 3600:.0f} 个小时" if quiet >= 3600 else f"{quiet / 60:.0f} 分钟"
        talk = "\n".join(
            f"{speaker(r, bot.name) if r.get('from_self') else r['sender_name']}："
            + " ".join(str(r.get("text") or "").split())[:100]
            for r in rows[-OPENER_CONTEXT:]
        )
        ask = OPENER_ASK.format(
            gap=gap,
            what=OPENER_MOODS[mood].format(game=GROUP_GAMES.get(game, "")),
            game=(games.told(invited) + "\n") if games and invited else "",
            talk=talk,
        )
        messages = [ChatMessage("system", self._persona(bot, channel)), ChatMessage("user", ask)]
        try:
            completion, call_id, _ = await self._complete(bot, messages, max_tokens=OPENER_TOKENS)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 想在群里开口，模型没写出来（%s）", bot.name, exc)
            completion, call_id = None, None
        text = "" if completion is None or completion.cut else after_drafting(completion.text)[0]
        lines = [line.strip() for line in text.replace(END_MARK, "").splitlines() if line.strip()]
        line = "\n".join(lines[:OPENER_LINES])[:OPENER_CHARS].strip().strip('「」"')
        if not line or line.upper().startswith("SKIP"):
            if invited is not None and games is not None:
                await games.drop(channel.channel_id)
            return None
        draft = Draft(
            outcome="would_reply",
            reason=f"群里 {gap}没人说话，主动{OPENER_NAMES[mood]}",
            reply=line,
            llm_call_id=call_id,
        )
        sent = await self._send(bot, channel, draft)
        sent = replace(sent, reason=f"{draft.reason}：{sent.reason}")
        try:
            await self._record(bot, channel.channel_id, sent, "opener", rows[-1])
        except Exception:
            log.exception("%s 记录冷场开口出错", bot.name)
        if sent.outcome == "replied" or sent.maybe_sent:
            log.info("%s 在群里冷场时开口：%s", bot.name, OPENER_NAMES[mood])
            return line
        if invited is not None and games is not None:
            await games.drop(channel.channel_id)
        return None

    def birthday_wishers(self) -> dict[str, list[tuple[BotConfig, BotChannel]]]:
        """生日祝福 (nest.core.birthday): by group key, the bots with it on there, live or observing, whose
        accounts are running."""
        found: dict[str, list[tuple[BotConfig, BotChannel]]] = {}
        for bot in self.bots.values():
            if bot.mode == "paused" or self.adapters.get(bot.account_id) is None:
                continue
            for channel in bot.channels.values():
                if channel.enabled and channel.birthday and channel.channel.kind == "group":
                    found.setdefault(group_key_of(channel.channel), []).append((bot, channel))
        return found

    @for_purpose("group")
    async def wish_birthday(
        self, bot: BotConfig, channel: BotChannel, user_id: str, name: str, said: Sequence[str] = ()
    ) -> tuple[str, str]:
        """生日祝福: one wish in the bot's own words, @ing the person; an observing bot only notes it.
        `name` as welcome.spoken(); `said`: the bot's last wishes. (What became of it — "replied" when it
        went out or may have, "would_reply", "failed" — and the words.)"""
        capable = capabilities_of(channel.channel.platform)
        mention = "mention" in capable
        draft = Draft(
            outcome="would_reply",
            reason="今天是群友的生日，祝ta生日快乐",
            target={"id": None, "external_id": "", "sender_id": user_id} if mention else None,
        )
        try:
            messages = birthday.prompt(self._persona(bot, channel), bot.name, name, said)
            completion, call_id, _ = await self._complete(bot, messages, max_tokens=birthday.MAX_TOKENS)
            words = "" if completion.cut else birthday.line(after_drafting(completion.text)[0])
            draft = replace(draft, llm_call_id=call_id)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 写生日祝福失败（%s），用默认的一句", bot.name, exc)
            words = ""
        plain = not words
        words = words or birthday.fallback()
        # After the @ comes a space, as when people @ someone in QQ.
        draft = replace(draft, reply=f" {words}" if mention else words)
        if bot.mode == "live":
            sent = await self._send(bot, channel, draft)
        else:
            sent = replace(draft, reason="只看不说：本来会祝ta生日快乐")
        if plain:
            sent = replace(sent, reason=f"{sent.reason}；模型没写出来，用了默认的一句")
        shown = f"@{name or user_id} {words}" if mention else words
        trigger_row = {"id": None, "sender_name": name or user_id, "text": None, "sent_at": None}
        try:
            await self._record(bot, channel.channel_id, replace(sent, reply=shown), "birthday", trigger_row)
        except Exception:
            log.exception("%s 记录生日祝福出错", bot.name)
        if sent.outcome == "replied" or sent.maybe_sent:
            return "replied", words
        return ("would_reply" if sent.outcome == "would_reply" else "failed"), words

    async def _mentions(self, bot: BotConfig, channel: BotChannel, event: MessageEvent) -> bool:
        if _private(channel) or bot.self_id in event.mentions:
            return True
        if _named(bot, typed_text(event.segments)):
            return True
        if self._in_her_game(bot, channel, event.from_self, event.sender_id):
            return True
        if event.reply_to:
            replied = await self.store.by_external_id(channel.channel_id, event.reply_to)
            return bool(replied and replied["from_self"])
        return False

    def _in_her_game(self, bot: BotConfig, channel: BotChannel, from_self: bool, sender: str) -> bool:
        """群里的小游戏: while hers is on in this group, what a person there says is for her."""
        return (
            self.group_games is not None
            and not from_self
            and sender not in self._our_accounts
            and self.group_games.active(bot.id, channel.channel_id) is not None
        )

    def _wants_to_chime_in(
        self, channel: BotChannel, state: ChannelState, now: float, event: MessageEvent
    ) -> bool:
        if channel.activity == "active":
            return True
        if channel.activity == "occasional":
            return (
                state.new_since_eval >= self.policy.occasional_messages
                or now - state.last_eval_at >= self.policy.occasional_seconds
                or (open_question(event) and not self._names_a_bot(event))
            )
        return False  # "mentions"

    def _names_a_bot(self, event: MessageEvent) -> bool:
        """Whether the message calls one of our bots by name: then it is addressed, not put to the group."""
        typed = typed_text(event.segments)
        return any(_named(bot, typed) for bot in self.bots.values())

    # -- per-channel worker ------------------------------------------------------------------------

    def _cancel(self, state: ChannelState) -> None:
        if state.task is not None and not state.task.done():
            state.task.cancel()
        state.pending.clear()
        state.mention = state.retrigger = False

    async def _run(self, bot_id: str, channel_id: int) -> None:
        state = self._states[(bot_id, channel_id)]
        bot = self.bots.get(bot_id)
        channel = bot.channels.get(channel_id) if bot else None
        if channel is not None:
            desk = is_desk_group(channel.channel)
            begin("chat" if _private(channel) else "desk_group" if desk else "group")
        try:
            while True:
                await self._collect(state)
                state.evaluating = True
                try:
                    await self._evaluate(bot_id, channel_id, state)
                except Exception:
                    log.exception("bot %s 在频道 %s 的判断出错", bot_id, channel_id)
                finally:
                    state.evaluating = False
                if not state.retrigger:
                    return
                state.retrigger = False
                state.first_trigger_at = time.time()
        except asyncio.CancelledError:
            pass

    async def _collect(self, state: ChannelState) -> None:
        while True:
            now = time.time()
            wait = min(
                state.last_message_at + self.policy.quiet_seconds - now,
                state.first_trigger_at + self.policy.max_collect_seconds - now,
            )
            if wait <= 0:
                return
            await asyncio.sleep(wait)

    async def _evaluate(self, bot_id: str, channel_id: int, state: ChannelState) -> None:
        bot = self.bots.get(bot_id)
        channel = bot.channels.get(channel_id) if bot else None
        if bot is None or channel is None or not channel.enabled or bot.mode == "paused":
            state.pending.clear()
            return
        mention = state.mention
        # A mention waits out the cooldown (briefly) instead of being dropped.
        cooldown_left = state.last_spoke_at + channel.cooldown_seconds - time.time()
        if mention and 0 < cooldown_left <= self.policy.mention_wait_limit:
            await asyncio.sleep(cooldown_left + 0.05)  # margin: timers may fire a few ms early (Windows)
            # The bot may have been edited while waiting (another model, name, persona): use it as it is now.
            bot = self.bots.get(bot_id)
            channel = bot.channels.get(channel_id) if bot else None
            if bot is None or channel is None or not channel.enabled or bot.mode == "paused":
                state.pending.clear()
                return
        batch = state.pending[-self.policy.batch_limit :]
        state.pending, state.mention, state.new_since_eval = [], False, 0
        again, state.again = state.again, ""
        state.last_eval_at = time.time()
        if not batch:
            return
        called = {row["id"] for row in batch if await self._row_mentions(bot, channel, row)}
        target = next((row for row in reversed(batch) if row["id"] in called), batch[-1])
        trigger = "mention" if mention else "chime_in"

        draft = await self._gate(bot, channel, state, target, mention)
        if draft is None:
            draft = await self._generate(bot, channel, batch, called, target, again=again)
        if draft.outcome == "would_reply" and bot.mode == "live":
            draft = await self._send(bot, channel, draft)
        elif draft.outcome == "would_reply" and draft.sticker is not None:
            draft = replace(draft, sticker_state="trial", reason=draft.reason + "，还会发一张表情")
        if draft.outcome == "would_reply" and draft.voice:
            draft = replace(draft, voice_state="trial", reason=draft.reason + "，会用语音说")
        if not mention and draft.outcome in ("skipped", "failed") and not draft.maybe_sent:
            self.arbiter.release(self._round(bot, channel, draft.target), bot.id)
        searched = f"联网查了 {draft.searches} 次" if draft.searches else ""
        if notes := [note for note in (draft.sorted_as, searched) if note]:
            draft = replace(draft, reason=f"{draft.reason}（{'；'.join(notes)}）")
        if draft.outcome in ("would_reply", "replied") or draft.maybe_sent:
            state.last_spoke_at = time.time()  # observe mode simulates speaking, so pacing stays realistic
        if draft.outcome == "replied" and _private(channel) and channel.channel_id not in self._remembering:
            self._remembering.add(channel.channel_id)
            task = asyncio.create_task(
                self._remember(bot, channel), name=f"brain:remember:{channel.channel_id}"
            )
            self._reading.add(task)
            task.add_done_callback(self._reading.discard)
        trigger_row = draft.target or target
        # The gap is stored before the decision, so whoever sees the decision finds its gap too.
        reading, forward, gap_id, base = False, False, 0, None
        if draft.gap is not None and self.knowledge is not None:
            base = self.knowledge.bases.get(draft.gap["base_id"])
            # 看源码: an answer went out (or would have), the docs could not give it, the base has source.
            reading = (
                base is not None
                and draft.project is not None
                and draft.outcome in ("replied", "would_reply")
                and self.knowledge.can_read_source(base)
            )
            # In a private chat the one asking is whoever keeps the docs: nothing to forward to them.
            forward = bot.mode == "live" and channel.channel.kind != "private"
            gap_id = await self.knowledge.report_gap(
                **draft.gap,
                account_id=bot.account_id,
                message_id=trigger_row["id"],
                asker=trigger_row["sender_name"],
                answer=draft.reply or "",
                forward=forward,
                hold=reading,
            )
        await self._record(bot, channel_id, draft, trigger, trigger_row)
        if reading:
            assert base is not None and draft.project is not None
            task = asyncio.create_task(
                self._read_source(bot, channel, base, gap_id, trigger_row, draft, forward=forward),
                name=f"read-source:{bot.id}",
            )
            self._reading.add(task)
            task.add_done_callback(self._reading.discard)

    async def _record(
        self, bot: BotConfig, channel_id: int, draft: Draft, trigger: str, trigger_row: dict[str, Any]
    ) -> None:
        decision = await self.repo.record_decision(
            bot_id=bot.id,
            channel_id=channel_id,
            trigger_id=trigger_row["id"],
            trigger=trigger,
            outcome=draft.outcome,
            reason=draft.reason,
            reply=draft.reply,
            quote=draft.quote,
            regenerated=draft.regenerated,
            llm_call_id=draft.llm_call_id,
            reply_message_id=draft.reply_message_id,
            sticker_id=draft.sticker["id"] if draft.sticker and draft.sticker_state else None,
            sticker_state=draft.sticker_state,
            slang_terms=draft.slang_terms,
            voice_state=draft.voice_state,
        )
        # Same shape as BotRepo.decisions() rows, so the console treats live and loaded decisions alike.
        self.broadcaster.publish(
            "decision",
            decision
            | {
                "trigger_sender": trigger_row["sender_name"],
                "trigger_text": trigger_row["text"],
                "trigger_sent_at": trigger_row["sent_at"],
                "bot_name": bot.name,
            },
        )

    async def _read_source(
        self,
        bot: BotConfig,
        channel: BotChannel,
        base: LoadedBase,
        gap_id: int,
        question_row: dict[str, Any],
        draft: Draft,
        *,
        forward: bool,
    ) -> None:
        """看源码 after an answer the docs could not give: the bot reads the project's source with its own
        model, a step at a time (nest.knowledge.investigate). A sure answer is posted as a reply to the
        question, marked as read from the source; every finding goes to whoever keeps the docs."""
        assert self.knowledge is not None and draft.gap is not None and draft.project is not None
        if self._model(bot) is None:
            await self.knowledge.finish_gap(gap_id, base.base.id, None, forward=forward)
            return

        async def ask(messages: list[ChatMessage], max_tokens: int) -> str:
            completion, _, _ = await self._complete(bot, messages, max_tokens=max_tokens)
            return completion.text

        finding = await self.knowledge.read_source(
            base,
            ask,
            name=bot.name,
            question=draft.gap["question"],
            replied=draft.reply or "",
            tone=self.tone(channel.answer_tone, DEFAULT_ANSWER),
            seen=[text.split("\n", 1)[0].lstrip("# ").strip() for text in draft.project.sections],
        )
        await self.knowledge.finish_gap(gap_id, base.base.id, finding, forward=forward)
        if finding.problem or not finding.sure:
            why = finding.problem or "没在代码里找到依据，没在群里说"
            await self._record(
                bot, channel.channel_id, Draft("skipped", f"{finding.summary}：{why}"), "source", question_row
            )
            return
        current = self.bots.get(bot.id)
        now = current.channels.get(channel.channel_id) if current else None
        if current is None or now is None or not now.enabled or current.mode == "paused":
            return
        reply = f"翻了下源码：{finding.answer}\n（看源码推断的，作者还没确认）"
        follow = Draft(
            "would_reply",
            f"{finding.summary}；只看不说：本来会补这条" if current.mode != "live" else finding.summary,
            reply=reply,
            quote=not _private(now),
            target=question_row,
            answer=True,
        )
        if current.mode == "live":
            sent = await self._send(current, now, follow)
            said = "，已发送" if sent.outcome == "replied" else f"；{sent.reason}"
            follow = replace(sent, reason=finding.summary + said)
        await self._record(current, channel.channel_id, follow, "source", question_row)

    async def _row_mentions(self, bot: BotConfig, channel: BotChannel, row: dict[str, Any]) -> bool:
        if (
            _private(channel)
            or bot.self_id in row["mentions"]
            or _named(bot, typed_text(row.get("segments") or [], row["text"]))
        ):
            return True
        if self._in_her_game(bot, channel, bool(row.get("from_self")), str(row.get("sender_id"))):
            return True
        if row.get("reply_to"):
            replied = await self.store.by_external_id(channel.channel_id, row["reply_to"])
            return bool(replied and replied["from_self"])
        return False

    async def _gate(
        self, bot: BotConfig, channel: BotChannel, state: ChannelState, target: dict[str, Any], mention: bool
    ) -> Draft | None:
        """A suppressing Draft if the bot must stay quiet regardless of what it would say."""
        now = time.time()
        if target["sender_id"] in self._our_accounts:
            chain = await self._bot_chain(channel.channel_id)
            if chain >= self.policy.bot_chain_limit:
                return Draft("suppressed", f"bot 之间已经连续说了 {chain} 句，等真人说话")
        if now - state.last_spoke_at < channel.cooldown_seconds:
            return Draft("suppressed", f"刚说过话，还在冷却（{channel.cooldown_seconds:.0f} 秒）")
        spoken = await self.repo.spoken_since(now - 3600, bot.id)
        if spoken >= self.policy.hourly_limit:
            return Draft("suppressed", f"这个小时已经说了 {spoken} 句，到上限了")
        if await self.repo.spoken_since(now - 60) >= self.policy.global_per_minute:
            return Draft("suppressed", "所有 bot 这一分钟说得太多了，先停一停")
        # A bot that cannot speak must not take the round from one that can: model checks come first.
        if bot.provider_id is None:
            return Draft("failed", "还没有给这个 bot 配置模型")
        runtime = self._model(bot)
        if runtime is None:
            why = self.providers.errors.get(bot.provider_id) or "没有加载"
            return Draft("failed", f"模型 {bot.provider_id} 设置有误：{why}")
        if runtime.health.open_until > now:
            wait = runtime.health.open_until - now
            return Draft("failed", f"模型 {bot.provider_id} 连续失败，暂停中，{wait:.0f} 秒后再试")
        if not mention:
            holder = self.arbiter.claim(self._round(bot, channel, target), bot.id, now)
            if holder is not None:
                name = self.bots[holder].name if holder in self.bots else holder
                return Draft("suppressed", f"这轮由 {name} 插话")
        return None

    def _model(self, bot: BotConfig) -> ProviderRuntime | None:
        """The model to call: the bot's own, or its backup while its own cannot take calls. Looked up each
        time: a model may have been edited, or the bot switched to another one, while it was waiting."""
        live = self.bots.get(bot.id) or bot
        own = self.providers.get(live.provider_id)
        small = self.providers.get(self.small.get(current()))
        if small is not None and small.health.open_until <= time.time():
            return small  # 闲聊、排日程 on a cheaper model, when one was chosen for them
        backup = self.providers.get(live.backup_provider_id)
        if backup is None or (own is not None and own.health.open_until <= time.time()):
            return own
        return backup if backup.health.open_until <= time.time() else own

    async def _complete(
        self,
        bot: BotConfig,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        search: bool = False,
        tools: Sequence[Tool] = (),
        run: ToolRunner | None = None,
        on_text: TextSink | None = None,
    ) -> tuple[Completion, int, ProviderRuntime]:
        """One model call, on the backup when the bot's own model fails. Raises what the last one raised;
        ProviderUnavailable when neither is there."""
        runtime = self._model(bot)
        if runtime is None:
            raise ProviderUnavailable("这个 bot 的模型刚被移除")
        try:
            completion, call_id = await runtime.complete(
                messages,
                bot_id=bot.id,
                max_tokens=max_tokens,
                search=search and runtime.can_search,
                tools=tools,
                run=run,
                on_text=on_text,
            )
        except (ProviderUnavailable, ProviderError) as exc:
            live = self.bots.get(bot.id) or bot
            backup = self.providers.get(live.backup_provider_id)
            if runtime is self.providers.get(self.small.get(current())):
                backup = self.providers.get(live.provider_id)  # the cheaper one failed: her own
            if backup is None or backup is runtime or backup.health.open_until > time.time():
                raise
            log.info("%s 的模型失败（%s），改用备用模型 %s", bot.name, exc, backup.config.id)
            runtime = backup
            completion, call_id = await runtime.complete(
                messages,
                bot_id=bot.id,
                max_tokens=max_tokens,
                search=search and runtime.can_search,
                tools=tools,
                run=run,
                on_text=on_text,
            )
        return completion, call_id, runtime

    @staticmethod
    def _round(bot: BotConfig, channel: BotChannel, target: dict[str, Any] | None = None) -> str:
        """Who competes for a chime-in round: bots of the same group that really speak, or that only
        observe. An observing bot says nothing in the group, so it must never silence a live one. In a 桌面群
        a round is one message (the members come from any platform, and each message, the user's or a
        companion's, is picked up by one of them; the bot chain limit ends it)."""
        side = "live" if bot.mode == "live" else "observe"
        if is_desk_group(channel.channel):
            said = (target or {}).get("external_id") or ""
            return f"desk:group:{channel.channel.id}:{said}:{side}"
        return f"{channel.channel.platform}:{channel.channel.kind}:{channel.channel.id}:{side}"

    async def _bot_chain(self, channel_id: int) -> int:
        """How many turns in a row our bots have had: a reply sent as several messages is one turn."""
        count, last = 0, None
        limit = (self.policy.bot_chain_limit + 2) * self.policy.max_reply_lines
        for row in reversed(await self.store.recent(channel_id, limit=limit)):
            if not (row["from_self"] or row["sender_id"] in self._our_accounts):
                break
            if row["sender_id"] != last:
                count, last = count + 1, row["sender_id"]
        return count

    async def _heard(
        self, bot: BotConfig, channel_id: int, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The bot's own voice messages come back as [语音]: its decisions have the words it said."""
        spoken = [row["external_id"] for row in rows if row["from_self"] and row["text"] == "[语音]"]
        words = await self.repo.voiced(bot.id, channel_id, spoken) if spoken else {}
        return [
            row | {"text": f"（语音）{words[row['external_id']]}"} if row["external_id"] in words else row
            for row in rows
        ]

    @for_purpose("memory")
    async def _remember(self, bot: BotConfig, channel: BotChannel) -> None:
        """A private chat outlives the context window: what falls out of it is folded into a note, some at a
        time and after the reply, so nothing waits for it. Group chats move on and have no such note."""
        channel_id = channel.channel_id
        try:
            summary, upto = await self.store.memory(channel_id)
            window = await self.store.recent(channel_id, limit=self.policy.context_messages)
            if not window:
                return
            older = [
                row
                for row in await self.store.after(channel_id, upto, limit=MEMORY_MOST)
                if row["id"] < window[0]["id"]
            ]
            if len(older) < self.policy.memory_batch:
                return
            messages = build_memory_prompt(bot_name=bot.name, summary=summary, rows=older)
            completion, _, _ = await self._complete(bot, messages, max_tokens=self.policy.memory_max_tokens)
            note = completion.text.strip()[: MEMORY_CHARS * 2]
            if note:
                await self.store.remember(channel_id, note, older[-1]["id"])
                log.info("%s 把私聊里 %d 条旧消息记成了笔记", bot.name, len(older))
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 这次没整理私聊笔记：%s", bot.name, exc)
        finally:
            self._remembering.discard(channel_id)

    async def _quoted(
        self, bot: BotConfig, channel_id: int, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Who and what each message replies to, for the prompt: without it a reply to an old line reads as
        a reply to whatever came just before."""
        out = []
        for row in rows:
            original = (
                await self.store.by_external_id(channel_id, row["reply_to"]) if row.get("reply_to") else None
            )
            if original is None:
                out.append(row)
                continue
            who = speaker(original) if original["from_self"] else original["sender_name"]
            words = " ".join(str(original["text"] or "").split())[:QUOTE_CHARS]
            out.append(row | {"quoted": f"{who}：{words}"})
        return out

    async def _generate(
        self,
        bot: BotConfig,
        channel: BotChannel,
        batch: list[dict[str, Any]],
        called: set[int],
        target: dict[str, Any],
        *,
        again: str = "",
    ) -> Draft:
        regenerated = False
        made_group = False  # 她拉群 by a mark: once, even when the answer is written again
        invite: dict[str, str] | None = None  # 她邀请你玩 by a mark
        invites = False  # …which she is told she may send
        kit: tuple[Any, Any] = ((), None)  # her tools in the chat with the user (nest.core.tools)
        private = _private(channel)
        lines = channel.reply_lines if self.chance() * 100 < channel.split_rate else 1  # 分条频率
        while True:
            history = await self.store.recent(
                channel.channel_id, limit=self.policy.context_messages + len(batch), before=batch[0]["id"]
            )
            # Taken back in the desk chat: gone, not a message marked as taken back.
            history = [r for r in history if not (r["recalled_at"] and from_app(r["external_id"]))]
            context = await self._heard(bot, channel.channel_id, history[-self.policy.context_messages :])
            shown = batch
            # 答题: only when someone called the bot (in a private chat every message does), and only for
            # what is not chat.
            sing = self._can_speak(bot, channel) and _sing_asked(batch, called)
            answering = channel.answer_questions and bool(called) and not sing
            sorting: Sorting | None = None
            sorted_as = ""
            records: list[dict[str, Any]] | None = None
            project: ProjectContext | None = None
            # 项目资料: the group answers questions about a project from its help docs.
            base = None
            if self.knowledge is not None:
                # In a private chat: only with whoever keeps a base, who may ask about it too.
                base = (
                    self.knowledge.base_kept_by(channel.channel.id, channel.channel.platform)
                    if private
                    else self.knowledge.base_for(group_key_of(channel.channel))
                )
            read = 0
            if answering and self.screenshots is not None and self.screenshots.ready:
                batch, read = await self._read_screenshots(bot, channel, batch, called)
                shown = batch if read else shown
            if answering:
                project_name = base.base.name if base is not None else ""
                sorting, sorted_as = await self._sort(bot, channel, context, batch, called, project_name)
                answering = sorting is None or sorting.kind != "chat"
                if sorting is not None and sorting.kind == "project" and base is not None and self.knowledge:
                    project, sorted_as = await self._look_up(bot, base, batch, called, sorting)
                if sorting is not None and sorting.kind == "history":
                    records = await self.store.search(
                        channel.channel_id,
                        words=list(sorting.keywords),
                        links=sorting.links,
                        before=min(called),  # anything said before the question, this batch too
                        limit=self.policy.history_matches,
                    )
                    sorted_as = f"问以前的聊天，翻到 {len(records)} 条记录"
            if read:
                sorted_as = f"读了 {read} 张截图；{sorted_as}" if sorted_as else f"读了 {read} 张截图"
            choices: list[dict[str, Any]] = []
            capable = capabilities_of(channel.channel.platform)
            desk_group = is_desk_group(channel.channel)
            in_group = (
                channel.sticker_mode != "off" and "send_image" in capable and not private and not desk_group
            )
            # 桌面聊天: her own 表情包套装 when she has one in use, else the collection;
            # never in her QQ private chat.
            at_desk = (
                (private or desk_group) and self.desk is not None and from_app(target.get("external_id"))
            )
            if at_desk and self.packs is not None and self.stickers is not None and not answering:
                at_desk = await self.packs.desk_on(bot.id) and not await self.stickers.store.sent_lately(
                    channel.channel_id, bot.id, DESK_STICKER_EVERY
                )
            else:
                at_desk = False
            if self.stickers is not None and (in_group or at_desk) and not answering:
                labels = await self.stickers.labels([*context, *batch])
                context, shown = relabel(context, labels), relabel(batch, labels)
                packs = self.packs
                # 「群里只发专属表情包」: in a platform's group too, hers only.
                group_own = in_group and packs is not None and await packs.group_own_only(bot.id)
                own = await packs.own(bot.id) if (at_desk or group_own) and packs else []
                own_only = at_desk and packs is not None and await packs.own_only(bot.id)
                choices, _ = await self.stickers.candidates(
                    channel.channel,
                    bot.id,
                    "\n".join(row["text"] for row in shown),
                    exclude=sticker_keys(batch),
                    # 「只用专属表情包」 (the default): hers only, none when she has none.
                    only=own if own_only or group_own else None,
                    prefer=own,
                    # In a group the packs' pictures stay out, but for hers when she sends only her own.
                    hide=await packs.all_pictures() if in_group and packs and not group_own else (),
                )
            if self.screenshots is not None and not answering:
                context, shown = await self.screenshots.noted(context, shown, bot.account_id)
            if desk_group:
                context, shown = with_files(context, shown)
            context = await self._quoted(bot, channel.channel_id, context)
            shown = await self._quoted(bot, channel.channel_id, shown)
            voice_asked = sing or _voice_asked(batch, called)
            speakable = await self._speakable(bot, channel, answering, voice_asked)
            glossary: list[tuple[str, str]] = []
            if self.slang is not None and channel.use_slang and not private and not answering:
                glossary = await self.slang.glossary(
                    group_key_of(channel.channel), [row["text"] for row in (*context, *shown)]
                )
            people = await self.memory.recall(bot, channel, [*context, *shown]) if self.memory else ""
            # Looked up on every attempt, with no await before the call: the model may have been edited
            # (its runtime swapped) or removed, or the bot switched to another one, while this evaluation
            # was waiting.
            provider = self._model(bot)
            if provider is None:
                return Draft("failed", "这个 bot 的模型刚被移除", regenerated=regenerated, target=target)
            search = (
                answering
                and records is None
                and project is None
                and provider.can_search
                and self._may_search(bot.id)
            )
            # A model that cannot search by itself: searched for it first (Tavily, when set up).
            searched = ""
            if (
                answering
                and not search
                and records is None
                and project is None
                and self.tools is not None
                and self.tools.search_key()
                and self._may_search(bot.id)
            ):
                topic = (
                    sorting.topic
                    if sorting is not None and sorting.topic
                    else str(shown[-1].get("text") or "")
                )
                searched = await self.tools.search_material(topic)
                if searched:
                    self._searched.setdefault(bot.id, []).append(time.time())
            # Her tools in her chat with the user, whichever way the message is answered: a request that
            # reads as a question (答题) still gets them, or "can you look at that folder?" never could.
            kit, note, lacking = ((), None), "", ""
            if private and self.tools is not None and await self.tools.available(bot, channel):
                await self.tools.prepare(bot)
                kit = self.tools.for_chat(bot, channel)
                note = self.tools.hint(bot)
            elif private and self.tools is not None:
                # Her model calls no tools: she is told, so she says so instead of pretending.
                lacking = note = await self.tools.missing(bot, channel)
            # 她拉群 without tools: a mark in her reply (nest.core.groups.split_group).
            mark_groups = private and kit[1] is None and self.groups is not None and len(self.bots) >= 2
            if mark_groups:
                note = f"{note}\n{GROUP_MARK_HINT}" if note else GROUP_MARK_HINT
            if again:
                note = "\n".join(x for x in (note, AGAIN.format(said=again)) if x)
            if answering:
                messages = build_answer_prompt(
                    persona=self._persona(bot, channel),
                    bot_name=bot.name,
                    context=context,
                    batch=shown,
                    called=called,
                    peers=self.peers_of(bot, anywhere=is_desk_group(channel.channel)),
                    private=private,
                    search=search,
                    now=time.time(),
                    sorting=sorting,
                    records=records,
                    project=project,
                    tone=self.tone(channel.answer_tone, DEFAULT_ANSWER),
                    place=_place_of(channel),
                    people=people,
                    note=note,
                    searched=searched,
                )
            elif private:
                # 一起玩: in the app the games have a board of their own; she invites to one with a mark.
                invites = (
                    self.desk is not None and self.games is not None and from_app(target.get("external_id"))
                )
                memory, _ = await self.store.memory(channel.channel_id)
                keepsake = await self.keepsakes.recall(bot, channel, shown) if self.keepsakes else ""
                if kit[1] is None and not again:
                    # Without tools a reminder she is asked for is read from the request here; with them she
                    # sets it herself.
                    asked = await self.reminders.from_chat(bot, channel, shown) if self.reminders else ""
                    note = "\n".join(x for x in (lacking, asked, GROUP_MARK_HINT if mark_groups else "") if x)
                if invites:
                    note = f"{note}\n{INVITE_HINT}" if note else INVITE_HINT
                messages = build_private_prompt(
                    persona=self._persona(bot, channel),
                    bot_name=bot.name,
                    context=context,
                    batch=shown,
                    tone=self.tone(channel.chat_tone, DEFAULT_CHAT),
                    voice=speakable,
                    sing=sing and speakable,
                    memory=memory,
                    place=_place_of(channel)[1],
                    people=people,
                    keepsake=keepsake,
                    note=note,
                    now=time.time(),
                    stickers=[self.stickers.describe_choice(s) for s in choices] if self.stickers else None,
                    life=(await self.life.line(bot) if self.life is not None else "")
                    + games_hint([*context, *shown])
                    + (await self.games.hint(bot.id) if self.games is not None else ""),
                    bubbles=self.desk is not None and from_app(target.get("external_id")),
                )
            else:
                messages = build_prompt(
                    persona=self._persona(bot, channel),
                    bot_name=bot.name,
                    context=context,
                    batch=shown,
                    called=called,
                    peers=self.peers_of(bot, anywhere=is_desk_group(channel.channel)),
                    stickers=[self.stickers.describe_choice(s) for s in choices] if self.stickers else None,
                    sticker_battle=bool(sticker_keys(batch)),
                    glossary=glossary,
                    tone=self.tone(channel.chat_tone, DEFAULT_CHAT),
                    voice=speakable,
                    sing=sing and speakable,
                    reply_lines=SONG_LINES if sing and speakable else lines,
                    place=_place_of(channel)[0],
                    people=people,
                    life=(await self.life.line(bot) if self.life is not None else "")
                    + games_hint([*context, *shown])
                    if is_desk_group(channel.channel)
                    else (
                        await self.group_games.hint(bot, channel, [*context, *shown], called)
                        if self.group_games is not None
                        else ""
                    ),
                    for_all=is_desk_group(channel.channel)
                    and target["sender_id"] not in self._our_accounts
                    and not target.get("from_self"),
                )
            terms = [term for term, _ in glossary]
            if answering:
                max_tokens = self.policy.answer_max_tokens
            else:
                max_tokens = self.policy.private_max_tokens if private else self.policy.max_tokens
            stream = self._desk_stream(bot, channel, target, answer=answering)
            try:
                completion, call_id, used = await self._complete(
                    bot,
                    messages,
                    max_tokens=max_tokens,
                    search=search,
                    tools=kit[0],
                    run=kit[1],
                    on_text=stream.feed if stream is not None else None,
                )
            except ProviderUnavailable as exc:
                return Draft("failed", str(exc), target=target)
            except ProviderError as exc:
                return Draft("failed", f"模型调用失败：{exc}", target=target)
            if completion.searches:
                self._searched.setdefault(bot.id, []).append(time.time())
            if completion.tools_used:
                note = "用了工具：" + "、".join(name for name, _ in completion.tools_used)
                sorted_as = f"{sorted_as}；{note}" if sorted_as else note
            own = (self.bots.get(bot.id) or bot).provider_id
            if used.config.id != own:
                note = f"{own} 用不了，用了备用模型 {used.config.id}"
                sorted_as = f"{note}；{sorted_as}" if sorted_as else note
            text, drafted = after_drafting(completion.text)
            if drafted:
                note = "模型把盘算也写进了回复，只取「输出：」后面的话"
                sorted_as = f"{sorted_as}；{note}" if sorted_as else note
            raw, spoken = split_voice(text)
            if mark_groups and not made_group:
                raw, group_asked = split_group(raw)
                if group_asked is not None:
                    made_group = True
                    made = await create_group_tool(self.groups, bot, group_asked)
                    sorted_as = f"{sorted_as}；拉群：{made}" if sorted_as else f"拉群：{made}"
            if private and not answering:
                raw, invite = split_invite(raw)
                if not invites:
                    invite = None
                if invite is not None and not raw.strip():
                    raw = "一起玩吧～"
            offered = [self.stickers.describe_choice(s) for s in choices] if self.stickers else []
            raw, number = split_sticker(raw, offered)
            if self.group_games is not None and not _private(channel) and not is_desk_group(channel.channel):
                raw = await self.group_games.finish(bot.id, channel.channel_id, raw)
            sticker = choices[number - 1] if number else None
            answer = answering and (sorting is not None or completion.searches > 0 or _long(raw))
            gap: dict[str, Any] | None = None
            if project is not None and base is not None:
                unsure = UNSURE in raw
                raw = raw.replace(UNSURE, "").strip()
                if unsure or not project.in_docs:
                    asked = [r for r in batch if r["id"] in called]
                    question = "\n".join(typed_text(r["segments"], r["text"]) for r in asked)
                    gap = {
                        "base_id": base.base.id,
                        "group_key": group_key_of(channel.channel),
                        "question": question.strip(),
                        "reason": "uncertain" if project.in_docs else "not_found",
                    }
            if answer:
                reply, why = clean_answer(raw, max_chars=self.policy.answer_max_chars)
            elif private:  # only people the bot was enabled for: no need to hide that it is a bot
                reply, why = clean_reply(
                    raw,
                    max_chars=self.policy.private_max_line_chars,
                    max_lines=self.policy.private_max_lines,
                    banned=(),
                )
                if len(reply) > self.policy.private_max_chars:
                    reply, why = "", f"回复太长（{len(reply)} 字），已丢弃"
            else:
                reply, why = clean_reply(
                    raw, max_chars=self.policy.max_reply_chars, max_lines=self.policy.max_reply_lines
                )
                if not (sing and speakable):  # a song is one voice message, its lines are its verses
                    reply, sticker = _fit(reply, sticker, lines, channel.reply_lines)
            # A sticker alone is a reply too.
            if not reply and not (sticker is not None and why == "模型没有给出内容"):
                return Draft(
                    "skipped",
                    why,
                    llm_call_id=call_id,
                    regenerated=regenerated,
                    target=target,
                    slang_terms=terms,
                    searches=completion.searches,
                    sorted_as=sorted_as,
                )

            later = await self.store.after(channel.channel_id, batch[-1]["id"])
            current = await self.store.message(target["id"])
            recalled = bool(current and current["recalled_at"])
            followed_up = any(r["sender_id"] == target["sender_id"] and not r["from_self"] for r in later)
            if recalled and len(batch) == 1 and not followed_up:
                regenerated = True  # nothing else to answer
            if not regenerated and (recalled or followed_up):
                regenerated = True
                if stream is not None:
                    stream.clear()
                fresh = [r for r in later if not r["from_self"]]
                if recalled:
                    batch = [r for r in batch if r["id"] != target["id"]]
                    called = called - {target["id"]}
                batch = (batch + fresh)[-self.policy.batch_limit :]
                called = called | {r["id"] for r in fresh if await self._row_mentions(bot, channel, r)}
                target = next(
                    (r for r in reversed(fresh) if r["sender_id"] == target["sender_id"]), batch[-1]
                )
                continue
            if recalled:
                return Draft(
                    "skipped",
                    "要接的消息被撤回了",
                    llm_call_id=call_id,
                    regenerated=regenerated,
                    target=target,
                )
            after_target = [
                r for r in await self.store.after(channel.channel_id, target["id"]) if not r["from_self"]
            ]
            reason = "只看不说：本来会回" if bot.mode != "live" else "准备发送"
            return Draft(
                "would_reply",
                reason,
                reply=reply,
                # An answer comes a while after the question: say which one it answers.
                quote=not private and (answer or len(after_target) >= self.policy.quote_after),
                regenerated=regenerated,
                llm_call_id=call_id,
                target=target,
                sticker=sticker,
                voice=speakable
                and (spoken or (voice_asked and readable(reply)) or self._rolled_voice(channel, reply)),
                voice_asked=voice_asked,
                sing=sing and speakable,
                slang_terms=terms,
                answer=answer,
                searches=completion.searches,
                sorted_as=sorted_as,
                gap=gap,
                project=project,
                streamed=stream is not None and bool(stream.shown),
                invite=invite,
            )

    def _desk_stream(
        self, bot: BotConfig, channel: BotChannel, target: dict[str, Any], *, answer: bool
    ) -> DeskStream | None:
        """逐字流式: an answer to something typed in the app is shown there while it is written. The chat
        window drops what it shows when the answer (or the decision that there is none) comes."""
        if self.desk is None or not _private(channel) or not from_app(target.get("external_id")):
            return None
        if bot.mode != "live":
            return None
        about = {"bot_id": bot.id, "channel_id": channel.channel_id}
        return DeskStream(
            lambda text: self.broadcaster.publish("desk_delta", {**about, "text": text}), answer=answer
        )

    async def _read_screenshots(
        self, bot: BotConfig, channel: BotChannel, batch: list[dict[str, Any]], called: set[int]
    ) -> tuple[list[dict[str, Any]], int]:
        """截图读字: the words in the pictures of what the bot was asked (and of the messages it quotes),
        added to the last of those messages. Returns the batch (changed copies, never the stored rows) and
        how many pictures had words."""
        assert self.screenshots is not None
        asked = [row for row in batch if row["id"] in called]
        quoted = []
        for row in asked:
            if row.get("reply_to"):
                found = await self.store.by_external_id(channel.channel_id, row["reply_to"])
                if found is not None:
                    quoted.append(found)
        urls = pictures([*asked, *quoted])
        place = _place_of(channel)[0]
        texts = await self.screenshots.read(urls, place=place, account=bot.account_id) if urls else []
        if not texts:
            return batch, 0
        note = "\n".join(f"[截图里的字：{text}]" for text in texts)
        target = asked[-1]
        changed = dict(
            target,
            text=f"{target['text']}\n{note}",
            segments=[*target["segments"], {"type": "text", "data": {"text": f"\n{note}"}}],
        )
        return [changed if row["id"] == target["id"] else row for row in batch], len(texts)

    async def _look_up(
        self,
        bot: BotConfig,
        base: LoadedBase,
        batch: list[dict[str, Any]],
        called: set[int],
        sorting: Sorting,
    ) -> tuple[ProjectContext, str]:
        """项目资料: the sections that answer a project question, and how to say so in the decision. The
        bot's own model picks the sections from the reranked candidates (nest.knowledge.choose)."""
        assert self.knowledge is not None
        question = "\n".join(typed_text(row["segments"], row["text"]) for row in batch if row["id"] in called)
        if sorting.error and sorting.error not in question:
            question += f"\n{sorting.error}"
        name = base.base.name

        async def ask(messages: list[ChatMessage], max_tokens: int) -> str:
            completion, _, _ = await self._complete(bot, messages, max_tokens=max_tokens)
            return completion.text

        try:
            found = await self.knowledge.retrieve(
                base, question, sorting.keywords, ask=ask if self._model(bot) is not None else None
            )
        except Exception:
            log.exception("项目资料「%s」检索失败", name)
            return ProjectContext(name, "", "", ()), f"问 {name}，查资料出错了"
        context = ProjectContext(
            name,
            self.knowledge.overview(base),
            self.knowledge.feature_titles(base),
            tuple(section.text() for section, _ in found.sections),
            in_docs=found.found,
        )
        if found.chosen:
            judged = f"，重排最相关 {found.best:.2f}" if found.reranked else "，没有重排"
            if found.reranked and not found.found:
                judged += "，认为资料里没有"
            note = f"问 {name}，模型从候选里挑了 {len(found.sections)} 节资料{judged}"
        elif not found.reranked:
            note = f"问 {name}，没有重排，用了 {len(found.sections)} 节资料"
        elif found.found:
            note = f"问 {name}，用了 {len(found.sections)} 节资料（最相关 {found.best:.2f}）"
        else:
            note = f"问 {name}，资料里没找到（最相关 {found.best:.2f}）"
        return context, note + (f"；{found.degraded}" if found.degraded else "")

    async def _sort(
        self,
        bot: BotConfig,
        channel: BotChannel,
        context: list[dict[str, Any]],
        batch: list[dict[str, Any]],
        called: set[int],
        project: str = "",
    ) -> tuple[Sorting | None, str]:
        """答题, step one: what the bot was called with, and how to say so in the decision. None: the model's
        sorting was unreadable or failed (the answer prompt then lets it decide)."""
        texts = [typed_text(row["segments"], row["text"]) for row in batch if row["id"] in called]
        if not worth_sorting(texts):
            return Sorting("chat"), "当闲聊：没有问句"
        if self._model(bot) is None:
            return None, ""
        messages = build_sort_prompt(
            bot_name=bot.name,
            context=context[-self.policy.sort_context :],
            batch=batch,
            called=called,
            peers=self.peers_of(bot, anywhere=is_desk_group(channel.channel)),
            private=_private(channel),
            project=project,
            place=_place_of(channel),
        )
        try:
            completion, _, _ = await self._complete(bot, messages, max_tokens=self.policy.sort_max_tokens)
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 分不出是不是正经问题（%s），交给回答时判断", bot.name, exc)
            return None, ""
        sorting = parse_sorting(completion.text)
        if sorting is None:
            return None, "没分出是不是正经问题"
        return sorting, {"chat": "当闲聊", "question": "正经问题"}.get(sorting.kind, "")

    def _may_search(self, bot_id: str) -> bool:
        """答题 searches are slow and use the subscription's allowance: at most so many per bot an hour."""
        since = time.time() - 3600
        recent = [at for at in self._searched.get(bot_id, []) if at > since]
        self._searched[bot_id] = recent
        return len(recent) < self.policy.answer_hourly_limit

    # -- live sending ------------------------------------------------------------------------------

    async def _speakable(self, bot: BotConfig, channel: BotChannel, plain: bool, asked: bool = False) -> bool:
        """Whether the model may mark this reply [语音]. `plain`: an answer: never."""
        if self.speech is None or plain or not self._can_speak(bot, channel):
            return False
        return not await self.speech.blocked(channel.channel, bot.id, time.time(), asked=asked)

    def _can_speak(self, bot: BotConfig, channel: BotChannel) -> bool:
        return (
            self.speech is not None
            and channel.voice_mode != "off"
            # 桌面上的伙伴 speaks in the desk chat (her voice shown beside the words).
            and (
                channel.channel.platform == DESK or "send_voice" in capabilities_of(channel.channel.platform)
            )
            and self.speech.ready(bot.voice)
        )

    def _rolled_voice(self, channel: BotChannel, reply: str) -> bool:
        """语音频率: whether a reply the model did not mark is spoken anyway."""
        return readable(reply) and self.chance() * 100 < channel.voice_rate

    async def _speak(
        self, bot: BotConfig, channel: BotChannel, current: BotConfig, draft: Draft
    ) -> tuple[Draft, bool]:
        """Try to send the reply as a voice message: (the draft, whether the words are dealt with). A voice
        message stands alone in QQ, so it quotes nobody. When it cannot go out, the words go out as text."""
        assert self.speech is not None and draft.reply
        mode = (current.channels.get(channel.channel_id) or channel).voice_mode
        if mode == "off":
            return draft, False
        if mode != "on":
            return replace(draft, voice_state="trial", voice_note="语音试运行：本来会用语音说"), False
        text = "，".join(draft.reply.split("\n"))
        if len(text) > self.speech.config.max_chars:
            return replace(draft, voice_note=f"太长（{len(text)} 字），没用语音"), False
        blocked = await self.speech.blocked(channel.channel, bot.id, time.time(), asked=draft.voice_asked)
        if blocked:
            return replace(draft, voice_note=f"没用语音：{blocked}"), False
        try:
            # Only Fish knows the singing marks: another service would read them out.
            if draft.sing and current.voice.startswith("fish:"):
                audio = await self.speech.sing(current.voice, draft.reply)
            else:
                audio = await self.speech.say(current.voice, text)
        except SpeechError as exc:
            log.warning("%s 的语音没合成出来：%s", bot.name, exc)
            return replace(draft, voice_note=f"语音没合成出来：{exc}"), False
        # Kept for the desk chat, where it plays, and with the words she said (heard_own_voice).
        name = None
        if self.desk_media is not None:
            name = await asyncio.to_thread(self.desk_media.save_audio, audio)
        now = time.time()
        waiting = [w for w in self._speaking.get(channel.channel, []) if now - w[0] < SPOKEN_WAIT]
        self._speaking[channel.channel] = [*waiting, (now, name, draft.reply)]
        sent = await self._perform(bot, channel, draft, [self.speech.segment(audio)])
        if sent.outcome == "replied":
            return replace(sent, voice_state="sent", reason="已唱歌" if draft.sing else "已发送语音"), True
        if sent.maybe_sent:
            return replace(sent, voice_state="unknown"), True
        return replace(draft, voice_state="failed", voice_note=f"语音{sent.reason}"), False

    async def _desk_voice(
        self, bot: BotConfig, channel: BotChannel, current: BotConfig, draft: Draft
    ) -> tuple[Draft, list[Segment]]:
        """In the desk chat a [语音] reply is shown as its words with her voice beside them, played there.
        The same settings and limits as a voice message on the platform."""
        if self.speech is None or self.desk_media is None or not draft.reply:
            return draft, []
        mode = (current.channels.get(channel.channel_id) or channel).voice_mode
        if mode != "on":
            return draft, []
        text = "，".join(draft.reply.split("\n"))
        if len(text) > self.speech.config.max_chars:
            return replace(draft, voice_note=f"太长（{len(text)} 字），没用语音"), []
        blocked = await self.speech.blocked(channel.channel, bot.id, time.time(), asked=draft.voice_asked)
        if blocked:
            return replace(draft, voice_note=f"没用语音：{blocked}"), []
        try:
            audio = await self.speech.say(current.voice, text)
        except SpeechError as exc:
            log.warning("%s 的语音没合成出来：%s", bot.name, exc)
            return replace(draft, voice_note=f"语音没合成出来：{exc}"), []
        name = await asyncio.to_thread(self.desk_media.save_audio, audio)
        if name is None:
            return replace(draft, voice_note="语音服务给的不是认得的音频"), []
        return replace(draft, voice_state="sent"), [voice_segment(name)]

    async def _send(self, bot: BotConfig, channel: BotChannel, draft: Draft) -> Draft:
        """_deliver, with a note in the reason when a reply meant to be spoken went out as text."""
        sent = await self._deliver(bot, channel, draft)
        if draft.invite is not None and sent.outcome == "replied" and self.games is not None:
            try:
                await self.games.invite(bot, channel, **draft.invite)
            except Exception:
                log.exception("%s 发一起玩的邀请时出错", bot.name)
            else:
                sent = replace(sent, reason=f"{sent.reason}；邀请一起玩{invite_name(**draft.invite)}")
        return replace(sent, reason=f"{sent.reason}；{sent.voice_note}") if sent.voice_note else sent

    async def _deliver(self, bot: BotConfig, channel: BotChannel, draft: Draft) -> Draft:
        """Send a would-be reply (live mode). The outcome says whether it went out; never retried.

        A reply of several lines goes out as one message per line, with a typing pause between them; only
        the first quotes. A reply with a sticker goes out as the words first, then the sticker as its own
        message, the way people send them. The sticker only goes out when 「发表情」 is on for this bot in
        this group ("trial": recorded as would-have-sent); a sticker that fails after the words went out
        leaves the reply sent."""
        current = self.bots.get(bot.id)
        if current is None or current.mode != "live":
            return replace(draft, reason="发送前被切回了只看不说，没有发出")
        at_desk = self._at_desk(draft)  # shown in the app: needs no connection, and comes as text
        if not at_desk and self.adapters.get(bot.account_id) is None:
            return replace(draft, outcome="failed", reason="这个账号没有在运行，没有发出")
        said = False
        capable = capabilities_of(channel.channel.platform)
        if draft.reply and draft.voice and self.speech is not None and not at_desk:
            draft, said = await self._speak(bot, channel, current, draft)
        lines: list[str] = []
        if at_desk and draft.reply and _private(channel) and not draft.answer and not draft.voice:
            lines = bubbles(draft.reply)
        if draft.reply and len(lines) > 1:  # 分条: short talk in the desk chat
            first, *rest = lines
            draft = await self._perform(bot, channel, draft, [Segment("text", {"text": first})])
            if draft.outcome == "replied":
                draft = await self._follow_up(bot, channel, draft, rest)
            if draft.outcome != "replied" or draft.sticker is None:
                return draft
        elif draft.reply and not said and (draft.answer or _private(channel)):  # reads best in one piece
            segments = [*_opening(draft, capable), Segment("text", {"text": draft.reply})]
            if at_desk and draft.voice and _private(channel):
                draft, voice = await self._desk_voice(bot, channel, current, draft)
                segments += voice
            draft = await self._perform(bot, channel, draft, segments)
            # In the desk chat a sticker may follow the words.
            if draft.outcome != "replied" or draft.sticker is None or not at_desk:
                return draft
        elif draft.reply:
            if not said:
                first, *rest = draft.reply.split("\n")
                segments = [*_opening(draft, capable), Segment("text", {"text": first})]
                draft = await self._perform(bot, channel, draft, segments)
                if draft.outcome == "replied" and rest:
                    draft = await self._follow_up(bot, channel, draft, rest)
            if draft.outcome != "replied" or draft.sticker is None:
                return draft
        if draft.sticker is None or self.stickers is None:
            return draft
        if draft.reply:
            await asyncio.sleep(self._typing(""))
        # The desk chat was let send one when the reply was written (its own switch, not the group's).
        mode = "on" if at_desk else (current.channels.get(channel.channel_id) or channel).sticker_mode
        words = bool(draft.reply)
        if mode != "on":
            if words:
                return replace(draft, sticker_state="trial", reason="已发送；表情试运行，本来还会发一张表情")
            return replace(draft, sticker_state="trial", reason="表情试运行：本来会发这张表情")
        blocked = await self.stickers.blocked(channel.channel)
        if blocked:
            if words:
                return replace(draft, reason=f"已发送；表情没有发：{blocked}")
            return replace(draft, outcome="skipped", reason=f"只想发表情，但{blocked}")
        try:
            if at_desk:
                picture = sticker_segment(draft.sticker["id"], self.stickers.describe_choice(draft.sticker))
            else:
                picture = await self.stickers.segment(draft.sticker)
        except OSError:
            if words:
                return replace(draft, reason="已发送；表情的图片文件不见了，没有发")
            return replace(draft, outcome="failed", reason="表情的图片文件不见了，没有发出")
        segments = [picture] if words else [*_opening(draft, capable), picture]
        sent = await self._perform(bot, channel, replace(draft, outcome="would_reply"), segments)
        if sent.outcome == "replied" or sent.maybe_sent:
            await self.stickers.store.used(draft.sticker["id"], time.time())
        state = "sent" if sent.outcome == "replied" else "unknown" if sent.maybe_sent else "failed"
        if not words:
            return replace(sent, sticker_state=state)
        if state == "sent":
            return replace(draft, sticker_state=state, reason="已发送，还发了一张表情")
        why = sent.reason.removeprefix("发送失败：").removeprefix("发送结果不确定，没有重发：")
        return replace(draft, sticker_state=state, reason=f"已发送；表情没发出：{why}")

    def _typing(self, line: str) -> float:
        return min(self.policy.typing_max, 0.8 + len(line) / self.policy.typing_chars_per_second)

    async def _follow_up(self, bot: BotConfig, channel: BotChannel, draft: Draft, lines: list[str]) -> Draft:
        """The second and later lines of a sent reply, each its own message after a typing pause. The
        reply stays sent when one of them fails; the lines after it are dropped, never retried."""
        for number, line in enumerate(lines, 2):
            if not draft.streamed:
                await asyncio.sleep(self._typing(line))
            current = self.bots.get(bot.id)
            if current is None or current.mode != "live":
                return replace(draft, reason=f"已发送前 {number - 1} 条；后面的没发，被切回了只看不说")
            segments = [Segment("text", {"text": line})]
            sent = await self._perform(bot, channel, replace(draft, outcome="would_reply"), segments)
            if sent.outcome != "replied":
                why = sent.reason.removeprefix("发送失败：").removeprefix("发送结果不确定，没有重发：")
                return replace(draft, reason=f"已发送前 {number - 1} 条；第 {number} 条没发出：{why}")
        return replace(draft, reason=f"已发送 {len(lines) + 1} 条")

    async def send_private(self, account_id: str, recipient: str, text: str) -> bool:
        """A private message from one of our accounts (掉线提醒, 问题汇总). `recipient` as in the settings; an
        account on another platform does not try: a Discord bot would ask to open a DM with a QQ number."""
        adapter = self.adapters.get(account_id)
        if adapter is None:
            return False
        platform, self_id = account_id.split(":", 1)
        to, user_id = parse_recipient(recipient)
        if to != platform:
            return False
        message = SendMessage(
            ChannelRef(platform, self_id, "private", user_id),
            (Segment("text", {"text": text}),),
            idempotency_key=f"private:{account_id}:{time.time():.3f}",
        )
        result = await adapter.perform(message)
        if result.state != "succeeded":
            log.info("%s 私聊 %s 没发出：%s", account_id, user_id, result.error)
        return result.state == "succeeded"

    async def forward(self, account_id: str, recipient: str, text: str) -> bool:
        """Pass something on to a person: from `account_id`, or, when they are on another platform, from a bot
        that is online there."""
        platform = parse_recipient(recipient)[0]
        others = [
            key
            for key, adapter in self.adapters.items()
            if key != account_id
            and key.partition(":")[0] == platform
            and can(key, "proactive")
            and adapter.status().state == "connected"
        ]
        for key in (account_id, *others):
            if await self.send_private(key, recipient, text):
                return True
        return False

    async def _perform(
        self, bot: BotConfig, channel: BotChannel, draft: Draft, segments: list[Segment]
    ) -> Draft:
        if self._at_desk(draft):
            assert self.desk is not None
            shown = await self.desk.deliver(bot, channel, segments)
            if shown.state == "succeeded":
                return replace(
                    draft, outcome="replied", reason="已在桌面聊天里回复", reply_message_id=shown.message_id
                )
            return replace(draft, outcome="failed", reason=f"发送失败：{shown.error}")
        adapter = self.adapters.get(bot.account_id)
        if adapter is None:  # e.g. its connection settings were just changed
            return replace(draft, outcome="failed", reason="这个账号没有在运行，没有发出")
        key = f"{bot.id}:{(draft.target or {}).get('id')}:{time.time():.3f}"
        trigger = (draft.target or {}).get("external_id") or None
        result = await adapter.perform(
            SendMessage(channel.channel, tuple(segments), idempotency_key=key, trigger=trigger)
        )
        if result.state == "succeeded":
            return replace(draft, outcome="replied", reason="已发送", reply_message_id=result.message_id)
        if result.state == "unknown":
            reason = f"发送结果不确定，没有重发：{result.error}"
            return replace(draft, outcome="failed", reason=reason, maybe_sent=True)
        return replace(draft, outcome="failed", reason=f"发送失败：{result.error}")

    def heard_own_voice(self, event: MessageEvent) -> MessageEvent:
        """Her voice message as the platform tells of it, before it is stored: with the words she said (in
        her history it reads as them, not as 「[语音]」) and the audio, which plays in the desk chat."""
        if not event.from_self or not any(s.type == "record" for s in event.segments):
            return event
        now = time.time()
        waiting = [w for w in self._speaking.get(event.channel, []) if now - w[0] < SPOKEN_WAIT]
        if not waiting:
            return event
        _, name, words = waiting.pop(0)
        self._speaking[event.channel] = waiting
        said = " ".join(words.split())
        segments = tuple(
            Segment(s.type, {**s.data, "words": said}) if s.type == "record" else s for s in event.segments
        )
        text = render_text(segments)
        if name is not None:
            segments += (voice_segment(name),)
        return replace(event, segments=segments, text=text)

    def _at_desk(self, draft: Draft) -> bool:
        """桌面聊天: the answer to a message typed in the app is shown there, not sent to the platform."""
        return self.desk is not None and from_app((draft.target or {}).get("external_id"))

    def _foreign(self, bot: BotConfig, now: float) -> None:
        """Our account spoke, but not through nest: another program is using it."""
        self.foreign_at[bot.id] = now
        self.broadcaster.publish(
            "foreign", {"bot_id": bot.id, "bot_name": bot.name, "at": now, "live": bot.mode == "live"}
        )
        if bot.mode == "live" and now - self._foreign_logged.get(bot.id, 0) > 600:
            self._foreign_logged[bot.id] = now
            log.warning("%s 正在正式发言，但有别的程序也在用这个号发消息，可能会重复回复", bot.name)

    async def purge(self, *, decisions_before: float, calls_before: float) -> int:
        return await self.repo.purge(decisions_before=decisions_before, calls_before=calls_before)


def _fit(
    reply: str, sticker: dict[str, Any] | None, lines: int, most: int
) -> tuple[str, dict[str, Any] | None]:
    """A chat reply within 一次最多发 (`most` messages, a sticker is one of them) and this reply's `lines`.
    With room for one message only, the words go out rather than the sticker."""
    if reply and sticker is not None and most == 1:
        sticker = None
    return fit_lines(reply, min(lines, most - (sticker is not None))), sticker


def _sing_asked(batch: list[dict[str, Any]], called: set[int]) -> bool:
    """Whether someone called the bot and asked for a song in the same message."""
    return any(
        SING_ASK.search(typed_text(row["segments"], row["text"])) for row in batch if row["id"] in called
    )


def _voice_asked(batch: list[dict[str, Any]], called: set[int]) -> bool:
    """Whether someone called the bot and said 语音 in the same message: it answers out loud."""
    return any("语音" in typed_text(row["segments"], row["text"]) for row in batch if row["id"] in called)


def typed_text(segments: Any, fallback: str = "") -> str:
    """What the sender typed or said, without @-names: "@小海豚粉" @-s a person; it does not call 小海豚."""
    if not segments:
        return fallback
    parts = []
    for segment in segments:
        kind, data = (
            (segment.type, segment.data)
            if isinstance(segment, Segment)
            else (segment.get("type"), segment.get("data") or {})
        )
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "record":
            parts.append(str(data.get("words", "")))
    return "".join(parts)


QUESTION_WORDS = ("有没有", "谁", "推荐", "怎么", "为什么", "为啥", "哪个", "哪里", "咋", "啥")


def open_question(event: MessageEvent) -> bool:
    """A question put to the whole group: the natural moment for an occasional bot to join in."""
    if event.mentions or event.reply_to:
        return False  # addressed to someone in particular
    text = typed_text(event.segments).strip()
    return bool(text) and (
        "?" in text
        or "？" in text
        or text.endswith(("吗", "呢", "么", "嘛"))
        or any(word in text for word in QUESTION_WORDS)
    )


def _long(raw: str) -> bool:
    """Too long for a chat line: the model answered at length (答题) rather than chatting."""
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    return len(lines) > 3 or any(len(line.strip()) > 40 for line in lines)


def _private(channel: BotChannel) -> bool:
    return channel.channel.kind == "private"


def _place_of(channel: BotChannel) -> tuple[str, str]:
    """(what a group is called, what a private chat is called) here: 桌面小群 for a 桌面群."""
    return DESK_PLACE if is_desk_group(channel.channel) else place_of(channel.channel.platform)


def _opening(draft: Draft, capable: Collection[str] = ("mention", "reply")) -> list[Segment]:
    """What a reply starts with: the quote, or an @ when this account never received the message (it has
    no id of its own to quote). Whichever the platform cannot do is left out."""
    target = draft.target
    if target is None:
        return []
    if not target["external_id"]:
        return [Segment("at", {"id": target["sender_id"]})] if "mention" in capable else []
    return [Segment("reply", {"id": target["external_id"]})] if draft.quote and "reply" in capable else []


def _missed(
    rows: list[dict[str, Any]], channel_id: int, ours: Collection[str], after: float
) -> list[dict[str, Any]]:
    """Of the group's @s, one row per real message, from people, that this bot may not have answered: its
    own copy came from history, or (since `after`) only another account has one (shown to the model as this
    channel's, without an id to quote), or it was received live, which the caller checks."""
    copies: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["sender_id"], int(row["sent_at"]), row["text"])
        if key not in copies or row["channel_id"] == channel_id:
            copies[key] = row
    missed = []
    for row in copies.values():
        mine = row["channel_id"] == channel_id
        live = mine and row["source"] != "backfill"
        if row["sender_id"] in ours or (not live and row["sent_at"] <= after):
            continue
        elsewhere = {} if mine else {"channel_id": channel_id, "external_id": ""}
        missed.append({**row, **elsewhere, "missed": True})
    return missed


def _named(bot: BotConfig, text: str) -> bool:
    return any(alias and calls(alias, text) for alias in (bot.name, *bot.aliases))


# What may follow a one-character name when it is said to her: 「星你看看」「星在吗」「星帮我」.
CALL_NEXT = frozenset("你在帮来呀啊呢")


def calls(name: str, text: str) -> bool:
    """Whether the text calls her by this name. A name of two or more characters anywhere in it does. One
    character is usually part of a word (星 in 星期, 明星, 火星), so it counts only when said to
    her: right after an @, or standing on its own (nothing word-like before it, and after it nothing, a
    pause, or a word like 你/在/帮 as in 「星你看看」)."""
    if len(name) > 1:
        return name in text
    at = text.find(name)
    while at != -1:
        before = text[at - 1] if at else ""
        after = text[at + 1] if at + 1 < len(text) else ""
        if before == "@" or (not _wordlike(before) and (not _wordlike(after) or after in CALL_NEXT)):
            return True
        at = text.find(name, at + 1)
    return False


def _wordlike(char: str) -> bool:
    """A letter (Chinese characters included) or a digit: part of a word, not a pause or the edge."""
    return bool(char) and unicodedata.category(char)[0] in "LN"
