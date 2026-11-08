"""回退密码 on the 系统 page: only the password's MD5 is kept; a compose container gets it through the compose
project's .env and `docker compose up -d`, one nest made is run again with it, with the same ports and
identity. Docker is faked."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from nest import docker, napcat
from nest.app import create_app
from nest.config import Settings
from nest.system import ProcessError
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}
MD5 = hashlib.md5("秘密 pass".encode()).hexdigest()
NAPCAT_PORTS = (("3040/tcp", 3046), ("3041/tcp", 3047), ("6099/tcp", 6100))


@dataclass
class FakeDocker:
    compose_dir: Path
    boxes: dict[str, docker.Container] = field(default_factory=dict)
    calls: list[tuple[str, object]] = field(default_factory=list)
    fail: str = ""

    def container(self, name: str) -> docker.Container:
        return self.boxes.get(name, docker.Container(False))

    def compose_up(self, compose: docker.Compose) -> None:
        self.calls.append(("compose", compose.service))
        if self.fail:
            raise ProcessError(self.fail)

    def remove(self, name: str) -> None:
        self.calls.append(("rm", name))

    def create(self, args: list[str]) -> None:
        self.calls.append(("run", args))
        if self.fail:
            raise ProcessError(self.fail)


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeDocker:
    compose_dir = tmp_path / "deploy"
    compose_dir.mkdir()
    passed_on = 'NAPCAT_QUICK_PASSWORD_MD5: "${NAPCAT_PASSWORD_MD5_900000001:-}"'
    (compose_dir / "docker-compose.yml").write_text(
        f'environment: {{ ACCOUNT: "900000001", {passed_on} }}\n', encoding="utf-8"
    )
    (compose_dir / ".env").write_text('NAPCAT_WEBUI_TOKEN="keep me"\n', encoding="utf-8")
    compose = docker.Compose("mac", "napcat-bot1", str(compose_dir), ("docker-compose.yml",))
    fake = FakeDocker(compose_dir)
    fake.boxes = {
        "napcat-bot1": docker.Container(
            True, running=True, ports=NAPCAT_PORTS, account="900000001", compose=compose
        ),
        "napcat-123456789": docker.Container(True, running=True, ports=NAPCAT_PORTS, account="123456789"),
        "snowluma": docker.Container(True, running=True, ports=(("6081/tcp", 6081),)),
    }
    for name in ("container", "compose_up", "remove", "create"):
        monkeypatch.setattr(docker, name, getattr(fake, name))
    return fake


@pytest.fixture
async def http(tmp_path: Path, box: FakeDocker) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(home=tmp_path / "nest", port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        app.state.secrets.set("NEST_NAPCAT_123456789_WEBUI_TOKEN", "webui")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            body = {
                "snowluma_mode": "docker",
                "snowluma_container": "napcat-bot1, napcat-123456789, snowluma",
            }
            assert (await client.patch("/api/system/settings", json=body)).status_code == 200
            yield client


async def put(http: httpx.AsyncClient, container: str, password: str) -> httpx.Response:
    return await http.put("/api/system/qq/password", json={"container": container, "password": password})


async def test_a_compose_container_gets_it_through_its_env_file(
    http: httpx.AsyncClient, box: FakeDocker
) -> None:
    response = await put(http, "napcat-bot1", "秘密 pass")
    assert response.json() == {"ok": True, "password_set": True}
    assert box.calls == [("compose", "napcat-bot1")]
    env = dotenv_values(box.compose_dir / ".env")
    assert env == {
        "NAPCAT_WEBUI_TOKEN": "keep me",
        "NAPCAT_PASSWORD_MD5_900000001": MD5,
    }  # never the password

    assert (await put(http, "napcat-bot1", "")).json()["password_set"] is False
    assert dotenv_values(box.compose_dir / ".env") == {"NAPCAT_WEBUI_TOKEN": "keep me"}

    box.fail = "docker 出错：boom"  # the container could not be made again: .env as it was
    response = await put(http, "napcat-bot1", "新的")
    assert response.status_code == 500 and "重建容器失败" in response.json()["detail"]
    assert dotenv_values(box.compose_dir / ".env") == {"NAPCAT_WEBUI_TOKEN": "keep me"}


async def test_a_compose_file_that_does_not_pass_it_on(http: httpx.AsyncClient, box: FakeDocker) -> None:
    (box.compose_dir / "docker-compose.yml").write_text('environment: { ACCOUNT: "900000001" }\n', "utf-8")
    response = await put(http, "napcat-bot1", "x")
    assert response.status_code == 409 and "还没有接回退密码" in response.json()["detail"]
    assert box.calls == []


async def test_a_container_nest_made_is_run_again_with_it(
    http: httpx.AsyncClient, box: FakeDocker, tmp_path: Path
) -> None:
    response = await put(http, "napcat-123456789", "秘密 pass")
    assert response.json() == {"ok": True, "password_set": True}
    (rm, name), (run, args) = box.calls
    assert (rm, name, run) == ("rm", "napcat-123456789", "run")
    assert isinstance(args, list)
    assert f"NAPCAT_QUICK_PASSWORD_MD5={MD5}" in args
    assert args[args.index("--mac-address") + 1] == napcat.mac_for("123456789")
    assert [args[n + 1] for n, a in enumerate(args) if a == "-p"] == [
        "127.0.0.1:6100:6099",
        "127.0.0.1:3046:3040",
        "127.0.0.1:3047:3041",
    ]  # the same ports
    secrets = dotenv_values(tmp_path / "nest" / ".env")
    assert secrets["NEST_NAPCAT_123456789_PASSWORD_MD5"] == MD5

    box.calls.clear()
    box.fail = "docker 出错：boom"
    response = await put(http, "napcat-123456789", "")
    assert response.status_code == 500
    assert [c[0] for c in box.calls] == ["rm", "run", "run"]  # tried again with the old setting
    assert f"NAPCAT_QUICK_PASSWORD_MD5={MD5}" in box.calls[-1][1]  # type: ignore[operator]
    assert dotenv_values(tmp_path / "nest" / ".env")["NEST_NAPCAT_123456789_PASSWORD_MD5"] == MD5


async def test_only_napcat_containers_have_one(http: httpx.AsyncClient, box: FakeDocker) -> None:
    response = await put(http, "snowluma", "x")
    assert response.status_code == 400 and "不是一个号的 NapCat 容器" in response.json()["detail"]
    response = await put(http, "napcat-unknown", "x")
    assert response.status_code == 400  # not one of the 系统 page's containers
    assert (await put(http, "napcat-bot1", "x" * 65)).status_code == 422
    containers = (await http.get("/api/system")).json()["containers"]
    assert [c["password_set"] for c in containers] == [False, False, False]
