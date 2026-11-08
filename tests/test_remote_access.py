"""Opening the console from other devices (NEST_REMOTE): who may connect, under which names, signing in
with the password, device sessions, the 远程访问 card's endpoints, and `nest set-password`.

Remote clients are httpx.ASGITransport clients with another address; nothing listens on a real port.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from nest import app as nest_app
from nest import hints, net
from nest.app import create_app
from nest.cli import set_password
from nest.config import Settings
from nest.net import address_kind, host_allowed, machine_names, split_host
from nest.remote import COOKIE, MAX_DEVICES, SESSION_SECONDS, TOUCH_SECONDS, RemoteAccess, Throttle
from nest.storage.db import Database
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}
PASSWORD = "鲸鱼爱冒泡-2026"
PHONE, LAPTOP, TAILNET, INTERNET = "192.168.1.30", "192.168.1.31", "100.101.1.2", "8.8.8.8"
NAS = "nest.home.arpa"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- addresses and names ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ("127.0.0.1", "local"),
        ("::1", "local"),
        ("::ffff:127.0.0.1", "local"),
        ("10.1.2.3", "lan"),
        ("172.16.0.9", "lan"),
        ("192.168.1.30", "lan"),
        ("::ffff:192.168.1.30", "lan"),
        ("fe80::1%en0", "lan"),
        ("fd12:3456::1", "lan"),
        ("100.64.0.1", "tailscale"),
        ("100.127.255.254", "tailscale"),
        ("fd7a:115c:a1e0::1", "tailscale"),
        ("172.32.0.1", None),
        ("100.128.0.1", None),
        ("8.8.8.8", None),
        ("2001:4860::8888", None),
        ("", None),
        ("example.com", None),
        (None, None),
    ],
)
def test_address_kinds(address: str | None, kind: str | None) -> None:
    assert address_kind(address) == kind


@pytest.mark.parametrize(
    ("header", "parsed"),
    [
        ("Mac-Mini.Local.:18800", ("mac-mini.local", 18800)),
        ("192.168.1.20", ("192.168.1.20", None)),
        ("[::1]:18800", ("::1", 18800)),
        ("[fd7a:115c:a1e0::1]", ("fd7a:115c:a1e0::1", None)),
        ("::1", None),
        ("[::1", None),
        ("[::1]x", None),
        ("host:abc", None),
        ("host:99999", None),
        (":18800", None),
        ("", None),
    ],
)
def test_host_headers_are_parsed_strictly(header: str, parsed: tuple[str, int | None] | None) -> None:
    assert split_host(header) == parsed


@pytest.mark.parametrize(
    ("header", "remote", "allowed"),
    [
        ("127.0.0.1:18800", False, True),
        ("localhost:18800", False, True),
        ("[::1]:18800", False, True),
        ("127.0.0.1", False, False),  # locally the port is always there
        ("127.0.0.1:9999", False, False),
        ("192.168.1.20:18800", False, False),
        ("192.168.1.20:18800", True, True),
        ("192.168.1.20", True, False),  # no proxy in front is supported: the port is always there
        ("100.101.1.2:18800", True, True),
        ("mac-mini.local:18800", True, True),
        ("mac-mini.local:9999", True, False),
        ("evil.example:18800", True, False),  # a DNS-rebinding page
        ("8.8.8.8:18800", True, False),
        ("192.168.1.20.nip.io:18800", True, False),
    ],
)
def test_which_host_names_reach_nest(header: str, remote: bool, allowed: bool) -> None:
    assert host_allowed(header, port=18800, remote=remote, names={"mac-mini.local"}) is allowed


def test_machine_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net.socket, "gethostname", lambda: "Mac-Mini.local")
    monkeypatch.setattr(net, "bonjour_name", lambda: None)
    assert machine_names([" NEST.home.arpa. ", ""]) == {"mac-mini.local", "mac-mini", "nest.home.arpa"}
    # macOS may call itself "bogon" after a network change; its Bonjour name still reaches it.
    monkeypatch.setattr(net.socket, "gethostname", lambda: "bogon")
    monkeypatch.setattr(net, "bonjour_name", lambda: "My-Mac-Mini")
    assert machine_names() == {"bogon", "bogon.local", "my-mac-mini", "my-mac-mini.local"}


def test_tailscale_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net.shutil, "which", lambda name: "tailscale")
    status = {"Self": {"DNSName": "Mac-Mini.tail1234.ts.net.", "TailscaleIPs": ["100.101.1.2", "fd7a::1"]}}
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(status).encode(), b"")

    monkeypatch.setattr(net.subprocess, "run", run)
    assert net.tailscale_self() == ("mac-mini.tail1234.ts.net", ["100.101.1.2", "fd7a::1"])
    assert calls == [["tailscale", "status", "--json"]]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess([], 1, b"", b"not running"),
        subprocess.CompletedProcess([], 0, b"not json", b""),
        subprocess.CompletedProcess([], 0, b"[]", b""),
        subprocess.CompletedProcess([], 0, b'{"Self": null}', b""),
        subprocess.TimeoutExpired("tailscale", 3),
        OSError("gone"),
    ],
)
def test_no_tailscale_is_fine(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    monkeypatch.setattr(net.shutil, "which", lambda name: "tailscale")

    def run(args: list[str], **kwargs: Any) -> object:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(net.subprocess, "run", run)
    assert net.tailscale_self() is None


def test_remote_listens_everywhere_but_host_stays_loopback(tmp_path: Path) -> None:
    assert Settings(home=tmp_path, _env_file=None).bind_host == "127.0.0.1"
    remote = Settings(home=tmp_path, remote=True, allowed_hosts=" a.lan, ,b.lan", _env_file=None)
    assert remote.bind_host == "0.0.0.0"
    assert remote.base_url == "http://127.0.0.1:18800"  # how this machine's CLI reaches it
    assert remote.extra_hosts == ["a.lan", "b.lan"]
    with pytest.raises(ValueError):
        Settings(home=tmp_path, host="0.0.0.0", _env_file=None)


# -- the running app ----------------------------------------------------------------------------------


class World:
    def __init__(self, app: FastAPI, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.clients: list[httpx.AsyncClient] = []

    def client(
        self, ip: str = "127.0.0.1", host: str | None = None, *, origin: bool = True, **kwargs: Any
    ) -> httpx.AsyncClient:
        """A browser at `ip` on nest's page http://<host>:18800 (host: the address itself). Like a browser,
        it sends that page's Origin with every request (only writes need it); origin=False: none."""
        if host is None:
            host = f"[{ip}]" if ":" in ip else ip
        base = f"http://{host}:18800"
        headers = {"origin": base} if origin else {}
        headers |= kwargs.pop("headers", {})
        transport = httpx.ASGITransport(app=self.app, client=(ip, 51000))
        client = httpx.AsyncClient(transport=transport, base_url=base, headers=headers, **kwargs)
        self.clients.append(client)
        return client

    def local(self) -> httpx.AsyncClient:
        return self.client(headers=AUTH, origin=False)  # the CLI: the token, no browser headers

    @property
    def remote(self) -> RemoteAccess:
        return self.app.state.remote

    async def set_password(self, password: str = PASSWORD) -> None:
        response = await self.local().put("/api/remote/password", json={"password": password})
        assert response.status_code == 200, response.text

    async def signed_in(self, ip: str = PHONE) -> httpx.AsyncClient:
        client = self.client(ip)
        response = await client.post("/api/login", json={"password": PASSWORD})
        assert response.status_code == 200, response.text
        return client


