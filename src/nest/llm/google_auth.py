"""Google Cloud credentials, for Vertex AI (Agent Platform) without an API key: an hour-long OAuth access
token made from a JSON credential kept in the model's key field, and kept until a few minutes before it runs
out. Two kinds:
- a service account's JSON key: a JWT signed with its private key (RS256), traded at Google's token address;
- the file `gcloud auth application-default login` writes (authorized_user): its refresh token traded there.

The signature is made here in plain Python (RSA PKCS #1 v1.5 over SHA-256), so nest needs no crypto package.
It runs on the user's own machine, signing a token request of its own.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from nest.llm.http import scrub

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_URI = "https://oauth2.googleapis.com/token"
LIFETIME = 3600
MARGIN = 5 * 60  # a token this close to running out is made again
NOT_A_CREDENTIAL = (
    "这不是 Google 的 JSON 凭据：要服务账号的 JSON 密钥，或者 gcloud auth application-default login 生成的"
    " application_default_credentials.json"
)
# SHA-256's DigestInfo prefix (RFC 8017, 9.2 note 1).
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class AuthError(Exception):
    """No token: why, in words, free of secrets. retryable: Google could not be reached, or was busy."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Credential:
    kind: str  # "service_account" | "authorized_user"
    project: str
    token_uri: str
    email: str = ""
    private_key: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""

    def __repr__(self) -> str:  # never show the secrets
        return f"Credential({self.kind}, {self.project or '-'})"


def _token_uri(value: Any) -> str:
    """Google's token address; one on this machine is let through for tests."""
    try:
        parts = urlsplit(str(value or ""))
    except ValueError:
        return TOKEN_URI
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"):
        return str(value)
    return TOKEN_URI


def credential(value: str) -> Credential | None:
    """The credential in `value`, or None when it is not JSON (an API key). ValueError: JSON, but not a
    Google credential. Newlines a store turned its escapes into are read as they are."""
    if not value.lstrip().startswith("{"):
        return None
    try:
        found = json.loads(value, strict=False)
    except ValueError:
        raise ValueError(NOT_A_CREDENTIAL) from None
    if not isinstance(found, dict):
        raise ValueError(NOT_A_CREDENTIAL)
    text = {k: v for k, v in found.items() if isinstance(v, str)}
    uri = _token_uri(found.get("token_uri"))
    if found.get("type") == "service_account":
        if not text.get("client_email") or "PRIVATE KEY" not in text.get("private_key", ""):
            raise ValueError(NOT_A_CREDENTIAL)
        try:
            _rsa_key(text["private_key"])
        except ValueError:
            raise ValueError("服务账号 JSON 里的私钥读不出来，重新下载一份 JSON 密钥再粘贴") from None
        return Credential(
            "service_account",
            text.get("project_id", ""),
            uri,
            email=text["client_email"],
            private_key=text["private_key"],
        )
    if found.get("type") == "authorized_user":
        if not (text.get("client_id") and text.get("client_secret") and text.get("refresh_token")):
            raise ValueError(NOT_A_CREDENTIAL)
        return Credential(
            "authorized_user",
            text.get("quota_project_id") or text.get("project_id", ""),
            uri,
            client_id=text["client_id"],
            client_secret=text["client_secret"],
            refresh_token=text["refresh_token"],
        )
    raise ValueError(NOT_A_CREDENTIAL)


def compact(value: str) -> str:
    """What the model's key field stores: the credential cut down to what a token needs, on one line of
    printable ASCII. ValueError when it is not one."""
    found = credential(value)
    if found is None:
        raise ValueError(NOT_A_CREDENTIAL)
    kept: dict[str, str] = {"type": found.kind, "project_id": found.project}
    if found.kind == "service_account":
        kept |= {"client_email": found.email, "private_key": found.private_key}
    else:
        kept |= {
            "client_id": found.client_id,
            "client_secret": found.client_secret,
            "refresh_token": found.refresh_token,
        }
    if found.token_uri != TOKEN_URI:
        kept["token_uri"] = found.token_uri
    return json.dumps(kept, separators=(",", ":"), ensure_ascii=True)


# -- RSA ----------------------------------------------------------------------------------------------


def _der(data: bytes, at: int) -> tuple[int, bytes, int]:
    """(tag, contents, where the next element starts) of the DER element at `at`."""
    if at + 2 > len(data):
        raise ValueError("truncated")
    tag, length = data[at], data[at + 1]
    at += 2
    if length & 0x80:
        size = length & 0x7F
        if not 1 <= size <= 4 or at + size > len(data):
            raise ValueError("bad length")
        length = int.from_bytes(data[at : at + size], "big")
        at += size
    if at + length > len(data):
        raise ValueError("truncated")
    return tag, data[at : at + length], at + length


def _elements(data: bytes) -> list[tuple[int, bytes]]:
    found, at = [], 0
    while at < len(data):
        tag, contents, at = _der(data, at)
        found.append((tag, contents))
    return found


