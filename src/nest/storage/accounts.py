"""Account rows: which platform identities nest operates and how to reach them."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nest.storage.db import Database


def account_id(platform: str, self_id: str) -> str:
    return f"{platform}:{self_id}"


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    platform: str
    self_id: str
    name: str
    http_url: str
    ws_url: str
    http_token_ref: str
    ws_token_ref: str
    enabled: bool
    # Plain settings; "refs": field name -> name of the secret in .env. QQ's are in the columns above.
    config: Mapping[str, Any] = field(default_factory=dict, hash=False)

    @property
    def refs(self) -> dict[str, str]:
        return dict(self.config.get("refs") or {})


COLUMNS = "id, platform, self_id, name, http_url, ws_url, http_token_ref, ws_token_ref, enabled, config"


def _account(row) -> Account:  # type: ignore[no-untyped-def]
    return Account(**(dict(row) | {"enabled": bool(row["enabled"]), "config": json.loads(row["config"])}))


async def upsert_account(
    db: Database,
    *,
    platform: str,
    self_id: str,
    name: str,
    http_url: str,
    ws_url: str,
    http_token_ref: str = "",
    ws_token_ref: str = "",
    enabled: bool = True,
    config: Mapping[str, Any] | None = None,
) -> Account:
    now = time.time()
    key = account_id(platform, self_id)
    async with db.transaction() as conn:
        await conn.execute(
            """INSERT INTO accounts (id, platform, self_id, name, http_url, ws_url, http_token_ref,
                                     ws_token_ref, enabled, config, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET
                   name = excluded.name, http_url = excluded.http_url, ws_url = excluded.ws_url,
                   http_token_ref = excluded.http_token_ref, ws_token_ref = excluded.ws_token_ref,
                   enabled = excluded.enabled, config = excluded.config, updated_at = excluded.updated_at""",
            (
                key,
                platform,
                self_id,
                name,
                http_url,
                ws_url,
                http_token_ref,
                ws_token_ref,
                int(enabled),
                json.dumps(dict(config or {}), ensure_ascii=False),
                now,
                now,
            ),
        )
    found = await get_account(db, key)
    assert found is not None
    return found


async def update_connection(
    db: Database, key: str, *, http_url: str, ws_url: str, http_token_ref: str, ws_token_ref: str
) -> None:
    """Where an account is reached (the tokens themselves live in .env under the refs)."""
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE accounts SET http_url = ?, ws_url = ?, http_token_ref = ?, ws_token_ref = ?,
                   updated_at = ? WHERE id = ?""",
            (http_url, ws_url, http_token_ref, ws_token_ref, time.time(), key),
        )


async def update_config(db: Database, key: str, config: Mapping[str, Any]) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE accounts SET config = ?, updated_at = ? WHERE id = ?",
            (json.dumps(dict(config), ensure_ascii=False), time.time(), key),
        )


async def get_account(db: Database, key: str) -> Account | None:
    row = await db.fetchone(f"SELECT {COLUMNS} FROM accounts WHERE id = ?", (key,))
    return _account(row) if row else None


async def list_accounts(db: Database, *, enabled_only: bool = False) -> list[Account]:
    where = "WHERE enabled = 1" if enabled_only else ""
    rows = await db.fetchall(f"SELECT {COLUMNS} FROM accounts {where} ORDER BY created_at, id")
    return [_account(row) for row in rows]
