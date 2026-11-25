"""伙伴包: a companion as a plugin package — her persona, what she remembers of the user,
her 回忆档案 — to keep, move to another nest, or share.

A pack is a plugin zip (nest.core.plugins) whose provides.companion names a folder holding companion.json:

    {"format": 1, "name": "…", "aliases": [], "user_name": "…", "persona": "…", "examples": "…",
     "points": [{"kind": "like" | "fact" | "promise", "content": "…", "due": ""}],
     "records": [{"title": "…", "file": "records/001.md", "source": "…"}]}

and the records' Markdown files beside it. The same pack may also carry skills and pets: imported, those
are installed as a plugin for the new companion only. Importing a pack makes a new companion that lives
on the desk (the same way 「新伙伴 / 搬一个进来」 keeps one), never changes one that exists.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from nest.core.companion_import import EXAMPLES_MAX, PERSONA_MAX, POINTS_MAX
from nest.core.keepsakes import BODY_MAX, RECORDS_MAX, TITLE_MAX
from nest.core.memory import CONTENT_MAX

FORMAT = 1
KINDS = ("like", "fact", "promise")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class PackProblem(ValueError):
    """What is wrong with a pack, in one Chinese sentence."""


@dataclass
class Pack:
    name: str
    aliases: list[str] = field(default_factory=list)
    user_name: str = ""
    persona: str = ""
    examples: str = ""
    points: list[dict[str, str]] = field(default_factory=list)
    records: list[dict[str, str]] = field(default_factory=list)  # title, body, source

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": self.aliases,
            "user_name": self.user_name,
            "persona": self.persona,
            "examples": self.examples,
            "points": self.points,
            "records": [{"title": r["title"], "chars": len(r["body"])} for r in self.records],
        }


def _text(value: Any, limit: int) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def read_pack(folder: Path) -> Pack:
    """The companion in a pack's companion folder; PackProblem when it cannot be used."""
    try:
        data = json.loads((folder / "companion.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackProblem("伙伴文件夹里没有 companion.json") from None
    except (OSError, ValueError) as exc:
        raise PackProblem(f"companion.json 读不了：{exc}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise PackProblem('companion.json 的格式不认识（要有 "format": 1）')
    name = _text(data.get("name"), 20)
    if not name:
        raise PackProblem("伙伴要有名字")
    points = []
    for item in data.get("points") or []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        content = _text(item.get("content"), CONTENT_MAX)
        due = _text(item.get("due"), 10)
        if content:
            points.append(
                {"kind": str(item["kind"]), "content": content, "due": due if DAY.fullmatch(due) else ""}
            )
    records = []
    base = folder.resolve()
    for item in (data.get("records") or [])[:RECORDS_MAX]:
        if not isinstance(item, dict):
            continue
        pure = PurePosixPath(str(item.get("file") or ""))
        path = (base / Path(*pure.parts)).resolve() if pure.parts else base
        if pure.is_absolute() or ".." in pure.parts or not path.is_relative_to(base) or not path.is_file():
            raise PackProblem(f"回忆文件找不到或路径不对：{item.get('file')}")
        body = _text(path.read_text(encoding="utf-8", errors="replace"), BODY_MAX)
        title = _text(item.get("title"), TITLE_MAX) or path.stem
        if body:
            records.append({"title": title, "body": body, "source": _text(item.get("source"), 200)})
    aliases = [a for a in (_text(x, 20) for x in data.get("aliases") or []) if a and a != name][:10]
    return Pack(
        name=name,
        aliases=aliases,
        user_name=_text(data.get("user_name"), 20),
        persona=_text(data.get("persona"), PERSONA_MAX),
        examples=_text(data.get("examples"), EXAMPLES_MAX),
        points=points[:POINTS_MAX],
        records=records,
    )


def pack_id(name: str, bot_id: str) -> str:
    """companion.<letters of her id>: a plugin id that stays the same when she is exported again."""
    tail = re.sub(r"[^a-z0-9]", "", bot_id.lower())[-12:] or "x"
    return f"companion.c{tail}"


def build_pack(pack: Pack, *, plugin_id: str, version: str | None = None) -> bytes:
    """The zip of a companion pack."""
    stamp = time.strftime("%Y.%m%d.%H%M%S") if version is None else version
    major, minor, patch = [*stamp.split("."), "0", "0"][:3]
    manifest = {
        "id": plugin_id,
        "name": pack.name,
        "version": f"{int(major)}.{int(minor)}.{int(patch)}",
        "description": f"伙伴「{pack.name}」：人设、要点 {len(pack.points)} 条、回忆 {len(pack.records)} 份",
        "provides": {"companion": "companion"},
    }
    records = []
    files: dict[str, str] = {}
    for number, record in enumerate(pack.records, 1):
        name = f"records/{number:03d}.md"
        files[name] = record["body"]
        records.append({"title": record["title"], "file": name, "source": record.get("source", "")})
    body = {
        "format": FORMAT,
        "name": pack.name,
        "aliases": pack.aliases,
        "user_name": pack.user_name,
        "persona": pack.persona,
        "examples": pack.examples,
        "points": pack.points,
        "records": records,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("companion/companion.json", json.dumps(body, ensure_ascii=False, indent=2))
        for name, text in files.items():
            archive.writestr(f"companion/{name}", text)
    return buffer.getvalue()
