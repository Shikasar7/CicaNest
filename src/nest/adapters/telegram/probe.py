"""Trying a Telegram token before saving it: it asks who the token belongs to, through the same proxy the bot
will use. Never raises, and nothing it says holds the token."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from nest.adapters.base import Probed
from nest.adapters.discord.probe import check_proxy
from nest.adapters.telegram.api import API_BASE, TOKEN, ProxyDown, TelegramApi, TelegramError, Unauthorized
from nest.adapters.telegram.normalize import person_name
from nest.llm.http import scrub

PRIVACY = (
    "；群里现在只收得到 @ 它和回复它的话。要它听到全部：到 @BotFather 用 /setprivacy 关掉，"
    "再把它移出群重新拉一次"
)


async def probe(
    fields: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_base: str = API_BASE,
) -> Probed:
    token = fields.get("token", "").strip()
    if not token:
        return Probed(False, detail="还没有填 Token")
    if not TOKEN.fullmatch(token):
        return Probed(False, detail="Token 是「数字:一串字母数字」的样子，到 @BotFather 那里再复制一遍")
    try:
        proxy = check_proxy(fields.get("proxy", ""))
    except ValueError as exc:
        return Probed(False, detail=str(exc))
    api = TelegramApi(token, proxy=proxy or None, transport=transport, api_base=api_base)
    try:
        me = await api.me()
    except Unauthorized:
        return Probed(False, detail="Token 不对：Telegram 拒绝了它")
    except ProxyDown as exc:
        return Probed(False, detail=f"{exc}，这不是 Token 的问题")
    except TelegramError as exc:
        return Probed(False, detail=scrub(str(exc), token))
    finally:
        await api.aclose()
    self_id, name = str(me.get("id") or ""), person_name(me)
    if not self_id:
        return Probed(False, detail="Telegram 没说这个 Token 是谁的")
    hint = "" if me.get("can_read_all_group_messages") else PRIVACY
    return Probed(True, self_id=self_id, name=name, detail=f"连上了：{name}（@{me.get('username')}）{hint}")
