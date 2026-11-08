"""Request validation errors as one plain Chinese sentence.

FastAPI's default 422 body echoes the submitted value (`input`), which for a model's key would put the
key in the response. This handler builds the message only from where the error is (`loc`) and its kind
(`type`), never from pydantic's `input`, `ctx` or `msg`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

LABELS = {
    "name": "名字",
    "aliases": "别名",
    "content": "人设内容",
    "cooldown_seconds": "冷却",
    "persona_id": "人设",
    "provider_id": "模型",
    "base_url": "地址",
    "model": "模型名",
    "key": "密钥",
    "clear_key": "清除密钥",
    "kind": "接口类型",
    "options": "参数",
    "options.timeout": "超时",
    "options.temperature": "温度",
    "options.proxy": "代理",
    "options.extra_body": "额外参数",
    "options.input_format": "对话格式",
    "options.effort": "思考档",
    "options.auth_file": "登录文件",
    "mode": "模式",
    "enabled": "开关",
    "activity": "活跃度",
    "days": "天数",
    "snowluma_dir": "SnowLuma 文件夹",
    "qq_executable": "QQ 程序的位置",
    "limit": "条数",
    "level": "日志级别",
    "fetch_proxy": "外网代理",
}
# The 监控 API: its own words where a field name means something else elsewhere ("kind").
WATCH_LABELS = LABELS | {
    "kind": "类型",
    "url": "地址",
    "selector": "选择器",
    "threshold": "提醒价格",
    "interval_minutes": "检查间隔",
    "bot_id": "bot",
    "target_kind": "推送到",
    "target_id": "群号或 QQ 号",
    "instructions": "额外要求",
    "use_proxy": "外网代理开关",
    "watch_id": "监控",
}
SOURCES = ("body", "query", "path", "header", "cookie")


def label(loc: Sequence[Any], labels: dict[str, str] = LABELS) -> str:
    parts = [str(p) for p in loc if not isinstance(p, int)]
    if parts and parts[0] in SOURCES:
        parts = parts[1:]
    if not parts:
        return "请求内容"
    for end in range(len(parts), 0, -1):  # the most specific known label: options.timeout before options
        found = labels.get(".".join(parts[:end]))
        if found:
            return found
    return f"「{parts[-1][:40]}」"


def reason(kind: str) -> str:
    if kind == "missing":
        return "必须填写"
    if kind == "extra_forbidden":
        return "不认识这个设置"
    if kind.endswith(("too_long", "too_short")):
        return "长度不对"
    if kind.startswith(("greater_than", "less_than")):
        return "超出范围"
    return "格式不对"


def describe(errors: Sequence[Any], labels: dict[str, str] = LABELS) -> str:
    if not errors:
        return "填写的内容不对"
    first = errors[0]
    return label(first.get("loc") or (), labels) + reason(str(first.get("type") or ""))


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    labels = WATCH_LABELS if request.url.path.startswith("/api/watches") else LABELS
    return JSONResponse({"detail": describe(exc.errors(), labels)}, status_code=422)
