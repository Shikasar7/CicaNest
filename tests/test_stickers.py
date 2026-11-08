"""表情包: collecting (dedupe, limits, what is never collected), describing, offering and sending them, the
daily cleanup, and the console API. No network, no real QQ, no real model."""

from __future__ import annotations

import io
import json
import os
import random
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw

from nest.core.events import ActionResult, ChannelRef, MessageEvent, Segment
from nest.core.prompt import split_sticker
from nest.core.stickers import (
    Sighting,
    StickerError,
    StickerKeeper,
    frame_sheet,
    look_of,
    parse_description,
    relabel,
    same_look,
    sticker_ref,
)
from nest.llm.base import ProviderError
from nest.llm.runtime import ProviderRuntime
from nest.storage.bots import ProviderConfig
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE
from tests.test_bot_settings_api import World as ApiWorld
from tests.test_bot_settings_api import world as api_world  # noqa: F401 (fixture)
from tests.test_brain import BOTS, GROUP, World, world  # noqa: F401 (fixture)
from tests.test_sending import FakeAdapter

GROUP_KEY = f"qq:group:{GROUP}"
CHANNEL = ChannelRef("qq", "10001", "group", GROUP)
DESCRIBED = json.dumps(
    {
        "description": "熊猫头捂脸",
        "text": "我真的会谢",
        "tags": ["无语", "尴尬"],
        "suitable": True,
        "reason": "",
    },
    ensure_ascii=False,
)


def picture(color: str = "red", fmt: str = "PNG", size: int = 64, frames: int = 1) -> bytes:
    out = io.BytesIO()
    images = [Image.new("RGB", (size, size), color if i == 0 else (i * 40, 0, 0)) for i in range(frames)]
    if frames > 1:
        images[0].save(out, format=fmt, save_all=True, append_images=images[1:])
    else:
        images[0].save(out, format=fmt)
    return out.getvalue()


def md5_name(n: int) -> str:
    return f"{n:032X}.png"


def sticker_segment(n: int, *, sub_type: Any = 1) -> Segment:
    return Segment(
        "image",
        {
            "file": md5_name(n),
            "url": f"https://qq.example/{n}",
            "sub_type": sub_type,
            "summary": "[动画表情]",
        },
    )


@dataclass
class Net:
    files: dict[str, bytes] = field(default_factory=dict)
    hits: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(str(request.url))
        body = self.files.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)


@dataclass
class Kit:
    w: World
    keeper: StickerKeeper
    vision: FakeProvider
    net: Net

    async def collect_on(self) -> None:
        await self.keeper.groups.set(GROUP_KEY, collect_stickers=True)
        await self.keeper.reload()

    async def send(
        self, *segments: Segment, sender: str = "7", text: str = "[动画表情]", to: list[str] | None = None
    ) -> str:
        """A message as every bot account receives it, through storage, the keeper and the brain."""
        w = self.w
        w.counter += 1
        mid = f"m{w.counter}"
        for account in BOTS:
            event = MessageEvent(
                channel=ChannelRef("qq", account, "group", GROUP),
                message_id=mid,
                sender_id=sender,
                sender_name="小明",
                segments=segments,
                text=text,
                timestamp=time.time(),
                from_self=sender == account,
                mentions=frozenset(to or ()),
            )
            row = await w.store.save(event)
            assert row is not None
            self.keeper.on_message(event)
            await w.brain.on_message(event, row)
        return mid

    async def stickers(self) -> list[dict[str, Any]]:
        items, _ = await self.keeper.store.page(status=None, query="", offset=0, limit=100)
        return items

    async def add(
        self, n: int, *, status: str = "ready", color: str = "red", **fields: Any
    ) -> dict[str, Any]:
        """A collected sticker, straight through collect()."""
        self.net.files[f"https://qq.example/{n}"] = picture(color, size=40 + n)
        row = await self.keeper.collect(
            Sighting(f"md5:{md5_name(n)[:32]}", f"https://qq.example/{n}", "image", GROUP_KEY, time.time())
        )
        assert row is not None
        await self.keeper.store.describe(
            row["id"],
            status=status,
            description=fields.get("description", f"表情{n}"),
            caption=fields.get("caption", ""),
            tags=fields.get("tags", []),
        )
        return await self.keeper.store.get(row["id"])  # type: ignore[return-value]


@pytest.fixture
async def kit(world: World, tmp_path: Path) -> AsyncIterator[Kit]:  # noqa: F811
    vision = FakeProvider(name="vision", default=DESCRIBED)
    config = ProviderConfig(id="vision", kind="openai_chat", base_url="http://x", model="v")
    await world.repo.upsert_provider(config)
    world.brain.providers.runtimes["vision"] = ProviderRuntime(
        config, vision, world.repo.record_llm_call, retry_delay=0
    )
    net = Net()
    keeper = StickerKeeper(
        world.db,
        world.brain.providers,
        world.broadcaster,
        tmp_path / "stickers",
        our_accounts=world.brain.our_accounts,
        transport=httpx.MockTransport(net.handler),
        describe_idle=0.05,
    )
    await keeper.start()
    world.brain.stickers = keeper
    try:
        yield Kit(world, keeper, vision, net)
    finally:
        await keeper.stop()


