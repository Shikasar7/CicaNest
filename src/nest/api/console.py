"""Serve the built console (web/dist) as a single-page app."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

NOT_BUILT = (
    '<!doctype html><meta charset="utf-8"><title>nest</title>'
    "<p>nest 后台已运行，但控制台还没有构建。"
    "请在 <code>web</code> 目录执行 <code>npm run build</code> 后重启。</p>"
)
# Files under assets/ carry a hash of their content in the name (Vite): a new build gets new names, so the
# browser may keep them for good instead of asking again on every page load.
FOREVER = "public, max-age=31536000, immutable"


def mount_console(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def console(path: str) -> FileResponse | HTMLResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        if not index.is_file():
            return HTMLResponse(NOT_BUILT)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                hashed = candidate.parent == root / "assets"
                return FileResponse(candidate, headers={"cache-control": FOREVER} if hashed else None)
        # index.html must never be cached: it names the hashed asset files of the current build.
        return FileResponse(index, headers={"cache-control": "no-store"})
