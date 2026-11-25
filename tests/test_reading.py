"""What read_page gives of a page: the part there is to read."""

from __future__ import annotations

from nest.watch.check import reading_lines

STORY = "".join(f"<p>第 {n} 段，说的是山里的一条小河，水很清，石头很圆，鱼游得很慢。</p>" for n in range(12))
MENU = "".join(f"<li><a href='/{n}'>菜单 {n}</a></li>" for n in range(30))


def test_the_main_part_without_what_is_around_it() -> None:
    page = f"""<html><head><title>小河 - 某站</title><style>p{{}}</style></head><body>
        <header><ul>{MENU}</ul></header><nav><ul>{MENU}</ul></nav>
        <main><header><h1>小河</h1><span>3月2日 · 阿星</span></header>{STORY}<aside>广告</aside></main>
        <div>相关推荐</div><footer>版权</footer></body></html>"""
    lines = reading_lines(page)
    assert lines[:3] == ["小河 - 某站", "小河", "3月2日 · 阿星"]  # a byline stays
    assert lines[3].startswith("第 0 段") and lines[-1].startswith("第 11 段")
    assert not any(word in "".join(lines) for word in ("菜单", "广告", "推荐", "版权"))


def test_a_page_that_marks_nothing_is_given_whole() -> None:
    assert reading_lines(f"<body><div>导语</div>{STORY}</body>")[0] == "导语"
    # Several articles (a list of posts): all of them; a main part with next to nothing in it: the page.
    posts = reading_lines(f"<body><article>{STORY}</article><article>{STORY}尾</article></body>")
    assert len(posts) == 25
    assert len(reading_lines(f"<body><main>空</main>{STORY}</body>")) == 13


def test_a_page_whose_text_is_all_around_is_still_read() -> None:
    assert reading_lines(f"<body><nav>{STORY}</nav></body>")[0].startswith("第 0 段")
    assert reading_lines("<body><nav>只有这些</nav></body>") == ["只有这些"]
    assert reading_lines("") == []
