"""Which networks may reach nest when it listens beyond this machine (NEST_REMOTE): this machine, the home
network, and the user's Tailscale network. Requests from anywhere else are refused before any route runs,
whatever password they carry.

The Host header is checked too, against DNS rebinding: a web page on some other site can make the browser
send requests to nest, but they arrive with that site's name. Accepted are IP addresses (a rebinding page
cannot produce one) and this machine's own names.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import psutil

log = logging.getLogger(__name__)

Kind = Literal["local", "lan", "tailscale"]

TAILSCALE = tuple(ipaddress.ip_network(n) for n in ("100.64.0.0/10", "fd7a:115c:a1e0::/48"))
LAN = tuple(
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10")
)
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"  # the Mac app's command line
TAILSCALE_TIMEOUT = 3.0


def address_kind(text: str | None) -> Kind | None:
    """The network an IP address belongs to; None for any other address (the internet) or a non-address."""
    if not text:
        return None
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])  # fe80::1%en0: drop the interface
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "local"
    if any(ip.version == net.version and ip in net for net in TAILSCALE):
        return "tailscale"
    if any(ip.version == net.version and ip in net for net in LAN):
        return "lan"
    return None


def split_host(value: str) -> tuple[str, int | None] | None:
    """A Host header -> (name: lower case, no brackets, no trailing dot; port or None). None: malformed."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        name, rest = value[1:end], value[end + 1 :]
        if rest and not rest.startswith(":"):
            return None
        port = rest[1:]
    else:
        name, _, port = value.partition(":")
        if ":" in port:  # a bare IPv6 address is not a valid Host
            return None
    name = name.rstrip(".")
    if not name or (port and not port.isdigit()) or (port and int(port) > 65535):
        return None
    return name, int(port) if port else None


def host_allowed(header: str, *, port: int, remote: bool, names: Iterable[str]) -> bool:
    """Whether a request's Host header names nest, always with nest's own port (no proxy in front is
    supported): the loopback names, and with remote access also any local-network or Tailscale IP and this
    machine's names."""
    parsed = split_host(header)
    if parsed is None:
        return False
    name, given = parsed
    if given != port:
        return False
    if name in LOOPBACK_NAMES:
        return True
    if not remote:
        return False
    return address_kind(name) is not None or name in names


def bonjour_name() -> str | None:
    """The Mac's local network name (System Settings > Sharing), what <name>.local resolves to; None
    elsewhere. Not the host name: macOS may report that as a placeholder such as "bogon" after a network
    change (it did once Tailscale was installed)."""
    if sys.platform != "darwin":
        return None
    try:
        done = subprocess.run(
            ["/usr/sbin/scutil", "--get", "LocalHostName"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def machine_names(extra: Iterable[str] = ()) -> set[str]:
    """This machine's own names: its host name and Bonjour name, each with and without .local, plus
    `extra`."""
    names = {name.strip().lower().rstrip(".") for name in extra if name.strip()}
    for host in (socket.gethostname(), bonjour_name() or ""):
        host = host.strip().lower().rstrip(".")
        if host:
            short = host.split(".")[0]
            names |= {host, short, f"{short}.local"}
    return names


def tailscale_self() -> tuple[str, list[str]] | None:
    """This machine's Tailscale name (e.g. mac-mini.tail1234.ts.net) and addresses, when Tailscale is
    installed and running; None otherwise. Asks the `tailscale` command line, waiting at most 3 seconds."""
    exe = shutil.which("tailscale") or (TAILSCALE_APP if Path(TAILSCALE_APP).exists() else None)
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, "status", "--json"], capture_output=True, timeout=TAILSCALE_TIMEOUT, check=False
        )
        status = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    me = status.get("Self") if isinstance(status, dict) else None
    if not isinstance(me, dict):
        return None
    name = str(me.get("DNSName") or "").strip().lower().rstrip(".")
    addresses = [ip for ip in me.get("TailscaleIPs") or [] if isinstance(ip, str)]
    return name, addresses


# Adapters for virtual machines and containers on this machine (WSL/Hyper-V, Docker, VMware, VirtualBox,
# macOS Virtualization): other devices cannot reach their addresses, so they are not offered as links.
VIRTUAL_ADAPTERS = (
    "vethernet",
    "docker",
    "br-",
    "veth",
    "bridge",
    "vmnet",
    "vboxnet",
    "virtualbox",
    "vmware",
)


def _home_first(address: str) -> int:
    """192.168.x before 10.x before 172.16–31.x: the order home routers are most likely to use."""
    return 0 if address.startswith("192.168.") else 1 if address.startswith("10.") else 2


def reachable_addresses() -> list[tuple[str, Kind]]:
    """This machine's IPv4 addresses that other devices can use (local network and Tailscale), for the
    console to show as links: interfaces that are up, not link-local, not a virtual machine's or
    container's network. Home addresses first, then Tailscale."""
    up = {name for name, stats in psutil.net_if_stats().items() if stats.isup}
    found: set[tuple[str, Kind]] = set()
    for name, addresses in psutil.net_if_addrs().items():
        if name not in up:
            continue
        for address in addresses:
            if address.family != socket.AF_INET or address.address.startswith("169.254."):
                continue
            kind = address_kind(address.address)
            if kind == "tailscale" or (kind == "lan" and not name.lower().startswith(VIRTUAL_ADAPTERS)):
                found.add((address.address, kind))
    return sorted(
        found, key=lambda item: (item[1] != "lan", _home_first(item[0]), ipaddress.ip_address(item[0]))
    )
