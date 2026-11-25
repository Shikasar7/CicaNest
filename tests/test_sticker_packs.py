"""表情包 in the desk chat and 表情包管理: her own packs drawn by nest or uploaded, she
sends them in the desk chat (one in four replies at most, never in QQ), you send her stickers too."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nest.core.sticker_packs import MOODS, PackKeeper
from nest.llm.runtime import ProviderRuntime
from nest.storage.bots import ProviderConfig
from tests.fakes.llm import FakeProvider
from tests.helpers import eventually
from tests.test_bot_settings_api import WHALE, World, world  # noqa: F401 (fixture)
from tests.test_desk import private_chat
from tests.test_stickers import picture, saved, scene

AVATAR = picture("blue", size=96)


class Painter(FakeProvider):
    """Draws a different picture every time (the collection would take two equal ones for one)."""

    def __init__(self) -> None:
        super().__init__(name="painter", default="")
        self.drawn: list[tuple[str, tuple[str, ...]]] = []

    async def draw(self, prompt: str, images: Sequence[str] = ()) -> bytes:
        self.drawn.append((prompt, tuple(images)))
        return saved(scene(100 + len(self.drawn)))


async def painter(w: World) -> Painter:
    provider = Painter()
    config = ProviderConfig(id="painter", kind="codex", base_url="http://x", model="p")
    await w.state.repo.upsert_provider(config)
    w.state.providers.runtimes["painter"] = ProviderRuntime(config, provider, w.state.repo.record_llm_call)
    return provider


def packs(w: World) -> PackKeeper:
    return w.state.sticker_packs  # type: ignore[no-any-return]


async def new_pack(w: World, name: str = "默认表情包") -> dict[str, Any]:
    response = await w.http.post(f"/api/sticker-packs/bots/{WHALE}", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def drawn_pack(w: World) -> dict[str, Any]:
    """A pack whose first three moods are drawn (the rest taken out), in use."""
    await painter(w)
    await w.http.put(f"/api/bots/{WHALE}/avatar/custom", content=AVATAR)
    pack = await new_pack(w)
    for slot in pack["slots"][3:]:
        assert (await w.http.delete(f"/api/sticker-packs/slots/{slot['id']}")).status_code == 200
    drawing = await w.http.post(f"/api/sticker-packs/{pack['id']}/draw", json={"which": "missing"})
    assert drawing.status_code == 200
    return await eventually(lambda: _all_done(w, pack["id"]))


async def _all_done(w: World, pack_id: int) -> dict[str, Any] | None:
    pack = await packs(w).pack(pack_id)
    return pack if all(s["state"] == "done" for s in pack["slots"]) else None


async def test_a_new_pack_has_the_usual_moods_and_the_first_one_is_in_use(world: World) -> None:  # noqa: F811
    first = await new_pack(world)
    assert [s["mood"] for s in first["slots"]] == [m for m, _ in MOODS] and len(first["slots"]) == 15
    assert first["active"] and first["style"] == "half"
    assert all(s["state"] == "empty" and s["sticker_id"] is None for s in first["slots"])
    second = await new_pack(world, "猫猫版")
    assert not second["active"]

    response = await world.http.patch(
        f"/api/sticker-packs/{second['id']}", json={"active": True, "extra": "都戴着猫耳", "style": "head"}
    )
    assert response.status_code == 200 and response.json()["active"]
    assert response.json()["extra"] == "都戴着猫耳"
    listed = (await world.http.get(f"/api/sticker-packs/bots/{WHALE}")).json()
    assert [p["active"] for p in listed["packs"]] == [False, True] and listed["desk_stickers"] is True
    unknown = await world.http.patch(f"/api/sticker-packs/{second['id']}", json={"style": "x"})
    assert unknown.status_code == 409

    # Moods can be added, renamed and taken out.
    slots = f"/api/sticker-packs/{first['id']}/slots"
    assert (await world.http.post(slots, json={"mood": "  吃 瓜 "})).json()["slots"][-1]["mood"] == "吃 瓜"
    assert (await world.http.post(slots, json={"mood": "吃 瓜"})).status_code == 409
    assert (await world.http.post(slots, json={"mood": "   "})).status_code == 409

    # The pack in use deleted: the other one takes its place.
    assert (await world.http.delete(f"/api/sticker-packs/{second['id']}")).status_code == 200
    assert [p["active"] for p in (await packs(world).packs(WHALE))] == [True]
    overview = (await world.http.get("/api/sticker-packs")).json()
    mine = next(b for b in overview["bots"] if b["bot_id"] == WHALE)
    assert (mine["done"], mine["total"]) == (0, 16) and overview["can_draw"] is False


async def test_drawing_her_pack_in_her_likeness(world: World) -> None:  # noqa: F811
    pack = await new_pack(world)
    draw = f"/api/sticker-packs/{pack['id']}/draw"
    refused = await world.http.post(draw, json={"which": "missing"})
    assert refused.status_code == 409 and "Codex" in refused.json()["detail"]
    provider = await painter(world)
    refused = await world.http.post(draw, json={"which": "missing"})
    assert refused.status_code == 409 and "头像" in refused.json()["detail"]
    await world.http.put(f"/api/bots/{WHALE}/avatar/custom", content=AVATAR)
    await world.http.patch(f"/api/sticker-packs/{pack['id']}", json={"extra": "都戴着猫耳"})

    queued = await world.http.post(draw, json={"which": "missing"})
    assert queued.status_code == 200 and queued.json()["queued"] == 15 and queued.json()["minutes"] >= 1
    done = await eventually(lambda: _all_done(world, pack["id"]), timeout=20)
    ids = [s["sticker_id"] for s in done["slots"]]
    assert len(set(ids)) == 15
    prompt, images = provider.drawn[1]
    assert "「卖萌」的表情：歪头眨一只眼" in prompt and "都戴着猫耳" in prompt and "半身 Q 版" in prompt
    assert len(images) == 1  # her picture, as the reference
    row = await world.state.stickers.store.get(ids[1])
    assert row["bot_id"] == WHALE and row["status"] == "ready" and row["kind"] == "drawn"
    assert row["description"].startswith("阿鲸卖萌") and row["tags"] == ["卖萌"]
    assert (await world.http.post(draw, json={"which": "missing"})).status_code == 409  # nothing left

    # One drawn again: the old picture leaves the collection.
    again = await world.http.post(draw, json={"slots": [done["slots"][0]["id"]]})
    assert again.json()["queued"] == 1
    redone = await eventually(lambda: _all_done(world, pack["id"]))
    assert redone["slots"][0]["sticker_id"] not in ids
    assert await world.state.stickers.store.get(ids[0]) is None

    # Your own picture for a mood.
    slot = redone["slots"][2]["id"]
    uploaded = await world.http.put(f"/api/sticker-packs/slots/{slot}/picture", content=picture("green"))
    assert uploaded.status_code == 200, uploaded.text
    placed = uploaded.json()["slots"][2]
    assert placed["state"] == "done" and placed["sticker_id"] != ids[2]
    assert (await world.state.stickers.store.get(placed["sticker_id"]))["kind"] == "upload"
    bad = await world.http.put(f"/api/sticker-packs/slots/{slot}/picture", content=b"not a picture")
    assert bad.status_code == 422


async def test_she_sends_her_own_stickers_in_the_desk_chat_now_and_then(world: World) -> None:  # noqa: F811
    pack = await drawn_pack(world)
    chat = await private_chat(world)
    model = world.state.providers.runtimes["ark"].provider
    model.queue("好呀好呀[表情2]")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "周末去玩吗"})
    said = await eventually(lambda: _hers(world, chat, 2))
    words, sticker = said
    assert words["text"] == "好呀好呀"
    assert [s["type"] for s in sticker["segments"]] == ["sticker"]
    assert sticker["segments"][0]["data"]["id"] == pack["slots"][1]["sticker_id"]
    assert sticker["text"].startswith("（发了表情：阿鲸卖萌")
    _, user = model.requests[0]
    # Hers only, newest first.
    assert "[表情1] 阿鲸害羞" in user.content and "[表情3] 阿鲸开心" in user.content
    assert "[表情4]" not in user.content

    # Not again in her next three replies: none are offered.
    for n in range(3):
        await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": f"第{n}句"})
        await eventually(lambda n=n: _hers(world, chat, 3 + n))
        assert "[表情1]" not in model.requests[-1][1].content
    # (the collection's own pause between two stickers in one chat has passed too)
    await world.sql("UPDATE decisions SET created_at = created_at - 3600 WHERE sticker_state = 'sent'")
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "再来"})
    await eventually(lambda: _hers(world, chat, 6))
    assert "[表情1]" in model.requests[-1][1].content

    # Turned off for her: none offered.
    off = await world.http.put(f"/api/sticker-packs/bots/{WHALE}/desk", json={"on": False})
    assert off.status_code == 200
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "还在吗"})
    await eventually(lambda: _hers(world, chat, 7))
    assert "[表情1]" not in model.requests[-1][1].content


async def test_you_send_her_a_sticker_and_she_reads_what_it_is(world: World) -> None:  # noqa: F811
    pack = await drawn_pack(world)
    chat = await private_chat(world)
    sticker_id = pack["slots"][2]["sticker_id"]
    model = world.state.providers.runtimes["ark"].provider
    response = await world.http.post(f"/api/desk/{WHALE}/messages", json={"sticker_id": sticker_id})
    assert response.status_code == 202, response.text
    await eventually(lambda: _hers(world, chat, 1))
    mine = next(r for r in await _rows(world, chat) if not r["from_self"])
    assert mine["segments"] == [{"type": "sticker", "data": {"id": sticker_id, "label": mine["text"][4:-1]}}]
    assert mine["text"].startswith("[表情：阿鲸害羞")
    assert "[表情：阿鲸害羞" in model.requests[0][1].content
    missing = await world.http.post(f"/api/desk/{WHALE}/messages", json={"sticker_id": 99999})
    assert missing.status_code == 404

    picked = (await world.http.get(f"/api/sticker-packs/bots/{WHALE}/picker")).json()
    assert [s["id"] for s in picked["hers"]] == [s["sticker_id"] for s in pack["slots"]]
    assert [s["id"] for s in picked["recent"]] == [sticker_id]


async def test_her_pack_stays_out_of_the_groups(world: World) -> None:  # noqa: F811
    pack = await drawn_pack(world)
    ids = {s["sticker_id"] for s in pack["slots"]}
    keeper = world.state.stickers
    group = world.channel("whale@42").channel
    offered, _ = await keeper.candidates(group, WHALE, "开心", set(), hide=await packs(world).all_pictures())
    assert not ids & {s["id"] for s in offered}
    offered, _ = await keeper.candidates(group, WHALE, "开心", set(), only=await packs(world).in_use(WHALE))
    assert {s["id"] for s in offered} == ids


async def _rows(w: World, chat: int) -> list[dict[str, Any]]:
    return (await w.http.get(f"/api/channels/{chat}/messages")).json()  # type: ignore[no-any-return]


async def _hers(w: World, chat: int, count: int) -> list[dict[str, Any]] | None:
    hers = [r for r in await _rows(w, chat) if r["from_self"]]
    return hers if len(hers) >= count else None


async def test_only_her_own_stickers_unless_you_let_her_use_the_collection(world: World) -> None:  # noqa: F811
    keeper = world.state.stickers
    common, _ = await keeper.add_own(picture("yellow"), "upload")  # anyone's
    await keeper.store.update(common["id"], description="一只开心的猫", tags=["开心"], status="ready")
    chat = await private_chat(world)
    model = world.state.providers.runtimes["ark"].provider

    # No pack, nothing of hers: with 「只用专属」 (the default) she is offered none.
    await world.http.post(f"/api/desk/{WHALE}/messages", json={"text": "开心"})
    await eventually(lambda: _hers(world, chat, 1))
    assert "[表情1]" not in model.requests[-1][1].content

    pack = await drawn_pack(world)
    gift, _ = await keeper.add_own(picture("purple"), "drawn", WHALE)  # drawn for her with 「画一张」
    await keeper.store.update(gift["id"], description="阿鲸比耶", tags=["比耶"], status="ready")
    own = [s["sticker_id"] for s in pack["slots"]] + [gift["id"]]
    assert await packs(world).own(WHALE) == own
    listed = (await world.http.get(f"/api/sticker-packs/bots/{WHALE}")).json()
    assert listed["own_only"] is True and [s["id"] for s in listed["own"]] == own

    # Let her use the collection too: hers come first, the cat after them.
    switch = await world.http.put(f"/api/sticker-packs/bots/{WHALE}/own-only", json={"on": False})
    assert switch.status_code == 200 and not await packs(world).own_only(WHALE)
    offered, _ = await keeper.candidates(
        world.channel("whale@42").channel, WHALE, "开心", set(), prefer=await packs(world).own(WHALE)
    )
    ids = [s["id"] for s in offered]
    assert set(ids[: len(own)]) == set(own) and common["id"] in ids[len(own) :]
