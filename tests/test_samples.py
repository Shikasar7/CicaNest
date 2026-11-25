"""示例伙伴: nobody is there until one is taken in; then she is a companion like any, with her picture,
stickers and pet. And what a companion pack may carry of hers."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Any

from PIL import Image

from nest.core.samples import samples
from nest.storage.avatars import image_type
from tests.test_bot_settings_api import World, world  # noqa: F401


def picture(colour: str = "red", kind: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), colour).save(buffer, kind)
    return buffer.getvalue()


STICKERS = json.dumps({"name": "日常", "items": [{"mood": "开心", "file": "a.png"}]}).encode()


def pack(companion: dict[str, Any], provides: dict[str, Any], files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    manifest = {"id": "demo.axing", "name": "阿星", "version": "1.0.0"}
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest | {"provides": {"companion": "c"} | provides}))
        archive.writestr("c/companion.json", json.dumps({"format": 1, "name": "阿星"} | companion))
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


async def bring(world: World, data: bytes, **body: Any) -> dict[str, Any]:  # noqa: F811
    staged = await world.http.put("/api/companion-packages", content=data)
    assert staged.status_code == 200, staged.text
    made = await world.http.post(f"/api/companion-packages/{staged.json()['stage']}/import", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def test_the_samples_that_come_with_nest_can_be_read() -> None:
    found = samples()
    assert [s.pack.name for s in found] == ["三花", "阿月", "橙橙"]
    for sample in found:
        assert image_type(sample.pack.avatar) == "image/webp"
        assert sample.pack.persona and sample.pack.examples
        # Nothing about anybody: whoever takes her in is new to her.
        assert (sample.pack.points, sample.pack.records, sample.pack.user_name) == ([], [], "")
        assert set(sample.manifest.view()["kinds"]) == {"pets", "stickers", "companion"}
        assert sample.manifest.trust == "guides" and not sample.manifest.network


async def test_one_is_taken_in_with_all_that_is_hers_and_only_once(world: World) -> None:  # noqa: F811
    before = len(world.state.brain.bots)
    shown = (await world.http.get("/api/samples")).json()
    assert [s["name"] for s in shown] == ["三花", "阿月", "橙橙"]
    assert all(s["bot_id"] is None for s in shown) and len(world.state.brain.bots) == before
    sample = shown[0]
    face = await world.http.get(f"/api/samples/{sample['id']}/avatar")
    assert face.status_code == 200 and face.headers["content-type"] == "image/webp"

    # Two clicks at once: one companion.
    path = f"/api/samples/{sample['id']}"
    answers = await asyncio.gather(*(world.http.post(path, json={"provider_id": "ark"}) for _ in range(2)))
    assert sorted(a.status_code for a in answers) == [201, 409]
    made = next(a for a in answers if a.status_code == 201).json()
    assert next(a for a in answers if a.status_code == 409).json()["detail"] == "三花已经在这里了"
    assert len(world.state.brain.bots) == before + 1
    bot = world.bot(made["bot_id"])
    assert (bot.name, bot.mode, bot.provider_id) == ("三花", "live", "ark")
    (chat,) = bot.channels.values()
    assert chat.persona.startswith("你是三花")

    pictures = (await world.http.get("/api/avatars")).json()[bot.id]
    assert pictures["chosen"] == "custom"
    packs = (await world.http.get(f"/api/sticker-packs/bots/{bot.id}")).json()["packs"]
    (stickers,) = packs
    assert stickers["active"] and [s["mood"] for s in stickers["slots"]] == ["打招呼", "犯困", "得意", "害羞"]
    assert all(s["sticker_id"] for s in stickers["slots"])
    (pet,) = [p for p in world.state.plugins.pets() if p["id"] == made["plugin_id"]]
    assert [i["for"] for i in pet["items"]] == ["三花"] and pet["bots"] == [bot.id]
    # What made her is not kept with her pet: no persona and no pictures that are hers now.
    _, folder = world.state.plugins.installed[made["plugin_id"]]
    assert sorted(p.name for p in folder.iterdir()) == ["pets", "plugin.json"]

    again = (await world.http.get("/api/samples")).json()
    assert [s["bot_id"] for s in again] == [bot.id, None, None]
    assert (await world.http.post("/api/samples/nobody.here", json={})).status_code == 404
    assert (await world.http.get("/api/samples/nobody.here/avatar")).status_code == 404


async def test_without_a_model_she_is_here_but_does_not_talk(world: World) -> None:  # noqa: F811
    sample = (await world.http.get("/api/samples")).json()[1]
    made = (await world.http.post(f"/api/samples/{sample['id']}", json={})).json()
    assert world.bot(made["bot_id"]).mode == "observe"


async def test_a_pack_brings_her_picture_stickers_and_a_pet_of_her_new_name(world: World) -> None:  # noqa: F811
    data = pack(
        {"avatar": "face.png"},
        {"stickers": ["s"], "pets": ["p"]},
        {
            "c/face.png": picture(),
            "s/pack.json": json.dumps(
                {"name": "日常", "items": [{"mood": "开心", "file": "a.png"}]}
            ).encode(),
            "s/a.png": picture("blue"),
            "p/pet.json": json.dumps({"name": "阿星", "actions": {"idle": {"file": "idle.png"}}}).encode(),
            "p/idle.png": picture("green"),
        },
    )
    made = await bring(world, data, name="阿星二号", provider_id="ark")
    bot = world.bot(made["bot_id"])
    assert (await world.http.get("/api/avatars")).json()[bot.id]["chosen"] == "custom"
    (stickers,) = (await world.http.get(f"/api/sticker-packs/bots/{bot.id}")).json()["packs"]
    assert stickers["name"] == "日常" and [s["mood"] for s in stickers["slots"]] == ["开心"]
    (pet,) = [p for p in world.state.plugins.pets() if p["id"] == made["plugin_id"]]
    assert [i["for"] for i in pet["items"]] == ["阿星二号"]


async def test_a_pack_with_only_stickers_leaves_no_plugin(world: World) -> None:  # noqa: F811
    data = pack(
        {},
        {"stickers": ["s"]},
        {
            "s/pack.json": json.dumps(
                {"name": "日常", "items": [{"mood": "开心", "file": "a.png"}]}
            ).encode(),
            "s/a.png": picture("blue"),
        },
    )
    made = await bring(world, data)
    assert made["plugin_id"] is None
    assert (await world.http.get(f"/api/sticker-packs/bots/{made['bot_id']}")).json()["packs"][0][
        "name"
    ] == "日常"


async def test_a_picture_that_is_none_or_outside_the_pack_is_refused(world: World) -> None:  # noqa: F811
    for avatar, files, why in (
        ("face.png", {"c/face.png": b"<svg/>"}, "头像要是"),
        ("../plugin.json", {}, "路径不对"),
        ("none.png", {}, "找不到"),
    ):
        refused = await world.http.put("/api/companion-packages", content=pack({"avatar": avatar}, {}, files))
        assert refused.status_code == 422 and why in refused.json()["detail"], avatar
