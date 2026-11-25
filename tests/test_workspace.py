"""工作区 and 她的容器 (伙伴模式): a companion's own folder — nothing outside it is reached, secret
names are refused, a size limit — her tools on it, her commands in a locked-down container (a pretend
`docker` here), and the console's view: list, download, send her a file, delete."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from nest.core.sandbox import DOCKERFILE, IMAGE, Sandbox, container_name, run_args
from nest.core.workspace import FOR_YOU, RECEIVED, Workspace, WorkspaceError
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model

BOT = "qq:90000001"


def test_her_folder_and_nothing_outside_it(tmp_path: Path) -> None:
    space = Workspace(tmp_path / "ws")
    folder = space.folder(BOT)
    assert folder.name == "qq-90000001"
    assert (folder / FOR_YOU).is_dir() and (folder / RECEIVED).is_dir()
    for bad in (
        "../x",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "a/../../x",
        "notes/.env",
        "id_rsa",
        "k.pem",
        "a\x00b",
    ):
        with pytest.raises(WorkspaceError):
            space.path(BOT, bad)
    assert space.path(BOT, "笔记/草稿.md") == (folder / "笔记" / "草稿.md").resolve()
    assert space.path(BOT, "") == folder.resolve()


def test_a_link_out_of_her_folder_leads_nowhere(tmp_path: Path) -> None:
    space = Workspace(tmp_path / "ws")
    outside = tmp_path / "secret.txt"
    outside.write_text("不该看到", encoding="utf-8")
    try:
        os.symlink(outside, space.folder(BOT) / "link.txt")
    except OSError:
        pytest.skip("this system does not let tests make symbolic links")
    with pytest.raises(WorkspaceError, match="外面"):
        space.read(BOT, "link.txt")


def _link_folder(link: Path, target: Path) -> None:
    """A link to a folder: a symbolic link, or on Windows (without the right to make one) a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("this system does not let tests make symbolic links")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_a_folder_swapped_for_a_link_leads_nowhere(tmp_path: Path) -> None:
    """Her container can swap a folder on the way for a link to some
    place outside at any moment; every use goes down by name and never through a link."""
    space = Workspace(tmp_path / "ws")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("不该看到", encoding="utf-8")
    folder = space.folder(BOT)
    _link_folder(folder / "d", outside)
    attempts = {
        "read": lambda: space.read(BOT, "d/secret.txt"),
        "open": lambda: space.open_file(BOT, "d/secret.txt"),
        "write": lambda: space.write(BOT, "d/x.plist", b"<plist/>"),
        "write deeper": lambda: space.write(BOT, "d/LaunchAgents/x.plist", b"<plist/>"),
        "edit": lambda: space.edit(BOT, "d/secret.txt", "不该", "x"),
        "deliver": lambda: space.deliver(BOT, "d/secret.txt"),
        "list": lambda: space.entries(BOT, "d"),
        "delete": lambda: space.delete(BOT, "d/secret.txt"),
    }
    for what, attempt in attempts.items():
        with pytest.raises(WorkspaceError, match="外面"):
            attempt()
            pytest.fail(what)
    space.write(BOT, "a.md", b"x")
    for shelf in (FOR_YOU, RECEIVED):
        (folder / shelf).rmdir()
        _link_folder(folder / shelf, outside)
    with pytest.raises(WorkspaceError, match="外面"):
        space.deliver(BOT, "a.md")
    with pytest.raises(WorkspaceError, match="外面"):
        space.receive(BOT, "报告.pdf", b"%PDF")
    space.delete(BOT, "d")  # the link goes, not what it points to
    assert not os.path.lexists(folder / "d")
    assert sorted(os.listdir(outside)) == ["secret.txt"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "不该看到"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_a_pipe_in_her_folder_does_not_hang_nest(tmp_path: Path) -> None:
    space = Workspace(tmp_path / "ws")
    os.mkfifo(space.folder(BOT) / "pipe")
    with pytest.raises(WorkspaceError):
        space.read(BOT, "pipe")
    with pytest.raises(WorkspaceError):
        space.write(BOT, "pipe", b"x")


async def test_a_download_is_the_file_opened_then(world: World) -> None:  # noqa: F811
    space = world.state.workspace
    await asyncio.to_thread(space.write, WHALE, f"{FOR_YOU}/清单.md", "带伞".encode())
    got = await world.http.get(f"/api/workspaces/{WHALE}/download", params={"path": f"{FOR_YOU}/清单.md"})
    assert got.status_code == 200 and got.content == "带伞".encode()
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment;")
    missing = await world.http.get(f"/api/workspaces/{WHALE}/download", params={"path": f"{FOR_YOU}/没有.md"})
    assert missing.status_code == 404


def test_reading_writing_changing_and_handing_over(tmp_path: Path) -> None:
    space = Workspace(tmp_path / "ws", limit=1000)
    space.write(BOT, "草稿/清单.md", "1. 订票\n2. 带伞\n".encode())
    assert space.read(BOT, "草稿/清单.md") == "1. 订票\n2. 带伞\n"
    space.edit(BOT, "草稿/清单.md", "带伞", "带伞和外套")
    assert "带伞和外套" in space.read(BOT, "草稿/清单.md")
    with pytest.raises(WorkspaceError, match="没找到"):
        space.edit(BOT, "草稿/清单.md", "不存在的", "x")
    assert space.deliver(BOT, "草稿/清单.md") == f"{FOR_YOU}/清单.md"
    assert space.deliver(BOT, "草稿/清单.md") == f"{FOR_YOU}/清单-2.md"  # the first stays
    assert space.deliver(BOT, f"{FOR_YOU}/清单.md") == f"{FOR_YOU}/清单.md"  # already there
    names = [e.name for e in space.entries(BOT, FOR_YOU)]
    assert names == ["清单-2.md", "清单.md"]
    with pytest.raises(WorkspaceError, match="满了"):
        space.write(BOT, "大.bin", b"x" * 980)
    space.write(BOT, "图.bin", b"\x89PNG\x00\x00")
    with pytest.raises(WorkspaceError, match="不是文字文件"):
        space.read(BOT, "图.bin")
    assert space.receive(BOT, "../../报告.pdf", b"%PDF") == f"{RECEIVED}/报告.pdf"
    assert space.receive(BOT, "报告.pdf", b"%PDF") == f"{RECEIVED}/报告-2.pdf"
    space.delete(BOT, "草稿")
    with pytest.raises(WorkspaceError):
        space.delete(BOT, FOR_YOU)
    assert [e.name for e in space.entries(BOT)] == [FOR_YOU, RECEIVED, "图.bin"]


# -- her container ------------------------------------------------------------------------------------------


class PretendDocker:
    """Docker as far as nest.core.sandbox uses it: images, networks, containers with labels, exec."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fed: list[bytes | None] = []
        self.containers: dict[str, bool] = {}  # name -> running
        self.labels: dict[str, dict[str, str]] = {}
        self.networks: dict[str, set[str]] = {}  # network -> containers on it
        self.image = False
        self.output = (0, "hello\n")

    async def __call__(self, args: list[str], seconds: float, stdin: bytes | None = None) -> tuple[int, str]:
        self.calls.append(args)
        self.fed.append(stdin)
        if args[:2] == ["image", "inspect"]:
            return (0, "[]") if self.image else (1, "No such image")
        if args[0] == "build":
            self.image = True
            return 0, "built"
        if args[:2] == ["network", "inspect"]:
            return (0, "[]") if args[2] in self.networks else (1, "Error: No such network")
        if args[:2] == ["network", "create"]:
            self.networks[args[-1]] = set()
            return 0, "id"
        if args[:2] == ["network", "connect"]:
            self.networks[args[2]].add(args[3])
            return 0, ""
        if args[0] == "inspect" and "NetworkSettings" in args[args.index("--format") + 1]:
            name = args[-1]
            on = sorted(n for n, members in self.networks.items() if name in members)
            return 0, json.dumps({n: {"IPAddress": f"10.9.{i}.2"} for i, n in enumerate(on)})
        if args[0] == "inspect":
            name = args[-1]
            if name not in self.containers:
                return 1, "Error: No such container"
            keys = re.findall(r'Labels "([^"]+)"', args[args.index("--format") + 1])
            labels = self.labels[name]
            shown = [
                "true" if self.containers[name] else "false",
                *(labels.get(k, "<no value>") for k in keys),
            ]
            return 0, " ".join(shown) + "\n"
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            self.containers[name] = True
            self.labels[name] = dict(
                args[i + 1].split("=", 1) for i in range(len(args)) if args[i] == "--label"
            )
            network = args[args.index("--network") + 1] if "--network" in args else "bridge"
            self.networks.setdefault(network, set()).add(name)
            return 0, "abc"
        if args[0] == "rm":
            self.containers.pop(args[-1], None)
            self.labels.pop(args[-1], None)
            for members in self.networks.values():
                members.discard(args[-1])
            return 0, ""
        if args[0] in ("start", "stop"):
            self.containers[args[-1]] = args[0] == "start"
            return 0, ""
        if args[0] == "exec":
            return self.output
        raise AssertionError(args)


async def offline(_: str) -> bool:
    return False


async def test_her_commands_run_in_her_locked_down_container(tmp_path: Path) -> None:
    docker = PretendDocker()
    clock = [100.0]
    sandbox = Sandbox(Workspace(tmp_path / "ws"), docker_call=docker, clock=lambda: clock[0], online=offline)
    result = await sandbox.run(BOT, "python3 -c 'print(1)'")
    assert result == "（退出码 0）\nhello"
    build = docker.calls.index(["build", "--tag", IMAGE, "-"])
    assert docker.fed[build] == DOCKERFILE.encode() and "FROM python:3.12-slim" in DOCKERFILE
    (made,) = [c for c in docker.calls if c[0] == "run"]
    assert made == run_args(BOT, (tmp_path / "ws" / "qq-90000001").resolve(), online=False)
    for setting in (["--network", "none"], ["--user", "1000:1000"], ["--cap-drop", "ALL"]):
        assert any(made[i : i + 2] == setting for i in range(len(made)))
    assert sum(1 for c in made if c.startswith(f"{(tmp_path / 'ws' / 'qq-90000001').resolve()}:")) == 1
    name = container_name(BOT)
    assert docker.calls[-1][:4] == ["exec", "--workdir", "/work", name]
    assert docker.calls[-1][4:] == ["timeout", "--kill-after=5", "120", "sh", "-c", "python3 -c 'print(1)'"]

    # Again: no new image or container; a long wait is capped.
    docker.calls.clear()
    await sandbox.run(BOT, "ls", seconds=9999)
    assert [c[0] for c in docker.calls] == ["inspect", "exec"]
    assert docker.calls[-1][6] == "600"

    docker.output = (124, "")
    assert (await sandbox.run(BOT, "sleep 1000", seconds=5)).startswith("（超过 5 秒，被停掉了）")
    assert (await sandbox.run(BOT, "   ")) == "命令是空的"

    # Left alone, it is stopped; the next command starts it again.
    clock[0] += 601
    assert await sandbox.stop_idle() == 1
    assert docker.containers[name] is False
    docker.output = (0, "x" * 20_000)
    long = await sandbox.run(BOT, "cat big")
    assert docker.containers[name] is True and "中间略" in long and len(long) < 13_000


# -- her tools, and the console ---------------------------------------------------------------------------


async def test_she_writes_a_file_and_hands_it_over(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    model(world).queue(
        ToolCall("write_file", {"path": "旅行/清单.md", "content": "1. 订票\n2. 带伞"}),
        ToolCall("deliver_file", {"path": "旅行/清单.md"}),
        ToolCall("read_file", {"path": "../../data/nest.db"}),
        "写好了，在「给你的」里：清单.md",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "把清单写成文件给我"})
    assert await eventually(lambda: _answer(world, chat)) == "写好了，在「给你的」里：清单.md"
    results = dict(model(world).tool_results)
    assert results["write_file"].startswith("写好了")
    assert results["deliver_file"].startswith(f"交给对方了：{FOR_YOU}/清单.md")
    assert results["read_file"].startswith("没做成：路径不能用 ..")
    assert ("workspace", {"bot_id": WHALE}) in world.events

    listed = (await world.http.get(f"/api/workspaces/{WHALE}/files", params={"path": FOR_YOU})).json()
    assert [(e["name"], e["size"]) for e in listed["entries"]] == [
        ("清单.md", len("1. 订票\n2. 带伞".encode()))
    ]
    got = await world.http.get(f"/api/workspaces/{WHALE}/download", params={"path": f"{FOR_YOU}/清单.md"})
    assert got.status_code == 200 and got.content.decode() == "1. 订票\n2. 带伞"
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")


async def test_the_console_side_of_her_folder(world: World) -> None:  # noqa: F811
    base = f"/api/workspaces/{WHALE}"
    sent = await world.http.put(f"{base}/upload", params={"name": "资料.txt"}, content="你好".encode())
    assert sent.status_code == 201 and sent.json() == {"path": f"{RECEIVED}/资料.txt"}
    listing = (await world.http.get(f"{base}/files")).json()
    assert [e["name"] for e in listing["entries"]] == [FOR_YOU, RECEIVED]
    assert listing["used"] == len("你好".encode())
    for bad in ("../x", "/etc/passwd", f"{RECEIVED}/.env"):
        assert (await world.http.get(f"{base}/download", params={"path": bad})).status_code == 404
    assert (
        await world.http.put(f"{base}/upload", params={"name": ".env"}, content=b"K=1")
    ).status_code == 422
    assert (
        await world.http.delete(f"{base}/files", params={"path": f"{RECEIVED}/资料.txt"})
    ).status_code == 200
    assert (await world.http.delete(f"{base}/files", params={"path": RECEIVED})).status_code == 404
    assert (await world.http.get("/api/workspaces/nobody/files")).status_code == 404


async def test_a_command_she_runs_goes_to_her_container(world: World, tmp_path: Path) -> None:  # noqa: F811
    docker = PretendDocker()
    world.state.brain.tools.sandbox = Sandbox(world.state.workspace, docker_call=docker)
    chat = await claude_chat(world)
    model(world).queue(ToolCall("run_command", {"command": "ls", "timeout_seconds": 30}), "看过了。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "看看你的文件夹"})
    assert await eventually(lambda: _answer(world, chat)) == "看过了。"
    assert dict(model(world).tool_results)["run_command"] == "（退出码 0）\nhello"
    assert docker.calls[-1][-6:] == ["timeout", "--kill-after=5", "30", "sh", "-c", "ls"]


async def test_a_command_that_fills_her_folder_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What her commands write is counted too. Her container's own
    system is read-only (scratch space is small and in memory); her folder is measured while a command runs,
    and past the limit the container goes, which ends the command."""
    from nest.core import sandbox as sandbox_module

    monkeypatch.setattr(sandbox_module, "WATCH_SECONDS", 0.01)
    space = Workspace(tmp_path / "ws", limit=1000)
    removed = asyncio.Event()

    class Filling(PretendDocker):
        async def __call__(
            self, args: list[str], seconds: float, stdin: bytes | None = None
        ) -> tuple[int, str]:
            if args[0] == "exec" and "dd" in args[-1]:
                (space.folder(BOT) / "big.bin").write_bytes(b"x" * 2000)
                await removed.wait()  # runs until her container goes
                return 137, ""
            if args[0] == "rm":
                removed.set()
            return await super().__call__(args, seconds, stdin)

    docker = Filling()
    sandbox = Sandbox(space, docker_call=docker, online=offline)
    said = await sandbox.run(BOT, "dd if=/dev/zero of=big.bin")
    assert "超过上限" in said and "被停掉" in said
    assert container_name(BOT) not in docker.containers
    (made,) = [c for c in docker.calls if c[0] == "run"]
    assert "--read-only" in made and "/tmp:size=256m" in made
    # Over the limit, a command still runs (to delete things), as long as it does not add.
    docker.output = (0, "")
    assert (await sandbox.run(BOT, "rm big.bin")).startswith("（退出码 0）")
