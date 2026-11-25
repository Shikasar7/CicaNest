"""工作区 (伙伴模式): a folder of her own on nest's machine where she reads and writes files, and puts
what she makes for the user (给你的/); what the user sends her lands in 收到的/.

Every path she or the console gives is relative to her folder. Nothing outside it is ever read or written,
even though her container can change the folder at any moment (it is her /work): a file is reached by
going down from her folder one name at a time, never following a symbolic link, and on macOS and Linux each
folder on the way is held open (a file descriptor) while the next is opened in it — so swapping a folder
for a link to some place outside between a check and a use leads nowhere.
Where the system cannot open relative to a folder (Windows, where nest is only developed), every
step is checked not to be a link instead. Names of the kind that hold keys (.env, *.pem, id_rsa…) are
refused outright. A folder may hold at most LIMIT bytes; a write that would go over it is refused.

Every method blocks (file system): call from a worker thread (asyncio.to_thread).
"""

from __future__ import annotations

import contextlib
import errno
import fnmatch
import os
import re
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

LIMIT = 2 * 1024**3
FOR_YOU = "给你的"
RECEIVED = "收到的"
READ_CHARS = 20_000
LIST_MAX = 200
FILE_MAX = 50 * 1024**2  # one upload or one written file
SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "credentials*", "auth.json")
BAD_CHARS = re.compile(r"[\x00-\x1f\x7f]")
OUTSIDE = "这个路径通到工作区外面去了"
# Open relative to a folder held open, never following a link (macOS, Linux).
BY_FD = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat, os.mkdir, os.unlink} <= os.supports_dir_fd
    and os.scandir in os.supports_fd
)
BINARY = getattr(os, "O_BINARY", 0)
# A pipe made in her container must not hang a read or a write: it fails instead (then: not a file).
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
NOT_FOLDER = "路径上有一段是文件，不是文件夹"


class WorkspaceError(Exception):
    """Why it was not done, in one Chinese sentence (told to her, or shown in the console)."""


@dataclass(frozen=True)
class Entry:
    name: str
    folder: bool
    size: int
    modified: float


