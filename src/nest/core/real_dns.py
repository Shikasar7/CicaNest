"""Where a host name really points, for the checks that keep nest's fetches off this machine and the local
network (读网页, the plugin market).

A proxy app in TUN mode with "fake IP" DNS (Clash, Surge… on the Mac) answers every name with an address
of its own from 198.18.0.0/15 and resolves the real one only when connected to. Such an answer says
nothing about where the name points, so it is never taken as one: the name is then asked of public
DNS-over-HTTPS resolvers instead. If none answers, the name counts as unresolved and the caller refuses.

The same logic, standalone, is in nest.core.egress_proxy (it runs in a container without nest).
Blocking (DNS and HTTPS): call from a worker thread.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import time
import urllib.request
from collections.abc import Callable

FAKE = ipaddress.ip_network("198.18.0.0/15")
RESOLVERS = (
    "https://223.5.5.5/resolve?name={}&type=1",
    "https://1.12.12.12/resolve?name={}&type=1",
    "https://1.1.1.1/dns-query?name={}&type=A",
)
TIMEOUT = 5.0
CACHE_SECONDS = 300.0
_cache: dict[str, tuple[float, list[str]]] = {}


def fake(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]) in FAKE
    except ValueError:
        return False


def system(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))


def over_https(host: str, fetch: Callable[[str], bytes] | None = None) -> list[str]:
    """The name's IPv4 addresses from public DNS-over-HTTPS; [] when none answers."""
    get = fetch or _get
    for template in RESOLVERS:
        try:
            data = json.loads(get(template.format(host)))
        except Exception:  # one resolver down or blocked: the next
            continue
        found = [
            str(a.get("data")) for a in data.get("Answer") or [] if isinstance(a, dict) and a.get("type") == 1
        ]
        if found or data.get("Status") == 3:  # 3: no such name
            return found
    return []


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    handler = urllib.request.ProxyHandler({})  # straight out, never through a configured proxy
    opener = urllib.request.build_opener(
        handler, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    with opener.open(request, timeout=TIMEOUT) as response:
        return response.read(65536)


def addresses(
    host: str,
    *,
    resolve: Callable[[str], list[str]] | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> list[str]:
    """Where `host` points: itself when it is an address; the system's answer unless that is a proxy app's
    fake one; else public DNS over HTTPS. [] when it cannot be known."""
    host = host.strip("[]")
    try:
        return [str(ipaddress.ip_address(host.split("%")[0]))]
    except ValueError:
        pass
    found = (resolve or system)(host)
    if found and not any(fake(a) for a in found):
        return found
    cached = _cache.get(host)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    real = over_https(host, fetch)
    if real:
        _cache[host] = (time.monotonic() + CACHE_SECONDS, real)
    return real
