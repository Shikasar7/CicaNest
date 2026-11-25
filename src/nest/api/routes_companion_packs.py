"""伙伴包 in the console: export a companion as a pack, and import one as a new companion
on the desk — read first and shown, then made; skills and pets in the pack are installed for her only."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.routes_companions import Companion, Point, Record, make_companion
from nest.api.routes_plugins import _body
from nest.core.companion_pack import Pack, PackProblem, build_pack, pack_id, read_pack
from nest.core.desk import chat_of, chosen_chats
from nest.core.keepsakes import RECORDS_MAX
from nest.core.memory import place_key
from nest.core.plugins import PluginError, read_manifest
from nest.storage.memories import MemoryStore

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class PackImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=20)] = ""
    provider_id: str | None = None


@router.get("/companions/{bot_id}/export")
async def export(request: Request, bot_id: str, memories: bool = True, records: bool = True) -> Response:
    """Her pack: persona (with examples), what she remembers in her chat with the user (likes, facts, her
    promises), her 回忆档案."""
    state = request.app.state
    bot = state.brain.bots.get(bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="没有这个 bot")
    chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
    persona = examples = ""
    points: list[dict[str, str]] = []
    user_name = ""
    if chat is not None:
        if chat.persona_id is not None:
            found = await state.repo.get_persona(chat.persona_id)
            persona, examples = (found["content"], found["examples"] or "") if found else ("", "")
        if memories:
            for row in await MemoryStore(state.db).of_place(place_key(chat)):
                if row["kind"] == "promise" and row["bot_id"] != bot.id:
                    continue
                user_name = user_name or row["person"]
                points.append({"kind": row["kind"], "content": row["content"], "due": row["due"] or ""})
    kept = await state.keepsakes.store.of_bot(bot.id, drafts=False) if records else []
    pack = Pack(
        name=bot.name,
        aliases=list(bot.aliases),
        user_name=user_name,
        persona=persona,
        examples=examples,
        points=points[:20],
        records=[{"title": r["title"], "body": r["body"], "source": r["source"] or ""} for r in kept],
    )
    data = await asyncio.to_thread(build_pack, pack, plugin_id=pack_id(bot.name, bot.id))
    filename = quote(f"{bot.name}.nest-companion.zip")
    return Response(
        data,
        media_type="application/zip",
        headers={"content-disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _read(request: Request, stage: str) -> tuple[Any, Pack]:
    manager = request.app.state.plugins
    try:
        _, folder = manager.staged(stage)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    manifest, problems = read_manifest(folder)
    if manifest is None:
        raise HTTPException(status_code=422, detail="；".join(problems))
    place = manifest.provides.get("companion")
    if not place:
        raise HTTPException(status_code=422, detail="这不是伙伴包（plugin.json 里没有 provides.companion）")
    if manifest.provides.get("python"):
        # A companion has no use for code that runs inside nest; that only comes in through 「插件」, where
        # the user says they trust it.
        raise HTTPException(status_code=422, detail="伙伴包里不能带在 nest 里跑的 Python 模块，没有导入")
    if manifest.provides.get("mcp"):
        # Nor a program: imported, it would be on for her at once, without the user deciding on it alone.
        raise HTTPException(
            status_code=422, detail="伙伴包里不能带插件程序（MCP），要的话在「插件」页单独装；没有导入"
        )
    try:
        pack = read_pack(folder / str(place))
    except PackProblem as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return manifest, pack


def _own_id(request: Request, stage: str, bot_id: str) -> None:
    """The pack's plugin gets an id of this companion's: the same pack imported twice is two companions,
    each with her own skills and pets. What made her (persona, what she remembers of the user, her
    records) is not kept with it: it is in her settings now."""
    _, folder = request.app.state.plugins.staged(stage)
    path = folder / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    tail = re.sub(r"[^a-z0-9]", "", bot_id.lower())[-12:] or "x"
    data["id"] = f"{data['id']}.for{tail}"[:120]
    place = (data.get("provides") or {}).pop("companion", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if isinstance(place, str):
        companion = (folder / place).resolve()
        if companion.is_relative_to(folder.resolve()) and companion != folder.resolve():
            shutil.rmtree(companion, ignore_errors=True)


@router.put("/companion-packages")
async def stage(request: Request) -> dict[str, Any]:
    """A pack (the request body), read and shown, not imported yet."""
    try:
        staged = await request.app.state.plugins.stage(await _body(request))
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        manifest, pack = await asyncio.to_thread(_read, request, staged["stage"])
    except HTTPException:
        await request.app.state.plugins.discard(staged["stage"])
        raise
    extras = [k for k in manifest.view()["kinds"] if k != "companion"]
    return {
        "stage": staged["stage"],
        "plugin": staged["plugin"],
        "companion": pack.summary(),
        "extras": extras,
    }


@router.post("/companion-packages/{stage}/import", status_code=201)
async def import_pack(request: Request, stage: str, body: PackImport) -> dict[str, Any]:
    state = request.app.state
    manifest, pack = await asyncio.to_thread(_read, request, stage)
    made = await make_companion(
        state,
        Companion(
            name=body.name.strip() or pack.name,
            aliases=pack.aliases,
            provider_id=body.provider_id,
            persona=pack.persona,
            examples=pack.examples,
            user_name=pack.user_name,
            points=[Point(**p) for p in pack.points],
            records=[Record(**r) for r in pack.records[:RECORDS_MAX]],
        ),
    )
    plugin_id = None
    if any(k != "companion" for k in manifest.view()["kinds"]):
        # Her skills and pets: a plugin of hers alone. Another of the same pack upgrades it, so it is kept
        # under an id of its own per companion.
        await asyncio.to_thread(_own_id, request, stage, made["bot_id"])
        installed = await state.plugins.install(stage)
        await state.plugins.configure(installed.id, enabled=True, bots=[made["bot_id"]])
        plugin_id = installed.id
        state.broadcaster.publish("plugins", {})
    else:
        await state.plugins.discard(stage)
    return made | {"plugin_id": plugin_id}
