"""Bots, personas, model providers, and what bots decided (decisions, llm_calls)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nest.core.events import ChannelRef
from nest.storage.db import Database

ACTIVITIES = ("mentions", "occasional", "active")
STICKER_MODES = ("off", "trial", "on")
MAX_REPLY_LINES = 5  # 一次最多发 goes up to this
MODES = ("observe", "live", "paused")
SPOKEN = ("would_reply", "replied")  # outcomes that count as the bot saying something


class InUse(Exception):
    """A row cannot be deleted while these (bot names, or places) still use it."""

    def __init__(self, users: list[str]) -> None:
        super().__init__("、".join(users))
        self.users = users


class NameTaken(Exception):
    """Another row already has this name."""


class _Unset(Enum):
    UNSET = "unset"


UNSET = _Unset.UNSET  # "not given": lets an update tell "leave as it is" from "set to NULL"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    id: str
    kind: str
    base_url: str
    model: str
    key_ref: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BotChannel:
    channel_id: int
    channel: ChannelRef
    name: str
    enabled: bool
    activity: str
    persona_name: str
    persona: str
    cooldown_seconds: float
    persona_id: int | None = None
    sticker_mode: str = "off"  # send 表情包: "off" | "trial" (recorded, never sent) | "on"
    voice_mode: str = "off"  # 发语音: "off" | "trial" (recorded, the words go out as text) | "on"
    voice_rate: int = 0  # percent of the replies the model did not mark that are spoken anyway
    reply_lines: int = 3  # 一次最多发: messages one reply may come as, a sticker included
    split_rate: int = 100  # 分条频率: percent of the replies that may come as several messages
    use_slang: bool = False  # explain the group's 黑话 in the prompt
    answer_questions: bool = False  # 答题: a question to the bot gets a real answer, searched if it can
    answer_tone: str = "crisp"  # 答题的语气 (a tones key, nest.core.tones)
    chat_tone: str = "persona"  # 聊天的语气
    recorder: bool = False  # 问题记录官: this bot writes down the group's unsolved questions (one per group)


@dataclass(frozen=True, slots=True)
class BotConfig:
    id: str
    account_id: str
    self_id: str
    name: str
    aliases: tuple[str, ...]
    provider_id: str | None
    mode: str
    channels: dict[int, BotChannel]
    voice: str = ""  # 发语音: "<service>:<voice id>"
    backup_provider_id: str | None = None  # 备用模型: speaks when the bot's own model cannot


def _with_examples(persona: str, examples: str) -> str:
    """The persona as the model reads it. Showing how she talks holds a voice better than describing it."""
    if not examples.strip():
        return persona
    return f"{persona}\n\n说话的样子（只学语气和长短，不要照抄这些句子）：\n{examples.strip()}"


class BotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- providers -------------------------------------------------------------------------------

    async def upsert_provider(self, config: ProviderConfig) -> None:
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO providers (id, kind, base_url, model, key_ref, options, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, base_url = excluded.base_url,
                       model = excluded.model, key_ref = excluded.key_ref, options = excluded.options,
                       updated_at = excluded.updated_at""",
                (
                    config.id,
                    config.kind,
                    config.base_url,
                    config.model,
                    config.key_ref,
                    json.dumps(config.options, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    async def providers(self) -> list[ProviderConfig]:
        rows = await self.db.fetchall(
            "SELECT id, kind, base_url, model, key_ref, options FROM providers ORDER BY id"
        )
        return [ProviderConfig(**(dict(row) | {"options": json.loads(row["options"])})) for row in rows]

    async def get_provider(self, provider_id: str) -> ProviderConfig | None:
        row = await self.db.fetchone(
            "SELECT id, kind, base_url, model, key_ref, options FROM providers WHERE id = ?", (provider_id,)
        )
        return ProviderConfig(**(dict(row) | {"options": json.loads(row["options"])})) if row else None

    async def delete_provider(self, provider_id: str) -> bool:
        """Delete a model row; raises InUse when a bot uses it (checked in the same transaction, so never
        the foreign key's ON DELETE SET NULL). False when there was no such row."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT name FROM bots WHERE (provider_id = ? OR backup_provider_id = ?)"
                " AND deleted_at IS NULL ORDER BY created_at, id",
                (provider_id, provider_id),
            )
            names = [row["name"] for row in await cursor.fetchall()]
            await cursor.close()
            if names:
                raise InUse(names)
            cursor = await conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    async def provider_users(self) -> dict[str, list[dict[str, Any]]]:
        """Provider id -> the bots using it ({bot_id, name, mode}), from the bots table."""
        rows = await self.db.fetchall(
            "SELECT id, name, mode, provider_id, backup_provider_id FROM bots"
            " WHERE deleted_at IS NULL ORDER BY created_at, id"
        )
        users: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            for provider in {row["provider_id"], row["backup_provider_id"]} - {None}:
                users.setdefault(provider, []).append(
                    {"bot_id": row["id"], "name": row["name"], "mode": row["mode"]}
                )
        return users

    # -- personas --------------------------------------------------------------------------------

    async def upsert_persona(self, name: str, content: str) -> int:
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO personas (name, content, created_at, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT (name) DO UPDATE SET content = excluded.content,
                       updated_at = excluded.updated_at""",
                (name, content, now, now),
            )
        row = await self.db.fetchone("SELECT id FROM personas WHERE name = ?", (name,))
        assert row is not None
        return row["id"]

    async def personas(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT id, name, content, examples, updated_at FROM personas ORDER BY name, id"
        )
        return [dict(row) for row in rows]

    async def get_persona(self, persona_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT id, name, content, examples, updated_at FROM personas WHERE id = ?", (persona_id,)
        )
        return dict(row) if row else None

    async def persona_uses(self) -> dict[int, list[dict[str, Any]]]:
        """Persona id -> where it is assigned ({bot_id, bot_name, channel_id, channel_name})."""
        rows = await self.db.fetchall(PERSONA_USES, (None, None))
        uses: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            uses.setdefault(row["persona_id"], []).append(_persona_use(row))
        return uses

    async def create_persona(self, name: str, content: str) -> int:
        """Plain INSERT (never the CLI's upsert, which would overwrite a persona of the same name)."""
        now = time.time()
        async with self.db.transaction() as conn:
            await _name_free(conn, name, None)
            cursor = await conn.execute(
                "INSERT INTO personas (name, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, content, now, now),
            )
            persona_id = cursor.lastrowid
            await cursor.close()
        assert persona_id is not None
        return persona_id

    async def update_persona(
        self,
        persona_id: int,
        *,
        name: str | None = None,
        content: str | None = None,
        examples: str | None = None,
    ) -> bool:
        """Change the given fields with an UPDATE; raises NameTaken on a rename onto another persona's
        name. False when there is no such persona."""
        columns: dict[str, Any] = {}
        if name is not None:
            columns["name"] = name
        if content is not None:
            columns["content"] = content
        if examples is not None:
            columns["examples"] = examples
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            if name is not None:
                await _name_free(conn, name, persona_id)
            cursor = await conn.execute(
                f"UPDATE personas SET {assignments} WHERE id = ?", (*columns.values(), persona_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def delete_persona(self, persona_id: int) -> bool:
        """Delete a persona; raises InUse ("bot（group）" entries) while a group uses it, checked in the
        same transaction so the foreign key's ON DELETE SET NULL never silently unassigns it. False when
        there was no such persona."""
        async with self.db.transaction() as conn:
            cursor = await conn.execute(PERSONA_USES, (persona_id, persona_id))
            uses = [_persona_use(row) for row in await cursor.fetchall()]
            await cursor.close()
            if uses:
                raise InUse([f"{use['bot_name']}（{use['channel_name']}）" for use in uses])
            cursor = await conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted

    # -- bots ------------------------------------------------------------------------------------

    async def upsert_bot(
        self,
        *,
        account_id: str,
        name: str,
        aliases: list[str],
        provider_id: str | None,
        mode: str = "observe",
    ) -> str:
        if mode not in MODES:
            raise ValueError(f"未知模式：{mode}")
        now = time.time()
        async with self.db.transaction() as conn:
            await conn.execute(
                """INSERT INTO bots (id, account_id, name, aliases, provider_id, mode, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET name = excluded.name, aliases = excluded.aliases,
                       provider_id = excluded.provider_id, updated_at = excluded.updated_at,
                       deleted_at = NULL""",
                (
                    account_id,
                    account_id,
                    name,
                    json.dumps(aliases, ensure_ascii=False),
                    provider_id,
                    mode,
                    now,
                    now,
                ),
            )
        return account_id

    async def remove_bot(self, bot_id: str) -> bool:
        """Removed in the console: hidden, paused, its model let go and its account disabled, but every
        record kept (upsert_bot with the same account brings it back). False when there is no such bot."""
        now = time.time()
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE bots SET deleted_at = ?, mode = 'paused', provider_id = NULL, "
                "backup_provider_id = NULL, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, now, bot_id),
            )
            removed = cursor.rowcount > 0
            if removed:
                await conn.execute(
                    "UPDATE accounts SET enabled = 0 WHERE id = (SELECT account_id FROM bots WHERE id = ?)",
                    (bot_id,),
                )
        return removed

    async def removed_bot(self, account_id: str) -> dict[str, Any] | None:
        """A removed bot of this account (to bring back), or None."""
        row = await self.db.fetchone(
            "SELECT id, name, deleted_at FROM bots WHERE account_id = ? AND deleted_at IS NOT NULL",
            (account_id,),
        )
        return dict(row) if row else None

    async def set_mode(self, bot_id: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"未知模式：{mode}")
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE bots SET mode = ?, updated_at = ? WHERE id = ?", (mode, time.time(), bot_id)
            )

    async def update_bot(
        self,
        bot_id: str,
        *,
        name: str | None = None,
        aliases: list[str] | None = None,
        provider_id: str | _Unset | None = UNSET,
        backup_provider_id: str | _Unset | None = UNSET,
        mode: str | None = None,
        voice: str | None = None,
    ) -> bool:
        """Change only the given fields (provider_id=None clears the model). A plain UPDATE: an INSERT OR
        REPLACE would delete the row and fire the ON DELETE actions. False when there is no such bot."""
        if mode is not None and mode not in MODES:
            raise ValueError(f"未知模式：{mode}")
        columns: dict[str, Any] = {}
        if name is not None:
            columns["name"] = name
        if aliases is not None:
            columns["aliases"] = json.dumps(aliases, ensure_ascii=False)
        if provider_id is not UNSET:
            columns["provider_id"] = provider_id
        if backup_provider_id is not UNSET:
            columns["backup_provider_id"] = backup_provider_id
        if mode is not None:
            columns["mode"] = mode
        if voice is not None:
            columns["voice"] = voice
        columns["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in columns)
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                f"UPDATE bots SET {assignments} WHERE id = ?", (*columns.values(), bot_id)
            )
            updated = cursor.rowcount > 0
            await cursor.close()
        return updated

    async def set_channel(
        self,
        bot_id: str,
        channel_id: int,
        *,
        enabled: bool | None = None,
        activity: str | None = None,
        persona_id: int | _Unset | None = UNSET,
        cooldown_seconds: float | None = None,
        sticker_mode: str | None = None,
        voice_mode: str | None = None,
        voice_rate: int | None = None,
        reply_lines: int | None = None,
        split_rate: int | None = None,
        use_slang: bool | None = None,
        answer_questions: bool | None = None,
        answer_tone: str | None = None,
        chat_tone: str | None = None,
        recorder: bool | None = None,
    ) -> None:
        """None (UNSET for persona_id) leaves a setting as it is; persona_id=None removes the persona.
        recorder=True takes 问题记录官 away from any other bot in the same group."""
        if activity is not None and activity not in ACTIVITIES:
            raise ValueError(f"未知活跃度：{activity}")
        if sticker_mode is not None and sticker_mode not in STICKER_MODES:
            raise ValueError(f"未知的发表情设置：{sticker_mode}")
        if voice_mode is not None and voice_mode not in STICKER_MODES:
            raise ValueError(f"未知的发语音设置：{voice_mode}")
        columns: dict[str, Any] = {}
        if enabled is not None:
            columns["enabled"] = int(enabled)
        if activity is not None:
            columns["activity"] = activity
        if persona_id is not UNSET:
            columns["persona_id"] = persona_id
        if cooldown_seconds is not None:
            columns["cooldown_seconds"] = cooldown_seconds
        if sticker_mode is not None:
            columns["sticker_mode"] = sticker_mode
        if voice_mode is not None:
            columns["voice_mode"] = voice_mode
        if voice_rate is not None:
            columns["voice_rate"] = voice_rate
        if reply_lines is not None:
            columns["reply_lines"] = reply_lines
        if split_rate is not None:
            columns["split_rate"] = split_rate
        if use_slang is not None:
            columns["use_slang"] = int(use_slang)
        if answer_questions is not None:
            columns["answer_questions"] = int(answer_questions)
        if answer_tone is not None:
            columns["answer_tone"] = answer_tone
        if chat_tone is not None:
            columns["chat_tone"] = chat_tone
        if recorder is not None:
            columns["recorder"] = int(recorder)
        async with self.db.transaction() as conn:
            if recorder:
                await conn.execute(
                    """UPDATE bot_channels SET recorder = 0
                       WHERE NOT (bot_id = ? AND channel_id = ?) AND channel_id IN (
                           SELECT c.id FROM channels c JOIN channels mine ON mine.id = ?
                           WHERE c.kind = 'group' AND c.external_id = mine.external_id)""",
                    (bot_id, channel_id, channel_id),
                )
            await conn.execute(
                "INSERT OR IGNORE INTO bot_channels (bot_id, channel_id, enabled) VALUES (?, ?, 0)",
                (bot_id, channel_id),
            )
            if columns:
                assignments = ", ".join(f"{column} = ?" for column in columns)
                await conn.execute(
                    f"UPDATE bot_channels SET {assignments} WHERE bot_id = ? AND channel_id = ?",
                    (*columns.values(), bot_id, channel_id),
                )

    async def bots(self) -> list[BotConfig]:
        rows = await self.db.fetchall(
            """SELECT b.id, b.account_id, a.self_id, a.platform, b.name, b.aliases, b.provider_id, b.mode,
                      b.voice, b.backup_provider_id
               FROM bots b JOIN accounts a ON a.id = b.account_id WHERE b.deleted_at IS NULL
               ORDER BY b.created_at, b.id"""
        )
        channel_rows = await self.db.fetchall(
            """SELECT bc.bot_id, bc.channel_id, bc.enabled, bc.activity, bc.cooldown_seconds, bc.persona_id,
                      coalesce(p.examples, '') AS examples,
                      bc.sticker_mode, bc.voice_mode, bc.voice_rate, bc.reply_lines, bc.split_rate,
                      bc.use_slang, bc.answer_questions, bc.answer_tone, bc.chat_tone, bc.recorder,
                      c.kind,
                      c.external_id, c.name,
                      coalesce(p.name, '') AS persona_name,
                      coalesce(p.content, '') AS persona
               FROM bot_channels bc JOIN channels c ON c.id = bc.channel_id
               LEFT JOIN personas p ON p.id = bc.persona_id"""
        )
        configs = []
        for row in rows:
            channels = {
                ch["channel_id"]: BotChannel(
                    channel_id=ch["channel_id"],
                    channel=ChannelRef(row["platform"], row["self_id"], ch["kind"], ch["external_id"]),
                    name=ch["name"],
                    enabled=bool(ch["enabled"]),
                    activity=ch["activity"],
                    persona_name=ch["persona_name"],
                    persona=_with_examples(ch["persona"], ch["examples"]),
                    cooldown_seconds=ch["cooldown_seconds"],
                    persona_id=ch["persona_id"],
                    sticker_mode=ch["sticker_mode"],
                    voice_mode=ch["voice_mode"],
                    voice_rate=ch["voice_rate"],
                    reply_lines=ch["reply_lines"],
                    split_rate=ch["split_rate"],
                    use_slang=bool(ch["use_slang"]),
                    answer_questions=bool(ch["answer_questions"]),
                    answer_tone=ch["answer_tone"],
                    chat_tone=ch["chat_tone"],
                    recorder=bool(ch["recorder"]),
                )
                for ch in channel_rows
                if ch["bot_id"] == row["id"]
            }
            configs.append(
                BotConfig(
                    id=row["id"],
                    account_id=row["account_id"],
                    self_id=row["self_id"],
                    name=row["name"],
                    aliases=tuple(json.loads(row["aliases"])),
                    provider_id=row["provider_id"],
                    mode=row["mode"],
                    channels=channels,
                    voice=row["voice"],
                    backup_provider_id=row["backup_provider_id"],
                )
            )
        return configs

    # -- what happened -----------------------------------------------------------------------------

    async def record_llm_call(
        self,
        *,
        bot_id: str | None,
        provider_id: str,
        model: str,
        started_at: float,
        latency_ms: float | None,
        status: str,
        error: str | None = None,
        prompt_chars: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        output: str | None = None,
    ) -> int:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO llm_calls (bot_id, provider_id, model, started_at, latency_ms, status, error,
                       prompt_chars, prompt_tokens, completion_tokens, output)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    bot_id,
                    provider_id,
                    model,
                    started_at,
                    latency_ms,
                    status,
                    error,
                    prompt_chars,
                    prompt_tokens,
                    completion_tokens,
                    (output or "")[:2000] or None,
                ),
            )
            call_id = cursor.lastrowid
            await cursor.close()
        assert call_id is not None
        return call_id

    async def record_decision(
        self,
        *,
        bot_id: str,
        channel_id: int,
        trigger_id: int | None,
        trigger: str,
        outcome: str,
        reason: str = "",
        reply: str | None = None,
        quote: bool = False,
        regenerated: bool = False,
        llm_call_id: int | None = None,
        reply_message_id: str | None = None,
        sticker_id: int | None = None,
        sticker_state: str | None = None,
        slang_terms: list[str] | None = None,
        voice_state: str | None = None,
    ) -> dict[str, Any]:
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                """INSERT INTO decisions (bot_id, channel_id, trigger_id, trigger, outcome, reason, reply,
                       quote, regenerated, llm_call_id, reply_message_id, sticker_id, sticker_state,
                       slang_terms, voice_state, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
                (
                    bot_id,
                    channel_id,
                    trigger_id,
                    trigger,
                    outcome,
                    reason,
                    reply,
                    int(quote),
                    int(regenerated),
                    llm_call_id,
                    reply_message_id,
                    sticker_id,
                    sticker_state,
                    json.dumps(slang_terms, ensure_ascii=False) if slang_terms else None,
                    voice_state,
                    time.time(),
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None
        return _decision(row)

    async def decisions(
        self, channel_ids: list[int], *, limit: int = 100, before: float | None = None
    ) -> list[dict[str, Any]]:
        if not channel_ids:
            return []
        marks = ",".join("?" * len(channel_ids))
        rows = await self.db.fetchall(
            f"""SELECT d.*, m.sender_name AS trigger_sender, m.text AS trigger_text,
                       m.sent_at AS trigger_sent_at
                FROM decisions d LEFT JOIN messages m ON m.id = d.trigger_id
                WHERE d.channel_id IN ({marks}) AND (? IS NULL OR d.created_at < ?)
                ORDER BY d.created_at DESC, d.id DESC LIMIT ?""",
            (*channel_ids, before, before, limit),
        )
        return [_decision(row) for row in reversed(rows)]

    async def last_lines(self) -> dict[str, dict[str, Any]]:
        """Each bot's latest line, sent or only what it would have said, however long ago."""
        rows = await self.db.fetchall(
            """SELECT d.*, m.sender_name AS trigger_sender, m.text AS trigger_text,
                      m.sent_at AS trigger_sent_at
               FROM decisions d LEFT JOIN messages m ON m.id = d.trigger_id
               WHERE d.id IN (SELECT max(id) FROM decisions
                              WHERE outcome IN ('replied', 'would_reply') AND reply IS NOT NULL
                              GROUP BY bot_id)"""
        )
        return {row["bot_id"]: _decision(row) for row in rows}

    async def spoken_since(self, since: float, bot_id: str | None = None) -> int:
        marks = ",".join("?" * len(SPOKEN))
        row = await self.db.fetchone(
            f"""SELECT count(*) FROM decisions WHERE outcome IN ({marks}) AND created_at >= ?
                AND (? IS NULL OR bot_id = ?)""",
            (*SPOKEN, since, bot_id, bot_id),
        )
        return int(row[0]) if row else 0

    async def voiced(self, bot_id: str, channel_id: int, message_ids: list[str]) -> dict[str, str]:
        """What the bot said in these voice messages of its own, by message id."""
        marks = ",".join("?" * len(message_ids))
        rows = await self.db.fetchall(
            f"""SELECT reply_message_id, reply FROM decisions
                WHERE bot_id = ? AND channel_id = ? AND voice_state = 'sent'
                  AND reply_message_id IN ({marks})""",
            (bot_id, channel_id, *message_ids),
        )
        return {row["reply_message_id"]: row["reply"] for row in rows}

    async def decided_since(self, bot_id: str, channel_id: int, since: float) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM decisions WHERE bot_id = ? AND channel_id = ? AND created_at >= ? LIMIT 1",
            (bot_id, channel_id, since),
        )
        return row is not None

    async def outcome_counts(self, since: float) -> dict[str, dict[str, int]]:
        rows = await self.db.fetchall(
            "SELECT bot_id, outcome, count(*) AS n FROM decisions WHERE created_at >= ?"
            " GROUP BY bot_id, outcome",
            (since,),
        )
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["bot_id"], {})[row["outcome"]] = row["n"]
        return counts

    async def purge(self, *, decisions_before: float, calls_before: float) -> int:
        async with self.db.transaction() as conn:
            first = await conn.execute("DELETE FROM decisions WHERE created_at < ?", (decisions_before,))
            second = await conn.execute("DELETE FROM llm_calls WHERE started_at < ?", (calls_before,))
            deleted = first.rowcount + second.rowcount
            await first.close()
            await second.close()
        return deleted


