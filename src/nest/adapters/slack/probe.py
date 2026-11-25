"""Trying a Slack app's two tokens before saving them: who the bot token belongs to, and whether the app
token may open a Socket Mode connection (asked for and not used). Never raises, and nothing it says holds a
token."""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from nest.adapters.base import Probed
from nest.adapters.discord.probe import check_proxy
from nest.adapters.slack.api import API_BASE, ProxyDown, SlackApi, SlackError, Unauthorized


def check_tokens(bot: str, app: str) -> None:
    """ValueError when one is not the token it should be (they are easily mixed up)."""
    if bot and not bot.startswith("xoxb-"):
        raise ValueError("Bot Token 是 xoxb- 开头的，在 OAuth & Permissions 页")
    if app and not app.startswith("xapp-"):
        raise ValueError("App Token 是 xapp- 开头的，在 Basic Information 页的 App-Level Tokens")


async def probe(
    fields: Mapping[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_base: str = API_BASE,
) -> Probed:
    bot, app = fields.get("token", "").strip(), fields.get("app_token", "").strip()
    if not bot or not app:
        return Probed(False, detail="Bot Token 和 App Token 都要填")
    try:
        check_tokens(bot, app)
        proxy = check_proxy(fields.get("proxy", ""))
    except ValueError as exc:
        return Probed(False, detail=str(exc))
    api = SlackApi(bot, app, proxy=proxy or None, transport=transport, api_base=api_base)
    try:
        try:
            me = await api.me()
        except Unauthorized:
            return Probed(False, detail="Bot Token 不对：Slack 拒绝了它")
        try:
            await api.open_socket()
        except Unauthorized:
            return Probed(False, detail="App Token 不对：要有 connections:write 权限，并且打开 Socket Mode")
    except ProxyDown as exc:
        return Probed(False, detail=f"{exc}，这不是 Token 的问题")
    except SlackError as exc:
        return Probed(False, detail=api.clean(str(exc)))
    finally:
        await api.aclose()
    if not me.get("bot_id"):
        return Probed(False, detail="这不是 bot 的 Token：要用 xoxb- 开头的 Bot User OAuth Token")
    self_id, name = str(me.get("user_id") or ""), str(me.get("user") or "")
    team = str(me.get("team") or "")
    return Probed(True, self_id=self_id, name=name, detail=f"连上了：{name}（{team}）")
