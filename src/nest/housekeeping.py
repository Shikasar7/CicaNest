"""Keeping the disk small (the 系统 page's 「存储」): how many days of history nest keeps, shrinking its
database once a cleanup freed room, and, with SnowLuma in Docker, deleting old pictures, videos and files
from the container's QQ data.

Runs once a day, and on 「立即清理」. History is messages, reply decisions, 监控 runs and 黑话 learning
runs; model calls are kept at most CALLS_DAYS. 表情包 beyond their limits and learned 黑话 not seen for
months go too. QQ's message databases are never touched, only its media caches: QQ shows an old picture
as expired and fetches it again if the server still has it. In program mode (Windows) QQ's folder
is shared with the user's own account, so nothing there is touched: the cache root only exists in docker
mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nest.core.hub import Hub
from nest.core.slang import SlangLearner
from nest.core.stickers import StickerKeeper
from nest.storage.db import Database

log = logging.getLogger(__name__)

DAY = 86400.0
MAX_DAYS = 365
CALLS_DAYS = 14
DEFAULT_QQ_CACHE_DAYS = 7
# Under each account's nt_qq_<hash>/nt_data: what QQ downloads or receives, never its databases (nt_db).
QQ_CACHE_DIRS = ("Pic", "Video", "File", "Ptt", "Thumb")
COMPACT_MIN_FREE = 4 * 1024 * 1024  # VACUUM once at least this much of the database file is unused


# Where deploy/mac/docker-compose.yml keeps each QQ client's profile, relative to the QQ data folder: one
# per NapCat container (napcat/<bot>/qq), and SnowLuma's, the image's own QQ (/app/.config) and the extra
# ones (SNOWLUMA_EXTRA_QQ_HOMES, each its own HOME).
QQ_PROFILES = ("napcat/*/qq", "snowluma/qq-config/QQ", "snowluma/qq-extra-*/.config/QQ")


def qq_cache_root(home: Path) -> Path:
    """The QQ containers' data folder, next to nest's own checkout (the compose file's NEST_QQ_DIR
    default); the QQ profiles are under it (QQ_PROFILES)."""
    return home.parent / "qq"


def _cache_dirs(root: Path) -> list[Path]:
    found = []
    accounts = [a for pattern in QQ_PROFILES for a in sorted(root.glob(f"{pattern}/nt_qq_*"))]
    with contextlib.suppress(OSError):
        for account in accounts:
            if account.is_symlink() or not account.is_dir():
                continue
            for name in QQ_CACHE_DIRS:
                folder = account / "nt_data" / name
                if folder.is_dir() and not folder.is_symlink():
                    found.append(folder)
    return found


def _files(folder: Path) -> list[tuple[str, os.stat_result]]:
    """Blocking. Every regular file under `folder` with its stat, never following links."""
    out: list[tuple[str, os.stat_result]] = []
    for parent, _dirs, names in os.walk(folder, followlinks=False):
        for name in names:
            with contextlib.suppress(OSError):
                path = os.path.join(parent, name)
                info = os.lstat(path)
                if stat.S_ISREG(info.st_mode):
                    out.append((path, info))
    return out


def qq_cache_size(root: Path) -> int:
    """Blocking: bytes in QQ's media caches under `root`."""
    return sum(info.st_size for folder in _cache_dirs(root) for _path, info in _files(folder))


def clean_qq_cache(root: Path, before: float) -> tuple[int, int]:
    """Blocking: delete cached files last changed before `before`. Returns (files, bytes) removed."""
    files = size = 0
    for folder in _cache_dirs(root):
        for path, info in _files(folder):
            if info.st_mtime >= before:
                continue
            try:
                os.remove(path)
            except OSError as exc:
                log.debug("删不掉 %s：%s", path, exc)
                continue
            files += 1
            size += info.st_size
    return files, size


def folder_size(path: Path) -> int:
    """Blocking: bytes of the regular files under `path` (0 when it does not exist)."""
    return sum(info.st_size for _path, info in _files(path)) if path.is_dir() else 0


@dataclass(frozen=True)
class Plan:
    history_days: float
    qq_cache_days: int  # 0: keep QQ's cache
    qq_cache_dir: Path | None  # None: not ours to clean (program mode)


@dataclass(frozen=True)
class Report:
    at: float
    messages: int = 0
    records: int = 0  # reply decisions, model calls, 监控 runs, 黑话 terms and learning runs
    files: int = 0  # QQ cache files
    stickers: int = 0  # 表情包 removed
    freed: int = 0  # bytes: QQ cache files plus what the database shrank by

    def view(self) -> dict[str, Any]:
        return asdict(self)


class Housekeeper:
    def __init__(
        self,
        db: Database,
        hub: Hub,
        plan: Callable[[], Awaitable[Plan]],
        *,
        interval: float = DAY,
        first_delay: float = 60.0,
        stickers: StickerKeeper | None = None,
        slang: SlangLearner | None = None,
    ) -> None:
        self.db = db
        self.hub = hub
        self.stickers = stickers
        self.slang = slang
        self._plan = plan
        self._interval = interval
        self._first_delay = first_delay
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.last: Report | None = None

    async def run(self, now: float | None = None) -> Report:
        """One cleanup now. Never two at once."""
        async with self._lock:
            now = time.time() if now is None else now
            plan = await self._plan()
            calls_days = min(plan.history_days, CALLS_DAYS)
            purged = await self.hub.purge(now, days=plan.history_days, calls_days=calls_days)
            records = purged["records"]
            stickers = sticker_bytes = 0
            if self.stickers is not None:
                stickers, sticker_bytes = await self.stickers.tidy(now)
            if self.slang is not None:
                records += await self.slang.tidy(now, plan.history_days)
            freed = await self.db.compact(COMPACT_MIN_FREE) + sticker_bytes
            files = 0
            if plan.qq_cache_dir is not None and plan.qq_cache_days > 0:
                cutoff = now - plan.qq_cache_days * DAY
                files, size = await asyncio.to_thread(clean_qq_cache, plan.qq_cache_dir, cutoff)
                freed += size
                if files:
                    days, mb = plan.qq_cache_days, size / 2**20
                    log.info("清理了 %d 个超过 %d 天的 QQ 图片、视频和文件（%.1f MB）", files, days, mb)
            report = Report(now, purged["messages"], records, files, freed=freed, stickers=stickers)
            self.last = report
            return report

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="housekeeping")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        await asyncio.sleep(self._first_delay)
        while True:
            try:
                await self.run()
            except Exception:
                log.exception("定时清理失败")
            await asyncio.sleep(self._interval)
