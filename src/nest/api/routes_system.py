"""Process-level endpoints: health, status, login, shutdown."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from nest import __version__, hints
from nest.api.auth import COOKIE_NAME, is_local, require_token, token_matches

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    # Unauthenticated on purpose: lets the launcher tell "nest is up" from "another program owns the port".
    return {"app": "nest", "ok": True, "version": __version__}


@router.get("/api/status", dependencies=[Depends(require_token)])
async def status(request: Request) -> dict:
    started = request.app.state.started_at
    return {
        "version": __version__,
        "started_at": started,
        "uptime_seconds": round(time.time() - started, 1),
        "schema_version": await request.app.state.db.schema_version(),
    }


@router.post("/api/shutdown", dependencies=[Depends(require_token)])
async def shutdown(request: Request) -> dict:
    request.app.state.broadcaster.close()
    request.app.state.request_shutdown()
    return {"ok": True}


@router.get("/login", include_in_schema=False, response_model=None)
async def login(request: Request, token: str = "") -> HTMLResponse | RedirectResponse:
    if not (is_local(request) and token_matches(request, token)):  # the token never leaves this machine
        return HTMLResponse(f"<p>登录链接已失效，请重新{hints.START}打开控制台。</p>", status_code=403)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", path="/")
    return response