def folder_name(bot_id: str) -> str:
    """qq:10001 -> qq-10001: a folder name on any system."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", bot_id)


def secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_NAMES)


def received_name(name: str) -> str:
    """The name a file from the user gets; WorkspaceError when it may not come in under it."""
    clean = PurePosixPath(name.replace("\\", "/")).name.strip()
    if not clean or clean in (".", "..") or BAD_CHARS.search(clean):
        raise WorkspaceError("文件名不能用")
    if secret_name(clean):
        raise WorkspaceError("这类文件名（.env、密钥、凭据）不能碰")
    return clean


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


class _Dir:
    """A folder inside her workspace, reached without following a link: held open by `fd` where the
    system allows it (then `path` is only for the record), else by its checked path."""

    def __init__(self, path: Path, fd: int | None) -> None:
        self.path = path
        self.fd = fd

    @classmethod
    def root(cls, base: Path) -> _Dir:
        return cls(base, os.open(base, os.O_RDONLY | os.O_DIRECTORY) if BY_FD else None)

    def close(self) -> None:
        if self.fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None

    def stat(self, name: str) -> os.stat_result | None:
        """The entry itself (a link is a link, not where it points); None when there is none."""
        try:
            if self.fd is not None:
                return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return (self.path / name).lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None

    def _refuse(self, name: str, error: OSError, missing: str) -> WorkspaceError:
        info = self.stat(name)
        if info is not None and stat.S_ISLNK(info.st_mode):
            return WorkspaceError(OUTSIDE)
        if isinstance(error, FileNotFoundError) or info is None:
            return WorkspaceError(missing)
        if error.errno == errno.ELOOP:
            return WorkspaceError(OUTSIDE)
        return WorkspaceError(missing)

    def sub(self, name: str, *, create: bool, missing: str) -> _Dir:
        """The folder `name` in this one (made when `create`); WorkspaceError when it is not a folder of
        hers (missing, a file, or a link)."""
        if self.fd is not None:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(name, dir_fd=self.fd)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            except OSError as exc:
                raise self._refuse(name, exc, missing) from None
            return _Dir(self.path / name, fd)
        path = self.path / name
        if create and not path.exists() and not _is_link(path):
            path.mkdir()
        if _is_link(path):
            raise WorkspaceError(OUTSIDE)
        if not path.is_dir():
            raise WorkspaceError(missing)
        return _Dir(path, None)

    def open(self, name: str, flags: int, *, missing: str) -> int:
        """A file descriptor for the file `name` here, never through a link."""
        if self.fd is not None:
            try:
                return os.open(name, flags | os.O_NOFOLLOW | NONBLOCK, 0o644, dir_fd=self.fd)
            except FileExistsError:
                raise
            except OSError as exc:
                raise self._refuse(name, exc, missing) from None
        path = self.path / name
        if _is_link(path):
            raise WorkspaceError(OUTSIDE)
        try:
            return os.open(path, flags | BINARY, 0o644)
        except FileExistsError:
            raise
        except OSError:  # a folder (Windows refuses to open one), or not there
            raise WorkspaceError(missing) from None

    def entries(self) -> list[tuple[str, os.stat_result]]:
        found = []
        with os.scandir(self.fd if self.fd is not None else self.path) as listing:
            for item in listing:
                with contextlib.suppress(OSError):
                    found.append((item.name, item.stat(follow_symlinks=False)))
        return found

    def remove(self, name: str, info: os.stat_result) -> None:
        """Delete `name` here; a link goes, never what it points to."""
        if stat.S_ISDIR(info.st_mode) and not (self.fd is None and _is_link(self.path / name)):
            if self.fd is not None:
                shutil.rmtree(name, dir_fd=self.fd)  # never follows a link inside (rmtree's own fd walk)
            else:
                shutil.rmtree(self.path / name)
        elif self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        elif (self.path / name).is_junction():
            os.rmdir(self.path / name)  # the junction, not what it points to
        else:
            (self.path / name).unlink()


def _regular(fd: int, missing: str) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise WorkspaceError(missing)
    return info


def _numbered(name: str, n: int) -> str:
    pure = PurePosixPath(name)
    return f"{pure.stem}-{n}{pure.suffix}"


class Workspace:
    def __init__(self, root: Path, *, limit: int = LIMIT) -> None:
        self.root = root
        self.limit = limit

    def folder(self, bot_id: str) -> Path:
        """Her folder, made with its two standing folders when it is not there yet."""
        place = self.root / folder_name(bot_id)
        place.mkdir(parents=True, exist_ok=True)
        for sub in (FOR_YOU, RECEIVED):
            path = place / sub
            if not path.exists() and not _is_link(path):
                path.mkdir()
        return place

    def move(self, old_bot: str, new_bot: str) -> None:
        """Her folder under another id (nest.core.move_in). WorkspaceError when the new one is not empty."""
        source, target = self.root / folder_name(old_bot), self.root / folder_name(new_bot)
        if not source.is_dir() or _is_link(source):
            return
        if target.exists():
            if any(files for _, _, files in os.walk(target)):
                raise WorkspaceError("那边的文件夹里已经有东西了")
            shutil.rmtree(target)
        source.rename(target)

    @staticmethod
    def _parts(relative: str) -> tuple[str, ...]:
        """`relative` as names inside her folder; WorkspaceError when it leads out or names a secret."""
        text = (relative or "").strip().replace("\\", "/")
        if BAD_CHARS.search(text):
            raise WorkspaceError("路径里有不能用的字符")
        pure = PurePosixPath(text)
        if pure.is_absolute() or (text[1:2] == ":" and text[:1].isalpha()):
            raise WorkspaceError("路径要写成工作区里的相对路径，比如 给你的/清单.md")
        if any(part == ".." for part in pure.parts):
            raise WorkspaceError("路径不能用 .. 走出工作区")
        if any(secret_name(part) for part in pure.parts):
            raise WorkspaceError("这类文件名（.env、密钥、凭据）不能碰")
        return pure.parts

    def path(self, bot_id: str, relative: str) -> Path:
        """Where `relative` is, for showing: checked like every use, but never open what it returns (the
        container can change the folder after the check; the methods below go down by name)."""
        parts = self._parts(relative)
        base = self.folder(bot_id).resolve()
        found = (base / Path(*parts)).resolve() if parts else base
        if found != base and not found.is_relative_to(base):
            raise WorkspaceError(OUTSIDE)
        return found

    @contextlib.contextmanager
    def _at(
        self, bot_id: str, parts: tuple[str, ...], *, missing: str, create: bool = False
    ) -> Iterator[_Dir]:
        """The folder `parts` inside hers, reached one name at a time without following a link."""
        opened = [_Dir.root(self.folder(bot_id).resolve())]
        try:
            for name in parts:
                opened.append(opened[-1].sub(name, create=create, missing=missing))
            yield opened[-1]
        finally:
            for folder in reversed(opened):
                folder.close()

    # -- looking --------------------------------------------------------------------------------------------

    def entries(self, bot_id: str, relative: str = "") -> list[Entry]:
        parts = self._parts(relative)
        with self._at(bot_id, parts, missing=f"没有这个文件夹：{relative or '/'}") as here:
            items = here.entries()
        found = [
            Entry(
                name,
                stat.S_ISDIR(info.st_mode),
                0 if stat.S_ISDIR(info.st_mode) else info.st_size,
                info.st_mtime,
            )
            for name, info in items
            if not secret_name(name)
        ]
        top = not parts
        found.sort(
            key=lambda e: (
                not e.folder,
                {FOR_YOU: 0, RECEIVED: 1}.get(e.name, 2) if top else 2,
                e.name.lower(),
            )
        )
        return found[:LIST_MAX]

    def used(self, bot_id: str) -> int:
        total = 0
        for top, _, files in os.walk(self.folder(bot_id)):  # does not follow links
            for name in files:
                with contextlib.suppress(OSError):
                    total += (Path(top) / name).lstat().st_size
        return total

    def open_file(self, bot_id: str, relative: str) -> tuple[BinaryIO, str, int]:
        """One of her files open for reading (the caller closes it), its name and size."""
        parts = self._parts(relative)
        missing = f"没有这个文件：{relative}"
        if not parts:
            raise WorkspaceError(missing)
        with self._at(bot_id, parts[:-1], missing=missing) as here:
            fd = here.open(parts[-1], os.O_RDONLY, missing=missing)
        info = _regular(fd, missing)
        return os.fdopen(fd, "rb"), parts[-1], info.st_size

    def read(self, bot_id: str, relative: str, *, chars: int = READ_CHARS) -> str:
        handle, _, size = self.open_file(bot_id, relative)
        with handle:
            data = handle.read(chars * 4)
        if b"\x00" in data[:8192]:
            raise WorkspaceError("这不是文字文件，读不了（可以在命令里用程序处理它）")
        text = data.decode("utf-8", errors="replace")
        if len(text) > chars or len(data) == chars * 4:
            return text[:chars] + f"\n……（后面略，文件共 {size} 字节）"
        return text

    # -- changing --------------------------------------------------------------------------------------------

    def _room(self, bot_id: str, adding: int, freed: int = 0) -> None:
        if adding > FILE_MAX:
            raise WorkspaceError(f"单个文件不能超过 {FILE_MAX // 1024**2} MB")
        if self.used(bot_id) - freed + adding > self.limit:
            raise WorkspaceError(f"工作区满了（上限 {self.limit / 1024**3:.0f} GB），先删掉些不用的东西")

    def write(self, bot_id: str, relative: str, data: bytes) -> str:
        parts = self._parts(relative)
        if not parts:
            raise WorkspaceError("要写的是文件，不是文件夹")
        with self._at(bot_id, parts[:-1], missing=NOT_FOLDER, create=True) as here:
            info = here.stat(parts[-1])
            if info is not None and stat.S_ISDIR(info.st_mode):
                raise WorkspaceError("要写的是文件，不是文件夹")
            if info is not None and not stat.S_ISREG(info.st_mode):
                raise WorkspaceError(OUTSIDE if stat.S_ISLNK(info.st_mode) else "这不是普通文件，写不了")
            self._room(
                bot_id, len(data), info.st_size if info is not None and stat.S_ISREG(info.st_mode) else 0
            )
            fd = here.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, missing=OUTSIDE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return "/".join(parts)

    def edit(self, bot_id: str, relative: str, old: str, new: str) -> str:
        handle, _, _ = self.open_file(bot_id, relative)
        with handle:
            data = handle.read(FILE_MAX + 1)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("这不是文字文件，改不了") from None
        count = text.count(old) if old else 0
        if count != 1:
            how = "没找到" if count == 0 else f"出现了 {count} 次，要能唯一定位"
            raise WorkspaceError(f"要替换的原文{how}")
        changed = text.replace(old, new).encode("utf-8")
        return self.write(bot_id, relative, changed)

    def _new_file(self, bot_id: str, shelf: str, name: str) -> tuple[int, str]:
        """A new file in `shelf` (给你的 / 收到的) named `name`, or name-2, name-3… when taken."""
        with self._at(bot_id, (shelf,), missing=OUTSIDE, create=True) as here:
            candidate, n = name, 2
            while True:
                try:
                    fd = here.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, missing=OUTSIDE)
                except FileExistsError:
                    candidate, n = _numbered(name, n), n + 1
                    continue
                return fd, f"{shelf}/{candidate}"

    def deliver(self, bot_id: str, relative: str) -> str:
        """Put a file into 给你的/ (a copy; one already there stays where it is). Its path there."""
        parts = self._parts(relative)
        source, name, size = self.open_file(bot_id, relative)
        with source:
            if parts[:-1] == (FOR_YOU,):
                return f"{FOR_YOU}/{name}"
            self._room(bot_id, size)
            fd, shelved = self._new_file(bot_id, FOR_YOU, name)
            with os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target)
        return shelved

    def receive(self, bot_id: str, name: str, data: bytes) -> str:
        """A file the user sends her, into 收到的/ (renamed when the name is taken). Its path."""
        clean = received_name(name)
        self._room(bot_id, len(data))
        fd, saved = self._new_file(bot_id, RECEIVED, clean)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return saved

    def delete(self, bot_id: str, relative: str) -> None:
        parts = self._parts(relative)
        if not parts or parts in ((FOR_YOU,), (RECEIVED,)):
            raise WorkspaceError("这个文件夹不能删")
        missing = f"没有这个文件：{relative}"
        with self._at(bot_id, parts[:-1], missing=missing) as here:
            info = here.stat(parts[-1])
            if info is None:
                raise WorkspaceError(missing)
            here.remove(parts[-1], info)
