"""插件市场: a list of plugins at an https address the user sets; unusable entries are
left out; a plugin is downloaded, checked against the list's sha256 and its own plugin.json, and shown
like a zip chosen by hand before it is installed."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import zipfile
from typing import Any

import httpx
import pytest

from nest.core import market
from nest.core.tools import pin as real_pin
from nest.core.tools import private_address as real_private
from tests.test_bot_settings_api import World, world  # noqa: F401

INDEX_URL = "https://plugins.example/index.json"


@pytest.fixture(autouse=True)
def no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Made-up host names are not looked up (that waits for DNS); addresses are judged for real."""

    def private(host: str) -> bool:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return real_private(host)

    monkeypatch.setattr(market, "private_address", private)

    def pin(host: str) -> str | None:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return None
        return real_pin(host)

    monkeypatch.setattr(market, "pin", pin)


def plugin_zip(version: str = "1.0.0", plugin_id: str = "demo.outing") -> bytes:
    manifest = {"id": plugin_id, "name": "出门", "version": version, "provides": {"skills": ["出门.md"]}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("出门.md", "# 出门提醒\n\n看天气。")
    return buffer.getvalue()


def entry(data: bytes, **changes: Any) -> dict[str, Any]:
    return {
        "id": "demo.outing",
        "name": "出门",
        "version": "1.0.0",
        "author": "阿鲸",
        "description": "出门前提醒带伞",
        "kinds": ["skills"],
        "trust": "content",
        "url": "https://plugins.example/outing-1.0.0.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "permissions": {"network": [], "secrets": [], "confirm": []},
    } | changes


def serve(world: World, files: dict[str, bytes]) -> list[str]:  # noqa: F811
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        body = files.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    world.state.fetch_transport = httpx.MockTransport(handler)
    return asked


async def test_installing_from_the_market(world: World) -> None:  # noqa: F811
    assert (await world.http.get("/api/market")).json() == {"url": "", "entries": [], "error": ""}
    data = plugin_zip()
    index = {
        "format": 1,
        "plugins": [
            entry(data),
            entry(data, id="demo.plain", url="http://plugins.example/x.zip"),  # not https
            entry(data, id="nest.fake"),  # nest's own names
            entry(data, id="demo.huge", size=10**9),
            entry(data, id="demo.badsum", sha256="00"),
        ],
    }
    serve(
        world,
        {
            INDEX_URL: json.dumps(index, ensure_ascii=False).encode(),
            "https://plugins.example/outing-1.0.0.zip": data,
        },
    )
    for bad in ("http://plugins.example/index.json", "https://192.168.1.1/index.json", "https://127.0.0.1/i"):
        assert (await world.http.put("/api/market", json={"url": bad})).status_code == 422, bad
    assert (await world.http.put("/api/market", json={"url": INDEX_URL})).status_code == 200

    shown = (await world.http.get("/api/market")).json()
    assert shown["error"] == "" and [e["id"] for e in shown["entries"]] == ["demo.outing"]
    assert (
        shown["entries"][0]["installed"] is None and shown["entries"][0]["trust"] == "guides"
    )  # skills: never less, whatever the list says

    staged = await world.http.post("/api/market/demo.outing/stage")
    assert staged.status_code == 200, staged.text
    assert staged.json()["plugin"]["id"] == "demo.outing"
    done = await world.http.post(f"/api/plugin-packages/{staged.json()['stage']}/install")
    assert done.status_code == 201
    assert (await world.http.get("/api/market")).json()["entries"][0]["installed"] == "1.0.0"
    assert (await world.http.post("/api/market/demo.nothing/stage")).status_code == 404


async def test_what_does_not_match_the_list_is_not_installed(world: World) -> None:  # noqa: F811
    data = plugin_zip()
    other = plugin_zip(version="9.9.9")
    index = {
        "format": 1,
        "plugins": [
            entry(data, sha256=hashlib.sha256(b"something else").hexdigest()),
            entry(
                other, id="demo.other", url="https://plugins.example/other.zip"
            ),  # its zip says demo.outing
        ],
    }
    serve(
        world,
        {
            INDEX_URL: json.dumps(index).encode(),
            "https://plugins.example/outing-1.0.0.zip": data,
            "https://plugins.example/other.zip": other,
        },
    )
    await world.http.put("/api/market", json={"url": INDEX_URL})
    tampered = await world.http.post("/api/market/demo.outing/stage")
    assert tampered.status_code == 502 and "校验值不同" in tampered.json()["detail"]
    swapped = await world.http.post("/api/market/demo.other/stage")
    assert swapped.status_code == 422 and "不是同一个插件" in swapped.json()["detail"]
    assert world.state.plugins.installed == {}


async def test_a_list_that_cannot_be_read_says_why(world: World) -> None:  # noqa: F811
    serve(world, {INDEX_URL: b"<html>not json</html>"})
    await world.http.put("/api/market", json={"url": INDEX_URL})
    assert (await world.http.get("/api/market")).json()["error"] == "插件清单不是 JSON"
    serve(world, {INDEX_URL: json.dumps({"plugins": []}).encode()})
    assert "格式不认识" in (await world.http.get("/api/market")).json()["error"]
    serve(world, {})
    assert "读不到" in (await world.http.get("/api/market")).json()["error"]
    assert (await world.http.put("/api/market", json={"url": ""})).json() == {"url": ""}
