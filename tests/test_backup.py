"""一键备份: the database whole while it is in use, the stickers, and nothing secret."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from nest import backup


def data_folder(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "stickers" / "ab").mkdir(parents=True)
    (data / "logs").mkdir()
    (data / "stickers" / "ab" / "cat.png").write_bytes(b"png")
    (data / "logs" / "nest.log").write_text("secret log")
    (tmp_path / ".env").write_text("KEY=secret")
    database = data / "nest.db"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
    return data, database


def test_a_backup_has_the_database_and_stickers_and_their_checksums(tmp_path: Path) -> None:
    data, database = data_folder(tmp_path)
    path = backup.make(data, database, version="1", schema=21)
    with zipfile.ZipFile(path) as archive:
        assert sorted(archive.namelist()) == ["manifest.json", "nest.db", "stickers/ab/cat.png"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == 21
        for name, digest in manifest["sha256"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        (tmp_path / "copy.db").write_bytes(archive.read("nest.db"))
    with sqlite3.connect(tmp_path / "copy.db") as conn:
        assert conn.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_only_the_latest_backups_stay(tmp_path: Path) -> None:
    data, database = data_folder(tmp_path)
    folder = data / backup.FOLDER
    folder.mkdir()
    for day in range(1, 6):
        (folder / f"nest-backup-2026110{day}-000000.zip").write_bytes(b"old")
    made = backup.make(data, database, version="1", schema=21)
    names = [item["name"] for item in backup.listed(data)]
    assert len(names) == backup.KEEP and names[0] == made.name
    assert not list(folder.glob("*.part"))


def test_a_name_that_is_not_a_backup_is_not_served(tmp_path: Path) -> None:
    data, database = data_folder(tmp_path)
    made = backup.make(data, database, version="1", schema=21)
    assert backup.path_of(data, made.name) == made
    assert backup.path_of(data, "../.env") is None
    assert backup.path_of(data, "nest.db") is None
