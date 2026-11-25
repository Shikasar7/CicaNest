"""逐字流式: an answer in the desk chat, shown while the model is still writing it.

What is shown on the way is only a preview: the answer itself goes out as always when it is complete
(checked, cleaned, stored), and the chat window then puts it in the preview's place. So nothing here decides
anything; it only keeps out of sight what would not be in the answer: reasoning, marks (a sticker's number,
语音), a SKIP. The first HOLD characters wait: a short answer comes in one go, and a reply that turns out to
be something else (「输出：」 after a draft in the open, SKIP) is not seen starting.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from nest.core.groups import GROUP_MARK
from nest.core.prompt import (
    DRAFT_MARK,
    STICKER_MARK,
    STICKER_WRITTEN,
    TAG,
    UNSURE,
    VOICE_MARK,
    after_drafting,
    clean_answer,
)
from nest.llm.base import without_thought

HOLD = 40  # characters before anything is shown
EVERY = 0.12  # seconds between two looks at it
MOST = 2000
MARKS = (VOICE_MARK, STICKER_MARK, STICKER_WRITTEN, GROUP_MARK)
OPEN_END = re.compile(r"[\[【<][^\]】>\n]{0,160}$")  # a mark (or a tag) still being written


def visible(raw: str, *, answer: bool) -> str:
    """What of an answer so far may be shown ("": nothing yet)."""
    text = without_thought(raw)
    text, drafted = after_drafting(text)
    if not drafted and any(DRAFT_MARK.match(line) for line in text.splitlines()):
        return ""
    for mark in MARKS:
        text = mark.sub("", text)
    text = TAG.sub("", OPEN_END.sub("", text).replace(UNSURE, "")).strip()
    if len(text) < HOLD or text.upper().startswith("SKIP"):
        return ""
    if answer:
        return clean_answer(text, max_chars=MOST)[0]
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())[:MOST]


class DeskStream:
    """One answer on its way. `feed` takes what the model writes (None: what it wrote so far is not the
    answer after all — a tool was called, or the call is made again); `show` gets the text so far, at most
    every EVERY seconds ("": nothing is shown any more)."""

    def __init__(self, show: Callable[[str], None], *, answer: bool) -> None:
        self._show = show
        self._answer = answer
        self._raw = ""
        self._at = 0.0
        self.shown = ""

    def feed(self, piece: str | None) -> None:
        if piece is None:
            self._raw = ""
            self.clear()
            return
        self._raw += piece
        if time.monotonic() - self._at < EVERY:
            return
        self._at = time.monotonic()
        text = visible(self._raw, answer=self._answer)
        if text != self.shown:
            self.shown = text
            self._show(text)

    def clear(self) -> None:
        if self.shown:
            self.shown = ""
            self._show("")
