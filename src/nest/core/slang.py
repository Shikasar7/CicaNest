"""黑话: learn what a group's own words mean, and explain the ones that come up when a
bot answers there.

Learning is per real group (several accounts in one QQ group share one glossary) and runs in the
background: at most every `min_hours`, once `min_new` messages from people arrived since the last run.
One model call per run; every term it proposes is checked against the messages it read (it must occur at
least twice), so a term the model made up never gets in. A term edited or added by hand keeps its meaning.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from nest.adapters.registry import can, place_of
from nest.core.broadcast import Broadcaster
from nest.llm.base import ChatMessage, ProviderError
from nest.llm.runtime import ProviderRegistry, ProviderUnavailable
from nest.storage.db import Database
from nest.storage.messages import MessageStore
from nest.storage.settings import SettingsRepo
from nest.storage.slang import GroupFeatureStore, SlangStore, split_group_key

log = logging.getLogger(__name__)

HOUR = 3600.0
DAY = 86400.0
SETTINGS_KEY = "slang"
TERM_MAX = 12
MEANING_MAX = 60
LINE_MAX = 120

LEARN_PROMPT = (
    "你在帮一个 {place} bot 学习群里的黑话。下面是这个群最近的聊天。"
    "找出反复出现、外人看不懂的词、梗、外号或群里的约定说法，结合上下文推断它们的意思。\n"
    "不要：常见网络用语（比如 绝绝子、yyds、破防、666）、普通词语、只出现一次的词、没有特别含义的人名。\n"
    "{known}"
    "只输出 JSON，不要别的文字："
    '{{"terms": [{{"term": "词", "meaning": "意思，不超过40字", "example": "聊天里的一句原话", '
    '"confidence": "high 或 medium 或 low"}}]}}，最多 10 条；没有就输出 {{"terms": []}}。'
)


@dataclass(frozen=True)
class SlangConfig:
    provider_id: str | None = None  # None: the model of the first bot that has one
    min_new: int = 60
    min_hours: float = 12.0
    max_messages: int = 400
    window_days: float = 7.0
    max_terms: int = 200
    stale_days: float = 90.0
    prompt_terms: int = 8
    forced_min: int = 10  # 「立即学习」 still needs this many messages


def typed(row: dict[str, Any]) -> str:
    """What a person typed: text segments only (no @-names, quotes or picture placeholders)."""
    segments = row.get("segments") or []
    if not segments:
        return str(row.get("text") or "")
    return "".join(str((s.get("data") or {}).get("text", "")) for s in segments if s.get("type") == "text")


def parse_terms(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("没有 JSON")
    data = json.loads(cleaned[start : end + 1])
    terms = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(terms, list):
        raise ValueError("没有 terms")
    return [t for t in terms if isinstance(t, dict)]


def check_term(item: dict[str, Any], lines: list[str]) -> tuple[dict[str, Any] | None, str]:
    """(the term, cleaned up, with its count and a real example) or (None, why it was dropped)."""
    term = " ".join(str(item.get("term") or "").split())
    meaning = " ".join(str(item.get("meaning") or "").split())[:MEANING_MAX]
    if not term or len(term) > TERM_MAX:
        return None, "词太长或是空的"
    if not meaning:
        return None, f"「{term}」没有解释"
    if str(item.get("confidence") or "").strip().lower() == "low":
        return None, f"「{term}」模型没把握"
    hits = sum(line.count(term) for line in lines)
    if hits < 2:
        return None, f"「{term}」在聊天里出现不到 2 次"
    example = " ".join(str(item.get("example") or "").split())
    if not example or term not in example or not any(example in line for line in lines):
        example = next(line for line in lines if term in line)  # the model's example must be a real line
    return {"term": term, "meaning": meaning, "example": example[:LINE_MAX], "hits": hits}, ""


def pick_terms(entries: Iterable[tuple[str, str]], text: str, limit: int) -> list[tuple[str, str]]:
    """The entries whose term occurs in `text`, longer terms first, at most `limit`."""
    found = [(term, meaning) for term, meaning in entries if term and term in text]
    found.sort(key=lambda entry: -len(entry[0]))
    return found[:limit]


class SlangLearner:
    def __init__(
        self,
        db: Database,
        store: MessageStore,
        providers: ProviderRegistry,
        broadcaster: Broadcaster,
        *,
        default_provider: Callable[[], str | None] = lambda: None,
        our_accounts: set[str] | None = None,
        check_interval: float = HOUR,
        first_delay: float = 300.0,
    ) -> None:
        self.db = db
        self.messages = store
        self.slang = SlangStore(db)
        self.groups = GroupFeatureStore(db)
        self.settings = SettingsRepo(db)
        self.providers = providers
        self.broadcaster = broadcaster
        self._default_provider = default_provider
        self.our_accounts = our_accounts if our_accounts is not None else set()
        self.config = SlangConfig()
        self._check_interval = check_interval
        self._first_delay = first_delay
        self._cache: dict[str, list[tuple[str, str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle ----------------------------------------------------------------------------------

    async def start(self) -> None:
        self.config = await self.load_config()
        self._task = asyncio.create_task(self._loop(), name="slang")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def load_config(self) -> SlangConfig:
        stored = (await self.settings.all()).get(SETTINGS_KEY)
        provider = stored.get("provider_id") if isinstance(stored, dict) else None
        return replace(
            SlangConfig(), provider_id=provider if isinstance(provider, str) and provider else None
        )

    async def save_config(self, provider_id: str | None) -> SlangConfig:
        await self.settings.set_many({SETTINGS_KEY: {"provider_id": provider_id}})
        self.config = replace(self.config, provider_id=provider_id)
        return self.config

    def provider_id(self) -> str | None:
        return self.config.provider_id or self._default_provider()

    def changed(self, group_key: str) -> None:
        """The group's terms changed (learning or the console): reload them for the next prompt."""
        self._cache.pop(group_key, None)
        self.broadcaster.publish("slang", {"group_key": group_key})

    async def _loop(self) -> None:
        await asyncio.sleep(self._first_delay)
        while True:
            try:
                for group_key in await self.due():
                    await self.learn(group_key)
            except Exception:
                log.exception("学习黑话出错")
            await asyncio.sleep(self._check_interval)

    # -- using --------------------------------------------------------------------------------------

    async def glossary(self, group_key: str, texts: Iterable[str]) -> list[tuple[str, str]]:
        """The group's terms that occur in `texts` (the prompt's messages), for the prompt."""
        entries = self._cache.get(group_key)
        if entries is None:
            entries = [(e["term"], e["meaning"]) for e in await self.slang.entries(group_key)]
            self._cache[group_key] = entries
        return pick_terms(entries, "\n".join(texts), self.config.prompt_terms)

    # -- learning -----------------------------------------------------------------------------------

    async def _viewer(self, group_key: str) -> int | None:
        """The channel to read: of our accounts in that group, the one that stored the most messages."""
        try:
            platform, kind, external_id = split_group_key(group_key)
        except ValueError:
            return None
        members = [
            c
            for c in await self.messages.channels()
            if c["kind"] == kind
            and c["external_id"] == external_id
            and c["account_id"].startswith(f"{platform}:")
        ]
        return max(members, key=lambda c: c["message_count"])["id"] if members else None

    def _since(self, learned_until: float | None, now: float) -> float:
        return max(learned_until or 0.0, now - self.config.window_days * DAY)

    async def due(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        due = []
        for key, features in (await self.groups.all()).items():
            if not features.learn_slang or not can(key, "sees_all"):
                continue
            if features.slang_learned_at and now - features.slang_learned_at < self.config.min_hours * HOUR:
                continue
            channel = await self._viewer(key)
            if channel is None:
                continue
            since = self._since(features.slang_learned_until, now)
            if await self.messages.count_from_members(channel, since=since, exclude=self.our_accounts) >= (
                self.config.min_new
            ):
                due.append(key)
        return due

    async def learn(self, group_key: str, *, force: bool = False) -> dict[str, Any]:
        """One learning run for a group; returns its slang_runs row. Never two at once for one group."""
        lock = self._locks.setdefault(group_key, asyncio.Lock())
        async with lock:
            run = await self._learn(group_key, force=force)
        self.changed(group_key)
        return run

    async def _learn(self, group_key: str, *, force: bool) -> dict[str, Any]:
        started = time.time()
        features = await self.groups.get(group_key)
        channel = await self._viewer(group_key)
        if channel is None:
            return await self.slang.record_run(
                group_key, started_at=started, messages=0, detail="还没有这个群的消息"
            )
        rows = await self.messages.from_members(
            channel,
            since=self._since(features.slang_learned_until, started),
            exclude=self.our_accounts,
            limit=self.config.max_messages,
        )
        said = [(row, " ".join(typed(row).split())[:LINE_MAX]) for row in rows]
        said = [(row, text) for row, text in said if text]
        lines = [text for _, text in said]
        needed = self.config.forced_min if force else self.config.min_new
        if len(lines) < needed:
            detail = f"新消息太少（{len(lines)} 条，至少要 {needed} 条），这次没有学"
            return await self.slang.record_run(
                group_key, started_at=started, messages=len(lines), detail=detail
            )
        runtime = self.providers.get(self.provider_id())
        if runtime is None:
            return await self.slang.record_run(
                group_key,
                started_at=started,
                messages=len(lines),
                error="还没有选学习用的模型，或者它没有加载",
            )
        existing = {e["term"]: e for e in await self.slang.entries(group_key)}
        known = ""
        if existing:
            present = [
                f"{t}：{e['meaning']}" for t, e in existing.items() if any(t in line for line in lines)
            ]
            known = "已经记下的词：" + "、".join(existing) + "。\n"
            if present:
                known += (
                    "其中这次聊天里出现的词和现在的解释（看出更准确的意思可以再给出）：\n"
                    + "\n".join(present)
                    + "\n"
                )
        chat = "\n".join(f"{row['sender_name']}：{text}" for row, text in said)
        place = place_of(split_group_key(group_key)[0])[0]
        messages = [
            ChatMessage("system", LEARN_PROMPT.format(known=known, place=place)),
            ChatMessage("user", chat),
        ]
        try:
            completion, _ = await runtime.complete(messages, bot_id=None, max_tokens=1500)
            proposed = parse_terms(completion.text)
        except (ProviderError, ProviderUnavailable, ValueError) as exc:
            # Not again within min_hours, but the same messages are read next time.
            await self.groups.learned(group_key, started, None)
            error = "模型的回答看不懂" if isinstance(exc, ValueError) else f"模型调用失败：{str(exc)[:160]}"
            return await self.slang.record_run(
                group_key, started_at=started, messages=len(lines), error=error
            )

        added = updated = 0
        reasons: list[str] = []
        touched: set[str] = set()
        now = time.time()
        for item in proposed[:10]:
            checked, why = check_term(item, lines)
            if checked is None:
                reasons.append(why)
                continue
            term = checked["term"]
            if term in touched:
                continue
            touched.add(term)
            entry = existing.get(term)
            if entry is None:
                await self.slang.add(
                    group_key,
                    term,
                    checked["meaning"],
                    example=checked["example"],
                    hits=checked["hits"],
                    at=now,
                )
                added += 1
            else:
                auto = entry["source"] == "auto"
                await self.slang.learned(
                    entry["id"],
                    meaning=checked["meaning"] if auto else None,
                    example=checked["example"] if auto else None,
                    hits=checked["hits"],
                    at=now,
                )
                updated += 1
        for term, entry in existing.items():  # known terms that came up again: still alive
            if term not in touched and (hits := sum(line.count(term) for line in lines)):
                await self.slang.learned(entry["id"], meaning=None, example=None, hits=hits, at=now)
        trimmed = await self.slang.trim(group_key, self.config.max_terms)
        await self.groups.learned(group_key, now, max(row["sent_at"] for row in rows))
        parts = [f"读了 {len(lines)} 条消息", f"新增 {added} 条", f"更新 {updated} 条"]
        if reasons:
            parts.append(f"丢掉 {len(reasons)} 条（{'；'.join(reasons[:5])}）")
        if trimmed:
            parts.append(f"词条太多，删掉了 {trimmed} 条最久没出现的")
        return await self.slang.record_run(
            group_key,
            started_at=started,
            messages=len(lines),
            added=added,
            updated=updated,
            dropped=len(reasons),
            detail="，".join(parts),
        )

    async def tidy(self, now: float, history_days: float) -> int:
        """The daily cleanup: learned terms not seen for `stale_days`, and runs older than the history."""
        terms = await self.slang.purge_stale(now - self.config.stale_days * DAY)
        runs = await self.slang.purge_runs(now - history_days * DAY)
        if terms:
            log.info("删掉了 %d 条 %g 天没再出现的黑话", terms, self.config.stale_days)
            self._cache.clear()
        return terms + runs
