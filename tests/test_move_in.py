"""接上平台账号 (nest.core.move_in): a companion of the desk moves into a bot with an account, with everything
of hers; nothing of anyone else's changes, and what cannot be is refused before anything is touched."""

from __future__ import annotations

import json
from typing import Any

from nest.core.move_in import renamed
from nest.storage.reminders import ReminderStore
from nest.storage.settings import SettingsRepo
from tests.helpers import eventually
from tests.test_bot_settings_api import PONY, WHALE, World, world  # noqa: F401 (fixture)


async def companion(w: World, name: str = "阿月") -> tuple[str, int]:
    body = {
        "name": name,
        "provider_id": "ark",
        "persona": "你是阿月，爱看云。",
        "points": [{"kind": "like", "content": "对方喜欢薄荷糖"}],
        "records": [{"title": "海边", "body": "那天去了海边。"}],
    }
    made = await w.http.post("/api/companions", json=body)
    assert made.status_code == 201, made.text
    return made.json()["bot_id"], made.json()["channel_id"]


async def fresh_bot(w: World, self_id: str = "10099") -> str:
    """A bot with an account, as the Bot page adds one."""
    from nest.storage.accounts import upsert_account

    state = w.state
    await upsert_account(
        state.db, platform="qq", self_id=self_id, name="新号", http_url="http://x", ws_url="ws://x/"
    )
    bot = await state.repo.upsert_bot(account_id=f"qq:{self_id}", name="新号", aliases=[], provider_id=None)
    await state.brain.load()
    return bot


async def everything(w: World) -> dict[str, list[dict[str, Any]]]:
    tables = await w.state.db.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {
        t["name"]: [dict(r) for r in await w.state.db.fetchall(f"SELECT * FROM {t['name']}")] for t in tables
    }


async def test_she_moves_with_everything_of_hers(world: World) -> None:  # noqa: F811
    state = world.state
    moon, chat = await companion(world)
    group = (
        await world.http.post("/api/desk/groups", json={"name": "小群", "bot_ids": [moon, WHALE]})
    ).json()
    said = await world.http.post(f"/api/desk/{moon}/messages", json={"text": "在吗"})
    assert said.status_code == 202

    async def answered() -> bool:
        rows = (await world.http.get(f"/api/channels/{chat}/messages")).json()
        return any(r["from_self"] for r in rows)

    await eventually(answered)
    await ReminderStore(state.db).add(bot_id=moon, channel_id=chat, text="喝水", due_at=4e9, source="console")
    state.workspace.write(moon, "诗.txt", "云".encode())
    settings = SettingsRepo(state.db)
    await settings.set_many({"desk_order": [PONY, moon], "desk_chats": {moon: chat, WHALE: 1}})
    old_self = moon.partition(":")[2]
    before = await everything(world)
    new = await fresh_bot(world)

    moved = await world.http.post(f"/api/bots/{new}/move-in", json={"companion": moon})
    assert moved.status_code == 200, moved.text
    copy = state.settings.data_dir / "backups" / moved.json()["backup"]
    assert copy.exists()

    assert moon not in state.brain.bots
    bot = state.brain.bots[new]
    assert (bot.name, bot.provider_id, bot.mode) == ("阿月", "ark", "live")
    assert bot.channels[chat].channel.account == "10099" and "爱看云" in bot.channels[chat].persona
    after = await everything(world)
    left = json.dumps(after, ensure_ascii=False, default=str)
    assert moon not in left and f'"{old_self}"' not in left  # nobody calls her by the old id any more
    for table in ("messages", "keepsakes", "reminders", "memories", "desk_group_members", "decisions"):
        assert len(after[table]) == len(before[table]), table
    others = [r for r in before["bot_channels"] if r["bot_id"] != moon]
    assert all(r in after["bot_channels"] for r in others)
    stored = await settings.all()
    assert stored["desk_order"] == [PONY, new] and stored["desk_chats"] == {new: chat, WHALE: 1}
    assert state.workspace.read(new, "诗.txt") == "云"
    assert new in {m["bot_id"] for m in (await state.groups.group(group["id"]))["members"]}

    # And she goes on: the same chat, her records, her words.
    kept = (await world.http.get(f"/api/bots/{new}/keepsakes")).json()
    assert [k["title"] for k in kept] == ["海边"]
    again = await world.http.post(f"/api/desk/{new}/messages", json={"text": "还是你吗"})
    assert again.status_code == 202 and again.json()["channel_id"] == chat
    assert (await world.http.get(f"/api/bots/{moon}/keepsakes")).status_code == 404


async def test_what_cannot_be_is_refused_and_changes_nothing(world: World) -> None:  # noqa: F811
    moon, _ = await companion(world)
    star, _ = await companion(world, "阿星")
    new = await fresh_bot(world)
    before = await everything(world)

    async def refused(bot: str, body: str, why: str, status: int = 409) -> None:
        response = await world.http.post(f"/api/bots/{bot}/move-in", json={"companion": body})
        assert response.status_code == status and why in response.json()["detail"], response.text

    await refused(new, WHALE, "已经有平台账号")
    await refused(star, moon, "也只在桌面上")
    await refused("qq:404", moon, "没有这个 bot", 404)
    await refused(new, "desk:404", "没有这个 bot", 404)
    world.state.workspace.write(moon, "诗.txt", b"y")
    world.state.workspace.write(new, "已有.txt", b"x")
    await refused(new, moon, "已经有东西")
    assert await everything(world) == before and moon in world.state.brain.bots

    # A bot that was lived in already is not for moving in.
    made = await world.http.post(f"/api/bots/{new}/keepsakes", json={"title": "旧事", "body": "以前的。"})
    assert made.status_code == 201
    await refused(new, moon, "已经有自己的回忆档案")


async def test_a_bot_that_already_speaks_in_groups_keeps_its_mode(world: World) -> None:  # noqa: F811
    moon, _ = await companion(world)
    new = await fresh_bot(world)
    from nest.core.events import ChannelRef

    channel = await world.state.store.channel_id(ChannelRef("qq", "10099", "group", "77"))
    await world.state.repo.set_channel(new, channel, enabled=True, activity="mentions")
    await world.state.brain.load()
    moved = await world.http.post(f"/api/bots/{new}/move-in", json={"companion": moon})
    assert moved.status_code == 200
    bot = world.state.brain.bots[new]
    assert bot.mode == "observe" and bot.channels[channel].enabled and len(bot.channels) == 2


def test_only_whole_strings_are_renamed() -> None:
    value = {"desk:c1": [{"a": "desk:c1"}, "desk:c12", 3, None], "x": "说到 desk:c1"}
    assert renamed(value, "desk:c1", "qq:1") == {
        "qq:1": [{"a": "qq:1"}, "desk:c12", 3, None],
        "x": "说到 desk:c1",
    }
