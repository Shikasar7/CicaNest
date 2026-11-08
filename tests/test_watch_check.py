"""One 监控 check without the app: fetching (through httpx.MockTransport only), feeds, pages, prices."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

import httpx
import pytest

from nest.storage.watches import Watch
from nest.watch.check import Check, evaluate, first_price, show_price, visible_lines
from nest.watch.fetch import MAX_REDIRECTS, Page, WatchError, decode, fetch

URL = "https://example.com/feed"
Handler = Callable[[httpx.Request], Any]


def watch(kind: str = "feed", **changes: Any) -> Watch:
    base = Watch(
        id=1,
        name="新闻",
        kind=kind,
        url=URL,
        selector="",
        threshold=None,
        interval_minutes=60,
        bot_id="qq:10001",
        target_kind="group",
        target_id="42",
        provider_id=None,
        instructions="",
        use_proxy=False,
        mode="preview",
        enabled=True,
    )
    return replace(base, **changes)


async def get(handler: Handler, url: str = URL, **kwargs: Any) -> Page:
    return await fetch(url, transport=httpx.MockTransport(handler), **kwargs)


async def checked(w: Watch, body: bytes | str, content_type: str = "text/html; charset=utf-8") -> Check:
    content = body.encode() if isinstance(body, str) else body
    page = await get(
        lambda request: httpx.Response(200, content=content, headers={"content-type": content_type})
    )
    return evaluate(w, page)


def then(w: Watch, result: Check) -> Watch:
    """The watch as the next check sees it: with the state this check left."""
    return replace(w, state=result.state if result.state is not None else w.state)


def rss(*items: tuple[str, str], title: str = "新闻") -> str:
    entries = "".join(
        f"<item><title>{name}</title><link>https://example.com/{key}</link><guid>{key}</guid>"
        f"<description>&lt;p&gt;关于 &lt;b&gt;{name}&lt;/b&gt; 的消息&lt;/p&gt;</description></item>"
        for key, name in items
    )
    head = '<?xml version="1.0" encoding="utf-8"?><rss version="2.0">'
    return f"{head}<channel><title>{title}</title>{entries}</channel></rss>"


# -- fetching ---------------------------------------------------------------------------------------------


async def test_fetch_sends_browser_headers_and_follows_redirects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="ok")

    page = await get(handler, "https://example.com/old")
    assert page.url == "https://example.com/new" and page.text == "ok" and page.status == 200
    assert "Mozilla/5.0" in seen[0].headers["user-agent"]
    assert seen[0].headers["accept-language"].startswith("zh-CN")


async def test_redirect_limit() -> None:
    def chain(request: httpx.Request) -> httpx.Response:
        hop = int(request.url.path.strip("/") or 0)
        return (
            httpx.Response(302, headers={"location": f"/{hop + 1}"}) if hop < limit else httpx.Response(200)
        )

    limit = MAX_REDIRECTS
    assert (await get(chain, "https://example.com/0")).url == f"https://example.com/{MAX_REDIRECTS}"
    limit = MAX_REDIRECTS + 1
    with pytest.raises(WatchError, match=r"^跳转次数太多（超过 5 次）$"):
        await get(chain, "https://example.com/0")
    # Only http and https, also after a redirect.
    with pytest.raises(WatchError, match="只支持 http 和 https"):
        await get(lambda r: httpx.Response(302, headers={"location": "file:///C:/Windows/win.ini"}))
    with pytest.raises(WatchError, match=r"^只支持 http:// 或 https:// 开头的地址$"):
        await get(lambda r: httpx.Response(200), "ftp://example.com/x")


async def test_errors_are_plain_chinese_without_the_address() -> None:
    url = "https://example.com/feed?token=sk-SECRET"

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("bad", request=request)

    cases = [
        (lambda r: httpx.Response(404), "网站返回了 HTTP 404"),
        (refused, "连不上 example.com"),
        (slow, "超时：example.com 在 20 秒内没有响应"),
        (broken, "读取 example.com 时网络出错（RemoteProtocolError）"),
    ]
    for handler, expected in cases:
        with pytest.raises(WatchError) as caught:
            await get(handler, url)
        assert str(caught.value).startswith(expected) and "SECRET" not in str(caught.value)


async def test_a_whole_fetch_has_one_deadline() -> None:
    async def stalls(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    with pytest.raises(WatchError, match="超时"):
        await get(stalls, timeout=0.2)


async def test_size_cap_stops_reading() -> None:
    with pytest.raises(WatchError, match=r"^内容太大（超过 3 MB），没有读完$"):
        await get(
            lambda r: httpx.Response(200, headers={"content-length": str(4 * 1024 * 1024)}, content=b"")
        )
    produced = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal produced
        while True:
            produced += 1
            yield b"x" * 65536

    with pytest.raises(WatchError, match="内容太大"):
        await get(lambda r: httpx.Response(200, content=endless()))
    assert produced <= 3 * 16 + 2  # stopped just past 3 MB
    page = await get(lambda r: httpx.Response(200, content=b"x" * 1000), max_bytes=1000)
    assert len(page.body) == 1000


async def test_charsets() -> None:
    text = "价格：鍾（gb18030 才有的字：𠀀）"
    gbk = text.encode("gb18030")
    cases = [
        (gbk, "text/html; charset=gbk"),  # the header; gbk is read as its superset gb18030
        (b'<html><head><meta charset="gb2312"></head><body>' + gbk + b"</body></html>", "text/html"),
        (
            b'<meta http-equiv="Content-Type" content="text/html; charset=GBK">' + gbk,
            "text/html",
        ),
        (text.encode(), "text/html"),  # no charset anywhere: UTF-8
        (b"\xef\xbb\xbf" + text.encode(), "text/html; charset=iso-8859-1"),  # a BOM wins
        (gbk, "text/html"),  # undeclared and not UTF-8: GB18030
    ]
    for body, content_type in cases:
        page = await get(
            lambda r, b=body, c=content_type: httpx.Response(200, content=b, headers={"content-type": c})
        )
        assert text in page.text, (content_type, page.text)
    assert decode("é".encode("latin-1"), "latin-1") == "é"
    assert decode(b"ok", "no-such-codec") == "ok"


# -- feeds ------------------------------------------------------------------------------------------------


async def test_feed_baseline_then_only_new_entries() -> None:
    w = watch()
    first = await checked(w, rss(("a", "第一条"), ("b", "第二条")), "application/rss+xml")
    assert first.outcome == "baseline" and first.message is None and first.changes == 0
    assert first.summary == "第一次检查：记下了现在的 2 条内容，以后有新的才推送"
    w = then(w, first)
    same = await checked(w, rss(("a", "第一条"), ("b", "第二条")), "application/rss+xml")
    assert same.outcome == "unchanged" and same.message is None and same.summary == "没有新内容"
    newer = await checked(
        w, rss(("c", "第三条 &amp; 更多"), ("a", "第一条"), ("b", "第二条")), "application/rss+xml"
    )
    assert newer.outcome == "changed" and newer.changes == 1
    assert newer.message == "「新闻」有 1 条新内容：\n• 第三条 & 更多 https://example.com/c"
    w = then(w, newer)
    # Seen once, never again; older entries that drop out of the feed stay remembered.
    again = await checked(w, rss(("c", "第三条 &amp; 更多")), "application/rss+xml")
    assert again.outcome == "unchanged"


async def test_feed_lists_five_then_the_rest_as_a_count() -> None:
    w = then(watch(), await checked(watch(), rss(("old", "旧的"))))
    many = await checked(w, rss(*((f"n{i}", f"新{i}") for i in range(8)), ("old", "旧的")))
    assert many.changes == 8
    lines = (many.message or "").split("\n")
    assert lines[0] == "「新闻」有 8 条新内容：" and lines[1:6] == [
        f"• 新{i} https://example.com/n{i}" for i in range(5)
    ]
    assert lines[6:] == ["还有 3 条"]


async def test_feed_entries_newest_first_and_seen_ids_capped() -> None:
    atom = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
    <entry><title>旧</title><id>urn:1</id><updated>2026-10-01T00:00:00Z</updated></entry>
    <entry><title>新</title><id>urn:2</id><updated>2026-10-20T00:00:00Z</updated></entry></feed>"""
    w = then(watch(), await checked(watch(), atom, "application/atom+xml"))
    added = await checked(
        w,
        atom.replace(
            "</feed>",
            "<entry><title>最新</title><link href='https://x.example/3'/>"
            "<updated>2026-10-25T00:00:00Z</updated></entry></feed>",
        ),
        "application/atom+xml",
    )
    assert added.message == "「新闻」有 1 条新内容：\n• 最新 https://x.example/3"
    assert len((added.state or {})["seen"]) == 3
    full = replace(watch(), state={"seen": [f"k{i}" for i in range(500)]})
    result = await checked(full, rss(("z", "新")))
    assert len((result.state or {})["seen"]) == 500 and (result.state or {})["seen"][-1] == "k498"


