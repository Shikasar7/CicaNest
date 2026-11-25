"""插件市场: a list of plugins somewhere on the web, to install from.

The list (the index) is one JSON file at an https address the user sets (setting plugin_market; none by
default):

    {"format": 1, "plugins": [{"id": "…", "name": "…", "version": "1.0.0", "author": "…",
      "description": "…", "kinds": ["skills"], "trust": "content" | "guides" | "code" | "trusted",
      "url": "https://…/x.zip", "sha256": "<64 hex>", "size": 12345,
      "permissions": {"network": [], "secrets": [], "confirm": []},
      "nest": "0.6.0", "author_key": "<base64>", "signature": "<base64>"}],
     "report": "https://…/issues/new?title={id}"}

`nest`: the nest it needs at least. `author_key`, `signature`: the author's Ed25519 key and signature of what
the zip is (signed_text), so a list that was tampered with cannot pass another zip off as theirs: an upgrade
of a signed plugin must be signed with the key it was installed with. `report`: where to say something is
wrong with a plugin ({id} and {version} filled in).

Installing one downloads its zip (https, not this machine or the local network, at most the size it says
and ZIP_MAX), checks it against the sha256 the list gives, and hands it to the usual two steps (read and
shown, then installed: nest.core.plugins). The zip's own plugin.json must be the plugin the list says (id
and version), and what the user is shown is read from the zip, not taken from the list.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from nest.core import ed25519
from nest.core.plugins import ID, VERSION, ZIP_MAX, newer
from nest.core.tools import pin, private_address
from nest.watch.fetch import WatchError, fetch

log = logging.getLogger(__name__)

SETTING = "plugin_market"
INDEX_MAX = 2 * 1024 * 1024
TIMEOUT = 60.0
SHA = re.compile(r"[0-9a-f]{64}")
TRUSTS = ("content", "guides", "code", "trusted")  # least first
TEXT_MAX = 500


class MarketError(Exception):
    """Why the list or a plugin could not be had, in one Chinese sentence."""


async def check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise MarketError("地址要以 https:// 开头")
    if await asyncio.to_thread(private_address, parts.hostname):
        raise MarketError("不能用本机或局域网的地址")
    return url


def _trust(item: dict[str, Any]) -> str:
    """What the list says, but never less than what its kinds mean (a list may play a plugin down; the
    zip's own plugin.json is shown again before installing)."""
    kinds = item.get("kinds") if isinstance(item.get("kinds"), list) else []
    if "python" in kinds:
        least = "trusted"
    elif "mcp" in kinds:
        least = "code"
    elif "skills" in kinds or "companion" in kinds:
        least = "guides"
    else:
        least = "content"
    said = item.get("trust") if item.get("trust") in TRUSTS else "code"
    return max(said, least, key=TRUSTS.index)


def signed_text(plugin_id: str, version: str, sha256: str) -> bytes:
    """What an author signs: this very zip, as this version of this plugin."""
    return f"cicanest-plugin\n{plugin_id}\n{version}\n{sha256}\n".encode()


def signed_by(entry: dict[str, Any]) -> str:
    """The key an entry is rightly signed with; "" when it is not signed. MarketError when the signature does
    not fit."""
    if not entry["author_key"] and not entry["signature"]:
        return ""
    try:
        key = base64.b64decode(entry["author_key"], validate=True)
        signature = base64.b64decode(entry["signature"], validate=True)
    except (binascii.Error, ValueError):
        raise MarketError("作者签名的格式不对") from None
    if not ed25519.verify(key, signed_text(entry["id"], entry["version"], entry["sha256"]), signature):
        raise MarketError("作者签名对不上，没有装")
    return str(entry["author_key"])


def signature_state(entry: dict[str, Any], installed_key: str) -> str:
    """For the list: "" (not signed), "signed", "bad" (the signature does not fit) or "changed" (what is
    installed was signed by someone else, or this one is not signed while that was)."""
    try:
        key = signed_by(entry)
    except MarketError:
        return "bad"
    if installed_key and key != installed_key:
        return "changed"
    return "signed" if key else ""


def report_link(template: str, entry: dict[str, Any]) -> str:
    if not template.startswith("https://"):
        return ""
    return template.replace("{id}", quote(entry["id"])).replace("{version}", quote(entry["version"]))[:500]


def _entry(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    plugin_id, version = str(item.get("id") or ""), str(item.get("version") or "")
    url, sha = str(item.get("url") or ""), str(item.get("sha256") or "").lower()
    size = item.get("size")
    if not ID.fullmatch(plugin_id) or plugin_id.startswith("nest.") or not VERSION.fullmatch(version):
        return None
    if not url.startswith("https://") or not SHA.fullmatch(sha):
        return None
    if not isinstance(size, int) or not 0 < size <= ZIP_MAX:
        return None
    asked = item.get("permissions") if isinstance(item.get("permissions"), dict) else {}
    return {
        "id": plugin_id,
        "name": str(item.get("name") or plugin_id)[:80],
        "version": version,
        "author": str(item.get("author") or "")[:80],
        "description": str(item.get("description") or "")[:TEXT_MAX],
        "kinds": [str(k) for k in item.get("kinds") or [] if isinstance(k, str)][:6],
        "trust": _trust(item),
        "nest": str(item.get("nest") or "").removeprefix(">=").strip()[:20],
        "author_key": str(item.get("author_key") or "")[:100],
        "signature": str(item.get("signature") or "")[:200],
        "url": url,
        "sha256": sha,
        "size": size,
        "permissions": {
            "network": [str(d)[:100] for d in asked.get("network") or []][:20],
            "secrets": [
                str(s.get("label") or s.get("name") or "")[:60]
                for s in asked.get("secrets") or []
                if isinstance(s, dict)
            ][:10],
            "confirm": [str(t)[:60] for t in asked.get("confirm") or []][:20],
        },
    }


async def read_index(
    url: str, *, proxy: str | None, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    """The plugins the list offers (unusable entries left out). MarketError when it cannot be read."""
    url = await check_url(url)
    try:
        page = await fetch(
            url, proxy=proxy, transport=transport, timeout=TIMEOUT, max_bytes=INDEX_MAX, pin=pin
        )
    except WatchError as exc:
        raise MarketError(f"插件清单读不到：{exc}") from None
    try:
        data = json.loads(page.body)
    except ValueError:
        raise MarketError("插件清单不是 JSON") from None
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("plugins"), list):
        raise MarketError('插件清单的格式不认识（要有 "format": 1 和 plugins 列表）')
    found: dict[str, dict[str, Any]] = {}
    for item in data["plugins"][:500]:
        entry = _entry(item)
        if entry is not None:
            entry["report"] = report_link(str(data.get("report") or ""), entry)
            found.setdefault(entry["id"], entry)
    return sorted(found.values(), key=lambda e: e["name"].lower())


def updates(entries: list[dict[str, Any]], installed: dict[str, Any]) -> list[dict[str, str]]:
    """What is installed and has a newer version in the list: [{id, name, from, to}]."""
    found = []
    for entry in entries:
        have = installed.get(entry["id"])
        if have is not None and newer(entry["version"], have[0].version):
            found.append(
                {"id": entry["id"], "name": entry["name"], "from": have[0].version, "to": entry["version"]}
            )
    return found


class MarketWatch:
    """有新版本时提醒: looks at the list once a day (and when the market page read it) for newer versions of
    what is installed. Nothing is downloaded or installed."""

    EVERY = 24 * 3600.0
    FIRST = 10 * 60.0  # after nest started

    def __init__(
        self,
        settings: Any,
        plugins: Any,
        broadcaster: Any,
        transport: Callable[[], httpx.AsyncBaseTransport | None] = lambda: None,
    ) -> None:
        self.settings = settings
        self.plugins = plugins
        self.broadcaster = broadcaster
        self._transport = transport  # tests put one in: asked each time
        self.found: list[dict[str, str]] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="market")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        await asyncio.sleep(self.FIRST)
        while True:
            try:
                await self.look()
            except Exception:
                log.exception("看插件有没有新版本出错")
            await asyncio.sleep(self.EVERY)

    async def look(self) -> list[dict[str, str]]:
        url = (await self.settings.all()).get(SETTING)
        if not isinstance(url, str) or not url or not self.plugins.installed:
            return self.take([])
        proxy = (await self.settings.fetch_proxy()) or None
        try:
            entries = await read_index(url, proxy=proxy, transport=self._transport())
        except MarketError as exc:
            log.info("看插件有没有新版本：%s", exc)
            return self.found
        return self.take(updates(entries, self.plugins.installed))

    def take(self, found: list[dict[str, str]]) -> list[dict[str, str]]:
        if found != self.found:
            self.found = found
            self.broadcaster.publish("plugins", {})
        return found


async def download(
    entry: dict[str, Any], *, proxy: str | None, transport: httpx.AsyncBaseTransport | None = None
) -> bytes:
    """The plugin's zip, checked against the list's sha256. MarketError otherwise."""
    url = await check_url(entry["url"])
    try:
        page = await fetch(
            url, proxy=proxy, transport=transport, timeout=TIMEOUT * 5, max_bytes=entry["size"], pin=pin
        )
    except WatchError as exc:
        raise MarketError(f"插件下载不了：{exc}") from None
    if hashlib.sha256(page.body).hexdigest() != entry["sha256"]:
        raise MarketError("下载的插件和清单里写的对不上（校验值不同），没有装")
    return page.body
