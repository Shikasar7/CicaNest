"""Signing in from other devices: one password for nest, then a session per device (browser).

Both live in the settings table: the password as a salted scrypt hash (key remote_password), the devices
as a list (key remote_sessions) holding only a SHA-256 of each device's cookie. Every change is one
read-modify-write transaction, so the console and `nest set-password` (another process) cannot lose each
other's writes. Nothing is cached: a device signed out from either place is out at its next request.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from nest.storage.db import Database

PASSWORD_KEY = "remote_password"
DEVICES_KEY = "remote_sessions"
COOKIE = "nest_device"
SESSION_SECONDS = 30 * 86400
TOUCH_SECONDS = 600  # how stale a device's "last seen" may get before a request updates it
MAX_DEVICES = 30  # the oldest sign-in goes first
MIN_PASSWORD, MAX_PASSWORD = 8, 128
SCRYPT = {"n": 2**15, "r": 8, "p": 1}  # about 50 ms; 32 MiB
CHECKS_AT_ONCE = 2  # password checks hashing at the same time; more wait their turn


def hash_password(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, dklen=32, **SCRYPT)
    return {
        "scheme": "scrypt",
        **SCRYPT,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
    }


def password_matches(password: str, stored: Any) -> bool:
    try:
        if stored["scheme"] != "scrypt":
            return False
        salt = base64.b64decode(stored["salt"])
        expected = base64.b64decode(stored["hash"])
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(stored["n"]),
            r=int(stored["r"]),
            p=int(stored["p"]),
            maxmem=64 * 1024 * 1024,
            dklen=len(expected),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, expected)


def password_problem(password: str) -> str | None:
    """Why a new password cannot be used, or None."""
    if len(password) < MIN_PASSWORD:
        return f"密码至少要 {MIN_PASSWORD} 个字符"
    if len(password) > MAX_PASSWORD:
        return f"密码最多 {MAX_PASSWORD} 个字符"
    if password != password.strip():
        return "密码的开头和结尾不能是空格"
    return None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Device:
    id: str  # shown in the console and used to sign the device out; not the cookie
    digest: str  # SHA-256 of the cookie
    created_at: float
    last_seen_at: float
    expires_at: float
    ip: str
    agent: str


def _devices(value: Any) -> list[Device]:
    devices = []
    for item in value if isinstance(value, list) else []:
        try:
            devices.append(Device(**item))
        except TypeError:  # edited by hand or from a newer nest: ignore that entry
            continue
    return devices


def _loads(row: Any) -> Any:
    try:
        return json.loads(row["value"]) if row else None
    except ValueError:
        return None


async def _get(conn: Any, key: str) -> Any:
    async with conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
        return _loads(await cursor.fetchone())


async def _put(conn: Any, key: str, value: Any, now: float) -> None:
    await conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), now),
    )


class RemoteAccess:
    def __init__(self, db: Database, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        self._hashing = asyncio.Semaphore(CHECKS_AT_ONCE)

    async def _read(self, key: str) -> Any:
        return _loads(await self.db.fetchone("SELECT value FROM settings WHERE key = ?", (key,)))

    async def _change(self, key: str, change: Callable[[Any], Any]) -> Any:
        """Replace the value of `key` by change(current value) in one transaction; returns the new value."""
        async with self.db.transaction() as conn:
            value = change(await _get(conn, key))
            await _put(conn, key, value, self.clock())
        return value

    # -- the password ---------------------------------------------------------------------------------

    async def password_set(self) -> bool:
        return isinstance(await self._read(PASSWORD_KEY), dict)

    async def check(self, password: str) -> str | None:
        """None when wrong; when right, a tag of the password it matched (its salt), for sign_in()."""
        stored = await self._read(PASSWORD_KEY)
        if not isinstance(stored, dict):
            return None
        async with self._hashing:  # a burst of guesses must not tie up every worker thread
            matched = await asyncio.to_thread(password_matches, password, stored)
        return str(stored.get("salt")) if matched else None

    async def set_password(self, password: str, *, keep: str | None = None) -> None:
        """Set a new password (validated by the caller) and sign out every device but `keep` (a device id)."""
        stored = await asyncio.to_thread(hash_password, password)
        await self._change(PASSWORD_KEY, lambda _: stored)
        await self._change(DEVICES_KEY, lambda value: [asdict(d) for d in _devices(value) if d.id == keep])

    # -- devices --------------------------------------------------------------------------------------

    async def sign_in(self, *, ip: str, agent: str, tag: str) -> tuple[str, Device] | None:
        """A new device session for a password check() passed: (the cookie value, the device). None when
        that password was replaced in the meantime: a check racing a change must not outlive it."""
        token = secrets.token_urlsafe(32)
        now = self.clock()
        device = Device(
            id=secrets.token_hex(6),
            digest=_digest(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_SECONDS,
            ip=ip,
            agent=agent[:200],
        )

        def add(value: Any) -> list[dict[str, Any]]:
            kept = [d for d in _devices(value) if d.expires_at > now]
            kept = sorted(kept, key=lambda d: d.last_seen_at)[-(MAX_DEVICES - 1) :]
            return [*(asdict(d) for d in kept), asdict(device)]

        async with self.db.transaction() as conn:
            stored = await _get(conn, PASSWORD_KEY)
            if not isinstance(stored, dict) or str(stored.get("salt")) != tag:
                return None
            await _put(conn, DEVICES_KEY, add(await _get(conn, DEVICES_KEY)), self.clock())
        return token, device

    async def device(self, token: str, *, ip: str) -> tuple[Device, bool] | None:
        """The signed-in device a cookie belongs to and whether its 30 days were just renewed (the cookie
        should be sent again), or None (unknown, signed out or expired). A device seen again after a while
        gets its last-seen time and address updated and its 30 days renewed."""
        digest = _digest(token)
        now = self.clock()
        found = next((d for d in _devices(await self._read(DEVICES_KEY)) if d.digest == digest), None)
        if found is None or found.expires_at <= now:
            return None
        if now - found.last_seen_at < TOUCH_SECONDS and found.ip == ip:
            return found, False
        touched = Device(
            **(asdict(found) | {"last_seen_at": now, "expires_at": now + SESSION_SECONDS, "ip": ip})
        )

        def touch(value: Any) -> list[dict[str, Any]]:
            return [asdict(touched if d.digest == digest else d) for d in _devices(value)]

        await self._change(DEVICES_KEY, touch)
        return touched, True

    async def devices(self) -> list[Device]:
        now = self.clock()
        return [d for d in _devices(await self._read(DEVICES_KEY)) if d.expires_at > now]

    async def sign_out(self, device_id: str) -> bool:
        """False: no such device."""
        before = _devices(await self._read(DEVICES_KEY))
        if not any(d.id == device_id for d in before):
            return False
        await self._change(
            DEVICES_KEY, lambda value: [asdict(d) for d in _devices(value) if d.id != device_id]
        )
        return True

    async def sign_out_all(self, *, keep: str | None = None) -> None:
        await self._change(DEVICES_KEY, lambda value: [asdict(d) for d in _devices(value) if d.id == keep])


class Throttle:
    """Password guesses. Each address gets `limit` attempts per `window` seconds, counted when the attempt
    starts (so a burst of parallel requests cannot all get through before the first is judged) and
    forgiven when one succeeds. All addresses together get `total` wrong ones per window, against
    guessing from many addresses at once. When over: wait until the oldest counted one is `window` old.
    """

    def __init__(
        self,
        *,
        limit: int = 5,
        total: int = 30,
        window: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.total = total
        self.window = window
        self.clock = clock
        self._attempts: dict[str, deque[float]] = {}
        self._failures: deque[float] = deque(maxlen=total)

    def _wait(self, times: deque[float] | None, limit: int) -> float:
        if not times:
            return 0.0
        now = self.clock()
        while times and now - times[0] > self.window:
            times.popleft()
        return max(0.0, times[0] + self.window - now) if len(times) >= limit else 0.0

    def attempt(self, ip: str) -> float:
        """Seconds `ip` must wait first (nothing counted), or 0.0 with this attempt counted."""
        wait = max(self._wait(self._attempts.get(ip), self.limit), self._wait(self._failures, self.total))
        if wait:
            return wait
        self._attempts.setdefault(ip, deque(maxlen=self.limit)).append(self.clock())
        if len(self._attempts) > 1000:  # many addresses: forget those whose attempts have all expired
            now = self.clock()
            for key in [k for k, v in self._attempts.items() if not v or now - v[-1] > self.window]:
                del self._attempts[key]
        return 0.0

    def failed(self) -> None:
        self._failures.append(self.clock())

    def succeeded(self, ip: str) -> None:
        self._attempts.pop(ip, None)
