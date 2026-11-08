"""问题记录: the issues the groups' 记录官 wrote down, their status, and the daily digest's settings."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import check_recipient
from nest.api.auth import require_token
from nest.core.issues import DEFAULT_TIMES, MAX_TIMES
from nest.storage.settings import SettingsRepo

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
IssueId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class IssuePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "done", "ignored"]


class DigestSettings(BaseModel):
    """null means "not sent"."""

    model_config = ConfigDict(extra="forbid")

    to: Annotated[str, Field(max_length=80)] | None = None  # "" turns the digest off
    times: Annotated[list[str], Field(max_length=MAX_TIMES)] | None = None


async def digest_settings(state: Any) -> tuple[str, list[str]]:
    """Who the digest goes to ("" = off) and the times of day it is sent, sorted."""
    stored = await SettingsRepo(state.db).all()
    to, times = stored.get("issue_digest_to"), stored.get("issue_digest_times")
    if not isinstance(times, list) or not all(isinstance(t, str) and TIME.fullmatch(t) for t in times):
        times = list(DEFAULT_TIMES)
    return (to if isinstance(to, str) else "", sorted(set(times)))


def _recorders(state: Any) -> list[dict[str, Any]]:
    return [
        {"bot_id": bot.id, "bot_name": bot.name, "group_id": ch.channel.id, "group_name": ch.name}
        for bot, ch in state.issues.recorders()
    ]


@router.get("/issues")
async def list_issues(
    request: Request,
    status: Literal["open", "done", "ignored"] | None = None,
    group: Annotated[str | None, Query(max_length=20)] = None,
) -> dict[str, Any]:
    state = request.app.state
    issues = await state.issues.repo.issues(status=status, group_id=group or None)
    every = await state.issues.repo.issues() if (status or group) else issues
    groups = {issue["group_id"]: issue["group_name"] for issue in reversed(every)}
    names = {b.id: b.name for b in state.brain.bots.values()}
    to, times = await digest_settings(state)
    return {
        "issues": [issue | {"bot_name": names.get(issue["bot_id"], "")} for issue in issues],
        "groups": [{"group_id": key, "name": name} for key, name in groups.items()],
        "counts": {s: sum(issue["status"] == s for issue in every) for s in ("open", "done", "ignored")},
        "recorders": _recorders(state),
        "digest": {"to": to, "times": times},
    }


@router.patch("/issues/{issue_id}")
async def update_issue(request: Request, issue_id: IssueId, patch: IssuePatch) -> dict[str, Any]:
    state = request.app.state
    if not await state.issues.repo.set_status(issue_id, patch.status):
        raise HTTPException(status_code=404, detail="没有这条记录")
    state.broadcaster.publish("issues", {"issue_id": issue_id})
    return {"ok": True}


@router.delete("/issues/{issue_id}")
async def delete_issue(request: Request, issue_id: IssueId) -> dict[str, Any]:
    state = request.app.state
    if not await state.issues.repo.delete(issue_id):
        raise HTTPException(status_code=404, detail="没有这条记录")
    state.broadcaster.publish("issues", {"issue_id": issue_id})
    return {"ok": True}


@router.put("/issues/digest")
async def update_digest(request: Request, body: DigestSettings) -> dict[str, Any]:
    state = request.app.state
    values: dict[str, Any] = {}
    if body.to is not None:
        try:
            values["issue_digest_to"] = check_recipient(body.to)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.times is not None:
        times = sorted({t.strip() for t in body.times})
        if not times:
            raise HTTPException(status_code=422, detail="至少要有一个发送时间")
        if not all(TIME.fullmatch(t) for t in times):
            raise HTTPException(status_code=422, detail="时间要写成 24 小时制的 HH:MM，比如 21:00")
        values["issue_digest_times"] = times
    if values:
        await SettingsRepo(state.db).set_many(values)
        state.broadcaster.publish("issues", {"digest": True})
    to, times = await digest_settings(state)
    return {"to": to, "times": times}


@router.post("/issues/review")
async def review_now(request: Request) -> dict[str, Any]:
    """「立即整理」: every 记录官 reads its group's new messages now."""
    state = request.app.state
    if not state.issues.recorders():
        return {"ok": False, "detail": "还没有 bot 当记录官：在「Bot」页某个群的设置里打开「问题记录官」"}
    added = await state.issues.review_all()
    return {"ok": True, "detail": f"新记下 {added} 条" if added else "没有新的要记"}


@router.post("/issues/digest")
async def send_digest_now(request: Request) -> dict[str, Any]:
    """「立即发送」: the digest now, as it would go out at the set time."""
    sent, detail = await request.app.state.issues.send_digest()
    return {"ok": sent, "detail": detail}
