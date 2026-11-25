"""插件: the plugin.json format, unpacking a zip safely, installing in two steps, the
switches — nest's own tools are plugins too, on or off and for which companions — and uninstalling."""

from __future__ import annotations

import io
import json
import stat
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nest.core.plugins import BUILTIN, PluginError, read_manifest, read_skill_head, unpack
from tests.fakes.llm import ToolCall
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401
from tests.test_tools import _answer, claude_chat, model

GOOD = {
    "id": "demo.weather",
    "name": "天气",
    "version": "1.0.0",
    "author": "阿鲸",
    "description": "查天气",
    "provides": {
        "skills": ["skills/出门.md"],
        "mcp": {"command": ["python", "-m", "weather"], "install": []},
    },
    "permissions": {
        "network": ["api.weather.example"],
        "secrets": [{"name": "WEATHER_KEY", "label": "天气密钥"}],
        "confirm": ["delete_city"],
    },
}


def package(manifest: dict[str, Any], *, top: str = "", extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{top}plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr(f"{top}skills/出门.md", "# 出门前看天气")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_reading_plugin_json(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "出门.md").write_text("x", encoding="utf-8")
    (tmp_path / "plugin.json").write_text(json.dumps(GOOD), encoding="utf-8")
    manifest, problems = read_manifest(tmp_path)
    assert problems == [] and manifest is not None
    assert manifest.trust == "code" and manifest.secrets == (("WEATHER_KEY", "天气密钥"),)
    assert manifest.view()["kinds"] == ["skills", "mcp"]

    def problems_of(**changes: Any) -> list[str]:
        (tmp_path / "plugin.json").write_text(json.dumps(GOOD | changes), encoding="utf-8")
        return read_manifest(tmp_path)[1]

    assert any("id 要写成" in p for p in problems_of(id="Weather"))
    assert any("留给 nest" in p for p in problems_of(id="nest.weather"))
    assert any("version" in p for p in problems_of(version="v1"))
    assert any("不认识「hooks」" in p for p in problems_of(provides={"hooks": ["x"]}))
    assert any("找不到" in p for p in problems_of(provides={"skills": ["skills/没有.md"]}))
    assert any("相对路径" in p for p in problems_of(provides={"skills": ["../../etc/passwd"]}))
    assert any("secrets" in p for p in problems_of(permissions={"secrets": [{"name": "lower"}]}))
    assert any("network" in p for p in problems_of(permissions={"network": ["http://x"]}))
    python = GOOD | {"provides": {"python": {"module": "demo_weather"}}}
    (tmp_path / "plugin.json").write_text(json.dumps(python), encoding="utf-8")
    assert read_manifest(tmp_path)[0].trust == "trusted"  # type: ignore[union-attr]


def test_unpacking_refuses_what_leads_out(tmp_path: Path) -> None:
    assert unpack(package(GOOD, top="weather/"), tmp_path / "a").name == "weather"
    with pytest.raises(PluginError, match="走出去"):
        unpack(package(GOOD, extra={"../escape.txt": b"x"}), tmp_path / "b")
    link = io.BytesIO()
    with zipfile.ZipFile(link, "w") as archive:
        archive.writestr("plugin.json", "{}")
        info = zipfile.ZipInfo("evil")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(PluginError, match="链接"):
        unpack(link.getvalue(), tmp_path / "c")
    with pytest.raises(PluginError, match="不是 zip"):
        unpack(b"hello", tmp_path / "d")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "x")
    with pytest.raises(PluginError, match=r"没有 plugin.json"):
        unpack(empty.getvalue(), tmp_path / "e")
    assert not (tmp_path.parent / "escape.txt").exists()


