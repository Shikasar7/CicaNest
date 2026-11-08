"""How each account is reached (the 系统 page's 「账号连接」), and the platforms nest can run. A QQ account
has its OneBot HTTP and WebSocket addresses and their access tokens; the other platforms a form of their own,
from nest.adapters.registry. Testing a connection before saving, and saving, which reconnects the account.

Secrets live in .env (under the account's refs) and are never sent back to the console, only whether one is
set. A stored QQ token is only ever sent to the host and port it was saved for: an address moved elsewhere
needs its token typed again, so a typo or a malicious edit cannot carry it to another machine.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from nest.adapters.base import Probed
from nest.adapters.onebot11 import probe
from nest.adapters.registry import PLATFORMS, PlatformSpec
from nest.api.auth import require_token
from nest.api.text import has_bad_chars
from nest.storage.accounts import (
    Account,
    account_id,
    get_account,
    update_config,
    update_connection,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

URL_LIMIT = 300
TOKEN_LIMIT = 500
KINDS = {"http": ("HTTP", ("http", "https")), "ws": ("WebSocket", ("ws", "wss"))}


class Connection(BaseModel):
    """Addresses: null keeps the saved one. Tokens: null keeps the saved one, "" removes it."""

    model_config = ConfigDict(extra="forbid")

    http_url: str | None = Field(default=None, max_length=URL_LIMIT)
    ws_url: str | None = Field(default=None, max_length=URL_LIMIT)
    http_token: str | None = Field(default=None, max_length=TOKEN_LIMIT)
    ws_token: str | None = Field(default=None, max_length=TOKEN_LIMIT)
    # A platform other than QQ, by field name. A secret left empty stays as saved.
    fields: dict[str, str] | None = None


class Trial(BaseModel):
    """A connection that does not exist yet."""

    model_config = ConfigDict(extra="forbid")

    platform: str
    fields: dict[str, str] = {}


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def platform_spec(key: str) -> PlatformSpec:
    spec = PLATFORMS.get(key)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"不支持这个平台：{key[:20]}")
    return spec


def check_fields(
    spec: PlatformSpec, given: Mapping[str, str], saved: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Every field of a platform's form, checked: what was sent, over what is `saved` or else the defaults."""
    known = {field.name: field for field in spec.fields}
    for name, value in given.items():
        field = known.get(name)
        if field is None:
            raise _invalid(f"{spec.label} 没有「{name[:20]}」这一项")
        if len(value) > TOKEN_LIMIT or has_bad_chars(value):
            raise _invalid(f"{field.label}里有不能保存的字符")
    values = {f.name: f.default if saved is None else saved.get(f.name, "") for f in spec.fields}
    values |= {name: value for name, value in given.items() if value or not known[name].secret}
    try:
        cleaned = spec.clean(values)
    except ValueError as exc:
        raise _invalid(str(exc)) from None
    for field in spec.fields:
        if field.required and not cleaned.get(field.name):
            raise _invalid(f"{field.label}要填")
    return cleaned


def stored_config(spec: PlatformSpec, fields: Mapping[str, str], refs: Mapping[str, str]) -> dict[str, Any]:
    """What goes into the account's row: never a secret, only the name it is kept under."""
    plain = {f.name: fields.get(f.name, "") for f in spec.fields if not f.secret and not f.identity}
    return {**plain, "refs": dict(refs)}


def _saved_fields(state: Any, spec: PlatformSpec, account: Account) -> dict[str, str]:
    saved = {f.name: str(account.config.get(f.name) or "") for f in spec.fields if not f.secret}
    saved |= {f.name: account.self_id for f in spec.fields if f.identity}
    return saved | {name: state.secrets.get(ref) for name, ref in account.refs.items()}


