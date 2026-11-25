"""The console's own files are served by their right types, whatever the system's table says."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI

from nest.api.console import mount_console


async def test_pictures_and_the_manifest_have_their_types(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (tmp_path / "me.webp").write_bytes(b"RIFF\0\0\0\0WEBP")
    (tmp_path / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    app = FastAPI()
    mount_console(app, tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as http:
        assert (await http.get("/me.webp")).headers["content-type"] == "image/webp"
        manifest = await http.get("/manifest.webmanifest")
        assert manifest.headers["content-type"] == "application/manifest+json"