async def test_installing_switching_and_uninstalling(world: World) -> None:  # noqa: F811
    listing = (await world.http.get("/api/plugins")).json()
    assert [p["id"] for p in listing["plugins"]] == [b.id for b in BUILTIN]
    assert all(p["enabled"] and p["bots"] is None for p in listing["plugins"] if p["id"] != "nest.windows")
    # Her doing things on the user's PC: on for no companion until the user picks.
    assert next(p for p in listing["plugins"] if p["id"] == "nest.windows")["bots"] == []

    staged = await world.http.put("/api/plugin-packages", content=package(GOOD, top="weather/"))
    assert staged.status_code == 200, staged.text
    shown = staged.json()
    assert shown["upgrade"] is None and shown["plugin"]["id"] == "demo.weather"
    assert shown["plugin"]["trust"] == "code" and shown["plugin"]["permissions"]["network"] == [
        "api.weather.example"
    ]
    # Read, not installed yet.
    assert len((await world.http.get("/api/plugins")).json()["plugins"]) == len(BUILTIN)
    done = await world.http.post(f"/api/plugin-packages/{shown['stage']}/install")
    assert done.status_code == 201 and done.json() == {"id": "demo.weather"}
    await eventually(lambda: ("plugins", {}) in world.events)
    (weather,) = [
        p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "demo.weather"
    ]
    assert weather["enabled"] is True and weather["bots"] == []  # nobody has it until the user picks
    assert (Path(world.state.plugins.root) / "demo.weather" / "skills" / "出门.md").is_file()

    # The same id again: an upgrade.
    newer = GOOD | {"version": "1.1.0"}
    again = (await world.http.put("/api/plugin-packages", content=package(newer))).json()
    assert again["upgrade"] == "1.0.0"
    await world.http.post(f"/api/plugin-packages/{again['stage']}/install")
    (weather,) = [
        p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == "demo.weather"
    ]
    assert weather["version"] == "1.1.0" and weather["bots"] == []

    assert (await world.http.put("/api/plugins/demo.weather", json={"bots": [WHALE]})).status_code == 200
    assert world.state.plugins.on_for("demo.weather", WHALE) and not world.state.plugins.on_for(
        "demo.weather", PONY
    )
    assert (await world.http.put("/api/plugins/demo.weather", json={"bots": ["nobody"]})).status_code == 404
    assert (await world.http.delete("/api/plugins/demo.weather")).status_code == 200
    assert not (Path(world.state.plugins.root) / "demo.weather").exists()
    assert (await world.http.delete("/api/plugins/nest.shell")).status_code == 404  # can only be switched off
    bad = await world.http.put("/api/plugin-packages", content=package(GOOD | {"id": "Bad"}))
    assert bad.status_code == 422 and "id 要写成" in bad.json()["detail"]
    assert (await world.http.post("/api/plugin-packages/0123456789abcdef/install")).status_code == 404


async def test_a_switched_off_tool_is_not_offered_to_her(world: World) -> None:  # noqa: F811
    chat = await claude_chat(world)
    off = await world.http.put("/api/plugins/nest.reminders", json={"all_bots": False, "bots": [PONY]})
    assert off.status_code == 200
    assert (await world.http.put("/api/plugins/nest.tasks", json={"enabled": False})).status_code == 200
    model(world).queue(ToolCall("set_reminder", {"when": "2099-01-01 09:00", "what": "x"}), "好。")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "明天九点提醒我"})
    assert await eventually(lambda: _answer(world, chat)) == "好。"
    offered = model(world).tool_lists[0]
    assert "set_reminder" not in offered and "start_task" not in offered and "read_page" in offered
    assert dict(model(world).tool_results)["set_reminder"] == "set_reminder 这个工具现在对你关着"
    assert "后台" not in model(world).requests[0][1].content.split("（你可以用工具")[-1].split("）")[-1]
    # Back on for everyone.
    await world.http.put("/api/plugins/nest.reminders", json={"all_bots": True})
    assert world.state.plugins.tool_allowed(WHALE, "set_reminder")


