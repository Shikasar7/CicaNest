"""确认 in the console and the desktop chat window: the steps waiting for the user's nod,
and the answer to one. Only a signed-in device can answer; who answered (address and browser) is kept."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

ApprovalId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["once", "kind", "deny"]


def _view(row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return row | {"bot_name": names.get(row["bot_id"], row["bot_id"])}


@router.get("/approvals")
async def approvals(request: Request, everything: bool = False) -> dict[str, Any]:
    """What waits for the user's nod (with `everything`, the latest answered ones too)."""
    keeper = request.app.state.approvals
    names = {b.id: b.name for b in request.app.state.brain.bots.values()}
    rows = await (keeper.recent() if everything else keeper.pending())
    return {"approvals": [_view(r, names) for r in rows]}


@router.post("/approvals/{approval_id}")
async def answer(request: Request, approval_id: ApprovalId, body: Answer) -> dict[str, Any]:
    client = request.client.host if request.client else ""
    agent = request.headers.get("user-agent", "")[:120]
    if not await request.app.state.approvals.decide(approval_id, body.choice, f"{client} · {agent}"):
        raise HTTPException(status_code=409, detail="这个确认已经过了（答过了、超时了，或者 nest 重启过）")
    return {"ok": True}
