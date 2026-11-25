"""桌面聊天的图片和语音: a picture you send her, her voice beside her words,
读出来, and 语音输入 through a transcription service."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, cast

import httpx

from nest.core.desk_media import DeskMedia, audio_ext, picture_url
from nest.core.screenshots import ScreenshotReader, picture_keys
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP3 = b"ID3\x04audio-bytes"


async def rows(w: World, chat: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{chat}/messages")).json()  # type: ignore[no-any-return]


def test_files_are_told_by_their_first_bytes(tmp_path: Path) -> None:
    media = DeskMedia(tmp_path / "desk-media")
    name = media.save_picture(PNG)
    assert name is not None and name.endswith(".png") and media.read(name) == PNG
    assert media.save_picture(b"<svg onload=alert(1)>") is None
    assert (
        audio_ext(MP3) == "mp3"
        and audio_ext(b"OggS....") == "ogg"
        and audio_ext(b"\x1a\x45\xdf\xa3") == "webm"
    )
    assert audio_ext(b"hello") is None
    assert media.path("../../etc/passwd") is None and media.path("0" * 32 + ".exe") is None
    # Old files go with the old messages.
    old = media.save_audio(MP3)
    assert old is not None
    os.utime(tmp_path / "desk-media" / old, (time.time() - 90_000, time.time() - 90_000))
    assert media.purge(time.time() - 86_400) == 1 and media.path(old) is None and media.path(name)


async def test_a_picture_you_send_her_is_read_from_its_file(tmp_path: Path) -> None:
    media = DeskMedia(tmp_path)
    name = media.save_picture(PNG)
    assert name is not None
    row = {"segments": [{"type": "image", "data": {"url": picture_url(name), "file": name}}]}
    assert picture_keys(row) == [(name, picture_url(name))]
    reader = ScreenshotReader(lambda: None)
    reader.desk_media = media
    assert await reader._download(picture_url(name), "") == PNG


async def test_you_send_her_a_picture(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    sent = await world.http.post(
        f"/api/desk/{WHALE}/pictures", content=PNG, headers={"content-type": "application/octet-stream"}
    )
    assert sent.status_code == 202, sent.text
    found = await eventually(lambda: _with_picture(world, chat))
    image = found["segments"][0]
    assert (
        found["text"] == "[图片]"
        and image["type"] == "image"
        and image["data"]["url"].startswith("desk-media:")
    )
    served = await world.http.get(f"/api/desk/media/{image['data']['file']}")
    assert (
        served.status_code == 200 and served.content == PNG and served.headers["content-type"] == "image/png"
    )
    assert (await world.http.get(f"/api/desk/media/{'0' * 32}.png")).status_code == 404
    refused = await world.http.post(f"/api/desk/{WHALE}/pictures", content=b"<svg/>")
    assert refused.status_code == 415


async def _with_picture(w: World, chat: int) -> dict[str, Any] | None:
    return next((r for r in await rows(w, chat) if r["text"] == "[图片]"), None)


async def test_pictures_and_words_are_one_message_and_one_answer(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    http = world.http
    names = []
    for n in range(3):
        kept = await http.post(f"/api/desk/{WHALE}/pictures?keep=true", content=PNG + bytes([n]))
        assert kept.status_code == 202, kept.text
        names.append(kept.json()["name"])
    assert await rows(world, chat) == []  # kept, not sent
    model = world.state.providers.runtimes["ark"].provider
    sent = await http.post(
        f"/api/desk/{WHALE}/messages", json={"text": "哪张好看？", "pictures": [*names, names[0]]}
    )
    assert sent.status_code == 202, sent.text
    await eventually(lambda: _answer_to(world, chat))
    mine, hers = await rows(world, chat)
    assert mine["text"] == "[图片][图片][图片]\n哪张好看？" and hers["from_self"]
    assert [s["data"]["file"] for s in mine["segments"] if s["type"] == "image"] == names
    assert mine["segments"][-1] == {"type": "text", "data": {"text": "哪张好看？"}}
    assert len(model.requests) == 1

    alone = await http.post(f"/api/desk/{WHALE}/messages", json={"pictures": names[:1]})
    assert alone.status_code == 202

    async def refused(**body: Any) -> int:
        return (await http.post(f"/api/desk/{WHALE}/messages", json=body)).status_code

    voice = world.state.desk_media.save_audio(MP3)
    for bad in ("0" * 32 + ".png", voice, "../nest.db"):
        assert await refused(text="看", pictures=[bad]) == 404
    assert await refused(text="看", pictures=names * 4) == 422  # more than nine
    assert await refused() == 422
    assert await refused(sticker_id=1, pictures=names) == 422


async def _answer_to(w: World, chat: int) -> bool:
    return any(r["from_self"] for r in await rows(w, chat))


async def test_she_looks_at_all_the_pictures_of_a_desk_message(tmp_path: Path) -> None:
    media = DeskMedia(tmp_path)
    reader = ScreenshotReader(lambda: cast(Any, object()), notes_per_hour=lambda: 100)
    reader.desk_media = media
    looked: list[str] = []

    async def look(key: str, url: str, account: str) -> str:
        looked.append(key)
        reader._notes[key] = f"第{len(looked)}张"
        return reader._notes[key]

    reader._write_note = look  # type: ignore[method-assign]
    ours = [{"type": "image", "data": {"url": picture_url(n), "file": n}} for n in "abcdefghi"]
    row = {"id": 1, "text": "[图片]" * 9 + "\n看看", "segments": ours}
    _, [seen] = await reader.noted([], [row])
    assert len(looked) == 9 and seen["text"].startswith("[图片：第1张][图片：第2张]")
    assert seen["text"].endswith("[图片：第9张]\n看看")
    # From the platforms: still two a reply.
    looked.clear()
    theirs = [{"type": "image", "data": {"url": f"https://x/{n}", "file": f"q{n}"}} for n in range(5)]
    await reader.noted([], [{"id": 2, "text": "[图片]" * 5, "segments": theirs}])
    assert len(looked) == 2


def speaking(w: World, calls: list[httpx.Request]) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "  今天吃什么  "})
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    w.state.speech._transport = httpx.MockTransport(answer)


async def test_her_voice_comes_with_her_words_and_she_reads_aloud(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    calls: list[httpx.Request] = []
    speaking(world, calls)
    http = world.http
    assert (await http.patch("/api/speech", json={"fish_key": "fish-key-123456"})).status_code == 200
    assert (await http.patch(f"/api/bots/{WHALE}", json={"voice": "fish:abc"})).status_code == 200
    assert (
        await http.patch(f"/api/bots/{WHALE}/channels/{chat}", json={"voice_mode": "on"})
    ).status_code == 200
    model = world.state.providers.runtimes["ark"].provider
    model.queue("[语音]好呀，晚上吃火锅")
    await http.post(f"/api/desk/{WHALE}/messages", json={"text": "晚上吃什么"})
    hers = await eventually(lambda: _hers(world, chat))
    assert hers["text"] == "好呀，晚上吃火锅"
    [voice] = [s for s in hers["segments"] if s["type"] == "voice"]
    played = await http.get(f"/api/desk/media/{voice['data']['media']}")
    assert played.status_code == 200 and played.content == MP3

    # 读出来: any of her lines, once (read again from the file).
    asked = len(calls)
    read = await http.post(f"/api/desk/{WHALE}/read-aloud", json={"message_id": hers["id"]})
    assert read.status_code == 200, read.text
    again = await http.post(f"/api/desk/{WHALE}/read-aloud", json={"message_id": hers["id"]})
    assert again.json() == read.json() and len(calls) == asked + 1
    mine = next(r for r in await rows(world, chat) if not r["from_self"])
    refused = await http.post(f"/api/desk/{WHALE}/read-aloud", json={"message_id": mine["id"]})
    assert refused.status_code == 404


async def _hers(w: World, chat: int) -> dict[str, Any] | None:
    return next((r for r in await rows(w, chat) if r["from_self"]), None)


async def test_reading_aloud_needs_her_voice(world: World) -> None:  # noqa: F811
    chat = await private_chat(world)
    world.state.providers.runtimes["ark"].provider.queue("在呢")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "在吗"})
    hers = await eventually(lambda: _hers(world, chat))
    assert [s["type"] for s in hers["segments"]] == ["text"]  # no voice unless 「发语音」 is on
    refused = await world.http.post(f"/api/desk/{WHALE}/read-aloud", json={"message_id": hers["id"]})
    assert refused.status_code == 409 and "还没有声音" in refused.json()["detail"]


async def test_speech_input_goes_through_the_transcription_service(world: World) -> None:  # noqa: F811
    http = world.http
    calls: list[httpx.Request] = []
    speaking(world, calls)
    refused = await http.post("/api/speech/listen", content=b"OggS" + b"\x00" * 40)
    assert refused.status_code == 502 and "还没设置语音转写" in refused.json()["detail"]
    settings = {
        "listen_url": "https://asr.example/v1/",
        "listen_model": "whisper-1",
        "listen_key": "sk-listen-1",
    }
    view = (await http.patch("/api/speech", json=settings)).json()
    assert view["listen"] == {"key_set": True, "key_from_env": False, "ready": True}
    assert "sk-listen-1" not in str(view)
    heard = await http.post("/api/speech/listen", content=b"OggS" + b"\x00" * 40)
    assert heard.status_code == 200 and heard.json() == {"text": "今天吃什么"}
    sent = calls[-1]
    assert str(sent.url) == "https://asr.example/v1/audio/transcriptions"
    assert sent.headers["authorization"] == "Bearer sk-listen-1" and b"whisper-1" in sent.content
    assert (await http.post("/api/speech/listen", content=b"not audio")).status_code == 415
    assert (await http.patch("/api/speech", json={"listen_url": "asr.example"})).status_code == 422
