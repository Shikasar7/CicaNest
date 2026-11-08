"""Trying a Discord token before saving it: it asks who the token belongs to, through the same proxy the bot
will use. Never raises, and nothing it says holds the token."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from nest.adapters.base import Probed
from nest.adapters.discord.rest import DiscordError, DiscordRest, ProxyDown, Unauthorized
from nest.api.text import proxy_url
from nest.llm.http import scrub


def check_proxy(value: str) -> str:
    """The proxy as stored, "" for none. ValueError when it is not an http proxy: websockets needs another
    package for SOCKS, which nest does not have."""
    proxy = proxy_url(value)
    if proxy is None:
        raise ValueError("代理要写成 http://127.0.0.1:7890 这样，不支持 socks")
    return proxy


async def probe(
    fields: Mapping[str, str], *, transport: httpx.AsyncBaseTransport | None = None, api_base: str = ""
) -> Probed:
    token = fields.get("token", "").strip()
    if not token:
        return Probed(False, detail="还没有填 Token")
    try:
        proxy = check_proxy(fields.get("proxy", ""))
    except ValueError as exc:
        return Probed(False, detail=str(exc))
    extra = {"api_base": api_base} if api_base else {}
    rest = DiscordRest(token, proxy=proxy or None, transport=transport, **extra)
    try:
        me = await rest.me()
    except Unauthorized:
        return Probed(False, detail="Token 不对：Discord 拒绝了它")
    except ProxyDown as exc:
        return Probed(False, detail=f"{exc}，这不是 Token 的问题")
    except DiscordError as exc:
        return Probed(False, detail=scrub(str(exc), token))
    finally:
        await rest.aclose()
    if not me.get("bot"):
        return Probed(False, detail="这不是 bot 的 Token：要用 Developer Portal 里 Bot 页的 Token")
    self_id = str(me.get("id") or "")
    name = str(me.get("global_name") or me.get("username") or "")
    return Probed(True, self_id=self_id, name=name, detail=f"连上了：{name}（{self_id}）")
