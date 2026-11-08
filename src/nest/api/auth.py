"""Access control.

- Requests may come from this machine only; with remote access on (NEST_REMOTE), also from the local
  network and Tailscale (nest.net). Anything else is refused before any route runs.
- Every request must carry a Host header that names nest (blocks DNS-rebinding pages from reaching it).
- API routes need either the per-process token, as a SameSite=Strict cookie (console) or a Bearer header
  (CLI), accepted from this machine only, or a device session from signing in with the password
  (nest.remote). The token is minted at startup and handed to the launcher through data/runtime.json.
- Writes (anything but GET/HEAD) from another site's page are refused even with a cookie: by Origin
  (browsers send it with every write; Sec-Fetch-Site only reaches https and localhost, not
  http://192.168.x.x), and by Sec-Fetch-Site where present. A device-cookie write without either is
  refused too: it cannot be nest's own page.
- No reverse proxy is trusted: the client address is the TCP peer (uvicorn's proxy headers are off).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from nest import hints
from nest.net import address_kind, host_allowed
from nest.remote import COOKIE as DEVICE_COOKIE
from nest.remote import SESSION_SECONDS

COOKIE_NAME = "nest_session"
SAFE_METHODS = ("GET", "HEAD")
SAME_SITE_VALUES = ("same-origin", "none")  # "none": typed into the address bar, a bookmark

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
        b"style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        b"form-action 'self'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


@dataclass
class Access:
    """Who may connect. `names`: this machine's own host names (filled in at startup)."""

    port: int
    remote: bool = False
    names: set[str] = field(default_factory=set)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def is_local(request: Request) -> bool:
    return address_kind(client_ip(request)) == "local"


def token_matches(request: Request, supplied: str | None) -> bool:
    return bool(supplied) and secrets.compare_digest(supplied, request.app.state.token)


def _own_origin(origin: str, request: Request) -> bool:
    """Whether an Origin header names the address this request was sent to."""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    host = request.headers.get("host", "").lower()
    return parts.scheme == request.url.scheme and parts.netloc.lower() == host and bool(host)


def refuse_cross_site(request: Request, *, by_device: bool = False) -> None:
    # SameSite ignores ports, so a page of another web app on the same machine (SnowLuma's WebUI, say)
    # would still send the cookies. The CLI sends neither header and signs in with the token.
    if request.method in SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    refused = (
        (site is not None and site not in SAME_SITE_VALUES)
        or (origin is not None and not _own_origin(origin, request))
        or (by_device and origin is None and site is None)
    )
    if refused:
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def set_device_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


async def signed_in(request: Request) -> bool:
    """The per-process token from this machine, or a device that signed in with the password (kept on
    request.state.device; request.state.renewed tells whether its 30 days were just extended)."""
    supplied = request.cookies.get(COOKIE_NAME)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    if supplied and is_local(request) and token_matches(request, supplied):
        return True
    return await device_signed_in(request)


async def device_signed_in(request: Request) -> bool:
    """Whether the request's device cookie still belongs to a signed-in device."""
    cookie = request.cookies.get(DEVICE_COOKIE)
    if not cookie:
        return False
    found = await request.app.state.remote.device(cookie, ip=client_ip(request))
    if found is None:
        return False
    request.state.device, request.state.renewed = found
    return True


async def require_token(request: Request, response: Response) -> None:
    if not await signed_in(request):
        detail = f"未登录：请{hints.START}打开控制台" if is_local(request) else "未登录：请先输入密码登录"
        raise HTTPException(status_code=401, detail=detail)
    device = getattr(request.state, "device", None)
    refuse_cross_site(request, by_device=device is not None)
    if device is not None and request.state.renewed:
        # The browser's copy expires 30 days after it was last sent: send it again with the new term.
        set_device_cookie(response, request.cookies[DEVICE_COOKIE], request)


class LocalOnly:
    """Pure ASGI middleware: client address and Host checks plus security headers (covers HTTP, SSE and
    WebSocket)."""

    def __init__(self, app: ASGIApp, *, access: Access) -> None:
        self.app = app
        self.access = access

    def _refusal(self, scope: Scope) -> str | None:
        client = scope.get("client")
        kind = address_kind(client[0] if client else None)
        if kind is None or (kind != "local" and not self.access.remote):
            return "只允许本机、局域网和 Tailscale 访问" if self.access.remote else "仅限本机访问"
        host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
        if not host_allowed(host, port=self.access.port, remote=self.access.remote, names=self.access.names):
            if not self.access.remote:
                return "仅限本机访问"
            return "这个地址不能打开 nest：请用 IP 地址，或把这个名字加进 .env 的 NEST_ALLOWED_HOSTS"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        refusal = self._refusal(scope)
        if refusal is not None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            body = refusal.encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                        *SECURITY_HEADERS,
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def with_headers(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], *SECURITY_HEADERS]
            await send(message)

        await self.app(scope, receive, with_headers)
