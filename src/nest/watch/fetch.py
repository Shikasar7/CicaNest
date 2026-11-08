"""Fetching one page or feed for a 监控: bounded in time, size and redirects; errors are one Chinese sentence.

Never inherits proxies from the environment: the only proxy is the system setting fetch_proxy, and only for
watches that ask for it. Messages name the host at most, never the full address (it may carry a token).
"""

from __future__ import annotations

import asyncio
import codecs
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 20.0
HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,"
        "application/atom+xml;q=0.9,*/*;q=0.8"
    ),
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.6",
}
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.IGNORECASE)
XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]+encoding\s*=\s*["']([A-Za-z0-9_.:\-]+)["']""")
# Pages that say gb2312 or gbk routinely use characters only gb18030 (their superset) has.
WIDER = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "gb_2312-80": "gb18030"}


class WatchError(Exception):
    """A check that could not be done; the message is shown as-is in the console."""


@dataclass(frozen=True)
class Page:
    url: str  # after redirects
    status: int
    content_type: str
    charset: str | None  # from the Content-Type header
    body: bytes = field(repr=False)

    @property
    def text(self) -> str:
        return decode(self.body, self.charset)


def network_transport(proxy: str | None) -> httpx.AsyncBaseTransport:
    """The real network (tests/conftest.py replaces this so no test can reach it)."""
    return httpx.AsyncHTTPTransport(proxy=proxy, trust_env=False)


def _codec(name: str | bytes | None) -> str | None:
    if not name:
        return None
    text = name.decode("ascii", "ignore") if isinstance(name, bytes) else name
    text = text.strip().lower()
    try:
        found = codecs.lookup(WIDER.get(text, text)).name
    except LookupError:
        return None
    return WIDER.get(found, found)


def _bom(body: bytes) -> str | None:
    if body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return None


def decode(body: bytes, charset: str | None = None) -> str:
    """Text of a page: its BOM, the header's charset, a <meta>/XML declaration, then UTF-8, then GB18030."""
    head = body[:4096]
    declared = META_CHARSET.search(head) or XML_ENCODING.search(head)
    for codec in (_bom(body), _codec(charset), _codec(declared.group(1) if declared else None)):
        if codec:
            return body.decode(codec, errors="replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return body.decode("gb18030")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def _host(url: str | httpx.URL) -> str:
    try:
        return urlsplit(str(url)).hostname or "这个网站"
    except ValueError:
        return "这个网站"


async def _read(response: httpx.Response, limit: int) -> bytes:
    """The body, refusing (and no longer reading) anything past `limit` bytes."""
    too_big = WatchError(f"内容太大（超过 {limit // (1024 * 1024)} MB），没有读完")
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise too_big
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > limit:
            raise too_big
    return bytes(body)


async def fetch(
    url: str,
    *,
    proxy: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 (a deadline for the whole fetch, redirects included)
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = MAX_BYTES,
) -> Page:
    """GET `url` (http/https only), following at most 5 redirects. An injected `transport` (tests) is used
    as it is, without the proxy."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise WatchError("只支持 http:// 或 https:// 开头的地址")
    host = _host(url)
    try:
        client = httpx.AsyncClient(
            transport=transport or network_transport(proxy),
            trust_env=False,
            timeout=timeout,
            follow_redirects=False,
            headers=HEADERS,
        )
        async with asyncio.timeout(timeout), client:
            request = client.build_request("GET", url)
            for hop in range(MAX_REDIRECTS + 1):
                host = _host(request.url)
                response = await client.send(request, stream=True)
                try:
                    redirect = response.next_request
                    if redirect is not None:
                        if hop == MAX_REDIRECTS:
                            raise WatchError(f"跳转次数太多（超过 {MAX_REDIRECTS} 次）")
                        if redirect.url.scheme not in ("http", "https"):
                            raise WatchError("网站跳转到了不支持的地址（只支持 http 和 https）")
                        request = redirect
                        continue
                    if response.status_code >= 400:
                        raise WatchError(f"网站返回了 HTTP {response.status_code}")
                    body = await _read(response, max_bytes)
                    return Page(
                        url=str(response.url),
                        status=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        charset=response.charset_encoding,
                        body=body,
                    )
                finally:
                    await response.aclose()
    except TimeoutError:
        raise WatchError(f"超时：{timeout:g} 秒内没有读完 {host}") from None
    except httpx.TimeoutException:
        raise WatchError(f"超时：{host} 在 {timeout:g} 秒内没有响应") from None
    except httpx.ProxyError:
        raise WatchError("外网代理拒绝了这次访问，请检查「系统」页的外网代理") from None
    except httpx.ConnectError:
        raise WatchError(f"连不上 {host}" + ("（通过外网代理）" if proxy else "")) from None
    except httpx.UnsupportedProtocol:
        raise WatchError("只支持 http:// 或 https:// 开头的地址") from None
    except httpx.HTTPError as exc:
        raise WatchError(f"读取 {host} 时网络出错（{type(exc).__name__}）") from None
    except (httpx.InvalidURL, ValueError):  # e.g. a host name that cannot be encoded
        raise WatchError("地址写得不对") from None
    raise AssertionError("unreachable")
