"""One check of a 监控: read the source, compare it with what was remembered, say what changed.

Every kind starts with a baseline: the first check (and the first after the address, type or selector
changed) only remembers what is there and pushes nothing. The message built here is the plain template;
the watcher may have a model rewrite it.
"""

from __future__ import annotations

import calendar
import difflib
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import feedparser
from selectolax.lexbor import LexborHTMLParser, LexborNode, SelectolaxError

from nest.storage.watches import Watch
from nest.watch.fetch import Page, WatchError

FEED_ENTRIES = 20  # newest entries looked at per check
SEEN_LIMIT = 500  # entry ids remembered
SUMMARY_CHARS = 200
TITLE_CHARS = 100
LISTED = 5  # new entries listed in a message
EXCERPT_CHARS = 300
CONTENT_CHARS = 6000  # of one new entry's full text, given to the model
MATERIAL_CHARS = 12_000  # of all new entries' full texts together
TEXT_KEPT = 20_000  # previous text kept for the diff of the next change
DIFF_LINES = 6
DIFF_LINE_CHARS = 100
INVISIBLE = ["script", "style", "noscript", "template"]
BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "option",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    }
)
CELLS = frozenset(("td", "th"))
SPACES = re.compile(r"\s+")
# The first number: 1,299.00 / 1299 / 12.5 (after NFKC, so ￥１２９９ reads as ¥1299).
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", re.ASCII)


@dataclass(frozen=True)
class Check:
    outcome: str  # "baseline" | "unchanged" | "changed" | "error"
    summary: str  # first line of the run's detail
    changes: int = 0
    lines: tuple[str, ...] = ()  # more detail (a page's diff)
    message: str | None = None  # the template text to push; None: nothing to push
    material: str | None = None  # what the model reads instead of the template (with full texts)
    state: dict[str, Any] | None = None  # None keeps the stored state
    error: str | None = None


def failed(error: str) -> Check:
    return Check("error", error, error=error)


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:20]


# -- feeds ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    key: str  # hashed id (or link, or title + published)
    title: str
    link: str
    summary: str
    published: str
    content: str = ""  # the full entry (HTML) when the feed carries it, else ""


def _html_text(html: str) -> str:
    if not html:
        return ""
    if "<" not in html:
        return html
    parser = LexborHTMLParser(html)
    parser.strip_tags(INVISIBLE)
    root = parser.body or parser.root
    return root.text(separator=" ") if root is not None else ""


def parse_feed(page: Page) -> list[Entry]:
    """The newest entries of an RSS/Atom feed, newest first."""
    # A stream, never a string: given a string, feedparser would fetch it if it looks like an address.
    parsed = feedparser.parse(
        io.BytesIO(page.body),
        response_headers={
            "content-type": page.content_type or "application/xml",
            "content-location": page.url,
        },
    )
    if not parsed.get("version") and not parsed.entries:
        raise WatchError("这个地址不像是订阅（RSS 或 Atom），请检查地址")
    items = list(parsed.entries)
    stamps = [item.get("published_parsed") or item.get("updated_parsed") for item in items]
    if items and all(stamps):  # dated entries: newest first, whatever order the feed lists them in
        order = sorted(range(len(items)), key=lambda i: calendar.timegm(stamps[i]), reverse=True)
        items = [items[i] for i in order]
    entries = []
    for item in items[:FEED_ENTRIES]:
        title = _one_line(_html_text(str(item.get("title") or "")), TITLE_CHARS)
        link = str(item.get("link") or "").strip()
        published = str(item.get("published") or item.get("updated") or "")
        content = item.get("content") or []
        identity = str(item.get("id") or "").strip() or link or f"{title}|{published}"
        entries.append(
            Entry(
                key=_key(identity),
                title=title or "（无标题）",
                link=link,
                summary=_one_line(_html_text(str(item.get("summary") or "")), SUMMARY_CHARS),
                published=published,
                content=str(content[0].get("value") or "") if content else "",
            )
        )
    return entries


def feed_message(name: str, entries: list[Entry]) -> str:
    lines = [f"「{name}」有 {len(entries)} 条新内容："]
    lines += [f"• {e.title} {e.link}".rstrip() for e in entries[:LISTED]]
    if len(entries) > LISTED:
        lines.append(f"还有 {len(entries) - LISTED} 条")
    return "\n".join(lines)


def feed_material(message: str, entries: list[Entry]) -> str | None:
    """The template plus the full text of the new entries that carry one, for the model; None: none do."""
    texts, budget = [], MATERIAL_CHARS
    for entry in entries[:LISTED]:
        if not entry.content or budget <= 0:
            continue
        body = "\n".join(visible_lines(entry.content, "")) if "<" in entry.content else entry.content.strip()
        body = body[: min(CONTENT_CHARS, budget)]
        budget -= len(body)
        texts.append(f"【{entry.title}】\n{body}")
    if not texts:
        return None
    return "\n\n".join([message, "新内容的正文，供你概括：", *texts])


