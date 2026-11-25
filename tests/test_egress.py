"""联网: her container goes out only through nest-egress — the proxy refuses this machine and
the local network, logs every host under her name — and the switch per companion, which makes her
container again on the other side; the console's switch and log."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nest.core.egress import recent
from nest.core.egress_proxy import Log, Networks, Proxy, public
from nest.core.sandbox import EGRESS, NETWORK_MAP, Sandbox, container_name, network_name, proxy_url
from nest.core.workspace import Workspace
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_workspace import PretendDocker

BOT = "qq:90000001"


def test_what_counts_as_out_there() -> None:
    for inside in (
        "127.0.0.1",
        "192.168.1.20",
        "10.1.2.3",
        "172.16.0.1",
        "100.64.1.1",  # CGNAT: Tailscale
        "169.254.169.254",
        "0.250.250.254",  # OrbStack's way to the Mac
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:192.168.1.1",
        "not an address",
    ):
        assert not public(inside), inside
    for outside in ("93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
        assert public(outside), outside


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[int, list[bytes]]]:
    """A server standing in for a site out there: echoes what a tunnel sends, answers a plain request."""
    heard: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        first = await reader.read(65536)
        heard.append(first)
        if first.startswith(b"GET "):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
        else:
            writer.write(b"echo:" + first)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1], heard


async def _proxy(tmp_path: Path) -> tuple[int, Path, asyncio.Server]:
    names = {
        "site.example": ["127.0.0.1"],
        "router.example": ["192.168.1.1"],
        "mixed.example": ["127.0.0.1", "10.0.0.1"],
    }

    async def resolve(host: str, port: int) -> list[str]:
        if host not in names:
            raise OSError("no such name")
        return names[host]

    log = tmp_path / "egress.log"
    # The proxy is reached on 127.0.0.1 here: that stands for its address on her network.
    (tmp_path / "networks.json").write_text(json.dumps({"127.0.0.1": "qq-90000001"}), encoding="utf-8")
    # Here 127.0.0.1 stands for a public address (the stand-in site); everything else is judged for real.
    proxy = Proxy(
        Log(log),
        resolver=resolve,
        allowed=lambda a: a == "127.0.0.1" or public(a),
        networks=Networks(tmp_path / "networks.json"),
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    return server.sockets[0].getsockname()[1], log, server


async def _ask(port: int, head: str, body: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head.encode() + body)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    if body and data.startswith(b"HTTP/1.1 200 Connection"):
        data += await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return data


AS_HER = "Proxy-Authorization: Basic " + base64.b64encode(b"qq-90000001:").decode() + "\r\n"


async def test_the_proxy_lets_her_out_and_keeps_her_off_the_local_network(
    tmp_path: Path, upstream: tuple[int, list[bytes]]
) -> None:
    site, heard = upstream
    port, log, server = await _proxy(tmp_path)
    async with server:
        tunnel = await _ask(port, f"CONNECT site.example:{site} HTTP/1.1\r\n{AS_HER}\r\n", b"ping")
        assert tunnel.startswith(b"HTTP/1.1 200 Connection established") and tunnel.endswith(b"echo:ping")

        plain = await _ask(
            port, f"GET http://site.example:{site}/a?b=1 HTTP/1.1\r\nHost: site.example\r\n{AS_HER}\r\n"
        )
        assert plain.endswith(b"hi")
        sent = heard[-1].decode()
        assert sent.startswith("GET /a?b=1 HTTP/1.1\r\n") and "Proxy-Authorization" not in sent

        for target, why in (
            ("router.example:443", "指向本机或局域网"),
            ("mixed.example:443", "指向本机或局域网"),  # one private address is enough
            ("192.168.1.1:80", "域名解析失败"),  # not a name here; real resolution would say private
            ("site.example:25", "这个端口不放行"),
            ("nowhere.example:443", "域名解析失败"),
        ):
            refused = await _ask(port, f"CONNECT {target} HTTP/1.1\r\n{AS_HER}\r\n")
            assert refused.split(b"\r\n")[0] in (b"HTTP/1.1 403 Forbidden", b"HTTP/1.1 502 Bad Gateway"), (
                target
            )
            assert why in refused.decode(), target
        assert (await _ask(port, "GET ftp://x/ HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 400")

    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(e["host"], e["ok"]) for e in lines[:3]] == [
        ("site.example", True),
        ("site.example", True),
        ("router.example", False),
    ]
    assert all(e["who"] == "qq-90000001" for e in lines[:-1])
    shown = recent(tmp_path, BOT)
    assert shown[0]["host"] == "ftp://x/" and shown[1]["host"] == "nowhere.example"  # hers by where it came
    assert shown[1]["ok"] is False and shown[-1]["ok"] is True
    assert recent(tmp_path, "qq:somebody-else") == []


async def test_online_she_is_on_the_internal_network_behind_the_proxy(tmp_path: Path) -> None:
    docker = PretendDocker()
    switch = {"on": True}

    async def online(_: str) -> bool:
        return switch["on"]

    sandbox = Sandbox(
        Workspace(tmp_path / "ws"), docker_call=docker, egress_log=tmp_path / "egress", online=online
    )
    assert (await sandbox.run(BOT, "pip install requests")).startswith("（退出码 0）")
    mine = network_name(BOT)
    assert mine == "nest-net-qq-90000001"
    assert ["network", "create", "--internal", mine] in docker.calls
    (egress,) = [c for c in docker.calls if c[0] == "run" and EGRESS in c]
    for setting in (["--read-only"], ["--user", "1000:1000"], ["--cap-drop", "ALL"]):
        assert any(egress[i : i + len(setting)] == setting for i in range(len(egress)))
    assert ["network", "connect", mine, EGRESS] in docker.calls
    # The proxy knows her by the network a connection arrives on.
    assert list(json.loads((tmp_path / "egress" / NETWORK_MAP).read_text(encoding="utf-8")).values()) == [
        "qq-90000001"
    ]
    assert (tmp_path / "egress").is_dir()
    name = container_name(BOT)
    (made,) = [c for c in docker.calls if c[0] == "run" and name in c]
    assert made[made.index("--network") + 1] == mine
    assert (
        f"HTTPS_PROXY={proxy_url(BOT)}" in made and proxy_url(BOT) == f"http://qq-90000001:nest@{EGRESS}:3128"
    )
    assert "PYTHONUSERBASE=/work/.local" in made  # what she installs stays with her files
    assert name in docker.networks[mine] and EGRESS in docker.networks[mine]
    # Another companion: a network of her own, which hers cannot reach.
    other = "qq:90000002"
    await sandbox.run(other, "ls")
    assert container_name(other) in docker.networks[network_name(other)]
    assert (
        container_name(other) not in docker.networks[mine]
        and name not in docker.networks[network_name(other)]
    )
    assert set(json.loads((tmp_path / "egress" / NETWORK_MAP).read_text(encoding="utf-8")).values()) == {
        "qq-90000001",
        "qq-90000002",
    }

    # Off: her container is made again with no network at all.
    switch["on"] = False
    docker.calls.clear()
    await sandbox.run(BOT, "ls")
    assert ["rm", "--force", name] in docker.calls
    (again,) = [c for c in docker.calls if c[0] == "run"]
    assert again[again.index("--network") + 1] == "none"
    assert not any(EGRESS in c for c in docker.calls)

    # A newer proxy script: the proxy container is made again.
    switch["on"] = True
    docker.labels[EGRESS]["nest.egress"] = "old"
    docker.calls.clear()
    await sandbox.run(BOT, "ls")
    assert ["rm", "--force", EGRESS] in docker.calls


async def test_the_switch_and_the_log_in_the_console(world: World, tmp_path: Path) -> None:  # noqa: F811
    docker = PretendDocker()
    sandbox = Sandbox(world.state.workspace, docker_call=docker, egress_log=tmp_path)
    world.state.sandbox = world.state.brain.tools.sandbox = sandbox
    base = f"/api/workspaces/{WHALE}"
    assert (await world.http.get(f"{base}/files")).json()["online"] is True
    turned = await world.http.put(f"{base}/online", json={"online": False})
    assert turned.status_code == 200
    assert (await world.http.get(f"{base}/files")).json()["online"] is False
    assert ("workspace", {"bot_id": WHALE}) in world.events
    (tmp_path / "egress.log").write_text(
        json.dumps({"t": 1.0, "who": "qq-10001", "host": "pypi.org", "port": 443, "ok": True}) + "\n",
        encoding="utf-8",
    )
    shown = (await world.http.get(f"{base}/network")).json()
    assert shown == {
        "entries": [{"at": 1.0, "host": "pypi.org", "port": 443, "ok": True, "why": ""}],
        "online": False,
    }
    assert (await world.http.put("/api/workspaces/nobody/online", json={"online": True})).status_code == 404


# -- a proxy app's fake addresses ---------------------------------------------------------------------------


def test_a_proxy_apps_fake_answer_is_never_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    from nest.core import real_dns
    from nest.core.tools import private_address

    real_dns._cache.clear()
    answers = {
        "site.example": ["198.18.0.43"],
        "router.example": ["198.18.0.44"],
        "plain.example": ["93.184.216.34"],
    }
    monkeypatch.setattr(real_dns, "system", lambda host: answers.get(host, []))
    asked: list[str] = []

    def doh(url: str) -> bytes:
        asked.append(url)
        name = url.split("name=")[1].split("&")[0]
        real = {"site.example": "151.101.0.223", "router.example": "192.168.0.1"}.get(name)
        if real is None:
            return json.dumps({"Status": 3}).encode()
        return json.dumps(
            {"Status": 0, "Answer": [{"type": 5, "data": "x."}, {"type": 1, "data": real}]}
        ).encode()

    monkeypatch.setattr(real_dns, "_get", doh)
    assert real_dns.addresses("plain.example") == ["93.184.216.34"] and asked == []  # a real answer is used
    assert real_dns.addresses("site.example") == ["151.101.0.223"]
    assert real_dns.addresses("192.168.1.1") == ["192.168.1.1"]
    assert not private_address("site.example")
    assert private_address("router.example")  # its fake answer hid a router
    assert not private_address("nowhere.example")  # not resolvable at all: the fetch decides

    def down(url: str) -> bytes:
        raise OSError("blocked")

    real_dns._cache.clear()
    monkeypatch.setattr(real_dns, "_get", down)
    assert private_address("site.example")  # only a fake answer, and no way to check it: refused
    real_dns._cache.clear()


async def test_the_proxy_connects_to_the_real_address(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    from nest.core import egress_proxy

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("198.18.0.43", 443))])
    monkeypatch.setattr(
        egress_proxy, "_over_https", lambda host: ["151.101.0.223"] if host == "pypi.org" else []
    )
    assert await egress_proxy.resolve("pypi.org", 443) == ["151.101.0.223"]
    assert await egress_proxy.resolve("10.0.0.7", 80) == ["10.0.0.7"]
    with pytest.raises(OSError):
        await egress_proxy.resolve("hidden.example", 443)


def test_python_tells_the_proxy_who_she_is() -> None:
    """urllib (and pip) send a user to the proxy only when the proxy URL has a password too."""
    import urllib.request

    from nest.core.sandbox import proxy_url

    _, user, password, _ = urllib.request._parse_proxy(proxy_url(BOT))  # type: ignore[attr-defined]
    assert user == "qq-90000001" and password


async def test_she_is_known_by_where_she_connects_not_by_the_name_she_gives(tmp_path: Path) -> None:
    """A program in her container could give another companion's name
    (or none) to the proxy; the log goes by the network the connection arrived on."""
    port, log, server = await _proxy(tmp_path)
    impostor = "Proxy-Authorization: Basic " + base64.b64encode(b"qq-90000002:nest").decode() + "\r\n"
    async with server:
        await _ask(port, f"CONNECT router.example:443 HTTP/1.1\r\n{impostor}\r\n")
        (tmp_path / "networks.json").write_text("{}", encoding="utf-8")  # reached somewhere else
        await _ask(port, f"CONNECT router.example:443 HTTP/1.1\r\n{impostor}\r\n")
    first, second = [json.loads(line)["who"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert first == "qq-90000001" and second == "?qq-90000002"
    assert recent(tmp_path, "qq:90000002") == []


async def test_what_a_plugin_reached_is_held_against_what_it_declared(tmp_path: Path) -> None:
    """The proxy logs which container a connection came from (containers.json, written by nest as they
    start); the plugins page shows a plugin's hosts and the ones it never declared."""
    from nest.core.egress import declared, plugin_reach

    log = tmp_path / "egress.log"
    # The proxy is reached on 127.0.0.1 (her network), from 127.0.0.1 (standing for her plugin's container).
    (tmp_path / "networks.json").write_text(json.dumps({"127.0.0.1": "qq-90000001"}), encoding="utf-8")
    proxy_map = {"127.0.0.1": "nest-mcp-qq-90000001-demo.weather"}
    (tmp_path / "containers.json").write_text(json.dumps(proxy_map), encoding="utf-8")
    proxy = Proxy(
        Log(log),
        resolver=lambda host, port: asyncio.sleep(0, ["127.0.0.1"]),
        allowed=lambda a: True,
        networks=Networks(tmp_path / "networks.json"),
        containers=Networks(tmp_path / "containers.json"),
    )
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await _ask(port, f"CONNECT router.example:1 HTTP/1.1\r\n{AS_HER}\r\n")
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["from"] == "nest-mcp-qq-90000001-demo.weather" and entry["who"] == "qq-90000001"

    lines = [
        {
            "t": 1,
            "who": "qq-90000001",
            "from": "nest-mcp-qq-90000001-demo.weather",
            "host": "api.weather.example",
            "ok": True,
        },
        {
            "t": 2,
            "who": "qq-90000001",
            "from": "nest-mcp-qq-90000001-demo.weather",
            "host": "cdn.weather.example",
            "ok": True,
        },
        {
            "t": 3,
            "who": "qq-90000001",
            "from": "nest-mcp-qq-90000001-demo.weather",
            "host": "tracker.example",
            "ok": False,
        },
        {"t": 4, "who": "qq-90000001", "from": "nest-ws-qq-90000001", "host": "pypi.org", "ok": True},  # hers
    ]
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n", encoding="utf-8")
    reach = plugin_reach(tmp_path, "demo.weather", ["*.weather.example"])
    assert [h["host"] for h in reach["seen"]] == [
        "api.weather.example",
        "cdn.weather.example",
        "tracker.example",
    ]
    assert reach["undeclared"] == ["tracker.example"]
    assert reach["seen"][2]["refused"] == 1
    assert declared("weather.example", ["*.weather.example"]) and declared("x.y", ["*"])
    assert not declared("evilweather.example", ["*.weather.example"])
