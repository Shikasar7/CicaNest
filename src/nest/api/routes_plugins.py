"""插件 in the console: nest's own and the installed ones, their switches (on, and for which
companions), installing a plugin zip in two steps (read it, then install it), uninstalling."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from nest import __version__, plugin_code
from nest.api.auth import require_token
from nest.core import egress, market
from nest.core.mcp_plugins import secret_ref
from nest.core.plugins import UNTRUSTED, ZIP_MAX, PluginError, UntrustedPlugin, newer, read_manifest
from nest.storage.settings import SettingsRepo

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class PluginSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    all_bots: bool | None = None  # true: every companion; false: only `bots`
    bots: list[str] | None = None


def _changed(request: Request) -> None:
    request.app.state.broadcaster.publish("plugins", {})
    # A theme plugin switched on or off, put in or taken out: the 外观 choices changed.
    request.app.state.broadcaster.publish("system", {"change": "appearance"})


@router.get("/plugins")
async def plugins(request: Request) -> dict[str, Any]:
    manager = request.app.state.plugins
    bots = [{"bot_id": b.id, "name": b.name} for b in request.app.state.brain.bots.values()]
    mcp = request.app.state.mcp
    shown = await asyncio.to_thread(manager.views)
    for plugin in shown:
        installed = manager.installed.get(plugin["id"])
        if installed is None:
            continue
        manifest = installed[0]
        plugin["secrets_set"] = {
            name: bool(request.app.state.secrets.get(secret_ref(manifest.id, name)))
            for name, _ in manifest.secrets
        }
        if manifest.provides.get("mcp"):
            tools = mcp.tools_of(manifest)
            plugin["mcp_tools"] = [t["name"] for t in tools] if tools is not None else None
            plugin["mcp_problem"] = mcp.problems.get(manifest.id, "")
    return {
        "plugins": shown,
        "broken": manager.broken,
        "bots": bots,
        "can_run": request.app.state.sandbox is not None,
    }


class PluginSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, str]  # name -> value; "" clears it


@router.get("/plugins/{plugin_id}/network")
async def plugin_network(request: Request, plugin_id: str) -> dict[str, Any]:
    """What the plugin's containers reached (from the proxy's log) against the domains it declared."""
    state = request.app.state
    found = state.plugins.installed.get(plugin_id)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这个插件")
    manifest, _ = found
    sandbox = getattr(state, "sandbox", None)
    folder = sandbox.egress_log if sandbox is not None else None
    if folder is None:
        return {"declared": list(manifest.network), "seen": [], "undeclared": [], "logged": False}
    reach = await asyncio.to_thread(egress.plugin_reach, folder, plugin_id, manifest.network)
    return reach | {"logged": True}


@router.put("/plugins/{plugin_id}/secrets")
async def secrets(request: Request, plugin_id: str, body: PluginSecrets) -> dict[str, Any]:
    """The secrets a plugin declared (only those), kept with nest's other secrets; never sent back."""
    installed = request.app.state.plugins.installed.get(plugin_id)
    if installed is None:
        raise HTTPException(status_code=404, detail="没有这个插件")
    declared = {name for name, _ in installed[0].secrets}
    store = request.app.state.secrets
    for name, value in body.values.items():
        if name not in declared:
            raise HTTPException(status_code=422, detail=f"这个插件没有要「{name}」")
        if any(c in value for c in "\r\n\x00") or len(value) > 4000:
            raise HTTPException(status_code=422, detail="密钥里不能有换行，也不能太长")
        ref = secret_ref(plugin_id, name)
        if value.strip():
            await asyncio.to_thread(store.set, ref, value.strip())
        else:
            await asyncio.to_thread(store.unset, ref)
    await request.app.state.mcp.close_plugin(plugin_id)
    _changed(request)
    return {"ok": True}


@router.get("/model-kinds")
async def model_kinds() -> list[dict[str, Any]]:
    """Kinds of model that plugins added (nest's own are known to the console)."""
    return [
        {"kind": k.kind, "label": k.label, "options": list(k.options), "plugin": k.plugin}
        for k in plugin_code.MODEL_KINDS.values()
    ]


@router.get("/pet-plugins")
async def pet_plugins(request: Request) -> dict[str, Any]:
    """Pets, skins and accessories from plugins that are on: the desktop app fetches their files."""
    return {"plugins": await asyncio.to_thread(request.app.state.plugins.pets)}


@router.get("/pet-plugins/{plugin_id}/file")
async def pet_file(request: Request, plugin_id: str, path: str) -> dict[str, Any]:
    """One file of a plugin's pet folder, as base64 (the desktop app's requests read text)."""
    try:
        found = request.app.state.plugins.pet_file(plugin_id, path)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    data = await asyncio.to_thread(found.read_bytes)
    return {"data": base64.b64encode(data).decode("ascii")}


@router.put("/plugins/{plugin_id}")
async def switch(request: Request, plugin_id: str, body: PluginSwitch) -> dict[str, Any]:
    manager = request.app.state.plugins
    known = request.app.state.brain.bots
    if body.bots is not None and any(b not in known for b in body.bots):
        raise HTTPException(status_code=404, detail="没有这个 bot")
    bots: Any = ...
    if body.all_bots is True:
        bots = None
    elif body.bots is not None:
        bots = body.bots
    try:
        await manager.configure(plugin_id, enabled=body.enabled, bots=bots)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.mcp.close_plugin(plugin_id)
    _changed(request)
    return {"ok": True}