def check_feed(watch: Watch, page: Page) -> Check:
    entries = parse_feed(page)
    keys = list(dict.fromkeys(e.key for e in entries))
    seen = watch.state.get("seen")
    if not isinstance(seen, list):
        return Check(
            "baseline",
            f"第一次检查：记下了现在的 {len(keys)} 条内容，以后有新的才推送",
            state={"seen": keys[:SEEN_LIMIT]},
        )
    known = set(seen)
    fresh = list({e.key: e for e in entries if e.key not in known}.values())
    state = {"seen": list(dict.fromkeys([*keys, *seen]))[:SEEN_LIMIT]}
    if not fresh:
        return Check("unchanged", "没有新内容", state=state)
    message = feed_message(watch.name, fresh)
    return Check(
        "changed",
        f"有 {len(fresh)} 条新内容",
        changes=len(fresh),
        lines=tuple(e.title for e in fresh[:DIFF_LINES]),
        message=message,
        material=feed_material(message, fresh),
        state=state,
    )


# -- pages ----------------------------------------------------------------------------------------------


def _node_lines(node: LexborNode, parts: list[str]) -> None:
    """Visible text of `node`, with a line break around every block element (iterative: deep pages)."""
    stack: list[tuple[LexborNode, bool]] = [(node, False)]
    while stack:
        current, leaving = stack.pop()
        if leaving:
            parts.append("\n")
            continue
        if current.is_text_node:
            parts.append(SPACES.sub(" ", current.text_content or ""))  # a newline in text is a space
            continue
        if not current.is_element_node:
            continue
        tag = current.tag or ""
        if tag in BLOCKS:
            parts.append("\n")
            stack.append((current, True))
        elif tag in CELLS:
            parts.append(" ")
        children = list(current.iter(include_text=True))
        stack.extend((child, False) for child in reversed(children))


def visible_lines(html: str, selector: str) -> list[str]:
    """The visible text of the selected part (all matches; no selector: <body>), one line per block,
    whitespace collapsed, empty lines dropped."""
    parser = LexborHTMLParser(html)
    parser.strip_tags(INVISIBLE)
    if selector:
        try:
            nodes = parser.css(selector)
        except SelectolaxError:
            raise WatchError(f"选择器写得不对：{selector}") from None
        if not nodes:
            raise WatchError(f"选择器没有找到内容：{selector}")
    else:
        root = parser.body or parser.root
        nodes = [root] if root is not None else []
    parts: list[str] = []
    for node in nodes:
        _node_lines(node, parts)
        parts.append("\n")
    return [line for line in (" ".join(raw.split()) for raw in "".join(parts).split("\n")) if line]


def changed_lines(old: list[str], new: list[str]) -> tuple[list[str], int]:
    """Up to DIFF_LINES readable changed lines, and how many changed in all."""
    changed = []
    for line in difflib.unified_diff(old, new, n=0, lineterm=""):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("+"):
            changed.append("新增：" + _one_line(line[1:], DIFF_LINE_CHARS))
        elif line.startswith("-"):
            changed.append("删掉：" + _one_line(line[1:], DIFF_LINE_CHARS))
    return changed[:DIFF_LINES], len(changed)


def check_page(watch: Watch, page: Page) -> Check:
    lines = visible_lines(page.text, watch.selector)
    text = "\n".join(lines)
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    state = {"hash": digest, "excerpt": text[:EXCERPT_CHARS], "text": text[:TEXT_KEPT]}
    old = watch.state
    if not isinstance(old.get("hash"), str):
        return Check(
            "baseline", f"第一次检查：记下了现在的内容（{len(text)} 字），以后有变化才推送", state=state
        )
    if old["hash"] == digest:
        return Check("unchanged", "没有变化")
    previous = str(old.get("text") or old.get("excerpt") or "")
    shown, total = changed_lines(previous.split("\n") if previous else [], lines)
    if not shown:  # the change lies past what was kept of the old text
        shown, total = ["新内容：" + _one_line(text, DIFF_LINE_CHARS)], 1
    more = [f"……还有 {total - len(shown)} 行有变化"] if total > len(shown) else []
    message = "\n".join([f"「{watch.name}」有变化：", *shown, *more, watch.url])
    return Check(
        "changed",
        f"内容有变化（{total} 行）",
        changes=1,
        lines=(*shown, *more),
        message=message,
        state=state,
    )


# -- prices ---------------------------------------------------------------------------------------------


def first_price(text: str) -> float:
    match = NUMBER.search(unicodedata.normalize("NFKC", text))
    if match is None:
        raise WatchError("没有在这里找到价格")
    return float(match.group().replace(",", ""))


def show_price(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def check_price(watch: Watch, page: Page) -> Check:
    value = first_price("\n".join(visible_lines(page.text, watch.selector)))
    state = {"price": value}
    old = watch.state.get("price")
    if isinstance(old, bool) or not isinstance(old, int | float):
        return Check("baseline", f"第一次检查：现在的价格是 {show_price(value)}", state=state)
    if value == old:
        return Check("unchanged", f"价格没变：{show_price(value)}")
    change = f"价格 {show_price(old)} → {show_price(value)}"
    threshold = watch.threshold
    if threshold is not None and value > threshold:
        return Check(
            "changed",
            f"{change}，没有低于你设的 {show_price(threshold)}，不推送",
            changes=1,
            state=state,
        )
    below = f"，低于你设的 {show_price(threshold)}" if threshold is not None else ""
    return Check(
        "changed",
        change + below,
        changes=1,
        message=f"「{watch.name}」{change}{below}\n{watch.url}",
        state=state,
    )


CHECKS = {"feed": check_feed, "page": check_page, "price": check_price}


def evaluate(watch: Watch, page: Page) -> Check:
    """Compare what was fetched with the watch's state. Raises WatchError."""
    return CHECKS[watch.kind](watch, page)
