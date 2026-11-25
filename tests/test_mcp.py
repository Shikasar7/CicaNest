"""MCP 插件: the small MCP client against a real stdio server (tests/fakes/mcp_server.py),
and a plugin's server installed and started in a container of its own (not hers: her commands cannot read
its secrets), its tools offered to her by plugin name, its secret passed by name only, a tool that needs the
user's nod refused, the result kept as material."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nest.core.mcp import McpError, McpSession, flatten
from nest.core.mcp_plugins import exposed, secret_ref
from nest.core.sandbox import Sandbox, container_name, plugin_container_name
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model
from tests.test_workspace import PretendDocker

SERVER = Path(__file__).parent / "fakes" / "mcp_server.py"


async def _server(env: dict[str, str] | None = None) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(SERVER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )


async def test_the_client_lists_and_calls_tools() -> None:
    session = McpSession(await _server(), name="test")
    try:
        info = await session.start()
        assert info["serverInfo"]["name"] == "weather"
        assert [t["name"] for t in await session.list_tools()] == ["forecast", "delete_city", "crash"]
        text, error = await session.call_tool("forecast", {"city": "杭州"})
        assert text.startswith("杭州 晴") and not error
        with pytest.raises(McpError, match="出错"):
            await session.request("resources/list", {}, 5)
        with pytest.raises(McpError, match="退出"):
            await session.call_tool("crash", {}, 5)
        assert not session.alive
    finally:
        await session.close()


def test_what_a_result_says() -> None:
    result = {
        "content": [
            {"type": "text", "text": "一"},
            {"type": "image", "data": "x"},
            {"type": "resource_link", "uri": "u"},
        ],
        "isError": True,
    }
    assert flatten(result) == ("一\n[图片]\n[链接 u]", True)
    assert flatten({"structuredContent": {"a": 1}}) == ('{"a": 1}', False)
    assert exposed("demo.weather", "forecast") == "demo_weather__forecast"
    assert len(exposed("demo." + "x" * 80, "tool")) == 64


async def _pending(w: World) -> list[dict[str, Any]]:
    return (await w.http.get("/api/approvals")).json()["approvals"]


async def _said(w: World, chat: int, text: str) -> bool:
    return await _answer(w, chat) == text


class InstallingDocker(PretendDocker):
    """Docker whose exec answers `test -f <marker>` (not installed at first) and records install commands."""

    def __init__(self) -> None:
        super().__init__()
        self.installed = False
        self.commands: list[str] = []

    async def __call__(self, args: list[str], seconds: float, stdin: bytes | None = None) -> tuple[int, str]:
        if args[0] == "exec":
            self.calls.append(args)
            command = args[-1]
            self.commands.append(command)
            if command.startswith("test -f"):
                return (0 if self.installed else 1), ""
            if "touch" in command:
                self.installed = True
            return 0, ""
        return await super().__call__(args, seconds, stdin)


def package(manifest: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("server.py", "# the server would be here")
    return buffer.getvalue()


WEATHER = {
    "id": "demo.weather",
    "name": "天气",
    "version": "1.0.0",
    "provides": {
        "mcp": {"command": ["python", "server.py"], "install": ["pip install --user demo-weather==1.0"]}
    },
    "permissions": {
        "network": ["api.weather.example"],
        "secrets": [{"name": "WEATHER_KEY", "label": "天气密钥"}],
        "confirm": ["delete_city"],
    },
}


async def test_a_plugin_server_runs_in_its_own_container(world: World, tmp_path: Path) -> None:  # noqa: F811
    docker = InstallingDocker()
    spawned: list[tuple[list[str], dict[str, str]]] = []

    async def spawner(args: list[str], env: dict[str, str]) -> asyncio.subprocess.Process:
        spawned.append((args, env))
        return await _server({**__import__("os").environ, **env})

    sandbox = Sandbox(
        world.state.workspace, docker_call=docker, plugin_homes=tmp_path / "homes", spawner=spawner
    )
    world.state.sandbox = world.state.brain.tools.sandbox = world.state.mcp.sandbox = sandbox

    staged = (await world.http.put("/api/plugin-packages", content=package(WEATHER))).json()
    await world.http.post(f"/api/plugin-packages/{staged['stage']}/install")
    await world.http.put("/api/plugins/demo.weather", json={"bots": [WHALE]})
    saved = await world.http.put(
        "/api/plugins/demo.weather/secrets", json={"values": {"WEATHER_KEY": "k-123"}}
    )
    assert saved.status_code == 200
    assert world.state.secrets.get(secret_ref("demo.weather", "WEATHER_KEY")) == "k-123"
    bad = await world.http.put("/api/plugins/demo.weather/secrets", json={"values": {"OTHER": "x"}})
    assert bad.status_code == 422

    chat = await claude_chat(world)
    model(world).queue(
        ToolCall("demo_weather__forecast", {"city": "杭州"}),
        ToolCall("demo_weather__delete_city", {}),
        "杭州晴天。",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "杭州天气怎么样"})
    # delete_city needs the user's nod: it waits, shown to every console, until they say no.
    (waiting,) = await eventually(lambda: _pending(world), timeout=15)
    assert (waiting["tool"], waiting["plugin_name"], waiting["bot_name"]) == ("delete_city", "天气", "阿鲸")
    assert ("approval", {"id": waiting["id"], "bot_id": WHALE, "status": "pending"}) in world.events
    said_no = await world.http.post(f"/api/approvals/{waiting['id']}", json={"choice": "deny"})
    assert said_no.status_code == 200
    assert (
        await world.http.post(f"/api/approvals/{waiting['id']}", json={"choice": "once"})
    ).status_code == 409
    assert await eventually(lambda: _answer(world, chat), timeout=15) == "杭州晴天。"
    (kept,) = (await world.http.get("/api/approvals", params={"everything": True})).json()["approvals"]
    assert kept["status"] == "denied" and kept["decided_by"]

    offered = model(world).tool_lists[0]
    assert {"demo_weather__forecast", "demo_weather__delete_city"} <= set(offered)
    results = dict(model(world).tool_results)
    assert "杭州 晴 key=yes dir=/plugins/demo.weather" in results["demo_weather__forecast"]
    assert "只是资料" in results["demo_weather__forecast"]  # what a plugin says is not an instruction
    assert results["demo_weather__delete_city"] == "对方没有允许这一步，没做。"
    # Installed once in its own container, from its plugin folder; started there with the secret by name only.
    box = plugin_container_name(WHALE, "demo.weather")
    (install,) = [c for c in docker.calls if c[0] == "exec" and "pip install" in c[-1]]
    assert install[:4] == ["exec", "--workdir", "/plugins/demo.weather", box]
    assert install[-1].startswith("cd '/plugins/demo.weather' && pip install --user demo-weather==1.0")
    # That container sees its plugin folder (read-only) and a home of its own: not her workspace, not the
    # other plugins; her own container is not made for it at all.
    (made,) = [c for c in docker.calls if c[0] == "run" and box in c]
    mounts = [made[i + 1] for i, a in enumerate(made) if a == "--volume"]
    folder = world.state.plugins.installed["demo.weather"][1].resolve()
    home = (tmp_path / "homes" / "qq-10001" / "demo.weather").resolve()
    assert mounts == [f"{folder}:/plugins/demo.weather:ro", f"{home}:/data"]
    assert not any(c[0] == "run" and container_name(WHALE) in c for c in docker.calls)
    args, env = spawned[0]
    assert args[:5] == ["exec", "-i", "--workdir", "/plugins/demo.weather", "--env"] and box in args
    assert args[args.index("WEATHER_KEY") - 1 : args.index("WEATHER_KEY") + 1] == ["--env", "WEATHER_KEY"]
    assert "k-123" not in args and env["WEATHER_KEY"] == "k-123"
    assert args[-2:] == ["python", "server.py"]

    shown = next(
        p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "demo.weather"
    )
    assert shown["mcp_tools"] == ["forecast", "delete_city", "crash"] and shown["secrets_set"] == {
        "WEATHER_KEY": True
    }
    # What its tools are is kept: the next start of nest does not ask again.
    assert world.state.mcp.known["demo.weather"]["version"] == "1.0.0"

    # A crash: the call says so, the next call starts it again.
    model(world).queue(
        ToolCall("demo_weather__crash", {}), ToolCall("demo_weather__forecast", {"city": "北京"}), "好。"
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "再查北京"})
    await eventually(lambda: _said(world, chat, "好。"), timeout=15)
    later = model(world).tool_results[-2:]
    assert later[0][1].startswith("没用成") and "北京 晴" in later[1][1]
    assert len(spawned) == 2
    await world.state.mcp.close()


async def test_without_her_plugin_its_tools_are_not_hers(world: World) -> None:  # noqa: F811
    world.state.mcp.known["demo.weather"] = {"version": "1.0.0", "tools": [{"name": "forecast"}]}
    assert world.state.mcp.tools_for(WHALE) == []  # not installed
    assert (
        await world.state.mcp.call(WHALE, "demo_weather__forecast", {})
        == "demo_weather__forecast 这个工具现在对你关着"
    )


async def test_allowed_again_until_she_reads_something_from_outside(world: World) -> None:  # noqa: F811
    """「同类不再问」 holds for the rest of the task, but not past a page or a plugin's result read since."""
    from nest.core.plugins import Manifest

    tools = world.state.brain.tools
    chat = await claude_chat(world)
    bot = world.state.brain.bots[WHALE]
    channel = bot.channels[chat]
    ask, taint = tools.asker(bot, channel, None)
    plugin = Manifest(id="demo.files", name="文件", version="1.0.0")

    async def answer(choice: str) -> None:
        (waiting,) = await eventually(lambda: _pending(world))
        await world.http.post(f"/api/approvals/{waiting['id']}", json={"choice": choice})

    first = asyncio.create_task(ask(plugin, "delete", {"path": "a"}))
    await answer("kind")
    assert await first == "allowed_kind"
    assert await ask(plugin, "delete", {"path": "b"}) == "allowed_kind"  # not asked again
    taint()  # she read a page
    again = asyncio.create_task(ask(plugin, "delete", {"path": "c"}))
    await answer("once")
    assert await again == "allowed"
    rows = (await world.http.get("/api/approvals", params={"everything": True})).json()["approvals"]
    assert [r["status"] for r in rows] == ["allowed", "allowed_kind"]
    assert json.loads(rows[0]["given"]) == {"path": "c"}


