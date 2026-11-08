"""Trying a 企业微信 bot's BotID and Secret: connect once and subscribe. Only when creating the bot: a
subscribe kicks out whoever is connected, the bot itself included. Never raises, and nothing it says holds the
secret."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from websockets.exceptions import WebSocketException

from nest.adapters.base import Probed
from nest.adapters.wecom.adapter import ACK_TIMEOUT, WS_URL, _load, connect, subscribe_frame
from nest.llm.http import scrub


async def probe(fields: Mapping[str, str], *, url: str = WS_URL) -> Probed:
    bot_id, secret = fields.get("bot_id", "").strip(), fields.get("secret", "").strip()
    if not bot_id or not secret:
        return Probed(False, detail="BotID 和 Secret 都要填")
    request = subscribe_frame(bot_id, secret)
    try:
        async with asyncio.timeout(ACK_TIMEOUT), connect(url) as ws:
            await ws.send(json.dumps(request))
            while True:
                answer = _load(await ws.recv())
                if answer and answer.get("headers", {}).get("req_id") == request["headers"]["req_id"]:
                    break
    except TimeoutError:
        return Probed(False, detail=f"企业微信 {ACK_TIMEOUT:.0f} 秒内没有回应")
    except (OSError, WebSocketException) as exc:
        return Probed(False, detail=f"连不上企业微信（{type(exc).__name__}）")
    if answer.get("errcode"):
        why = scrub(str(answer.get("errmsg") or ""), secret)
        return Probed(False, detail=f"BotID 或 Secret 不对（{answer.get('errcode')}）：{why}")
    return Probed(True, self_id=bot_id, detail="连上了。保存后到群里 @ 它一次，群才会出现在这里")
