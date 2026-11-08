"""Usage numbers for the console: model calls (llm_calls) and bot decisions since a point in time.

Every llm_calls row is one attempt: a retry is a row of its own, and a call refused by an open breaker
writes none (it shows up as a `failed` decision instead). Calls without a bot_id are 测试一下 calls.
"""

from __future__ import annotations

from typing import Any

from nest.storage.db import Database

OUTCOMES = ("replied", "would_reply", "skipped", "suppressed", "failed")
RECENT_ERRORS = 20


class UsageRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def by_provider(self, since: float) -> dict[str, dict[str, Any]]:
        """Provider id -> calls, ok, errors, avg_latency_ms (ok rows), tokens (NULL as 0), token_calls."""
        rows = await self.db.fetchall(
            """SELECT provider_id, count(*) AS calls, sum(status = 'ok') AS ok,
                      avg(CASE WHEN status = 'ok' THEN latency_ms END) AS avg_latency_ms,
                      coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                      coalesce(sum(completion_tokens), 0) AS completion_tokens,
                      sum(status = 'ok' AND (prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL))
                          AS token_calls
               FROM llm_calls WHERE started_at >= ? GROUP BY provider_id""",
            (since,),
        )
        return {row["provider_id"]: dict(row) | {"errors": row["calls"] - row["ok"]} for row in rows}

    async def p95_latency(self, since: float) -> dict[str, float]:
        """Provider id -> nearest-rank 95th percentile of ok latencies: sorted[ceil(0.95·n) − 1]."""
        rows = await self.db.fetchall(
            # (95n + 99) / 100 is ceil(0.95·n) in integer arithmetic; rank counts from 1.
            """SELECT provider_id, latency_ms FROM (
                   SELECT provider_id, latency_ms,
                          row_number() OVER (PARTITION BY provider_id ORDER BY latency_ms) AS rank,
                          count(*) OVER (PARTITION BY provider_id) AS n
                   FROM llm_calls
                   WHERE started_at >= ? AND status = 'ok' AND latency_ms IS NOT NULL)
               WHERE rank = (95 * n + 99) / 100""",
            (since,),
        )
        return {row["provider_id"]: row["latency_ms"] for row in rows}

    async def daily(self, since: float) -> list[dict[str, Any]]:
        """Per local day ("YYYY-MM-DD") and provider: calls, errors, tokens. Days without calls are absent."""
        rows = await self.db.fetchall(
            """SELECT date(started_at, 'unixepoch', 'localtime') AS day, provider_id, count(*) AS calls,
                      sum(status != 'ok') AS errors,
                      coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                      coalesce(sum(completion_tokens), 0) AS completion_tokens
               FROM llm_calls WHERE started_at >= ? GROUP BY day, provider_id""",
            (since,),
        )
        return [dict(row) for row in rows]

    async def by_bot(self, since: float) -> list[dict[str, Any]]:
        """Every bot (in creation order) with its calls, tokens and the count of each decision outcome."""
        bots = await self.db.fetchall("SELECT id, name FROM bots ORDER BY created_at, id")
        calls = {
            row["bot_id"]: row
            for row in await self.db.fetchall(
                """SELECT bot_id, count(*) AS calls, coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                          coalesce(sum(completion_tokens), 0) AS completion_tokens
                   FROM llm_calls WHERE started_at >= ? AND bot_id IS NOT NULL GROUP BY bot_id""",
                (since,),
            )
        }
        outcomes: dict[str, dict[str, int]] = {}
        for row in await self.db.fetchall(
            "SELECT bot_id, outcome, count(*) AS n FROM decisions WHERE created_at >= ?"
            " GROUP BY bot_id, outcome",
            (since,),
        ):
            outcomes.setdefault(row["bot_id"], {})[row["outcome"]] = row["n"]
        views = []
        for bot in bots:
            used = calls.get(bot["id"])
            counted = outcomes.get(bot["id"], {})
            views.append(
                {
                    "bot_id": bot["id"],
                    "name": bot["name"],
                    "calls": used["calls"] if used else 0,
                    "prompt_tokens": used["prompt_tokens"] if used else 0,
                    "completion_tokens": used["completion_tokens"] if used else 0,
                    "outcomes": {outcome: counted.get(outcome, 0) for outcome in OUTCOMES},
                }
            )
        return views

    async def recent_errors(self, since: float, limit: int = RECENT_ERRORS) -> list[dict[str, Any]]:
        """Failed attempts, newest first (also those a retry then fixed). Test calls have no bot."""
        rows = await self.db.fetchall(
            """SELECT l.started_at AS at, l.provider_id, l.bot_id, coalesce(b.name, l.bot_id) AS bot_name,
                      coalesce(l.error, '') AS error
               FROM llm_calls l LEFT JOIN bots b ON b.id = l.bot_id
               WHERE l.started_at >= ? AND l.status = 'error'
               ORDER BY l.started_at DESC, l.id DESC LIMIT ?""",
            (since, limit),
        )
        return [dict(row) for row in rows]
