"""伙伴模式 in the console: companions that live only on the desk, bringing one in from elsewhere (a backup
from another app), and her 回忆档案.

POST /api/companions/preview reads the uploaded files and lets a model draft the persona and the points to
remember; nothing is kept. POST /api/companions keeps what the user checked: a new companion (an account on
the DESK platform: no connection, nothing to log in to) or more for a bot that exists. Both take the same
draft, so a companion made from nothing is the same call with no points and no records.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.registry import DESK
from nest.api.auth import require_token
from nest.api.routes_bots import _aliases, _name
from nest.api.text import has_bad_chars
from nest.core import companion_import as importing
from nest.core.desk import chat_of, chosen_chats, open_chat
from nest.core.keepsakes import BODY_MAX, RECORDS_MAX, TITLE_MAX
from nest.core.memory import CONTENT_MAX, place_key
from nest.core.routines import parse_when
from nest.llm.base import ProviderError
from nest.llm.runtime import ProviderUnavailable
from nest.storage.accounts import account_id as make_account_id
from nest.storage.accounts import upsert_account
from nest.storage.bots import UNSET, NameTaken
from nest.storage.memories import MemoryStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

KeepsakeId = Annotated[int, Path(ge=1, le=2**63 - 1)]
NO_BOT = "没有这个 bot"
UPLOAD_B64_MAX = 3_000_000  # characters of one base64 file (about 2.2 MB)


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    data: Annotated[str, Field(max_length=UPLOAD_B64_MAX)]  # base64


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    name: Annotated[str, Field(max_length=40)] = ""
    bot_id: str | None = None  # brought to a companion that exists: compared with what she has
    files: Annotated[list[Upload], Field(min_length=1, max_length=importing.FILES_MAX)]


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["like", "fact", "promise"]
    content: Annotated[str, Field(min_length=1, max_length=CONTENT_MAX)]
    due: str = ""
    same: bool = False  # as the preview marked it: not read


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)]
    body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)]
    source: Annotated[str, Field(max_length=200)] = ""
    replace: int | None = None  # in place of this record of hers (the same title, another text)


class Todo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(min_length=1, max_length=importing.TODO_CHARS)]
    due: Annotated[str, Field(max_length=16)] = ""  # a reminder at this time; "": a task for her to do now


class Companion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str | None = None  # None: a new companion, only on the desk
    name: str = ""
    aliases: list[str] = []
    provider_id: str | None = None
    persona: Annotated[str, Field(max_length=importing.PERSONA_MAX)] = ""
    examples: Annotated[str, Field(max_length=importing.EXAMPLES_MAX)] = ""
    user_name: Annotated[str, Field(max_length=20)] = ""
    points: Annotated[list[Point], Field(max_length=importing.POINTS_MAX)] = []
    records: Annotated[list[Record], Field(max_length=RECORDS_MAX)] = []
    todos: Annotated[list[Todo], Field(max_length=importing.TODOS_MAX)] = []  # what the user ticked


class RecordEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=TITLE_MAX)] | None = None
    body: Annotated[str, Field(min_length=1, max_length=BODY_MAX)] | None = None
    keep: Literal[True] | None = None  # a draft of hers, read and kept by the user


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _decode(upload: Upload) -> tuple[str, bytes]:
    try:
        return upload.name, base64.b64decode(upload.data, validate=True)
    except (binascii.Error, ValueError):
        raise _invalid(f"{upload.name} 没有传完整，再选一次试试") from None


@router.post("/companions/preview")
async def preview(request: Request, body: PreviewRequest) -> dict[str, Any]:
    """The uploaded files, read and drafted into a persona and points; nothing is kept."""
    state = request.app.state
    runtime = state.providers.get(body.provider_id)
    if runtime is None:
        raise HTTPException(status_code=400, detail="没有这个模型")
    try:
        docs = importing.read_upload([_decode(f) for f in body.files])
    except importing.ImportProblem as exc:
        raise _invalid(str(exc)) from None
    messages = importing.build_prompt(docs, body.name.strip())
    try:
        completion, _ = await runtime.complete(messages, bot_id=None, max_tokens=importing.MAX_TOKENS)
        draft = importing.parse_draft(completion.text)
    except (ProviderUnavailable, ProviderError) as exc:
        raise HTTPException(status_code=502, detail=f"模型整理失败：{exc}") from None
    except importing.ImportProblem as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    skip = set(draft.skip)
    known = state.brain.bots.get(body.bot_id) if body.bot_id else None
    had, remembered = await _had(state, known)
    return {
        "name": draft.name or body.name.strip(),
        "aliases": draft.aliases,
        "user_name": draft.user_name,
        "persona": draft.persona,
        "examples": draft.examples,
        # 再次导入: what she already has is marked, and not kept again unless the user says so.
        "points": [
            p | {"same": any(importing.same_text(p["content"], m) for m in remembered)} for p in draft.points
        ],
        "records": [
            {
                "title": importing.title_of(doc),
                "body": doc.text[:BODY_MAX],
                "source": doc.name,
                **_compared(had, importing.title_of(doc), doc.text[:BODY_MAX], doc.name not in skip),
            }
            for doc in docs
        ],
        "todos": draft.todos,
        "cut": [doc.name for doc in docs if len(doc.text) > BODY_MAX],
    }


async def _had(state: Any, bot: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """What a companion that exists has already: her records, and what she remembers of the user."""
    if bot is None:
        return [], []
    chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
    remembered = await MemoryStore(state.db).of_place(place_key(chat)) if chat is not None else []
    return await state.keepsakes.store.of_bot(bot.id), [m["content"] for m in remembered]


def _compared(had: list[dict[str, Any]], title: str, body: str, keep: bool) -> dict[str, Any]:
    """A record beside hers: "same" word for word (not kept again), or one of hers with this title and another
    text (`other`: the user chooses)."""
    if any(importing.same_text(r["body"], body) for r in had):
        return {"keep": False, "same": True, "other": None}
    other = next((r["id"] for r in had if r["title"] == title), None)
    return {"keep": keep, "same": False, "other": other}


async def _persona_name(state: Any, name: str) -> str:
    taken = {p["name"] for p in await state.repo.personas()}
    base = f"{name}@桌面"
    return next(n for n in (base, *(f"{base}{i}" for i in range(2, 100))) if n not in taken)


def _check_text(value: str, what: str) -> str:
    value = value.replace("\r\n", "\n").strip()
    if has_bad_chars(value, allow="\n\t"):
        raise _invalid(f"{what}里有不能保存的控制字符")
    return value


@router.post("/companions", status_code=201)
async def keep(request: Request, body: Companion) -> dict[str, Any]:
    """Keep a companion: made new on the desk, or added to a bot that exists (her chat with the user here)."""
    return await make_companion(request.app.state, body)


async def make_companion(state: Any, body: Companion) -> dict[str, Any]:
    persona = _check_text(body.persona, "人设")
    examples = _check_text(body.examples, "说话示例")
    user_name = body.user_name.strip()
    if body.provider_id is not None and await state.repo.get_provider(body.provider_id) is None:
        raise HTTPException(status_code=400, detail="没有这个模型")
    for point in body.points:
        if point.due and not _is_day(point.due):
            raise _invalid("约定的日子要写成 2026-01-31 这样")
    records = [
        (r.title.strip(), _check_text(r.body, "回忆"), r.source.strip(), r.replace) for r in body.records
    ]
    todos = [(t.text.strip(), parse_when(t.due) if t.due else None) for t in body.todos]
    if any(t.due and when is None for t, (_, when) in zip(body.todos, todos, strict=True)):
        raise _invalid("待办的时间要写成 2026-01-31 09:00 这样")
    async with state.config_lock:
        if body.bot_id is None:
            name = _name(body.name)
            aliases = [a for a in _aliases(body.aliases) if a != name]
            self_id = "c" + secrets.token_hex(4)
            account = make_account_id(DESK, self_id)
            await upsert_account(state.db, platform=DESK, self_id=self_id, name=name, http_url="", ws_url="")
            # Nothing she says goes anywhere but here: she may talk as soon as she has a model.
            mode = "live" if body.provider_id else "observe"
            await state.repo.upsert_bot(
                account_id=account, name=name, aliases=aliases, provider_id=body.provider_id, mode=mode
            )
            await state.brain.load()
            bot = state.brain.bots[account]
            chat_id = None
        else:
            bot = state.brain.bots.get(body.bot_id)
            if bot is None:
                raise HTTPException(status_code=404, detail=NO_BOT)
            chat = chat_of(bot, (await chosen_chats(state.db)).get(bot.id))
            chat_id = chat.channel_id if chat else None
        persona_id = None
        if persona:
            try:
                persona_id = await state.repo.create_persona(await _persona_name(state, bot.name), persona)
            except NameTaken:
                raise HTTPException(status_code=409, detail="人设名字刚被占用了，再试一次") from None
            if examples:
                await state.repo.update_persona(persona_id, examples=examples)
        remember = bool(body.points)
        if chat_id is None:
            chat_id = await open_chat(state.repo, state.store, bot, persona_id=persona_id, remember=remember)
        else:
            await state.repo.set_channel(
                bot.id,
                chat_id,
                persona_id=persona_id if persona_id is not None else UNSET,
                remember=True if remember else None,
            )
        await state.brain.load()
        bot = state.brain.bots[bot.id]
        channel = bot.channels[chat_id]
        now = time.time()
        memories = MemoryStore(state.db)
        had, remembered = await _had(state, bot)
        for point in body.points:
            if any(importing.same_text(point.content, m) for m in remembered):
                continue  # she remembers it already
            await memories.add(
                place_key(channel),
                channel.channel.id,
                user_name or "我",
                point.kind,
                point.content.strip(),
                bot_id=bot.id if point.kind == "promise" else None,
                due=point.due or None,
                source="manual",
                at=now,
            )
        theirs = {r["id"]: r for r in had}
        for title, text, source, replace in records:
            if replace is not None and replace in theirs:
                await state.keepsakes.store.update(replace, title=title, body=text)
            elif not any(importing.same_text(r["body"], text) for r in had):
                await state.keepsakes.store.add(bot.id, title, text, source=source)
        state.keepsakes.changed(bot.id)
    problems = await _take_up(state, bot, channel, todos)
    log.info(
        "伙伴 %s：人设 %s，要点 %d 条，回忆 %d 份",
        bot.name,
        "已建" if persona_id else "没改",
        len(body.points),
        len(records),
    )
    state.broadcaster.publish("bots", {"bot_id": bot.id})
    return {"bot_id": bot.id, "channel_id": chat_id, "persona_id": persona_id, "problems": problems}


async def _take_up(state: Any, bot: Any, channel: Any, todos: list[tuple[str, float | None]]) -> list[str]:
    """The 待办 the user ticked: a reminder when it has a time, else a background task of hers. What could not
    be set, in words."""
    problems = []
    for text, when in todos:
        if when is None:
            started, why = await state.tasks.begin(bot, channel, text, text, source="import")
            if started is None:
                problems.append(f"「{text}」没开成任务：{why}")
        elif when < time.time():
            problems.append(f"「{text}」的时间已经过去了，没设提醒")
        else:
            await state.reminders.store.add(
                bot_id=bot.id, channel_id=channel.channel_id, text=text, due_at=when, source="import"
            )
    if todos:
        state.broadcaster.publish("reminders", {"bot_id": bot.id})
    return problems


def _is_day(value: str) -> bool:
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# -- 回忆档案 -------------------------------------------------------------------------------------------


@router.get("/bots/{bot_id}/keepsakes")
async def keepsakes(request: Request, bot_id: str) -> list[dict[str, Any]]:
    state = request.app.state
    if bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail=NO_BOT)
    return await state.keepsakes.store.of_bot(bot_id)


@router.post("/bots/{bot_id}/keepsakes", status_code=201)
async def add_keepsake(request: Request, bot_id: str, body: Record) -> dict[str, Any]:
    state = request.app.state
    if bot_id not in state.brain.bots:
        raise HTTPException(status_code=404, detail=NO_BOT)
    async with state.config_lock:
        if len(await state.keepsakes.store.of_bot(bot_id)) >= RECORDS_MAX:
            raise HTTPException(status_code=409, detail=f"回忆档案最多 {RECORDS_MAX} 份")
        new = await state.keepsakes.store.add(
            bot_id, body.title.strip(), _check_text(body.body, "回忆"), source=body.source.strip()
        )
        state.keepsakes.changed(bot_id)
    return {"id": new}


async def _owned(request: Request, keepsake_id: int) -> dict[str, Any]:
    found = await request.app.state.keepsakes.store.get(keepsake_id)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这份回忆")
    return found


@router.patch("/keepsakes/{keepsake_id}")
async def edit_keepsake(request: Request, keepsake_id: KeepsakeId, body: RecordEdit) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        found = await _owned(request, keepsake_id)
        await state.keepsakes.store.update(
            keepsake_id,
            title=body.title.strip() if body.title is not None else None,
            body=_check_text(body.body, "回忆") if body.body is not None else None,
            kept=bool(body.keep),
        )
        state.keepsakes.changed(found["bot_id"])
    if found["draft"]:
        state.broadcaster.publish("bots", {"bot_id": found["bot_id"]})  # the chat list counts her drafts
    return {"ok": True}


@router.delete("/keepsakes/{keepsake_id}")
async def delete_keepsake(request: Request, keepsake_id: KeepsakeId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        found = await _owned(request, keepsake_id)
        await state.keepsakes.store.delete(keepsake_id)
        state.keepsakes.changed(found["bot_id"])
    if found["draft"]:
        state.broadcaster.publish("bots", {"bot_id": found["bot_id"]})  # the chat list counts her drafts
    return {"ok": True}