async def use_vision(kit: Kit) -> None:
    await kit.keeper.save_config({"vision_provider": "vision"})


async def sticker_mode(w: World, mode: str, bots: list[str] | None = None) -> None:
    for uin in bots or list(BOTS):
        bot = w.brain.bots[f"qq:{uin}"]
        for channel_id in bot.channels:
            await w.repo.set_channel(bot.id, channel_id, sticker_mode=mode)
    await w.brain.load()


# -- what counts as a sticker -------------------------------------------------------------------------


def test_only_collected_stickers_and_mall_stickers_count() -> None:
    assert sticker_ref(sticker_segment(1)) == (f"md5:{md5_name(1)[:32]}", "https://qq.example/1", "image")
    assert sticker_ref({"type": "image", "data": dict(sticker_segment(2, sub_type="1").data)}) is not None
    assert sticker_ref(sticker_segment(3, sub_type=0)) is None  # a photo or screenshot
    no_url = Segment("image", {"file": md5_name(4), "sub_type": 1})
    assert sticker_ref(no_url) is None
    mall = Segment("mface", {"emoji_id": "abc", "url": "https://gxh.example/abc.gif", "summary": "[吃瓜]"})
    assert sticker_ref(mall) == ("mface:abc", "https://gxh.example/abc.gif", "mface")
    assert sticker_ref(Segment("face", {"id": "14"})) is None


def test_description_json_is_checked_and_trimmed() -> None:
    parsed = parse_description("```json\n" + DESCRIBED + "\n```")
    assert parsed == {
        "description": "熊猫头捂脸",
        "caption": "我真的会谢",
        "tags": ["无语", "尴尬"],
        "suitable": True,
        "reason": "",
    }
    unsuitable = parse_description(
        '好的：{"description": "x", "tags": ["a","a","b","c","d"], "suitable": "false"}'
    )
    assert unsuitable["suitable"] is False and unsuitable["tags"] == ["a", "b", "c"]
    for bad in ("我看不清", '{"description": "", "text": ""}', "[1, 2]"):
        with pytest.raises(ValueError):
            parse_description(bad)


def test_sticker_marks_are_split_from_the_reply() -> None:
    three = ["猫咪狂笑，配字「哈」（嘲笑）", "狗头", "熊猫"]
    assert split_sticker("哈哈 [表情2]", three) == ("哈哈", 2)
    assert split_sticker("[表情 1]", three) == ("", 1)
    assert split_sticker("好[表情9]", three) == ("好", None)  # not one of the choices: ignored
    assert split_sticker("普通的一句话", []) == ("普通的一句话", None)


def test_a_sticker_written_out_as_in_the_chat_log_never_goes_out_as_words() -> None:
    three = ["猫咪狂笑，配字「哈」（嘲笑）", "狗头", "熊猫"]
    assert split_sticker("哼\n[表情：猫咪狂笑，配字「哈」]", three) == ("哼", 1)  # the same sticker
    assert split_sticker("哼 [表情：没有这张]", three) == ("哼", None)
    assert split_sticker("（发了表情：狗头）", three) == ("", 2)
    assert split_sticker("我（表情包作者）没说话", three) == ("我（表情包作者）没说话", None)


# -- collecting ---------------------------------------------------------------------------------------


def scene(seed: int, caption: bool = False) -> Image.Image:
    """A picture with something in it: blobs that depend on `seed`, and a caption bar if asked for."""
    rng = random.Random(seed)
    image = Image.new("RGB", (240, 240), "white")
    draw = ImageDraw.Draw(image)
    for _ in range(14):
        x, y, r = rng.randrange(240), rng.randrange(240), rng.randrange(20, 70)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randrange(256) for _ in range(3)))
    if caption:
        draw.rectangle((30, 180, 210, 225), fill="black")
    return image


def saved(image: Image.Image, fmt: str = "PNG", **options: Any) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


def test_a_picture_saved_again_looks_the_same_and_others_do_not() -> None:
    original = look_of(saved(scene(1)))
    assert same_look(original, look_of(saved(scene(1), "JPEG", quality=85)))
    assert same_look(original, look_of(saved(scene(1).resize((120, 120)), "WEBP", quality=90)))
    assert not same_look(original, look_of(saved(scene(2))))
    assert not same_look(original, look_of(saved(scene(1, caption=True))))  # another caption, another sticker


def test_plain_and_broken_pictures_have_no_look() -> None:
    assert look_of(picture("red")) == "" and look_of(b"not a picture") == ""
    assert not same_look("", "")


def test_animated_and_still_pictures_are_never_the_same() -> None:
    frames = [scene(1), scene(2), scene(3)]
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:])
    animated = look_of(out.getvalue())
    assert animated.count(":") == 2 and not same_look(animated, look_of(saved(scene(1))))
    assert same_look(animated, animated)


def gif(*frames: Image.Image) -> bytes:
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=list(frames[1:]), duration=100)
    return out.getvalue()


