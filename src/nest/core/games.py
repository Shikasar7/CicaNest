"""小游戏 (伙伴模式): games the user plays with a companion in the desktop app — 五子棋 and 黑白棋 on a board,
海龟汤, and 你画我猜.

Board games: the board is drawn in the app; a move goes through the API. Her moves come from a small engine
(nest.core.gomoku, nest.core.reversi, seen alike through nest.core.board_rules) after a moment of 「想」, not
from her model. Her model talks: at the start, at the end, and now and then when something happens on the
board (a threat, a corner, many discs turned, your taking a move back), a short line in her own voice, maybe
with a 表情包, into her chat with the user, where the game room shows it. What is typed in the game room is a
message of that chat like any other; while a game is on, her answers are told how it stands (`hint`), so she
talks about the game without calling out moves.

海龟汤 (nest.core.soup): her model makes up the puzzle and judges each question; question, answer and her word
stay in the game (the game room shows them), not in the chat. 你画我猜 (nest.core.sketch): the user draws, the
vision model says what is on the paper and her model guesses; or she draws (nest.core.drawing) and the user
guesses. The drawings are kept as files (`media`).

Each change of a game is published as a "game" event carrying the game as the API shows it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nest.core import sketch, soup
from nest.core.board_rules import PASS, RULES, Rules, to_move
from nest.core.desk import chat_of, chosen_chats, sticker_segment
from nest.core.events import Segment
from nest.core.prompt import clock_label, speaker, split_sticker
from nest.core.stickers import data_url, preview_png
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.purpose import for_purpose
from nest.llm.runtime import ProviderUnavailable
from nest.storage.bots import BotChannel, BotConfig
from nest.storage.db import Database
from nest.storage.games import GameStore

log = logging.getLogger(__name__)

BLACK, WHITE = 1, 2
KINDS = {"gomoku": "五子棋", "reversi": "黑白棋", "soup": "海龟汤", "draw": "你画我猜"}
LEVELS = ("easy", "normal", "hard")
LEVEL_NAMES = {"easy": "轻松", "normal": "认真", "hard": "全力"}
COLOR_NAMES = {BLACK: "黑子", WHITE: "白子"}
KIND = "g:"  # her lines in a game: "app:g:…" (not answers: no 重说)
THINK = (0.7, 1.8)  # seconds she 「想」 before a move
REACT_GAP = 20.0  # seconds between two lines of hers in the middle of a game
REACT_CHANCE = 0.6  # something notable on the board gets a line this often
MAX_TOKENS = 200
HINT_FRESH = 3 * 3600.0  # a game untouched this long is not mentioned in her answers
AFTER_FRESH = 15 * 60.0  # a game that ended this long ago still is
LINE_MAX = 60
STICKER_EVERY = 3  # at most one 表情包 in her last this many messages (as in her answers)

MOMENTS = {
    "start": "你们刚开始一局，对方先下。",
    "start_her": "你们刚开始一局，你先下，你已经落了第一子。",
    "you_three": "对方刚下出一个活三，有点威胁。",
    "you_four": "对方刚冲四，差一步就连成五个了，你得赶紧堵。",
    "her_three": "你刚下出一个活三，有点得意。",
    "her_four": "你刚冲四，对方必须堵你。",
    "you_corner": "对方刚占了一个角，这下有点难办。",
    "her_corner": "你刚占了一个角，有点得意。",
    "you_flip": "对方这一步一下子翻了你好几个子。",
    "her_flip": "你这一步一下子翻了对方好几个子。",
    "you_pass": "对方没地方下，只好跳过一回合，你接着下。",
    "her_pass": "你没地方下，只好跳过一回合。",
    "undo": "对方刚悔了一步棋（这局第 {undos} 次）。",
    "resigned": "对方认输了，这局算你赢。",
}
ENDS = {
    "gomoku": {
        "won": "这局对方赢了，连成了五个。",
        "lost": "这局你赢了，你连成了五个。",
        "draw": "棋盘下满了，这局和棋。",
    },
    "reversi": {
        "won": "这局对方赢了，最后数子：{score}。",
        "lost": "这局你赢了，最后数子：{score}。",
        "draw": "最后子数一样多，这局和棋。",
    },
}
ASK = """你正在和对方在桌面上用棋盘下{game}（你执{color}，{level}难度，已经下了 {count} 手）。
{moment}
局面：{situation}。{record}
现在是 {now}。
最近的聊天：
{talk}