async def test_a_hand_edited_plugin_that_cannot_be_read_is_shown(world: World) -> None:  # noqa: F811
    folder = Path(world.state.plugins.root) / "demo.broken"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text("{not json", encoding="utf-8")
    await world.state.plugins.load()
    broken = (await world.http.get("/api/plugins")).json()["broken"]
    assert "demo.broken" in broken and "读不了" in broken["demo.broken"][0]


# -- step 3: skills and pets -------------------------------------------------------------------------------

SKILL = """---
name: 出门提醒
description: 出门前看天气，提醒带伞
---
# 出门提醒

1. 先查天气
2. 下雨就提醒带伞
"""
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def bundle(files: dict[str, bytes | str], manifest: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


async def _install(world: World, data: bytes) -> str:  # noqa: F811
    staged = await world.http.put("/api/plugin-packages", content=data)
    assert staged.status_code == 200, staged.text
    done = await world.http.post(f"/api/plugin-packages/{staged.json()['stage']}/install")
    return done.json()["id"]


def test_a_skill_says_what_it_is_called(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(SKILL, encoding="utf-8")
    assert read_skill_head(tmp_path / "a.md") == ("出门提醒", "出门前看天气，提醒带伞")
    (tmp_path / "b.md").write_text("# 写周报\n\n每周五把这周做的事整理成三段。\n", encoding="utf-8")
    assert read_skill_head(tmp_path / "b.md") == ("写周报", "每周五把这周做的事整理成三段。")
    (tmp_path / "c.md").write_text("", encoding="utf-8")
    assert read_skill_head(tmp_path / "c.md") == ("c", "")


async def test_she_knows_her_skills_and_reads_one_when_she_needs_it(world: World) -> None:  # noqa: F811
    manifest = {"id": "demo.outing", "name": "出门", "version": "1.0.0", "provides": {"skills": ["出门.md"]}}
    # Before installing, the user reads it in full: a skill tells her what to do.
    preview = (
        await world.http.put("/api/plugin-packages", content=bundle({"出门.md": SKILL}, manifest))
    ).json()
    assert preview["plugin"]["trust"] == "guides"
    assert preview["plugin"]["skills"] == [
        {"name": "出门提醒", "description": "出门前看天气，提醒带伞", "text": SKILL}
    ]
    await world.http.delete(f"/api/plugin-packages/{preview['stage']}")
    plugin = await _install(world, bundle({"出门.md": SKILL}, manifest))
    await world.http.put(f"/api/plugins/{plugin}", json={"bots": [WHALE]})
    shown = next(p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == plugin)
    assert shown["skills"] == [{"name": "出门提醒", "description": "出门前看天气，提醒带伞"}]

    chat = await claude_chat(world)
    model(world).queue(
        ToolCall("read_skill", {"name": "出门提醒"}),
        ToolCall("read_skill", {"name": "没有的"}),
        "记得带伞哦。",
    )
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "我要出门了"})
    assert await eventually(lambda: _answer(world, chat)) == "记得带伞哦。"
    assert "read_skill" in model(world).tool_lists[0]
    assert "- 出门提醒：出门前看天气，提醒带伞" in model(world).requests[0][1].content
    results = model(world).tool_results
    assert results[0][1].startswith("技能「出门提醒」的做法：") and "下雨就提醒带伞" in results[0][1]
    assert results[1][1] == "没有叫「没有的」的技能（你会的：出门提醒）"
    # Not hers: the other companion does not have it.
    assert world.state.plugins.skills_for(PONY) == []


