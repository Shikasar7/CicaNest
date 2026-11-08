"""Models (providers): list, add, edit, delete, test. Edits take effect without restarting nest.

Every change runs under app.state.config_lock in this order: re-read .env (the CLI may have written it)
→ validate → build every affected model with the new settings (a new key is overlaid, nothing is written
yet) → write .env → write the row → swap the new runtimes in → brain.load() → publish. A rejected change
leaves the runtimes, their errors, the row and .env exactly as they were.

Existence is decided by the providers table, not the registry: a row that failed to build must stay
editable and deletable. Key values never leave .env: they are not returned, logged or broadcast, and
error messages are built without them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from nest.api.auth import require_token
from nest.api.text import has_bad_chars, proxy_url
from nest.llm.anthropic import EFFORTS
from nest.llm.base import ChatMessage, Provider, ProviderError
from nest.llm.codex import DEFAULT_URL as CODEX_URL
from nest.llm.models_list import list_models
from nest.llm.runtime import KINDS, ProviderUnavailable, error_text
from nest.secrets import SecretStore
from nest.storage.accounts import list_accounts
from nest.storage.bots import InUse, ProviderConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

ID = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
KEY = re.compile(r"[\x21-\x7e]{1,500}")
DELETABLE_REF = re.compile(r"NEST_KEY_[A-Z0-9_]+")  # only refs nest made itself are ever removed
CODEX_HOST = "chatgpt.com"  # Codex sends the login token to its address: never anywhere else
NOT_LOADED = "还没有加载，保存一次或重启 nest"

# Exactly what build_provider reads for each kind.
OPTIONS = {
    "openai_chat": ("proxy", "timeout", "temperature", "extra_body"),
    "openai_responses": ("proxy", "timeout", "extra_body", "input_format"),
    "anthropic": ("proxy", "timeout", "extra_body", "auth_header", "effort"),
    "codex": ("proxy", "timeout", "auth_file"),
}
RESERVED_BODY = (
    "model",
    "messages",
    "input",
    "system",
    "max_tokens",
    "max_output_tokens",
    "stream",
    "temperature",
)
EXTRA_BODY_BYTES = 4000
EXTRA_BODY_DEPTH = 5


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    base_url: str = ""
    model: str
    options: dict[str, Any] | None = None
    # No constraint and no validator may read the key: pydantic errors would echo it.
    key: str | None = None
    clear_key: bool = False


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    options: dict[str, Any] | None = None
    key: str | None = None
    clear_key: bool = False


class ModelListRequest(BaseModel):
    """What the model form has right now; the key is the one typed, else the saved one of `provider_id`."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    base_url: str
    provider_id: str | None = None
    options: dict[str, Any] | None = None
    key: str | None = None


@dataclass(frozen=True)
class KeyWrite:
    """One .env change: store `value` under `ref`, or remove `ref` when value is None."""

    ref: str
    value: str | None = field(repr=False)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _reload_secrets(state: Any, *, strict: bool) -> None:
    """Read .env again: the CLI may have written it since nest started.

    Changes must see the file as it is now (strict); a listing falls back to what was read before.
    """
    try:
        state.secrets.reload()
    except (OSError, ValueError) as exc:  # locked while being replaced, or saved in another encoding
        log.warning("读取 .env 失败：%s", type(exc).__name__)
        if strict:
            raise HTTPException(
                status_code=500, detail="读取 .env 失败，请检查它是否被别的程序占用或改坏"
            ) from None


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def default_ref(provider_id: str) -> str:
    return "NEST_KEY_" + provider_id.upper().replace("-", "_")


def _ref(config: ProviderConfig) -> str:
    """The key name a model actually reads (Codex reads none)."""
    return "" if config.kind == "codex" else config.key_ref


# -- validation -------------------------------------------------------------------------------------


def _kind(value: str) -> str:
    if value not in KINDS:
        raise _invalid("接口类型只能是 OpenAI Chat 格式、Responses 格式、Claude（Anthropic）或 Codex 订阅")
    return value


def _model_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 100:
        raise _invalid("模型名要填 1–100 个字")
    if has_bad_chars(value):
        raise _invalid("模型名里不能有换行或控制字符")
    return value


