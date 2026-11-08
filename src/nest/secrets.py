"""Secrets referenced by name (e.g. NEST_QQ_123_WS_TOKEN), stored in <home>/.env.

Read at startup and on explicit reload (after each write, and before each model change, since the CLI
may have written the file) — a lookup never re-parses it. Process environment
variables take precedence over .env. Values are taken literally: `${OTHER}` inside a value is never
expanded (a crafted key could otherwise pull in another secret, such as a QQ token, and send it anywhere).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Protocol

from dotenv import dotenv_values, set_key, unset_key

REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretReader(Protocol):
    """What model factories get: lookup by name only."""

    def get(self, ref: str) -> str: ...


def _check_ref(ref: str) -> None:
    if not REF.match(ref):
        raise ValueError(f"密钥名只能用字母、数字和下划线：{ref!r}")


class SecretView:
    """Read-only view of a store with one value replaced ("" = cleared): lets a model be built and
    checked with a new key before that key is written to .env."""

    __slots__ = ("_base", "_ref", "_value")

    def __init__(self, base: SecretReader, ref: str, value: str) -> None:
        self._base = base
        self._ref = ref
        self._value = value

    def get(self, ref: str) -> str:
        if ref and ref == self._ref:
            return self._value
        return self._base.get(ref)

    def __repr__(self) -> str:  # never show the value
        return f"SecretView(ref={self._ref!r})"


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._values: dict[str, str] = {}
        self._present: set[str] = set()  # every name in the file, including ones without a value
        self.reload()

    def reload(self) -> None:
        if self.path.is_file():
            raw = dotenv_values(self.path, interpolate=False)
            self._values = {k: v for k, v in raw.items() if v is not None}
            self._present = set(raw)
        else:
            self._values, self._present = {}, set()

    def get(self, ref: str) -> str:
        if not ref:
            return ""
        return os.environ.get(ref) or self._values.get(ref, "")

    def source(self, ref: str) -> Literal["env", "file", ""]:
        """Where get(ref) comes from: a process environment variable, the .env file, or nowhere."""
        if not ref:
            return ""
        if os.environ.get(ref):
            return "env"
        return "file" if self._values.get(ref) else ""

    def file_value(self, ref: str) -> str | None:
        """The raw .env value (None when the name is not in the file), e.g. to roll a write back."""
        return self._values.get(ref) if ref in self._present else None

    def entries(self) -> dict[str, str]:
        """Every name and value in .env, for scrubbing text shown in the console. Never log or return them."""
        return dict(self._values)

    def overlay(self, ref: str, value: str) -> SecretView:
        return SecretView(self, ref, value)

    def set(self, ref: str, value: str) -> None:
        _check_ref(ref)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        set_key(self.path, ref, value, quote_mode="always")
        self.reload()

    def unset(self, ref: str) -> None:
        """Remove `ref` from .env (no-op when it is not there). Never touches the process environment."""
        _check_ref(ref)
        self.reload()  # the CLI may have written the file since the last read
        if ref not in self._present:
            return
        unset_key(self.path, ref, quote_mode="always")
        self.reload()