# Where personas are assigned (enabled or not); the two parameters narrow it to one persona (NULL, NULL:
# all of them). A group whose name is not known yet is shown as 群 <number>.
PERSONA_USES = """SELECT bc.persona_id, bc.bot_id, b.name AS bot_name, bc.channel_id,
                         coalesce(nullif(c.name, ''), '群 ' || c.external_id) AS channel_name
                  FROM bot_channels bc JOIN bots b ON b.id = bc.bot_id JOIN channels c ON c.id = bc.channel_id
                  WHERE bc.persona_id IS NOT NULL AND (? IS NULL OR bc.persona_id = ?)
                        AND b.deleted_at IS NULL
                  ORDER BY b.created_at, b.id, channel_name, bc.channel_id"""


def _persona_use(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "bot_id": row["bot_id"],
        "bot_name": row["bot_name"],
        "channel_id": row["channel_id"],
        "channel_name": row["channel_name"],
    }


async def _name_free(conn: Any, name: str, persona_id: int | None) -> None:
    cursor = await conn.execute(
        "SELECT 1 FROM personas WHERE name = ? AND (? IS NULL OR id != ?)", (name, persona_id, persona_id)
    )
    taken = await cursor.fetchone()
    await cursor.close()
    if taken:
        raise NameTaken(name)


def _decision(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    data = dict(row)
    data["quote"] = bool(data["quote"])
    data["regenerated"] = bool(data["regenerated"])
    data["slang_terms"] = json.loads(data["slang_terms"]) if data.get("slang_terms") else []
    return data
