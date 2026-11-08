"""项目资料 (the console's 「资料」 page): help-doc bases, which group answers project questions from which,
reloading a base, and 「试问」: what retrieval finds for a question, step by step, without asking a model. A
base may name its project's source checkout, read when the docs do not answer (看源码).

Writes hold app.state.config_lock. A base is a folder on this machine in the docs/help format; only the
help files named in nest.knowledge.loader.FILES are read from it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path as FilePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import check_recipient
from nest.api.auth import require_token
from nest.api.routes_stickers import group_names
from nest.api.text import has_bad_chars
from nest.knowledge.loader import load
from nest.knowledge.service import KnowledgeService, LoadedBase
from nest.knowledge.source import SourceTree

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

BaseId = Annotated[int, Path(ge=1, le=2**63 - 1)]
NAME_MAX = 40
FOLDER_MAX = 400
QUESTION_MAX = 500
NOTE_TITLE_MAX = 60
NOTE_BODY_MAX = 1500
GapId = Annotated[int, Path(ge=1, le=2**63 - 1)]
STATUSES = ("open", "answered", "ignored")
NOT_FOUND = "没有这个资料库"


class BaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    folder: str


class BasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    folder: str | None = None
    forward_to: str | None = None  # who hears of unanswered questions (check_recipient), "" for nobody
    source_dir: str | None = None  # the project's source checkout, or "" for none


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: Annotated[str, Field(min_length=1, max_length=80)]
    base_id: int | None  # null: the group answers no project questions


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=NOTE_TITLE_MAX)]
    body: Annotated[str, Field(min_length=1, max_length=NOTE_BODY_MAX)]


class Ask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=1, max_length=QUESTION_MAX)]


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _name(value: str) -> str:
    value = " ".join(value.split())
    if not value or len(value) > NAME_MAX or has_bad_chars(value):
        raise _invalid(f"名字不能是空的，最多 {NAME_MAX} 个字")
    return value


async def _folder(value: str) -> str:
    """A folder that holds help files; checked by reading them (blocking, so in a thread)."""
    value = value.strip()
    if not value or len(value) > FOLDER_MAX or has_bad_chars(value):
        raise _invalid("文件夹路径不对")

    def check() -> int:
        folder = FilePath(value).expanduser()
        if not folder.is_dir():
            raise _invalid(f"找不到文件夹 {value}")
        try:
            return len(load(folder))
        except (OSError, ValueError) as exc:
            raise _invalid(f"读不了这个文件夹里的资料：{exc}") from None

    if not await asyncio.to_thread(check):
        raise _invalid("这个文件夹里没有资料：要有 features.md、errors.md 这类帮助文件")
    return value


async def _source_dir(value: str) -> str:
    """A folder of source to read, or "" for none."""
    value = value.strip()
    if not value:
        return ""
    if len(value) > FOLDER_MAX or has_bad_chars(value):
        raise _invalid("源码文件夹路径不对")
    if not await asyncio.to_thread(lambda: FilePath(value).expanduser().is_dir()):
        raise _invalid(f"找不到文件夹 {value}")
    return value


def _source(folder: str) -> dict[str, Any] | None:
    """Blocking: the checkout's commit and how many files can be read, or None when there is none."""
    if not folder:
        return None
    tree = SourceTree(folder)
    if not tree.ready():
        return {"ready": False, "commit": "", "date": "", "files": 0}
    revision = tree.revision()
    return {"ready": True, "commit": revision.commit, "date": revision.date, "files": len(tree.files())}


def _service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge


async def _view(loaded: LoadedBase, groups: dict[str, int]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for section in loaded.sections:
        counts[section.file] = counts.get(section.file, 0) + 1
    return {
        "id": loaded.base.id,
        "name": loaded.base.name,
        "folder": loaded.base.folder,
        "sections": len(loaded.sections),
        "files": counts,
        "loaded_at": loaded.loaded_at or None,
        "vectors": loaded.dense is not None,
        "problem": loaded.problem,
        "forward_to": loaded.base.forward_to,
        "source_dir": loaded.base.source_dir,
        "source": await asyncio.to_thread(_source, loaded.base.source_dir),
        "groups": sorted(key for key, base_id in groups.items() if base_id == loaded.base.id),
    }


@router.get("/knowledge")
async def overview(request: Request) -> dict[str, Any]:
    service = _service(request)
    names = await group_names(request.app.state)
    return {
        "bases": [await _view(loaded, service.groups) for loaded in service.bases.values()],
        "groups": [
            {"group_key": key, "name": name, "base_id": service.groups.get(key)}
            for key, name in sorted(names.items())
        ],
        "models": {
            "embed": service.embedder.model.name,
            "rerank": service.reranker.model.name,
            **{f"{key}_up": up for key, up in (await service.models_up()).items()},
        },
    }


@router.post("/knowledge", status_code=201)
async def add_base(request: Request, body: BaseCreate) -> dict[str, Any]:
    name, folder = _name(body.name), await _folder(body.folder)
    service = _service(request)
    async with request.app.state.config_lock:
        try:
            base_id = await service.repo.add(name, folder)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"已经有叫「{name}」的资料库了") from None
        await service.refresh()
    return await _view(service.bases[base_id], service.groups)


