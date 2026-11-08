"""截图读字: a question asked with a screenshot (or quoting one) gets the picture's words, read by the vision
model, so sorting and the help-docs search see the error text. Stickers are never read, keys are hidden,
and a picture is read once."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
from PIL import Image

from nest.core.events import Segment
from nest.core.screenshots import ScreenshotReader, hide_secrets, pictures
from nest.llm.runtime import ProviderRuntime
from nest.storage.bots import ProviderConfig
from tests.fakes.llm import FakeProvider
from tests.test_brain import World, world  # noqa: F401 (world is a fixture)
from tests.test_project_answers import bind_docs
from tests.test_sending import answering, go_live


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(out, format="PNG")
    return out.getvalue()


def image(url: str, sub_type: str = "0") -> dict:
    return {"type": "image", "data": {"url": url, "sub_type": sub_type, "file": "x.png"}}


def test_only_screenshots_and_photos_are_read_and_keys_are_hidden() -> None:
    rows = [
        {
            "segments": [
                {"type": "text", "data": {"text": "看图"}},
                image("https://q/1"),
                image("https://q/s", "1"),
            ]
        },
        {
            "segments": [
                image("https://q/1"),
                image("https://q/2"),
                image("https://q/3"),
                image("https://q/4"),
            ]
        },
        {"segments": [image("file:///etc/passwd")]},
    ]
    assert pictures(rows) == [
        "https://q/1",
        "https://q/2",
        "https://q/3",
    ]  # no sticker, no repeats, at most 3
    assert hide_secrets("Key: sk-abc123def456ghi 报错 401") == "Key: *** 报错 401"
    assert hide_secrets("token " + "a" * 40) == "token ***"
    assert hide_secrets("连不上 DeepSeek（请求超时）") == "连不上 DeepSeek（请求超时）"


async def test_a_screenshot_question_is_read_sorted_and_looked_up_with_its_words(
    world: World,  # noqa: F811
    tmp_path: Path,
) -> None:
    adapter = await go_live(world)
    await answering(world)
    await bind_docs(world, tmp_path)
    downloads: list[str] = []

    def cdn(request: httpx.Request) -> httpx.Response:
        downloads.append(str(request.url))
        return httpx.Response(200, content=png())

    vision = world.brain.providers.get("model-10001")
    world.brain.screenshots = ScreenshotReader(lambda: vision, transport=httpx.MockTransport(cdn))
    world.providers["10001"].queue(
        "朗读失败：sk-secret0123456789",  # what the vision model read
        '{"kind": "project", "topic": "朗读报错", "keywords": ["朗读"], "error": "朗读失败"}',
        '{"ids": ["tts"]}',  # the sections it picks
        "去 设置 → 声音 → 朗读 看看朗读来源。",
    )
    segments = (
        Segment("text", {"text": "阿鲸 这个咋办"}),
        Segment("image", image("https://cdn/a.png")["data"]),
    )
    await world.say("阿鲸 这个咋办[图片]", accounts=["10001"], segments=segments)
    [decision] = await world.settle(1)
    assert decision["reason"].startswith("已发送（读了 1 张截图；问 DemoApp，模型从候选里挑了 1 节资料")
    assert downloads == ["https://cdn/a.png"]
    read, sort, pick, answer = world.providers["10001"].requests
    assert "sk-secret" not in pick[1].content
    assert read[0].images and read[0].images[0].startswith("data:image/png;base64,")
    assert "[截图里的字：朗读失败：***]" in sort[1].content  # the key never reaches the other prompts
    assert "sk-secret" not in sort[1].content + answer[1].content
    assert "## 朗读" in answer[1].content
    assert adapter.actions

    # The same picture again: read once.
    world.providers["10001"].queue('{"kind": "chat"}', "嗯")
    await world.say("阿鲸 还是这个", accounts=["10001"], segments=segments)
    await world.settle(2)
    assert sum(1 for r in world.providers["10001"].requests if r[0].images) == 1


async def test_a_sticker_or_no_vision_model_reads_nothing(world: World) -> None:  # noqa: F811
    await answering(world)
    world.brain.screenshots = ScreenshotReader(lambda: None)
    world.providers["10001"].queue('{"kind": "chat"}', "哈哈")
    segments = (
        Segment("text", {"text": "阿鲸 你看这个？"}),
        Segment("image", image("https://cdn/b.png")["data"]),
    )
    await world.say("阿鲸 你看这个？[图片]", accounts=["10001"], segments=segments)
    [decision] = await world.settle(1)
    assert "截图" not in decision["reason"] and not any(
        r[0].images for r in world.providers["10001"].requests
    )


def picture_row(url: str, file: str = "a.jpg", sub_type: str = "0") -> dict:
    data = {"url": url, "file": file, "sub_type": sub_type}
    return {"text": "看[图片]", "segments": [{"type": "image", "data": data}]}


def note_reader(*answers: str, per_hour: int = 5) -> tuple[ScreenshotReader, FakeProvider, list[str]]:
    fake = FakeProvider(*answers, default="一只猫")
    config = ProviderConfig(id="eye", kind="openai_chat", base_url="http://x", model="m")

    async def record(**_: object) -> int:
        return 1

    vision = ProviderRuntime(config, fake, record, retry_delay=0)
    fetched: list[str] = []

    def cdn(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, content=png())

    reader = ScreenshotReader(
        lambda: vision, transport=httpx.MockTransport(cdn), notes_per_hour=lambda: per_hour
    )
    return reader, fake, fetched


async def test_a_picture_in_the_chat_gets_a_line_once() -> None:
    reader, fake, _ = note_reader("一只橘猫趴在键盘上 sk-abcdefghijkl")
    _, fresh = await reader.noted([], [picture_row("http://cdn/1")])
    assert fresh[0]["text"] == "看[图片：一只橘猫趴在键盘上 ***]"
    # Later it is context, and another account's url for the same file finds the same line.
    context, fresh = await reader.noted([picture_row("http://cdn/other")], [])
    assert context[0]["text"] == "看[图片：一只橘猫趴在键盘上 ***]" and len(fake.requests) == 1


async def test_stickers_and_old_pictures_are_not_looked_at() -> None:
    reader, fake, _ = note_reader()
    sticker = picture_row("http://cdn/1", sub_type="1")
    context, fresh = await reader.noted([picture_row("http://cdn/2", "old.jpg")], [sticker])
    assert context[0]["text"] == fresh[0]["text"] == "看[图片]" and not fake.requests


async def test_the_hourly_budget_leaves_pictures_as_they_are() -> None:
    reader, fake, _ = note_reader(per_hour=1)
    await reader.noted([], [picture_row("http://cdn/1", "1.jpg")])
    _, fresh = await reader.noted([], [picture_row("http://cdn/2", "2.jpg")])
    assert fresh[0]["text"] == "看[图片]" and len(fake.requests) == 1


async def test_two_bots_seeing_one_picture_cost_one_look() -> None:
    reader, fake, _ = note_reader()
    rows = [picture_row("http://cdn/1")]
    one, two = await asyncio.gather(reader.noted([], rows), reader.noted([], rows))
    assert one[1][0]["text"] == two[1][0]["text"] == "看[图片：一只猫]" and len(fake.requests) == 1
