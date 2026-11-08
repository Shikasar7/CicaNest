"""Usage: model calls, latency, tokens and bot decisions for today, 7 or 14 days (local days)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from nest.api.auth import require_token
from nest.storage.usage import UsageRepo

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

DAYS = (1, 7, 14)  # llm_calls are kept 14 days


def _days(days: int) -> list[date]:
    """The local days in range, oldest first: today, back to (days − 1) days ago."""
    today = date.today()
    return [today - timedelta(days=back) for back in reversed(range(days))]


@router.get("/usage")
async def usage(request: Request, days: int = Query(7)) -> dict[str, Any]:
    if days not in DAYS:
        raise HTTPException(status_code=422, detail="天数只能是 1、7 或 14")
    state = request.app.state
    span = _days(days)
    in_range = [day.isoformat() for day in span]
    since = int(datetime.combine(span[0], datetime.min.time()).timestamp())  # local midnight
    repo = UsageRepo(state.db)
    totals = await repo.by_provider(since)
    p95 = await repo.p95_latency(since)
    configured = {config.id for config in await state.repo.providers()}

    providers = []
    for provider_id in sorted(configured | set(totals)):
        row = totals.get(provider_id)
        calls = row["calls"] if row else 0
        errors = row["errors"] if row else 0
        avg = row["avg_latency_ms"] if row else None
        providers.append(
            {
                "provider_id": provider_id,
                "deleted": provider_id not in configured,
                "calls": calls,
                "ok": calls - errors,
                "errors": errors,
                "error_rate": errors / calls if calls else None,
                "avg_latency_ms": round(avg, 1) if avg is not None else None,
                "p95_latency_ms": p95.get(provider_id),
                "prompt_tokens": row["prompt_tokens"] if row else 0,
                "completion_tokens": row["completion_tokens"] if row else 0,
                "token_calls": row["token_calls"] if row else 0,
            }
        )

    # Every day in range for every model that was called, zero-filled.
    found = {(row["provider_id"], row["day"]): row for row in await repo.daily(since)}
    daily = []
    for provider_id in sorted(totals):
        for day in in_range:
            row = found.get((provider_id, day))
            daily.append(
                {
                    "day": day,
                    "provider_id": provider_id,
                    "calls": row["calls"] if row else 0,
                    "errors": row["errors"] if row else 0,
                    "prompt_tokens": row["prompt_tokens"] if row else 0,
                    "completion_tokens": row["completion_tokens"] if row else 0,
                }
            )

    return {
        "since": since,
        "days": days,
        "providers": providers,
        "bots": await repo.by_bot(since),
        "daily": daily,
        "recent_errors": await repo.recent_errors(since),
    }
