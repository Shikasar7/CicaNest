"""The system API (SnowLuma and QQ paths, start/stop, the system view) and the live log.

Every process helper is a fake here: the real SnowLuma and QQ run on this machine and must never be
found, started or stopped by a test (tests/conftest.py guards the rest of the suite the same way).
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from fastapi import FastAPI

from nest import __version__, system
from nest.api import routes_system_admin
from nest.app import create_app
from nest.config import Settings
from nest.logs import LogBuffer
from nest.secrets import SecretReader
from nest.storage.accounts import upsert_account
from nest.storage.bots import BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

# The real helpers, bound before tests/conftest.py swaps the module's attributes for refusing fakes.
from nest.system import (
    ProcessError,
    open_qq,
    qq_clients,
    runs_snowluma,
    snowluma_pids,
    start_snowluma,
    stop_snowluma,
    tree,
)
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually, free_socket, serve, sse_events

TOKEN = "t"
AUTH = {"authorization": f"Bearer {TOKEN}"}
NOT_SET_SNOWLUMA = "还没有设置 SnowLuma 文件夹，请先在「系统」页填写"
NOT_SET_QQ = "还没有设置 QQ 程序的位置，请先在「系统」页填写"


def factory(config: ProviderConfig, secrets: SecretReader) -> FakeProvider:
    return FakeProvider(name=config.id, default="在")


def snowluma_folder(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "node.exe").write_bytes(b"")
    (path / "index.mjs").write_text("// SnowLuma", encoding="utf-8")
    return path


def qq_program(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


@dataclass
class Machine:
    """Fake processes: what nest.system would find, start and stop."""

    snowluma_dir: Path
    qq_exe: Path
    pids: list[int] = field(default_factory=list)
    port_open: bool = False
    clients: int = 0
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    start_error: str | None = None
    start_delay: float = 0.0
    next_pid: int = 4242

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(system, "DEFAULT_SNOWLUMA_DIR", str(self.snowluma_dir))
        monkeypatch.setattr(system, "DEFAULT_QQ_EXECUTABLE", str(self.qq_exe))
        monkeypatch.setattr(system, "snowluma_pids", self.snowluma_pids)
        monkeypatch.setattr(system, "webui_listening", lambda timeout=0.3: self.port_open or bool(self.pids))
        monkeypatch.setattr(system, "qq_clients", lambda: self.clients)
        monkeypatch.setattr(system, "start_snowluma", self.start_snowluma)
        monkeypatch.setattr(system, "stop_snowluma", self.stop_snowluma)
        monkeypatch.setattr(system, "open_qq", self.open_qq)

    def snowluma_pids(self, folder: str | Path) -> list[int]:
        return list(self.pids) if Path(folder) == self.snowluma_dir else []

    def start_snowluma(self, folder: str | Path, *, hide: Any = ()) -> int:
        self.calls.append(("start", str(folder), set(hide)))
        time.sleep(self.start_delay)  # runs in a worker thread, like the real one
        if self.start_error:
            raise ProcessError(self.start_error)
        pid = self.next_pid
        self.next_pid += 1
        self.pids.append(pid)
        return pid

    def stop_snowluma(self, pids: Any) -> bool:
        pids = list(pids)
        self.calls.append(("stop", pids))
        stopped = bool(set(pids) & set(self.pids))
        self.pids = [pid for pid in self.pids if pid not in pids]
        return stopped

    def open_qq(self, executable: str | Path, *, hide: Any = ()) -> None:
        self.calls.append(("open_qq", str(executable), set(hide)))

    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]


@dataclass
class Nest:
    http: httpx.AsyncClient
    app: FastAPI
    machine: Machine
    queue: asyncio.Queue

    @property
    def state(self) -> Any:
        return self.app.state

    def events(self, kind: str = "system") -> list[dict[str, Any]]:
        """Published events of one kind since the last call."""
        found = []
        while not self.queue.empty():
            item_kind, data = self.queue.get_nowait()
            if item_kind == kind:
                found.append(data)
        return found

    async def view(self) -> dict[str, Any]:
        response = await self.http.get("/api/system")
        assert response.status_code == 200, response.text
        return response.json()

    async def rows(self) -> list[tuple[str, str]]:
        return [
            (r["key"], r["value"]) for r in await self.state.db.fetchall("SELECT key, value FROM settings")
        ]


async def seed(home: Path) -> None:
    db = await Database.open(home / "data" / "nest.db")
    try:
        await upsert_account(
            db,
            platform="qq",
            self_id="10001",
            name="阿鲸",
            http_url="http://x",
            ws_url="ws://x/",
            http_token_ref="NEST_QQ_10001_TOKEN",
            ws_token_ref="QQ_WS_TOKEN",
            enabled=False,  # no adapter: its state is "stopped"
        )
        await BotRepo(db).upsert_provider(
            ProviderConfig("ark", "openai_chat", "http://a.example/v1", "m", "ARK_API_KEY")
        )
    finally:
        await db.close()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    fake = Machine(snowluma_folder(tmp_path / "SnowLuma"), qq_program(tmp_path / "Tencent" / "QQ.exe"))
    fake.install(monkeypatch)
    # The Windows setup unless a test says otherwise (on a Mac the default would be Docker).
    monkeypatch.setattr(routes_system_admin, "DEFAULT_MODE", "program")
    return fake


@pytest.fixture
async def nest(tmp_path: Path, machine: Machine, caplog: pytest.LogCaptureFixture) -> AsyncIterator[Nest]:
    caplog.set_level(logging.INFO, logger="nest")  # the live log shows what the log level lets through
    home = tmp_path / "home"
    home.mkdir()
    await seed(home)
    settings = Settings(home=home, port=18800, _env_file=None)
    app = create_app(settings, token=TOKEN, provider_factory=factory)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=settings.base_url, headers=AUTH) as http:
            yield Nest(http, app, machine, app.state.broadcaster.subscribe())


# -- settings ---------------------------------------------------------------------------------------


async def test_defaults_come_from_disk_and_are_not_stored(nest: Nest) -> None:
    view = await nest.view()
    assert view["snowluma"]["dir"] == str(nest.machine.snowluma_dir)
    assert view["snowluma"]["configured"] is True
    assert view["qq"] == {
        "executable": str(nest.machine.qq_exe),
        "configured": True,
        "clients": 0,
        "login_url": "",
    }
    assert await nest.rows() == []


async def test_defaults_follow_the_disk_until_saved(nest: Nest) -> None:
    for name in ("node.exe", "index.mjs"):
        (nest.machine.snowluma_dir / name).unlink()
    nest.machine.qq_exe.unlink()
    view = await nest.view()
    assert view["snowluma"] == {
        "mode": "program",
        "container": "",
        "status": "",
        "problem": "",
        "dir": "",
        "configured": False,
        "running": False,
        "pid": None,
        "webui_url": "http://127.0.0.1:5099",
    }
    assert view["qq"] == {"executable": "", "configured": False, "clients": 0, "login_url": ""}
    qq_program(nest.machine.qq_exe)  # the default appears: picked up on the next read
    assert (await nest.view())["qq"]["executable"] == str(nest.machine.qq_exe)


async def test_not_configured_actions_are_400(nest: Nest) -> None:
    for name in ("node.exe", "index.mjs"):
        (nest.machine.snowluma_dir / name).unlink()
    nest.machine.qq_exe.unlink()
    for action in ("start", "stop", "restart"):
        response = await nest.http.post(f"/api/system/snowluma/{action}")
        assert response.status_code == 400 and response.json() == {"detail": NOT_SET_SNOWLUMA}, action
    response = await nest.http.post("/api/system/qq/open")
    assert response.status_code == 400 and response.json() == {"detail": NOT_SET_QQ}
    assert nest.machine.calls == [] and nest.events() == []


async def test_a_saved_path_that_disappears_is_not_configured(nest: Nest, tmp_path: Path) -> None:
    other = snowluma_folder(tmp_path / "SnowLuma2")
    assert (
        await nest.http.patch("/api/system/settings", json={"snowluma_dir": str(other)})
    ).status_code == 200
    (other / "index.mjs").unlink()
    view = await nest.view()
    assert view["snowluma"]["dir"] == str(other) and view["snowluma"]["configured"] is False
    response = await nest.http.post("/api/system/snowluma/start")
    assert response.status_code == 400 and response.json() == {"detail": NOT_SET_SNOWLUMA}


async def test_saving_paths(nest: Nest, tmp_path: Path) -> None:
    other = snowluma_folder(tmp_path / "Apps" / "SnowLuma")
    qq = qq_program(tmp_path / "QQNT" / "QQ.exe")
    nest.events()
    # Pasted from Explorer: quotes and spaces around it.
    response = await nest.http.patch("/api/system/settings", json={"snowluma_dir": f'  "{other}" '})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["snowluma"]["dir"] == str(other) and view["snowluma"]["configured"] is True
    assert view["qq"]["executable"] == str(nest.machine.qq_exe)  # still the default, still not stored
    assert nest.events() == [{"change": "settings"}]
    assert [key for key, _ in await nest.rows()] == ["snowluma_dir"]

    response = await nest.http.patch(
        "/api/system/settings", json={"snowluma_dir": str(other), "qq_executable": str(qq)}
    )
    assert response.status_code == 200
    assert response.json()["qq"] == {"executable": str(qq), "configured": True, "clients": 0, "login_url": ""}
    assert dict(await nest.rows()) == {
        "snowluma_dir": f'"{other}"'.replace("\\", "\\\\"),
        "qq_executable": f'"{qq}"'.replace("\\", "\\\\"),
    }
    # A saved choice is kept even when the default would now point somewhere else.
    assert (await nest.view())["snowluma"]["dir"] == str(other)


async def test_settings_validation(nest: Nest, tmp_path: Path) -> None:
    half = tmp_path / "half"
    half.mkdir()
    (half / "node.exe").write_bytes(b"")
    text_file = tmp_path / "QQ.txt"
    text_file.write_text("", encoding="utf-8")
    cases = [
        ({}, "没有要改的内容"),
        ({"snowluma_dir": None, "qq_executable": None}, "没有要改的内容"),
        ({"snowluma_dir": "  "}, "请填写 SnowLuma 文件夹"),
        ({"snowluma_dir": "SnowLuma"}, r"SnowLuma 文件夹要填从盘符开始的完整路径，例如 D:\SnowLuma"),
        ({"snowluma_dir": str(half)}, f"这个文件夹里没有 SnowLuma（要有 node.exe 和 index.mjs）：{half}"),
        ({"snowluma_dir": f"{half}\n"}, f"这个文件夹里没有 SnowLuma（要有 node.exe 和 index.mjs）：{half}"),
        ({"snowluma_dir": f"{half}\x00x"}, "SnowLuma 文件夹里有不能保存的字符，或者太长了"),
        ({"snowluma_dir": "D:\\" + "x" * 600}, "SnowLuma 文件夹里有不能保存的字符，或者太长了"),
        ({"snowluma_dir": 5}, "SnowLuma 文件夹格式不对"),
        ({"qq_executable": ""}, "请填写 QQ 程序的位置"),
        ({"qq_executable": str(text_file)}, f"找不到 QQ 程序（要填 QQ.exe 的完整路径）：{text_file}"),
        (
            {"qq_executable": str(tmp_path / "nope.exe")},
            f"找不到 QQ 程序（要填 QQ.exe 的完整路径）：{tmp_path / 'nope.exe'}",
        ),
        ({"qq_executable": str(tmp_path)}, f"找不到 QQ 程序（要填 QQ.exe 的完整路径）：{tmp_path}"),
        # One bad field rejects the whole change.
        ({"snowluma_dir": str(nest.machine.snowluma_dir), "qq_executable": "QQ.exe"}, None),
        ({"other": 1}, "「other」不认识这个设置"),
    ]
    for body, detail in cases:
        response = await nest.http.patch("/api/system/settings", json=body)
        assert response.status_code == 422, (body, response.text)
        if detail is not None:
            assert response.json() == {"detail": detail}, body
    assert await nest.rows() == []
    assert nest.events() == []


async def test_the_fetch_proxy(nest: Nest) -> None:
    assert (await nest.view())["fetch_proxy"] == ""
    response = await nest.http.patch("/api/system/settings", json={"fetch_proxy": " http://127.0.0.1:7890/ "})
    assert response.status_code == 200, response.text
    assert response.json()["fetch_proxy"] == "http://127.0.0.1:7890"
    assert dict(await nest.rows()) == {"fetch_proxy": '"http://127.0.0.1:7890"'}
    assert nest.events() == [{"change": "settings"}]
    assert await SettingsRepo(nest.state.db).fetch_proxy() == "http://127.0.0.1:7890"
    for bad in (
        "socks5://127.0.0.1:1080",
        "http://user:secret@127.0.0.1:7890",
        "http://127.0.0.1",
        "http://127.0.0.1:7890/path",
        "127.0.0.1:7890",
        "http://[::1:7890",
    ):
        response = await nest.http.patch("/api/system/settings", json={"fetch_proxy": bad})
        assert response.status_code == 422, bad
        assert response.json() == {
            "detail": "外网代理要写成 http://主机:端口，例如 http://127.0.0.1:7890，留空表示不用代理"
        }
        assert "secret" not in response.text
    response = await nest.http.patch("/api/system/settings", json={"fetch_proxy": [1]})
    assert response.json() == {"detail": "外网代理格式不对"}
    # Cleared: no proxy.
    response = await nest.http.patch("/api/system/settings", json={"fetch_proxy": ""})
    assert response.status_code == 200 and response.json()["fetch_proxy"] == ""
    assert await SettingsRepo(nest.state.db).fetch_proxy() == ""


async def test_settings_change_holds_the_config_lock(nest: Nest) -> None:
    async with nest.state.config_lock:
        pending = asyncio.create_task(
            nest.http.patch("/api/system/settings", json={"snowluma_dir": str(nest.machine.snowluma_dir)})
        )
        await asyncio.sleep(0.1)
        assert not pending.done()
    assert (await pending).status_code == 200


# -- the system view --------------------------------------------------------------------------------


async def test_system_view(nest: Nest) -> None:
    nest.machine.pids = [4242, 4343]
    nest.machine.clients = 4
    view = await nest.view()
    assert set(view) == {"nest", "snowluma", "containers", "qq", "fetch_proxy", "offline_alert", "accounts"}
    assert view["fetch_proxy"] == ""
    assert set(view["nest"]) == {"version", "pid", "started_at", "uptime_seconds", "memory_mb"}
    assert view["nest"]["version"] == __version__ and view["nest"]["pid"] == os.getpid()
    assert view["nest"]["started_at"] == nest.state.started_at
    assert 0 <= view["nest"]["uptime_seconds"] < 60 and view["nest"]["memory_mb"] > 0
    assert view["snowluma"] == {
        "mode": "program",
        "container": "",
        "status": "",
        "problem": "",
        "dir": str(nest.machine.snowluma_dir),
        "configured": True,
        "running": True,
        "pid": 4242,
        "webui_url": "http://127.0.0.1:5099",
    }
    assert view["qq"] == {
        "executable": str(nest.machine.qq_exe),
        "configured": True,
        "clients": 4,
        "login_url": "",
    }
    assert view["accounts"] == [
        {"id": "qq:10001", "self_id": "10001", "name": "阿鲸", "state": "stopped", "detail": "已停用"}
    ]


async def test_running_when_only_the_webui_port_answers(nest: Nest) -> None:
    nest.machine.port_open = True  # e.g. an elevated SnowLuma whose command line nest cannot read
    snowluma = (await nest.view())["snowluma"]
    assert snowluma["running"] is True and snowluma["pid"] is None
    response = await nest.http.post("/api/system/snowluma/start")
    assert response.json() == {"ok": True, "started": False}
    assert nest.machine.calls == []


# -- SnowLuma and QQ --------------------------------------------------------------------------------


async def test_start_starts_once(nest: Nest) -> None:
    nest.events()
    response = await nest.http.post("/api/system/snowluma/start")
    assert response.status_code == 200 and response.json() == {"ok": True, "started": True}
    # Started from its folder, told which environment variables hold nest's secrets.
    assert nest.machine.calls == [
        ("start", str(nest.machine.snowluma_dir), {"ARK_API_KEY", "NEST_QQ_10001_TOKEN", "QQ_WS_TOKEN"})
    ]
    assert nest.events() == [{"change": "snowluma_started"}]
    assert (await nest.view())["snowluma"]["pid"] == 4242

    again = await nest.http.post("/api/system/snowluma/start")
    assert again.json() == {"ok": True, "started": False}
    assert nest.machine.kinds() == ["start"] and nest.events() == []


async def test_concurrent_starts_start_one(nest: Nest) -> None:
    nest.machine.start_delay = 0.2
    responses = await asyncio.gather(*(nest.http.post("/api/system/snowluma/start") for _ in range(3)))
    assert sorted(r.json()["started"] for r in responses) == [False, False, True]
    assert nest.machine.kinds() == ["start"]


async def test_stop(nest: Nest) -> None:
    nest.machine.pids = [4242]
    nest.events()
    response = await nest.http.post("/api/system/snowluma/stop")
    assert response.status_code == 200 and response.json() == {"ok": True, "stopped": True}
    assert nest.machine.calls == [("stop", [4242])]
    assert nest.events() == [{"change": "snowluma_stopped"}]
    assert (await nest.view())["snowluma"]["running"] is False

    again = await nest.http.post("/api/system/snowluma/stop")
    assert again.json() == {"ok": True, "stopped": False}
    assert nest.machine.kinds() == ["stop"] and nest.events() == []


async def test_restart(nest: Nest) -> None:
    nest.machine.pids = [4000]
    nest.events()
    response = await nest.http.post("/api/system/snowluma/restart")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert nest.machine.kinds() == ["stop", "start"]
    assert nest.machine.pids == [4242]
    assert nest.events() == [{"change": "snowluma_restarted"}]
    # Not running: restart just starts it.
    nest.machine.pids = []
    assert (await nest.http.post("/api/system/snowluma/restart")).json() == {"ok": True}
    assert nest.machine.kinds() == ["stop", "start", "start"]


async def test_restart_when_the_port_stays_taken(nest: Nest) -> None:
    nest.machine.pids = [4000]
    nest.machine.port_open = True
    nest.events()
    response = await nest.http.post("/api/system/snowluma/restart")
    assert response.status_code == 409
    assert response.json() == {"detail": "SnowLuma 的端口还被占用（可能是别的程序），没有重新启动"}
    assert nest.machine.kinds() == ["stop"]
    assert nest.events() == [{"change": "snowluma_stopped"}]


async def test_start_failure(nest: Nest) -> None:
    nest.machine.start_error = "SnowLuma 启动后马上退出了（退出码 1），请查看 X 里的日志"
    nest.events()
    response = await nest.http.post("/api/system/snowluma/start")
    assert response.status_code == 500 and response.json() == {"detail": nest.machine.start_error}
    assert nest.events() == []
    logged = (await nest.http.get("/api/logs", params={"level": "ERROR"})).json()
    assert logged[-1]["message"] == nest.machine.start_error


async def test_open_qq(nest: Nest) -> None:
    nest.events()
    response = await nest.http.post("/api/system/qq/open")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert nest.machine.calls == [
        ("open_qq", str(nest.machine.qq_exe), {"ARK_API_KEY", "NEST_QQ_10001_TOKEN", "QQ_WS_TOKEN"})
    ]
    assert nest.events() == [{"change": "qq_opened"}]


# -- the process helpers themselves (no real SnowLuma or QQ involved) ----------------------------------


def test_runs_snowluma(tmp_path: Path) -> None:
    folder = tmp_path / "SnowLuma"
    node = str(folder / "node.exe")
    assert runs_snowluma(folder, [node, "index.mjs"], str(folder), node)
    assert runs_snowluma(folder, [node, "./index.mjs"], str(folder), node)
    assert runs_snowluma(folder, ["node", str(folder / "index.mjs")], None, None)
    assert runs_snowluma(folder, ["node", f'"{folder / "index.mjs"}"'], None, None)
    assert runs_snowluma(folder, [node, "index.mjs"], None, node)  # cwd unreadable: node.exe's folder
    assert not runs_snowluma(folder, [node, "index.mjs"], str(tmp_path), node)
    assert not runs_snowluma(folder, ["node", str(tmp_path / "other" / "index.mjs")], None, None)
    assert not runs_snowluma(folder, ["node", "index.mjs"], None, None)
    assert not runs_snowluma(folder, [node, "server.js"], str(folder), node)
    assert not runs_snowluma(folder, [str(folder / "index.mjs")], str(folder), None)  # argv[0] only
    if os.name == "nt":
        assert runs_snowluma(folder, [node, str(folder / "INDEX.MJS").upper()], None, None)


@dataclass
class FakeProc:
    """Enough of psutil.Process for the helpers; `args` may be an exception cmdline() raises."""

    name_: str
    kids: list[FakeProc] = field(default_factory=list)
    pid: int = 0
    parent: int = 1
    args: list[str] | Exception = field(default_factory=list)
    cwd_: str | None = None

    def name(self) -> str:
        return self.name_

    def children(self) -> list[FakeProc]:
        return self.kids

    def cmdline(self) -> list[str]:
        if isinstance(self.args, Exception):
            raise self.args
        return self.args

    def ppid(self) -> int:
        return self.parent

    def exe(self) -> str:
        raise system.psutil.AccessDenied(self.pid)

    def cwd(self) -> str:
        if self.cwd_ is None:
            raise system.psutil.AccessDenied(self.pid)
        return self.cwd_


def test_qq_clients_counts_main_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = [
        FakeProc("QQ.exe", pid=10, args=[r"E:\QQ\QQ.exe"]),
        FakeProc("QQ.exe", pid=11, parent=10, args=[r"E:\QQ\QQ.exe", "--type=renderer"]),
        FakeProc("QQ.exe", pid=12, parent=10, args=[r"E:\QQ\QQ.exe", "--type=gpu-process", "--x"]),
        FakeProc("QQ.exe", pid=20, parent=5, args=system.psutil.AccessDenied(20)),  # unreadable main
        FakeProc("QQ.exe", pid=21, parent=20, args=system.psutil.AccessDenied(21)),  # unreadable helper
        FakeProc("QQ.exe", pid=30, args=system.psutil.NoSuchProcess(30)),  # exited meanwhile
    ]
    monkeypatch.setattr(system, "_named", lambda name: procs if name == "qq.exe" else [])
    assert qq_clients() == 2


def test_snowluma_pids_finds_the_top_of_each_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = tmp_path / "SnowLuma"
    procs = [
        FakeProc("node.exe", pid=40, parent=2, args=["node.exe", "index.mjs"], cwd_=str(folder)),
        FakeProc("node.exe", pid=41, parent=40, args=["node", str(folder / "index.mjs")]),  # its own child
        FakeProc("node.exe", pid=42, args=["node.exe", "index.mjs"], cwd_=str(tmp_path)),  # another app
        FakeProc("node.exe", pid=43, args=system.psutil.AccessDenied(43)),
    ]
    monkeypatch.setattr(system, "_named", lambda name: procs if name == "node.exe" else [])
    assert snowluma_pids(folder) == [40]
    assert snowluma_pids("") == []


def test_tree_leaves_qq_running() -> None:
    qq_child = FakeProc("QQ.exe")
    qq = FakeProc("QQ.exe", [qq_child])
    helper = FakeProc("node.exe")
    worker = FakeProc("cmd.exe", [helper])
    root = FakeProc("node.exe", [worker, qq])
    assert [p.name_ for p in tree(root)] == ["node.exe", "cmd.exe", "node.exe"]
    assert tree(root)[-1] is root and qq not in tree(root) and qq_child not in tree(root)


class FakePopen:
    """Stands in for subprocess.Popen inside nest.system: records each start, starts nothing."""

    calls: ClassVar[list[tuple[list[str], dict[str, Any]]]]
    refuse_breakaway: ClassVar[bool] = False
    exit_code: ClassVar[int | None] = None

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        flags = kwargs.get("creationflags", 0)
        if os.name == "nt" and self.refuse_breakaway and flags & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "拒绝访问。")
        self.calls.append((args, kwargs))
        self.pid = 777

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("node.exe", timeout or 0)
        return self.exit_code


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> type[FakePopen]:
    recorder = type("Recorder", (FakePopen,), {"calls": []})
    monkeypatch.setattr(system.subprocess, "Popen", recorder)
    return recorder


def test_start_snowluma_detached_windowless_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, popen: type[FakePopen]
) -> None:
    folder = snowluma_folder(tmp_path / "SnowLuma")
    monkeypatch.setenv("NEST_KEY_ARK", "sk-from-env")
    monkeypatch.setenv("ARK_API_KEY", "sk-also-secret")
    monkeypatch.setenv("NEST_TEST_KEEP_PATH", "x")
    popen.refuse_breakaway = True  # a job that does not allow leaving it: started inside it
    assert start_snowluma(folder, hide={"ARK_API_KEY"}) == 777
    args, kwargs = popen.calls[-1]
    assert args == [str(folder / "node.exe"), "index.mjs"]
    assert kwargs["cwd"] == folder and kwargs["close_fds"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    env = kwargs["env"]
    assert {k: env[k] for k in system.SNOWLUMA_ENV} == {
        "SNOWLUMA_ACCEPT_EULA": "1",
        "SNOWLUMA_ACCEPT_PRIVACY": "1",
        "SNOWLUMA_HOOK_AUTOLOAD": "1",
        "SNOWLUMA_WEBUI_HOST": "127.0.0.1",
    }
    assert not any(k.upper().startswith("NEST_") for k in env) and "ARK_API_KEY" not in env
    assert "sk-from-env" not in env.values() and "sk-also-secret" not in env.values()
    if os.name == "nt":
        flags = kwargs["creationflags"]
        wanted = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
        assert flags & wanted == wanted and not flags & subprocess.CREATE_BREAKAWAY_FROM_JOB
        popen.refuse_breakaway = False
        start_snowluma(folder)
        assert popen.calls[-1][1]["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB


def test_start_snowluma_that_exits_at_once(tmp_path: Path, popen: type[FakePopen]) -> None:
    folder = snowluma_folder(tmp_path / "SnowLuma")
    popen.exit_code = 1
    with pytest.raises(ProcessError, match="SnowLuma 启动后马上退出了（退出码 1）"):
        start_snowluma(folder)


def test_start_snowluma_that_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "系统找不到指定的文件。")

    monkeypatch.setattr(system.subprocess, "Popen", broken)
    with pytest.raises(ProcessError, match="SnowLuma 没能启动：系统找不到指定的文件。"):
        start_snowluma(tmp_path)


def test_open_qq_detached(tmp_path: Path, popen: type[FakePopen]) -> None:
    exe = qq_program(tmp_path / "Tencent" / "QQ.exe")
    open_qq(exe, hide={"ARK_API_KEY"})
    args, kwargs = popen.calls[-1]
    assert args == [str(exe)] and kwargs["cwd"] == exe.parent
    if os.name == "nt":
        flags = kwargs["creationflags"]
        assert flags & subprocess.DETACHED_PROCESS and not flags & subprocess.CREATE_NO_WINDOW


def test_stop_snowluma_ends_the_tree(tmp_path: Path) -> None:
    # A stand-in "SnowLuma" (Python) with a child of its own; both are ours, nothing else is touched.
    marker = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 10
        while not (marker.exists() and marker.read_text()):
            assert time.monotonic() < deadline, "child did not start"
            time.sleep(0.05)
        child = int(marker.read_text())
        assert stop_snowluma([parent.pid]) is True
        assert parent.wait(5) is not None
        assert not system.psutil.pid_exists(child) or system.psutil.Process(child).status() == "zombie"
        assert stop_snowluma([parent.pid]) is False
    finally:
        if parent.poll() is None:
            parent.kill()


# -- the live log -----------------------------------------------------------------------------------


def record(
    name: str, level: int, message: str, *args: Any, exc: BaseException | None = None
) -> logging.LogRecord:
    exc_info = (type(exc), exc, None) if exc else None
    return logging.LogRecord(name, level, __file__, 1, message, args, exc_info)


async def test_log_buffer_filters_limits_and_scrubs() -> None:
    published: list[dict[str, Any]] = []
    buffer = LogBuffer(
        published.append, asyncio.get_running_loop(), secrets=lambda: ["zz-LEAKCHECK-456", "short"], size=5
    )
    buffer.handle(record("nest.x", logging.INFO, "第 %d 行", 1))
    buffer.handle(record("nest.x", logging.WARNING, "key sk-test-LEAKCHECK-123 and zz-LEAKCHECK-456, short"))
    buffer.handle(record("nest.x", logging.ERROR, "失败", exc=ValueError("Bearer abcdefghijk")))
    items = buffer.records(level=logging.DEBUG, limit=10)
    assert [i["id"] for i in items] == [1, 2, 3] and published == items
    assert set(items[0]) == {"id", "at", "level", "logger", "message"}
    assert items[0]["message"] == "第 1 行" and items[0]["logger"] == "nest.x" and items[0]["level"] == "INFO"
    assert items[1]["message"] == "key sk-*** and ***, short"  # values under 8 characters are left alone
    assert items[2]["message"] == "失败\nValueError: Bearer ***"
    assert [i["level"] for i in buffer.records(level=logging.WARNING)] == ["WARNING", "ERROR"]
    assert [i["id"] for i in buffer.records(level=logging.INFO, limit=1)] == [3]
    for n in range(10):
        buffer.handle(record("nest.x", logging.INFO, "more %d", n))
    kept = buffer.records(limit=100)
    assert len(kept) == 5 and kept[-1]["message"] == "more 9" and kept[-1]["id"] == 13


async def test_log_buffer_publishes_on_the_loop_from_any_thread() -> None:
    loop = asyncio.get_running_loop()
    threads: list[int] = []
    buffer = LogBuffer(lambda item: threads.append(threading.get_ident()), loop)
    buffer.handle(record("nest.x", logging.INFO, "on the loop"))
    await asyncio.to_thread(buffer.handle, record("nest.x", logging.INFO, "from a worker"))
    await eventually(lambda: len(threads) == 2)
    assert threads == [threading.get_ident()] * 2


async def test_logs_api(nest: Nest, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="nest")
    logging.getLogger("nest.test").debug("调试信息不进实时日志")
    logging.getLogger("nest.test").info("普通的一行")
    logging.getLogger("nest.test").warning("密钥 sk-test-LEAKCHECK-123 不该出现")
    logging.getLogger("elsewhere").warning("别的库的日志不显示")
    logging.getLogger("uvicorn.error").error("uvicorn 的错误")
    await asyncio.to_thread(logging.getLogger("nest.worker").warning, "工作线程里的一行")

    lines = (await nest.http.get("/api/logs")).json()
    messages = [line["message"] for line in lines]
    assert messages[0].startswith("nest 已启动")  # installed before startup
    assert messages[-4:] == ["普通的一行", "密钥 sk-*** 不该出现", "uvicorn 的错误", "工作线程里的一行"]
    assert all(line["logger"] != "elsewhere" for line in lines)
    ids = [line["id"] for line in lines]
    assert ids == sorted(ids)
    everything = (await nest.http.get("/api/logs", params={"level": "DEBUG", "limit": 1000})).json()
    assert everything == lines  # INFO and above only, whatever is asked for

    warnings = (await nest.http.get("/api/logs", params={"level": "warning"})).json()
    assert [w["message"] for w in warnings][-3:] == [
        "密钥 sk-*** 不该出现",
        "uvicorn 的错误",
        "工作线程里的一行",
    ]
    assert {w["level"] for w in warnings} <= {"WARNING", "ERROR"}
    assert [x["message"] for x in (await nest.http.get("/api/logs", params={"limit": 1})).json()] == [
        "工作线程里的一行"
    ]
    for params, detail in (
        ({"level": "LOUD"}, "日志级别只能是 DEBUG、INFO、WARNING 或 ERROR"),
        ({"limit": 0}, "条数超出范围"),
        ({"limit": 1001}, "条数超出范围"),
        ({"limit": "x"}, "条数格式不对"),
    ):
        response = await nest.http.get("/api/logs", params=params)
        assert response.status_code == 422 and response.json() == {"detail": detail}, params


async def test_logs_hide_the_token_and_env_secrets(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nest")
    token = "console-token-0123456789abcdef"
    (tmp_path / ".env").write_text(
        f"NEST_QQ_1_TOKEN='qq-secret-token-1'\nNEST_HOME='{tmp_path}'\nNEST_PORT='18800'\n", encoding="utf-8"
    )
    settings = Settings(home=tmp_path, port=18800, _env_file=None)
    app = create_app(settings, token=token, provider_factory=factory)
    async with app.router.lifespan_context(app):
        logging.getLogger("nest.test").warning("token=%s qq=%s home=%s", token, "qq-secret-token-1", tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=settings.base_url
        ) as http:
            lines = (await http.get("/api/logs", headers={"authorization": f"Bearer {token}"})).json()
    assert lines[-1]["message"] == f"token=*** qq=*** home={tmp_path}"  # nest's own settings stay


async def test_log_events_on_the_live_stream(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nest")
    sock = free_socket()
    settings = Settings(home=tmp_path, port=sock.getsockname()[1], _env_file=None)
    app = create_app(settings, token=TOKEN, provider_factory=factory)
    async with (
        serve(app, sock) as base,
        httpx.AsyncClient(base_url=base, headers=AUTH, trust_env=False) as api,
    ):
        received: list[tuple[str, dict[str, Any]]] = []

        async def listen() -> None:
            async with api.stream("GET", "/api/events", timeout=None) as response:
                async for item in sse_events(response):
                    received.append(item)

        listener = asyncio.create_task(listen())
        try:
            await eventually(lambda: any(kind == "hello" for kind, _ in received))
            logging.getLogger("nest.test").warning("实时日志 sk-test-LEAKCHECK-123")
            await asyncio.to_thread(logging.getLogger("nest.test").info, "线程里的实时日志")

            def live() -> list[dict[str, Any]]:
                return [d for k, d in received if k == "log" and "实时日志" in d["message"]]

            await eventually(lambda: len(live()) == 2)
            logs = live()
            assert [d["message"] for d in logs] == ["实时日志 sk-***", "线程里的实时日志"]
            assert set(logs[0]) == {"id", "at", "level", "logger", "message"}
            assert logs[0]["level"] == "WARNING" and logs[0]["logger"] == "nest.test"
            assert logs[1]["id"] == logs[0]["id"] + 1 and abs(logs[0]["at"] - time.time()) < 30
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


async def test_the_log_handler_is_removed_on_shutdown(settings: Settings) -> None:
    loggers = [logging.getLogger(name) for name in ("nest", "uvicorn.error")]
    before = [list(logger.handlers) for logger in loggers]
    app = create_app(settings, token=TOKEN)
    async with app.router.lifespan_context(app):
        assert all(app.state.logs in logger.handlers for logger in loggers)
    assert [list(logger.handlers) for logger in loggers] == before
