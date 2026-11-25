"""伙伴包: a companion exported as a plugin-shaped zip — persona, what she remembers of
the user, her records — and imported again as a new companion on the desk; skills and pets in a pack go to her
alone; a broken pack is refused before anything is made."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from tests.test_bot_settings_api import World, world  # noqa: F401

PERSONA = "你是阿月，说话轻轻的，喜欢看星星。"
COMPANION = {
    "name": "阿月",
    "aliases": ["小月"],
    "provider_id": "ark",
    "persona": PERSONA,
    "examples": "嗯……今晚月亮很圆呢。",
    "user_name": "阿鲸",
    "points": [
        {"kind": "like", "content": "喜欢热可可", "due": ""},
        {"kind": "promise", "content": "周末一起看流星雨", "due": "2026-12-13"},
    ],
    "records": [{"title": "第一次聊天", "body": "那天聊到了猎户座。", "source": "旧手机"}],
}


def unzip(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {n: archive.read(n).decode("utf-8") for n in archive.namelist()}


def rezip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


async def _export(world: World, bot_id: str, **params: Any) -> dict[str, str]:  # noqa: F811
    got = await world.http.get(f"/api/companions/{bot_id}/export", params=params)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "application/zip"
    assert got.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    return unzip(got.content)


async def test_a_companion_goes_out_as_a_pack_and_comes_back_as_a_new_one(world: World) -> None:  # noqa: F811
    made = (await world.http.post("/api/companions", json=COMPANION)).json()
    files = await _export(world, made["bot_id"])
    manifest = json.loads(files["plugin.json"])
    assert manifest["id"].startswith("companion.c") and manifest["provides"] == {"companion": "companion"}
    body = json.loads(files["companion/companion.json"])
    assert (body["format"], body["name"], body["aliases"], body["user_name"]) == (1, "阿月", ["小月"], "阿鲸")
    assert body["persona"] == PERSONA and body["examples"] == "嗯……今晚月亮很圆呢。"
    assert sorted(p["content"] for p in body["points"]) == ["周末一起看流星雨", "喜欢热可可"]
    assert next(p for p in body["points"] if p["kind"] == "promise")["due"] == "2026-12-13"
    (record,) = body["records"]
    assert record["title"] == "第一次聊天" and files[f"companion/{record['file']}"] == "那天聊到了猎户座。"

    bare = await _export(world, made["bot_id"], memories=False, records=False)
    bare_body = json.loads(bare["companion/companion.json"])
    assert bare_body["points"] == [] and bare_body["records"] == [] and bare_body["persona"] == PERSONA

    pack = rezip(files)
    shown = await world.http.put("/api/companion-packages", content=pack)
    assert shown.status_code == 200, shown.text
    preview = shown.json()
    assert preview["companion"]["name"] == "阿月" and preview["extras"] == []
    assert preview["companion"]["records"] == [{"title": "第一次聊天", "chars": 9}]
    brought = await world.http.post(
        f"/api/companion-packages/{preview['stage']}/import", json={"name": "阿月二号", "provider_id": "ark"}
    )
    assert brought.status_code == 201, brought.text
    again = brought.json()
    assert again["bot_id"] != made["bot_id"] and again["plugin_id"] is None
    again_files = await _export(world, again["bot_id"])
    again_body = json.loads(again_files["companion/companion.json"])
    assert again_body["name"] == "阿月二号" and again_body["persona"] == PERSONA
    assert sorted(p["content"] for p in again_body["points"]) == ["周末一起看流星雨", "喜欢热可可"]
    assert [r["title"] for r in again_body["records"]] == ["第一次聊天"]
    # The staging folder is gone: the same stage cannot be imported twice.
    twice = await world.http.post(f"/api/companion-packages/{preview['stage']}/import", json={})
    assert twice.status_code == 404


async def test_her_skills_in_a_pack_are_hers_alone(world: World) -> None:  # noqa: F811
    made = (await world.http.post("/api/companions", json=COMPANION)).json()
    files = await _export(world, made["bot_id"])
    manifest = json.loads(files["plugin.json"])
    manifest["provides"]["skills"] = ["skills/看星星.md"]
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False)
    files["skills/看星星.md"] = "# 看星星\n\n先查天气，再看月相。"
    pack = rezip(files)

    ids = []
    for _ in range(2):
        preview = (await world.http.put("/api/companion-packages", content=pack)).json()
        assert preview["extras"] == ["skills"]
        brought = (
            await world.http.post(f"/api/companion-packages/{preview['stage']}/import", json={})
        ).json()
        ids.append((brought["bot_id"], brought["plugin_id"]))
    (first_bot, first_plugin), (second_bot, second_plugin) = ids
    assert first_plugin != second_plugin and first_plugin.startswith(manifest["id"] + ".for")
    plugins = world.state.plugins
    assert plugins.on_for(first_plugin, first_bot) and not plugins.on_for(first_plugin, second_bot)
    assert [k.name for k in plugins.skills_for(second_bot)] == ["看星星"]
    assert plugins.skills_for(made["bot_id"]) == []
    # What made her (persona, what she remembers of the user, records) is not kept with her skills.
    manifest_kept, folder = plugins.installed[first_plugin]
    assert "companion" not in manifest_kept.provides and manifest_kept.trust == "guides"
    assert not (folder / "companion").exists()
    assert sorted(p.name for p in folder.iterdir()) == ["plugin.json", "skills"]


async def test_a_broken_pack_is_refused_before_anything_is_made(world: World) -> None:  # noqa: F811
    before = len(world.state.brain.bots)
    plain = {"plugin.json": json.dumps({"id": "demo.x", "name": "x", "version": "1.0.0"})}
    not_companion = await world.http.put("/api/companion-packages", content=rezip(plain))
    assert not_companion.status_code == 422 and "不是伙伴包" in not_companion.json()["detail"]
    manifest = {"id": "companion.cbad", "name": "x", "version": "1.0.0", "provides": {"companion": "c"}}
    no_body = rezip({"plugin.json": json.dumps(manifest), "c/readme.txt": "x"})
    assert (
        "companion.json"
        in (await world.http.put("/api/companion-packages", content=no_body)).json()["detail"]
    )
    sneaky = rezip(
        {
            "plugin.json": json.dumps(manifest),
            "c/companion.json": json.dumps(
                {"format": 1, "name": "x", "records": [{"title": "t", "file": "../../plugin.json"}]}
            ),
        }
    )
    refused = await world.http.put("/api/companion-packages", content=sneaky)
    assert refused.status_code == 422 and "路径不对" in refused.json()["detail"]
    assert len(world.state.brain.bots) == before
    assert (await world.http.get("/api/companions/nobody/export")).status_code == 404


async def test_a_pack_with_code_for_inside_nest_is_refused(world: World) -> None:  # noqa: F811
    """Code that runs inside nest only comes in through 「插件」 with the user's word that they trust it; a
    companion pack never carries it, and no install path skips that word."""
    from nest.core.plugins import UntrustedPlugin

    made = (await world.http.post("/api/companions", json=COMPANION)).json()
    files = await _export(world, made["bot_id"])
    manifest = json.loads(files["plugin.json"])
    manifest["provides"]["python"] = {"module": "extra"}
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False)
    files["extra.py"] = "def register(nest):\n    pass\n"
    before = len(world.state.brain.bots)
    refused = await world.http.put("/api/companion-packages", content=rezip(files))
    assert refused.status_code == 422 and "Python" in refused.json()["detail"]
    assert len(world.state.brain.bots) == before
    # Staged some other way, the install itself still asks for trust.
    staged = await world.state.plugins.stage(rezip(files))
    with pytest.raises(UntrustedPlugin):
        await world.state.plugins.install(staged["stage"])
    assert manifest["id"] not in world.state.plugins.installed


async def test_a_pack_with_a_program_is_refused(world: World) -> None:  # noqa: F811
    """Imported, a pack's plugin is on for her at once: a program (MCP) is installed only on its own."""
    made = (await world.http.post("/api/companions", json=COMPANION)).json()
    files = await _export(world, made["bot_id"])
    manifest = json.loads(files["plugin.json"])
    manifest["provides"]["mcp"] = {"command": ["python", "server.py"]}
    files["plugin.json"] = json.dumps(manifest, ensure_ascii=False)
    files["server.py"] = "print('hi')"
    refused = await world.http.put("/api/companion-packages", content=rezip(files))
    assert refused.status_code == 422 and "MCP" in refused.json()["detail"]
