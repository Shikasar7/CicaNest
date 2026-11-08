"""Personas: who a bot is told it is, assigned per group. Edits take effect at once, without restarting.

Every change runs under app.state.config_lock and ends with brain.load(), which re-reads persona text.
Writes are plain INSERT/UPDATE (never the CLI's upsert, which would overwrite a persona of the same name,
and never INSERT OR REPLACE, which would delete the row and unassign it everywhere).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.bots import InUse, NameTaken

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

NAME_LIMIT = 40
CONTENT_LIMIT = 4000
EXAMPLES_LIMIT = 1000
# Bounded like an SQLite id: a larger number cannot even be looked up.
PersonaId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class PersonaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    content: str


class PersonaPatch(BaseModel):
    """At least one field; null means "not sent"."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    content: str | None = None
    examples: str | None = None  # "" removes them


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= NAME_LIMIT:
        raise _invalid(f"人设名字要填 1–{NAME_LIMIT} 个字")
    if has_bad_chars(value):
        raise _invalid("人设名字里不能有换行或控制字符")
    return value


def _content(value: str) -> str:
    value = value.strip()
    if not value:
        raise _invalid("人设内容不能是空的")
    if len(value) > CONTENT_LIMIT:
        raise _invalid(f"人设内容最多 {CONTENT_LIMIT} 个字")
    if has_bad_chars(value, allow="\n\r\t"):
        raise _invalid("人设内容里有不能保存的控制字符")
    return value


def _examples(value: str) -> str:
    value = value.strip()
    if len(value) > EXAMPLES_LIMIT:
        raise _invalid(f"说话示例最多 {EXAMPLES_LIMIT} 个字")
    if has_bad_chars(value, allow="\n\r\t"):
        raise _invalid("说话示例里有不能保存的控制字符")
    return value


def _taken(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"已经有叫 {name} 的人设")


async def _views(state: Any) -> list[dict[str, Any]]:
    uses = await state.repo.persona_uses()
    return [persona | {"used_by": uses.get(persona["id"], [])} for persona in await state.repo.personas()]


async def _view_of(state: Any, persona_id: int) -> dict[str, Any]:
    return next(view for view in await _views(state) if view["id"] == persona_id)


async def _changed(state: Any) -> None:
    await state.brain.load()
    state.broadcaster.publish("bots", {"bot_id": None})


@router.get("/personas")
async def personas(request: Request) -> list[dict[str, Any]]:
    """Sorted by name; used_by lists every group a persona is assigned in."""
    return await _views(request.app.state)


@router.post("/personas", status_code=201)
async def create_persona(request: Request, body: PersonaCreate) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        name, content = _name(body.name), _content(body.content)
        try:
            persona_id = await state.repo.create_persona(name, content)
        except NameTaken:
            raise _taken(name) from None
        await _changed(state)
        return await _view_of(state, persona_id)


@router.patch("/personas/{persona_id}")
async def update_persona(request: Request, persona_id: PersonaId, patch: PersonaPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        if patch.name is None and patch.content is None and patch.examples is None:
            raise _invalid("没有要改的内容")
        if await state.repo.get_persona(persona_id) is None:
            raise HTTPException(status_code=404, detail="没有这个人设")
        # Only what was sent is checked: renaming never re-checks the stored text.
        name = _name(patch.name) if patch.name is not None else None
        content = _content(patch.content) if patch.content is not None else None
        try:
            updated = await state.repo.update_persona(
                persona_id,
                name=name,
                content=content,
                examples=_examples(patch.examples) if patch.examples is not None else None,
            )
        except NameTaken:
            raise _taken(name or "") from None
        if not updated:
            raise HTTPException(status_code=404, detail="没有这个人设")
        await _changed(state)
        return await _view_of(state, persona_id)


@router.delete("/personas/{persona_id}")
async def delete_persona(request: Request, persona_id: PersonaId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        try:
            deleted = await state.repo.delete_persona(persona_id)
        except InUse as exc:
            raise HTTPException(status_code=409, detail=f"还用在：{'、'.join(exc.users)}") from None
        if not deleted:
            raise HTTPException(status_code=404, detail="没有这个人设")
        await _changed(state)
    return {"ok": True}
