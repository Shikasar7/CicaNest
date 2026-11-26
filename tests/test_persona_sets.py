"""人设方案: save a bot's persona in each place, switch the whole set and back."""

from __future__ import annotations

import time
from typing import Any

from nest.adapters.base import AdapterStatus
from nest.core.events import MessageEvent
from nest.core.prompt import render_record, speaker
from nest.core.segments import Segment
from nest.storage.messages import MessageStore
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, persona, world  # noqa: F401


async def sets_of(world: World, bot_id: str) -> dict[str, dict[str, Any]]:  # noqa: F811
    [bot] = [b for b in (await world.http.get("/api/bots")).json() if b["id"] == bot_id]
    return {s["name"]: s for s in bot["persona_sets"]}


async def save(world: World, name: str, bot_id: str = WHALE) -> int:  # noqa: F811
    response = await world.http.post(f"/api/bots/{bot_id}/persona-sets", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def switch(world: World, set_id: int, bot_id: str = WHALE) -> Any:  # noqa: F811
    return await world.http.post(f"/api/bots/{bot_id}/persona-sets/{set_id}/apply")


async def test_a_whole_set_is_switched_and_switched_back(world: World) -> None:  # noqa: F811
    tavern = await persona(world, "鲸@群甲")
    plaza = await persona(world, "鲸@群乙")
    new = await persona(world, "新鲸")
    assert (await world.patch_channel("whale@42", {"persona_id": tavern["id"]})).status_code == 200
    assert (await world.patch_channel("whale@77", {"persona_id": plaza["id"]})).status_code == 200
    old = await save(world, "原来")

    for key in ("whale@42", "whale@77"):
        assert (await world.patch_channel(key, {"persona_id": new["id"]})).status_code == 200
    fresh = await save(world, "  新的 ")
    sets = await sets_of(world, WHALE)
    assert sets["原来"]["current"] is False and sets["新的"]["current"] is True
    assert sorted(sets["原来"]["items"], key=lambda i: i["channel_id"]) == sorted(
        [
            {"channel_id": world.channels["whale@42"], "persona_id": tavern["id"]},
            {"channel_id": world.channels["whale@77"], "persona_id": plaza["id"]},
        ],
        key=lambda i: i["channel_id"],
    )

    world.events.clear()
    switched = await switch(world, old)
    assert switched.status_code == 200 and switched.json()["changed"] == 2
    # At once, in what the brain uses.
    assert world.channel("whale@42").persona_id == tavern["id"]
    assert world.channel("whale@77").persona_id == plaza["id"]
    await eventually(lambda: ("bots", {"bot_id": WHALE}) in world.events)
    assert (await switch(world, old)).json()["changed"] == 0
    sets = await sets_of(world, WHALE)
    assert sets["原来"]["current"] is True and sets["新的"]["current"] is False

    assert (await switch(world, fresh)).json()["changed"] == 2
    assert world.channel("whale@42").persona_id == new["id"]
    # The other bot in the same group is not touched.
    assert world.channel("pony@42").persona_id is None


async def test_no_persona_is_restored_and_places_added_later_keep_theirs(world: World) -> None:  # noqa: F811
    whale = await persona(world, "鲸")
    assert (await world.patch_channel("whale@42", {"persona_id": whale["id"]})).status_code == 200
    set_id = await save(world, "原来")  # whale@77 has 不用人设
    # A place the set does not know, as if the bot joined it after the set was saved.
    forget = "DELETE FROM persona_set_items WHERE set_id = ? AND channel_id = ?"
    await world.sql(forget, set_id, world.channels["whale@42"])
    later = await persona(world, "后来")
    for key in ("whale@42", "whale@77"):
        assert (await world.patch_channel(key, {"persona_id": later["id"]})).status_code == 200
    assert (await switch(world, set_id)).json()["changed"] == 1
    assert world.channel("whale@77").persona_id is None
    assert world.channel("whale@42").persona_id == later["id"]


async def test_sets_are_named_resaved_renamed_and_deleted_per_bot(world: World) -> None:  # noqa: F811
    whale = await persona(world, "鲸")
    set_id = await save(world, "原来")
    taken = await world.http.post(f"/api/bots/{WHALE}/persona-sets", json={"name": "原来"})
    assert taken.status_code == 409 and taken.json() == {"detail": "已经有叫「原来」的方案"}
    # Another bot may use the same name.
    pony_set = await save(world, "原来", PONY)
    for name in ("", "  ", "x" * 21, "a\nb"):
        refused = await world.http.post(f"/api/bots/{WHALE}/persona-sets", json={"name": name})
        assert refused.status_code == 422, name
    assert (await world.http.post("/api/bots/qq:1/persona-sets", json={"name": "a"})).status_code == 404

    # A set belongs to its bot.
    assert (await switch(world, set_id, PONY)).status_code == 404
    assert (await switch(world, pony_set)).status_code == 404
    assert (await switch(world, 999)).status_code == 404

    assert (await world.patch_channel("whale@42", {"persona_id": whale["id"]})).status_code == 200
    assert (await sets_of(world, WHALE))["原来"]["current"] is False
    path = f"/api/bots/{WHALE}/persona-sets/{set_id}"
    assert (await world.http.patch(path, json={"resave": True})).status_code == 200
    assert (await sets_of(world, WHALE))["原来"]["current"] is True
    other = await save(world, "另一套")
    onto = await world.http.patch(f"/api/bots/{WHALE}/persona-sets/{other}", json={"name": "原来"})
    assert onto.status_code == 409
    assert (await world.http.patch(path, json={})).status_code == 422
    assert (await world.http.patch(path, json={"name": " 鲸鱼版 "})).status_code == 200
    assert set(await sets_of(world, WHALE)) == {"鲸鱼版", "另一套"}

    assert (await world.http.delete(f"/api/bots/{PONY}/persona-sets/{set_id}")).status_code == 404
    assert (await world.http.delete(path)).status_code == 200
    assert (await world.http.delete(path)).status_code == 404
    assert set(await sets_of(world, WHALE)) == {"另一套"}


async def test_a_persona_a_set_keeps_cannot_be_deleted(world: World) -> None:  # noqa: F811
    whale = await persona(world, "鲸")
    assert (await world.patch_channel("whale@42", {"persona_id": whale["id"]})).status_code == 200
    set_id = await save(world, "原来")
    assert (await world.patch_channel("whale@42", {"persona_id": None})).status_code == 200
    refused = await world.http.delete(f"/api/personas/{whale['id']}")
    assert refused.status_code == 409 and refused.json() == {"detail": "还用在：阿鲸的方案「原来」"}
    # A removed bot's sets let go of it.
    await world.sql("UPDATE bots SET deleted_at = 1 WHERE id = ?", WHALE)
    assert (await world.http.delete(f"/api/personas/{whale['id']}")).status_code == 200
    row = await world.row("SELECT persona_id FROM persona_set_items WHERE set_id = ?", set_id)
    assert row[0] is None


async def test_a_bot_without_places_has_nothing_to_save(world: World) -> None:  # noqa: F811
    refused = await world.http.post("/api/bots/qq:10003/persona-sets", json={"name": "原来"})
    assert refused.status_code == 400


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40


class FakeAccount:
    """A QQ account that takes profile changes; fail names what it refuses."""

    def __init__(self, state: str = "connected", fail: tuple[str, ...] = ()) -> None:
        self.state = state
        self.fail = fail
        self.worn: list[tuple[str, bytes | None, list[str]]] = []

    def status(self) -> AdapterStatus:
        return AdapterStatus(self.state, "", 0.0)  # type: ignore[arg-type]

    async def stop(self) -> None:
        pass

    async def set_profile(self, nickname: str, avatar: bytes | None, groups: Any) -> list[str]:
        self.worn.append((nickname, avatar, sorted(groups)))
        return [f"QQ 头像没换成（{reason}）" for reason in self.fail]


async def test_a_set_keeps_who_she_is_and_switching_puts_it_back(world: World) -> None:  # noqa: F811
    pony = await persona(world, "小马@群甲")
    assert (await world.patch_channel("whale@42", {"persona_id": pony["id"]})).status_code == 200
    old = await save(world, "阿马")
    identity = (await sets_of(world, WHALE))["阿马"]["identity"]
    assert identity | {"updated_at": 0} == {
        "name": "阿鲸",
        "aliases": ["鲸鱼"],
        "voice": "",
        "provider_id": "ark",
        "avatar": False,
        "updated_at": 0,
    }

    # Becoming the cat: her name, aliases, voice, model and persona, saved as a set.
    cat = await persona(world, "阿喵@群甲")
    assert (await world.patch_channel("whale@42", {"persona_id": cat["id"]})).status_code == 200
    body = {"name": "阿喵", "aliases": ["咪咪", "小喵"], "voice": "fish:abc", "provider_id": "grok"}
    assert (await world.patch_bot(WHALE, body)).status_code == 200
    new = await save(world, "阿喵")
    sets = await sets_of(world, WHALE)
    assert sets["阿喵"]["current"] is True and sets["阿马"]["current"] is False

    switched = await switch(world, old)
    assert switched.status_code == 200
    assert switched.json() == {"changed": 1, "renamed": True, "problems": [ANY_OFFLINE]}
    bot = world.bot(WHALE)
    assert (bot.name, bot.aliases, bot.voice, bot.provider_id) == ("阿鲸", ("鲸鱼",), "", "ark")
    assert world.channel("whale@42").persona_id == pony["id"]
    # The name decides which set she wears now, as well as the personas.
    sets = await sets_of(world, WHALE)
    assert sets["阿马"]["current"] is True and sets["阿喵"]["current"] is False

    assert (await switch(world, new)).json()["renamed"] is True
    bot = world.bot(WHALE)
    assert (bot.name, bot.aliases, bot.voice, bot.provider_id) == (
        "阿喵",
        ("咪咪", "小喵"),
        "fish:abc",
        "grok",
    )


ANY_OFFLINE = "账号没在线，昵称和头像没改：上线后再点一次「换成这套」"


async def test_a_set_saved_before_identities_leaves_the_name_alone(world: World) -> None:  # noqa: F811
    set_id = await save(world, "老方案")
    await world.sql(
        "UPDATE persona_sets SET bot_name = NULL, aliases = NULL, voice = NULL WHERE id = ?",
        set_id,
    )
    assert (await sets_of(world, WHALE))["老方案"]["identity"] is None
    assert (await world.patch_bot(WHALE, {"name": "别的名字"})).status_code == 200
    assert (await switch(world, set_id)).json() == {"changed": 0, "renamed": False, "problems": []}
    assert world.bot(WHALE).name == "别的名字"


async def test_a_saved_model_that_was_deleted_keeps_hers(world: World) -> None:  # noqa: F811
    set_id = await save(world, "原来")
    await world.sql("UPDATE persona_sets SET provider_id = NULL WHERE id = ?", set_id)
    assert (await world.patch_bot(WHALE, {"name": "新名字", "provider_id": "grok"})).status_code == 200
    assert (await switch(world, set_id)).json()["renamed"] is True
    assert (world.bot(WHALE).name, world.bot(WHALE).provider_id) == ("阿鲸", "grok")


async def test_switching_puts_on_the_nickname_and_the_sets_avatar(world: World) -> None:  # noqa: F811
    account = FakeAccount()
    world.state.hub.adapters[WHALE] = account
    set_id = await save(world, "原来")
    path = f"/api/bots/{WHALE}/persona-sets/{set_id}/avatar"
    assert (await world.http.get(path)).status_code == 404
    assert (await world.http.put(path, content=GIF)).status_code == 415
    assert (await world.http.put(path, content=b"<svg/>")).status_code == 415
    assert (await world.http.put(path, content=b"\x89PNG" + b"\x00" * (3 * 1024 * 1024))).status_code == 413
    assert (
        await world.http.put(f"/api/bots/{PONY}/persona-sets/{set_id}/avatar", content=PNG)
    ).status_code == 404
    assert (await world.http.put(path, content=PNG)).status_code == 200
    served = await world.http.get(path)
    assert (
        served.status_code == 200 and served.content == PNG and served.headers["content-type"] == "image/png"
    )
    assert (await sets_of(world, WHALE))["原来"]["identity"]["avatar"] is True

    assert (await world.patch_bot(WHALE, {"name": "别人"})).status_code == 200
    switched = await switch(world, set_id)
    assert switched.json() == {"changed": 0, "renamed": True, "problems": []}
    groups = sorted(world.channel(key).channel.id for key in ("whale@42", "whale@77"))
    assert account.worn == [("阿鲸", PNG, groups)]
    # The console now shows the avatar she wears.
    stored = await world.state.avatars.get(WHALE, "qq")
    assert stored is not None and stored.data == PNG
    await eventually(lambda: ("avatars", {"bot_id": WHALE}) in world.events)

    # What QQ refused is told, and the avatar nest shows stays the one she has.
    account.fail = ("太频繁",)
    assert (await world.http.put(path, content=JPEG)).status_code == 200
    problems = (await switch(world, set_id)).json()["problems"]
    assert problems == ["QQ 头像没换成（太频繁）"]
    assert (await world.state.avatars.get(WHALE, "qq")).data == PNG  # type: ignore[union-attr]

    # No avatar: only the nickname.
    account.fail = ()
    assert (await world.http.delete(path)).status_code == 200
    await switch(world, set_id)
    assert account.worn[-1] == ("阿鲸", None, groups)

    account.state = "connecting"
    assert (await switch(world, set_id)).json()["problems"] == [ANY_OFFLINE]


async def test_saving_takes_the_qq_avatar_she_wears(world: World) -> None:  # noqa: F811
    await world.state.avatars.put(WHALE, "qq", "image/jpeg", JPEG)
    set_id = await save(world, "原来")
    served = await world.http.get(f"/api/bots/{WHALE}/persona-sets/{set_id}/avatar")
    assert served.content == JPEG
    # Resaving without a stored QQ avatar keeps the set's picture.
    await world.sql("DELETE FROM bot_avatars WHERE bot_id = ?", WHALE)
    assert (
        await world.http.patch(f"/api/bots/{WHALE}/persona-sets/{set_id}", json={"resave": True})
    ).status_code == 200
    assert (await world.http.get(f"/api/bots/{WHALE}/persona-sets/{set_id}/avatar")).content == JPEG


async def said(world: World, text: str, *, own: bool, at: float) -> None:  # noqa: F811
    """A line in whale@42: the account's own (sender 10001), or 小明's."""
    event = MessageEvent(
        channel=world.channel("whale@42").channel,
        message_id=f"m{text}",
        sender_id="10001" if own else "7",
        sender_name="阿鲸" if own else "小明",
        segments=(Segment("text", {"text": text}),),
        text=text,
        timestamp=at,
        from_self=own,
    )
    assert await world.state.store.save(event) is not None


async def test_after_a_switch_what_the_account_said_before_is_not_hers(world: World) -> None:  # noqa: F811
    store = world.state.store
    channel = world.channels["whale@42"]
    old = await save(world, "阿马")
    assert (await world.patch_bot(WHALE, {"name": "阿喵"})).status_code == 200
    cat = await save(world, "阿喵")
    assert (await world.patch_bot(WHALE, {"name": "阿鲸"})).status_code == 200  # a rename, not a switch

    now = time.time()
    await said(world, "本小姐才不唱歌", own=True, at=now - 300)
    await said(world, "小马唱首歌嘛", own=False, at=now - 200)
    # No switch yet: all of it is hers.
    assert all("said_as" not in r for r in await store.recent(channel))

    await switch(world, cat)
    await said(world, "喵？", own=True, at=time.time())
    rows = {r["text"]: r for r in await store.recent(channel)}
    assert rows["本小姐才不唱歌"]["said_as"] == "阿鲸" and "said_as" not in rows["喵？"]
    assert "said_as" not in rows["小马唱首歌嘛"]  # what others said is theirs as ever
    shown = speaker(rows["本小姐才不唱歌"], "阿喵")
    assert shown == "阿鲸（这个号以前的人设，不是你）" and speaker(rows["喵？"], "阿喵") == "你（阿喵）"
    assert (
        render_record(rows["本小姐才不唱歌"], bot_name="阿喵").count("阿鲸（这个号以前的人设，不是你）") == 1
    )

    # Back again: the first lines are hers once more, the cat's are not.
    await switch(world, old)
    rows = {r["text"]: r for r in await store.recent(channel)}
    assert "said_as" not in rows["本小姐才不唱歌"] and rows["喵？"]["said_as"] == "阿喵"
    # Kept across a restart.
    fresh = MessageStore(world.state.db)
    await fresh.load_switches()
    assert {r["text"]: r.get("said_as") for r in await fresh.recent(channel)}["喵？"] == "阿喵"
    # Re-applying the set she wears records no switch.
    count = "SELECT count(*) FROM identity_switches"
    before = (await world.row(count))[0]
    await switch(world, old)
    assert (await world.row(count))[0] == before
