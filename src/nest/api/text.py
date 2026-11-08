"""Checks shared by endpoints that take short typed text (names, aliases, persona text).

Lengths count Unicode code points (`len()`), as the console does with `[...text].length`.
"""

from __future__ import annotations

import unicodedata
from typing import Any
from urllib.parse import urlsplit

# Control characters, lone surrogates (they cannot be stored as UTF-8) and line/paragraph separators.
BAD_CATEGORIES = ("Cc", "Cs", "Zl", "Zp")


def has_bad_chars(text: str, *, allow: str = "") -> bool:
    return any(c not in allow and unicodedata.category(c) in BAD_CATEGORIES for c in text)


def proxy_url(value: Any) -> str | None:
    """A proxy address as http(s)://host:port, "" for none, None when it is not one. Credentials, paths and
    other schemes (socks5) are refused, so a stored proxy never holds a secret."""
    text = value.strip() if isinstance(value, str) else None
    if text == "":
        return ""
    try:
        parts = urlsplit(text or "")
        port = parts.port
    except ValueError:  # a malformed host (e.g. "[::1:7890", missing its "]") or port
        return None
    if (
        text is None
        or parts.scheme not in ("http", "https")
        or not parts.hostname
        or port is None
        or "@" in parts.netloc
        or parts.path not in ("", "/")
        or "?" in text
        or "#" in text
        or any(c.isspace() for c in text)
    ):
        return None
    return f"{parts.scheme}://{parts.netloc}"
