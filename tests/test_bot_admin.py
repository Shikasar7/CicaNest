"""增加 / 删除 bot in the Docker setup, with Docker faked: adding a QQ number sets up its NapCat container
(ports, OneBot config and tokens, the host name and MAC address that keep its login), the account and the
bot; removing it deletes the container and hides the bot but keeps its data, and adding it again brings it
back with the same container identity."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest import docker, napcat
from nest.adapters.onebot11 import probe
from nest.app import create_app
from nest.config import Settings
from nest.secrets import SecretStore
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}
WHALE = docker.Container(
    True,
    running=True,
    status="running",
    ports=(("3040/tcp", 3042), ("3041/tcp", 3043), ("6099/tcp", 6098)),
    account="900000001",
)


@dataclass
class FakeDocker:
    boxes: dict[str, docker.Container] = field(default_factory=lambda: {"napcat-bot1": WHALE})
    created: list[list[str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def container(self, name: str) -> docker.Container:
        return self.boxes.get(name, docker.Container(False))

    def names(self) -> list[str]:
        return list(self.boxes)

    def create(self, args: list[str]) -> None:
        self.created.append(args)
        name = args[args.index("--name") + 1]
        account = next(a.removeprefix("ACCOUNT=") for a in args if a.startswith("ACCOUNT="))
        ports = []
        for n, arg in enumerate(args):
            if arg == "-p":
                _, host, inside = args[n + 1].split(":")
                ports.append((f"{inside}/tcp", int(host)))
        self.boxes[name] = docker.Container(
            True, running=True, status="running", ports=tuple(ports), account=account
        )

    def remove(self, name: str) -> None:
        self.removed.append(name)
        self.boxes.pop(name)


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    for name in ("container", "names", "create", "remove"):
        monkeypatch.setattr(docker, name, getattr(fake, name))
    monkeypatch.setattr(docker, "act", lambda name, action: None)
    monkeypatch.setattr(docker, "login_qr", lambda name: None)
    monkeypatch.setattr(napcat, "_free_on_host", lambda port: True)
    return fake


@pytest.fixture
async def http(tmp_path: Path, box: FakeDocker) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(home=tmp_path / "nest", port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            body = {"snowluma_mode": "docker", "snowluma_container": "napcat-bot1"}
            assert (await client.patch("/api/system/settings", json=body)).status_code == 200
            yield client


def env_file(tmp_path: Path) -> SecretStore:
    return SecretStore(tmp_path / "nest" / ".env")


async def test_a_bot_is_added_with_its_own_container(
    http: httpx.AsyncClient, box: FakeDocker, tmp_path: Path
) -> None:
    for body, status in (
        ({"name": "小海豚", "self_id": "12ab"}, 422),
        ({"name": " ", "self_id": "123456789"}, 422),
    ):
        assert (await http.post("/api/bots", json=body)).status_code == status
    response = await http.post(
        "/api/bots", json={"name": "小海豚", "self_id": "123456789", "aliases": ["豚豚"]}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"bot_id": "qq:123456789", "container": "napcat-123456789"}

    [args] = box.created
    assert args[:2] == ["run", "-d"] and args[-1] == napcat.DEFAULT_IMAGE
    assert args[args.index("--hostname") + 1] == "napcat-123456789"
    assert args[args.index("--mac-address") + 1] == napcat.mac_for("123456789")
    ports = [args[n + 1] for n, a in enumerate(args) if a == "-p"]
    assert ports == [
        "127.0.0.1:6100:6099",
        "127.0.0.1:3046:3040",
        "127.0.0.1:3047:3041",
    ]  # next to the first three
    assert "ACCOUNT=123456789" in args  # logs back in by itself after a restart

    config = json.loads(
        (tmp_path / "qq" / "napcat" / "123456789" / "config" / "onebot11.json").read_text("utf-8")
    )
    secrets = env_file(tmp_path)
    assert config["network"]["httpServers"][0]["token"] == secrets.get("NEST_QQ_123456789_HTTP_TOKEN")
    assert config["network"]["websocketServers"][0]["token"] == secrets.get("NEST_QQ_123456789_WS_TOKEN")
    assert len(secrets.get("NEST_QQ_123456789_HTTP_TOKEN")) >= 24

    [bot] = (await http.get("/api/bots")).json()
    assert (bot["name"], bot["mode"], bot["aliases"]) == ("小海豚", "observe", ["小海豚", "豚豚"])
    [conn] = (await http.get("/api/accounts/connections")).json()
    assert (conn["http_url"], conn["ws_url"]) == ("http://127.0.0.1:3046", "ws://127.0.0.1:3047")
    system = (await http.get("/api/system")).json()
    assert [c["name"] for c in system["containers"]] == ["napcat-bot1", "napcat-123456789"]
    again = await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789"})
    assert again.status_code == 409


async def test_a_removed_bot_keeps_its_data_and_comes_back(
    http: httpx.AsyncClient, box: FakeDocker, tmp_path: Path
) -> None:
    await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789"})
    response = await http.delete("/api/bots/qq:123456789")
    assert response.json() == {"ok": True, "container": "napcat-123456789", "problem": ""}
    assert box.removed == ["napcat-123456789"]
    assert (await http.get("/api/bots")).json() == [] and (
        await http.get("/api/accounts/connections")
    ).json() == []
    assert (tmp_path / "qq" / "napcat" / "123456789" / "config" / "onebot11.json").is_file()  # data stays
    system = (await http.get("/api/system")).json()
    assert [c["name"] for c in system["containers"]] == ["napcat-bot1"]
    assert (await http.delete("/api/bots/qq:123456789")).status_code == 404

    back = await http.post("/api/bots", json={"name": "回来了", "self_id": "123456789"})
    assert back.status_code == 201
    assert (
        box.created[0][box.created[0].index("--mac-address") + 1]
        == box.created[1][box.created[1].index("--mac-address") + 1]
    )  # the same identity, so the saved login is still good
    [bot] = (await http.get("/api/bots")).json()
    assert bot["name"] == "回来了"


async def test_an_existing_container_without_a_known_account_is_not_taken_over(
    http: httpx.AsyncClient,
) -> None:
    response = await http.post("/api/bots", json={"name": "阿鲸", "self_id": "900000001"})
    assert response.status_code == 409 and "napcat-bot1" in response.json()["detail"]


async def test_a_stopped_container_holds_its_connection(http: httpx.AsyncClient, box: FakeDocker) -> None:
    assert (await http.post("/api/bots", json={"name": "阿克", "self_id": "123456789"})).status_code == 201
    hub = http._transport.app.state.hub  # type: ignore[attr-defined]
    assert "qq:123456789" in hub.adapters

    async def state() -> dict[str, str]:
        views = (await http.get("/api/accounts")).json()
        return next(v["status"] for v in views if v["id"] == "qq:123456789")

    box.boxes["napcat-123456789"] = replace(box.boxes["napcat-123456789"], running=False, status="exited")
    assert (await http.post("/api/system/snowluma/stop", params={"container": "napcat-123456789"})).is_success
    assert "qq:123456789" not in hub.adapters  # not retried while the container is stopped
    status = await state()
    assert status["state"] == "stopped" and "QQ 容器已停止" in status["detail"]

    box.boxes["napcat-123456789"] = replace(box.boxes["napcat-123456789"], running=True, status="running")
    assert (
        await http.post("/api/system/snowluma/start", params={"container": "napcat-123456789"})
    ).is_success
    assert "qq:123456789" in hub.adapters and "QQ 容器已停止" not in (await state())["detail"]

    guard = http._transport.app.state.qr_guard  # type: ignore[attr-defined]
    box.boxes["napcat-123456789"] = replace(box.boxes["napcat-123456789"], running=False, status="exited")
    await guard.check()  # stopped by hand, outside nest: held at the next check
    assert (await state())["state"] == "stopped"
    box.boxes["napcat-123456789"] = replace(box.boxes["napcat-123456789"], running=True, status="running")
    await guard.check()
    assert "qq:123456789" in hub.adapters


@pytest.mark.parametrize(
    ("hours", "zone"),
    [(8, "Etc/GMT-8"), (-5, "Etc/GMT+5"), (0, "UTC"), (5.5, "UTC"), (14, "Etc/GMT-14")],
)
def test_an_offset_without_a_zoneinfo_link_names_an_etc_zone(hours: float, zone: str) -> None:
    assert napcat.offset_zone(timedelta(hours=hours)) == zone


def test_the_console_can_live_apart_from_the_data(tmp_path: Path) -> None:
    built = tmp_path / "image" / "dist"
    assert Settings(home=tmp_path, _env_file=None).web_dist == tmp_path / "web" / "dist"
    assert Settings(home=tmp_path, web_dist_dir=built, _env_file=None).web_dist == built


# -- the image, and QQ on an endpoint someone runs ------------------------------------------------------


async def test_new_containers_use_the_image_set_on_the_platform_page(
    http: httpx.AsyncClient, box: FakeDocker
) -> None:
    system = (await http.get("/api/system")).json()
    assert (system["qq"]["napcat_image"], system["qq"]["napcat_image_default"]) == (
        napcat.DEFAULT_IMAGE,
        True,
    )
    bad = await http.patch("/api/system/settings", json={"napcat_image": "Napcat; rm -rf /"})
    assert bad.status_code == 422
    mine = "registry.local:5000/me/napcat:4.18.4"
    system = (await http.patch("/api/system/settings", json={"napcat_image": f" {mine} "})).json()
    assert (system["qq"]["napcat_image"], system["qq"]["napcat_image_default"]) == (mine, False)
    assert (await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789"})).status_code == 201
    assert box.created[0][-1] == mine
    system = (await http.patch("/api/system/settings", json={"napcat_image": ""})).json()
    assert system["qq"]["napcat_image"] == napcat.DEFAULT_IMAGE  # empty: back to the default


async def test_an_image_docker_cannot_get_says_what_to_do(
    http: httpx.AsyncClient, box: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(args: list[str]) -> None:
        raise docker.ImageMissing("docker 出错：Error response from daemon: pull access denied")

    monkeypatch.setattr(docker, "create", gone)
    response = await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert napcat.DEFAULT_IMAGE in detail and "下架" in detail and "接我自己运行的协议端" in detail
    assert (await http.get("/api/bots")).json() == []


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The protocol program someone runs: what nest asked it, and what it answers."""
    seen: dict[str, Any] = {"http": [], "ws": [], "login": "123456789"}

    async def check_http(url: str, token: str, *, self_id: str, transport: Any = None) -> probe.Result:
        seen["http"].append((url, token, self_id))
        if seen["login"] != self_id:
            return probe.Result(False, f"这个地址登录的是 QQ {seen['login']}，不是 {self_id}")
        return probe.Result(True, "连上了")

    async def check_ws(url: str, token: str) -> probe.Result:
        seen["ws"].append((url, token))
        return probe.Result(True, "WebSocket 连上了")

    monkeypatch.setattr(probe, "check_http", check_http)
    monkeypatch.setattr(probe, "check_ws", check_ws)
    return seen


