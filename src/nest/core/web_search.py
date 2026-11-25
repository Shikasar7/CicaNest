"""联网搜索 for models that cannot search by themselves: Claude and the
ChatGPT subscription search on their own; others get results from Tavily's search API — as a tool
(web_search) in the user's chat and in tasks, and fetched beforehand when they answer a question (答题).

The key is TAVILY_API_KEY in .env (or the environment); without it nothing here is offered. What comes back
is material, never instructions (nest.core.tools.MATERIAL).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

KEY = "TAVILY_API_KEY"
URL = "https://api.tavily.com/search"
RESULTS = 5
TIMEOUT = 20.0
QUERY_MAX = 400
SNIPPET_CHARS = 600


class SearchError(Exception):
    """Why the search did not work, in one Chinese sentence."""


async def search(
    query: str,
    *,
    key: str,
    proxy: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    results: int = RESULTS,
) -> list[dict[str, str]]:
    """[{title, url, content}] for `query`, best first."""
    query = " ".join(query.split())[:QUERY_MAX]
    if not query:
        raise SearchError("没给要搜的内容")
    body = {"query": query, "max_results": results, "search_depth": "basic", "include_answer": False}
    try:
        async with httpx.AsyncClient(proxy=proxy, transport=transport, timeout=TIMEOUT) as client:
            response = await client.post(URL, json=body, headers={"authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise SearchError(f"搜索服务连不上（{type(exc).__name__}）") from exc
    if response.status_code in (401, 403):
        raise SearchError("搜索服务的 key 不对或没有额度了")
    if response.status_code == 429:
        raise SearchError("搜索次数用完了，过一会儿再试")
    if response.status_code >= 400:
        raise SearchError(f"搜索服务出错了（HTTP {response.status_code}）")
    try:
        found: Any = response.json().get("results") or []
    except ValueError as exc:
        raise SearchError("搜索服务返回的内容看不懂") from exc
    return [
        {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "content": " ".join(str(item.get("content") or "").split())[:SNIPPET_CHARS],
        }
        for item in found
        if isinstance(item, dict) and item.get("url")
    ][:results]


def render(found: list[dict[str, str]]) -> str:
    if not found:
        return "什么也没搜到。"
    return "\n\n".join(
        f"{n}. {item['title'] or item['url']}\n{item['url']}\n{item['content']}"
        for n, item in enumerate(found, 1)
    )
