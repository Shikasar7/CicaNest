"""桌面群: groups of the user and several companions, in the desk chat only."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.api.auth import require_token
from nest.api.routes_desk import _body
from nest.api.text import has_bad_chars
from nest.core.desk import DEFAULT_NAME, DESK_PEER, MAX_CHARS, chat_of, chosen_chats, from_app
from nest.core.desk_files import MAX_FILES, file_segment
from nest.core.desk_media import MAX_PICTURE, MAX_PICTURES
from nest.core.groups import NAME_MAX, GroupError, group_ref
from nest.core.workspace import FILE_MAX, WorkspaceError, received_name
from nest.storage.bots import BotConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desk/groups", dependencies=[Depends(require_token)])


class NewGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=NAME_MAX * 2)]
    bot_ids: Annotated[list[str], Field(max_length=20)]


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=NAME_MAX * 2)] | None = None
    bot_ids: Annotated[list[str], Field(max_length=20)] | None = None
    remember: bool | None = None  # 长期记忆
    slang: bool | None = None  # 学黑话、用黑话
    activity: dict[str, str] | None = (
        None  # bot id -> "active" (话多) | "occasional" (普通) | "mentions" (安静)
    )


class SentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=64)]  # from POST /{group_id}/files
    title: Annotated[str, Field(min_length=1, max_length=255)]  # what it is called


class Words(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=MAX_CHARS)] = ""
    sticker_id: int | None = None
    pictures: Annotated[list[str], Field(max_length=MAX_PICTURES)] = []  # from POST /{group_id}/pictures
    files: Annotated[list[SentFile], Field(max_length=MAX_FILES)] = []


def _refused(exc: GroupError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


async def _with_last(request: Request, group: dict[str, Any]) -> dict[str, Any]:
    view = group["view"]
    rows = await request.app.state.store.recent(view, limit=30) if view is not None else []
    last = next((r for r in reversed(rows) if not r["recalled_at"]), None)
    group["last"] = (
        {
            "text": last["text"],
            "from_self": last["from_self"],
            "sender_name": last["sender_name"],
            "sent_at": last["sent_at"],
            "sticker": any(s.get("type") == "sticker" for s in last.get("segments") or []),
        }
        if last
        else None
    )
    return group


@router.get("")
async def groups(request: Request) -> list[dict[str, Any]]:
    return [await _with_last(request, g) for g in await request.app.state.groups.groups()]


@router.post("", status_code=201)
async def create(request: Request, body: NewGroup) -> dict[str, Any]:
    try:
        return await _with_last(request, await request.app.state.groups.create(body.name, body.bot_ids))
    except GroupError as exc:
        raise _refused(exc) from None


@router.patch("/{group_id}")
async def update(request: Request, group_id: int, body: GroupPatch) -> dict[str, Any]:
    try:
        group = await request.app.state.groups.update(
            group_id,
            name=body.name,
            members=body.bot_ids,
            activity=body.activity,
            remember=body.remember,
            slang=body.slang,
        )
    except GroupError as exc:
        raise _refused(exc) from None
    return await _with_last(request, group)


@router.delete("/{group_id}")
async def dissolve(request: Request, group_id: int) -> dict[str, Any]:
    try:
        await request.app.state.groups.dissolve(group_id)
    except GroupError as exc:
        raise _refused(exc) from None
    return {"ok": True}


@router.delete("/{group_id}/messages/{message_id}")
async def take_back(request: Request, group_id: int, message_id: int) -> dict[str, Any]:
    """撤回 something you said in the group, from every companion's copy. `message_id`: in the window's."""
    state = request.app.state
    try:
        group = await state.groups.group(group_id)
    except GroupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    row = await state.store.message(message_id)
    if row is None or row["channel_id"] != group["view"]:
        raise HTTPException(status_code=404, detail="没有这条消息")
    if row["sender_id"] != DESK_PEER or not from_app(row["external_id"]):
        raise HTTPException(status_code=409, detail="只能撤回你自己说的")
    if row["recalled_at"] is None:
        refs = [group_ref(bot, group_id) for bot, _ in await state.groups.members_of(group_id)]
        await state.desk.take_back(refs, row["external_id"])
    return {"ok": True}


async def _members(request: Request, group_id: int) -> tuple[dict[str, Any], list[tuple[BotConfig, int]]]:
    """The group and who is in it, when somebody there would answer."""
    state = request.app.state
    try:
        group = await state.groups.group(group_id)
    except GroupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    members = await state.groups.members_of(group_id)
    if not any(bot.mode == "live" for bot, _ in members):
        raise HTTPException(status_code=409, detail="群里的伙伴现在都不会回话")
    return group, members


def _title(name: str) -> str:
    try:
        return received_name(name)
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/{group_id}/pictures", status_code=202)
async def keep_picture(request: Request, group_id: int) -> dict[str, Any]:
    """Keep a picture (the raw request body) for a message that is sent with it afterwards."""
    await _members(request, group_id)
    data = await _body(request, MAX_PICTURE, "图片太大了，10 MB 以内")
    name = await asyncio.to_thread(request.app.state.desk_media.save_picture, data)
    if name is None:
        raise HTTPException(status_code=415, detail="只能发 PNG、JPEG、GIF 或 WebP 图片")
    return {"ok": True, "name": name}


@router.post("/{group_id}/files", status_code=202)
async def keep_file(
    request: Request, group_id: int, name: Annotated[str, Query(min_length=1, max_length=255)]
) -> dict[str, Any]:
    """Keep a file (the raw request body) for a message that is sent with it afterwards."""
    await _members(request, group_id)
    title = _title(name)
    data = await _body(request, FILE_MAX, f"文件不能超过 {FILE_MAX // 1024**2} MB")
    kept = await asyncio.to_thread(request.app.state.desk_media.save_file, data)
    return {"ok": True, "name": kept, "title": title}


async def _hand_out(state: Any, members: list[tuple[BotConfig, int]], title: str, data: bytes) -> list[str]:
    """Into 收到的 of everyone in the group: who does not have it, and why."""
    problems = []
    for bot, _ in members:
        try:
            await asyncio.to_thread(state.workspace.receive, bot.id, title, data)
        except (WorkspaceError, OSError) as exc:
            log.info("群里的文件没放进 %s 的文件夹：%s", bot.name, exc)
            why = str(exc) if isinstance(exc, WorkspaceError) else "没写成"
            problems.append(f"「{title}」没放进{bot.name}的文件夹（{why}）")
            continue
        state.broadcaster.publish("workspace", {"bot_id": bot.id})
    return problems


@router.post("/{group_id}/messages", status_code=202)
async def say(request: Request, group_id: int, body: Words) -> dict[str, Any]:
    """Say something in the group, with pictures and files if you like, or send a sticker. The companions'
    answers arrive as "message" events of the group's channels. `problems`: whose folder a file did not get
    into."""
    state = request.app.state
    group, members = await _members(request, group_id)
    text = body.text.replace("\r\n", "\n").strip()
    sticker: tuple[int, str] | None = None
    if body.sticker_id is not None:
        if body.pictures or body.files:
            raise HTTPException(status_code=422, detail="表情和图片、文件分开发吧")
        row = await state.stickers.store.get(body.sticker_id)
        if row is None:
            raise HTTPException(status_code=404, detail="没有这张表情")
        sticker = (row["id"], state.stickers.describe_choice(row) if row["status"] == "ready" else "表情")
        text = ""
    elif not text and not body.pictures and not body.files:
        raise HTTPException(status_code=422, detail="说点什么吧")
    elif has_bad_chars(text, allow="\n\t"):
        raise HTTPException(status_code=422, detail="里面有不能发的控制字符")
    # What the companions call you: the name you have in the first one's chat with you.
    chosen = await chosen_chats(state.db)
    name = DEFAULT_NAME
    for bot, _ in members:
        chat = chat_of(bot, chosen.get(bot.id))
        if chat is not None:
            name = await state.desk.name_of(chat)
            break
    media = state.desk_media
    pictures = list(dict.fromkeys(body.pictures))
    kept = {f.name: _title(f.title) for f in body.files}
    if not all(media.picture(n) for n in pictures) or not all(media.file(n) for n in kept):
        raise HTTPException(status_code=404, detail="有张图或文件已经不在了，重新放一次")
    files, problems = [], []
    for stored, title in kept.items():
        data = await asyncio.to_thread(media.read, stored)
        if data is None:
            raise HTTPException(status_code=404, detail="有张图或文件已经不在了，重新放一次")
        files.append(await asyncio.to_thread(file_segment, title, data))
        problems += await _hand_out(state, members, title, data)
    refs = [group_ref(bot, group_id) for bot, _ in members]
    message_id = await state.desk.say_group(refs, name, text, sticker, pictures, files)
    for stored in kept:
        await asyncio.to_thread(media.remove, stored)
    return {"ok": True, "channel_id": group["view"], "message_id": message_id, "problems": problems}