@pytest.mark.parametrize(
    ("frames", "size"), [(2, (520, 256)), (3, (784, 256)), (4, (520, 520)), (9, (784, 520))]
)
def test_the_vision_model_sees_an_animated_picture_as_a_sheet_of_frames(
    frames: int, size: tuple[int, int]
) -> None:
    png, moments = frame_sheet(gif(*(scene(n) for n in range(frames))))
    assert moments == min(frames, 6)
    with Image.open(io.BytesIO(png)) as sheet:
        assert sheet.size == size


def test_frames_that_do_not_change_count_once_and_a_still_picture_stays_one() -> None:
    assert frame_sheet(gif(scene(1), scene(1), scene(2), scene(2)))[1] == 2
    assert frame_sheet(gif(scene(1), scene(1)))[1] == 1
    assert frame_sheet(saved(scene(1)))[1] == 1


async def test_an_animated_sticker_is_described_as_a_whole(kit: Kit) -> None:
    await kit.keeper.save_config({"vision_provider": "vision"})
    kit.vision.queue('{"description": "挥手后转身走开", "text": "再见", "tags": [], "suitable": true}')
    await kit.keeper.add_own(gif(scene(1), scene(2), scene(3)), "upload")
    row = await eventually(lambda: ready_sticker(kit))
    assert row["description"] == "挥手后转身走开"
    assert "3 个瞬间" in kit.vision.requests[0][0].content


async def ready_sticker(kit: Kit) -> dict[str, Any] | None:
    rows = await kit.stickers()
    return rows[0] if rows and rows[0]["status"] == "ready" else None


async def test_a_copy_of_a_sticker_we_have_is_not_added_twice(kit: Kit) -> None:
    first, added = await kit.keeper.add_own(saved(scene(1)), "upload")
    assert added
    again, added = await kit.keeper.add_own(saved(scene(1), "JPEG", quality=85), "upload")
    assert not added and again["id"] == first["id"]
    _, added = await kit.keeper.add_own(saved(scene(2)), "upload")
    assert added

    kit.net.files["https://qq.example/copy"] = saved(scene(2).resize((160, 160)), "JPEG", quality=85)
    row = await kit.keeper.collect(Sighting("md5:COPY", "https://qq.example/copy", "image", GROUP_KEY, 5.0))
    assert row is None and len(await kit.stickers()) == 2


async def test_stickers_from_before_get_their_look(kit: Kit) -> None:
    row, _ = await kit.keeper.add_own(saved(scene(1)), "upload")
    await kit.keeper.store.set_look(row["id"], "")
    await kit.keeper._fill_looks()
    assert (await kit.keeper.store.looks())[row["id"]] == look_of(saved(scene(1)))
    _, added = await kit.keeper.add_own(saved(scene(1), "JPEG", quality=85), "upload")
    assert not added


async def test_a_sticker_three_accounts_receive_is_downloaded_once_and_described(kit: Kit) -> None:
    await kit.collect_on()
    await use_vision(kit)
    kit.net.files["https://qq.example/1"] = picture("red", "GIF", frames=3)
    await kit.send(sticker_segment(1))
    rows = await eventually(lambda: _ready(kit))
    assert kit.net.hits == ["https://qq.example/1"]
    sticker = rows[0]
    assert sticker["content_type"] == "image/gif" and sticker["animated"] is True
    assert sticker["description"] == "熊猫头捂脸" and sticker["caption"] == "我真的会谢"
    assert sticker["tags"] == ["无语", "尴尬"] and sticker["seen_count"] == 1
    assert (
        kit.keeper.path(sticker["sha256"], "image/gif").read_bytes() == kit.net.files["https://qq.example/1"]
    )
    # The vision model saw one PNG frame, not the GIF.
    image = kit.vision.requests[0][0].images[0]
    assert image.startswith("data:image/png;base64,")
    # Seen again later: counted, not downloaded again.
    await kit.send(sticker_segment(1))
    await eventually(lambda: _seen(kit, 2))
    assert len(kit.net.hits) == 1 and len(kit.vision.requests) == 1


async def _ready(kit: Kit) -> list[dict[str, Any]] | None:
    rows = await kit.stickers()
    return rows if rows and rows[0]["status"] == "ready" else None


async def _seen(kit: Kit, n: int) -> bool:
    rows = await kit.stickers()
    return bool(rows) and rows[0]["seen_count"] == n


async def test_photos_other_groups_and_our_own_bots_are_never_collected(kit: Kit) -> None:
    kit.net.files["https://qq.example/1"] = picture()
    kit.net.files["https://qq.example/3"] = picture("blue")
    await kit.send(sticker_segment(1))  # the group does not collect yet
    await kit.collect_on()
    await kit.send(sticker_segment(3, sub_type=0))  # a photo
    await kit.send(sticker_segment(1), sender="10002")  # one of our bots
    await kit.send(Segment("text", {"text": "hi"}), text="hi")
    kit.net.files["https://qq.example/5"] = picture("green")
    await kit.send(sticker_segment(5))  # the queue keeps going: this one is collected
    await eventually(lambda: _count(kit, 1))
    assert kit.net.hits == ["https://qq.example/5"]


