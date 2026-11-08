"""掉线提醒: a bot's QQ down for the set minutes is reported once in a private message from a bot that is
still connected, and its return once more; short drops are not reported, nor anything when no number is
set."""

from __future__ import annotations

from typing import Any

import httpx

from nest.core.offline_alert import OfflineAlert


class FakeHub:
    def __init__(self) -> None:
        self.states = {"qq:1": "connected", "qq:2": "connected"}

    def account_views(self) -> list[dict[str, Any]]:
        names = {"qq:1": "阿鲸", "qq:2": "阿龙"}
        down = "连不上 http://127.0.0.1:3044"
        return [
            {
                "id": account,
                "self_id": account.removeprefix("qq:") + "0000",
                "name": names[account],
                "enabled": True,
                "status": {"state": state, "detail": "" if state == "connected" else down},
            }
            for account, state in self.states.items()
        ]


async def test_a_long_drop_is_reported_once_and_its_return_too() -> None:
    hub = FakeHub()
    now = [1000.0]
    sent: list[tuple[str, str, str]] = []
    settings = ["123456789", 5]
    refused: set[str] = set()

    async def send(account: str, user: str, text: str) -> bool:
        if account in refused:
            return False
        sent.append((account, user, text))
        return True

    async def read() -> tuple[str, int]:
        return settings[0], settings[1]

    alert = OfflineAlert(hub, send, read, clock=lambda: now[0])
    assert await alert.check() == []
    hub.states["qq:2"] = "reconnecting"
    assert await alert.check() == []  # just dropped
    now[0] += 4 * 60
    assert await alert.check() == []  # not long enough yet
    now[0] += 60
    [text] = await alert.check()
    assert sent == [("qq:1", "123456789", text)]  # from the bot still connected
    assert text.startswith("nest 提醒：阿龙（20000）掉线 5 分钟了：连不上 http://127.0.0.1:3044")
    now[0] += 30 * 60
    assert await alert.check() == []  # told once only

    hub.states["qq:2"] = "connected"
    [back] = await alert.check()
    assert back == "nest 提醒：阿龙（20000）又连上了，前后断了大约 35 分钟。"
    assert sent[-1][0] == "qq:2"  # the one that came back says so itself

    hub.states["qq:2"] = "reconnecting"  # a short drop: nothing
    await alert.check()
    now[0] += 60
    hub.states["qq:2"] = "connected"
    assert await alert.check() == [] and len(sent) == 2


async def test_nobody_to_send_it_and_switched_off() -> None:
    hub = FakeHub()
    now = [0.0]
    sent: list[str] = []
    settings = ["123456789", 1]

    async def send(account: str, user: str, text: str) -> bool:
        sent.append(account)
        return True

    async def read() -> tuple[str, int]:
        return settings[0], settings[1]

    alert = OfflineAlert(hub, send, read, clock=lambda: now[0])
    hub.states = {"qq:1": "reconnecting", "qq:2": "reconnecting"}
    await alert.check()
    now[0] += 120
    assert await alert.check() == []  # every bot is down: nobody can tell
    hub.states["qq:1"] = "connected"
    assert len(await alert.check()) == 1 and sent == ["qq:1"]  # tried again once one is back

    settings[0] = ""  # 掉线提醒 off
    hub.states["qq:1"] = "reconnecting"
    now[0] += 600
    await alert.check()
    now[0] += 600
    assert await alert.check() == [] and sent == ["qq:1"]


async def test_the_setting_in_the_console(client: httpx.AsyncClient) -> None:
    client.headers["authorization"] = "Bearer test-token"
    view = (await client.get("/api/system")).json()
    assert view["offline_alert"] == {"to": "", "minutes": 5}  # off until a number is set
    response = await client.patch(
        "/api/system/settings", json={"offline_alert_to": " 900000009 ", "offline_alert_minutes": 3}
    )
    assert response.status_code == 200, response.text
    assert response.json()["offline_alert"] == {"to": "900000009", "minutes": 3}
    response = await client.patch("/api/system/settings", json={"offline_alert_to": "abc"})
    assert response.status_code == 422 and "QQ 号" in response.json()["detail"]
    response = await client.patch("/api/system/settings", json={"offline_alert_minutes": 0})
    assert response.status_code == 422
    response = await client.patch("/api/system/settings", json={"offline_alert_to": ""})
    assert response.json()["offline_alert"]["to"] == ""


async def test_a_qq_stopped_on_purpose_is_not_reported() -> None:
    hub = FakeHub()
    now = [1000.0]
    sent: list[str] = []

    async def send(account: str, user: str, text: str) -> bool:
        sent.append(text)
        return True

    async def read() -> tuple[str, int]:
        return "123456789", 5

    alert = OfflineAlert(hub, send, read, clock=lambda: now[0])
    hub.states["qq:2"] = "stopped"  # its QQ container was stopped (Hub.hold)
    await alert.check()
    now[0] += 60 * 60
    assert await alert.check() == [] and sent == []
