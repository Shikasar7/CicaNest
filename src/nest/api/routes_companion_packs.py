"""伙伴包 in the console: export a companion as a pack, and import one as a new companion
on the desk — read first and shown, then made; her picture and stickers in the pack become hers, skills and
pets are installed for her only."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
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
from nest.core.plugins import PluginError, read_manifest, read_sticker_pack
from nest.core.sticker_packs import PackError
from nest.storage.avatars import image_type
from nest.storage.memories import MemoryStore

log = logging.getLogger(__name__)

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


def _read(state: Any, stage: str) -> tuple[Any, Pack]:
    manager = state.plugins
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


def _own_id(state: Any, stage: str, bot_id: str, was: str, name: str) -> None:
    """The pack's plugin gets an id of this companion's: the same pack imported twice is two companions,
    each with her own skills and pets (under the name she got here). What made her (persona, what she
    remembers of the user, her records, her stickers) is not kept with it: it is in her settings now."""
    _, folder = state.plugins.staged(stage)
    path = folder / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    tail = re.sub(r"[^a-z0-9]", "", bot_id.lower())[-12:] or "x"
    data["id"] = f"{data['id']}.for{tail}"[:120]
    provides = data.get("provides") or {}
    place = provides.pop("companion", None)
    stickers = provides.pop("stickers", None) or []
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for gone in (place, *stickers):
        if isinstance(gone, str):
            inside = (folder / gone).resolve()
            if inside.is_relative_to(folder.resolve()) and inside != folder.resolve():
                shutil.rmtree(inside, ignore_errors=True)
    for pet in provides.get("pets") or []:
        _rename_pet(folder / str(pet), was, name)


def _rename_pet(folder: Path, was: str, name: str) -> None:
    """A pet belongs to the companion of its name."""
    path = folder / "pet.json"
    if was == name or not path.is_file():
        return
    meta = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(meta, dict):
        for key in ("name", "skin_of"):
            if meta.get(key) == was:
                meta[key] = name
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


async def _look(state: Any, bot_id: str, pack: Pack) -> None:
    """Her picture."""
    content_type = image_type(pack.avatar)
    if content_type is not None:
        await state.avatars.put(bot_id, "custom", content_type, pack.avatar)
        state.broadcaster.publish("avatars", {"bot_id": bot_id})


async def _stickers(state: Any, stage: str, bot_id: str, manifest: Any) -> None:
    """Her sticker packs, hers from now on."""
    _, folder = state.plugins.staged(stage)
    for place in manifest.provides.get("stickers") or []:

        def read(place: str = str(place)) -> tuple[str, list[tuple[str, bytes]]]:
            found = read_sticker_pack(folder / place)
            return found["name"], [(mood, path.read_bytes()) for mood, path in found["items"]]

        try:
            name, items = await asyncio.to_thread(read)
            await state.sticker_packs.take_in(bot_id, name, items)
        except (PackError, PluginError, OSError) as exc:
            log.warning("伙伴包里的表情 %s 没拿进来：%s", place, exc)


@router.put("/companion-packages")
async def stage(request: Request) -> dict[str, Any]:
    """A pack (the request body), read and shown, not imported yet."""
    try:
        staged = await request.app.state.plugins.stage(await _body(request))
    except PluginError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        manifest, pack = await asyncio.to_thread(_read, request.app.state, staged["stage"])
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
    return await bring(request.app.state, stage, name=body.name, provider_id=body.provider_id)


async def bring(state: Any, stage: str, *, name: str = "", provider_id: str | None = None) -> dict[str, Any]:
    """The companion of a staged pack, made new on the desk with all the pack has of hers."""
    manifest, pack = await asyncio.to_thread(_read, state, stage)
    name = name.strip() or pack.name
    made = await make_companion(
        state,
        Companion(
            name=name,
            aliases=pack.aliases,
            provider_id=provider_id,
            persona=pack.persona,
            examples=pack.examples,
            user_name=pack.user_name,
            points=[Point(**p) for p in pack.points],
            records=[Record(**r) for r in pack.records[:RECORDS_MAX]],
        ),
    )
    bot_id = made["bot_id"]
    await _look(state, bot_id, pack)
    await _stickers(state, stage, bot_id, manifest)
    plugin_id = None
    if any(k not in ("companion", "stickers") for k in manifest.view()["kinds"]):
        # Her skills and pets: a plugin of hers alone. Another of the same pack upgrades it, so it is kept
        # under an id of its own per companion.
        await asyncio.to_thread(_own_id, state, stage, bot_id, pack.name, name)
        installed = await state.plugins.install(stage)
        await state.plugins.configure(installed.id, enabled=True, bots=[bot_id])
        plugin_id = installed.id
        state.broadcaster.publish("plugins", {})
    else:
        await state.plugins.discard(stage)
    return made | {"plugin_id": plugin_id}