@dataclass(frozen=True, slots=True)
class _RsaKey:
    n: int
    d: int
    p: int
    q: int
    dp: int
    dq: int
    qinv: int


def _rsa_key(pem: str) -> _RsaKey:
    """The RSA key in a PEM: PKCS #8 (BEGIN PRIVATE KEY, what Google hands out) or PKCS #1."""
    lines = [line.strip() for line in pem.strip().splitlines()]
    if len(lines) < 3 or not lines[0].startswith("-----BEGIN") or not lines[-1].startswith("-----END"):
        raise ValueError("not a PEM")
    der = base64.b64decode("".join(lines[1:-1]), validate=True)
    tag, body, _ = _der(der, 0)
    if tag != 0x30:
        raise ValueError("not a sequence")
    items = _elements(body)
    if "RSA PRIVATE KEY" not in lines[0]:  # PKCS #8: version, algorithm, the PKCS #1 key in an octet string
        if len(items) < 3 or items[2][0] != 0x04:
            raise ValueError("not PKCS #8")
        tag, body, _ = _der(items[2][1], 0)
        if tag != 0x30:
            raise ValueError("not a sequence")
        items = _elements(body)
    numbers = [int.from_bytes(contents, "big") for tag, contents in items if tag == 0x02]
    if len(numbers) < 9:
        raise ValueError("not an RSA key")
    _, n, _, d, p, q, dp, dq, qinv = numbers[:9]
    return _RsaKey(n, d, p, q, dp, dq, qinv)


def sign(pem: str, message: bytes) -> bytes:
    """RSASSA-PKCS1-v1_5 with SHA-256 (RS256)."""
    key = _rsa_key(pem)
    size = (key.n.bit_length() + 7) // 8
    digest = SHA256_INFO + hashlib.sha256(message).digest()
    if size < len(digest) + 11:
        raise ValueError("key too short")
    encoded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    m = int.from_bytes(encoded, "big")
    # The Chinese remainder theorem: the same signature as pow(m, d, n), about four times as fast.
    m1, m2 = pow(m, key.dp, key.p), pow(m, key.dq, key.q)
    s = m2 + (key.qinv * (m1 - m2) % key.p) * key.q
    return s.to_bytes(size, "big")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def assertion(found: Credential, now: float) -> str:
    """The signed JWT a service account trades for a token."""
    issued = int(now)
    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = {
        "iss": found.email,
        "scope": SCOPE,
        "aud": found.token_uri,
        "iat": issued,
        "exp": issued + LIFETIME,
    }
    body = _b64url(json.dumps(claims).encode())
    signed = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{_b64url(sign(found.private_key, signed))}"


# -- tokens -------------------------------------------------------------------------------------------

# Tokens by credential (a hash of it, never the credential itself): (token, when it runs out).
_tokens: dict[str, tuple[str, float]] = {}


def _cache_key(found: Credential) -> str:
    secret = found.private_key or found.refresh_token
    return hashlib.sha256(f"{found.kind}\0{found.email}{found.client_id}\0{secret}".encode()).hexdigest()


async def access_token(found: Credential, http: httpx.AsyncClient) -> str:
    """A token for the credential: the one made before while it lasts, else a new one from Google."""
    key = _cache_key(found)
    now = time.time()
    known = _tokens.get(key)
    if known is not None and known[1] - MARGIN > now:
        return known[0]
    if found.kind == "service_account":
        form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion(found, now),
        }
    else:
        form = {
            "grant_type": "refresh_token",
            "client_id": found.client_id,
            "client_secret": found.client_secret,
            "refresh_token": found.refresh_token,
        }
    try:
        response = await http.post(found.token_uri, data=form)
    except httpx.HTTPError as exc:
        raise AuthError(f"连不上 Google 的令牌地址：{type(exc).__name__}", retryable=True) from exc
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    token = answer.get("access_token") if isinstance(answer, dict) else None
    if response.status_code >= 300 or not isinstance(token, str) or not token:
        reason = answer.get("error_description") or answer.get("error") if isinstance(answer, dict) else ""
        reason = scrub(
            " ".join(str(reason or response.text).split()), found.client_secret, found.refresh_token
        )
        what = (
            "密钥可能被删了或停用了，重新下载一份 JSON 密钥再试"
            if found.kind == "service_account"
            else "gcloud 的登录可能过期了，重新运行 gcloud auth application-default login，再把新文件填进来"
        )
        retryable = response.status_code == 429 or response.status_code >= 500
        raise AuthError(
            f"Google 没有发令牌（{response.status_code} {reason[:160]}）：{what}", retryable=retryable
        )
    try:
        lifetime = float(answer.get("expires_in") or LIFETIME)
    except (TypeError, ValueError):
        lifetime = LIFETIME
    _tokens[key] = (token, now + max(lifetime, 0))
    return token