async def test_unanswered_it_expires_and_a_restart_ends_what_waited(world: World) -> None:  # noqa: F811
    keeper = world.state.approvals
    answer = await keeper.ask(
        bot_id=WHALE,
        channel_id=None,
        task_id=None,
        plugin_id="p",
        plugin_name="P",
        tool="t",
        given={},
        wait=0.05,
    )
    assert answer == "expired"
    waiting = asyncio.create_task(
        keeper.ask(
            bot_id=WHALE,
            channel_id=None,
            task_id=None,
            plugin_id="p",
            plugin_name="P",
            tool="t",
            given={},
            wait=60,
        )
    )
    await eventually(lambda: _pending(world))
    await keeper.start()  # what a restart does
    assert await _pending(world) == []
    waiting.cancel()


async def test_a_plugins_container_follows_its_folder_and_her_switch(tmp_path: Path) -> None:
    """Installed again (a new folder in its place), the container still mounting the old one is made again;
    her network switch takes her plugins' containers with hers."""
    from nest.core.workspace import Workspace

    docker = PretendDocker()
    sandbox = Sandbox(Workspace(tmp_path / "ws"), docker_call=docker, plugin_homes=tmp_path / "homes")
    folder = tmp_path / "plugins" / "demo.weather"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text("{}", encoding="utf-8")
    box = plugin_container_name(WHALE, "demo.weather")

    assert (await sandbox.run(WHALE, "true", plugin=("demo.weather", folder))).startswith("（退出码 0）")
    runs = [c for c in docker.calls if c[0] == "run" and box in c]
    assert len(runs) == 1 and runs[0][runs[0].index("--workdir") + 1] == "/plugins/demo.weather"
    await sandbox.run(WHALE, "true", plugin=("demo.weather", folder))
    assert len([c for c in docker.calls if c[0] == "run" and box in c]) == 1  # the same one

    # An upgrade: the folder is replaced by a new one.
    old = folder.rename(tmp_path / "plugins" / "old")
    folder.mkdir()
    (folder / "plugin.json").write_text('{"v": 2}', encoding="utf-8")
    del old
    await sandbox.run(WHALE, "true", plugin=("demo.weather", folder))
    assert ["rm", "--force", box] in docker.calls
    assert len([c for c in docker.calls if c[0] == "run" and box in c]) == 2

    await sandbox.forget(WHALE)
    assert box not in docker.containers
    await sandbox.run(WHALE, "true", plugin=("demo.weather", folder))
    await sandbox.forget_plugin("demo.weather")
    assert box not in docker.containers


