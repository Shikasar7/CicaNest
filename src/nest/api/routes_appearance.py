"""外观 in the console: which theme (nest's own or a plugin's) and accent colour the console, the chat window
and the desktop app use; sharing a theme as a plugin zip and putting one in (nest.core.themes)."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.routes_plugins import _body
from nest.core import themes
from nest.core.plugins import PluginError
from nest.storage.settings import SettingsRepo

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

ONLY_THEMES = "这个包里不只有主题，请到「插件」页去装（那里会给你看它要什么）"


async def _catalogue(request: Request) -> dict[str, dict[str, Any]]:
    return await asyncio.to_thread(themes.catalogue, request.app.state.plugins)


async def _choice(request: Request) -> dict[str, Any]:
    return themes.choice_of((await SettingsRepo(request.app.state.db).all()).get(themes.SETTING))


@router.get("/appearance")
async def appearance(request: Request) -> dict[str, Any]:
    return themes.view(await _choice(request), await _catalogue(request))


class AppearanceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str | None = None
    accent: str | None = None  # "#rrggbb"; "" for the theme's own


@router.put("/appearance")
async def change(request: Request, body: AppearanceChange) -> dict[str, Any]:
    choice = await _choice(request)
    found = await _catalogue(request)
    if body.theme is not None:
        if body.theme not in found:
            raise HTTPException(status_code=404, detail="没有这个主题（可能装它的插件关掉或卸载了）")
        choice["theme"] = body.theme
    if body.accent is not None:
        if body.accent == "":
            choice["accent"] = None
        else:
            colour = themes.hex_colour(body.accent)
            if colour is None:
                raise HTTPException(status_code=422, detail="颜色要写成 #rrggbb")
            choice["accent"] = colour
    await SettingsRepo(request.app.state.db).set_many({themes.SETTING: choice})
    request.app.state.broadcaster.publish("system", {"change": "appearance"})
    return themes.view(choice, found)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str
    accent: str | None = None
    name: str
    author: str = ""
    description: str = ""
    id: str | None = None  # the plugin's id; made up from the colours when left out


@router.post("/themes/export")
async def export(request: Request, body: ExportBody) -> Response:
    """A theme (with the accent of your own, if any) as a plugin zip: install it on another nest, or put it
    on a market for others. Every colour is written out, so it looks the same wherever it goes."""
    found = await _catalogue(request)
    theme = found.get(body.theme)
    if theme is None:
        raise HTTPException(status_code=404, detail="没有这个主题")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="给主题起个名字")
    accent = themes.hex_colour(body.accent) if body.accent else None
    colours = themes.resolve(theme, accent)
    shared = {"name": name[: themes.NAME_MAX], "description": body.description.strip(), **colours}
    try:
        plugin_id, data = themes.package(shared, author=body.author, plugin_id=body.id or None)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    filename = f"{plugin_id}-1.0.0.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.put("/theme-packages", status_code=201)
async def put_in(request: Request) -> dict[str, Any]:
    """A theme to use: a theme plugin zip, or a single theme .json (made into a plugin here). Installed at
    once, since a theme is only colours; a zip that holds anything else goes through the 插件 page."""
    data = await _body(request)
    manager = request.app.state.plugins
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
        try:
            theme = themes.read_theme_bytes(data)
            _, data = themes.package(theme)
        except themes.ThemeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        staged = await manager.stage(data)
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if staged["plugin"]["kinds"] != ["themes"]:
        await manager.discard(staged["stage"])
        raise HTTPException(status_code=422, detail=ONLY_THEMES)
    try:
        manifest = await manager.install(staged["stage"])
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await manager.configure(manifest.id, enabled=True)  # put in to be used: on, even if switched off before
    request.app.state.broadcaster.publish("plugins", {})
    request.app.state.broadcaster.publish("system", {"change": "appearance"})
    found = await _catalogue(request)
    return {
        "plugin": manifest.id,
        "upgrade": staged["upgrade"],
        "themes": [tid for tid, t in found.items() if t.get("plugin") == manifest.id],
    }
