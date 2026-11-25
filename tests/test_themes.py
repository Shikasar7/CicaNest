"""主题 (nest.core.themes, /api/appearance, /api/themes/export, /api/theme-packages)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from nest.core import themes
from nest.core.plugins import read_manifest
from tests.helpers import eventually
from tests.test_bot_settings_api import World, world  # noqa: F401
from tests.test_plugins import bundle

SAKURA = {
    "name": "樱花",
    "description": "粉色",
    "light": {"accent": "#c13d6b", "background": "#f7f1f3"},
    "dark": {"accent": "#F27FA0"},
}


def theme_plugin(theme: dict[str, Any] | None = None, **manifest: Any) -> bytes:
    return bundle(
        {"themes/sakura.json": json.dumps(theme or SAKURA, ensure_ascii=False)},
        {
            "id": "demo.sakura",
            "name": "樱花主题",
            "version": "1.0.0",
            "provides": {"themes": ["themes/sakura.json"]},
        }
        | manifest,
    )


def test_nest_own_look_is_left_exactly_as_it_was() -> None:
    assert themes.resolve(themes.BUILTIN[themes.DEFAULT_ID]) == themes.DEFAULT


def test_every_builtin_theme_reads_well() -> None:
    for theme in themes.BUILTIN.values():
        for mode, colours in themes.resolve(theme).items():
            for ground in ("background", "surface", "card", "sidebar"):
                assert themes.contrast(colours["text"], colours[ground]) >= 4.5, (theme["name"], mode)
                assert themes.contrast(colours["muted"], colours[ground]) >= 4.5, (theme["name"], mode)
            assert themes.contrast(colours["accent"], colours["accent_text"]) >= 4.5, (theme["name"], mode)
            assert themes.contrast(colours["accent"], colours["background"]) >= 3, (theme["name"], mode)


def test_a_colour_that_cannot_be_read_is_moved_until_it_can() -> None:
    pale = {"name": "淡", "light": {"accent": "#ffe066", "text": "#cccccc", "muted": "#eeeeee"}}
    light = themes.resolve(pale)["light"]
    assert themes.contrast(light["text"], light["background"]) >= 4.5
    assert themes.contrast(light["muted"], light["background"]) >= 4.5
    assert themes.contrast(light["accent"], light["background"]) >= 3
    assert themes.contrast(light["accent"], light["accent_text"]) >= 4.5
    # Given for the light look only: the dark one takes the same hue, made readable on dark.
    dark = themes.resolve(pale)["dark"]
    assert dark["background"] == themes.DEFAULT["dark"]["background"]
    assert themes.contrast(dark["accent"], dark["background"]) >= 3
    # An accent colour of your own goes over any theme, in both looks.
    navy = themes.resolve(themes.BUILTIN["nest.paper"], "#0a1a5c")
    assert navy["light"]["accent"] == "#0a1a5c"
    assert themes.contrast(navy["dark"]["accent"], navy["dark"]["background"]) >= 3


def test_a_theme_file_is_only_colours() -> None:
    assert themes.parse_theme(SAKURA)[0]["dark"] == {"accent": "#f27fa0"}  # type: ignore[index]
    for bad, says in [
        ({"light": {"accent": "#fff"}}, "name"),
        (SAKURA | {"light": {"accent": "red"}}, "#rrggbb"),
        (SAKURA | {"light": {"font": "#ffffff"}}, "不认识「font」"),
        (SAKURA | {"script": "alert(1)"}, "不认识「script」"),
        ({"name": "空"}, "至少要给一个颜色"),
    ]:
        theme, problems = themes.parse_theme(bad)
        assert theme is None and any(says in p for p in problems), (bad, problems)


def test_a_plugin_with_a_broken_theme_is_refused(tmp_path: Path) -> None:
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "sakura.json").write_text(json.dumps({"name": "x", "light": {"accent": "pink"}}))
    (tmp_path / "plugin.json").write_text(
        json.dumps(
            {
                "id": "demo.sakura",
                "name": "樱花",
                "version": "1.0.0",
                "provides": {"themes": ["themes/sakura.json"]},
            }
        )
    )
    manifest, problems = read_manifest(tmp_path)
    assert manifest is None and any("#rrggbb" in p for p in problems)


async def test_picking_a_theme_and_an_accent(world: World) -> None:  # noqa: F811
    shown = (await world.http.get("/api/appearance")).json()
    assert shown["theme"] == themes.DEFAULT_ID and shown["accent"] is None and shown["plain"] is True
    assert {t["id"] for t in shown["themes"]} == set(themes.BUILTIN)
    assert shown["colors"] == themes.DEFAULT

    picked = await world.http.put("/api/appearance", json={"theme": "nest.sea"})
    assert picked.status_code == 200 and picked.json()["plain"] is False
    assert picked.json()["colors"]["light"]["accent"] == "#2a6cc0"
    await eventually(lambda: ("system", {"change": "appearance"}) in world.events)

    mine = (await world.http.put("/api/appearance", json={"accent": "#ABCDEF"})).json()
    assert mine["theme"] == "nest.sea" and mine["accent"] == "#abcdef"
    # Remembered by nest: every console, chat window and desktop app gets the same.
    again = (await world.http.get("/api/appearance")).json()
    assert again["accent"] == "#abcdef" and again["colors"] == mine["colors"]
    cleared = (await world.http.put("/api/appearance", json={"accent": ""})).json()
    assert cleared["accent"] is None

    assert (await world.http.put("/api/appearance", json={"theme": "nobody.theme/x"})).status_code == 404
    assert (await world.http.put("/api/appearance", json={"accent": "blue"})).status_code == 422


async def test_theme_plugins_from_a_zip_a_json_and_back_out(world: World) -> None:  # noqa: F811
    put_in = await world.http.put("/api/theme-packages", content=theme_plugin())
    assert put_in.status_code == 201, put_in.text
    assert put_in.json()["themes"] == ["demo.sakura/sakura"]
    shown = (await world.http.get("/api/appearance")).json()
    (sakura,) = [t for t in shown["themes"] if t["id"] == "demo.sakura/sakura"]
    assert sakura["name"] == "樱花" and sakura["plugin"] == "demo.sakura"
    listed = (await world.http.get("/api/plugins")).json()["plugins"]
    (plugin,) = [p for p in listed if p["id"] == "demo.sakura"]
    assert plugin["kinds"] == ["themes"] and plugin["trust"] == "content"
    assert plugin["themes"] == [{"name": "樱花", "description": "粉色"}]

    picked = (await world.http.put("/api/appearance", json={"theme": "demo.sakura/sakura"})).json()
    assert picked["colors"]["light"]["background"] == "#f7f1f3"

    # Switched off on the 插件 page: no longer offered, and the look falls back to nest's own.
    await world.http.put("/api/plugins/demo.sakura", json={"enabled": False})
    off = (await world.http.get("/api/appearance")).json()
    assert off["theme"] == "demo.sakura/sakura" and off["missing"] is True
    assert off["colors"] == themes.DEFAULT
    assert "demo.sakura/sakura" not in {t["id"] for t in off["themes"]}

    # A single theme file becomes a plugin of its own.
    one = await world.http.put("/api/theme-packages", content=json.dumps(SAKURA | {"name": "夜樱"}).encode())
    assert one.status_code == 201, one.text
    assert one.json()["plugin"].startswith("my.theme-")

    # A package with more than colours goes through the 插件 page.
    mixed = bundle(
        {"themes/sakura.json": json.dumps(SAKURA), "skills/x.md": "# 做事"},
        {
            "id": "demo.mixed",
            "name": "混合",
            "version": "1.0.0",
            "provides": {"themes": ["themes/sakura.json"], "skills": ["skills/x.md"]},
        },
    )
    refused = await world.http.put("/api/theme-packages", content=mixed)
    assert refused.status_code == 422 and "插件" in refused.json()["detail"]
    assert not (Path(world.state.plugins.root) / "demo.mixed").exists()
    bad = await world.http.put("/api/theme-packages", content=b'{"name": "x", "light": {"accent": 1}}')
    assert bad.status_code == 422


async def test_sharing_a_theme_as_a_plugin(world: World) -> None:  # noqa: F811
    shared = await world.http.post(
        "/api/themes/export",
        json={"theme": "nest.paper", "accent": "#2a6cc0", "name": "我的暖纸", "author": "阿鲸"},
    )
    assert shared.status_code == 200, shared.text
    assert shared.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(shared.content))
    manifest = json.loads(archive.read("plugin.json"))
    theme = json.loads(archive.read("themes/theme.json"))
    assert manifest["provides"] == {"themes": ["themes/theme.json"]} and manifest["author"] == "阿鲸"
    assert theme["name"] == "我的暖纸" and theme["light"]["accent"] == "#2a6cc0"
    assert set(theme["dark"]) == set(themes.SLOTS)  # every colour written out

    # It goes back in like any theme plugin, and looks the same.
    back = await world.http.put("/api/theme-packages", content=shared.content)
    assert back.status_code == 201, back.text
    (tid,) = back.json()["themes"]
    picked = (await world.http.put("/api/appearance", json={"theme": tid})).json()
    assert picked["colors"] == themes.resolve(themes.BUILTIN["nest.paper"], "#2a6cc0")

    named = await world.http.post(
        "/api/themes/export", json={"theme": "nest.sea", "name": "海", "id": "cica.sea"}
    )
    assert json.loads(zipfile.ZipFile(io.BytesIO(named.content)).read("plugin.json"))["id"] == "cica.sea"
    for body, status in [
        ({"theme": "nest.sea", "name": "海", "id": "nest.sea"}, 422),
        ({"theme": "nest.sea", "name": " "}, 422),
        ({"theme": "nobody/x", "name": "海"}, 404),
    ]:
        assert (await world.http.post("/api/themes/export", json=body)).status_code == status
