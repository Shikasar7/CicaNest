"""nest-egress: the only way out of the companions' containers (伙伴模式).

Runs on its own in the nest-egress container (plain Python 3, no nest imports): an HTTP proxy with CONNECT
tunnels. The companions' containers sit on an internal Docker network with no way out but this, through
HTTP_PROXY / HTTPS_PROXY. Every host is resolved here and connected to by the address found — one that is
this machine, the local network, link-local, CGNAT (Tailscale), multicast or otherwise not public is
refused, so pointing a name at 192.168.x.x does not get through. SMTP is refused too.

Each connection is one JSON line in /log/egress.log (moved to egress.log.1 past LOG_BYTES): when, whose
(the user name in the proxy URL: http://<her folder>:nest@nest-egress:3128 — a label; the password means
nothing), from which container (containers.json: its address there -> its name; her own or one of her
plugins', so what a plugin reaches can be held against what it declared — shown only, nothing is decided
by it), host, port, whether it went through, and why not.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

PORT = 3128
BLOCKED_PORTS = frozenset({25, 465, 587})  # mail
LOG_BYTES = 2 * 1024 * 1024
HEAD_MAX = 64 * 1024
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 300.0

Resolve = Callable[[str, int], Awaitable[list[str]]]


def public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


FAKE = ipaddress.ip_network("198.18.0.0/15")
RESOLVERS = (
    "https://223.5.5.5/resolve?name={}&type=1",
    "https://1.12.12.12/resolve?name={}&type=1",
    "https://1.1.1.1/dns-query?name={}&type=A",
)


def _fake(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]) in FAKE
    except ValueError:
        return False


def _over_https(host: str) -> list[str]:
    """IPv4 addresses from public DNS over HTTPS (a proxy app's fake answer says nothing); [] when none."""
    for template in RESOLVERS:
        request = urllib.request.Request(template.format(host), headers={"accept": "application/dns-json"})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as r:
                data = json.loads(r.read(65536))
        except Exception:
            continue
        found = [
            str(a.get("data")) for a in data.get("Answer") or [] if isinstance(a, dict) and a.get("type") == 1
        ]
        if found or data.get("Status") == 3:
            return found
    return []


async def resolve(host: str, port: int) -> list[str]:
    """Where `host` really points. A proxy app in TUN mode answers every name with a fake address of its
    own (198.18.0.0/15); then the real one is asked of public DNS over HTTPS, and connected to directly."""
    try:
        return [str(ipaddress.ip_address(host.split("%")[0]))]
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    found = list(dict.fromkeys(info[4][0] for info in infos))
    if found and not any(_fake(a) for a in found):
        return found
    real = await asyncio.to_thread(_over_https, host)
    if not real:
        raise OSError("no real address")
    return real


class Log:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, **entry: object) -> None:
        line = json.dumps({"t": round(time.time(), 3), **entry}, ensure_ascii=False)
        if self.path is None:
            print(line, flush=True)
            return
        try:
            if self.path.exists() and self.path.stat().st_size > LOG_BYTES:
                self.path.replace(self.path.with_name(self.path.name + ".1"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print(f"egress log: {exc}", file=sys.stderr, flush=True)


def _who(headers: list[str]) -> str:
    for line in headers:
        name, _, value = line.partition(":")
        if name.strip().lower() == "proxy-authorization":
            scheme, _, token = value.strip().partition(" ")
            if scheme.lower() == "basic":
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    return base64.b64decode(token).decode("utf-8").partition(":")[0][:64]
    return ""


def _target(method: str, target: str) -> tuple[str, int, str] | None:
    """(host, port, path for the origin) from a proxy request line; None when unreadable."""
    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        host = host.strip("[]")
        return (host, int(port), "") if host and port.isdigit() else None
    if not target.lower().startswith("http://"):
        return None
    try:
        parts = urlsplit(target)
        port = parts.port or 80
    except ValueError:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return (parts.hostname, port, path) if parts.hostname else None


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (TimeoutError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class Networks:
    """Whose network the proxy was reached on: nest writes the proxy's address on each companion's network
    (networks.json beside the log). A connection is hers by where it arrived, never by the name it gives:
    one that arrives elsewhere is logged as "?" plus the name it gave."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._seen: tuple[float, dict[str, str]] = (-1.0, {})

    def whose(self, local: str) -> str:
        if self.path is None:
            return ""
        try:
            changed = self.path.stat().st_mtime
            if changed != self._seen[0]:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._seen = (
                    changed,
                    {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {},
                )
        except (OSError, ValueError):
            return ""
        return self._seen[1].get(local, "")


class Proxy:
    def __init__(
        self,
        log: Log,
        *,
        resolver: Resolve = resolve,
        allowed: Callable[[str], bool] = public,
        networks: Networks | None = None,
        containers: Networks | None = None,
    ) -> None:
        self.log = log
        self.resolver = resolver
        self.allowed = allowed
        self.networks = networks or Networks(None)
        self.containers = containers or Networks(None)  # a container's address -> its name

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._handle(reader, writer)
        except Exception as exc:  # one bad client never stops the proxy
            print(f"egress: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            return
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            await self._refuse(writer, 400, {"who": ""}, "", 0, "看不懂的请求")
            return
        method, target, version = parts
        local = writer.get_extra_info("sockname")
        peer = writer.get_extra_info("peername")
        whose = self.networks.whose(str(local[0]) if local else "") or "?" + _who(lines[1:])
        sender = self.containers.whose(str(peer[0]) if peer else "")
        who = {"who": whose, **({"from": sender} if sender else {})}
        found = _target(method.upper(), target)
        if found is None:
            await self._refuse(writer, 400, who, target[:200], 0, "只代理 http:// 和 CONNECT")
            return
        host, port, path = found
        if port in BLOCKED_PORTS or not 0 < port < 65536:
            await self._refuse(writer, 403, who, host, port, "这个端口不放行")
            return
        try:
            addresses = await asyncio.wait_for(self.resolver(host, port), CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            await self._refuse(writer, 502, who, host, port, "域名解析失败")
            return
        if not addresses or not all(self.allowed(a) for a in addresses):
            await self._refuse(writer, 403, who, host, port, "指向本机或局域网")
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(addresses[0], port), CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError):
            await self._refuse(writer, 502, who, host, port, "连不上")
            return
        self.log.write(**who, host=host, port=port, ok=True)
        if method.upper() == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
        else:
            kept = [
                line
                for line in lines[1:]
                if line and not line.lower().startswith(("proxy-", "connection:", "keep-alive:"))
            ]
            request = "\r\n".join([f"{method} {path} {version}", *kept, "Connection: close", "", ""])
            upstream_writer.write(request.encode("latin-1"))
            await upstream_writer.drain()
        await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))

    async def _refuse(
        self, writer: asyncio.StreamWriter, status: int, who: dict[str, str], host: str, port: int, why: str
    ) -> None:
        self.log.write(**who, host=host, port=port, ok=False, why=why)
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[status]
        body = f"nest-egress: {why}\n".encode()
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        with contextlib.suppress(ConnectionError, OSError):
            await writer.drain()


async def main(log: Log, networks: Networks, containers: Networks | None = None) -> None:
    proxy = Proxy(log, networks=networks, containers=containers)
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", PORT, limit=HEAD_MAX)
    print(f"nest-egress listening on {PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    folder = Path(os.environ.get("EGRESS_LOG_DIR", "/log"))
    there = folder.is_dir()
    asyncio.run(
        main(
            Log(folder / "egress.log" if there else None),
            Networks(folder / "networks.json" if there else None),
            Networks(folder / "containers.json" if there else None),
        )
    )
