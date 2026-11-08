"""Configuration commands: model providers and bots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nest.core.events import ChannelRef
from nest.llm.runtime import KINDS
from nest.secrets import SecretStore
from nest.storage.accounts import account_id, get_account
from nest.storage.bots import ACTIVITIES, BotRepo, ProviderConfig
from nest.storage.db import Database
from nest.storage.messages import MessageStore


def read_credential(spec: str) -> str:
    """'C:\\path\\file.yaml:NAME' → value of a `NAME: value` line (the format DeepSeek Harness uses)."""
    path, _, name = spec.rpartition(":")
    if not path or not name:
        raise ValueError("--key-from 的格式是 文件路径:名字")
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(name + ":"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if value:
                return value
    raise ValueError(f"{path} 里没有 {name}")


def parse_option(text: str) -> tuple[str, Any]:
    key, sep, raw = text.partition("=")
    if not sep:
        raise ValueError(f"选项要写成 名字=值：{text}")
    try:
        value: Any = json.loads(raw)
    except ValueError:
        value = raw
    return key, value


async def add_provider(
    db: Database,
    secrets: SecretStore,
    *,
    provider_id: str,
    kind: str,
    base_url: str,
    model: str,
    key_ref: str = "",
    key_from: str = "",
    options: list[str] | None = None,
) -> ProviderConfig:
    if kind not in KINDS:
        raise ValueError(f"未知的模型接口类型：{kind}（可选 {', '.join(KINDS)}）")
    if key_from:
        key_ref = key_ref or f"NEST_KEY_{provider_id.upper().replace('-', '_')}"
        secrets.set(key_ref, read_credential(key_from))
    config = ProviderConfig(
        provider_id, kind, base_url, model, key_ref, dict(map(parse_option, options or []))
    )
    await BotRepo(db).upsert_provider(config)
    return config


async def add_bot(
    db: Database,
    *,
    uin: str,
    provider_id: str,
    name: str = "",
    aliases: list[str] | None = None,
    groups: list[str] | None = None,
    activity: str = "occasional",
    mode: str | None = None,
) -> list[str]:
    """Create/update the bot of QQ account `uin` and enable it in `groups`. Returns notes for the user.

    `mode` (observe / paused) is applied when given; otherwise a new bot starts in observe mode and an
    existing bot keeps its mode.
    """
    if activity not in ACTIVITIES:
        raise ValueError(f"未知活跃度：{activity}（可选 {', '.join(ACTIVITIES)}）")
    if mode not in (None, "observe", "paused"):
        raise ValueError("模式只能是 observe（只看不说）或 paused（暂停）；正式发言在 M3 开放")
    account = await get_account(db, account_id("qq", uin))
    if account is None:
        raise ValueError(f"还没有 QQ {uin} 这个账号，先在控制台增加它")
    repo = BotRepo(db)
    if provider_id not in {p.id for p in await repo.providers()}:
        raise ValueError(f"没有模型 {provider_id}，先运行 nest provider add")
    bot_name = name or account.name or uin
    bot_id = await repo.upsert_bot(
        account_id=account.id, name=bot_name, aliases=aliases or [bot_name], provider_id=provider_id
    )
    if mode is not None:
        await repo.set_mode(bot_id, mode)
    store = MessageStore(db)
    notes = [f"模式：{'暂停' if await _mode(repo, bot_id) == 'paused' else '只看不说'}"]
    for group in groups or []:
        channel = await store.channel_id(ChannelRef("qq", uin, "group", group))
        await repo.set_channel(bot_id, channel, enabled=True, activity=activity)
        notes.append(f"在群 {group} 启用（{activity}）")
    return notes


async def _mode(repo: BotRepo, bot_id: str) -> str:
    return next(bot.mode for bot in await repo.bots() if bot.id == bot_id)