async def _count(kit: Kit, n: int) -> bool:
    return len(await kit.stickers()) == n


async def test_too_big_broken_or_missing_pictures_are_skipped(kit: Kit) -> None:
    await kit.keeper.save_config({})
    kit.net.files["https://qq.example/1"] = b"<html>not a picture</html>"
    kit.net.files["https://qq.example/2"] = picture(size=900)  # fine, but bigger than the file limit below
    kit.keeper.config = kit.keeper.config.__class__(file_max_bytes=2000)
    from nest.core.stickers import StickerError

    for n in (1, 2, 404):
        with pytest.raises(StickerError):
            await kit.keeper.collect(
                Sighting(f"md5:{n:032X}", f"https://qq.example/{n}", "image", GROUP_KEY, time.time())
            )
    assert await kit.stickers() == []
    assert not list((kit.keeper.folder).glob("*/*"))


async def test_the_same_picture_under_another_name_is_one_sticker(kit: Kit) -> None:
    body = picture("purple")
    kit.net.files["https://qq.example/a"] = kit.net.files["https://qq.example/b"] = body
    first = await kit.keeper.collect(Sighting("md5:A", "https://qq.example/a", "image", GROUP_KEY, 1.0))
    second = await kit.keeper.collect(Sighting("mface:b", "https://qq.example/b", "mface", GROUP_KEY, 2.0))
    assert first is not None and second is None
    rows = await kit.stickers()
    assert len(rows) == 1 and rows[0]["seen_count"] == 2


async def test_over_the_limit_the_oldest_unkept_sticker_makes_room(kit: Kit) -> None:
    await kit.keeper.save_config({"max_count": 10})
    kit.keeper.config = kit.keeper.config.__class__(max_count=2)
    first = await kit.add(1)
    await kit.keeper.store.update(first["id"], kept=True)
    second = await kit.add(2)
    third = await kit.add(3)
    ids = {s["id"] for s in await kit.stickers()}
    assert ids == {first["id"], third["id"]}  # the kept one stays, the unkept older one went
    assert not kit.keeper.path(second["sha256"], second["content_type"]).exists()
    await kit.keeper.store.update(third["id"], kept=True)
    kit.net.files["https://qq.example/9"] = picture("white")
    full = await kit.keeper.collect(
        Sighting("md5:9", "https://qq.example/9", "image", GROUP_KEY, time.time())
    )
    assert full is None and len(await kit.stickers()) == 2  # only kept ones left: not collected


# -- describing ---------------------------------------------------------------------------------------


async def test_descriptions_wait_for_a_vision_model_and_respect_the_hourly_budget(kit: Kit) -> None:
    for n in (1, 2, 3):
        kit.net.files[f"https://qq.example/{n}"] = picture(size=30 + n)
        await kit.keeper.collect(
            Sighting(f"md5:{n}", f"https://qq.example/{n}", "image", GROUP_KEY, time.time())
        )
    assert await kit.keeper.describe_pending() == 0  # no vision model chosen: they wait
    await kit.keeper.save_config({"vision_provider": "vision", "describe_per_hour": 2})
    await eventually(lambda: _statuses(kit, {"ready": 2, "pending": 1}))
    assert len(kit.vision.requests) == 2


async def _statuses(kit: Kit, expected: dict[str, int]) -> bool:
    return await kit.keeper.store.counts() == expected


async def test_unsuitable_unreadable_and_failed_descriptions(kit: Kit) -> None:
    kit.vision.queue(
        json.dumps(
            {"description": "一张真人自拍", "suitable": False, "reason": "真人的私人照片"}, ensure_ascii=False
        ),
        "我说不清楚",
        ProviderError("400 不支持图片", retryable=False),
    )
    for n in (1, 2, 3):
        kit.net.files[f"https://qq.example/{n}"] = picture(size=30 + n)
        await kit.keeper.collect(Sighting(f"md5:{n}", f"https://qq.example/{n}", "image", GROUP_KEY, n))
    await use_vision(kit)
    rows = await eventually(lambda: _all_done(kit))
    by_seen = {round(r["first_seen_at"]): r for r in rows}
    assert by_seen[1]["status"] == "unsuitable" and by_seen[1]["note"] == "真人的私人照片"
    assert by_seen[2]["status"] == "failed" and "看不懂" in by_seen[2]["note"]
    assert by_seen[3]["status"] == "failed" and "不支持图片" in by_seen[3]["note"]


async def _all_done(kit: Kit) -> list[dict[str, Any]] | None:
    rows = await kit.stickers()
    return rows if rows and all(r["status"] != "pending" for r in rows) else None


async def test_vision_test_needs_the_red_circle(kit: Kit) -> None:
    runtime = kit.w.brain.providers.get("vision")
    kit.vision.queue("一个红色的圆形", "一只猫")
    assert await kit.keeper.test_vision(runtime) == (True, "一个红色的圆形")
    assert (await kit.keeper.test_vision(runtime))[0] is False
    assert kit.vision.requests[0][0].images[0].startswith("data:image/png;base64,")


# -- offering and sending ---------------------------------------------------------------------------


