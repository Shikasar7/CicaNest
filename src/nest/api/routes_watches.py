"""监控 (watches): list, add, edit, delete, check now, the check history, and where each bot can push.

Writes hold app.state.config_lock from validation through the write (nothing under it waits on the
network); 立即检查 runs outside it, since it fetches from the internet. Validation errors are 422 with one
plain Chinese sentence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from nest.adapters.registry import can, check_user
from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.watches import KINDS, MODES, TARGET_KINDS, Watch
from nest.watch.watcher import Busy

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

NAME_LIMIT = 40
URL_LIMIT = 1000
SELECTOR_LIMIT = 300
INSTRUCTIONS_LIMIT = 500
INTERVAL_MIN, INTERVAL_MAX = 5, 1440
WEEK = 7 * 86400.0
NOT_FOUND = "没有这个监控"
NO_COUNTS = {"runs_7d": 0, "changes_7d": 0, "errors_7d": 0}
NULLABLE = ("threshold", "provider_id")  # sent as null in a PATCH: removed, not "not sent"

Interval = Annotated[int, Field(strict=True)]
Price = Annotated[float, Field(strict=True, allow_inf_nan=False)]
WatchId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class WatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    url: str
    selector: str = ""
    threshold: Price | None = None
    interval_minutes: Interval = 60
    bot_id: str
    target_kind: str
    target_id: str
    provider_id: str | None = None
    instructions: str = ""
    use_proxy: bool = False
    mode: str = "preview"
    enabled: bool = True


class WatchPatch(BaseModel):
    """Every field optional; null means "not sent", except threshold and provider_id, where null removes
    them (told apart with model_fields_set)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    kind: str | None = None
    url: str | None = None
    selector: str | None = None
    threshold: Price | None = None
    interval_minutes: Interval | None = None
    bot_id: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    provider_id: str | None = None
    instructions: str | None = None
    use_proxy: bool | None = None
    mode: str | None = None
    enabled: bool | None = None


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


# -- validation -------------------------------------------------------------------------------------


def _name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= NAME_LIMIT:
        raise _invalid(f"名字要填 1–{NAME_LIMIT} 个字")
    if has_bad_chars(value):
        raise _invalid("名字里不能有换行或控制字符")
    return value


def _kind(value: str) -> str:
    if value not in KINDS:
        raise _invalid("类型只能是订阅、网页变化或价格")
    return value


def _url(value: str) -> str:
    text = value.strip()
    if not text:
        raise _invalid("请填写地址")
    if len(text) > URL_LIMIT:
        raise _invalid(f"地址最多 {URL_LIMIT} 个字")
    hint = "地址要写成 http:// 或 https:// 开头的完整网址"
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 (raises on a malformed port)
    except ValueError:
        raise _invalid(hint) from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or any(c.isspace() or not c.isprintable() for c in text)
    ):
        raise _invalid(hint)
    if "@" in parts.netloc:
        raise _invalid("地址里不要带用户名或密码")
    return text


def _selector(value: str) -> str:
    text = value.strip()
    if len(text) > SELECTOR_LIMIT:
        raise _invalid(f"选择器最多 {SELECTOR_LIMIT} 个字")
    if has_bad_chars(text):
        raise _invalid("选择器里不能有换行或控制字符")
    if text:
        try:
            LexborHTMLParser("<p></p>").css(text)
        except SelectolaxError:
            raise _invalid("选择器写得不对，可以在浏览器里右键元素 → 检查 → 复制选择器") from None
    return text


def _interval(value: int) -> int:
    if not INTERVAL_MIN <= value <= INTERVAL_MAX:
        raise _invalid(f"检查间隔要在 {INTERVAL_MIN}–{INTERVAL_MAX} 分钟之间")
    return value


def _instructions(value: str) -> str:
    text = value.strip()
    if len(text) > INSTRUCTIONS_LIMIT:
        raise _invalid(f"额外要求最多 {INSTRUCTIONS_LIMIT} 字")
    if has_bad_chars(text, allow="\n\r\t"):
        raise _invalid("额外要求里有不能保存的字符")
    return text


def _mode(value: str) -> str:
    if value not in MODES:
        raise _invalid("模式只能是试运行或推送")
    return value


