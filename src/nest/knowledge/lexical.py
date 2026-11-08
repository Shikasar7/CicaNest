"""Keyword search over help sections, and exact matching of error messages.

BM25 with field weights (a simple BM25F): a section's title and what players also call it count most,
where it is in the app next, the body least. Chinese has no spaces, so Chinese text is cut into pairs of
neighbouring characters; Latin text into words. An error the player pastes or screenshots is matched
against the sections' 原文 / 英文 lines by shared runs of characters, which survives a filled-in {0} and
a cut-off message.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

from nest.knowledge.loader import Section

RUN = re.compile(r"[㐀-鿿豈-﫿]+|[a-z0-9]+")
CJK = re.compile(r"[㐀-鿿豈-﫿]")
# How much a term counts, by where in the section it stands.
WEIGHTS = {"title": 3.0, "也叫": 3.0, "在哪": 2.0, "原文": 2.0, "英文": 2.0, "body": 1.0}
K1 = 1.2
B = 0.75
# Runs of this many characters (after dropping spaces and punctuation) that a question shares with an
# error's own text, at least MIN_RUNS of them, make it a match. Shorter runs, or a single one, matched
# ordinary words in questions that quoted no error.
SHINGLE = 8
MIN_RUNS = 2
PLACEHOLDER = re.compile(r"\{\d+\}")
NOT_TEXT = re.compile(r"[^\w㐀-鿿]+")


def tokens(text: str) -> list[str]:
    """Words of `text` for searching: neighbouring character pairs in Chinese, whole words otherwise."""
    out: list[str] = []
    for run in RUN.findall(text.lower()):
        if CJK.match(run):
            out += [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


def _fields(section: Section) -> dict[str, str]:
    rest = [v for name in ("平台", "版本", "出现在", "原因", "解决") for v in section.values(name)]
    return {
        "title": section.title,
        "也叫": " ".join(section.values("也叫")),
        "在哪": " ".join(section.values("在哪")),
        "原文": " ".join(section.values("原文")),
        "英文": " ".join(section.values("英文")),
        "body": "\n".join([*rest, section.body]),
    }


class LexicalIndex:
    """BM25 over a fixed set of sections. Built once (milliseconds for a few hundred sections)."""

    def __init__(self, sections: Iterable[Section]) -> None:
        self.sections = list(sections)
        self._tf: list[Counter[str]] = []
        self._len: list[float] = []
        df: Counter[str] = Counter()
        for section in self.sections:
            tf: Counter[str] = Counter()
            for name, text in _fields(section).items():
                for term in tokens(text):
                    tf[term] += WEIGHTS[name]
            self._tf.append(tf)
            self._len.append(sum(tf.values()))
            df.update(tf.keys())
        n = len(self.sections)
        self._avg = (sum(self._len) / n) if n else 1.0
        self._idf = {term: math.log(1 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()}

    def search(self, query: str, k: int = 30) -> list[tuple[Section, float]]:
        """The `k` best sections for `query`, best first, with their BM25 scores (0 never comes back)."""
        terms = Counter(tokens(query))
        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self._tf):
            norm = K1 * (1 - B + B * self._len[i] / self._avg)
            score = 0.0
            for term, times in terms.items():
                if (f := tf.get(term)) is not None:
                    score += times * self._idf[term] * f * (K1 + 1) / (f + norm)
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [(self.sections[i], score) for score, i in scored[:k]]


def _squash(text: str) -> str:
    return NOT_TEXT.sub("", PLACEHOLDER.sub(" ", text).lower())


def _shingles(text: str) -> set[str]:
    return {text[i : i + SHINGLE] for i in range(len(text) - SHINGLE + 1)}


class ErrorIndex:
    """The 原文 / 英文 lines of error sections, for finding the section of an error the player pasted."""

    def __init__(self, sections: Iterable[Section]) -> None:
        self._owner: dict[str, set[int]] = {}
        self.sections = [s for s in sections if s.values("原文") or s.values("英文")]
        for i, section in enumerate(self.sections):
            for line in (*section.values("原文"), *section.values("英文")):
                for part in PLACEHOLDER.split(line):
                    for shingle in _shingles(_squash(part)):
                        self._owner.setdefault(shingle, set()).add(i)

    def search(self, query: str) -> list[tuple[Section, int]]:
        """Sections whose error text shares at least MIN_RUNS runs with `query`, most shared runs first."""
        hits: Counter[int] = Counter()
        for shingle in _shingles(_squash(query)):
            for i in self._owner.get(shingle, ()):
                hits[i] += 1
        return [(self.sections[i], n) for i, n in hits.most_common() if n >= MIN_RUNS]


def rrf(rankings: Iterable[list[Section]], k: int = 60) -> list[tuple[Section, float]]:
    """Reciprocal rank fusion: each list gives its r-th section 1 / (k + r); sections are ranked by the
    sum. Only ranks count, so lists scored on different scales combine without tuning."""
    score: dict[str, float] = {}
    by_id: dict[str, Section] = {}
    for ranking in rankings:
        for rank, section in enumerate(ranking, start=1):
            score[section.id] = score.get(section.id, 0.0) + 1 / (k + rank)
            by_id[section.id] = section
    return sorted(((by_id[i], s) for i, s in score.items()), key=lambda pair: -pair[1])
