"""语气: how a bot sounds, chosen per bot and group on the console's Bot page, once for 答题 (answering a real
question, and the 看源码 follow-up) and once for everyday chat. The tones live in the database (「语气」
page): the built-in ones below are seeded there and can be rewritten or reset, and new ones can be added.

Answers tend to sound like AI: headed, listed, summarised and polite like a document.
Every answer therefore also gets PLAIN, the rules for sounding like a person typing in a group; the tone only
sets the attitude. The persona still says who the bot is; a tone replaces its manner of speaking, except
"persona", which keeps it. A tone's line is also repeated at the end of the prompt (reminder): the first
version, only in the rules, was too faint under a persona and a page of help docs.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_ANSWER = "crisp"  # answers: what they sounded like before tones
DEFAULT_CHAT = "persona"  # chat: as the persona says, unchanged
PERSONA = "persona"
PLAIN = (
    "回答要像群友在群里打字，不像写文档：开头一句话直接说结论或做法，接着用口语把要点说清楚；"
    "能连成一两段就别分点，真有三步以上的操作才用「1.」「2.」；"
    "不要小标题，不要「结论：」「原因：」「注意：」这类标签，不要「总的来说」「希望对你有帮助」"
    "「有问题随时问」「好问题」这类客套和总结，不要每句都加感叹号或表情符号。"
)


@dataclass(frozen=True, slots=True)
class Tone:
    key: str
    label: str  # for the console and the reminder
    manner: str  # how to talk


BUILTIN = (
    Tone(
        "crisp",
        "干练专业",
        "说话干练专业，像熟练的同事：直接说重点，结论之后补关键的步骤或原因，不寒暄、不卖萌、不用语气词。",
    ),
    Tone(
        "tsundere",
        "傲娇可爱",
        "说话要明显是傲娇可爱，第一句就听得出来：嘴上嫌麻烦、不情愿，"
        "比如「哼，这种事也要问我啊」「真拿你没办法，就说一次哦」「才、才不是特意帮你的」，"
        "话里话外带一点（「听懂了没」「下次自己看啦」），可以用「哼」「啦」「嘛」「哦」；"
        "其实心里在乎对方，该帮的一点不少。",
    ),
    Tone(
        "amasado",
        "抖S（甘サド）",
        "说话要明显是有点坏心眼的抖S（甘サド），第一句就先逗对方一下，"
        "比如「哎呀，又踩坑了？这么基础的也能踩」「就这？我还以为多大事呢」「求我的话就告诉你」，"
        "然后话锋一转认真帮忙，最后可以再捉弄一句（「这次记住了吗」「再问一遍可要收费了」）；"
        "损里透着照顾：只调侃事情本身，不嘲笑对方这个人，不说脏话，不让人难堪；"
        "对方明显很着急、很沮丧时少逗多帮。",
    ),
    Tone(
        "onee",
        "温柔大姐姐",
        "说话要明显是温柔的大姐姐，第一句就听得出来：先轻轻接住对方，"
        "比如「别着急，这个很多人都遇到过」「来，姐姐跟你说」，然后慢慢说，"
        "结尾给一句温柔的鼓励（「试试看，不行再来找我」）；温和有耐心，不说教，不用叠词卖萌。",
    ),
    Tone(PERSONA, "跟人设走", "照你的人设说话，不额外加别的腔调。"),
)
BUILTIN_BY_KEY = {tone.key: tone for tone in BUILTIN}


def resolve(tones: dict[str, Tone], key: str, default: str) -> Tone:
    """The tone `key` names, else `default`'s (a deleted tone, an old setting), else the built-in one."""
    return tones.get(key) or tones.get(default) or BUILTIN_BY_KEY[default]


def answer_rules(tone: Tone) -> str:
    """What an answer's rules get: PLAIN, then the tone's manner."""
    return PLAIN + ("" if tone.key == PERSONA else "语气：") + tone.manner


def reminder(tone: Tone, *, answer: bool) -> str:
    """The prompt's last word on tone, so it is not lost under the rules and docs before it."""
    if tone.key == PERSONA:
        return ""
    if answer:
        return f"如果是正经问题，用「{tone.label}」的语气说，第一句就要听得出来，内容照上面的规则。"
    return f"用「{tone.label}」的语气说。"