def _url(kind: str, value: str) -> str:
    label, schemes = KINDS[kind]
    url = value.strip()
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 (raises ValueError for a port that is not a number or out of range)
    except ValueError:
        raise _invalid(f"{label} 地址写得不对") from None
    example = "http://127.0.0.1:3010" if kind == "http" else "ws://127.0.0.1:3011/"
    if parts.scheme not in schemes or not parts.hostname or has_bad_chars(url):
        raise _invalid(f"{label} 地址要写成 {example} 这样")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid(f"{label} 地址里不能带用户名、密码或 ? # 后面的部分；令牌填在下面")
    return url.rstrip("/") if kind == "http" else url


def _endpoint(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    default = {"http": 80, "ws": 80, "https": 443, "wss": 443}.get(parts.scheme)
    return (parts.hostname or "").lower(), parts.port or default


def _ref(account: Account, kind: str) -> str:
    ref = account.http_token_ref if kind == "http" else account.ws_token_ref
    return ref or f"NEST_QQ_{account.self_id}_{kind.upper()}_TOKEN"


def _token(state: Any, account: Account, kind: str, url: str, supplied: str | None) -> str:
    """The token to use with `url`: the one supplied, or the saved one when `url` points where the saved
    address did. Refuses to send a saved token anywhere else."""
    if supplied is not None:
        if has_bad_chars(supplied):
            raise _invalid(f"{KINDS[kind][0]} 令牌里有不能保存的字符")
        return supplied.strip()
    saved_url = account.http_url if kind == "http" else account.ws_url
    if _endpoint(url) != _endpoint(saved_url):
        host, port = _endpoint(url)
        raise _invalid(
            f"{KINDS[kind][0]} 地址换到了 {host}:{port}，请把它的令牌重新填一遍（保存的令牌只发给原来的地址）"
        )
    return state.secrets.get(_ref(account, kind))


async def _account(request: Request, account_id: str) -> Account:
    account = await get_account(request.app.state.db, account_id)
    if account is None or account.platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail="没有这个账号")
    return account


async def _resolve(request: Request, account: Account, body: Connection) -> dict[str, str]:
    state = request.app.state
    http_url = _url("http", body.http_url) if body.http_url is not None else account.http_url
    ws_url = _url("ws", body.ws_url) if body.ws_url is not None else account.ws_url
    return {
        "http_url": http_url,
        "ws_url": ws_url,
        "http_token": _token(state, account, "http", http_url, body.http_token),
        "ws_token": _token(state, account, "ws", ws_url, body.ws_token),
    }


def _platform_view(state: Any, account: Account) -> dict[str, Any]:
    status = state.hub.status_of(account.id)
    spec = PLATFORMS[account.platform]
    return {
        "id": account.id,
        "platform": account.platform,
        "self_id": account.self_id,
        "name": state.hub.name_of(account),
        "fields": {
            f.name: str(account.config.get(f.name) or "")
            for f in spec.fields
            if not f.secret and not f.identity
        },
        "secrets": {name: bool(state.secrets.get(ref)) for name, ref in account.refs.items()},
        "state": status.state,
        "detail": status.detail,
    }


def _view(state: Any, account: Account) -> dict[str, Any]:
    if account.platform != "qq":
        return _platform_view(state, account)
    status = state.hub.status_of(account.id)
    return {
        "id": account.id,
        "self_id": account.self_id,
        "name": state.hub.name_of(account),
        "http_url": account.http_url,
        "ws_url": account.ws_url,
        "http_token": bool(state.secrets.get(account.http_token_ref)),
        "ws_token": bool(state.secrets.get(account.ws_token_ref)),
        "state": status.state,
        "detail": status.detail,
    }


