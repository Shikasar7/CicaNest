from __future__ import annotations

import re

import httpx
import pytest
from fastapi.routing import iter_route_contexts

from nest.api.auth import COOKIE_NAME
from nest.config import Settings
from nest.storage.db import load_migrations
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}


async def test_health_is_public(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["app"] == "nest"


async def test_status_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/status")).status_code == 401
    assert (await client.get("/api/status", headers={"authorization": "Bearer wrong"})).status_code == 401
    response = await client.get("/api/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["schema_version"] == len(load_migrations())


async def test_login_sets_cookie_that_authorizes_api(client: httpx.AsyncClient) -> None:
    bad = await client.get("/login", params={"token": "wrong"})
    assert bad.status_code == 403
    response = await client.get("/login", params={"token": TOKEN})
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.cookies.get(COOKIE_NAME) == TOKEN
    assert (await client.get("/api/status")).status_code == 200


@pytest.mark.parametrize("host", ["evil.example:18800", "127.0.0.1:9999", "192.168.1.5:18800"])
async def test_rejects_non_loopback_host_header(client: httpx.AsyncClient, host: str) -> None:
    # A DNS-rebinding page would arrive with its own Host header; it must not reach any route.
    response = await client.get("/api/health", headers={"host": host})
    assert response.status_code == 403


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "media-src 'self' blob:" in response.headers["content-security-policy"]  # 试听
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_shutdown_requires_token_and_signals(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/shutdown")).status_code == 401
    assert client.app.state.shutdown_requests == 0  # type: ignore[attr-defined]
    assert (await client.post("/api/shutdown", headers=AUTH)).status_code == 200
    assert client.app.state.shutdown_requests == 1  # type: ignore[attr-defined]


async def test_console_placeholder_when_not_built(client: httpx.AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200 and "npm run build" in response.text
    assert (await client.get("/api/nope")).status_code == 404


async def test_console_serves_build_and_spa_fallback(settings: Settings) -> None:
    from nest.app import create_app

    dist = settings.web_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "assets" / "big.js").write_bytes(b"console.log(1);\n" * 200)
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (settings.home / "secret.txt").write_text("nope", encoding="utf-8")
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url) as http:
            script = await http.get("/assets/app.js")
            assert script.text == "console.log(1)" and "immutable" in script.headers["cache-control"]
            assert "cache-control" not in (await http.get("/favicon.svg")).headers  # not hashed: asked again
            # Compressed for phones on Tailscale (httpx unpacks it); small answers are not worth it.
            big = await http.get("/assets/big.js", headers={"accept-encoding": "gzip"})
            assert big.headers["content-encoding"] == "gzip" and big.text == "console.log(1);\n" * 200
            assert "content-encoding" not in script.headers
            index = await http.get("/bots/whale")
            assert index.text == "<div id=root></div>" and index.headers["cache-control"] == "no-store"
            escaped = await http.get("/..%2Fsecret.txt")
            assert "nope" not in escaped.text


def test_settings_refuse_non_loopback_host(tmp_path) -> None:
    with pytest.raises(ValueError):
        Settings(home=tmp_path, host="0.0.0.0", _env_file=None)
    assert Settings(home=tmp_path, host="::1", _env_file=None).base_url == "http://[::1]:18800"


@pytest.mark.parametrize(
    ("site", "status"), [("cross-site", 403), ("same-site", 403), ("same-origin", 200), ("none", 200)]
)
async def test_cross_site_writes_are_refused(client: httpx.AsyncClient, site: str, status: int) -> None:
    # SameSite cookies ignore ports: another local web app could otherwise post with the console cookie.
    response = await client.post("/api/shutdown", headers=AUTH | {"sec-fetch-site": site})
    assert response.status_code == status
    if status == 403:
        assert response.json() == {"detail": "跨站请求被拒绝"}
        assert client.app.state.shutdown_requests == 0  # type: ignore[attr-defined]
    # Reads are not affected.
    assert (await client.get("/api/status", headers=AUTH | {"sec-fetch-site": site})).status_code == 200


async def test_every_api_route_needs_the_token(client: httpx.AsyncClient) -> None:
    app = client.app  # type: ignore[attr-defined]
    checked = []
    for route in iter_route_contexts(app.routes):  # flattens included routers
        path = route.path or ""
        # Public on purpose: the launcher's health check, and signing in with the password.
        if not path.startswith("/api/") or path in ("/api/health", "/api/auth", "/api/login"):
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(route.methods or ()):
            response = await client.request(method, concrete)
            assert response.status_code == 401, f"{method} {path} → {response.status_code}"
            checked.append(f"{method} {path}")
    assert "PATCH /api/providers/{provider_id}" in checked and "POST /api/bots/pause-all" in checked
    assert "DELETE /api/providers/{provider_id}" in checked and "GET /api/events" in checked
    assert {
        "GET /api/personas",
        "POST /api/personas",
        "PATCH /api/personas/{persona_id}",
        "DELETE /api/personas/{persona_id}",
        "GET /api/usage",
        "GET /api/system",
        "PATCH /api/system/settings",
        "POST /api/system/snowluma/start",
        "POST /api/system/snowluma/stop",
        "POST /api/system/snowluma/restart",
        "POST /api/system/qq/open",
        "GET /api/logs",
        "GET /api/watches",
        "POST /api/watches",
        "GET /api/watches/targets",
        "PATCH /api/watches/{watch_id}",
        "DELETE /api/watches/{watch_id}",
        "POST /api/watches/{watch_id}/run",
        "GET /api/watches/{watch_id}/runs",
        "POST /api/logout",
        "GET /api/remote",
        "PATCH /api/remote",
        "PUT /api/remote/password",
        "DELETE /api/remote/devices",
        "DELETE /api/remote/devices/{device_id}",
    } <= set(checked)


async def test_validation_errors_are_one_plain_sentence(client: httpx.AsyncClient) -> None:
    secret = "sk-test-LEAKCHECK-123"
    cases = [
        ("PATCH", "/api/bots/qq:1", {"mode": secret}, "模式格式不对"),
        ("PATCH", "/api/bots/qq:1", {"mode": "live", "note": secret}, "「note」不认识这个设置"),
        ("PATCH", "/api/bots/qq:1", {}, "没有要改的内容"),
        ("PATCH", "/api/bots/qq:1", {"aliases": secret}, "别名格式不对"),
        ("PATCH", "/api/bots/qq:1/channels/1", {"cooldown_seconds": 601}, "冷却超出范围"),
        ("PATCH", "/api/bots/qq:1/channels/1", {"cooldown_seconds": 1.5}, "冷却格式不对"),
        ("PATCH", "/api/bots/qq:1/channels/1", {"persona_id": secret}, "人设格式不对"),
        ("POST", "/api/personas", {"name": secret}, "人设内容必须填写"),
        ("PATCH", "/api/personas/1", {"content": [secret]}, "人设内容格式不对"),
        ("PATCH", "/api/personas/x", {"name": secret}, "人设格式不对"),
        (
            "POST",
            "/api/providers",
            {"id": "x", "kind": "codex", "model": "m", "key": [secret]},
            "密钥格式不对",
        ),
        ("PATCH", "/api/providers/x", {"id": secret}, "「id」不认识这个设置"),
        ("PATCH", "/api/providers/x", {"options": [secret]}, "参数格式不对"),
        ("PATCH", "/api/bots/qq:1/channels/x", {"enabled": True}, "「channel_id」格式不对"),
        ("PATCH", "/api/system/settings", {"snowluma_dir": [secret]}, "SnowLuma 文件夹格式不对"),
        ("PATCH", "/api/system/settings", {"qq_executable": {"x": secret}}, "QQ 程序的位置格式不对"),
        ("PATCH", "/api/system/settings", {"fetch_proxy": [secret]}, "外网代理格式不对"),
        ("POST", "/api/watches", {"name": secret}, "类型必须填写"),
        ("POST", "/api/watches", {"kind": [secret]}, "名字必须填写"),
        ("PATCH", "/api/watches/1", {"kind": [secret]}, "类型格式不对"),
        ("PATCH", "/api/watches/1", {"interval_minutes": "60"}, "检查间隔格式不对"),
        ("PATCH", "/api/watches/1", {"threshold": secret}, "提醒价格格式不对"),
        ("PATCH", "/api/watches/1", {"note": secret}, "「note」不认识这个设置"),
        ("PATCH", "/api/watches/x", {"name": secret}, "监控格式不对"),
        ("PATCH", "/api/watches/0", {"name": secret}, "监控超出范围"),
    ]
    for method, path, body, detail in cases:
        response = await client.request(method, path, json=body, headers=AUTH)
        assert response.status_code == 422 and response.json() == {"detail": detail}, (path, body)
        assert secret not in response.text
    broken = await client.request("PATCH", "/api/bots/qq:1", content=b"{not json", headers=AUTH)
    assert broken.status_code == 422 and broken.json() == {"detail": "请求内容格式不对"}
    days = await client.get("/api/usage", params={"days": secret}, headers=AUTH)
    assert days.status_code == 422 and days.json() == {"detail": "天数格式不对"}
