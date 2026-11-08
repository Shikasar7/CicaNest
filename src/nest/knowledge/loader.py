"""Reading a help folder in the docs/help format: every
section is `## 标题 {#id}`, then one line per field (也叫, 在哪, 平台, 版本, 原文, 英文, 出现在, 原因, 解决),
then the body. Level-1 headings in features.md name the area its sections belong to. The 功能清单 at the
top of features.md is an index of the other sections, not a section of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FILES = ("overview.md", "features.md", "errors.md", "faq.md", "versions.md")
FIELDS = ("也叫", "在哪", "平台", "版本", "原文", "英文", "出现在", "原因", "解决")
HEADING = re.compile(r"^## (.+?) \{#([a-z0-9]+(?:-[a-z0-9]+)*)\}\s*$")
FIELD_LINE = re.compile(r"^- ([^：:]{1,6})[：:]\s*(.*)$")
INDEX_SECTION = "feature-list"
KIND = {
    "overview.md": "简介",
    "features.md": "功能",
    "errors.md": "报错",
    "faq.md": "常见问题",
    "versions.md": "版本",
    "notes.md": "补充",  # answers added in the console for questions the docs missed (not a file)
}


@dataclass(frozen=True)
class Section:
    id: str
    file: str
    area: str
    title: str
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    body: str = ""

    def values(self, name: str) -> tuple[str, ...]:
        return self.fields.get(name, ())

    def header(self) -> str:
        """One line saying what the section is about, put in front of it when it is indexed or shown on
        its own: which file and area, the title, what players also call it, where it is in the app."""
        parts = [KIND.get(self.file, self.file)]
        if self.area:
            parts.append(self.area)
        title = self.title
        if aliases := self.values("也叫"):
            title += f"（也叫：{'、'.join(aliases)}）"
        parts.append(title)
        if where := self.values("在哪"):
            parts.append(f"在哪：{'；'.join(where)}")
        return " · ".join(parts)

    def text(self) -> str:
        """The whole section as the model reads it: the title, the fields, the body."""
        lines = [f"## {self.title}"]
        lines += [f"- {name}：{value}" for name in FIELDS for value in self.values(name)]
        if self.body:
            lines += ["", self.body]
        return "\n".join(lines)


def parse(text: str, file: str) -> list[Section]:
    """The sections of one help file, in order."""
    sections: list[Section] = []
    area = ""
    current: dict | None = None
    in_fields = False

    def close() -> None:
        if current is not None and current["id"] != INDEX_SECTION:
            fields = {name: tuple(values) for name, values in current["fields"].items()}
            body = "\n".join(current["body"]).strip()
            sections.append(Section(current["id"], file, current["area"], current["title"], fields, body))

    for line in text.replace("\r\n", "\n").split("\n"):
        if heading := HEADING.match(line):
            close()
            current = {"id": heading[2], "area": area, "title": heading[1].strip(), "fields": {}, "body": []}
            in_fields = True
            continue
        if line.startswith(("# ", "## ")):
            close()
            current = None
            if line.startswith("# "):
                area = line[2:].strip()
            continue
        if current is None:
            continue
        # The field list comes right after the heading; the first other line (or a blank line after a
        # field) starts the body.
        if in_fields:
            if (found := FIELD_LINE.match(line)) and found[1] in FIELDS:
                current["fields"].setdefault(found[1], []).append(found[2].strip())
                continue
            if not line.strip() and not current["fields"]:
                continue
            in_fields = False
        current["body"].append(line)
    close()
    # A file's own title is a level-1 heading too: it is not an area.
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    return [s if s.area != title else Section(s.id, s.file, "", s.title, s.fields, s.body) for s in sections]


def load(folder: Path) -> list[Section]:
    """Every section of the help files in `folder` (missing files are skipped). Ids are unique."""
    sections: list[Section] = []
    for name in FILES:
        path = folder / name
        if path.is_file():
            sections += parse(path.read_text(encoding="utf-8"), name)
    seen: set[str] = set()
    for section in sections:
        if section.id in seen:
            raise ValueError(f"资料里的 id「{section.id}」重复了")
        seen.add(section.id)
    return sections
