"""你画我猜 (小游戏): one draws a word, the other guesses it — the user on a sketch pad in the app and she
guesses (mode "you"), or she draws and the user guesses (mode "her").

nest picks the word (or the user writes their own); she never sees it until the game is over. Each time the
user hands the drawing over, the vision model (the one 表情包 uses) says what is on the paper, without
guessing, and her own model — in her own voice — makes one guess from that. Three guesses per drawing; the
user may keep drawing between them. A guess counts when it names the word (or one of its other names).

她画你猜: nest picks the word; her picture comes from the drawing model (nest.core.drawing: about a minute,
out of her pictures for the day) and the word is not shown in the app until the game is over. The user has
five guesses and two hints (how many characters, the first one); at the end she says a word of her own.

The pure parts are here (the words, what she is asked, telling a right guess); nest.core.games plays it.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any

TRIES = 3
HER_TRIES = 5  # 她画你猜: the user's guesses
SKETCH_MAX = 2 * 1024 * 1024  # bytes of one drawing (PNG)
WORD_MAX = 12
LOOK_TOKENS = 300
GUESS_TOKENS = 300
LINE_MAX = 60

# word: other names that count too
WORDS: dict[str, dict[str, list[str]]] = {
    "easy": {
        "猫": ["猫咪", "小猫"], "太阳": [], "月亮": [], "苹果": [], "西瓜": [], "雨伞": ["伞"],
        "房子": ["屋子", "小房子"], "花": ["花朵", "小花"], "鱼": ["小鱼"], "星星": ["星"], "树": ["大树"],
        "眼镜": [], "气球": [], "爱心": ["心", "心形"], "雪人": [], "蘑菇": [], "钥匙": [], "杯子": ["水杯"],
        "蝴蝶结": [], "彩虹": [],
    },
    "normal": {
        "兔子": ["小兔", "兔"], "长颈鹿": [], "冰淇淋": ["雪糕", "甜筒"], "蛋糕": [], "自行车": ["单车"],
        "吉他": [], "灯泡": [], "蜗牛": [], "企鹅": [], "汉堡": ["汉堡包"], "蜡烛": [], "风筝": [],
        "飞机": [], "城堡": [], "鲸鱼": ["鲸"], "热气球": [], "向日葵": [], "闹钟": ["钟"], "章鱼": [],
        "猫爪": ["爪子", "猫掌"],
    },
    "hard": {
        "下雨": ["雨", "下雨天"], "睡觉": ["睡着"], "放风筝": [], "钓鱼": [], "过生日": ["生日"],
        "刷牙": [], "打伞": [], "跳绳": [], "晒太阳": [], "看书": ["读书"], "堆雪人": [], "吹泡泡": ["泡泡"],
        "失眠": [], "下班": [], "摸鱼": [], "追星": [], "做梦": ["梦"], "迟到": [], "流星": [], "火锅": [],
    },
}  # fmt: skip
LEVEL_NAMES = {"easy": "简单", "normal": "普通", "hard": "有点难"}

LOOK = (
    "这是一幅手绘的简笔画，是有人在玩你画我猜时画的。客观地描述画面上画了什么：形状、颜色、位置、"
    "像是什么东西或动作，可以说出最多三个它可能是的东西。只描述，不超过 80 个字。"
)
GUESS = """你在和对方玩你画我猜：对方在画，你来猜。
你看着对方的画，画上是：{seen}
{before}这是你第 {n} 次猜（一共能猜 {tries} 次）。