async def test_with_stickers_off_the_prompt_is_unchanged(kit: Kit) -> None:
    await kit.add(1, description="熊猫头捂脸")
    await kit.w.say("阿鲸 你好", to=["10001"], accounts=["10001"])
    await kit.w.settle(1)
    user = kit.w.providers["10001"].requests[0][1].content
    assert "表情" not in user


async def test_trial_offers_stickers_and_records_the_pick_without_sending(kit: Kit) -> None:
    w = kit.w
    a = await kit.add(1, description="熊猫头捂脸", caption="我真的会谢", tags=["无语"])
    await kit.add(2, description="小猫点头", tags=["好的"])
    await kit.add(3, status="unsuitable")
    await sticker_mode(w, "trial", ["10001"])
    w.providers["10001"].queue("真的会谢 [表情1]")
    await w.say("阿鲸 你真无语", to=["10001"], accounts=["10001"])
    decision = (await w.settle(1))[0]
    user = w.providers["10001"].requests[0][1].content
    assert "你可以发的表情" in user and "[表情1] 熊猫头捂脸，配字「我真的会谢」（无语）" in user
    assert "[表情2] 小猫点头（好的）" in user and "[表情3]" not in user  # unsuitable ones never offered
    assert decision["outcome"] == "would_reply" and decision["reply"] == "真的会谢"
    assert decision["sticker_id"] == a["id"] and decision["sticker_state"] == "trial"
    assert "还会发一张表情" in decision["reason"]


async def test_live_sends_the_words_then_the_sticker_as_base64(kit: Kit) -> None:
    w = kit.w
    sticker = await kit.add(1, description="熊猫头捂脸")
    adapter = FakeAdapter()
    w.brain.adapters = {"qq:10001": adapter}
    await w.repo.set_mode("qq:10001", "live")
    await sticker_mode(w, "on", ["10001"])
    w.providers["10001"].queue("好嘞[表情1]")
    await w.say("阿鲸 在吗", to=["10001"], accounts=["10001"])
    decision = (await w.settle(1))[0]
    assert decision["outcome"] == "replied" and decision["sticker_state"] == "sent"
    assert decision["reason"] == "已发送，还发了一张表情"
    words, image = adapter.actions
    assert words.segments == (Segment("text", {"text": "好嘞"}),)
    (segment,) = image.segments
    assert segment.type == "image" and segment.data["sub_type"] == 1
    import base64

    assert base64.b64decode(segment.data["file"].removeprefix("base64://")) == await kit.keeper.read(sticker)
    assert (await kit.keeper.store.get(sticker["id"]))["used_count"] == 1
    # Right after, the group's sticker gap holds: the next reply is offered no stickers.
    await w.say("阿鲸 再来", to=["10001"], accounts=["10001"])
    await w.settle(2, timeout=5)
    assert "你可以发的表情" not in w.providers["10001"].requests[1][1].content


async def test_a_sticker_alone_is_a_reply_and_a_failed_one_is_recorded(kit: Kit) -> None:
    w = kit.w
    await kit.add(1)
    adapter = FakeAdapter(ActionResult("failed", error="群里被禁言了"))
    w.brain.adapters = {"qq:10001": adapter}
    await w.repo.set_mode("qq:10001", "live")
    await sticker_mode(w, "on", ["10001"])
    w.providers["10001"].queue("[表情1]")
    await w.say("阿鲸 斗图", to=["10001"], accounts=["10001"])
    decision = (await w.settle(1))[0]
    assert decision["outcome"] == "failed" and decision["sticker_state"] == "failed"
    assert decision["reply"] == "" and "禁言" in decision["reason"]
    assert len(adapter.actions) == 1 and adapter.actions[0].segments[0].type == "image"


async def test_hourly_sticker_limit_and_no_repeats(kit: Kit) -> None:
    w = kit.w
    first = await kit.add(1)
    await kit.add(2)
    await sticker_mode(w, "trial", ["10001"])
    kit.keeper.config = kit.keeper.config.__class__(hourly_per_bot=1, group_gap=10)
    channel = w.brain.bots["qq:10001"].channels
    ref = next(iter(channel.values())).channel
    await w.repo.record_decision(
        bot_id="qq:10002",
        channel_id=next(iter(w.brain.bots["qq:10002"].channels)),
        trigger_id=None,
        trigger="mention",
        outcome="replied",
        sticker_id=first["id"],
        sticker_state="sent",
    )
    # Another bot's sticker 10 s ago: within the gap, nothing is offered.
    assert (await kit.keeper.candidates(ref, "qq:10001", "", set()))[0] == []
    async with w.db.transaction() as conn:
        await conn.execute("UPDATE decisions SET created_at = created_at - 30")
    picked, _ = await kit.keeper.candidates(ref, "qq:10001", "", set())
    assert [s["id"] for s in picked] != [] and first["id"] not in {s["id"] for s in picked}  # no repeats
    await w.repo.record_decision(
        bot_id="qq:10001",
        channel_id=next(iter(channel)),
        trigger_id=None,
        trigger="mention",
        outcome="would_reply",
        sticker_id=picked[0]["id"],
        sticker_state="trial",
    )
    async with w.db.transaction() as conn:
        await conn.execute("UPDATE decisions SET created_at = created_at - 30")
    picked, why = await kit.keeper.candidates(ref, "qq:10001", "", set())
    assert picked == [] and "到上限" in why