OWN = {"http_url": "http://192.168.1.20:3000/", "ws_url": "ws://192.168.1.20:3001", "token": " t0ken "}


async def test_a_qq_bot_can_use_a_protocol_program_someone_runs(
    http: httpx.AsyncClient, box: FakeDocker, endpoint: dict[str, Any], tmp_path: Path
) -> None:
    # whichever way QQ is set to run: this does not need nest's containers
    assert (await http.patch("/api/system/settings", json={"snowluma_mode": "program"})).status_code == 200
    body = {"name": "小海豚", "self_id": "123456789", "onebot": OWN}
    response = await http.post("/api/bots", json=body)
    assert response.status_code == 201, response.text
    assert response.json() == {"bot_id": "qq:123456789", "container": None}
    assert box.created == []
    assert endpoint["http"] == [("http://192.168.1.20:3000", "t0ken", "123456789")]
    assert endpoint["ws"] == [("ws://192.168.1.20:3001", "t0ken")]
    [conn] = (await http.get("/api/accounts/connections")).json()
    assert (conn["http_url"], conn["ws_url"], conn["http_token"], conn["ws_token"]) == (
        "http://192.168.1.20:3000",
        "ws://192.168.1.20:3001",
        True,
        True,
    )
    secrets = env_file(tmp_path)
    assert secrets.get("NEST_QQ_123456789_HTTP_TOKEN") == secrets.get("NEST_QQ_123456789_WS_TOKEN") == "t0ken"
    [bot] = (await http.get("/api/bots")).json()
    assert (bot["name"], bot["mode"]) == ("小海豚", "observe")