async def test_pets_skins_and_accessories_from_a_plugin(world: World) -> None:  # noqa: F811
    pet = {
        "name": "阿鲸",
        "label": "夏装",
        "frame": 96,
        "actions": {"idle": {"file": "idle.png", "frames": 4}},
    }
    skin = pet | {"skin_of": "阿马", "label": "冬装"}
    hat = {"label": "草帽", "rendering": "pixel", "width": 0.4}
    files = {
        "pets/summer/pet.json": json.dumps(pet),
        "pets/summer/idle.png": PNG,
        "pets/winter/pet.json": json.dumps(skin),
        "pets/winter/idle.png": PNG,
        "pets/hat/acc.json": json.dumps(hat),
        "pets/hat/item.png": PNG,
        "pets/hat/notes.txt": "not served",
    }
    manifest = {
        "id": "demo.wardrobe",
        "name": "衣柜",
        "version": "2.0.0",
        "provides": {"pets": ["pets/summer", "pets/winter", "pets/hat"]},
    }
    plugin = await _install(world, bundle(files, manifest))
    listed = (await world.http.get("/api/pet-plugins")).json()["plugins"]
    assert [(p["id"], p["version"], p["bots"]) for p in listed] == [(plugin, "2.0.0", [])]
    items = {i["path"]: i for i in listed[0]["items"]}
    assert (items["pets/summer"]["kind"], items["pets/summer"]["for"], items["pets/summer"]["preview"]) == (
        "pet",
        "阿鲸",
        "idle.png",
    )
    assert (items["pets/winter"]["kind"], items["pets/winter"]["for"]) == ("skin", "阿马")
    assert (items["pets/hat"]["kind"], items["pets/hat"]["label"], items["pets/hat"]["files"]) == (
        "accessory",
        "草帽",
        ["acc.json", "item.png"],
    )
    got = await world.http.get(f"/api/pet-plugins/{plugin}/file", params={"path": "pets/hat/item.png"})
    assert got.status_code == 200 and got.json()["data"].startswith("iVBORw0KGgo")
    for bad in ("pets/hat/notes.txt", "pets/../plugin.json", "plugin.json", "pets/other/item.png"):
        assert (
            await world.http.get(f"/api/pet-plugins/{plugin}/file", params={"path": bad})
        ).status_code == 404
    shown = next(p for p in (await world.http.get("/api/plugins")).json()["plugins"] if p["id"] == plugin)
    assert [i["kind"] for i in shown["pets"]] == ["pet", "skin", "accessory"]
    # Switched off: the desktop app no longer gets it.
    await world.http.put(f"/api/plugins/{plugin}", json={"enabled": False})
    assert (await world.http.get("/api/pet-plugins")).json()["plugins"] == []


def test_a_pet_folder_must_be_a_pet_or_an_accessory(tmp_path: Path) -> None:
    (tmp_path / "pets" / "empty").mkdir(parents=True)
    manifest = {"id": "demo.pets", "name": "x", "version": "1.0.0", "provides": {"pets": ["pets/empty"]}}
    (tmp_path / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any("pet.json" in p for p in read_manifest(tmp_path)[1])


def test_a_secret_cannot_take_a_name_the_system_uses(tmp_path: Path) -> None:
    """DOCKER_HOST, PATH, HTTPS_PROXY… would change how the docker
    program on this machine or the container behaves."""
    from nest.core.plugins import read_manifest, reserved_secret

    for name in (
        "DOCKER_HOST",
        "PATH",
        "HOME",
        "HTTPS_PROXY",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
    ):
        assert reserved_secret(name), name
    assert not reserved_secret("WEATHER_KEY") and not reserved_secret("OPENAI_API_KEY")
    asked = {"secrets": [{"name": "DOCKER_HOST", "label": "x"}]}
    manifest = {"id": "demo.x", "name": "x", "version": "1.0.0", "permissions": asked}
    (tmp_path / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    found, problems = read_manifest(tmp_path)
    assert found is None and any("DOCKER_HOST" in p for p in problems)


async def test_packages_never_installed_are_cleared_at_the_next_start(world: World) -> None:  # noqa: F811
    import os

    plugins = world.state.plugins
    staging = plugins.root / ".staging"
    old, fresh = staging / "0123456789abcdef", staging / "fedcba9876543210"
    for holder in (old, fresh):
        (holder / "x").mkdir(parents=True)
    long_ago = time.time() - 2 * 86400
    os.utime(old, (long_ago, long_ago))
    await plugins.load()
    assert not old.exists() and fresh.exists()