@router.patch("/knowledge/{base_id}")
async def edit_base(request: Request, base_id: BaseId, body: BasePatch) -> dict[str, Any]:
    name = _name(body.name) if body.name is not None else None
    folder = await _folder(body.folder) if body.folder is not None else None
    forward_to = None
    if body.forward_to is not None:
        try:
            forward_to = check_recipient(body.forward_to)
        except ValueError as exc:
            raise _invalid(f"转给谁：{exc}，不转就留空") from None
    source_dir = await _source_dir(body.source_dir) if body.source_dir is not None else None
    service = _service(request)
    async with request.app.state.config_lock:
        try:
            changed = await service.repo.update(base_id, name=name, folder=folder, source_dir=source_dir)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"已经有叫「{name}」的资料库了") from None
        if forward_to is not None:
            changed = await service.gaps.set_forward_to(base_id, forward_to) or changed
        if not changed:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        await service.refresh()
    return await _view(service.bases[base_id], service.groups)


@router.delete("/knowledge/{base_id}")
async def delete_base(request: Request, base_id: BaseId) -> dict[str, bool]:
    service = _service(request)
    async with request.app.state.config_lock:
        if not await service.repo.delete(base_id):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        await service.refresh()
    return {"ok": True}


@router.post("/knowledge/{base_id}/reload")
async def reload_base(request: Request, base_id: BaseId) -> dict[str, Any]:
    """「重新载入」: read the folder again (after the docs changed) and embed new or changed sections."""
    service = _service(request)
    loaded = await service.reload(base_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return await _view(loaded, service.groups)


@router.put("/knowledge/groups")
async def bind_group(request: Request, body: Binding) -> dict[str, Any]:
    service = _service(request)
    if body.group_key not in await group_names(request.app.state):
        raise HTTPException(status_code=404, detail="没有这个群")
    async with request.app.state.config_lock:
        if body.base_id is not None and body.base_id not in service.bases:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        await service.repo.bind(body.group_key, body.base_id)
        await service.refresh()
    return {"group_key": body.group_key, "base_id": body.base_id}


@router.post("/knowledge/{base_id}/ask")
async def ask(request: Request, base_id: BaseId, body: Ask) -> dict[str, Any]:
    """「试问」: what retrieval finds for `question` and how each search ranked it. No chat model is
    called; the sorting step (keywords, the pasted error) is left out, so this is the question alone."""
    service = _service(request)
    loaded = service.bases.get(base_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if not loaded.sections:
        raise _invalid(loaded.problem or "这个资料库还没有载入")
    found = await service.retrieve(loaded, body.question.strip())
    titles = {s.id: s.title for s in loaded.sections}
    return {
        "found": found.found,
        "best": found.best,
        "reranked": found.reranked,
        "degraded": found.degraded,
        "took_ms": round(found.took_ms),
        "sections": [
            {"id": s.id, "title": s.title, "file": s.file, "score": score, "text": s.text()}
            for s, score in found.sections
        ],
        "steps": {
            name: [{"id": i, "title": titles.get(i, i), "score": score} for i, score in rows]
            for name, rows in found.steps.items()
        },
    }


@router.get("/knowledge/{base_id}/gaps")
async def gaps(request: Request, base_id: BaseId, status: str = "open") -> dict[str, Any]:
    """Questions the docs did not answer (`status`: open, answered, ignored or all), and the answers added."""
    service = _service(request)
    if base_id not in service.bases:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if status not in (*STATUSES, "all"):
        raise _invalid("状态只能是 open、answered、ignored 或 all")
    names = await group_names(request.app.state)
    rows = await service.gaps.gaps(base_id, status=None if status == "all" else status)
    notes = await service.gaps.notes(base_id)
    return {
        "gaps": [row | {"group_name": names.get(row["group_key"], "")} for row in rows],
        "notes": [{"id": n.id, "title": n.title, "body": n.body, "created_at": n.created_at} for n in notes],
    }


async def _gap(request: Request, gap_id: int) -> dict[str, Any]:
    gap = await _service(request).gaps.gap(gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="没有这个问题")
    return gap


@router.post("/knowledge/gaps/{gap_id}/answer")
async def answer_gap(request: Request, gap_id: GapId, body: NoteBody) -> dict[str, Any]:
    """「补回答」: the answer becomes a section of the base, searched like the docs; the gap is answered."""
    gap = await _gap(request, gap_id)
    title, text = _name_text(body.title, NOTE_TITLE_MAX), body.body.strip()
    if has_bad_chars(text.replace("\n", "")):
        raise _invalid("回答里不能有控制字符")
    service = _service(request)
    async with request.app.state.config_lock:
        note_id = await service.gaps.add_note(gap["base_id"], title, text)
        await service.gaps.set_status(gap_id, "answered", note_id)
    await service.reload(gap["base_id"])
    return {"id": gap_id, "status": "answered", "note_id": note_id}


@router.post("/knowledge/gaps/{gap_id}/{status}")
async def set_gap_status(request: Request, gap_id: GapId, status: str) -> dict[str, Any]:
    """「忽略」 (ignored) or back to open."""
    if status not in ("ignored", "open"):
        raise HTTPException(status_code=404, detail="没有这个操作")
    await _gap(request, gap_id)
    await _service(request).gaps.set_status(gap_id, status)
    return {"id": gap_id, "status": status}


@router.delete("/knowledge/notes/{note_id}")
async def delete_note(request: Request, note_id: GapId) -> dict[str, bool]:
    """Remove an added answer; the question it answered is open again."""
    service = _service(request)
    base_id = await service.gaps.delete_note(note_id)
    if base_id is None:
        raise HTTPException(status_code=404, detail="没有这条补充")
    await service.reload(base_id)
    return {"ok": True}


def _name_text(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if not value or len(value) > limit or has_bad_chars(value):
        raise _invalid(f"标题不能是空的，最多 {limit} 个字")
    return value