def app_world(tmp_path: Path, **settings: Any) -> Callable[[], Any]:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def context() -> AsyncIterator[World]:
        config = Settings(home=tmp_path, port=18800, _env_file=None, **settings)
        app = create_app(config, token=TOKEN)
        async with app.router.lifespan_context(app):
            world = World(app, config)
            try:
                yield world
            finally:
                for client in world.clients:
                    await client.aclose()

    return context


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with app_world(tmp_path, remote=True, allowed_hosts=NAS)() as w:
        yield w


async def test_without_remote_access_only_this_machine_gets_in(tmp_path: Path) -> None:
    async with app_world(tmp_path)() as w:
        response = await w.client(PHONE).get("/api/health")
        assert response.status_code == 403
        assert response.text == "仅限本机访问"
        assert (await w.client().get("/api/health")).status_code == 200
        assert w.app.state.access.remote is False


async def test_the_internet_is_refused_even_with_remote_access(world: World) -> None:
    for path in ("/api/health", "/", "/api/login"):
        response = await world.client(INTERNET).get(path)
        assert response.status_code == 403
        assert response.text == "只允许本机、局域网和 Tailscale 访问"
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_lan_and_tailscale_reach_nest_under_its_names(world: World) -> None:
    for ip in (PHONE, TAILNET, "fd7a:115c:a1e0::5"):
        assert (await world.client(ip).get("/api/health")).status_code == 200
    assert (await world.client(PHONE, host=NAS).get("/api/health")).status_code == 200
    response = await world.client(PHONE, host="evil.example").get("/api/health")
    assert response.status_code == 403
    assert "NEST_ALLOWED_HOSTS" in response.text


