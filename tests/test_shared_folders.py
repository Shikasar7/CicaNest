"""共享文件夹 (伙伴模式): folders on nest's machine the user lets her into — under 共享/<name>/ in her
workspace; read-only or writable, the old version of a file kept in 旧版本/ before it is replaced, nothing
deleted from them; in her container read-only; what may not be shared; the console's side; and what she is
told (the folders, or how to get some, or that her model calls no tools at all)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nest.core.sandbox import Sandbox, container_name, run_args, shared_stamp
from nest.core.tools import NO_TOOLS, SHARE_HOW, SHARED_HINT
from nest.core.workspace import OLD, SHARED, Shared, Workspace, WorkspaceError
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model
from tests.test_workspace import PretendDocker

BOT = "qq:90000001"


def _setup(tmp_path: Path) -> tuple[Workspace, Path, Path]:
    home = tmp_path / "home"
    notes, docs = home / "笔记", home / "资料"
    for place in (notes, docs):
        place.mkdir(parents=True)
    (notes / "清单.md").write_bytes("1. 订票\n".encode())
    (docs / "合同.txt").write_text("甲方乙方", encoding="utf-8")
    space = Workspace(tmp_path / "ws", guarded=(tmp_path / "nest",), home=home)
    return space, notes, docs


def test_she_reads_and_writes_a_shared_folder_and_the_old_version_is_kept(tmp_path: Path) -> None:
    space, notes, docs = _setup(tmp_path)
    made = space.set_shared(BOT, [(str(notes), True), (str(docs), False)])
    assert made == (Shared("笔记", str(notes.resolve()), True), Shared("资料", str(docs.resolve()), False))
    assert space.shared(BOT) == made

    top = [e.name for e in space.entries(BOT)]
    assert top[:3] == ["给你的", "收到的", SHARED]
    assert [e.name for e in space.entries(BOT, SHARED)] == ["笔记", "资料"]
    assert [e.name for e in space.entries(BOT, f"{SHARED}/资料")] == ["合同.txt"]
    assert space.read(BOT, f"{SHARED}/资料/合同.txt") == "甲方乙方"

    # Writable: a new file, and a changed one whose old version goes to 旧版本/笔记/.
    space.write(BOT, f"{SHARED}/笔记/新/想法.md", "想到的".encode())
    assert (notes / "新" / "想法.md").read_text(encoding="utf-8") == "想到的"
    space.edit(BOT, f"{SHARED}/笔记/清单.md", "订票", "订酒店")
    assert (notes / "清单.md").read_text(encoding="utf-8") == "1. 订酒店\n"
    (kept,) = space.entries(BOT, f"{OLD}/笔记")
    assert kept.name.startswith("清单-") and kept.name.endswith(".md")
    assert space.read(BOT, f"{OLD}/笔记/{kept.name}") == "1. 订票\n"
    assert space.used(BOT) == len("1. 订票\n".encode())  # the old version counts against her room

    # Read-only: nothing changes. Nothing is deleted from a shared folder; 共享/ itself holds no files.
    with pytest.raises(WorkspaceError, match="只读"):
        space.write(BOT, f"{SHARED}/资料/合同.txt", b"x")
    with pytest.raises(WorkspaceError, match="取消共享"):
        space.delete(BOT, f"{SHARED}/资料/合同.txt")
    with pytest.raises(WorkspaceError):
        space.write(BOT, f"{SHARED}/x.txt", b"x")
    with pytest.raises(WorkspaceError, match="没有叫「别的」"):
        space.read(BOT, f"{SHARED}/别的/a.txt")
    # Still never out of it, nor at a secret name.
    for bad in (f"{SHARED}/资料/../../x", f"{SHARED}/资料/.env"):
        with pytest.raises(WorkspaceError):
            space.read(BOT, bad)
    assert (docs / "合同.txt").read_text(encoding="utf-8") == "甲方乙方"

    # Delivered from a shared folder: a copy in 给你的/.
    assert space.deliver(BOT, f"{SHARED}/资料/合同.txt") == "给你的/合同.txt"

    # Unshared: gone from her view; the folder itself untouched.
    space.set_shared(BOT, [])
    assert space.shared(BOT) == () and SHARED not in [e.name for e in space.entries(BOT)]
    with pytest.raises(WorkspaceError):
        space.read(BOT, f"{SHARED}/资料/合同.txt")
    assert (docs / "合同.txt").exists()


def test_what_may_not_be_shared(tmp_path: Path) -> None:
    space, notes, _ = _setup(tmp_path)
    home = tmp_path / "home"
    (home / ".ssh").mkdir()
    space.folder(BOT)
    (tmp_path / "nest" / "data").mkdir(parents=True)
    for bad, why in (
        ("", "不能用"),
        ("笔记", "完整路径"),
        (str(home / "没有"), "没有这个文件夹"),
        (str(home), "整个"),
        (str(home / ".ssh"), "不能共享"),
        (str(tmp_path / "nest" / "data"), "不能共享"),  # nest's own
        (str(tmp_path), "不能共享"),  # holds nest's own
        (str(tmp_path / "ws"), "不能共享"),  # the companions' folders
    ):
        with pytest.raises(WorkspaceError, match=why):
            space.check_shared(bad)
    # The same folder twice is one; two of the same name are told apart.
    other = tmp_path / "elsewhere" / "笔记"
    other.mkdir(parents=True)
    made = space.set_shared(BOT, [(str(notes), False), (str(notes), True), (str(other), False)])
    assert [s.name for s in made] == ["笔记", "笔记-2"]
    with pytest.raises(WorkspaceError, match="最多"):
        space.set_shared(BOT, [(str(notes), False)] * 21)


def test_in_her_container_shared_folders_are_read_only(tmp_path: Path) -> None:
    space, notes, docs = _setup(tmp_path)
    shared = space.set_shared(BOT, [(str(notes), True), (str(docs), False)])
    args = run_args(BOT, tmp_path / "ws" / "x", online=False, shared=shared)
    volumes = [args[i + 1] for i in range(len(args)) if args[i] == "--volume"]
    assert volumes[1:] == [
        f"{notes.resolve()}:/work/{SHARED}/笔记:ro",
        f"{docs.resolve()}:/work/{SHARED}/资料:ro",
    ]
    assert f"nest.shared={shared_stamp(shared)}" in args and shared_stamp(()) == ""


async def test_sharing_from_the_console_makes_her_container_again(
    world: World,  # noqa: F811
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    docker = PretendDocker()
    world.state.sandbox = world.state.brain.tools.sandbox = Sandbox(world.state.workspace, docker_call=docker)
    home = tmp_path_factory.mktemp("home")  # apart from this nest's own folder
    world.state.workspace.home = home
    folder = home / "资料"
    folder.mkdir(parents=True)
    (folder / "清单.txt").write_text("带伞", encoding="utf-8")
    base = f"/api/workspaces/{WHALE}"
    chat = await claude_chat(world)
    model(world).queue(ToolCall("run_command", {"command": "ls"}), "看过了。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "看看"})
    await eventually(lambda: _answer(world, chat))
    assert docker.containers[container_name(WHALE)] is True

    refused = await world.http.put(f"{base}/shared", json={"folders": [{"path": str(home)}]})
    assert refused.status_code == 422 and "整个" in refused.json()["detail"]
    shared = await world.http.put(
        f"{base}/shared", json={"folders": [{"path": str(folder), "writable": True}]}
    )
    assert shared.status_code == 200
    assert shared.json()["shared"] == [{"name": "资料", "path": str(folder.resolve()), "writable": True}]
    assert container_name(WHALE) not in docker.containers  # made again, with it, at her next command
    listing = (await world.http.get(f"{base}/files")).json()
    assert listing["shared"] == shared.json()["shared"]
    assert listing["access"]["tools"] is True and listing["access"]["files"] is True
    got = await world.http.get(f"{base}/download", params={"path": f"{SHARED}/资料/清单.txt"})
    assert got.content.decode() == "带伞"
    assert (
        await world.http.delete(f"{base}/files", params={"path": f"{SHARED}/资料/清单.txt"})
    ).status_code == 404

    # She is told what is shared, and writes there.
    model(world).queue(
        ToolCall("write_file", {"path": f"{SHARED}/资料/清单.txt", "content": "带伞和水"}), "改好了。"
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "清单加上水"})
    await eventually(lambda: _said(world, chat, "改好了。"))
    assert SHARED_HINT.format(folders=f"{SHARED}/资料") in model(world).requests[-1][1].content
    assert f"旧的存进了 {OLD}/" in dict(model(world).tool_results)["write_file"]
    assert (folder / "清单.txt").read_text(encoding="utf-8") == "带伞和水"


async def test_she_is_told_how_to_get_folders_and_when_her_model_calls_no_tools(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    model(world).queue("好")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "在吗"})
    await eventually(lambda: _answer(world, chat))
    prompt = model(world).requests[-1][1].content
    assert SHARE_HOW in prompt and NO_TOOLS not in prompt

    # An OpenAI-compatible model with 工具调用 off: no tools, and she says so instead of pretending.
    runtime = world.state.providers.get("ark")
    runtime.config = replace(runtime.config, kind="openai_chat", options={})
    model(world).queue("做不了")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "帮我改一下电脑上的文件"})
    await eventually(lambda: _said(world, chat, "做不了"))
    assert model(world).tool_lists[-1] == []
    assert NO_TOOLS in model(world).requests[-1][1].content
    access = (await world.http.get(f"/api/workspaces/{WHALE}/files")).json()["access"]
    assert access["tools"] is False and access["switch"] is True


async def _said(w: World, chat: int, text: str) -> bool | None:
    return (await _answer(w, chat)) == text or None
