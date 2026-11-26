"""群里的小游戏: little text games a companion plays with a group of a platform (QQ…) in the chat
itself — 猜数字, 海龟汤, 成语接龙. She invites the group (冷场时主动开口, nest.core.openers) or someone there
asks her to play one.

What a game has to keep is kept by nest, not by her model, so she neither forgets it nor cheats: the number of
猜数字 (each guess is told to her as 大了 / 小了 / 猜中了, worked out here), and the 汤面 and 汤底 of 海龟汤
(written by her model when the game starts, nest.core.soup). While a game is on, every message of a person in
the group counts as calling her (she is the one running it), and her reply prompt carries the rules and what
she has to know. She ends it by adding END_MARK to her reply (someone got it, everyone gave up); it is taken
out before the reply goes out. A game nobody took up, or nobody played for a while, is dropped.

One game per group (per channel of her account); the games are kept in the settings (SETTING), so a restart
does not lose one.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

from nest.core import soup
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.runtime import ProviderUnavailable
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

SETTING = "group_games"
KINDS = {"number": "猜数字", "soup": "海龟汤", "idiom": "成语接龙"}
INVITED_FOR = 30 * 60.0  # an invitation nobody answered is dropped after this
IDLE_FOR = 30 * 60.0  # a game nobody played for this long is over
END_MARK = "[游戏结束]"
LOWEST, HIGHEST = 1, 100
GUESS = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
RULES = {
    "number": (
        f"猜数字：你心里想了一个 {LOWEST} 到 {HIGHEST} 之间的整数，大家来猜，你只说大了还是小了，猜中为止。"
    ),
    "soup": (
        "海龟汤：你出一道题（汤面），大家问只能用「是」「不是」「无关」回答的问题来猜背后的真相（汤底），"
        "说中关键就算猜中。"
    ),
    "idiom": "成语接龙：下一个成语的第一个字要接上一个成语的最后一个字（同音也算），接不上就算输。",
}


def _human(row: dict[str, Any], ours: Iterable[str]) -> bool:
    return not row.get("from_self") and row.get("sender_id") not in set(ours)


class GroupGames:
    def __init__(
        self,
        db: Database,
        brain: Any,
        *,
        clock: Callable[[], float] = time.time,
        chance: Callable[[], float] = random.random,
    ) -> None:
        self.db = db
        self.brain = brain
        self.clock = clock
        self.chance = chance
        self._games: dict[str, dict[str, Any]] = {}  # channel id (as text) -> the game

    async def load(self) -> None:
        stored = (await SettingsRepo(self.db).all()).get(SETTING)
        self._games = {k: v for k, v in (stored or {}).items() if isinstance(v, dict)}

    async def _save(self) -> None:
        await SettingsRepo(self.db).set_many({SETTING: self._games})

    def active(self, bot_id: str, channel_id: int) -> dict[str, Any] | None:
        """Her game in this group, while it lasts."""
        game = self._games.get(str(channel_id))
        if game is None or game.get("bot_id") != bot_id:
            return None
        limit = INVITED_FOR if game.get("status") == "invited" else IDLE_FOR
        return game if self.clock() - float(game.get("last_at") or 0) < limit else None

    async def invite(self, bot: Any, channel: Any, kind: str) -> dict[str, Any] | None:
        """A game she invites the group to (or is asked for: `status` "on" at once by `start`). None when it
        could not be set up (海龟汤: her model wrote no puzzle)."""
        now = self.clock()
        game: dict[str, Any] = {
            "kind": kind,
            "bot_id": bot.id,
            "status": "invited",
            "at": now,
            "last_at": now,
        }
        if kind == "number":
            game["number"] = LOWEST + int(self.chance() * (HIGHEST - LOWEST + 1)) % (HIGHEST - LOWEST + 1)
        elif kind == "soup":
            puzzle = await self._cook(bot, channel)
            if puzzle is None:
                return None
            game["puzzle"] = puzzle
        elif kind != "idiom":
            raise ValueError(f"没有这种小游戏：{kind}")
        self._games[str(channel.channel_id)] = game
        await self._save()
        log.info("%s 在群里开了一局%s", bot.name, KINDS[kind])
        return game

    async def _cook(self, bot: Any, channel: Any) -> dict[str, Any] | None:
        flavor = "clear" if self.chance() < 0.5 else "funny"
        ask = soup.COOK.format(flavor=soup.FLAVORS[flavor], level=soup.LEVELS["easy"])
        system = self.brain._persona(bot, channel)
        try:
            completion, _, _ = await self.brain._complete(
                bot, [ChatMessage("system", system), ChatMessage("user", ask)], max_tokens=soup.COOK_TOKENS
            )
        except (ProviderUnavailable, ProviderError) as exc:
            log.info("%s 没想出海龟汤：%s", bot.name, exc)
            return None
        return soup.read_puzzle(completion.text)

    async def hint(self, bot: Any, channel: Any, rows: list[dict[str, Any]], called: set[int]) -> str:
        """What her reply prompt is told about the game in this group, if there is one (or someone calling her
        just asked for one: it starts). `rows`: the chat as she sees it, the new messages last."""
        ours = self.brain.our_accounts
        game = self.active(bot.id, channel.channel_id)
        fresh = [r for r in rows[-8:] if _human(r, ours)]
        if game is None:
            asked = next(
                (
                    kind
                    for r in reversed(fresh)
                    if r.get("id") in called
                    for kind, name in KINDS.items()
                    if name in str(r.get("text") or "")
                ),
                None,
            )
            if asked is None:
                return ""
            game = await self.invite(bot, channel, asked)
            if game is None:
                return ""
            game["status"] = "on"
        since = float(game.get("seen_at") or game["at"])
        new = [r for r in fresh if float(r.get("sent_at") or 0) > since]
        if new:
            game["status"] = "on"
            game["last_at"] = self.clock()
            game["seen_at"] = max(float(r.get("sent_at") or 0) for r in new)
        # What was said since the game started, as far as she sees: a reply asked again sees the same guesses.
        lines = self._lines(game, [r for r in fresh if float(r.get("sent_at") or 0) > float(game["at"])])
        await self._save()
        return lines

    def _lines(self, game: dict[str, Any], new: list[dict[str, Any]]) -> str:
        kind = game["kind"]
        name = KINDS[kind]
        head = (
            f"（群里小游戏：你刚邀请大家玩{name}，{RULES[kind]}有人答应就开始；没人理就算了，别一直催。"
            if game["status"] == "invited"
            else f"（群里小游戏：你在主持{name}。{RULES[kind]}"
        )
        parts = [head]
        if kind == "number":
            number = int(game["number"])
            parts.append(f"你心里的数是 {number}，猜中之前绝对不能说出来，也不能暗示得太明显。")
            for row in new:
                for guess in (int(g) for g in GUESS.findall(str(row.get("text") or ""))):
                    if not LOWEST <= guess <= HIGHEST:
                        continue
                    if guess == number:
                        game["won"] = row.get("sender_name") or "有人"
                        parts.append(f"{game['won']} 猜了 {guess}：猜中了！")
                    else:
                        way = "大了" if guess > number else "小了"
                        parts.append(f"{row.get('sender_name') or '有人'} 猜了 {guess}：{way}")
            if game.get("won"):
                parts.append(f"{game['won']} 猜中了，宣布答案、夸一下（或者傲娇地不服），这局就结束了。")
        elif kind == "soup":
            puzzle = game["puzzle"]
            parts.append(
                f"题目「{puzzle['title']}」。汤面（大家看得到，邀请或者有人问题目时要发出来）：{puzzle['surface']}"
            )
            parts.append(f"汤底（只有你知道，猜中之前绝对不能说出来或者暗示太多）：{puzzle['bottom']}")
            if puzzle.get("keys"):
                parts.append("猜中必须说到的关键点：" + "；".join(puzzle["keys"]))
            parts.append(
                "有人问是不是的问题，就照汤底回答「是」「不是」或「无关」，可以加一句你的语气；"
                "有人说中了关键就宣布猜中并公布汤底；大家卡住了可以给一点提示；大家说不玩了就公布汤底。"
            )
        else:
            parts.append("你也要接，接得上就接一个，接不上就认输；大家说不玩了就结束。")
        parts.append(f"这局结束的时候（有人猜中、有人认输、大家不玩了），在你回复的最后加上 {END_MARK}。）")
        return "\n".join(parts)

    def told(self, game: dict[str, Any]) -> str:
        """What she is told of a game she is about to invite the group to."""
        return self._lines(game, [])

    async def finish(self, bot_id: str, channel_id: int, reply: str) -> str:
        """Her reply with END_MARK taken out; the game is over when it was there, or someone guessed the
        number."""
        game = self.active(bot_id, channel_id)
        ended = END_MARK in reply
        if game is not None and (ended or game.get("won")):
            self._games.pop(str(channel_id), None)
            await self._save()
            log.info("群里的%s结束了", KINDS.get(game["kind"], "小游戏"))
        return reply.replace(END_MARK, "").strip() if ended else reply

    async def drop(self, channel_id: int) -> None:
        """An invitation she did not send after all."""
        if self._games.pop(str(channel_id), None) is not None:
            await self._save()