async def _target(state: Any, bot_id: str, target_kind: str, target_id: str) -> dict[str, str]:
    bot = state.brain.bots.get(bot_id)
    if bot is None:
        raise _invalid("没有这个 bot")
    if target_kind not in TARGET_KINDS:
        raise _invalid("推送到只能选群或私聊")
    if not can(bot.account_id, "proactive"):
        raise _invalid(f"{bot.name} 的平台不能主动发消息")
    target = target_id.strip()
    if target_kind == "group":
        channels = await state.store.channels()
        if not any(
            c["account_id"] == bot.account_id and c["kind"] == "group" and c["external_id"] == target
            for c in channels
        ):
            raise _invalid(f"{bot.name} 的号不在这个群里")
    else:
        try:
            target = check_user(bot.account_id.partition(":")[0], target)
        except ValueError as exc:
            raise _invalid(str(exc)) from None
    return {"bot_id": bot.id, "target_kind": target_kind, "target_id": target}


async def _columns(state: Any, body: WatchCreate | WatchPatch, current: Watch | None) -> dict[str, Any]:
    """The columns to write: the sent fields, checked on their own and together with the rest of the watch,
    plus what follows from them (selector/threshold cleared for kinds without them, a fresh baseline after
    the source changed, an early next check after enabling or a new interval)."""
    data = body.model_dump()
    if current is None:
        sent = set(WatchCreate.model_fields)
    else:
        sent = {key for key in body.model_fields_set if data[key] is not None or key in NULLABLE}
        if not sent:
            raise _invalid("没有要改的内容")

    def value(key: str) -> Any:
        return data[key] if key in sent or current is None else getattr(current, key)

    columns: dict[str, Any] = {}
    if "name" in sent:
        columns["name"] = _name(data["name"])
    kind = _kind(data["kind"]) if "kind" in sent else value("kind")
    if "kind" in sent:
        columns["kind"] = kind
    if "url" in sent:
        columns["url"] = _url(data["url"])
    if "selector" in sent or "kind" in sent:
        selector = "" if kind == "feed" else _selector(value("selector"))
        if kind == "price" and not selector:
            raise _invalid("价格监控要填选择器（价格在网页上的位置）")
        columns["selector"] = selector
    if "threshold" in sent or "kind" in sent:
        threshold = value("threshold")
        if kind != "price":
            if "threshold" in sent and threshold is not None:
                raise _invalid("只有价格监控能设「低于这个价格才推送」")
            threshold = None
        elif threshold is not None and not threshold > 0:
            raise _invalid("提醒价格要大于 0")
        columns["threshold"] = threshold
    if "interval_minutes" in sent:
        columns["interval_minutes"] = _interval(data["interval_minutes"])
    if sent & {"bot_id", "target_kind", "target_id"}:
        columns |= await _target(state, value("bot_id"), value("target_kind"), value("target_id"))
    if "provider_id" in sent:
        provider_id = data["provider_id"]
        if provider_id is not None and (
            has_bad_chars(provider_id) or await state.repo.get_provider(provider_id) is None
        ):
            raise _invalid("没有这个模型")
        columns["provider_id"] = provider_id
    if "instructions" in sent:
        columns["instructions"] = _instructions(data["instructions"])
    if "use_proxy" in sent:
        columns["use_proxy"] = data["use_proxy"]
    if "mode" in sent:
        columns["mode"] = _mode(data["mode"])
    if "enabled" in sent:
        columns["enabled"] = data["enabled"]

    now = time.time()
    if current is None:
        columns |= {"state": {}, "next_run_at": now}
        return columns
    source = (
        columns.get("kind", current.kind),
        columns.get("url", current.url),
        columns.get("selector", current.selector),
    )
    if source != current.source:
        columns["state"] = {}  # the next check is a new baseline
    enabled = columns.get("enabled") is True and not current.enabled
    if enabled or columns.get("interval_minutes", current.interval_minutes) != current.interval_minutes:
        columns["next_run_at"] = now
    return columns


# -- views --------------------------------------------------------------------------------------------