用你自己的语气说一句很短的话（不超过 20 个字），像边下棋边随口说的。
不要报坐标，不要替对方下，不要说教。{stickers}
不想说就只输出 SKIP。"""
STICKERS = "\n你可以发的表情（想发就在最后写编号，比如 [表情1]；也可以只写编号，只发表情）：\n{listed}"
HINT = (
    "（你们正在桌面上用棋盘下{game}：你执{color}，{level}难度，已经下了 {count} 手，{situation}。"
    "棋盘在对方眼前，落子在棋盘上点，你不用报坐标，也不用在聊天里替谁下；聊天时可以自然地说说这局棋。）"
)
HINT_AFTER = "（你们刚在桌面上下完一局{game}：{result}。{record}）"
RESULTS = {
    "won": "对方赢了",
    "lost": "你赢了",
    "draw": "和棋",
    "resigned": "对方认输，你赢了",
    "left": "没下完",
}
SOUP_RESULTS = {"won": "对方猜中了汤底", "resigned": "对方没猜出来，你揭晓了汤底", "left": "没玩完"}
DRAW_RESULTS = {
    "won": "你猜中了",
    "lost": "你三次都没猜中",
    "resigned": "对方直接公布了答案",
    "left": "没玩完",
}
SKETCHES_KEPT = 50  # 你画我猜: the drawings of this many newest games are kept
HER_RESULTS = {
    "won": "对方猜中了",
    "lost": "对方五次都没猜中",
    "resigned": "对方放弃了，看了答案",
    "left": "没玩完",
}
PICTURE_SIDE = 768  # 她画你猜: her picture is kept this big
NO_EYES = "她现在看不了图：先在控制台「表情包」里选一个识图模型"


def _line(text: str) -> str:
    words = " ".join(text.split()).strip().strip("「」\"'")
    return "" if not words or words.upper().startswith("SKIP") else words[:LINE_MAX]


def record_line(record: dict[str, int], kind: str = "gomoku") -> str:
    if not any(record.values()):
        return ""
    if kind == "soup":
        return f"你出的海龟汤对方猜中过 {record['won']} 道、没猜出 {record['lost']} 道。"
    if kind == "draw":
        return f"对方画的你猜中过 {record['won']} 幅、没猜出 {record['lost']} 幅。"
    draw = f"、和 {record['draw']} 局" if record["draw"] else ""
    return (
        f"你们一共下完 {sum(record.values())} 局：对方赢 {record['won']} 局、你赢 {record['lost']} 局{draw}。"
    )


def score_line(score: dict[int, int] | None, her_color: int) -> str:
    if not score:
        return ""
    return f"你 {score[her_color]} 子、对方 {score[BLACK + WHITE - her_color]} 子"


class GameKeeper:
    def __init__(
        self,
        db: Database,
        brain: Any,
        broadcaster: Any,
        *,
        clock: Callable[[], float] = time.time,
        chance: Callable[[], float] = random.random,
        think: tuple[float, float] = THINK,
        media: Path | None = None,
        vision: Callable[[], Any] = lambda: None,
        drawing: Any = None,
    ) -> None:
        self.store = GameStore(db)
        self.media = media  # 你画我猜's drawings, as "<game>-<n>.png"
        self.vision = vision  # the model that looks at pictures (表情包's), or None
        self.drawing = drawing  # 她画你猜: nest.core.drawing.Drawing
        self.db = db
        self.brain = brain
        self.broadcaster = broadcaster
        self.clock = clock
        self.chance = chance
        self.think = think
        self._locks: dict[int, asyncio.Lock] = {}
        self._thinking: set[int] = set()
        self._turns: dict[int, asyncio.Task[None]] = {}
        self._talks: set[asyncio.Task[None]] = set()
        self._said_at: dict[str, float] = {}  # bot id -> when she last said something in a game

    async def stop(self) -> None:
        tasks = [*self._turns.values(), *self._talks]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- what the app sees -----------------------------------------------------------------------------

    async def view(self, game: dict[str, Any]) -> dict[str, Any]:
        moves = game["moves"]
        playing = game["status"] == "playing"
        kind = game["kind"]
        shown: dict[str, Any] = {
            "id": game["id"],
            "bot_id": game["bot_id"],
            "kind": kind,
            "level": game["level"],
            "status": game["status"],
            "thinking": game["id"] in self._thinking,
            "undos": game["undos"],
            "record": await self.store.record(game["bot_id"], kind),
            "created_at": game["created_at"],
            "updated_at": game["updated_at"],
        }
        if kind == "draw":
            drawn = sketch.shown_state(game["state"], ended=not playing)
            shown |= {
                "draw": drawn,
                "thinking": playing and (drawn["looking"] or drawn["painting"]),
                "turn": None,
            }
            return shown
        rules = RULES.get(kind)
        if rules is None:
            state = soup.shown_state(game["state"], ended=not playing)
            shown["soup"] = state
            shown["thinking"] = playing and (
                state["preparing"]
                or any(q.get("answer") is None and q.get("solved") is None for q in state["questions"])
            )
            shown["turn"] = None
            return shown
        mine = to_move(moves) != game["her_color"]
        shown |= {
            "size": rules.size,
            "moves": [list(m) for m in moves],
            "board": rules.grid(moves),
            "her_color": game["her_color"],
            "win": [list(m) for m in game["win"]],
            "turn": ("you" if mine else "her") if playing else None,
            "score": {str(k): v for k, v in (rules.score(moves) or {}).items()} or None,
            "options": [list(m) for m in rules.options(moves)] if playing and mine else [],
        }
        return shown

    async def _publish(self, game_id: int) -> dict[str, Any]:
        game = await self.store.get(game_id)
        assert game is not None
        shown = await self.view(game)
        self.broadcaster.publish("game", shown)
        return shown

    async def current(self, bot_id: str) -> dict[str, Any] | None:
        game = await self.store.current(bot_id)
        return await self.view(game) if game is not None else None

    def _lock(self, game_id: int) -> asyncio.Lock:
        return self._locks.setdefault(game_id, asyncio.Lock())

    async def _mine(self, bot: BotConfig, game_id: int) -> tuple[dict[str, Any] | None, str]:
        game = await self.store.get(game_id)
        if game is None or game["bot_id"] != bot.id:
            return None, "没有这局"
        if game["status"] != "playing":
            return None, "这局已经结束了"
        return game, ""

    def _chat(self, bot: BotConfig, game: dict[str, Any]) -> BotChannel | None:
        live = self.brain.bots.get(bot.id) or bot
        return live.channels.get(game["channel_id"])

    # -- starting --------------------------------------------------------------------------------------

    async def start(
        self,
        bot: BotConfig,
        *,
        kind: str = "gomoku",
        level: str = "normal",
        first: str = "",
        flavor: str = "clear",
        word: str = "",
        mode: str = "you",
    ) -> tuple[dict[str, Any] | None, str]:
        """A new game with her (one left unfinished ends as 'left'). `first` (board games): "you", "her",
        or "" (in turn: whoever did not begin the last one of this game). `flavor` (海龟汤): soup.FLAVORS."""
        if kind not in KINDS:
            return None, "没有这个游戏"
        if level not in LEVELS:
            return None, "难度只能是轻松、认真或全力"
        chat = chat_of(bot, (await chosen_chats(self.db)).get(bot.id))
        if chat is None:
            return None, f"还没有和{bot.name}的聊天，先在聊天页开一个"
        old = await self.store.current(bot.id)
        if old is not None and old["status"] == "playing":
            self._cancel_turn(old["id"])
        if kind == "draw" and mode == "her":
            if self.drawing is None or self.media is None:
                return None, f"{bot.name}现在画不了画"
            if why := self.drawing.claim(bot):
                return None, why.removeprefix("没画：")
            state = {"mode": "her", "word": sketch.pick(level, self.chance), "guesses": [], "painting": True}
            game_id = await self.store.add(
                bot_id=bot.id, channel_id=chat.channel_id, kind=kind, her_color=0, level=level, state=state
            )
            await self._prune_sketches()
            shown = await self._publish(game_id)
            self._spawn(game_id, self._paint(bot, game_id))
            return shown, ""
        if kind == "draw":
            word = " ".join(word.split())[: sketch.WORD_MAX]
            state = {"word": word or sketch.pick(level, self.chance), "own": bool(word), "guesses": []}
            game_id = await self.store.add(
                bot_id=bot.id, channel_id=chat.channel_id, kind=kind, her_color=0, level=level, state=state
            )
            await self._prune_sketches()
            return await self._publish(game_id), ""
        if kind == "soup":
            if flavor not in soup.FLAVORS:
                return None, "没有这种口味"
            game_id = await self.store.add(
                bot_id=bot.id,
                channel_id=chat.channel_id,
                kind=kind,
                her_color=0,
                level=level,
                state={"flavor": flavor, "preparing": True, "questions": [], "given": 0},
            )
            shown = await self._publish(game_id)
            self._spawn(game_id, self._cook(bot, chat, game_id))
            return shown, ""
        if first not in ("you", "her"):
            last = await self.store.last_of(bot.id, kind)
            first = "her" if last is not None and last["her_color"] == WHITE else "you"
        her_color = BLACK if first == "her" else WHITE
        game_id = await self.store.add(
            bot_id=bot.id, channel_id=chat.channel_id, kind=kind, her_color=her_color, level=level
        )
        if first == "her":
            self._thinking.add(game_id)  # shown as thinking from the start
        shown = await self._publish(game_id)
        if first == "her":
            self._her_turn(bot, chat, game_id, moment="start_her")
        else:
            self._talk(bot, chat, game_id, "start")
        return shown, ""

    # -- board games -----------------------------------------------------------------------------------

    async def move(self, bot: BotConfig, game_id: int, x: int, y: int) -> tuple[dict[str, Any] | None, str]:
        """The user's stone on (x, y); her answer follows by itself."""
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            rules = RULES.get(game["kind"])
            if rules is None:
                return None, "这个游戏不用落子"
            moves = list(game["moves"])
            if to_move(moves) == game["her_color"] or game_id in self._thinking:
                return None, f"还没轮到你，{bot.name}在想"
            if not rules.legal(moves, (x, y)):
                return None, "这里不能下"
            moves = rules.with_passes([*moves, (x, y)])
            made = rules.moment(moves[: len(moves) - (moves[-1] == PASS)])
            her_passed = moves[-1] == PASS
            ended = await self._settle(game, rules, moves)
            chat = self._chat(bot, game)
            if ended or to_move(moves) != game["her_color"]:
                shown = await self._publish(game_id)
                if chat is not None and ended:
                    self._talk(bot, chat, game_id, ended, final=True)
                elif chat is not None and her_passed:
                    self._talk(bot, chat, game_id, "her_pass")
                return shown, ""
            self._thinking.add(game_id)
            shown = await self._publish(game_id)
        if chat is not None:
            self._her_turn(bot, chat, game_id, moment=f"you_{made}" if made and made != "pass" else None)
        else:
            self._thinking.discard(game_id)
        return shown, ""

    async def _settle(self, game: dict[str, Any], rules: Rules, moves: list[tuple[int, int]]) -> str:
        """Save the moves; when the game is over, how it ended for the user ("won", "lost", "draw");
        else ''."""
        winner, five = rules.outcome(moves)
        if winner is None:
            await self.store.save(game["id"], moves)
            return ""
        status = "draw" if winner == 0 else "lost" if winner == game["her_color"] else "won"
        await self.store.save(game["id"], moves, status=status, win=five)
        return status

    async def undo(self, bot: BotConfig, game_id: int) -> tuple[dict[str, Any] | None, str]:
        """悔棋: the user's last move goes back (with what came after it)."""
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            if game["kind"] not in RULES:
                return None, "这个游戏不能悔棋"
            if game_id in self._thinking:
                return None, f"{bot.name}正在想，等她落子再悔"
            moves = list(game["moves"])
            her = game["her_color"]
            mine = [i for i in range(len(moves)) if to_move(moves[:i]) != her and moves[i] != PASS]
            if not mine:
                return None, "你还没下呢"
            moves = moves[: mine[-1]]
            undos = game["undos"] + 1
            await self.store.save(game_id, moves, undos=undos)
            shown = await self._publish(game_id)
        chat = self._chat(bot, game)
        if chat is not None and self.chance() < REACT_CHANCE:
            self._talk(bot, chat, game_id, "undo", undos=undos)
        return shown, ""

    async def resign(self, bot: BotConfig, game_id: int) -> tuple[dict[str, Any] | None, str]:
        """认输; in 海龟汤, give up and see the 汤底."""
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            if game["kind"] == "soup" and game["state"].get("preparing"):
                return None, f"{bot.name}还在想题目"
            if game["kind"] == "draw" and game["state"].get("painting"):
                return None, f"{bot.name}还在画"
            self._cancel_turn(game_id)
            state = None
            if game["kind"] == "soup":  # a question still being judged stays unanswered
                asked = [
                    q
                    for q in game["state"].get("questions", [])
                    if q.get("answer") or q.get("solved") is not None
                ]
                state = {**game["state"], "questions": asked}
            await self.store.save(game_id, list(game["moves"]), status="resigned", state=state)
            shown = await self._publish(game_id)
        if game["kind"] in RULES and (chat := self._chat(bot, game)) is not None:
            self._talk(bot, chat, game_id, "resigned", final=True)
        if game["kind"] == "draw" and game["state"].get("mode") == "her":
            self._spawn(game_id, self._end_line(bot, game_id))
        return shown, ""

    # -- 你画我猜 --------------------------------------------------------------------------------------

    async def new_word(
        self, bot: BotConfig, game_id: int, word: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        """Another word to draw (one of the user's own, or another from the list), before she has guessed."""
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            if game["kind"] != "draw" or game["state"].get("mode") == "her":
                return None, "这个游戏没有题目可换"
            state = game["state"]
            if state.get("guesses"):
                return None, "她已经开始猜了，这一幅画完再换"
            word = " ".join(word.split())[: sketch.WORD_MAX]
            fresh = word or sketch.pick(game["level"], self.chance, not_=state.get("word", ""))
            await self.store.save(game_id, [], state={**state, "word": fresh, "own": bool(word)})
            return await self._publish(game_id), ""

    def sketch_file(self, game_id: int, number: int) -> Path | None:
        if self.media is None:
            return None
        path = self.media / f"{game_id}-{number}.png"
        return path if path.is_file() else None

    async def _prune_sketches(self) -> None:
        if self.media is None or not self.media.is_dir():
            return
        rows = await self.db.fetchall(
            "SELECT id FROM games WHERE kind = 'draw' ORDER BY id DESC LIMIT ?", (SKETCHES_KEPT,)
        )
        kept = {int(r["id"]) for r in rows}
        for path in self.media.glob("*-*.png"):
            head = path.name.split("-", 1)[0]
            if head.isdigit() and int(head) not in kept:
                path.unlink(missing_ok=True)

    async def hand_over(self, bot: BotConfig, game_id: int, png: bytes) -> tuple[dict[str, Any] | None, str]:
        """The drawing so far: she looks at it and guesses (a "game" event brings her guess)."""
        if not png.startswith(b"\x89PNG") or len(png) > sketch.SKETCH_MAX:
            return None, "画没传好，再试一次"
        if self.media is None:
            return None, "这里存不了画"
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            if game["kind"] != "draw" or game["state"].get("mode") == "her":
                return None, "这一局是她画"
            if self.vision() is None:
                return None, NO_EYES
            state = game["state"]
            if state.get("looking"):
                return None, f"{bot.name}还在看上一幅"
            guesses = list(state.get("guesses", []))
            number = len(guesses) + 1
            self.media.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread((self.media / f"{game_id}-{number}.png").write_bytes, png)
            guesses.append({"n": number, "guess": None, "right": False, "at": self.clock()})
            await self.store.save(
                game_id, [], state={**state, "guesses": guesses, "looking": True, "error": ""}
            )
            shown = await self._publish(game_id)
        chat = self._chat(bot, game)
        if chat is None:
            return None, f"{bot.name}的聊天不在了"
        self._spawn(game_id, self._look(bot, chat, game_id, number))
        return shown, ""

    async def _paint(self, bot: BotConfig, game_id: int) -> None:
        """她画你猜: she draws the word."""
        error = ""
        try:
            game = await self.store.get(game_id)
            if game is None or self.drawing is None or self.media is None:
                return
            picture = await self.drawing.picture(sketch.PAINT.format(word=game["state"]["word"]))
            png = await asyncio.to_thread(preview_png, picture, PICTURE_SIDE)
            self.media.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread((self.media / f"{game_id}-0.png").write_bytes, png)
        except asyncio.CancelledError:
            raise
        except (ProviderUnavailable, ProviderError) as exc:
            error = f"{bot.name}没画成：{exc}"
        except Exception as exc:
            log.exception("%s 画你画我猜的画时出错", bot.name)
            error = f"{bot.name}没画成：{exc}"
        finally:
            if self.drawing is not None:
                self.drawing.release(bot)
        async with self._lock(game_id):
            game = await self.store.get(game_id)
            if game is None or game["status"] != "playing":
                return
            state = {**game["state"], "painting": False, "painted": not error, "error": error}
            await self.store.save(game_id, [], status="left" if error else "playing", state=state)
            await self._publish(game_id)

    async def answer(self, bot: BotConfig, game_id: int, text: str) -> tuple[dict[str, Any] | None, str]:
        """她画你猜: what the user thinks she drew."""
        text = " ".join(text.split())[:20]
        if not text:
            return None, "猜一个词吧"
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            state = game["state"]
            if game["kind"] != "draw" or state.get("mode") != "her":
                return None, "这一局不是她画"
            if not state.get("painted"):
                return None, f"{bot.name}还在画"
            guesses = list(state.get("guesses", []))
            right = sketch.is_right(text, state["word"])
            guesses.append({"n": len(guesses) + 1, "guess": text, "right": right, "at": self.clock()})
            status = "won" if right else "lost" if len(guesses) >= sketch.HER_TRIES else "playing"
            await self.store.save(game_id, [], status=status, state={**state, "guesses": guesses})
            shown = await self._publish(game_id)
        if status != "playing":
            self._spawn(game_id, self._end_line(bot, game_id))
        return shown, ""

    @for_purpose("game")
    async def _end_line(self, bot: BotConfig, game_id: int) -> None:
        """她画你猜 is over: a word of hers about it, shown with the answer."""
        game = await self.store.get(game_id)
        chat = self._chat(bot, game) if game is not None else None
        if game is None or chat is None:
            return
        state = game["state"]
        live = self.brain.bots.get(bot.id) or bot
        try:
            choices = await self._stickers(live, chat)
            offered = [self.brain.stickers.describe_choice(s) for s in choices] if choices else []
            listed = "\n".join(f"[表情{n}] {text}" for n, text in enumerate(offered, 1))
            tried = (
                "、".join(g["guess"] for g in state.get("guesses", []) if g.get("guess")) or "（一次也没猜）"
            )
            ask = sketch.END.format(
                word=state["word"],
                tried=tried,
                result=HER_RESULTS.get(game["status"], "结束了"),
                stickers=sketch.STICKERS.format(listed=listed) if listed else "",
            )
            messages = [ChatMessage("system", self._soup_system(live, chat)), ChatMessage("user", ask)]
            completion, _, _ = await self.brain._complete(live, messages, max_tokens=MAX_TOKENS)
            words, picked = split_sticker(completion.text, offered)
            line = _line(words)
            sticker = {"id": choices[picked - 1]["id"], "label": offered[picked - 1]} if picked else None
        except asyncio.CancelledError:
            raise
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 想说句话，模型没回（%s）", live.name, exc)
            return
        except Exception:
            log.exception("%s 你画我猜结束时说话出错", bot.name)
            return
        if not line and sticker is None:
            return
        async with self._lock(game_id):
            game = await self.store.get(game_id)
            if game is None:
                return
            state = {**game["state"], "line": line, "line_sticker": sticker}
            await self.store.save(game_id, [], status=game["status"], state=state)
            await self._publish(game_id)
        if sticker is not None:
            await self.brain.stickers.store.used(sticker["id"], time.time())

    @for_purpose("game")
    async def _look(self, bot: BotConfig, chat: BotChannel, game_id: int, number: int) -> None:
        said: dict[str, Any] | None = None
        sticker: dict[str, Any] | None = None
        label, error = "", ""
        try:
            game = await self.store.get(game_id)
            path = self.sketch_file(game_id, number)
            eyes = self.vision()
            if game is None or path is None:
                return
            if eyes is None:
                error = NO_EYES
            else:
                png = await asyncio.to_thread(path.read_bytes)
                looked = [ChatMessage("user", sketch.LOOK, images=(data_url(png),))]
                try:
                    seen, _ = await eyes.complete(looked, bot_id=bot.id, max_tokens=sketch.LOOK_TOKENS)
                except (ProviderUnavailable, ProviderError) as exc:
                    seen, error = None, f"识图模型没看清这幅画：{exc}"
                if seen is not None and seen.text.strip():
                    live = self.brain.bots.get(bot.id) or bot
                    choices = await self._stickers(live, chat)
                    offered = [self.brain.stickers.describe_choice(s) for s in choices] if choices else []
                    listed = "\n".join(f"[表情{n}] {text}" for n, text in enumerate(offered, 1))
                    ask = sketch.GUESS.format(
                        seen=" ".join(seen.text.split())[:300],
                        before=sketch.before_lines(game["state"].get("guesses", [])),
                        n=number,
                        tries=sketch.TRIES,
                        stickers=sketch.STICKERS.format(listed=listed) if listed else "",
                    )
                    messages = [
                        ChatMessage("system", self._soup_system(live, chat)),
                        ChatMessage("user", ask),
                    ]
                    for _ in range(2):  # once more when what came back could not be read
                        try:
                            done, _, _ = await self.brain._complete(
                                live, messages, max_tokens=sketch.GUESS_TOKENS
                            )
                        except (ProviderUnavailable, ProviderError) as exc:
                            error = f"{live.name}没想出来：{exc}"
                            break
                        found = soup.read_json(done.text)
                        guess = " ".join(str((found or {}).get("guess") or "").split())[:20]
                        if guess:
                            words, picked = (
                                split_sticker(str(found.get("line") or ""), offered) if found else ("", None)
                            )
                            said = {"guess": guess, "line": _line(words)}
                            if picked:
                                sticker, label = choices[picked - 1], offered[picked - 1]
                            break
                    if said is None and not error:
                        error = f"{live.name}没说清猜的是什么，再让她猜一次吧"
                elif not error:
                    error = "识图模型什么也没看出来，再画清楚一点？"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s 猜画时出错", bot.name)
            error = f"猜的时候出错了：{exc}"
        async with self._lock(game_id):
            game = await self.store.get(game_id)
            if game is None:
                return
            state = game["state"]
            guesses = [g for g in state.get("guesses", []) if g.get("n") != number or said is not None]
            status = game["status"]
            if said is not None:
                right = sketch.is_right(said["guess"], state["word"])
                entry = {**next(g for g in guesses if g.get("n") == number), **said, "right": right}
                if sticker is not None:
                    entry |= {"sticker_id": sticker["id"], "sticker_label": label}
                guesses = [entry if g.get("n") == number else g for g in guesses]
                tried = len([g for g in guesses if g.get("guess")])
                if status == "playing":
                    status = "won" if right else "lost" if tried >= sketch.TRIES else "playing"
            await self.store.save(
                game_id,
                [],
                status=status,
                state={**state, "guesses": guesses, "looking": False, "error": error},
            )
            await self._publish(game_id)
        if sticker is not None:
            await self.brain.stickers.store.used(sticker["id"], time.time())

    def _cancel_turn(self, game_id: int) -> None:
        task = self._turns.pop(game_id, None)
        if task is not None:
            task.cancel()
        self._thinking.discard(game_id)

    def _spawn(self, game_id: int, work: Any) -> None:
        task = asyncio.create_task(work)
        self._turns[game_id] = task
        task.add_done_callback(
            lambda _: self._turns.pop(game_id, None) if self._turns.get(game_id) is task else None
        )

    def _her_turn(self, bot: BotConfig, chat: BotChannel, game_id: int, *, moment: str | None) -> None:
        self._thinking.add(game_id)
        self._spawn(game_id, self._play(bot, chat, game_id, moment))

    async def _play(self, bot: BotConfig, chat: BotChannel, game_id: int, moment: str | None) -> None:
        again = False
        try:
            low, high = self.think
            await asyncio.sleep(low + (high - low) * self.chance())
            async with self._lock(game_id):
                game = await self.store.get(game_id)
                if game is None or game["status"] != "playing" or to_move(game["moves"]) != game["her_color"]:
                    return
                rules = RULES[game["kind"]]
                moves = list(game["moves"])
                spot = await asyncio.to_thread(rules.choose, moves, game["level"], self.chance)
                moves = rules.with_passes([*moves, spot])
                you_passed = moves[-1] == PASS and spot != PASS
                made = rules.moment(moves[: len(moves) - you_passed])
                ended = await self._settle(game, rules, moves)
                # You cannot move: she goes on.
                again = not ended and to_move(moves) == game["her_color"]
                if not again:
                    self._thinking.discard(game_id)
                await self._publish(game_id)
            if ended:
                self._talk(bot, chat, game_id, ended, final=True)
            elif moment == "start_her":
                self._talk(bot, chat, game_id, "start_her")
            elif you_passed:
                self._talk(bot, chat, game_id, "you_pass")
            elif moment is not None and self.chance() < REACT_CHANCE:
                self._talk(bot, chat, game_id, moment)
            elif made and made != "pass" and self.chance() < REACT_CHANCE / 2:
                self._talk(bot, chat, game_id, f"her_{made}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s 下棋时出错", bot.name)
            again = False
        finally:
            if not again:
                self._thinking.discard(game_id)
        if again:
            self._her_turn(bot, chat, game_id, moment=None)

    # -- 海龟汤 ----------------------------------------------------------------------------------------

    def _soup_system(self, bot: BotConfig, chat: BotChannel) -> str:
        return self.brain._persona(bot, chat) or f"你是{bot.name}。"

    @for_purpose("game")
    async def _cook(self, bot: BotConfig, chat: BotChannel, game_id: int) -> None:
        """She makes up the puzzle."""
        error = ""
        puzzle: dict[str, Any] | None = None
        try:
            game = await self.store.get(game_id)
            if game is None:
                return
            live = self.brain.bots.get(bot.id) or bot
            ask = soup.COOK.format(
                flavor=soup.FLAVORS[game["state"]["flavor"]], level=soup.LEVELS[game["level"]]
            )
            messages = [ChatMessage("system", self._soup_system(live, chat)), ChatMessage("user", ask)]
            try:
                completion, _, _ = await self.brain._complete(live, messages, max_tokens=soup.COOK_TOKENS)
                puzzle = soup.read_puzzle(completion.text)
                error = "" if puzzle else f"{live.name}出的题没写完整，再来一次吧"
            except (ProviderUnavailable, ProviderError) as exc:
                error = f"{live.name}没想出题目：{exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s 出海龟汤时出错", bot.name)
            error = f"出题时出错了：{exc}"
        async with self._lock(game_id):
            game = await self.store.get(game_id)
            if game is None or game["status"] != "playing":
                return
            state = {**game["state"], "preparing": False}
            if puzzle is None:
                await self.store.save(game_id, [], status="left", state={**state, "error": error})
            else:
                await self.store.save(game_id, [], state={**state, **puzzle})
            await self._publish(game_id)

    async def ask(
        self, bot: BotConfig, game_id: int, text: str, *, guess: bool = False
    ) -> tuple[dict[str, Any] | None, str]:
        """A yes-or-no question about the 海龟汤 (or, `guess`, what the user thinks the 汤底 is)."""
        text = " ".join(text.split())[: soup.QUESTION_MAX]
        if not text:
            return None, "问点什么吧"
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            if game["kind"] != "soup":
                return None, "这个游戏不用提问"
            state = game["state"]
            if state.get("preparing"):
                return None, f"{bot.name}还在想题目"
            questions = list(state.get("questions", []))
            if any(q.get("answer") is None and q.get("solved") is None for q in questions):
                return None, f"等{bot.name}答完上一个"
            if len(questions) >= soup.QUESTIONS_MOST:
                return None, "问得太多啦，揭晓汤底吧"
            questions.append({"q": text, "guess": guess, "answer": None, "solved": None, "at": self.clock()})
            await self.store.save(game_id, [], state={**state, "questions": questions})
            shown = await self._publish(game_id)
        chat = self._chat(bot, game)
        if chat is None:
            return None, f"{bot.name}的聊天不在了"
        self._spawn(game_id, self._judge(bot, chat, game_id, len(questions) - 1))
        return shown, ""

    @for_purpose("game")
    async def _judge(self, bot: BotConfig, chat: BotChannel, game_id: int, index: int) -> None:
        verdict: dict[str, Any] | None = None
        sticker: dict[str, Any] | None = None
        label = ""
        try:
            game = await self.store.get(game_id)
            if game is None:
                return
            state = game["state"]
            question = state["questions"][index]
            live = self.brain.bots.get(bot.id) or bot
            choices = await self._stickers(live, chat)
            offered = [self.brain.stickers.describe_choice(s) for s in choices] if choices else []
            listed = "\n".join(f"[表情{n}] {text}" for n, text in enumerate(offered, 1))
            guess = bool(question["guess"])
            ask = soup.JUDGE.format(
                title=state["title"],
                surface=state["surface"],
                bottom=state["bottom"],
                keys="；".join(state.get("keys") or []) or "（见汤底）",
                asked=soup.asked_lines(state["questions"][:index]),
                what="猜汤底" if guess else "问",
                question=question["q"],
                task=soup.GUESS_TASK if guess else soup.ASK_TASK,
                stickers=soup.STICKERS.format(listed=listed) if listed else "",
                shape=soup.GUESS_SHAPE if guess else soup.ASK_SHAPE,
            )
            messages = [ChatMessage("system", self._soup_system(live, chat)), ChatMessage("user", ask)]
            for _ in range(2):  # once more when what came back could not be read
                try:
                    completion, _, _ = await self.brain._complete(
                        live, messages, max_tokens=soup.JUDGE_TOKENS
                    )
                except (ProviderUnavailable, ProviderError) as exc:
                    log.info("%s 判海龟汤的问题时模型没回（%s）", live.name, exc)
                    break
                verdict = soup.read_judgement(completion.text, guess=guess)
                if verdict is not None:
                    break
            if verdict is not None:
                words, number = split_sticker(verdict["line"], offered)
                verdict["line"] = _line(words)
                if number:
                    sticker, label = choices[number - 1], offered[number - 1]
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s 判海龟汤的问题时出错", bot.name)
        async with self._lock(game_id):
            game = await self.store.get(game_id)
            if game is None:
                return
            state = game["state"]
            questions = list(state["questions"])
            if index >= len(questions):
                return
            if verdict is None:
                questions.pop(index)  # not answered: it can be asked again
                failed = f"{bot.name}没听清，再问一次吧"
                await self.store.save(game_id, [], state={**state, "questions": questions, "error": failed})
            else:
                entry = {**questions[index], **verdict}
                if sticker is not None:
                    entry |= {"sticker_id": sticker["id"], "sticker_label": label}
                questions[index] = entry
                solved = bool(verdict.get("solved"))
                await self.store.save(
                    game_id,
                    [],
                    status="won" if solved and game["status"] == "playing" else game["status"],
                    state={**state, "questions": questions, "error": ""},
                )
            await self._publish(game_id)
        if sticker is not None:
            await self.brain.stickers.store.used(sticker["id"], time.time())

    async def give_hint(self, bot: BotConfig, game_id: int) -> tuple[dict[str, Any] | None, str]:
        async with self._lock(game_id):
            game, why = await self._mine(bot, game_id)
            if game is None:
                return None, why
            state = game["state"]
            if game["kind"] == "draw" and state.get("mode") == "her":
                given = int(state.get("given", 0))
                if given >= 2:
                    return None, "提示都给完了"
                await self.store.save(game_id, [], state={**state, "given": given + 1})
                return await self._publish(game_id), ""
            if game["kind"] != "soup":
                return None, "这个游戏没有提示"
            given = int(state.get("given", 0))
            if given >= len(state.get("hints") or []):
                return None, "提示都给完了"
            await self.store.save(game_id, [], state={**state, "given": given + 1})
            return await self._publish(game_id), ""

    # -- what she says ---------------------------------------------------------------------------------

    def _talk(
        self, bot: BotConfig, chat: BotChannel, game_id: int, moment: str, *, final: bool = False, **what: Any
    ) -> None:
        now = self.clock()
        if (
            not final
            and moment not in ("start", "start_her")
            and now - self._said_at.get(bot.id, 0.0) < REACT_GAP
        ):
            return
        if not final and self.brain.busy(bot.id, chat.channel_id):
            return  # she is answering what the user typed: that is enough talk
        self._said_at[bot.id] = now
        task = asyncio.create_task(self._say(bot, chat, game_id, moment, what))
        self._talks.add(task)
        task.add_done_callback(self._talks.discard)

    @for_purpose("game")
    async def _say(
        self, bot: BotConfig, chat: BotChannel, game_id: int, moment: str, what: dict[str, Any]
    ) -> None:
        try:
            game = await self.store.get(game_id)
            if game is None or game["kind"] not in RULES:
                return
            rules = RULES[game["kind"]]
            live = self.brain.bots.get(bot.id) or bot
            choices = await self._stickers(live, chat)
            offered = [self.brain.stickers.describe_choice(s) for s in choices] if choices else []
            listed = "\n".join(f"[表情{n}] {text}" for n, text in enumerate(offered, 1))
            rows = await self.brain.store.recent(chat.channel_id, limit=6)
            moves, her = game["moves"], game["her_color"]
            ends = ENDS[game["kind"]]
            said = (
                ends[moment].format(score=score_line(rules.score(moves), her))
                if moment in ends
                else MOMENTS[moment].format(**what)
            )
            ask = ASK.format(
                game=KINDS[game["kind"]],
                color=COLOR_NAMES[her],
                level=LEVEL_NAMES[game["level"]],
                count=len([m for m in moves if m != PASS]),
                moment=said,
                situation=rules.situation(moves, her)
                if game["status"] == "playing"
                else RESULTS.get(game["status"], ""),
                record=record_line(await self.store.record(bot.id, game["kind"]), game["kind"]),
                now=clock_label(self.clock()),
                talk=self._talk_lines(live, rows),
                stickers=STICKERS.format(listed=listed) if listed else "",
            )
            messages = [
                ChatMessage("system", self.brain._persona(live, chat) or f"你是{live.name}。"),
                ChatMessage("user", ask),
            ]
            try:
                completion, _, _ = await self.brain._complete(live, messages, max_tokens=MAX_TOKENS)
            except (ProviderUnavailable, ProviderError) as exc:
                log.info("%s 下棋时想说句话，模型没回（%s）", live.name, exc)
                return
            if completion.cut:
                return
            words, number = split_sticker(completion.text, offered)
            line = _line(words)
            desk = self.brain.desk
            if desk is None:
                return
            if line:
                await desk.deliver(live, chat, [Segment("text", {"text": line})], kind=KIND)
            if number:
                sticker = choices[number - 1]
                await desk.deliver(
                    live, chat, [sticker_segment(sticker["id"], offered[number - 1])], kind=KIND
                )
                await self.brain.stickers.store.used(sticker["id"], time.time())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s 下棋时说话出错", bot.name)

    def _talk_lines(self, bot: BotConfig, rows: list[dict[str, Any]]) -> str:
        def show(row: dict[str, Any]) -> str:
            who = speaker(row, bot.name) if row["from_self"] else row["sender_name"]
            return f"{who}：{' '.join(str(row.get('text') or '').split())[:120]}"

        return "\n".join(show(r) for r in rows) or "（还没聊过）"

    async def _stickers(self, bot: BotConfig, chat: BotChannel) -> list[dict[str, Any]]:
        """The 表情包 she may send with a line: as in her answers in the desk chat (her own 套装 when it is
        on, not too often)."""
        stickers, packs = self.brain.stickers, self.brain.packs
        if stickers is None or packs is None or not await packs.desk_on(bot.id):
            return []
        if await stickers.store.sent_lately(chat.channel_id, bot.id, STICKER_EVERY):
            return []
        own = await packs.own(bot.id)
        choices, _ = await stickers.candidates(
            chat.channel,
            bot.id,
            "",
            exclude=set(),
            only=own if await packs.own_only(bot.id) else None,
            prefer=own,
        )
        return choices[:8]

    # -- her answers in the chat -----------------------------------------------------------------------

    async def hint(self, bot_id: str) -> str:
        """How the game with her stands, for her answers in the chat; '' when there is none to speak of."""
        game = await self.store.current(bot_id)
        if game is None:
            return ""
        now = self.clock()
        kind = game["kind"]
        playing = game["status"] == "playing"
        fresh = playing and now - game["updated_at"] < HINT_FRESH
        after = not playing and now - (game["finished_at"] or 0) < AFTER_FRESH
        if kind == "draw" and game["state"].get("mode") == "her":
            state = game["state"]
            if fresh:
                return sketch.HER_HINT.format(word=state["word"], count=len(state.get("guesses", [])))
            if after:
                return sketch.HINT_AFTER.format(
                    word=state["word"], result=HER_RESULTS.get(game["status"], "结束了")
                )
            return ""
        if kind == "draw":
            state = game["state"]
            if fresh:
                return sketch.HINT.format(count=len([g for g in state.get("guesses", []) if g.get("guess")]))
            if after:
                return sketch.HINT_AFTER.format(
                    word=state.get("word", ""), result=DRAW_RESULTS.get(game["status"], "结束了")
                )
            return ""
        if kind == "soup":
            state = game["state"]
            if not state.get("bottom"):
                return ""
            if fresh:
                return soup.HINT.format(
                    title=state["title"],
                    surface=state["surface"],
                    bottom=state["bottom"],
                    count=len(state.get("questions", [])),
                )
            if after:
                return soup.HINT_AFTER.format(
                    title=state["title"],
                    result=SOUP_RESULTS.get(game["status"], "结束了"),
                    bottom=state["bottom"],
                )
            return ""
        rules = RULES.get(kind)
        if rules is None:
            return ""
        if fresh:
            return HINT.format(
                game=KINDS[kind],
                color=COLOR_NAMES[game["her_color"]],
                level=LEVEL_NAMES[game["level"]],
                count=len([m for m in game["moves"] if m != PASS]),
                situation=rules.situation(game["moves"], game["her_color"]),
            )
        if after:
            return HINT_AFTER.format(
                game=KINDS[kind],
                result=RESULTS.get(game["status"], "下完了"),
                record=record_line(await self.store.record(bot_id, kind), kind),
            )
        return ""