async def test_the_tailscale_name_is_learned_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nest_app, "tailscale_self", lambda: ("mac-mini.tail1234.ts.net", ["100.101.1.2"]))
    async with app_world(tmp_path, remote=True)() as w:
        assert {"mac-mini.tail1234.ts.net", "mac-mini"} <= w.app.state.access.names
        laptop = w.client(TAILNET, host="mac-mini.tail1234.ts.net")
        assert (await laptop.get("/api/health")).status_code == 200
        assert (await laptop.get("/api/status")).status_code == 401  # still needs a sign-in


async def test_the_tailscale_name_is_learned_later_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_world(tmp_path, remote=True)() as w:  # Tailscale not up yet when nest started
        laptop = w.client(TAILNET, host="mac-mini.tail1234.ts.net")
        assert (await laptop.get("/api/health")).status_code == 403
        monkeypatch.setattr(nest_app, "tailscale_self", lambda: ("mac-mini.tail1234.ts.net", []))
        await nest_app.learn_names(w.app)  # what the app does every few minutes
        assert (await laptop.get("/api/health")).status_code == 200


async def test_the_process_token_only_works_from_this_machine(world: World) -> None:
    phone = world.client(PHONE, headers=AUTH)
    response = await phone.get("/api/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "未登录：请先输入密码登录"
    assert (await world.client(PHONE).get("/login", params={"token": TOKEN})).status_code == 403
    assert (await world.local().get("/api/status")).status_code == 200


async def test_signing_in(world: World) -> None:
    phone = world.client(PHONE)
    assert (await phone.get("/api/auth")).json() == {
        "signed_in": False,
        "password_set": False,
        "local": False,
        "start_hint": hints.START,
    }
    response = await phone.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 409
    assert "nest set-password" in response.json()["detail"]

    await world.set_password()
    assert (await phone.post("/api/login", json={"password": "不对的密码"})).status_code == 401
    response = await phone.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{COOKIE}=")
    assert "httponly" in cookie and "samesite=strict" in cookie and f"max-age={SESSION_SECONDS}" in cookie
    assert "secure" not in cookie  # plain http on the home network: a Secure cookie would never be sent
    assert (await phone.get("/api/status")).status_code == 200
    assert (await phone.get("/api/auth")).json()["signed_in"] is True

    stored = await world.app.state.db.fetchone("SELECT value FROM settings WHERE key = 'remote_sessions'")
    token = phone.cookies[COOKIE]
    assert token not in stored["value"] and PASSWORD not in stored["value"]


async def test_signing_in_needs_the_same_site(world: World) -> None:
    await world.set_password()
    response = await world.client(PHONE).post(
        "/api/login", json={"password": PASSWORD}, headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403


async def test_wrong_passwords_lock_that_address_out(world: World) -> None:
    clock = Clock()
    world.app.state.throttle = Throttle(clock=clock)
    await world.set_password()
    phone = world.client(PHONE)
    for _ in range(5):
        assert (await phone.post("/api/login", json={"password": "guess"})).status_code == 401
    response = await phone.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 429
    assert response.json()["detail"] == "密码错误次数太多，15 分钟后再试"
    assert (await world.client(LAPTOP).post("/api/login", json={"password": PASSWORD})).status_code == 200
    clock.now += 14 * 60
    assert "1 分钟" in (await phone.post("/api/login", json={"password": PASSWORD})).json()["detail"]
    clock.now += 61
    assert (await phone.post("/api/login", json={"password": PASSWORD})).status_code == 200


async def test_signing_out(world: World) -> None:
    await world.set_password()
    phone = await world.signed_in()
    response = await phone.post("/api/logout")
    assert response.status_code == 200
    assert f'{COOKIE}=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"]
    assert (await phone.get("/api/status")).status_code == 401
    assert await world.remote.devices() == []


async def test_the_remote_card(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nest.api.routes_remote.reachable_addresses",
        lambda: [("192.168.1.20", "lan"), (TAILNET, "tailscale")],
    )
    await world.set_password()
    laptop = await world.signed_in(LAPTOP)
    phone = await world.signed_in(PHONE)
    card = (await phone.get("/api/remote")).json()
    assert card["enabled"] is True and card["configured"] is False and card["password_set"] is True
    assert card["addresses"] == [
        {"url": "http://192.168.1.20:18800", "kind": "lan"},
        {"url": f"http://{TAILNET}:18800", "kind": "tailscale"},
    ]
    assert {"url": f"http://{NAS}:18800", "name": NAS} in card["names"]
    assert [(d["ip"], d["current"]) for d in card["devices"]] == [(PHONE, True), (LAPTOP, False)]

    laptop_id = card["devices"][1]["id"]
    assert (await phone.delete(f"/api/remote/devices/{laptop_id}")).status_code == 200
    assert (await laptop.get("/api/status")).status_code == 401
    assert (await phone.delete(f"/api/remote/devices/{laptop_id}")).status_code == 404

    tablet = await world.signed_in("192.168.1.32")
    assert (await phone.delete("/api/remote/devices")).status_code == 200
    assert (await tablet.get("/api/status")).status_code == 401
    assert (await phone.get("/api/status")).status_code == 200


async def test_changing_the_password(world: World) -> None:
    await world.set_password()
    phone = await world.signed_in(PHONE)
    laptop = await world.signed_in(LAPTOP)
    response = await phone.put("/api/remote/password", json={"password": "新的密码新的密码"})
    assert response.status_code == 403  # the current one is needed once one is set
    response = await phone.put(
        "/api/remote/password", json={"current": "不对", "password": "新的密码新的密码"}
    )
    assert response.status_code == 403
    response = await phone.put("/api/remote/password", json={"current": PASSWORD, "password": "短"})
    assert response.status_code == 422
    assert response.json()["detail"] == "密码至少要 8 个字符"
    response = await phone.put(
        "/api/remote/password", json={"current": PASSWORD, "password": "新的密码新的密码"}
    )
    assert response.status_code == 200
    assert (await phone.get("/api/status")).status_code == 200  # the device that changed it stays in
    assert (await laptop.get("/api/status")).status_code == 401
    retry = await world.client(LAPTOP).post("/api/login", json={"password": "新的密码新的密码"})
    assert retry.status_code == 200


@pytest.mark.parametrize(
    ("password", "problem"), [(" 前后有空格的密码 ", "开头和结尾"), ("x" * 129, "最多 128")]
)
async def test_password_rules(world: World, password: str, problem: str) -> None:
    response = await world.local().put("/api/remote/password", json={"password": password})
    assert response.status_code == 422
    assert problem in response.json()["detail"]


async def test_sessions_expire_and_renew(world: World) -> None:
    clock = Clock()
    world.remote.clock = clock
    await world.set_password()
    phone = await world.signed_in()
    (first,) = await world.remote.devices()
    clock.now += TOUCH_SECONDS + 1
    assert (await phone.get("/api/status")).status_code == 200
    (renewed,) = await world.remote.devices()
    assert renewed.last_seen_at == clock.now and renewed.expires_at == clock.now + SESSION_SECONDS
    assert renewed.expires_at > first.expires_at
    clock.now += SESSION_SECONDS + 1
    assert (await phone.get("/api/status")).status_code == 401


async def test_a_device_seen_from_a_new_address_is_updated(world: World) -> None:
    await world.set_password()
    phone = await world.signed_in(PHONE)
    roaming = world.client(TAILNET)
    roaming.cookies.set(COOKIE, phone.cookies[COOKIE])
    assert (await roaming.get("/api/status")).status_code == 200
    (device,) = await world.remote.devices()
    assert device.ip == TAILNET


async def test_only_the_newest_devices_are_kept(world: World) -> None:
    await world.set_password()
    tag = await world.remote.check(PASSWORD)
    assert tag is not None
    for i in range(MAX_DEVICES + 2):
        assert await world.remote.sign_in(ip=f"192.168.1.{i + 1}", agent="x", tag=tag) is not None
    devices = await world.remote.devices()
    assert len(devices) == MAX_DEVICES
    assert "192.168.1.1" not in {d.ip for d in devices}


async def test_turning_remote_access_on_and_off(world: World) -> None:
    local = world.local()
    response = await local.patch("/api/remote", json={"enabled": True})
    assert response.status_code == 409
    assert response.json()["detail"] == "先设置登录密码，再开启远程访问"
    await world.set_password()
    response = await local.patch("/api/remote", json={"enabled": False})
    assert response.json() == {"enabled": True, "configured": False}
    response = await local.patch("/api/remote", json={"enabled": True})
    assert response.json() == {"enabled": True, "configured": True}
    assert Settings(home=world.settings.home, _env_file=world.settings.home / ".env").remote is True
    assert (await local.get("/api/remote")).json()["configured"] is True


async def test_an_environment_variable_wins(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world.set_password()
    monkeypatch.setenv("NEST_REMOTE", "true")
    response = await world.local().patch("/api/remote", json={"enabled": False})
    assert response.status_code == 409
    assert not (world.settings.home / ".env").exists()


# -- nest set-password ----------------------------------------------------------------------------------


async def stored_password(settings: Settings) -> bool:
    db = await Database.open(settings.db_path)
    try:
        return await RemoteAccess(db).password_set()
    finally:
        await db.close()


def answers(*values: str) -> Callable[[str], str]:
    queue = list(values)
    return lambda prompt: queue.pop(0)


def test_set_password_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(home=tmp_path, _env_file=None)
    assert set_password(settings, ask=answers("短")) == 1
    assert set_password(settings, ask=answers(PASSWORD, PASSWORD + "x")) == 1
    assert not asyncio.run(stored_password(settings))
    assert set_password(settings, ask=answers(PASSWORD, PASSWORD)) == 0
    out = capsys.readouterr().out
    assert "密码已设置" in out and "NEST_REMOTE=true" in out and PASSWORD not in out
    assert asyncio.run(stored_password(settings))


async def test_set_password_signs_everyone_out(tmp_path: Path) -> None:
    async with app_world(tmp_path, remote=True)() as w:
        await w.set_password()
        phone = await w.signed_in()
        # Another process in real life: its own event loop and database connection.
        done = await asyncio.to_thread(
            set_password, w.settings, ask=answers("另一个新密码123", "另一个新密码123")
        )
        assert done == 0
        assert (await phone.get("/api/status")).status_code == 401
        retry = await w.client(PHONE).post("/api/login", json={"password": "另一个新密码123"})
        assert retry.status_code == 200


def test_only_reachable_addresses_are_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    def nic(address: str, family: int = socket.AF_INET) -> SimpleNamespace:
        return SimpleNamespace(family=family, address=address)

    adapters = {
        "en0": [nic("192.168.1.20"), nic("fe80::1", socket.AF_INET6)],
        "utun4": [nic("100.101.1.2")],  # Tailscale on macOS
        "vEthernet (WSL)": [nic("172.31.224.1")],
        "bridge100": [nic("192.168.64.1")],  # macOS Virtualization
        "en1": [nic("10.0.0.5")],
        "en5": [nic("10.0.0.9")],  # down
        "en7": [nic("169.254.3.3")],
        "Mihomo": [nic("198.18.0.1")],  # a proxy's TUN: not a home address
        "lo0": [nic("127.0.0.1")],
    }
    monkeypatch.setattr(net.psutil, "net_if_addrs", lambda: adapters)
    monkeypatch.setattr(
        net.psutil, "net_if_stats", lambda: {name: SimpleNamespace(isup=name != "en5") for name in adapters}
    )
    assert net.reachable_addresses() == [
        ("192.168.1.20", "lan"),
        ("10.0.0.5", "lan"),
        ("100.101.1.2", "tailscale"),
    ]


# -- what the security review found --------------------------------------------------------------------


async def test_writes_need_nest_s_own_page(world: World) -> None:
    """Over plain http browsers send no Sec-Fetch-Site; Origin tells nest's page from another one on the
    same machine (another port), which would carry the cookie too."""
    await world.set_password()
    phone = await world.signed_in()
    cookie = {COOKIE: phone.cookies[COOKIE]}
    foreign = world.client(PHONE, headers={"origin": f"http://{PHONE}:5099"}, cookies=cookie)
    response = await foreign.delete("/api/remote/devices")
    assert response.status_code == 403
    assert response.json()["detail"] == "跨站请求被拒绝"
    bare = world.client(PHONE, origin=False, cookies=cookie)
    assert (await bare.delete("/api/remote/devices")).status_code == 403  # a device write with no Origin
    assert (await bare.get("/api/remote")).status_code == 200  # reading needs none
    assert (await phone.delete("/api/remote/devices")).status_code == 200
    null = world.client(PHONE, headers={"origin": "null"}, cookies=cookie)
    assert (await null.post("/api/logout")).status_code == 403


async def test_signing_in_from_another_page_is_refused(world: World) -> None:
    await world.set_password()
    page = world.client(PHONE, headers={"origin": "http://192.168.1.99:8080"})
    assert (await page.post("/api/login", json={"password": PASSWORD})).status_code == 403
    assert await world.remote.devices() == []


async def test_parallel_guesses_are_cut_off_at_the_limit(world: World) -> None:
    await world.set_password()
    phone = world.client(PHONE)
    answers = await asyncio.gather(
        *(phone.post("/api/login", json={"password": f"guess{i}"}) for i in range(20))
    )
    codes = sorted(r.status_code for r in answers)
    assert codes == [401] * 5 + [429] * 15


async def test_guessing_from_many_addresses_hits_a_shared_limit(world: World) -> None:
    world.app.state.throttle = Throttle(limit=5, total=3)
    await world.set_password()
    for i in range(3):
        response = await world.client(f"192.168.1.{40 + i}").post("/api/login", json={"password": "guess"})
        assert response.status_code == 401
    response = await world.client(LAPTOP).post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 429


async def test_a_check_racing_a_password_change_does_not_sign_in(world: World) -> None:
    await world.set_password()
    tag = await world.remote.check(PASSWORD)
    assert tag is not None
    await world.remote.set_password("换过的新密码123")
    assert await world.remote.sign_in(ip=PHONE, agent="x", tag=tag) is None
    assert await world.remote.devices() == []


async def test_the_cookie_is_renewed_with_the_session(world: World) -> None:
    clock = Clock()
    world.remote.clock = clock
    await world.set_password()
    phone = await world.signed_in()
    assert "set-cookie" not in (await phone.get("/api/remote")).headers  # nothing to renew yet
    clock.now += TOUCH_SECONDS + 1
    response = await phone.get("/api/remote")
    assert response.status_code == 200
    renewed = response.headers["set-cookie"].lower()
    assert renewed.startswith(f"{COOKIE}=") and f"max-age={SESSION_SECONDS}" in renewed


async def test_a_signed_out_device_stops_hearing_the_live_stream(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nest.api import routes_live

    monkeypatch.setattr(routes_live, "KEEPALIVE_SECONDS", 0.05)
    await world.set_password()
    phone = await world.signed_in()
    listening = asyncio.create_task(phone.get("/api/events"))
    await asyncio.sleep(0.2)
    assert not listening.done()
    assert (await world.local().delete("/api/remote/devices")).status_code == 200  # signs the phone out
    response = await asyncio.wait_for(listening, 5)
    assert response.status_code == 200 and response.text.startswith("event: hello")


def test_forwarded_addresses_are_not_believed(tmp_path: Path) -> None:
    from nest.cli import make_server

    app = create_app(Settings(home=tmp_path, _env_file=None), token=TOKEN)
    assert make_server(app).config.proxy_headers is False


def test_this_machine_reaches_a_remote_nest_on_ipv4(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, host="::1", remote=True, _env_file=None)
    assert settings.base_url == "http://127.0.0.1:18800"
    assert Settings(home=tmp_path, host="::1", _env_file=None).base_url == "http://[::1]:18800"