猜一个具体的词（一个东西或一个动作，越短越好），再用你自己的语气说一句很短的话（不超过 20 个字），
像边看边嘀咕。不要说别的。{stickers}
只输出一个 JSON 对象：{{"guess": "你猜的词", "line": "你说的话"}}"""
STICKERS = "\n这句话最后可以带一个表情（写它的编号，比如 [表情1]）：\n{listed}"
PAINT = (
    "画一幅「你画我猜」游戏里用的简笔画，题目是「{word}」。像用马克笔随手画的可爱简笔画：白色背景，线条简单，"
    "少量颜色，一眼能认出是什么但不要太写实。画面里绝对不要出现任何文字、字母或数字。"
)
END = """你在和对方玩你画我猜：你画了一幅画让对方猜，答案是「{word}」。
对方猜了：{tried}
结果：{result}
用你自己的语气说一句很短的话（不超过 25 个字）。{stickers}
只输出你要说的话，最后可以带一个表情编号。"""
HER_HINT = (
    "（你们正在桌面上玩你画我猜：你画了一幅「{word}」让对方猜，答案只有你知道，聊天里别说出来，"
    "也别直接给提示词以外的提示。对方已经猜了 {count} 次。）"
)
HINT = (
    "（你们正在桌面上玩你画我猜：对方在画，你来猜，你还不知道答案。这一幅你猜了 {count} 次。"
    "聊天里别乱编答案，对方画完会交给你猜。）"
)
HINT_AFTER = "（你们刚在桌面上玩完一局你画我猜，答案是「{word}」：{result}。）"

_SPACE = re.compile(r"[\s，。！？、,.!?~～「」『』\"'“”‘’()（）\[\]【】:：;；]+")


def pick(level: str, chance: Callable[[], float] = random.random, *, not_: str = "") -> str:
    words = [w for w in WORDS.get(level, WORDS["easy"]) if w != not_]
    return words[int(chance() * len(words)) % len(words)]


def names_of(word: str) -> list[str]:
    for level in WORDS.values():
        if word in level:
            return [word, *level[word]]
    return [word]


def _plain(text: str) -> str:
    return _SPACE.sub("", (text or "").lower())


def is_right(guess: str, word: str) -> bool:
    """Whether her guess names the word: one of its names, or a guess that holds one (「一只猫」 for 猫);
    a short guess held in a longer word counts when it is at least half of it (「西瓜」 for 「大西瓜」)."""
    said = _plain(guess)
    if not said:
        return False
    for name in names_of(word):
        plain = _plain(name)
        if not plain:
            continue
        if (
            said == plain
            or plain in said
            or (len(said) >= 2 and said in plain and len(said) * 2 >= len(plain))
        ):
            return True
    return False


def before_lines(guesses: list[dict[str, Any]]) -> str:
    wrong = [g["guess"] for g in guesses if g.get("guess") and not g.get("right")]
    return f"你之前猜过（都不对）：{'、'.join(wrong)}\n" if wrong else ""


def her_hints(word: str, given: int) -> list[str]:
    """The hints of 她画你猜 given so far."""
    hints = [f"{len(word)} 个字", f"第一个字是「{word[:1]}」"]
    return hints[:given]


def shown_state(state: dict[str, Any], *, ended: bool) -> dict[str, Any]:
    """What the app sees. 你画她猜: the word is the user's to draw, so the app shows it (her prompts never
    hold it). 她画你猜: not until the game is over."""
    guesses = state.get("guesses", [])
    mode = state.get("mode", "you")
    tries = HER_TRIES if mode == "her" else TRIES
    word = state.get("word", "")
    return {
        "mode": mode,
        "word": word if mode == "you" or ended else "",
        "own": bool(state.get("own")),
        "guesses": guesses,
        "tries_left": max(0, tries - len([g for g in guesses if g.get("guess")])),
        "looking": bool(state.get("looking")),
        "painting": bool(state.get("painting")),
        "painted": bool(state.get("painted")),
        "hints": her_hints(word, int(state.get("given", 0))) if mode == "her" else [],
        "hints_left": max(0, 2 - int(state.get("given", 0))) if mode == "her" else 0,
        "line": state.get("line", ""),
        "line_sticker": state.get("line_sticker"),
        "error": state.get("error", ""),
    }
