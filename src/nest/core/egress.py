"""联网 for the companions' containers, nest's side: the per-companion switch (on unless
turned off) and reading what the proxy (nest.core.egress_proxy) logged — hers, and what a plugin reached
against what it declared."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nest.core.workspace import folder_name
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

OFFLINE = "workspace_offline"  # setting: the bot ids whose containers have no network
SHOWN = 200


async def offline_bots(db: Database) -> set[str]:
    stored = (await SettingsRepo(db).all()).get(OFFLINE)
    return {str(b) for b in stored} if isinstance(stored, list) else set()


async def is_online(db: Database, bot_id: str) -> bool:
    return bot_id not in await offline_bots(db)


async def set_online(db: Database, bot_id: str, online: bool) -> None:
    offline = await offline_bots(db)
    offline = offline - {bot_id} if online else offline | {bot_id}
    await SettingsRepo(db).set_many({OFFLINE: sorted(offline)})


def _entries(folder: Path) -> list[dict[str, Any]]:
    """Every logged connection, oldest first (blocks: reads the log files)."""
    found: list[dict[str, Any]] = []
    for name in ("egress.log.1", "egress.log"):
        try:
            lines = (folder / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                found.append(entry)
    return found


def declared(host: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """Whether `host` is one a plugin said it reaches: "api.example.com", "*.example.com" (and below), "*"."""
    host = host.lower().rstrip(".")
    for pattern in patterns:
        if pattern == "*" or host == pattern:
            return True
        if pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])):
            return True
    return False


def plugin_reach(folder: Path, plugin_id: str, patterns: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """What a plugin's containers (nest-mcp-<her folder>-<plugin>, one per companion) reached, per host:
    how often, when last, how often refused — and which of those it never declared. Only connections logged
    with where they came from count (blocks: reads the log files)."""
    suffix = "-" + re.sub(r"[^a-z0-9.-]", "-", plugin_id.lower())
    hosts: dict[str, dict[str, Any]] = {}
    for entry in _entries(folder):
        sender = str(entry.get("from") or "")
        host = str(entry.get("host") or "").lower()
        if not (sender.startswith("nest-mcp-") and sender.endswith(suffix)) or not host:
            continue
        seen = hosts.setdefault(host, {"host": host, "times": 0, "refused": 0, "last": None})
        seen["times"] += 1
        seen["refused"] += entry.get("ok") is not True
        seen["last"] = entry.get("t")
    rows = sorted(hosts.values(), key=lambda h: (-h["times"], h["host"]))
    for row in rows:
        row["declared"] = declared(row["host"], patterns)
    return {
        "declared": list(patterns),
        "seen": rows,
        "undeclared": [row["host"] for row in rows if not row["declared"]],
    }


def recent(folder: Path, bot_id: str, limit: int = SHOWN) -> list[dict[str, Any]]:
    """Her latest connections, newest first (blocks: reads the log files)."""
    who = folder_name(bot_id)
    found: list[dict[str, Any]] = []
    for name in ("egress.log", "egress.log.1"):
        path = folder / name
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("who") == who:
                found.append(
                    {
                        "at": entry.get("t"),
                        "host": str(entry.get("host") or ""),
                        "port": entry.get("port"),
                        "ok": entry.get("ok") is True,
                        "why": str(entry.get("why") or ""),
                    }
                )
                if len(found) >= limit:
                    return found
    return found
