"""SnowLuma as a Docker container (the Docker setup): the 系统 page API in docker mode, with Docker
faked, and nest.docker's reading of the docker command line, with the command faked."""

from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from nest import docker
from nest.app import create_app
from nest.config import Settings

# The real functions, bound at import: tests/conftest.py swaps the module's attributes for every test.
from nest.docker import act as real_act
from nest.docker import container as real_container
from nest.docker import login_log as real_login_log
from nest.docker import login_qr as real_login_qr
from nest.system import ProcessError
from tests.conftest import TOKEN

AUTH = {"authorization": f"Bearer {TOKEN}"}


@dataclass
class FakeDocker:
    exists: bool = True
    problem: str = ""
    fail: str = ""
    states: dict[str, bool] = field(default_factory=lambda: {"snowluma": False})  # container: running
    ports: dict[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    accounts: dict[str, str] = field(default_factory=dict)  # container: the QQ number in its ACCOUNT
    qr: dict[str, tuple[bytes, int] | None] = field(default_factory=dict)  # container: (PNG, age in seconds)
    logs: dict[str, str] = field(default_factory=dict)  # container: the end of its output
    calls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.states["snowluma"]

    @running.setter
    def running(self, value: bool) -> None:
        self.states["snowluma"] = value

    def container(self, name: str) -> docker.Container:
        if self.problem:
            return docker.Container(False, problem=self.problem)
        if not self.exists or name not in self.states:
            return docker.Container(False)
        running = self.states[name]
        return docker.Container(
            True,
            running=running,
            status="running" if running else "exited",
            ports=self.ports.get(name, ()),
            account=self.accounts.get(name, ""),
        )

    def login_qr(self, name: str) -> tuple[bytes, int] | None:
        if self.fail:
            raise ProcessError(self.fail)
        return self.qr.get(name)

    def login_log(self, name: str) -> str:
        return self.logs.get(name, "")

    def act(self, name: str, action: str) -> None:
        self.calls.append((action, name))
        if self.fail:
            raise ProcessError(self.fail)
        self.states[name] = action != "stop"


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(docker, "container", fake.container)
    monkeypatch.setattr(docker, "act", fake.act)
    monkeypatch.setattr(docker, "login_qr", fake.login_qr)
    monkeypatch.setattr(docker, "login_log", fake.login_log)
    return fake


@pytest.fixture
async def http(tmp_path: Path, box: FakeDocker) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            response = await client.patch("/api/system/settings", json={"snowluma_mode": "docker"})
            assert response.status_code == 200, response.text
            yield client


async def test_the_view_in_docker_mode(http: httpx.AsyncClient, box: FakeDocker) -> None:
    view = (await http.get("/api/system")).json()
    assert view["snowluma"]["mode"] == "docker"
    assert view["snowluma"]["container"] == "snowluma"
    assert view["snowluma"]["configured"] is True and view["snowluma"]["running"] is False
    assert view["snowluma"]["status"] == "exited" and view["snowluma"]["problem"] == ""
    assert view["qq"]["login_url"] == "http://127.0.0.1:6081"
    assert view["qq"]["clients"] is None  # inside the container
    assert view["containers"] == [
        {
            "name": "snowluma",
            "found": True,
            "running": False,
            "status": "exited",
            "problem": "",
            "login_url": "http://127.0.0.1:6081",  # Docker did not say: the saved address
            "qr_login": False,  # not NapCat: scanned on its remote desktop
            "self_id": "",
            "password_set": False,
        }
    ]
    box.running = True
    assert (await http.get("/api/system")).json()["snowluma"]["running"] is True


async def test_a_napcat_container_per_account(http: httpx.AsyncClient, box: FakeDocker) -> None:
    box.states = {"napcat-bot1": True, "napcat-bot2": False, "napcat-bot3": True}
    box.ports = {
        "napcat-bot1": (("3040/tcp", 3042), ("6099/tcp", 6098)),
        "napcat-bot2": (("6099/tcp", 6099),),
    }
    response = await http.patch(
        "/api/system/settings",
        json={"snowluma_container": "napcat-bot1,napcat-bot2， napcat-bot3 napcat-bot2"},
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["snowluma"]["container"] == "napcat-bot1, napcat-bot2, napcat-bot3"
    assert view["snowluma"]["configured"] is True and view["snowluma"]["running"] is False  # pony is stopped
    assert [(c["name"], c["running"], c["login_url"], c["qr_login"]) for c in view["containers"]] == [
        ("napcat-bot1", True, "http://127.0.0.1:6098/webui", True),
        ("napcat-bot2", False, "http://127.0.0.1:6099/webui", True),
        ("napcat-bot3", True, "http://127.0.0.1:6081", False),  # no ports known: the saved address
    ]

    assert (await http.post("/api/system/snowluma/start")).json() == {"ok": True, "started": True}
    assert box.calls == [("start", "napcat-bot2")]  # the others were running
    assert (await http.post("/api/system/snowluma/restart", params={"container": "napcat-bot3"})).json()["ok"]
    assert box.calls[-1] == ("restart", "napcat-bot3")
    response = await http.post("/api/system/snowluma/stop", params={"container": "snowluma"})
    assert response.status_code == 400 and "不是「系统」页设置里的 QQ 容器" in response.json()["detail"]
    assert (await http.post("/api/system/snowluma/stop")).json() == {"ok": True, "stopped": True}
    assert box.calls[-3:] == [("stop", "napcat-bot1"), ("stop", "napcat-bot2"), ("stop", "napcat-bot3")]

    too_many = ",".join(f"q{i}" for i in range(9))
    response = await http.patch("/api/system/settings", json={"snowluma_container": too_many})
    assert response.status_code == 422 and "最多 8 个容器" in response.json()["detail"]


PNG = b"\x89PNG\r\n\x1a\nqr"


async def test_the_login_qr_code_in_the_console(http: httpx.AsyncClient, box: FakeDocker) -> None:
    box.states = {"napcat-bot1": True, "napcat-bot2": False}
    box.ports = {"napcat-bot1": (("6099/tcp", 6098),)}
    box.accounts = {"napcat-bot1": "900000001"}
    await http.patch("/api/system/settings", json={"snowluma_container": "napcat-bot1, napcat-bot2"})

    async def login(name: str) -> dict[str, Any]:
        response = await http.get("/api/system/qq/login", params={"container": name})
        assert response.status_code == 200, response.text
        return response.json()

    box.qr = {"napcat-bot1": (PNG, 20)}  # QQ waits for a scan
    view = await login("napcat-bot1")
    assert view["self_id"] == "900000001" and view["logged_in"] is False
    assert view["qr"] == "data:image/png;base64,iVBORw0KGgpxcg=="
    assert "用 900000001 的手机 QQ 扫码" in view["detail"]  # no such account in nest: its number
    box.qr = {"napcat-bot1": (PNG, 400)}  # an old code: QQ is not showing it any more
    view = await login("napcat-bot1")
    assert view["qr"] is None and "没有在等扫码" in view["detail"]
    scanned = (
        "an earlier start\n"
        "NapCat Shell App Loading...\n"
        "01-01 00:39:57 [\x1b[33mwarn\x1b[39m] 请扫描下面的二维码，然后在手Q上授权登录：\n"
        "01-01 00:40:11 [\x1b[32minfo\x1b[39m] [Core] [Login] 二维码已被扫描，等待确认...\n"
    )
    failed = "01-01 00:40:13 [\x1b[31merror\x1b[39m] [Core] [Login] Login Error,ErrType:  1  ErrCode: 5\n"
    box.logs["napcat-bot1"] = scanned + failed  # QQ refused the scan: the old code means nothing now
    for age in (20, 400):
        box.qr = {"napcat-bot1": (PNG, age)}
        view = await login("napcat-bot1")
        assert view["qr"] is None
        assert view["detail"].startswith("900000001 没登上：扫码后 QQ 没让登录（1 ErrCode: 5）")
        assert "「重启」" in view["detail"] and "已经登上" not in view["detail"]
    expired = "01-01 00:42:00 [\x1b[31merror\x1b[39m] [Core] [Login] Login Error,ErrType:  1  ErrCode: 3\n"
    box.logs["napcat-bot1"] = scanned + expired  # ran out unscanned: NapCat brings a new one itself
    box.qr = {"napcat-bot1": (PNG, 20)}
    assert (await login("napcat-bot1"))["qr"]
    box.logs["napcat-bot1"] = scanned + failed + "01-01 00:50:00 请扫描下面的二维码，然后在手Q上授权登录：\n"
    assert (await login("napcat-bot1"))["qr"]  # a new code since
    box.logs["napcat-bot1"] = scanned + failed + "NapCat Shell App Loading...\n"
    assert (await login("napcat-bot1"))["qr"]  # restarted: that failure was before
    box.logs.pop("napcat-bot1")
    box.qr = {}  # none since the container started: logging in with the saved login
    assert "保存的登录" in (await login("napcat-bot1"))["detail"]
    box.logs["napcat-bot1"] = (  # stuck: the saved login was refused and the 回退密码 needs an SMS code
        "an earlier start\n"
        "NapCat Shell App Loading...\n"
        "01-01 01:00:00 [\x1b[31merror\x1b[39m] 快速登录错误： 你的用户身份已失效，请你重新登录。\n"
        "01-01 01:00:00 [\x1b[32minfo\x1b[39m] 正在密码登录  900000001\n"
        "01-01 01:00:00 [\x1b[32minfo\x1b[39m] 需要验证码, proofWaterUrl:  https://ti.qq.com/safe/x?sid=1\n"
    )
    detail = (await login("napcat-bot1"))["detail"]
    assert detail.startswith("900000001 没登上：保存的登录用不了（QQ 说：你的用户身份已失效")
    assert "短信验证码" in detail and "「清除」" in detail and "ti.qq.com" not in detail
    box.logs["napcat-bot1"] = "NapCat Shell App Loading...\n正在快速登录 900000001\n"
    assert "可能正在用保存的登录自己登录" in (await login("napcat-bot1"))["detail"]
    view = await login("napcat-bot2")
    assert view["qr"] is None and "没有在运行" in view["detail"] and view["running"] is False

    response = await http.get("/api/system/qq/login", params={"container": "snowluma"})
    assert response.status_code == 400 and "不是「系统」页设置里的 QQ 容器" in response.json()["detail"]
    box.fail = "docker 出错：boom"
    response = await http.get("/api/system/qq/login", params={"container": "napcat-bot1"})
    assert response.status_code == 500 and response.json()["detail"] == "docker 出错：boom"


async def test_a_qq_waiting_for_a_scan_nobody_makes_is_stopped(
    http: httpx.AsyncClient, box: FakeDocker
) -> None:
    box.states = {"napcat-bot1": True, "napcat-bot2": True, "napcat-bot3": True}
    box.ports = {name: (("6099/tcp", 6098),) for name in box.states}
    box.accounts = {"napcat-bot1": "900000001", "napcat-bot2": "900000002", "napcat-bot3": "900000003"}
    await http.patch(
        "/api/system/settings", json={"snowluma_container": "napcat-bot1, napcat-bot2, napcat-bot3"}
    )
    guard = http._transport.app.state.qr_guard  # type: ignore[attr-defined]
    box.qr = {"napcat-bot2": (PNG, 30), "napcat-bot3": (PNG, 30), "napcat-bot1": (PNG, 400)}

    assert await guard.check() == []  # first seen waiting: left a check to settle
    await http.get("/api/system/qq/login", params={"container": "napcat-bot3"})  # the console shows it
    assert await guard.check() == ["napcat-bot2"]
    assert box.calls == [("stop", "napcat-bot2")] and box.states["napcat-bot2"] is False
    assert await guard.check() == []  # dragon is being scanned; whale's code is old (logged in)

    box.qr["napcat-bot2"] = (PNG, 10)  # started again from the console: waits, but someone looks
    box.states["napcat-bot2"] = True
    view = (await http.get("/api/system/qq/login", params={"container": "napcat-bot2"})).json()
    assert view["running"] is True and view["qr"]
    assert await guard.check() == [] and await guard.check() == []


async def test_the_login_qr_code_once_the_account_is_connected(tmp_path: Path, box: FakeDocker) -> None:
    box.states = {"napcat-bot1": True}
    box.accounts = {"napcat-bot1": "900000001"}
    box.qr = {"napcat-bot1": (PNG, 5)}  # still fresh right after the scan
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        status = {"state": "connected", "detail": ""}
        connected = {"id": "qq:900000001", "self_id": "900000001", "name": "阿鲸", "status": status}
        app.state.hub.account_views = lambda: [connected]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as client:
            body = {"snowluma_mode": "docker", "snowluma_container": "napcat-bot1"}
            assert (await client.patch("/api/system/settings", json=body)).status_code == 200
            view = (await client.get("/api/system/qq/login", params={"container": "napcat-bot1"})).json()
    assert view["logged_in"] is True and view["qr"] is None and view["name"] == "阿鲸"
    assert view["detail"] == "阿鲸 已经登录，nest 也连上了"


async def test_start_stop_restart_the_container(http: httpx.AsyncClient, box: FakeDocker) -> None:
    assert (await http.post("/api/system/snowluma/start")).json() == {"ok": True, "started": True}
    assert (await http.post("/api/system/snowluma/start")).json() == {"ok": True, "started": False}
    assert (await http.post("/api/system/snowluma/restart")).json() == {"ok": True}
    assert (await http.post("/api/system/snowluma/stop")).json() == {"ok": True, "stopped": True}
    assert (await http.post("/api/system/snowluma/stop")).json() == {"ok": True, "stopped": False}
    assert box.calls == [("start", "snowluma"), ("restart", "snowluma"), ("stop", "snowluma")]


async def test_docker_problems_are_one_sentence(http: httpx.AsyncClient, box: FakeDocker) -> None:
    box.exists = False
    response = await http.post("/api/system/snowluma/start")
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Docker 里没有叫 snowluma 的容器")
    box.exists, box.problem = True, "Docker 没有在运行：请先打开 OrbStack 或 Docker Desktop"
    response = await http.post("/api/system/snowluma/restart")
    assert response.status_code == 400 and response.json()["detail"] == box.problem
    assert (await http.get("/api/system")).json()["snowluma"]["problem"] == box.problem
    box.problem, box.fail = "", "docker 出错：boom"
    response = await http.post("/api/system/snowluma/start")
    assert response.status_code == 500 and response.json()["detail"].startswith("docker 出错：boom")


async def test_qq_is_not_opened_on_this_machine(http: httpx.AsyncClient) -> None:
    response = await http.post("/api/system/qq/open")
    assert response.status_code == 400
    assert "扫码登录" in response.json()["detail"]


async def test_docker_settings(http: httpx.AsyncClient, box: FakeDocker) -> None:
    response = await http.patch(
        "/api/system/settings",
        json={"snowluma_container": " nest-snowluma ", "qq_login_url": "http://mac:6081/"},
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["snowluma"]["container"] == "nest-snowluma"
    assert view["snowluma"]["configured"] is False  # no such container (the fake only has "snowluma")
    assert view["qq"]["login_url"] == "http://mac:6081"
    for body, problem in (
        ({"snowluma_container": "rm -rf /"}, "容器名只能用"),
        ({"snowluma_container": ""}, "容器名只能用"),
        ({"qq_login_url": "javascript:alert(1)"}, "扫码页地址"),
        ({"qq_login_url": "ftp://mac/"}, "扫码页地址"),
        ({"snowluma_mode": "kubernetes"}, ""),
        ({"snowluma_mode": None}, "没有要改的内容"),
    ):
        response = await http.patch("/api/system/settings", json=body)
        assert response.status_code == 422, body
        assert problem in response.json()["detail"]
    response = await http.patch("/api/system/settings", json={"snowluma_mode": "program"})
    assert response.json()["snowluma"]["mode"] == "program"
    assert response.json()["qq"]["login_url"] == ""


# -- nest.docker, with the command line faked ------------------------------------------------------------


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Queue answers for subprocess.run inside nest.docker; records the commands."""
    answers: list[Any] = []
    commands: list[list[str]] = []

    def fake(args: list[str], **kwargs: Any) -> Any:
        commands.append(args)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        code, out, err = answer
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(docker.subprocess, "run", fake)
    monkeypatch.setattr(docker, "cli", lambda: "/usr/local/bin/docker")
    answers.append(commands)  # first item: where the test can read the commands
    return answers


def test_reading_a_running_container(run: list[Any]) -> None:
    commands = run.pop(0)
    state = {
        "Status": "running",
        "Running": True,
        "StartedAt": "2026-10-27T01:00:00Z",
        "Health": {"Status": "healthy"},
    }
    ports = {
        "3040/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3042"}],
        "6099/tcp": [{"HostIp": "127.0.0.1", "HostPort": "6098"}],
        "9999/tcp": None,
    }
    env = ["WEBUI_TOKEN=secret", "ACCOUNT=900000001", "TZ=UTC"]
    run.append((0, f"{json.dumps(state)}\t{json.dumps(ports)}\t{json.dumps(env)}\n", ""))
    found = real_container("napcat-bot1")
    assert found == docker.Container(
        True,
        True,
        "running",
        "2026-10-27T01:00:00Z",
        "healthy",
        ports=(("3040/tcp", 3042), ("6099/tcp", 6098)),
        account="900000001",  # and nothing else from the environment
    )
    assert commands == [
        [
            "/usr/local/bin/docker",
            "inspect",
            "--type",
            "container",
            "--format",
            docker.INSPECT_FORMAT,
            "napcat-bot1",
        ]
    ]
    run.append((0, json.dumps({"Status": "exited", "Running": False}) + "\tnull\n", ""))
    assert real_container("napcat-bot1") == docker.Container(True, False, "exited")

    # A compose service with a 回退密码: that it has one (never the value) and where compose made it.
    env = ["ACCOUNT=900000001", "NAPCAT_QUICK_PASSWORD_MD5="]
    folder = "/srv/nest/deploy/mac"
    labels = {
        "com.docker.compose.project": "mac",
        "com.docker.compose.service": "napcat-bot1",
        "com.docker.compose.project.working_dir": folder,
        "com.docker.compose.project.config_files": f"{folder}/docker-compose.yml",
    }
    answer = f"{json.dumps(state)}\tnull\t{json.dumps(env)}\t{json.dumps(labels)}\n"
    run.append((0, answer, ""))
    found = real_container("napcat-bot1")
    assert found.quick_password is False  # set but empty: none
    assert found.compose == docker.Compose(
        "mac",
        "napcat-bot1",
        "/srv/nest/deploy/mac",
        ("/srv/nest/deploy/mac/docker-compose.yml",),
    )
    env[1] = "NAPCAT_QUICK_PASSWORD_MD5=0123456789abcdef0123456789abcdef"
    run.append((0, f"{json.dumps(state)}\tnull\t{json.dumps(env)}\tnull\n", ""))
    found = real_container("napcat-bot1")
    assert found.quick_password is True and found.compose is None


def test_reading_the_login_qr_code(run: list[Any]) -> None:
    commands = run.pop(0)
    run.append((0, b"42\n" + PNG, b""))
    assert real_login_qr("napcat-bot1") == (PNG, 42)
    assert commands[0][:5] == ["/usr/local/bin/docker", "exec", "napcat-bot1", "sh", "-c"]
    assert docker.QR_PATH in commands[0][5]
    run.append((3, b"", b""))  # no QR code saved
    assert real_login_qr("napcat-bot1") is None
    run.append((0, b"42\nnot a picture", b""))
    with pytest.raises(ProcessError, match="看不懂"):
        real_login_qr("napcat-bot1")
    run.append((1, b"", b"Error response from daemon: container is not running"))
    with pytest.raises(ProcessError, match="docker 出错"):
        real_login_qr("napcat-bot1")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((1, "", "Error: No such object: snowluma"), docker.Container(False)),
        (
            (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."),
            docker.Container(False, problem="Docker 没有在运行：请先打开 OrbStack 或 Docker Desktop"),
        ),
        ((0, "not json", ""), docker.Container(False, problem="看不懂 docker 的回答")),
        (
            subprocess.TimeoutExpired("docker", 10),
            docker.Container(False, problem="Docker 没有响应（超过 10 秒）"),
        ),
    ],
)
def test_reading_problems(run: list[Any], answer: Any, expected: docker.Container) -> None:
    run.pop(0)
    run.append(answer)
    assert real_container("snowluma") == expected


def test_no_docker_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "cli", lambda: None)
    assert real_container("snowluma").problem.startswith("找不到 docker 命令")
    with pytest.raises(ProcessError, match="找不到 docker 命令"):
        real_act("snowluma", "start")


def test_actions(run: list[Any]) -> None:
    commands = run.pop(0)
    run.extend([(0, "snowluma\n", ""), (1, "", "Error response from daemon: No such container: snowluma")])
    real_act("snowluma", "restart")
    assert commands == [["/usr/local/bin/docker", "restart", "snowluma"]]
    with pytest.raises(ProcessError, match="没有叫 snowluma 的容器"):
        real_act("snowluma", "start")
    with pytest.raises(ValueError):
        real_act("snowluma", "rm")


def test_the_command_line_is_found_outside_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orbstack = tmp_path / "docker"
    orbstack.write_text("")
    monkeypatch.setattr(docker.shutil, "which", lambda name: None)
    monkeypatch.setattr(docker, "KNOWN_PLACES", (str(tmp_path / "missing"), str(orbstack)))
    assert docker.cli() == str(orbstack)
    monkeypatch.setattr(docker, "KNOWN_PLACES", ())
    assert docker.cli() is None


@pytest.mark.parametrize(
    ("name", "valid"),
    [("snowluma", True), ("nest_snow.luma-2", True), ("-x", False), ("a b", False), ("", False)],
)
def test_container_names(name: str, valid: bool) -> None:
    assert docker.valid_name(name) is valid


def test_reading_a_containers_log(run: list[Any]) -> None:
    commands = run.pop(0)
    run.append((0, "NapCat Shell App Loading...\n", None))
    assert real_login_log("napcat-bot1") == "NapCat Shell App Loading...\n"
    assert commands[0] == ["/usr/local/bin/docker", "logs", "--tail", "400", "napcat-bot1"]
    run.append((1, "Error response from daemon: No such container: napcat-x", None))
    with pytest.raises(ProcessError):
        real_login_log("napcat-x")