async def test_feed_parse_gives_summaries_without_html() -> None:
    from nest.watch.check import parse_feed

    page = await get(lambda r: httpx.Response(200, text=rss(("a", "标题"))))
    [entry] = parse_feed(page)
    assert (
        entry.title == "标题"
        and entry.link == "https://example.com/a"
        and entry.summary == "关于 标题 的消息"
    )


async def test_not_a_feed() -> None:
    with pytest.raises(WatchError, match="不像是订阅"):
        await checked(watch(), "<html><head><title>x</title></head><body>hi</body></html>")


# -- pages ------------------------------------------------------------------------------------------------

PAGE = """<html><head><title>公告</title><style>.x{{color:red}}</style>
<script>var t = {stamp};</script></head><body><nav>首页 关于</nav>
<div id="news"><h2>公告</h2><ul>{items}</ul></div><footer>访问量 {stamp}</footer></body></html>"""


def page_html(*items: str, stamp: int = 1) -> str:
    return PAGE.format(items="".join(f"<li>{item}</li>" for item in items), stamp=stamp)


async def test_page_change_with_selector_and_diff() -> None:
    w = watch("page", selector="#news", url="https://example.com/news")
    first = await checked(w, page_html("停电通知", "放假安排"))
    assert first.outcome == "baseline" and first.message is None
    assert first.summary == "第一次检查：记下了现在的内容（12 字），以后有变化才推送"
    w = then(w, first)
    # Outside the selector (the footer, a script), markup or spacing around the text: no change.
    same = await checked(w, page_html("停电通知", " <b>放假安排</b>\n ", stamp=7))
    assert same.outcome == "unchanged" and same.summary == "没有变化" and same.state is None
    changed = await checked(w, page_html("停电通知", "放假安排有调整", "新开食堂"))
    assert changed.outcome == "changed" and changed.changes == 1
    assert changed.lines == ("删掉：放假安排", "新增：放假安排有调整", "新增：新开食堂")
    assert (
        changed.message
        == "「新闻」有变化：\n删掉：放假安排\n新增：放假安排有调整\n新增：新开食堂\nhttps://example.com/news"
    )
    assert changed.summary == "内容有变化（3 行）"
    assert set(changed.state or {}) == {"hash", "excerpt", "text"}