async def test_relevant_stickers_come_first_and_the_one_just_sent_is_left_out(kit: Kit) -> None:
    w = kit.w
    await kit.add(1, description="小猫睡觉", tags=["晚安"])
    hug = await kit.add(2, description="抱抱", caption="谢谢你", tags=["谢谢"])
    await kit.add(3, description="狗头", tags=["开心"])
    ref = next(iter(w.brain.bots["qq:10001"].channels.values())).channel
    picked, _ = await kit.keeper.candidates(ref, "qq:10001", "太谢谢你了", set())
    assert picked[0]["id"] == hug["id"] and len(picked) == 3
    picked, _ = await kit.keeper.candidates(ref, "qq:10001", "太谢谢你了", {hug["source_key"]})
    assert hug["id"] not in {s["id"] for s in picked}


async def test_described_stickers_read_as_words_in_the_prompt(kit: Kit) -> None:
    w = kit.w
    await kit.add(1, description="熊猫头捂脸", caption="我真的会谢")
    await kit.add(2, description="小猫点头")
    await sticker_mode(w, "trial", ["10001"])
    await kit.send(sticker_segment(1), sticker_segment(7), to=["10001"])
    await w.settle(1)
    user = w.providers["10001"].requests[0][1].content
    assert "小明：[表情：熊猫头捂脸，配字「我真的会谢」][动画表情]" in user  # 7 is not described
    assert "群里有人在发表情" in user


def test_relabel_keeps_rows_without_known_stickers() -> None:
    row = {"text": "[动画表情]", "segments": [{"type": "image", "data": dict(sticker_segment(1).data)}]}
    assert relabel([row], {}) == [row]
    assert relabel([row], {"md5:" + md5_name(1)[:32]: "狗头"})[0]["text"] == "[表情：狗头]"
    own = relabel([row | {"from_self": True}], {"md5:" + md5_name(1)[:32]: "狗头"})
    assert own[0]["text"] == "（发了表情：狗头）"  # not a way to write that it could copy


# -- cleanup ------------------------------------------------------------------------------------------


async def test_tidy_removes_expired_unsuitable_ones_extras_and_orphan_files(kit: Kit) -> None:
    keep = await kit.add(1)
    bad = await kit.add(2, status="unsuitable")
    async with kit.w.db.transaction() as conn:
        await conn.execute("UPDATE stickers SET status_at = 0 WHERE id = ?", (bad["id"],))
    orphan = kit.keeper.folder / "ff" / ("f" * 64 + ".png")
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"x" * 100)
    os.utime(orphan, (0, 0))
    fresh = kit.keeper.folder / "ee" / ("e" * 64 + ".png")  # maybe being added right now: left alone
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"y")
    removed, freed = await kit.keeper.tidy()
    assert removed == 1 and freed >= bad["bytes"] + 100
    assert [s["id"] for s in await kit.stickers()] == [keep["id"]]
    assert not orphan.exists() and fresh.exists()
    assert kit.keeper.path(keep["sha256"], keep["content_type"]).exists()


# -- console API --------------------------------------------------------------------------------------


async def _api_sticker(api: ApiWorld) -> dict[str, Any]:
    keeper = api.state.stickers
    from nest.core.stickers import _write
    from nest.storage.stickers import NewSticker

    body = picture("orange")
    sha = "a" * 64
    _write(keeper.path(sha, "image/png"), body)
    sticker_id = await keeper.store.add(
        NewSticker(sha, "md5:X", "image", "image/png", len(body), 64, 64, False, "qq:group:42", time.time())
    )
    return await keeper.store.get(sticker_id)


