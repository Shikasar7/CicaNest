"""The models an interface offers: GET {base_url}/models, for picking a model name in the console.

OpenAI-compatible interfaces (and most relays) answer {"data": [{"id": …}, …]} with a Bearer key;
Anthropic answers the same shape with an x-api-key (or Bearer, for relays) and the version header.
"""

from __future__ import annotations

from typing import Any

import httpx

from nest.llm.anthropic import API_VERSION
from nest.llm.base import ProviderError
from nest.llm.http import client, status_error, transport_error

MAX_MODELS = 500


def _ids(data: Any) -> list[str]:
    items = data.get("data", data.get("models")) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ProviderError("返回的内容里没有模型列表", retryable=False)
    names = (item.get("id") if isinstance(item, dict) else item for item in items)
    return sorted({name for name in names if isinstance(name, str) and name.strip()})[:MAX_MODELS]


async def list_models(
    kind: str,
    base_url: str,
    key: str = "",
    *,
    auth_header: str = "x-api-key",
    proxy: str | None = None,
    wait: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    """Model ids, sorted. Raises ProviderError (secret-free) when the interface can't be asked."""
    headers: dict[str, str] = {}
    params: dict[str, Any] = {}
    if kind == "anthropic":
        headers["anthropic-version"] = API_VERSION
        params["limit"] = 1000
        if key:
            headers.update(
                {"authorization": f"Bearer {key}"} if auth_header == "bearer" else {"x-api-key": key}
            )
    elif key:
        headers["authorization"] = f"Bearer {key}"
    async with client(timeout=wait, proxy=proxy, transport=transport) as http:
        try:
            response = await http.get(base_url.rstrip("/") + "/models", headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise transport_error(exc) from exc
    if response.status_code == 404:  # e.g. Volcengine Ark's coding plan has no /models
        raise ProviderError("这个接口不提供模型列表（HTTP 404），模型名请照接口文档填", retryable=False)
    if response.status_code >= 400:
        raise status_error(response, key)
    try:
        return _ids(response.json())
    except ValueError:
        raise ProviderError("返回的不是 JSON，这个地址可能不支持获取模型列表", retryable=False) from None
