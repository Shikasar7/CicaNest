"""人设方案: save a bot's persona in each place, switch the whole set and back."""

from __future__ import annotations

from typing import Any

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
    assert switched.status_code == 200 and switched.json() == {"changed": 2}
    # At once, in what the brain uses.
    assert world.channel("whale@42").persona_id == tavern["id"]
    assert world.channel("whale@77").persona_id == plaza["id"]
    await eventually(lambda: ("bots", {"bot_id": WHALE}) in world.events)
    assert (await switch(world, old)).json() == {"changed": 0}
    sets = await sets_of(world, WHALE)
    assert sets["原来"]["current"] is True and sets["新的"]["current"] is False

    assert (await switch(world, fresh)).json() == {"changed": 2}
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
    assert (await switch(world, set_id)).json() == {"changed": 1}
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
