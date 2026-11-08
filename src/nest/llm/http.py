"""Shared HTTP plumbing for model providers: error mapping and secret-safe messages."""

from __future__ import annotations

import re

import httpx

from nest.llm.base import ProviderError

KEYISH = re.compile(r"(sk-|Bearer\s+)[A-Za-z0-9._\-]{6,}")


def scrub(text: str, *secrets: str) -> str:
    """Remove secrets (and anything that looks like an API key) from text shown in logs or the console."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return KEYISH.sub(r"\1***", text)


def client(*, timeout: float, proxy: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
    # trust_env=False: proxies are explicit per provider, never inherited from the environment.
    return httpx.AsyncClient(timeout=timeout, trust_env=False, proxy=proxy, transport=transport)


def transport_error(exc: Exception) -> ProviderError:
    kind = "超时" if isinstance(exc, httpx.TimeoutException) else "网络错误"
    return ProviderError(f"{kind}：{type(exc).__name__}", retryable=True)


def status_error(response: httpx.Response, *secrets: str) -> ProviderError:
    try:
        body = response.json()
        error = body.get("error")
        detail = error.get("message") if isinstance(error, dict) else str(error or body)
    except ValueError:
        detail = response.text
    detail = scrub(str(detail), *secrets)[:160]
    retryable = response.status_code == 429 or response.status_code >= 500
    return ProviderError(f"HTTP {response.status_code}：{detail}", retryable=retryable)