async def _views(state: Any, watches: list[Watch]) -> list[dict[str, Any]]:
    store = state.watcher.store
    group_names = {
        (c["account_id"], c["external_id"]): c["name"]
        for c in await state.store.channels()
        if c["kind"] == "group"
    }
    last = await store.latest_runs()
    said = await store.latest_runs(with_message=True)
    counts = await store.counts(time.time() - WEEK)
    views = []
    for watch in watches:
        bot = state.brain.bots.get(watch.bot_id)
        account = bot.account_id if bot else watch.bot_id
        if watch.target_kind == "group":
            target_name = group_names.get((account, watch.target_id)) or f"群 {watch.target_id}"
        else:
            target_name = f"私聊 {watch.target_id}"
        views.append(
            {
                "id": watch.id,
                "name": watch.name,
                "kind": watch.kind,
                "url": watch.url,
                "selector": watch.selector,
                "threshold": watch.threshold,
                "interval_minutes": watch.interval_minutes,
                "bot_id": watch.bot_id,
                "bot_name": bot.name if bot else watch.bot_id,
                "target_kind": watch.target_kind,
                "target_id": watch.target_id,
                "target_name": target_name,
                "provider_id": watch.provider_id,
                "instructions": watch.instructions,
                "use_proxy": watch.use_proxy,
                "mode": watch.mode,
                "enabled": watch.enabled,
                "last_run_at": watch.last_run_at,
                "next_run_at": watch.next_run_at,
                "running": state.watcher.running(watch.id),
                "last_run": last.get(watch.id),
                # The newest run with a message (pushed or would-be), for the bubble.
                "last_message": said.get(watch.id),
                "counts": counts.get(watch.id, NO_COUNTS),
            }
        )
    return views


async def _view(state: Any, watch_id: int) -> dict[str, Any]:
    watch = await state.watcher.store.get(watch_id)
    if watch is None:
        raise _not_found()
    return (await _views(state, [watch]))[0]


# -- routes -------------------------------------------------------------------------------------------


@router.get("/watches")
async def list_watches(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    return await _views(state, await state.watcher.store.all())


@router.get("/watches/targets")
async def targets(request: Request) -> list[dict[str, Any]]:
    """Every bot and the QQ groups its account is in (as stored), for the 推送给 picker."""
    state = request.app.state
    channels = await state.store.channels()
    result = []
    for bot in state.brain.bots.values():
        groups = [
            {"group_id": c["external_id"], "name": c["name"]}
            for c in channels
            if c["account_id"] == bot.account_id and c["kind"] == "group"
        ]
        groups.sort(key=lambda g: (g["name"] or "~", g["group_id"]))
        result.append({"bot_id": bot.id, "bot_name": bot.name, "groups": groups})
    return result


@router.post("/watches", status_code=201)
async def create_watch(request: Request, body: WatchCreate) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        columns = await _columns(state, body, None)
        watch_id = await state.watcher.store.create(columns)
        log.info("添加了监控「%s」", columns["name"])
    state.watcher.wake()
    return await _view(state, watch_id)


@router.patch("/watches/{watch_id}")
async def update_watch(request: Request, watch_id: WatchId, body: WatchPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        current = await state.watcher.store.get(watch_id)
        if current is None:
            raise _not_found()
        columns = await _columns(state, body, current)
        await state.watcher.store.update(watch_id, columns)
    state.watcher.wake()
    return await _view(state, watch_id)


@router.delete("/watches/{watch_id}")
async def delete_watch(request: Request, watch_id: WatchId) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        current = await state.watcher.store.get(watch_id)
        if current is None or not await state.watcher.store.delete(watch_id):
            raise _not_found()
        log.info("删除了监控「%s」", current.name)
    return {"ok": True}


@router.post("/watches/{watch_id}/run")
async def run_watch(request: Request, watch_id: WatchId) -> dict[str, Any]:
    """Check now, whatever the schedule or switch; the mode still decides whether anything is sent."""
    state = request.app.state
    watch = await state.watcher.store.get(watch_id)
    if watch is None:
        raise _not_found()
    try:
        task = state.watcher.run_now(watch)
    except Busy:
        raise HTTPException(status_code=409, detail="正在检查，稍等") from None
    run = await asyncio.shield(task)  # a closed console tab does not cancel the check
    if run is None:
        raise _not_found()  # deleted while it was being checked
    return run


@router.get("/watches/{watch_id}/runs")
async def watch_runs(
    request: Request, watch_id: WatchId, limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    """Newest first."""
    state = request.app.state
    if await state.watcher.store.get(watch_id) is None:
        raise _not_found()
    return await state.watcher.store.runs(watch_id, limit=limit)