async def test_a_protocol_program_that_does_not_answer_for_the_number_is_not_kept(
    http: httpx.AsyncClient, endpoint: dict[str, Any]
) -> None:
    endpoint["login"] = "987654321"
    response = await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789", "onebot": OWN})
    assert response.status_code == 400
    assert response.json()["detail"] == "HTTP：这个地址登录的是 QQ 987654321，不是 123456789"
    assert (await http.get("/api/bots")).json() == []
    wrong = {**OWN, "http_url": "ftp://192.168.1.20"}
    response = await http.post("/api/bots", json={"name": "小海豚", "self_id": "123456789", "onebot": wrong})
    assert response.status_code == 422


async def test_removing_it_leaves_a_container_nest_does_not_list(
    http: httpx.AsyncClient, box: FakeDocker, endpoint: dict[str, Any]
) -> None:
    # someone's own NapCat container for the same number, not one nest made or was told about
    box.boxes["my-napcat"] = replace(WHALE, account="123456789")
    body = {"name": "小海豚", "self_id": "123456789", "onebot": OWN}
    assert (await http.post("/api/bots", json=body)).status_code == 201
    response = await http.delete("/api/bots/qq:123456789")
    assert response.json() == {"ok": True, "container": None, "problem": ""}
    assert box.removed == [] and "my-napcat" in box.boxes