async def _body(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > ZIP_MAX:
            raise HTTPException(status_code=413, detail=f"插件包不能超过 {ZIP_MAX // 1024**2} MB")
    return bytes(data)


@router.put("/plugin-packages")
async def stage(request: Request) -> dict[str, Any]:
    """A plugin zip (the request body), unpacked and read but not installed: what it provides and asks for."""
    try:
        return await request.app.state.plugins.stage(await _body(request))
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/plugin-packages/{stage}")
async def discard(request: Request, stage: str) -> dict[str, Any]:
    """A package read and not installed: thrown away (cancelled in the console)."""
    try:
        await request.app.state.plugins.discard(stage)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"ok": True}


class InstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust: bool = False  # the user said they trust it: needed for a plugin whose code runs inside nest


@router.post("/plugin-packages/{stage}/install", status_code=201)
async def install(request: Request, stage: str, body: InstallBody | None = None) -> dict[str, Any]:
    manager = request.app.state.plugins
    try:
        _, folder = manager.staged(stage)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    found, _ = await asyncio.to_thread(read_manifest, folder)
    if found is not None and found.trust == "trusted" and not (body and body.trust):
        raise HTTPException(status_code=409, detail=UNTRUSTED)
    try:
        manifest = await manager.install(stage, trusted=bool(body and body.trust))
    except UntrustedPlugin as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.mcp.close_plugin(manifest.id)
    _changed(request)
    return {"id": manifest.id}


class MarketAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # "" for none


async def _market(request: Request) -> tuple[str, str | None]:
    settings = SettingsRepo(request.app.state.db)
    url = (await settings.all()).get(market.SETTING)
    return (url if isinstance(url, str) else ""), (await settings.fetch_proxy()) or None


@router.get("/market")
async def market_list(request: Request) -> dict[str, Any]:
    """The plugins the market list offers, with what is installed of each."""
    url, proxy = await _market(request)
    if not url:
        return {"url": "", "entries": [], "error": ""}
    try:
        entries = await market.read_index(url, proxy=proxy, transport=request.app.state.fetch_transport)
    except market.MarketError as exc:
        return {"url": url, "entries": [], "error": str(exc)}
    manager = request.app.state.plugins
    request.app.state.market.take(market.updates(entries, manager.installed))
    for entry in entries:
        found = manager.installed.get(entry["id"])
        entry["installed"] = found[0].version if found else None
        entry["newer"] = bool(found and newer(entry["version"], found[0].version))
        entry["signature"] = market.signature_state(entry, manager.author_key(entry["id"]))
        entry["too_new"] = bool(entry["nest"] and newer(entry["nest"], __version__))
        del entry["author_key"]
    return {"url": url, "entries": entries, "error": ""}


@router.get("/market/updates")
async def market_updates(request: Request) -> dict[str, Any]:
    """Installed plugins with a newer version in the market, as last looked (once a day)."""
    return {"updates": request.app.state.market.found}


@router.put("/market")
async def market_address(request: Request, body: MarketAddress) -> dict[str, Any]:
    url = body.url.strip()
    if url:
        try:
            url = await market.check_url(url)
        except market.MarketError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    await SettingsRepo(request.app.state.db).set_many({market.SETTING: url})
    return {"url": url}


@router.post("/market/{plugin_id}/stage")
async def market_stage(request: Request, plugin_id: str) -> dict[str, Any]:
    """Download one from the market and read it (not installed yet), shown like a zip chosen by hand."""
    url, proxy = await _market(request)
    if not url:
        raise HTTPException(status_code=409, detail="还没有设插件市场的地址")
    transport = request.app.state.fetch_transport
    try:
        entries = await market.read_index(url, proxy=proxy, transport=transport)
        entry = next((e for e in entries if e["id"] == plugin_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="市场里没有这个插件")
        if entry["nest"] and newer(entry["nest"], __version__):
            raise HTTPException(
                status_code=409, detail=f"它要 nest {entry['nest']} 以上，现在是 {__version__}：先更新 nest"
            )
        key = market.signed_by(entry)
        kept = request.app.state.plugins.author_key(plugin_id)
        if kept and key != kept:
            raise HTTPException(
                status_code=409,
                detail="签名和装着的那一版不是同一个作者（或者这一版没有签名），没有下载。确认是作者自己换了签名的话，先卸载再装",
            )
        data = await market.download(entry, proxy=proxy, transport=transport)
    except market.MarketError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    manager = request.app.state.plugins
    try:
        staged = await manager.stage(data)
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    shown = staged["plugin"]
    if shown["id"] != entry["id"] or shown["version"] != entry["version"]:
        await manager.discard(staged["stage"])
        raise HTTPException(
            status_code=422, detail="下载的包和市场里写的不是同一个插件（id 或版本不同），没有装"
        )
    if key:
        manager.stage_keys[staged["stage"]] = key
    return staged | {"signed": bool(key)}


@router.delete("/plugins/{plugin_id}")
async def uninstall(request: Request, plugin_id: str) -> dict[str, Any]:
    try:
        await request.app.state.plugins.uninstall(plugin_id)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await request.app.state.mcp.close_plugin(plugin_id)
    _changed(request)
    return {"ok": True}