def _base_url(kind: str, value: str) -> str:
    value = value.strip()
    if kind == "codex" and not value:
        return CODEX_URL
    if len(value) > 300:
        raise _invalid("地址最多 300 个字")
    try:
        parts = urlsplit(value)
    except ValueError:  # e.g. a bracketed host without its "]", or no IP address inside the brackets
        raise _invalid("地址要写成 http:// 或 https:// 开头的完整网址") from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or any(c.isspace() or not c.isprintable() for c in value)
    ):
        raise _invalid("地址要写成 http:// 或 https:// 开头的完整网址")
    if "@" in parts.netloc or "?" in value or "#" in value:
        raise _invalid("地址里不要带用户名、密码或 ? 参数，密钥请填在「密钥」里")
    try:
        parts.port  # noqa: B018 (raises on a malformed port)
    except ValueError:
        raise _invalid("地址里的端口不对") from None
    if kind == "codex" and (parts.scheme != "https" or parts.hostname != CODEX_HOST):
        raise _invalid("Codex 订阅的地址只能是 https://chatgpt.com 下的网址（登录凭证会发到这里）")
    return value.rstrip("/")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return value


def _proxy(value: Any) -> str:
    proxy = proxy_url(value)
    if proxy is None:
        raise _invalid("代理要写成 http://主机:端口（不支持 socks5，不要带用户名或路径）")
    return proxy


def _timeout(value: Any) -> float:
    number = _number(value)
    if number is None or not 1 <= number <= 300:
        raise _invalid("超时要在 1–300 秒之间")
    return number


def _temperature(value: Any) -> float:
    number = _number(value)
    if number is None or not 0 <= number <= 2:
        raise _invalid("温度要在 0–2 之间")
    return number


