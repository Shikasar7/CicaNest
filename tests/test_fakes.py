"""The fakes are the ground truth for every later test, so they get tests of their own."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from nest.llm.base import ChatMessage, Provider, ProviderError
from tests.fakes.llm import FakeProvider
from tests.fakes.onebot import FakeOneBot


async def _next_event(ws, post_type: str) -> dict:
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if event.get("post_type") == post_type:
            return event


async def test_http_api_requires_token_and_records_calls() -> None:
    async with FakeOneBot(self_id=10001) as bot, httpx.AsyncClient(trust_env=False) as http:
        denied = await http.post(f"{bot.http_url}/get_login_info", json={})
        assert denied.status_code == 403
        headers = {"authorization": f"Bearer {bot.token}"}
        info = (await http.post(f"{bot.http_url}/get_login_info", json={}, headers=headers)).json()
        assert info["data"]["user_id"] == 10001
        missing = (await http.post(f"{bot.http_url}/no_such_action", json={}, headers=headers)).json()
        assert missing["status"] == "failed" and missing["retcode"] == 1404
        assert [name for name, _ in bot.calls] == ["get_login_info", "no_such_action"]


async def test_websocket_delivers_pushed_events_and_self_echo() -> None:
    async with (
        FakeOneBot(self_id=10001) as bot,
        websockets.connect(
            bot.ws_url, additional_headers={"Authorization": f"Bearer {bot.token}"}, proxy=None
        ) as ws,
    ):
        assert (await _next_event(ws, "meta_event"))["sub_type"] == "connect"
        await bot.push(bot.group_message(group_id=42, user_id=7, text="你好", at=[10001]))
        event = await _next_event(ws, "message")
        assert event["group_id"] == 42 and event["message"][0] == {"type": "at", "data": {"qq": "10001"}}

        # Universal role: API calls over the same socket, answered with the caller's echo.
        await ws.send(
            json.dumps(
                {
                    "action": "send_group_msg",
                    "echo": "e1",
                    "params": {"group_id": 42, "message": [{"type": "text", "data": {"text": "收到"}}]},
                }
            )
        )
        reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        while reply.get("echo") != "e1":
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert reply["status"] == "ok"
        echo = await _next_event(ws, "message_sent")
        assert echo["message_id"] == reply["data"]["message_id"] and echo["user_id"] == 10001


async def test_websocket_rejects_bad_token_and_can_be_dropped() -> None:
    async with FakeOneBot() as bot:
        with pytest.raises(websockets.InvalidStatus):
            async with websockets.connect(
                bot.ws_url, additional_headers={"Authorization": "Bearer wrong"}, proxy=None
            ):
                pass
        async with websockets.connect(f"{bot.ws_url}?access_token={bot.token}", proxy=None) as ws:
            await _next_event(ws, "meta_event")
            assert bot.connected_clients == 1
            await bot.drop_connections()
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)


async def test_history_and_get_msg_return_recorded_messages() -> None:
    async with FakeOneBot() as bot, httpx.AsyncClient(trust_env=False) as http:
        first = bot.group_message(group_id=5, user_id=1, text="一")
        second = bot.group_message(group_id=5, user_id=2, text="二")
        await bot.push(first)
        await bot.push(second)
        headers = {"authorization": f"Bearer {bot.token}"}
        history = (
            await http.post(
                f"{bot.http_url}/get_group_msg_history", json={"group_id": 5, "count": 1}, headers=headers
            )
        ).json()
        assert [m["raw_message"] for m in history["data"]["messages"]] == ["二"]
        found = (
            await http.post(
                f"{bot.http_url}/get_msg", json={"message_id": first["message_id"]}, headers=headers
            )
        ).json()
        assert found["data"]["raw_message"] == "一"


async def test_fake_provider_scripts_replies_and_errors() -> None:
    provider = FakeProvider(
        "第一句", ProviderError("502", retryable=True), lambda msgs: msgs[-1].content.upper()
    )
    assert isinstance(provider, Provider)
    request = [ChatMessage("user", "hi")]
    assert (await provider.complete(request, max_tokens=10)).text == "第一句"
    with pytest.raises(ProviderError) as failure:
        await provider.complete(request, max_tokens=10)
    assert failure.value.retryable
    assert (await provider.complete(request, max_tokens=10)).text == "HI"
    assert (await provider.complete(request, max_tokens=10)).text == "SKIP"
    assert len(provider.requests) == 4
