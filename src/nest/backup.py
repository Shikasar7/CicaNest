"""一键备份: everything of nest's own that cannot be got back, in one zip.

The database comes from SQLite's online backup, so it is whole while nest keeps writing. Keys (.env), QQ
logins, logs and the code stay out: a backup is a file people copy around.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

FOLDER = "backups"
KEEP = 3
FOLDERS = ("stickers",)  # beside the database, in the data folder
NAME = re.compile(r"nest-backup-\d{8}-\d{6}\.zip")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_database(source: Path, target: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as live,
        closing(sqlite3.connect(target)) as copy,
    ):
        live.backup(copy)


def _files(data_dir: Path) -> Iterator[Path]:
    for folder in FOLDERS:
        for path in sorted((data_dir / folder).rglob("*")):
            if path.is_file():
                yield path


def make(data_dir: Path, database: Path, *, version: str, schema: int) -> Path:
    """Blocking. Writes a zip into data/backups, leaves the latest KEEP there, returns its path."""
    folder = data_dir / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / time.strftime("nest-backup-%Y%m%d-%H%M%S.zip")
    partial = target.with_suffix(".part")
    checksums: dict[str, str] = {}
    try:
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            copy = Path(scratch) / "nest.db"
            _copy_database(database, copy)
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(copy, "nest.db")
                checksums["nest.db"] = _sha256(copy)
                for path in _files(data_dir):
                    name = path.relative_to(data_dir).as_posix()
                    archive.write(path, name, compress_type=zipfile.ZIP_STORED)  # pictures do not shrink
                    checksums[name] = _sha256(path)
                manifest: dict[str, Any] = {
                    "app": "nest",
                    "version": version,
                    "schema": schema,
                    "created_at": time.time(),
                    "sha256": checksums,
                }
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    for old in listed(data_dir)[KEEP:]:
        (folder / old["name"]).unlink(missing_ok=True)
    return target


def listed(data_dir: Path) -> list[dict[str, Any]]:
    """Blocking. The backups there, newest first."""
    folder = data_dir / FOLDER
    if not folder.is_dir():
        return []
    found = [path for path in folder.iterdir() if NAME.fullmatch(path.name)]
    found.sort(key=lambda path: path.name, reverse=True)
    return [{"name": path.name, "size": path.stat().st_size, "at": path.stat().st_mtime} for path in found]


def path_of(data_dir: Path, name: str) -> Path | None:
    """The file of a backup by its name; None for anything that is not one."""
    path = data_dir / FOLDER / name
    return path if NAME.fullmatch(name) and path.is_file() else None