@router.get("/accounts/connections")
async def connections(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    accounts = [await get_account(state.db, key) for key in state.hub.accounts]
    return [_view(state, a) for a in accounts if a is not None and a.platform in PLATFORMS]


@router.post("/accounts/{account_id}/connection/test")
async def test_connection(request: Request, account_id: str, body: Connection) -> dict[str, Any]:
    """Try the addresses and tokens in `body` (the saved ones where left out) without saving anything."""
    account = await _account(request, account_id)
    if account.platform != "qq":
        return await _test_platform(request.app.state, account, body)
    use = await _resolve(request, account, body)
    http = await probe.check_http(
        use["http_url"],
        use["http_token"],
        self_id=account.self_id,
        transport=request.app.state.fetch_transport,
    )
    ws = await probe.check_ws(use["ws_url"], use["ws_token"])
    return {"http": {"ok": http.ok, "detail": http.detail}, "ws": {"ok": ws.ok, "detail": ws.detail}}


@router.put("/accounts/{account_id}/connection")
async def save_connection(request: Request, account_id: str, body: Connection) -> dict[str, Any]:
    """Save, then reconnect the account with the new settings (its bot pauses for a few seconds)."""
    state = request.app.state
    async with state.config_lock:
        account = await _account(request, account_id)
        if account.platform == "qq":
            await _save_qq(state, account, await _resolve(request, account, body))
        else:
            await _save_platform(state, account, body)
    log.info("%s 的连接设置已修改，正在重新连接", account.id)
    await state.hub.reconnect(account.id)
    state.broadcaster.publish("system", {"change": "connection"})
    saved = await get_account(state.db, account.id)
    assert saved is not None
    return _view(state, saved)


async def _save_qq(state: Any, account: Account, use: dict[str, str]) -> None:
    refs = {kind: _ref(account, kind) for kind in ("http", "ws")}
    for kind in ("http", "ws"):
        if use[f"{kind}_token"]:
            state.secrets.set(refs[kind], use[f"{kind}_token"])
        else:
            state.secrets.unset(refs[kind])
    await update_connection(
        state.db,
        account.id,
        http_url=use["http_url"],
        ws_url=use["ws_url"],
        http_token_ref=refs["http"],
        ws_token_ref=refs["ws"],
    )


async def _test_platform(state: Any, account: Account, body: Connection) -> dict[str, Any]:
    spec = PLATFORMS[account.platform]
    if spec.probe_kicks:
        detail = f"{spec.label} 只在新建时测试：测试要用这个 bot 连一次，会把在线的它顶下线"
        return _trial(Probed(False, detail=detail))
    found = await spec.probe(check_fields(spec, body.fields or {}, _saved_fields(state, spec, account)))
    if found.ok and found.self_id != account.self_id:
        return _trial(Probed(False, detail=f"这个 Token 属于 {found.name}（{found.self_id}），不是这个 bot"))
    return _trial(found)


async def _save_platform(state: Any, account: Account, body: Connection) -> None:
    spec = PLATFORMS[account.platform]
    given = body.fields or {}
    fields = check_fields(spec, given, _saved_fields(state, spec, account))
    refs = account.refs or spec.refs(account.self_id)
    for name, ref in refs.items():
        if given.get(name):
            state.secrets.set(ref, fields[name])
    await update_config(state.db, account.id, stored_config(spec, fields, refs))


def _trial(found: Probed) -> dict[str, Any]:
    return {"ok": found.ok, "self_id": found.self_id, "name": found.name, "detail": found.detail}


@router.get("/platforms")
async def platforms() -> list[dict[str, Any]]:
    """What the console needs to offer them: each platform's form, what it can do, how a person is named."""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "capabilities": sorted(spec.capabilities),
            "fields": [asdict(field) for field in spec.fields],
            "user_label": spec.user_label,
            "user_pattern": spec.user_id.pattern,
            "user_error": spec.user_error,
            "probe_kicks": spec.probe_kicks,
        }
        for spec in PLATFORMS.values()
    ]


@router.post("/accounts/test")
async def test_new_account(request: Request, body: Trial) -> dict[str, Any]:
    """Try what the 增加 bot form holds; nothing is saved."""
    spec = platform_spec(body.platform)
    fields = check_fields(spec, body.fields)
    identity = next((f.name for f in spec.fields if f.identity), "")
    taken = account_id(spec.key, fields.get(identity, "")) in request.app.state.brain.bots
    if taken and spec.probe_kicks:
        raise HTTPException(status_code=409, detail="已经有用这个账号的 bot 了：测试会把它顶下线")
    return _trial(await spec.probe(fields))
