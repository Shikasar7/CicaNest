"""Bot pictures: fetching the QQ avatar, uploading one, choosing which is shown, serving them.

Tencent's avatar server is the `Web` MockTransport of the 监控 tests: nothing here reaches the network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from nest.api.routes_avatars import QQ_AVATAR
from nest.storage.avatars import MAX_BYTES, image_type
from tests.test_watches import PONY, QUIET, WHALE, World, running

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x02" * 40
GIF = b"GIF89a" + b"\x03" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
NOTHING = {"chosen": None, "qq": None, "custom": None}


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with running(tmp_path, QUIET) as w:
        yield w


def path(bot: str, tail: str = "") -> str:
    return f"/api/bots/{bot.replace(':', '%3A')}/avatar{tail}"


def serve_qq(world: World, uin: str, body: bytes, content_type: str = "image/jpeg") -> None:
    async def page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    world.web.pages[QQ_AVATAR.format(uin=uin)] = page


async def views(world: World) -> dict[str, dict[str, object]]:
    response = await world.http.get("/api/avatars")
    assert response.status_code == 200
    return response.json()


def avatar_events(world: World) -> list[dict[str, object]]:
    return [data for kind, data in world.events if kind == "avatars"]


def test_image_type_reads_the_first_bytes() -> None:
    assert [image_type(data) for data in (PNG, JPEG, WEBP, GIF)] == [
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    ]
    assert image_type(SVG) is None
    assert image_type(b"RIFF\x24\x00\x00\x00WAVEfmt ") is None
    assert image_type(b"") is None


async def test_every_bot_starts_with_its_brush_character(world: World) -> None:
    assert await views(world) == {WHALE: NOTHING, PONY: NOTHING}
    response = await world.http.get(path(WHALE, "/qq"))
    assert response.status_code == 404
    assert response.json()["detail"] == "还没有 QQ 头像，先获取一次"


async def test_fetching_the_qq_avatar_stores_and_shows_it(world: World) -> None:
    serve_qq(world, "10001", JPEG)
    response = await world.http.post(path(WHALE, "/qq"))
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["chosen"] == "qq" and isinstance(view["qq"], float) and view["custom"] is None
    assert world.web.requests == [QQ_AVATAR.format(uin="10001")]
    assert (await views(world))[PONY] == NOTHING

    picture = await world.http.get(path(WHALE, "/qq"), params={"v": view["qq"]})
    assert picture.status_code == 200
    assert picture.content == JPEG
    assert picture.headers["content-type"] == "image/jpeg"
    assert "immutable" in picture.headers["cache-control"]
    assert picture.headers["x-content-type-options"] == "nosniff"
    assert avatar_events(world) == [{"bot_id": WHALE}]


async def test_a_new_qq_avatar_replaces_the_old_one(world: World) -> None:
    serve_qq(world, "10001", JPEG)
    first = (await world.http.post(path(WHALE, "/qq"))).json()
    serve_qq(world, "10001", PNG, "image/png")
    second = (await world.http.post(path(WHALE, "/qq"))).json()
    assert second["qq"] > first["qq"]
    picture = await world.http.get(path(WHALE, "/qq"))
    assert picture.content == PNG and picture.headers["content-type"] == "image/png"


async def test_what_qq_sends_must_be_a_picture(world: World) -> None:
    serve_qq(world, "10001", b"<html>busy</html>", "image/jpeg")  # the claimed type is not trusted
    response = await world.http.post(path(WHALE, "/qq"))
    assert response.status_code == 502
    assert response.json()["detail"] == "QQ 返回的不是图片，稍后再试"
    assert (await views(world))[WHALE] == NOTHING


async def test_qq_failing_is_one_sentence(world: World) -> None:
    response = await world.http.post(path(WHALE, "/qq"))  # nothing served: HTTP 404
    assert response.status_code == 502
    assert response.json()["detail"] == "没能从 QQ 取到头像：网站返回了 HTTP 404"
    assert avatar_events(world) == []


async def test_uploading_shows_the_picture_and_keeps_the_qq_one(world: World) -> None:
    serve_qq(world, "10001", JPEG)
    await world.http.post(path(WHALE, "/qq"))
    response = await world.http.put(
        path(WHALE, "/custom"), content=WEBP, headers={"content-type": "image/webp"}
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["chosen"] == "custom" and view["qq"] is not None and view["custom"] is not None
    picture = await world.http.get(path(WHALE, "/custom"))
    assert picture.content == WEBP and picture.headers["content-type"] == "image/webp"

    for chosen in ("qq", "glyph", "custom"):
        response = await world.http.put(path(WHALE), json={"chosen": chosen})
        assert response.status_code == 200, response.text
        assert response.json()["chosen"] == (None if chosen == "glyph" else chosen)
    assert (await views(world))[WHALE]["chosen"] == "custom"
    assert len(avatar_events(world)) == 5


async def test_the_type_comes_from_the_bytes_not_the_request(world: World) -> None:
    response = await world.http.put(
        path(WHALE, "/custom"), content=GIF, headers={"content-type": "image/png"}
    )
    assert response.status_code == 200
    picture = await world.http.get(path(WHALE, "/custom"))
    assert picture.headers["content-type"] == "image/gif"


@pytest.mark.parametrize("body", [SVG, b"", b"hello"])
async def test_only_pictures_can_be_uploaded(world: World, body: bytes) -> None:
    response = await world.http.put(
        path(WHALE, "/custom"), content=body, headers={"content-type": "image/png"}
    )
    assert response.status_code == 415
    assert response.json()["detail"] == "只能用 PNG、JPEG、WebP 或 GIF 图片"
    assert (await views(world))[WHALE] == NOTHING


async def test_big_uploads_are_refused(world: World) -> None:
    body = PNG + b"\x00" * MAX_BYTES
    response = await world.http.put(path(WHALE, "/custom"), content=body)
    assert response.status_code == 413
    assert response.json()["detail"] == "图片太大了（超过 2 MB），换一张小一点的"

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: the limit holds while reading
        for _ in range(3):
            yield b"\x00" * (MAX_BYTES // 2)

    response = await world.http.put(path(WHALE, "/custom"), content=chunks())
    assert response.status_code == 413
    assert (await views(world))[WHALE] == NOTHING


async def test_choosing_a_picture_that_is_not_there(world: World) -> None:
    for chosen, detail in (("qq", "还没有 QQ 头像，先获取一次"), ("custom", "还没有上传图片")):
        response = await world.http.put(path(PONY), json={"chosen": chosen})
        assert response.status_code == 409
        assert response.json()["detail"] == detail
    response = await world.http.put(path(PONY), json={"chosen": "glyph"})
    assert response.status_code == 200 and response.json() == NOTHING
    assert (await world.http.put(path(PONY), json={"chosen": "photo"})).status_code == 422


async def test_deleting_the_upload_brings_back_the_character(world: World) -> None:
    await world.http.put(path(WHALE, "/custom"), content=PNG)
    response = await world.http.delete(path(WHALE, "/custom"))
    assert response.status_code == 200 and response.json() == NOTHING
    assert (await world.http.get(path(WHALE, "/custom"))).status_code == 404
    assert (await world.http.delete(path(WHALE, "/custom"))).status_code == 200  # already gone: fine


async def test_unknown_bots(world: World) -> None:
    ghost = "qq:999"
    assert (await world.http.post(path(ghost, "/qq"))).status_code == 404
    assert (await world.http.put(path(ghost, "/custom"), content=PNG)).status_code == 404
    assert (await world.http.put(path(ghost), json={"chosen": "glyph"})).status_code == 404
    assert (await world.http.delete(path(ghost, "/custom"))).status_code == 404
    assert world.web.requests == []


async def test_pictures_need_the_session(world: World) -> None:
    await world.http.put(path(WHALE, "/custom"), content=PNG)
    response = await world.http.get(path(WHALE, "/custom"), headers={"authorization": ""})
    assert response.status_code == 401
    response = await world.http.put(
        path(WHALE, "/custom"), content=JPEG, headers={"sec-fetch-site": "cross-site"}
    )
    assert response.status_code == 403
    picture = await world.http.get(path(WHALE, "/custom"))
    assert picture.content == PNG
