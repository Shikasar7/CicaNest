"""接上平台账号: a companion who lives only on the desk moves into a bot that has an
account on a platform, with everything of hers.

A bot's id is its account's id, so hers changes: whatever names her by it is told the new one — her chats,
memories, records, tasks, reminders, routines, sticker packs, days, settings. Channel ids stay, so what is
kept per chat stays hers. The bot she moves into was added for her just before: its name, model and mode
become hers, its account and connection stay.

One transaction: all of it, or nothing. What she has outside the database (her workspace folder) is moved by
the caller.
"""

from __future__ import annotations

import json
from typing import Any

from nest.adapters.registry import DESK
from nest.storage.bots import BotConfig
from nest.storage.db import Database
from nest.storage.tasks import ACTIVE

# Columns that hold a bot's id (an account's is the same), by name, in whatever table has them.
COLUMNS = ("bot_id", "account_id", "created_by")
OWN = ("bots", "accounts")  # the rows she is made of: handled by themselves


class MoveError(Exception):
    """Why she cannot move in there, in one Chinese sentence."""


async def problem(db: Database, companion: BotConfig, bot: BotConfig) -> str:
    """Why not, "" when she can."""
    if companion.account_id.partition(":")[0] != DESK:
        return f"{companion.name} 已经有平台账号了"
    if bot.account_id.partition(":")[0] == DESK:
        return f"{bot.name} 也只在桌面上，要选一个有平台账号的 bot"
    marks = ", ".join("?" for _ in ACTIVE)
    busy = await db.fetchone(
        f"SELECT count(*) AS n FROM tasks WHERE bot_id IN (?, ?) AND status IN ({marks})",
        (companion.id, bot.id, *ACTIVE),
    )
    if busy and busy["n"]:
        return "还有任务在做，等做完或者先停下"
    told = await db.fetchone(
        """SELECT count(*) AS n FROM messages m JOIN channels c ON c.id = m.channel_id
           WHERE c.account_id = ? AND c.external_id IN (
               SELECT external_id FROM channels WHERE account_id = ?)""",
        (bot.account_id, companion.account_id),
    )
    if told and told["n"]:
        return f"{bot.name} 在桌面上已经有自己的聊天了，不能再搬进去一位"
    for table, what in (("keepsakes", "回忆档案"), ("tasks", "任务"), ("reminders", "提醒")):
        row = await db.fetchone(f"SELECT count(*) AS n FROM {table} WHERE bot_id = ?", (bot.id,))
        if row and row["n"]:
            return f"{bot.name} 已经有自己的{what}了，要选一个新加的 bot"
    return ""


def renamed(value: Any, old: str, new: str) -> Any:
    """JSON with every "old" (a whole string, also as a key) made "new"."""
    if isinstance(value, dict):
        return {renamed(k, old, new): renamed(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [renamed(v, old, new) for v in value]
    return new if value == old else value


async def move_in(db: Database, companion: BotConfig, bot: BotConfig) -> None:
    if why := await problem(db, companion, bot):
        raise MoveError(why)
    old, new = companion.id, bot.id
    tables = [
        r["name"]
        for r in await db.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        if r["name"] not in OWN
    ]
    columns: list[tuple[str, str]] = []
    for table in tables:
        info = await db.fetchall(f"SELECT name FROM pragma_table_info('{table}')")
        columns += [(table, r["name"]) for r in info if r["name"] in COLUMNS]
    async with db.transaction() as conn:
        await conn.execute("PRAGMA defer_foreign_keys = ON")  # consistent again at the commit
        # The new bot's own groups stay with it, so what it may do there is for its mode to say: kept only
        # when it takes part in some already.
        cursor = await conn.execute(
            "SELECT count(*) FROM bot_channels WHERE bot_id = ? AND enabled = 1", (new,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        settled = bool(row and row[0])
        for table, column in columns:
            # Where both have one (an avatar, a life profile, an empty desk chat), hers stays.
            await conn.execute(
                f"UPDATE OR REPLACE {table} SET {column} = ? WHERE {column} = ?",  # names from the schema
                (new, old),
            )
        await conn.execute(
            "UPDATE messages SET sender_id = ? WHERE sender_id = ?", (bot.self_id, companion.self_id)
        )
        await conn.execute(
            f"""UPDATE bots SET (name, aliases, provider_id, backup_provider_id, task_provider_id, voice
                                 {"" if settled else ", mode"}) =
                (SELECT name, aliases, provider_id, backup_provider_id, task_provider_id, voice
                        {"" if settled else ", mode"} FROM bots WHERE id = ?) WHERE id = ?""",
            (old, new),
        )
        await conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (companion.name, bot.account_id))
        await conn.execute("DELETE FROM bots WHERE id = ?", (old,))
        await conn.execute("DELETE FROM accounts WHERE id = ?", (companion.account_id,))
        for table, key, column in (("settings", "key", "value"), ("life_days", "day", "plan")):
            cursor = await conn.execute(
                f"SELECT {key}, {column} FROM {table} WHERE instr({column}, ?)", (old,)
            )
            rows = list(await cursor.fetchall())
            await cursor.close()
            for name, text in rows:
                try:
                    kept = json.dumps(renamed(json.loads(text), old, new), ensure_ascii=False)
                except ValueError:
                    continue
                await conn.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (kept, name))