async def test_api_lists_edits_keeps_and_deletes_stickers(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    sticker = await _api_sticker(api_world)
    listing = (await http.get("/api/stickers")).json()
    assert listing["total"] == 1 and listing["count"] == 1 and listing["counts"] == {"pending": 1}
    assert {g["key"]: g["collect"] for g in listing["groups"]} == {"qq:group:42": False, "qq:group:77": False}
    image = await http.get(f"/api/stickers/{sticker['id']}/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"

    response = await http.patch(
        f"/api/stickers/{sticker['id']}",
        json={"description": " 橘色 方块 ", "tags": ["好", "好", "开心"], "kept": True},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["description"] == "橘色 方块" and edited["tags"] == ["好", "开心"]
    assert edited["edited"] is True and edited["kept"] is True
    assert (
        await http.patch(f"/api/stickers/{sticker['id']}", json={"tags": ["一", "二", "三", "四"]})
    ).status_code == 422
    assert (await http.patch(f"/api/stickers/{sticker['id']}", json={"status": "unsuitable"})).json()[
        "status"
    ] == "unsuitable"
    assert (await http.get("/api/stickers", params={"q": "橘色"})).json()["total"] == 1
    assert (await http.get("/api/stickers", params={"status": "ready"})).json()["total"] == 0
    assert (await http.post(f"/api/stickers/{sticker['id']}/describe")).json()["status"] == "pending"

    assert (await http.post("/api/stickers/delete", json={"ids": [sticker["id"]]})).json() == {"removed": 1}
    assert not api_world.state.stickers.path(sticker["sha256"], "image/png").exists()
    assert (await http.delete(f"/api/stickers/{sticker['id']}")).status_code == 404


async def test_api_settings_switches_and_vision_test(api_world: ApiWorld) -> None:  # noqa: F811
    http = api_world.http
    assert (await http.patch("/api/stickers/settings", json={"vision_provider": "nope"})).status_code == 400
    response = await http.patch("/api/stickers/settings", json={"vision_provider": "ark", "max_count": 50})
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["vision_provider"] == "ark" and response.json()["vision_loaded"]
    assert api_world.state.stickers.config.max_count == 50
    assert (await http.patch("/api/stickers/settings", json={"max_count": 1})).status_code == 422

    assert (
        await http.patch("/api/groups/qq:group:999/features", json={"collect_stickers": True})
    ).status_code == 404
    response = await http.patch("/api/groups/qq:group:42/features", json={"collect_stickers": True})
    assert response.json() == {
        "key": "qq:group:42",
        "collect_stickers": True,
        "learn_slang": False,
        "welcome": False,
    }
    assert api_world.state.stickers.collecting("qq:group:42")

    api_world.state.providers.get("ark").provider.queue("红色的圆")
    assert (await http.post("/api/stickers/vision-test", json={"provider_id": "ark"})).json()["ok"] is True
    assert (await http.post("/api/stickers/vision-test", json={"provider_id": "ark"})).json()["ok"] is False
    assert (await http.post("/api/stickers/vision-test", json={"provider_id": "broken"})).status_code == 400


async def test_api_bot_group_settings_and_storage_line(api_world: ApiWorld) -> None:  # noqa: F811
    response = await api_world.patch_channel("whale@42", {"sticker_mode": "trial", "use_slang": True})
    assert response.status_code == 200, response.text
    assert api_world.channel("whale@42").sticker_mode == "trial" and api_world.channel("whale@42").use_slang
    assert (await api_world.patch_channel("whale@42", {"sticker_mode": "loud"})).status_code == 422
    bots = {b["id"]: b for b in (await api_world.http.get("/api/bots")).json()}
    group = next(c for c in bots[WHALE]["channels"] if c["group_id"] == "42")
    assert group["sticker_mode"] == "trial" and group["use_slang"] is True
    await _api_sticker(api_world)
    storage = (await api_world.http.get("/api/system/storage")).json()
    assert storage["sticker_count"] == 1 and storage["sizes"]["stickers"] > 0
    # Every stored message reaches the keeper, and the daily cleanup tidies the collection.
    state = api_world.state
    assert state.hub.stickers is state.stickers and state.brain.stickers is state.stickers
    assert state.brain.slang is state.slang and state.housekeeper.stickers is state.stickers
    cleaned = (await api_world.http.post("/api/system/storage/clean")).json()
    assert cleaned["last"]["stickers"] == 0 and cleaned["sticker_count"] == 1


# -- added by hand and drawn ----------------------------------------------------------------------------


class Painter(FakeProvider):
    def __init__(self, picture_bytes: bytes) -> None:
        super().__init__(name="painter", default="")
        self.picture = picture_bytes
        self.drawn: list[tuple[str, tuple[str, ...]]] = []

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes:
        self.drawn.append((prompt, tuple(images)))
        return self.picture


async def painter(kit: Kit, picture_bytes: bytes) -> Painter:
    provider = Painter(picture_bytes)
    config = ProviderConfig(id="painter", kind="codex", base_url="http://x", model="p")
    await kit.w.repo.upsert_provider(config)
    kit.w.brain.providers.runtimes["painter"] = ProviderRuntime(config, provider, kit.w.repo.record_llm_call)
    return provider


async def described(kit: Kit, sticker_id: int) -> bool:
    row = await kit.keeper.store.get(sticker_id)
    return row is not None and row["status"] == "ready"


async def test_an_uploaded_sticker_is_kept_described_and_offered_to_every_bot(kit: Kit) -> None:
    row, added = await kit.keeper.add_own(picture("blue"), "upload")
    assert added and row["kept"] and row["kind"] == "upload" and row["status"] == "pending"
    assert row["bot_id"] is None and row["group_key"] == ""
    await use_vision(kit)
    await eventually(lambda: described(kit, row["id"]))
    choices, _ = await kit.keeper.candidates(CHANNEL, "qq:10001", "", set())
    assert [s["id"] for s in choices] == [row["id"]]


async def test_uploading_a_picture_already_collected_keeps_it(kit: Kit) -> None:
    collected = await kit.add(1)
    assert not collected["kept"]
    row, added = await kit.keeper.add_own(picture("red", size=41), "upload")
    assert not added and row["id"] == collected["id"] and row["kept"]
    assert (await kit.keeper.store.get(collected["id"]))["kept"]  # type: ignore[index]
    assert len(await kit.stickers()) == 1


async def test_uploads_are_checked_and_refused_when_only_kept_ones_are_left(kit: Kit) -> None:
    with pytest.raises(StickerError):
        await kit.keeper.add_own(b"not a picture", "upload")
    with pytest.raises(StickerError, match="太大"):
        await kit.keeper.add_own(b"x" * (kit.keeper.config.upload_max_bytes + 1), "upload")
    kit.keeper.config = replace(kit.keeper.config, max_count=1)
    await kit.keeper.add_own(picture("blue"), "upload")
    with pytest.raises(StickerError, match="满了"):
        await kit.keeper.add_own(picture("green"), "upload")


async def test_a_picture_too_big_to_collect_can_still_be_added_by_hand(kit: Kit) -> None:
    kit.keeper.config = replace(kit.keeper.config, file_max_bytes=2000)
    big = picture(size=900)
    assert len(big) > 2000
    _, added = await kit.keeper.add_own(big, "upload")
    assert added


async def test_a_drawn_character_is_only_offered_to_its_bot(kit: Kit) -> None:
    provider = await painter(kit, picture("white", size=1024))
    row = await kit.keeper.draw("开心地跳起来", bot_name="阿鱼", bot_id="qq:10001", avatar=picture("blue"))
    assert row["kind"] == "drawn" and row["bot_id"] == "qq:10001" and row["kept"]
    assert (row["width"], row["height"]) == (512, 512) and row["content_type"] == "image/png"
    prompt, images = provider.drawn[0]
    assert "开心地跳起来" in prompt and "阿鱼" in prompt and "纯白背景" in prompt
    assert len(images) == 1 and images[0].startswith("data:image/png;base64,")
    await use_vision(kit)
    await eventually(lambda: described(kit, row["id"]))
    mine, _ = await kit.keeper.candidates(CHANNEL, "qq:10001", "", set())
    theirs, why = await kit.keeper.candidates(CHANNEL, "qq:10002", "", set())
    assert [s["id"] for s in mine] == [row["id"]] and theirs == [] and why == "没有能发的表情"


async def test_drawing_needs_a_model_that_can_and_runs_one_at_a_time(kit: Kit) -> None:
    with pytest.raises(StickerError, match="Codex"):
        await kit.keeper.draw("一只猫")
    await painter(kit, picture("white"))
    async with kit.keeper._drawing:
        with pytest.raises(StickerError, match="另一张"):
            await kit.keeper.draw("一只猫")
    assert (await kit.keeper.draw("一只猫"))["bot_id"] is None


async def test_a_picture_the_model_broke_is_a_clear_error(kit: Kit) -> None:
    await painter(kit, b"not a picture")
    with pytest.raises(StickerError, match="打不开"):
        await kit.keeper.draw("一只猫")
    assert await kit.stickers() == []


async def test_api_uploads_and_draws_stickers(api_world: ApiWorld) -> None:  # noqa: F811
    http, state = api_world.http, api_world.state
    added = await http.put("/api/stickers/upload", content=picture("blue"))
    assert added.status_code == 200, added.text
    assert added.json()["added"] is True and added.json()["sticker"]["kind"] == "upload"
    assert (await http.put("/api/stickers/upload", content=picture("blue"))).json()["added"] is False
    assert (await http.put("/api/stickers/upload", content=b"not a picture")).status_code == 422

    assert (await http.get("/api/stickers")).json()["can_draw"] is False
    refused = await http.post("/api/stickers/draw", json={"idea": "一只猫"})
    assert refused.status_code == 409 and "Codex" in refused.json()["detail"]
    provider = Painter(picture("white"))
    config = ProviderConfig(id="painter", kind="codex", base_url="http://x", model="p")
    await state.repo.upsert_provider(config)
    state.providers.runtimes["painter"] = ProviderRuntime(config, provider, state.repo.record_llm_call)
    assert (await http.get("/api/stickers")).json()["can_draw"] is True

    assert (await http.post("/api/stickers/draw", json={"idea": "   "})).status_code == 422
    assert (await http.post("/api/stickers/draw", json={"idea": "猫", "bot_id": "qq:404"})).status_code == 404
    uploaded = (await http.put("/api/stickers/upload", content=saved(scene(7)))).json()["sticker"]["id"]
    owner = {"ids": [uploaded], "bot_id": WHALE}
    assert (await http.post("/api/stickers/owner", json=owner)).json() == {"updated": 1}
    assert (await state.stickers.store.get(uploaded))["bot_id"] == WHALE
    assert (await http.post("/api/stickers/owner", json={**owner, "bot_id": "qq:404"})).status_code == 404
    assert (await http.post("/api/stickers/owner", json={**owner, "bot_id": None})).status_code == 200
    assert (await state.stickers.store.get(uploaded))["bot_id"] is None
    drawn = await http.post("/api/stickers/draw", json={"idea": " 一只  猫 "})
    assert drawn.status_code == 200, drawn.text
    assert drawn.json()["kind"] == "drawn" and "一只 猫" in provider.drawn[0][0]