def _too_deep(value: Any, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:  # iterative: a deeply nested body must not hit the recursion limit
        item, depth = stack.pop()
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else None
        if children is None:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _extra_body(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("额外参数要是一个 JSON 对象（用 { } 包起来）")
    if _too_deep(value, EXTRA_BODY_DEPTH):
        raise _invalid(f"额外参数最多嵌套 {EXTRA_BODY_DEPTH} 层")
    for name in RESERVED_BODY:
        if name in value:
            raise _invalid(f"额外参数里不能设置 {name}，它由 nest 控制")
    try:
        # NaN and infinity are not JSON: the HTTP client would refuse every call with this body.
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ValueError:
        raise _invalid("额外参数里不能有 NaN 或无穷大") from None
    try:
        size = len(encoded.encode())
    except UnicodeEncodeError:  # a lone surrogate
        raise _invalid("额外参数里有不能保存的字符") from None
    if size > EXTRA_BODY_BYTES:
        raise _invalid(f"额外参数最多 {EXTRA_BODY_BYTES} 字节")
    return value


def _input_format(value: Any) -> str:
    if value not in ("text", "messages"):
        raise _invalid("对话格式只能选「合成一段文字」或「按角色分条发送」")
    return value


def _auth_header(value: Any) -> str:
    if value not in ("x-api-key", "bearer"):
        raise _invalid("密钥请求头只能选 x-api-key 或 Authorization: Bearer")
    return value


def _effort(value: Any) -> str:
    if value not in EFFORTS:
        raise _invalid("思考档只能选低、中或高")
    return value


def _auth_file(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("登录文件要填文件路径")
    text = value.strip()
    if not text:
        return ""  # the default, ~/.codex/auth.json
    if has_bad_chars(text):
        raise _invalid("登录文件路径里有不能保存的字符")
    path = Path(text).expanduser()
    # Only existence is checked; the content (a login token) is never read here.
    if not path.is_absolute() or path.suffix.lower() != ".json" or not path.is_file():
        raise _invalid(f"找不到 Codex 登录文件：{path}")
    return text


CHECKS = {
    "proxy": _proxy,
    "timeout": _timeout,
    "temperature": _temperature,
    "extra_body": _extra_body,
    "input_format": _input_format,
    "auth_header": _auth_header,
    "effort": _effort,
    "auth_file": _auth_file,
}


def _options(kind: str, options: dict[str, Any]) -> dict[str, Any]:
    """The options to store: only what this kind reads; null or empty values mean the default."""
    clean: dict[str, Any] = {}
    for name, value in options.items():
        if has_bad_chars(name):
            raise _invalid("参数名里有不能保存的字符")
        if name not in OPTIONS[kind]:
            raise _invalid(f"「{name[:40]}」这个设置在这种接口里不起作用")
        if value is None:
            continue
        checked = CHECKS[name](value)
        if checked not in ("", {}):
            clean[name] = checked
    if "effort" in clean and "output_config" in clean.get("extra_body", {}):
        raise _invalid("额外参数里的 output_config 会盖过思考档，两处留一处")
    return clean


def _key_input(kind: str, key: str | None, clear: bool) -> str | None:
    """The requested key change: a new key, "" to clear the saved one, or None for no change."""
    if key is not None and clear:
        raise _invalid("填了新密钥就不要再勾选清除")
    if kind == "codex" and (key is not None or clear):
        raise _invalid("Codex 订阅不用密钥，它用登录文件")
    if clear:
        return ""
    if key is None:
        return None
    key = key.strip()
    if not KEY.fullmatch(key):
        raise _invalid("密钥里有空格、换行或中文字符，请检查是否复制完整")
    return key


# -- key names ----------------------------------------------------------------------------------------


async def _account_refs(state: Any) -> set[str]:
    refs = set()
    for account in await list_accounts(state.db):
        refs |= {account.http_token_ref, account.ws_token_ref, *account.refs.values()}
    return refs - {""}


def _env_conflict(ref: str) -> HTTPException:
    return _conflict(f"这个模型的密钥由环境变量 {ref} 提供，在这里改不会生效")


async def _claim(state: Any, ref: str, provider_id: str) -> None:
    """A model gets a fresh key name (new, or no longer Codex): it must not be anyone else's."""
    if ref in await _account_refs(state):
        raise _conflict("这个密钥名被 QQ 连接占用")
    for other in await state.repo.providers():
        if other.id != provider_id and other.key_ref == ref:
            raise _conflict(f"密钥名 {ref} 已被 {other.id} 使用")


def _fresh_write(secrets: SecretStore, ref: str, key: str | None) -> KeyWrite | None:
    """For a fresh key name: store the new key, or remove a value left by an old model, so a new model
    never inherits an old key."""
    if key:
        if secrets.source(ref) == "env":
            raise _env_conflict(ref)
        return KeyWrite(ref, key)
    if secrets.file_value(ref) is not None:
        return KeyWrite(ref, None)
    return None


async def _forget_ref(state: Any, ref: str) -> None:
    """Remove a key nobody reads any more from .env (only names nest made, never a QQ token)."""
    if not DELETABLE_REF.fullmatch(ref):
        return
    if any(c.key_ref == ref for c in await state.repo.providers()) or ref in await _account_refs(state):
        return
    try:
        state.secrets.unset(ref)
    except Exception:
        log.warning("没能从 .env 删除不再使用的密钥 %s", ref)


# -- applying a change --------------------------------------------------------------------------------


async def _close(providers: list[Provider]) -> None:
    for provider in providers:
        closer = getattr(provider, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()


def _restore(secrets: SecretStore, ref: str, previous: str | None) -> None:
    try:
        if previous is None:
            secrets.unset(ref)
        else:
            secrets.set(ref, previous)
    except Exception:
        log.error("没能把 .env 里的密钥 %s 恢复成修改前的值", ref)


async def _apply(
    state: Any, config: ProviderConfig, sharers: list[ProviderConfig], write: KeyWrite | None
) -> None:
    """Build → write .env → write the row → swap in. Raises HTTPException; before the swap nothing changes
    unless every step succeeded."""
    secrets: SecretStore = state.secrets
    reader: Any = secrets
    if write is not None:
        # What get(ref) will return once written: an environment variable still wins over .env.
        value = secrets.get(write.ref) if secrets.source(write.ref) == "env" else write.value or ""
        reader = secrets.overlay(write.ref, value)
    known = [write.value or ""] if write else []
    built: list[tuple[ProviderConfig, Provider]] = []
    for target in [config, *sharers]:
        try:
            built.append((target, state.provider_factory(target, reader)))
        except Exception as exc:
            await _close([p for _, p in built])
            text = error_text(exc, *known, secrets.get(target.key_ref))
            where = "" if target is config else f"（{target.id} 也用这个密钥）"
            raise HTTPException(status_code=400, detail=f"模型设置有误{where}：{text}") from None
    providers = [p for _, p in built]

    previous = secrets.file_value(write.ref) if write else None
    if write is not None:
        try:
            if write.value is None:
                secrets.unset(write.ref)
            else:
                secrets.set(write.ref, write.value)
        except (OSError, ValueError):  # ValueError: a malformed key name from an old CLI row
            log.error("密钥 %s 写入 .env 失败", write.ref)
            await _close(providers)
            raise HTTPException(status_code=500, detail="密钥写入 .env 失败") from None
    try:
        await state.repo.upsert_provider(config)
    except Exception:
        log.error("模型 %s 没能保存到数据库", config.id, exc_info=True)
        if write is not None:
            _restore(secrets, write.ref, previous)
        await _close(providers)
        raise HTTPException(status_code=500, detail="模型设置没能保存") from None
    for target, provider in built:
        state.providers.replace(target, provider)


async def _changed(state: Any, provider_ids: list[str]) -> None:
    await state.brain.load()
    state.broadcaster.publish("bots", {"bot_id": None})
    for provider_id in provider_ids:
        state.broadcaster.publish("providers", {"provider_id": provider_id})


# -- views ------------------------------------------------------------------------------------------


def _view(
    state: Any,
    config: ProviderConfig,
    configs: list[ProviderConfig],
    users: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    secrets: SecretStore = state.secrets
    ref = _ref(config)
    runtime = state.providers.get(config.id)
    return {
        "id": config.id,
        "kind": config.kind,
        "base_url": config.base_url,
        "model": config.model,
        "options": config.options,
        "key_ref": ref,
        "key_set": bool(secrets.get(ref)),
        "key_from_env": secrets.source(ref) == "env",
        "key_shared_with": [c.id for c in configs if ref and c.id != config.id and _ref(c) == ref],
        "used_by": users.get(config.id, []),
        "health": runtime.health_view() if runtime else None,
        # A row with neither a runtime nor an error was added by the CLI while nest was running.
        "build_error": None if runtime else state.providers.errors.get(config.id) or NOT_LOADED,
    }


async def _views(state: Any) -> list[dict[str, Any]]:
    configs = await state.repo.providers()  # sorted by id
    users = await state.repo.provider_users()
    return [_view(state, config, configs, users) for config in configs]


async def _view_of(state: Any, provider_id: str) -> dict[str, Any]:
    return next(view for view in await _views(state) if view["id"] == provider_id)


# -- endpoints ----------------------------------------------------------------------------------------


@router.get("/providers")
async def providers(request: Request) -> list[dict[str, Any]]:
    _reload_secrets(request.app.state, strict=False)  # key status of keys the CLI wrote
    return await _views(request.app.state)


@router.post("/providers", status_code=201)
async def create_provider(request: Request, body: ProviderCreate) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _reload_secrets(state, strict=True)
        if not ID.fullmatch(body.id):
            raise _invalid("名字只能用小写字母、数字和 -，2–40 个字，以字母或数字开头")
        kind = _kind(body.kind)
        base_url = _base_url(kind, body.base_url)
        model = _model_name(body.model)
        options = _options(kind, body.options or {})
        key = _key_input(kind, body.key, body.clear_key)
        if await state.repo.get_provider(body.id) is not None:
            raise _conflict(f"已经有叫 {body.id} 的模型")
        ref, write = "", None
        if kind != "codex":
            ref = default_ref(body.id)
            await _claim(state, ref, body.id)
            write = _fresh_write(state.secrets, ref, key)
        config = ProviderConfig(body.id, kind, base_url, model, ref, options)
        await _apply(state, config, [], write)
        log.info("模型 %s 已添加", config.id)
        await _changed(state, [config.id])
        return await _view_of(state, config.id)


@router.patch("/providers/{provider_id}")
async def update_provider(request: Request, provider_id: str, patch: ProviderPatch) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _reload_secrets(state, strict=True)
        stored = await state.repo.get_provider(provider_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="没有这个模型")
        kind = _kind(patch.kind if patch.kind is not None else stored.kind)
        base_url = _base_url(kind, patch.base_url if patch.base_url is not None else stored.base_url)
        model = _model_name(patch.model if patch.model is not None else stored.model)
        if patch.options is not None:
            options = _options(kind, patch.options)
        else:
            try:
                options = _options(kind, stored.options)
            except HTTPException:
                if kind != stored.kind:
                    raise _invalid("换接口类型时请把参数一起提交") from None
                raise
        key = _key_input(kind, patch.key, patch.clear_key)

        write, sharers, forget = None, [], ""
        if kind == "codex":
            ref = ""
            if stored.kind != "codex":
                forget = stored.key_ref
        elif stored.kind == "codex" or (not stored.key_ref and key):
            # A fresh key name (no longer Codex, or a model that never had one): same rules as a new model.
            ref = default_ref(provider_id)
            await _claim(state, ref, provider_id)
            write = _fresh_write(state.secrets, ref, key)
        else:
            ref = stored.key_ref
            if key is not None and ref:
                if ref in await _account_refs(state):
                    raise _conflict("这个密钥名被 QQ 连接占用")
                if state.secrets.source(ref) == "env":
                    raise _env_conflict(ref)
                write = KeyWrite(ref, key or None)
                # A shared key changes for every model that reads it: all are rebuilt together.
                sharers = [c for c in await state.repo.providers() if c.id != provider_id and _ref(c) == ref]
        config = ProviderConfig(provider_id, kind, base_url, model, ref, options)
        await _apply(state, config, sharers, write)
        if forget:
            await _forget_ref(state, forget)
        log.info("模型 %s 已修改", provider_id)
        await _changed(state, [provider_id, *(c.id for c in sharers)])
        return await _view_of(state, provider_id)


@router.delete("/providers/{provider_id}")
async def delete_provider(request: Request, provider_id: str) -> dict[str, Any]:
    state = request.app.state
    async with state.config_lock:
        _reload_secrets(state, strict=True)
        stored = await state.repo.get_provider(provider_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="没有这个模型")
        try:
            deleted = await state.repo.delete_provider(provider_id)
        except InUse as exc:
            raise _conflict(f"还有 bot 在用这个模型：{'、'.join(exc.users)}") from None
        if not deleted:
            raise HTTPException(status_code=404, detail="没有这个模型")
        state.providers.remove(provider_id)
        await _forget_ref(state, stored.key_ref)
        log.info("模型 %s 已删除", provider_id)
        await _changed(state, [provider_id])
    return {"ok": True}


def _same_server(a: str, b: str) -> bool:
    first, second = urlsplit(a), urlsplit(b)
    return (first.scheme, first.hostname, first.port) == (second.scheme, second.hostname, second.port)


@router.post("/providers/models")
async def provider_models(request: Request, body: ModelListRequest) -> dict[str, Any]:
    """The model names the interface offers. A saved key is only sent back to the server it was saved
    for: with another address, the key has to be typed again."""
    state = request.app.state
    kind = _kind(body.kind)
    if kind == "codex":
        raise _invalid("Codex 订阅不能获取模型列表")
    base_url = _base_url(kind, body.base_url)
    options = _options(kind, {k: v for k, v in (body.options or {}).items() if k != "extra_body"})
    key = _key_input(kind, body.key, False) or ""
    if not key and body.provider_id:
        stored = await state.repo.get_provider(body.provider_id)
        if stored is not None and stored.kind != "codex" and _same_server(stored.base_url, base_url):
            _reload_secrets(state, strict=False)
            key = state.secrets.get(stored.key_ref)
    try:
        models = await list_models(
            kind,
            base_url,
            key,
            auth_header=options.get("auth_header", "x-api-key"),
            proxy=options.get("proxy") or None,
            wait=min(float(options.get("timeout", 15)), 30.0),
        )
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "models": models}


@router.post("/providers/{provider_id}/test")
async def test_provider(request: Request, provider_id: str) -> dict[str, Any]:
    # Not under config_lock: this waits on the network.
    state = request.app.state
    if await state.repo.get_provider(provider_id) is None:
        raise HTTPException(status_code=404, detail="没有这个模型")
    runtime = state.providers.get(provider_id)
    if runtime is None:
        return {"ok": False, "error": state.providers.errors.get(provider_id) or NOT_LOADED}
    messages = [ChatMessage("system", "这是连通性测试。只回复一个字：在"), ChatMessage("user", "在吗？")]
    try:
        completion, _ = await runtime.complete(messages, bot_id=None, max_tokens=60)
    except (ProviderError, ProviderUnavailable) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "text": completion.text.strip()[:100], "latency_ms": round(completion.latency_ms)}
