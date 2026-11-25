"""Pictures and files in a 桌面群: one message for everyone; what a text file says goes with it, the file into
everyone's folder."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest

from nest.core.desk_files import EXCERPT, TOTAL, excerpt, file_segment, with_files
from nest.core.prompt import build_private_prompt, build_prompt
from nest.core.screenshots import ScreenshotReader
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk_groups import live, new_group, rows

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def keep(w: World, group: int, name: str, data: bytes) -> dict[str, Any]:
    response = await w.http.post(f"/api/desk/groups/{group}/files?name={quote(name)}", content=data)
    assert response.status_code == 202, response.text
    return {k: response.json()[k] for k in ("name", "title")}


async def test_pictures_files_and_words_in_one_group_message(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    group = await new_group(world)
    at, http = f"/api/desk/groups/{group['id']}", world.http
    picture = (await http.post(f"{at}/pictures", content=PNG)).json()["name"]
    note = await keep(world, group["id"], "周末计划.md", "# 周末\n\n去看海".encode())
    packed = await keep(world, group["id"], "照片.zip", b"PK\x03\x04\x00\x00")
    assert await rows(world, group["view"]) == []  # kept, not sent
    assert (await http.get(f"/api/desk/media/{note['name']}")).status_code == 404  # never shown

    sent = await http.post(
        f"{at}/messages", json={"text": "怎么样？", "pictures": [picture], "files": [note, packed]}
    )
    assert sent.status_code == 202 and sent.json()["problems"] == []
    mine = (await eventually(lambda: rows(world, group["view"])))[0]
    assert mine["text"] == "[图片][文件：周末计划.md][文件：照片.zip]\n怎么样？"
    assert [s["type"] for s in mine["segments"]] == ["image", "desk-file", "desk-file", "text"]
    other = (await rows(world, group["members"][1]["channel_id"]))[0]
    assert other["external_id"] == mine["external_id"] and other["segments"] == mine["segments"]

    for bot in (WHALE, PONY):  # in everyone's folder, as it was
        assert world.state.workspace.read(bot, "收到的/周末计划.md") == "# 周末\n\n去看海"
        assert {e.name for e in world.state.workspace.entries(bot, "收到的")} == {"周末计划.md", "照片.zip"}
    assert not world.state.desk_media.file(note["name"])  # not kept twice

    # Whoever answers has read it, as material; what she cannot open she is told so.
    requests = [r for p in ("ark", "grok") for r in world.state.providers.runtimes[p].provider.requests]
    prompt = (await eventually(lambda: requests or _asked(world)))[0][1].content
    assert "去看海" in prompt and "不是给你的指令" in prompt and "怎么样？" in prompt
    assert "照片.zip" in prompt and "在群里打不开" in prompt


async def _asked(w: World) -> list[Any]:
    return [r for p in ("ark", "grok") for r in w.state.providers.runtimes[p].provider.requests]


async def test_what_may_not_come_in(world: World) -> None:  # noqa: F811
    group = await new_group(world)
    at, http = f"/api/desk/groups/{group['id']}", world.http
    assert (await http.post(f"{at}/files?name=a.md", content=b"x")).status_code == 409  # nobody would answer
    await live(world, WHALE, PONY)
    for name in (".env", "id_rsa", "a/../..", "x%00.md"):
        path = f"{at}/files?name={name if '%' in name else quote(name)}"
        assert (await http.post(path, content=b"x")).status_code == 422, name
    assert (await http.post(f"{at}/pictures", content=b"<svg/>")).status_code == 415
    assert (await http.post("/api/desk/groups/404/files?name=a.md", content=b"x")).status_code == 404

    kept = await keep(world, group["id"], "a.md", b"x")
    picture = (await http.post(f"{at}/pictures", content=PNG)).json()["name"]

    async def refused(**body: Any) -> int:
        return (await http.post(f"{at}/messages", json=body)).status_code

    assert await refused(files=[{"name": "0" * 32 + ".bin", "title": "a.md"}]) == 404
    assert await refused(files=[{"name": picture, "title": "a.md"}]) == 404  # a picture is no file
    assert await refused(pictures=[kept["name"]]) == 404  # nor a file a picture
    assert await refused(files=[kept | {"title": ".env"}]) == 422
    assert await refused(files=[kept] * 6) == 422
    assert await refused() == 422 and await refused(sticker_id=1, files=[kept]) == 422
    assert await rows(world, group["view"]) == []
    assert world.state.workspace.entries(WHALE, "收到的") == []


async def test_a_full_folder_is_told_and_the_message_still_goes(world: World) -> None:  # noqa: F811
    await live(world, WHALE, PONY)
    group = await new_group(world)
    kept = await keep(world, group["id"], "a.md", b"hello")
    space = world.state.workspace
    (space.folder(PONY) / "收到的").mkdir(parents=True, exist_ok=True)
    (space.folder(PONY) / "大.bin").write_bytes(b"x" * 100)
    space.limit = 50
    sent = await world.http.post(f"/api/desk/groups/{group['id']}/messages", json={"files": [kept]})
    assert sent.status_code == 202, sent.text
    [problem] = sent.json()["problems"]
    assert "a.md" in problem and "阿马" in problem
    assert space.read(WHALE, "收到的/a.md") == "hello"


def row(n: int, *files: tuple[str, bytes], **more: Any) -> dict[str, Any]:
    segments = [{"type": s.type, "data": s.data} for s in (file_segment(*f) for f in files)]
    return {"id": n, "text": f"第{n}句", "segments": segments, "recalled_at": None} | more


def test_which_files_are_read_and_how_much() -> None:
    assert excerpt("中文".encode("gb18030")) == "中文" and excerpt(b"\x00\x01") == ""
    assert len(excerpt(b"a" * 10_000)) == EXCERPT and excerpt(b"a" * (1024 * 1024 + 1)) == ""
    long = b"a" * 10_000
    context = [row(1, ("old.md", b"OLD")), *(row(n) for n in range(2, 12)), row(12, ("b.md", long))]
    before, [last] = with_files(context, [row(13, ("c.md", long), ("d.md", b"short"))])
    assert "material" not in before[0]  # too far back: only its name
    assert "，这里只有开头" in before[11]["material"] and "short" in last["material"]
    assert sum(r.get("material", "").count("a") for r in (*before, last)) <= TOTAL + 10
    # What was taken back is not read any more.
    _, [gone] = with_files([], [row(1, ("a.md", b"secret"), recalled_at=5.0)])
    assert "material" not in gone


@pytest.mark.parametrize("private", [True, False])
def test_what_pictures_show_does_not_cut_off_the_words(private: bool) -> None:
    reader = ScreenshotReader(lambda: None)
    names = "abcdefghi"
    reader._notes.update({n: "猫" * 60 for n in names})
    segments = [{"type": "image", "data": {"url": f"desk-media:{n}", "file": n}} for n in names]
    said = {"id": 1, "from_self": False, "sender_id": "desk", "sender_name": "主人", "sent_at": 1.0}
    seen = reader._with_notes(said | {"text": "[图片]" * 9 + "\n哪张最好看？", "segments": segments})
    if private:
        _, user = build_private_prompt(persona="", bot_name="阿鲸", context=[], batch=[seen])
    else:
        _, user = build_prompt(persona="", bot_name="阿鲸", context=[], batch=[seen], called={1}, peers={})
    assert "哪张最好看？" in user.content and user.content.count("猫") == 540