async def test_page_diff_shows_at_most_six_lines() -> None:
    w = then(watch("page"), await checked(watch("page"), page_html("a")))
    changed = await checked(w, page_html(*(f"第{i}条" for i in range(10))))
    assert len([line for line in changed.lines if line.startswith(("新增", "删掉"))]) == 6
    assert changed.lines[-1] == "……还有 5 行有变化"


async def test_visible_text_skips_scripts_and_styles() -> None:
    html = page_html("一", stamp=5)
    assert visible_lines(html, "") == ["首页 关于", "公告", "一", "访问量 5"]
    assert visible_lines(html, "li") == ["一"]
    # Line breaks in the source are spaces, as a browser shows them; blocks and <br> are lines.
    assert visible_lines("<p>第一\n  行</p><p>第二<br>第三 <span>同一行</span></p>", "") == [
        "第一 行",
        "第二",
        "第三 同一行",
    ]


async def test_selector_that_matches_nothing() -> None:
    for kind in ("page", "price"):
        w = watch(kind, selector=".gone")
        with pytest.raises(WatchError, match=r"^选择器没有找到内容：\.gone$"):
            await checked(w, page_html("x"))
    with pytest.raises(WatchError, match="选择器写得不对"):
        await checked(watch("page", selector="div[["), page_html("x"))


# -- prices -----------------------------------------------------------------------------------------------


def test_price_variants() -> None:
    cases = {
        "¥1,299.00": 1299.0,
        "1299元": 1299.0,
        "$12.5": 12.5,
        "￥１,２９９": 1299.0,  # full-width
        "到手价 12,345,678.90 元起": 12345678.9,
        "原价 ¥199 现价 ¥99": 199.0,  # the first number: point the selector at the right element
    }
    for text, value in cases.items():
        assert first_price(text) == value, text
    with pytest.raises(WatchError, match=r"^没有在这里找到价格$"):
        first_price("暂时缺货")
    assert [show_price(v) for v in (1299.0, 12.5, 0.1 + 0.2, 99.999)] == ["1299", "12.5", "0.3", "100"]


async def test_price_change_and_threshold() -> None:
    html = '<div class="price">¥{}</div>'
    w = watch("price", selector=".price", url="https://shop.example/item", name="显卡")
    first = await checked(w, html.format("1,299.00"))
    assert (
        first.outcome == "baseline"
        and first.summary == "第一次检查：现在的价格是 1299"
        and first.message is None
    )
    w = then(w, first)
    same = await checked(w, html.format("1299"))
    assert same.outcome == "unchanged" and same.summary == "价格没变：1299"
    down = await checked(w, html.format("1,199"))
    assert down.outcome == "changed" and down.message == "「显卡」价格 1299 → 1199\nhttps://shop.example/item"
    # With a threshold: only a price at or below it is pushed.
    capped = replace(w, threshold=1000.0)
    above = await checked(capped, html.format("1,199"))
    assert above.outcome == "changed" and above.message is None
    assert above.summary == "价格 1299 → 1199，没有低于你设的 1000，不推送"
    assert (above.state or {})["price"] == 1199.0
    below = await checked(then(capped, above), html.format("999.5"))
    assert below.message == "「显卡」价格 1199 → 999.5，低于你设的 1000\nhttps://shop.example/item"
    exact = await checked(then(capped, below), html.format("1000"))
    assert exact.message is not None and "低于你设的 1000" in exact.message
    with pytest.raises(WatchError, match="没有在这里找到价格"):
        await checked(w, html.format("缺货"))