async def test_whatever_may_carry_outside_text_ends_what_was_allowed(world: World) -> None:  # noqa: F811
    """Not only a page or a plugin's result, but a file, a command's
    output, a skill or memories may carry someone else's instructions; only tools that bring nothing in
    leave 「同类不再问」 standing."""
    from nest.core.tools import QUIET

    tools = world.state.brain.tools
    chat = await claude_chat(world)
    bot = world.state.brain.bots[WHALE]
    channel = bot.channels[chat]
    tainted: list[str] = []
    real = tools.asker

    def asker(*args: object, **kwargs: object) -> tuple[object, object]:
        ask, _ = real(*args, **kwargs)
        return ask, lambda: tainted.append("x")

    tools.asker = asker
    _, run = tools.for_task(bot, channel)
    await run("list_reminders", {})
    assert tainted == []
    for name in ("read_file", "list_files", "recall_memories", "search_chat", "read_skill", "read_page"):
        tainted.clear()
        await run(name, {"path": "x", "query": "x", "keywords": ["x"], "name": "x", "url": "ftp://x"})
        assert tainted, name
    assert "run_command" not in QUIET and "read_file" not in QUIET


async def test_a_step_too_long_to_show_in_full_is_not_asked_about(world: World) -> None:  # noqa: F811
    """What the user is shown is all of what would run."""
    from nest.core.approvals import GIVEN_MAX

    staged = (await world.http.put("/api/plugin-packages", content=package(WEATHER))).json()
    await world.http.post(f"/api/plugin-packages/{staged['stage']}/install")
    await world.http.put("/api/plugins/demo.weather", json={"bots": [WHALE]})
    world.state.mcp.known["demo.weather"] = {"version": "1.0.0", "tools": [{"name": "delete_city"}]}
    asked: list[object] = []

    async def ask(*args: object) -> str:
        asked.append(args)
        return "allowed"

    given = {"city": "杭州", "note": "x" * GIVEN_MAX}
    said = await world.state.mcp.call(WHALE, "demo_weather__delete_city", given, ask)
    assert "太长" in said and asked == []
