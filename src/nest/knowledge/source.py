"""A project's source code, for the bot to read when its help docs do not answer a question
(nest.knowledge.investigate): a checkout on this machine of the project's public repository, kept current by
deploy/mac/knowledge/sync.sh.

Only reading, and only inside the checkout: paths are resolved and must stay under it (no `..`, no
symlinks out), hidden folders (.git…), dependencies and build output are skipped, and so is anything that is
not text or is too big. Every function blocks (it reads files): call them from a worker thread.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {"node_modules", "dist", "build", "out", "vendor", "coverage", "__pycache__", "venv"}
MAX_FILE = 512 * 1024  # bytes: bigger files are generated or bundled, not read
MATCHES = 40  # search results returned at most
LINE_CHARS = 200  # a matched line is cut to this
READ_LINES = 200  # lines one read returns at most
LIST_ENTRIES = 120


class SourceError(ValueError):
    """A request the source cannot answer (a path outside it, no such file…), in one Chinese sentence."""


@dataclass(frozen=True, slots=True)
class Revision:
    commit: str  # short hash, "" when the folder is not a git checkout
    date: str  # its commit date (YYYY-MM-DD)


class SourceTree:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._files: list[str] | None = None

    def ready(self) -> bool:
        return self.root.is_dir()

    def files(self) -> list[str]:
        """Every readable text file, as a path relative to the root with forward slashes."""
        if self._files is None:
            found = []
            for folder, dirs, names in os.walk(self.root):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
                for name in sorted(names):
                    path = Path(folder) / name
                    if name.startswith(".") or path.is_symlink() or not path.is_file():
                        continue
                    if path.stat().st_size > MAX_FILE or not _is_text(path):
                        continue
                    found.append(path.relative_to(self.root).as_posix())
            self._files = sorted(found)
        return self._files

    def _resolve(self, rel: str) -> Path:
        path = (self.root / rel.strip().lstrip("/")).resolve()
        if not path.is_relative_to(self.root):
            raise SourceError(f"{rel} 不在源码里")
        return path

    def search(self, pattern: str, under: str = "") -> list[str]:
        """Lines matching `pattern` (a regular expression, case-insensitive; taken literally when it is not
        a valid one), as "path:line: text", in at most MATCHES results."""
        pattern = pattern.strip()
        if not pattern:
            raise SourceError("要搜的内容是空的")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        prefix = under.strip().strip("/")
        out = []
        for rel in self.files():
            if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
                continue
            text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    out.append(f"{rel}:{number}: {line.strip()[:LINE_CHARS]}")
                    if len(out) >= MATCHES:
                        return out
        return out

    def read(self, rel: str, start: int = 1, end: int | None = None) -> str:
        """Lines start..end of a file (1-based, at most READ_LINES), each with its number."""
        path = self._resolve(rel)
        key = path.relative_to(self.root).as_posix()
        if key not in set(self.files()):
            raise SourceError(f"没有 {rel} 这个文件（或者它不是能读的源码）")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start)
        end = min(len(lines), end if end is not None else start + READ_LINES - 1, start + READ_LINES - 1)
        if start > len(lines):
            raise SourceError(f"{key} 只有 {len(lines)} 行")
        body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        return f"{key} 第 {start}–{end} 行（共 {len(lines)} 行）\n{body}"

    def list(self, rel: str = "") -> str:
        """The folders and files right under `rel` (the root when empty)."""
        prefix = rel.strip().strip("/")
        if prefix:
            self._resolve(prefix)
        entries: dict[str, int] = {}
        for path in self.files():
            if prefix and not path.startswith(prefix + "/"):
                continue
            rest = path[len(prefix) + 1 :] if prefix else path
            head, _, tail = rest.partition("/")
            name = head + "/" if tail else head
            entries[name] = entries.get(name, 0) + 1
        if not entries:
            raise SourceError(f"源码里没有 {rel} 这个文件夹")
        shown = sorted(entries)[:LIST_ENTRIES]
        lines = [f"{name}（{entries[name]} 个文件）" if name.endswith("/") else name for name in shown]
        if len(entries) > LIST_ENTRIES:
            lines.append(f"……还有 {len(entries) - LIST_ENTRIES} 项")
        return "\n".join(lines)

    def revision(self) -> Revision:
        """The checked-out commit, for showing which code was read."""
        try:
            done = subprocess.run(
                ["git", "-C", str(self.root), "log", "-1", "--format=%h %cs"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return Revision("", "")
        commit, _, date = done.stdout.strip().partition(" ")
        return Revision(commit, date) if done.returncode == 0 else Revision("", "")


def _is_text(path: Path) -> bool:
    with path.open("rb") as file:
        head = file.read(4096)
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start > len(head) - 4  # a character cut at the end of the sample
    return True
