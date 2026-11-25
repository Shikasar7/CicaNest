"""桌面群: the user and several companions chatting together, in the desk chat only.

Channels stay "one account's view of a place": each member sees the group through a channel of her own
(account = her, kind "group", id "desk-g<group id>"), like one QQ group seen by several of our accounts. So
the brain needs no new idea of a group: what the user says is stored in every member's channel (one message
id for all the copies), and what a member says is stored in hers (as said by her) and copied into the others'
(as said by another of our bots). Each member then decides by the usual group rules whether to chime in
(nest.core.brain: one companion picks up each message nobody was named in, the chain of bot turns is
limited), and her answer is shown in the desk chat, never sent anywhere.

The window shows a group through one member's channel (`view`): every message reaches every member's.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from nest.core.broadcast import Broadcaster
from nest.core.desk import chat_of, chosen_chats
from nest.core.events import ChannelRef
from nest.llm.base import Tool
from nest.storage.bots import BotConfig, BotRepo
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.storage.slang import GroupFeatureStore

PREFIX = "desk-g"  # a desk group's id in its members' channels
NAME_MAX = 20
MEMBERS_MAX = 6
CREATOR_USER = "user"
# How much each member talks there (the group setting 活跃度): 话多 / 普通 / 安静.
LIVELINESS = {"active": "话多", "occasional": "普通", "mentions": "安静"}


class GroupError(Exception):
    """Why that cannot be done, in one Chinese sentence."""


def is_desk_group(ref: ChannelRef) -> bool:
    return ref.kind == "group" and ref.id.startswith(PREFIX)


def group_id_of(ref: ChannelRef) -> int | None:
    if not is_desk_group(ref):
        return None
    tail = ref.id.removeprefix(PREFIX)
    return int(tail) if tail.isdigit() else None


def group_ref(bot: BotConfig, group_id: int) -> ChannelRef:
    return ChannelRef(bot.account_id.partition(":")[0], bot.self_id, "group", f"{PREFIX}{group_id}")


class DeskGroups:
    def __init__(
        self,
        db: Database,
        repo: BotRepo,
        store: MessageStore,
        broadcaster: Broadcaster,
        bots: Callable[[], Mapping[str, BotConfig]],
        reload: Callable[[], Any],
    ) -> None:
        self.db = db
        self.repo = repo
        self.store = store
        self.broadcaster = broadcaster
        self.bots = bots
        self.reload = reload  # the brain picks up channels added or turned off

    def _changed(self, group_id: int) -> None:
        # "bots": the desk lists groups with the companions ("groups" is the QQ groups' features).
        self.broadcaster.publish("bots", {"group_id": group_id})

    # -- reading ------------------------------------------------------------------------------------

    async def groups(self) -> list[dict[str, Any]]:
        """The groups still going, oldest first, each with its members (in the order they joined) and the
        channel the window shows it through."""
        groups = [
            dict(r)
            for r in await self.db.fetchall(
                "SELECT * FROM desk_groups WHERE dissolved_at IS NULL ORDER BY id"
            )
        ]
        members: dict[int, list[dict[str, Any]]] = {g["id"]: [] for g in groups}
        for row in await self.db.fetchall(
            """SELECT group_id, bot_id, channel_id, joined_at FROM desk_group_members
               ORDER BY joined_at, bot_id"""
        ):
            if row["group_id"] in members:
                members[row["group_id"]].append(dict(row))
        bots = self.bots()
        for group in groups:
            listed = [m for m in members[group["id"]] if m["bot_id"] in bots]
            for member in listed:
                channel = bots[member["bot_id"]].channels.get(member["channel_id"])
                member["name"] = bots[member["bot_id"]].name
                member["activity"] = channel.activity if channel else "active"
            mine = [bots[m["bot_id"]].channels.get(m["channel_id"]) for m in listed]
            group["remember"] = any(c is not None and c.remember for c in mine)
            group["slang"] = any(c is not None and c.use_slang for c in mine)
            group["members"] = listed
            group["view"] = listed[0]["channel_id"] if listed else None
        return groups

    async def group(self, group_id: int) -> dict[str, Any]:
        found = next((g for g in await self.groups() if g["id"] == group_id), None)
        if found is None:
            raise GroupError("没有这个群，或者已经解散了")
        return found

    async def copies(self, ref: ChannelRef) -> list[ChannelRef]:
        """The other members' channels of the desk group `ref` is one member's view of."""
        group_id = group_id_of(ref)
        if group_id is None:
            return []
        bots = self.bots()
        rows = await self.db.fetchall("SELECT bot_id FROM desk_group_members WHERE group_id = ?", (group_id,))
        out = []
        for row in rows:
            bot = bots.get(row["bot_id"])
            if bot is not None and group_ref(bot, group_id) != ref:
                out.append(group_ref(bot, group_id))
        return out

    async def members_of(self, group_id: int) -> list[tuple[BotConfig, int]]:
        bots = self.bots()
        rows = await self.db.fetchall(
            "SELECT bot_id, channel_id FROM desk_group_members WHERE group_id = ? ORDER BY joined_at, bot_id",
            (group_id,),
        )
        return [(bots[r["bot_id"]], r["channel_id"]) for r in rows if r["bot_id"] in bots]

    # -- editing ------------------------------------------------------------------------------------

    async def create(self, name: str, bot_ids: list[str], created_by: str = CREATOR_USER) -> dict[str, Any]:
        """A new group of the user and these companions (at least two). `created_by`: who made it."""
        name = " ".join(name.split())[:NAME_MAX]
        if not name:
            raise GroupError("给群起个名字吧")
        wanted = list(dict.fromkeys(bot_ids))
        bots = self.bots()
        if any(b not in bots for b in wanted):
            raise GroupError("里面有不认识的伙伴")
        if len(wanted) < 2:
            raise GroupError("至少拉两个伙伴进来，才热闹")
        if len(wanted) > MEMBERS_MAX:
            raise GroupError(f"一个群最多 {MEMBERS_MAX} 个伙伴")
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO desk_groups (name, created_by, created_at) VALUES (?, ?, ?)",
                (name, created_by, time.time()),
            )
            group_id = int(cursor.lastrowid or 0)
        for bot_id in wanted:
            await self._join(group_id, bots[bot_id], name)
        await self.reload()
        self._changed(group_id)
        return await self.group(group_id)

    async def _join(self, group_id: int, bot: BotConfig, name: str) -> None:
        channel_id = await self.store.channel_id(group_ref(bot, group_id))
        # The persona she has with the user (her desk chat's), not a QQ group's.
        chat = chat_of(bot, (await chosen_chats(self.db)).get(bot.id))
        persona = chat.persona_id if chat is not None else None
        await self.repo.set_channel(
            bot.id,
            channel_id,
            enabled=True,
            activity="active",  # every message may be picked up; one companion per message (the brain)
            persona_id=persona,
            cooldown_seconds=0,
            sticker_mode="on",
            remember=None,
        )
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE channels SET name = ? WHERE id = ?", (name, channel_id))
            await conn.execute(
                """INSERT OR REPLACE INTO desk_group_members (group_id, bot_id, channel_id, joined_at)
                   VALUES (?, ?, ?, ?)""",
                (group_id, bot.id, channel_id, time.time()),
            )

    async def update(
        self,
        group_id: int,
        *,
        name: str | None = None,
        members: list[str] | None = None,
        activity: Mapping[str, str] | None = None,
        remember: bool | None = None,
        slang: bool | None = None,
    ) -> dict[str, Any]:
        """`remember`: 长期记忆 of what is said there; `slang`: they learn the group's own words and use them.
        Both for every member, and for who joins later."""
        group = await self.group(group_id)
        remember = group["remember"] if remember is None and members is not None else remember
        slang = group["slang"] if slang is None and members is not None else slang
        bots = self.bots()
        if name is not None:
            name = " ".join(name.split())[:NAME_MAX]
            if not name:
                raise GroupError("给群起个名字吧")
            async with self.db.transaction() as conn:
                await conn.execute("UPDATE desk_groups SET name = ? WHERE id = ?", (name, group_id))
                for m in group["members"]:
                    await conn.execute("UPDATE channels SET name = ? WHERE id = ?", (name, m["channel_id"]))
        if members is not None:
            wanted = list(dict.fromkeys(members))
            if any(b not in bots for b in wanted):
                raise GroupError("里面有不认识的伙伴")
            if len(wanted) < 2:
                raise GroupError("群里至少要有两个伙伴；不想要了可以解散")
            if len(wanted) > MEMBERS_MAX:
                raise GroupError(f"一个群最多 {MEMBERS_MAX} 个伙伴")
            now = {m["bot_id"]: m["channel_id"] for m in group["members"]}
            for bot_id, channel_id in now.items():
                if bot_id not in wanted:
                    await self.repo.set_channel(bot_id, channel_id, enabled=False)
                    async with self.db.transaction() as conn:
                        await conn.execute(
                            "DELETE FROM desk_group_members WHERE group_id = ? AND bot_id = ?",
                            (group_id, bot_id),
                        )
            for bot_id in wanted:
                if bot_id not in now:
                    await self._join(group_id, bots[bot_id], name or group["name"])
        if activity:
            current = {m["bot_id"]: m["channel_id"] for m in (await self.group(group_id))["members"]}
            for bot_id, level in activity.items():
                if level not in LIVELINESS:
                    raise GroupError("活跃度只能是话多、普通或安静")
                if bot_id in current:
                    await self.repo.set_channel(bot_id, current[bot_id], activity=level)
        if remember is not None or slang is not None:
            for member in (await self.group(group_id))["members"]:
                await self.repo.set_channel(
                    member["bot_id"], member["channel_id"], remember=remember, use_slang=slang
                )
            if slang is not None:
                key = f"desk:group:{PREFIX}{group_id}"
                await GroupFeatureStore(self.db).set(key, learn_slang=slang)
        await self.reload()
        self._changed(group_id)
        return await self.group(group_id)

    async def dissolve(self, group_id: int) -> None:
        """解散: nobody hears it any more; the history stays."""
        group = await self.group(group_id)
        for member in group["members"]:
            await self.repo.set_channel(member["bot_id"], member["channel_id"], enabled=False)
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE desk_groups SET dissolved_at = ? WHERE id = ?", (time.time(), group_id)
            )
        await self.reload()
        self._changed(group_id)


# 她拉群: in her chat with the user she may make a group; the user is always in it.
CREATE_GROUP = Tool(
    "create_group",
    "拉一个桌面群：你、对方，再加上你点名的伙伴，一起聊天（只在桌面聊天里，不会发到 QQ）。"
    "对方想大家一起聊、或者一起商量件事更好玩的时候才用，不要随便拉。建好后告诉对方群名和拉了谁。",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "群名字，20 字以内，比如「周末吃什么」"},
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要拉进来的其他伙伴的名字（至少一个，不用写你自己）",
            },
        },
        "required": ["name", "members"],
    },
)


async def create_group_tool(groups: DeskGroups, bot: BotConfig, given: Mapping[str, Any]) -> str:
    """The create_group tool: she and the companions she names, made by her."""
    bots = groups.bots()
    names = [str(n).strip() for n in given.get("members") or [] if str(n).strip()]
    wanted = [bot.id]
    unknown = []
    for name in names:
        found = next((b for b in bots.values() if b.name == name), None) or next(
            (b for b in bots.values() if name in b.name or b.name in name), None
        )
        if found is None:
            unknown.append(name)
        elif found.id not in wanted:
            wanted.append(found.id)
    if unknown:
        known = "、".join(b.name for b in bots.values() if b.id != bot.id)
        return f"没找到这些伙伴：{'、'.join(unknown)}。能拉的有：{known}"
    try:
        made = await groups.create(str(given.get("name") or ""), wanted, created_by=bot.id)
    except GroupError as exc:
        return f"没建成：{exc}"
    others = "、".join(m["name"] for m in made["members"] if m["bot_id"] != bot.id)
    return f"建好了「{made['name']}」，群里有对方、你和{others}。对方在聊天窗口的「群聊」里能看到。"


# 她拉群 for a model that cannot use tools: a mark at the end of her reply, taken out before it is shown.
GROUP_MARK = re.compile(r"\[\s*拉群\s*[:：]\s*([^|｜\]\n]{1,40})[|｜]([^\]\n]{1,120})\]")
GROUP_MARK_HINT = (
    "对方想大家一起聊、或者一起商量件事更好玩的时候，你可以拉一个桌面群："
    "在回复最后写 [拉群：群名｜伙伴1、伙伴2]（伙伴写名字，不用写你自己），nest 会建好群并把对方拉进去。"
    "不要随便拉。"
)


def split_group(raw: str) -> tuple[str, dict[str, Any] | None]:
    """(her reply without the mark, the group she asked for as create_group's arguments, or None)."""
    found = GROUP_MARK.search(raw)
    if found is None:
        return raw, None
    members = [m.strip() for m in re.split(r"[、,，/ ]+", found.group(2)) if m.strip()]
    return (raw[: found.start()] + raw[found.end() :]).strip(), {
        "name": found.group(1).strip(),
        "members": members,
    }
