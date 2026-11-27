"""插件: what nest can be extended with, installed, switched on and off, per companion.

A plugin is a folder with plugin.json at its top (shared as a zip): who made it, what it provides (skills,
pets, an MCP server, a companion, a Python module) and what it asks for (network, secrets, tools that need
the user's nod). Installing is two steps: a zip is unpacked into a staging folder and read — the console
shows what it provides and asks for — and only then moved into data/plugins/<id>/.

nest's own tools are plugins too (BUILTIN: nest.web, nest.files…), so the same switches cover them: a
plugin can be off altogether, or on for some companions only. A tool is offered to a companion only when
its plugin is on for her (tool_allowed).

What an installed plugin's contributions do is elsewhere: this module only knows
them, checks them, keeps them and their switches.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import re
import shutil
import stat
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from nest import __version__
from nest.core import themes
from nest.storage.db import Database
from nest.storage.settings import SettingsRepo

log = logging.getLogger(__name__)

STAGING_KEPT = 24 * 3600.0  # a package read but not installed is removed at the next start after this
STATE = "plugins"  # setting: plugin id -> {"enabled": bool, "bots": null (every companion) | [bot ids]}
ID = re.compile(r"[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+")
VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,6}")
SECRET = re.compile(r"[A-Z][A-Z0-9_]{1,40}")
# Names a secret may not take: a value under them would change how nest's docker program, or the
# system in the container, behaves.
RESERVED_SECRET = re.compile(
    r"PATH|HOME|USER|SHELL|TMPDIR|PLUGIN_DIR|PWD|LANG|TERM|DOCKER_\w*|LD_\w*|DYLD_\w*|\w*_PROXY|PYTHON\w*"
    r"|SSL_\w*|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE|NODE_\w*|NPM_\w*|PIP_\w*|UV_\w*|LC_\w*|XDG_\w*"
)


def reserved_secret(name: str) -> bool:
    return RESERVED_SECRET.fullmatch(name) is not None


DOMAIN = re.compile(r"(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+|\*")
TOOL = re.compile(r"[a-zA-Z0-9_-]{1,64}")
KINDS = ("skills", "pets", "themes", "stickers", "mcp", "companion", "python")
ZIP_MAX = 50 * 1024**2
UNPACKED_MAX = 200 * 1024**2
FILES_MAX = 5000
TEXT_MAX = 500


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    author: str = ""
    description: str = ""
    provides: dict[str, Any] = field(default_factory=dict)
    network: tuple[str, ...] = ()
    secrets: tuple[tuple[str, str], ...] = ()  # (variable name, what to call it for the user)
    confirm: tuple[str, ...] = ()
    nest: str = ""  # the nest it needs at least ("": any)

    @property
    def trust(self) -> str:
        """content: pictures and such only; guides: skills or a persona, which tell her how to do things
        (with her commands and the network too); code: runs in a
        container of its own (MCP); trusted: runs inside nest."""
        if self.provides.get("python"):
            return "trusted"
        if self.provides.get("mcp"):
            return "code"
        if self.provides.get("skills") or self.provides.get("companion"):
            return "guides"
        return "content"

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "kinds": [k for k in KINDS if self.provides.get(k)],
            "trust": self.trust,
            "nest": self.nest,
            "permissions": {
                "network": list(self.network),
                "secrets": [{"name": n, "label": label} for n, label in self.secrets],
                "confirm": list(self.confirm),
            },
        }


@dataclass(frozen=True)
class Builtin:
    """One of nest's own groups of tools, switched like a plugin."""

    id: str
    name: str
    description: str
    tools: tuple[str, ...]
    network: tuple[str, ...] = ()
    on_for_all: bool = True  # False: on for no companion until the user picks

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": "",
            "author": "nest",
            "description": self.description,
            "kinds": ["tools"],
            "trust": "builtin",
            "tools": list(self.tools),
            "permissions": {"network": list(self.network), "secrets": [], "confirm": []},
        }


BUILTIN = (
    Builtin(
        "nest.web",
        "读网页和联网搜索",
        "读一个网页的文字（不读本机和局域网的地址）；配了 Tavily 的 key 时还能联网搜索",
        ("read_page", "web_search"),
        ("*",),
    ),
    Builtin(
        "nest.reminders",
        "提醒",
        "在聊天里设、看、改、取消提醒",
        ("set_reminder", "list_reminders", "change_reminder", "cancel_reminder"),
    ),
    Builtin(
        "nest.memory",
        "回忆和聊天记录",
        "翻她的回忆档案、查你们的聊天记录；记下你的一件事，或把一段写进回忆档案（先当草稿，你看过才算）",
        ("recall_memories", "search_chat", "remember", "write_memory"),
    ),
    Builtin(
        "nest.files",
        "工作区文件",
        "在她自己的文件夹和你共享给她的文件夹里看、读、写、改文件，把做好的交给你",
        ("list_files", "read_file", "write_file", "edit_file", "deliver_file"),
    ),
    Builtin(
        "nest.download",
        "下载文件",
        "从网上下载一个文件到她的文件夹「收到的」：每次都先让你看来源和大小，你点头才下",
        ("download_file",),
        ("*",),
    ),
    Builtin(
        "nest.draw",
        "画图",
        "给你画一张图，画好发到聊天里（用能画图的模型，每人每天最多 10 张）",
        ("draw_picture",),
    ),
    Builtin(
        "nest.shell",
        "跑命令",
        "在她自己的容器里跑命令（Linux、Python；联网与否看「文件」里的开关）",
        ("run_command",),
        ("*",),
    ),
    Builtin(
        "nest.tasks",
        "后台任务",
        "把要花一阵子的事交给后台一步步做；也能设定时任务，每天或每周到点自己做",
        ("start_task", "set_routine", "list_routines", "cancel_routine"),
    ),
    Builtin("nest.skills", "技能", "装上的插件里的技能：她知道自己会什么，用的时候读出做法", ("read_skill",)),
    Builtin(
        "nest.windows",
        "电脑上做事",
        "在你的 Windows 电脑上看、写、改、删（进回收站）、移动文件，跑 PowerShell 命令，打开文件或网址"
        "（要桌面端开着；除了在授权文件夹里看、在设成「改不用问」的文件夹里改，每一步都要你在电脑上点头）",
        (
            "win_list_files",
            "win_read_file",
            "win_write_file",
            "win_edit_file",
            "win_delete",
            "win_move",
            "win_run_command",
            "win_open",
        ),
        on_for_all=False,
    ),
)
TOOL_PLUGIN = {tool: b.id for b in BUILTIN for tool in b.tools}


class PluginError(Exception):
    """Why it was not done, in one Chinese sentence."""


class UntrustedPlugin(PluginError):
    """A plugin whose code runs inside nest, installed without the user saying they trust it."""


UNTRUSTED = "这个插件的代码直接在 nest 里跑，要你明确表示信任它才装"
NO_TRUST_RECORD = "没有你信任它的记录，没载入：要用的话在「插件」页重新装一次"


# -- reading a plugin ---------------------------------------------------------------------------------------


def _text(data: dict[str, Any], key: str, problems: list[str], *, required: bool = False) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        problems.append(f"「{key}」要写成{'不为空的' if required else ''}文字")
        return ""
    return value.strip()[:TEXT_MAX]


def _inside(folder: Path, relative: Any, problems: list[str], what: str) -> None:
    pure = PurePosixPath(str(relative))
    if not isinstance(relative, str) or pure.is_absolute() or ".." in pure.parts:
        problems.append(f"{what}的路径要写成包里的相对路径：{relative}")
    elif not (folder / Path(*pure.parts)).exists():
        problems.append(f"{what}在包里找不到：{relative}")


def read_manifest(folder: Path) -> tuple[Manifest | None, list[str]]:
    """The plugin in `folder` and what is wrong with it; (None, problems) when it cannot be used."""
    problems: list[str] = []
    try:
        data = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["包里没有 plugin.json"]
    except (OSError, ValueError) as exc:
        return None, [f"plugin.json 读不了：{exc}"]
    if not isinstance(data, dict):
        return None, ["plugin.json 要是一个对象"]
    plugin_id = _text(data, "id", problems, required=True)
    if plugin_id and not ID.fullmatch(plugin_id):
        problems.append("id 要写成 作者.名字 这样的小写字母、数字和点，比如 yourname.weather")
    if plugin_id.startswith("nest."):
        problems.append("nest. 开头的 id 留给 nest 自己")
    version = _text(data, "version", problems, required=True)
    if version and not VERSION.fullmatch(version):
        problems.append("version 要写成 1.0.0 这样")
    name = _text(data, "name", problems, required=True)
    needs = _text(data, "nest", problems).removeprefix(">=").strip()
    if needs and not VERSION.fullmatch(needs):
        problems.append("nest 要写成最低版本号，比如 0.6.0")
    elif needs and newer(needs, __version__):
        problems.append(f"它要 nest {needs} 以上，现在是 {__version__}：先更新 nest")

    provides = data.get("provides") or {}
    if not isinstance(provides, dict):
        problems.append("provides 要是一个对象")
        provides = {}
    for key in provides:
        if key not in KINDS:
            problems.append(f"provides 里不认识「{key}」（能写的：{'、'.join(KINDS)}）")
    for skill in provides.get("skills") or []:
        _inside(folder, skill, problems, "技能")
        if isinstance(skill, str) and not skill.lower().endswith(".md"):
            problems.append(f"技能要是 .md 文件：{skill}")
    for pet in provides.get("pets") or []:
        _inside(folder, pet, problems, "桌宠")
        if isinstance(pet, str) and not problems:
            place = folder / Path(*PurePosixPath(pet).parts)
            if not ((place / "pet.json").is_file() or (place / "acc.json").is_file()):
                problems.append(f"桌宠文件夹里要有 pet.json（桌宠或皮肤）或 acc.json（配饰）：{pet}")
    for theme in provides.get("themes") or []:
        _inside(folder, theme, problems, "主题")
        if not isinstance(theme, str) or problems:
            continue
        if not themes.THEME_FILE.fullmatch(PurePosixPath(theme).name):
            problems.append(f"主题文件名要是小写字母、数字和 - 的 .json，比如 sakura.json：{theme}")
            continue
        try:
            themes.read_theme_bytes((folder / Path(*PurePosixPath(theme).parts)).read_bytes())
        except (OSError, themes.ThemeError) as exc:
            problems.append(f"{theme}：{exc}")
    for pack in provides.get("stickers") or []:
        _inside(folder, pack, problems, "表情包")
        if not isinstance(pack, str) or problems:
            continue
        try:
            read_sticker_pack(folder / Path(*PurePosixPath(pack).parts))
        except PluginError as exc:
            problems.append(f"{pack}：{exc}")
    if provides.get("companion"):
        _inside(folder, provides["companion"], problems, "伙伴")
    mcp = provides.get("mcp")
    if mcp is not None and mcp != {}:
        command = mcp.get("command") if isinstance(mcp, dict) else None
        install = mcp.get("install", []) if isinstance(mcp, dict) else None
        if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
            problems.append("mcp.command 要写成命令和参数的列表")
        if not isinstance(install, list) or not all(isinstance(c, str) for c in install):
            problems.append("mcp.install 要写成命令的列表")
    python = provides.get("python")
    if python and (not isinstance(python, dict) or not isinstance(python.get("module"), str)):
        problems.append('python 要写成 {"module": "模块名"}')

    asked = data.get("permissions") or {}
    if not isinstance(asked, dict):
        problems.append("permissions 要是一个对象")
        asked = {}
    network = asked.get("network") or []
    if not isinstance(network, list) or not all(isinstance(d, str) and DOMAIN.fullmatch(d) for d in network):
        problems.append('permissions.network 要写成域名列表，比如 ["api.example.com"]')
        network = []
    secrets = []
    for item in asked.get("secrets") or []:
        if not isinstance(item, dict) or not SECRET.fullmatch(str(item.get("name") or "")):
            problems.append("permissions.secrets 每项要有 name（大写字母、数字、下划线）和 label")
            continue
        if reserved_secret(str(item["name"])):
            problems.append(
                f"permissions.secrets 的 name 不能用 {item['name']}（这是系统和程序自己用的变量名）"
            )
            continue
        secrets.append((str(item["name"]), str(item.get("label") or item["name"])[:TEXT_MAX]))
    confirm = asked.get("confirm") or []
    if not isinstance(confirm, list) or not all(isinstance(t, str) and TOOL.fullmatch(t) for t in confirm):
        problems.append("permissions.confirm 要写成工具名列表")
        confirm = []
    if problems:
        return None, problems
    manifest = Manifest(
        id=plugin_id,
        name=name,
        version=version,
        author=_text(data, "author", problems),
        description=_text(data, "description", problems),
        provides={k: v for k, v in provides.items() if v},
        network=tuple(network),
        secrets=tuple(secrets),
        confirm=tuple(confirm),
        nest=needs,
    )
    return manifest, []


def newer(version: str, than: str) -> bool:
    """Whether a version is later than another ("1.10.0" than "1.9.2")."""

    def numbers(text: str) -> list[int]:
        return [int(n) for n in re.findall(r"\d+", text)[:3]]

    return numbers(version) > numbers(than)


def read_sticker_pack(folder: Path) -> dict[str, Any]:
    """A sticker pack in a plugin: pack.json {"name", "items": [{"mood", "file"}]} and its pictures beside it.
    {"name", "items": [(mood, path)]}; PluginError says what is wrong."""
    meta = _json(folder / "pack.json")
    items = meta.get("items")
    if not isinstance(items, list) or not items:
        raise PluginError("要有 pack.json，里面有 items：[{mood, file}]")
    if len(items) > PACK_ITEMS:
        raise PluginError(f"一套最多 {PACK_ITEMS} 张")
    found: list[tuple[str, Path]] = []
    for item in items:
        mood = str(item.get("mood") or "").strip() if isinstance(item, dict) else ""
        name = PurePosixPath(str(item.get("file") or "") if isinstance(item, dict) else "").name
        path = folder / name
        if not mood or len(mood) > 12 or not name or path.suffix.lower() not in PACK_FILES:
            raise PluginError("items 每项要有 mood（12 字以内）和 file（png、gif、webp、jpg）")
        if not path.is_file() or path.is_symlink() or path.stat().st_size > PET_FILE_MAX:
            raise PluginError(f"没有这张图，或者太大了：{name}")
        found.append((mood, path))
    return {"name": str(meta.get("name") or folder.name)[:20], "items": found}


def unpack(data: bytes, target: Path) -> Path:
    """A zip's files into `target` (made fresh). The folder holding plugin.json: the top, or the one folder
    the zip has. PluginError for anything unsafe or too big."""
    if len(data) > ZIP_MAX:
        raise PluginError(f"插件包不能超过 {ZIP_MAX // 1024**2} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PluginError("这不是 zip 文件") from None
    members = archive.infolist()
    if len(members) > FILES_MAX:
        raise PluginError(f"插件包里的文件太多（最多 {FILES_MAX} 个）")
    if sum(m.file_size for m in members) > UNPACKED_MAX:
        raise PluginError(f"插件包解开后不能超过 {UNPACKED_MAX // 1024**2} MB")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    base = target.resolve()
    for member in members:
        pure = PurePosixPath(member.filename.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or (member.filename[1:2] == ":"):
            raise PluginError(f"插件包里有走出去的路径：{member.filename}")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise PluginError(f"插件包里不能有链接：{member.filename}")
        place = (base / Path(*pure.parts)).resolve() if pure.parts else base
        if place != base and not place.is_relative_to(base):
            raise PluginError(f"插件包里有走出去的路径：{member.filename}")
        if member.is_dir():
            place.mkdir(parents=True, exist_ok=True)
            continue
        place.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, place.open("wb") as sink:
            shutil.copyfileobj(source, sink)
    if (target / "plugin.json").is_file():
        return target
    tops = [p for p in target.iterdir() if p.is_dir()]
    if len(tops) == 1 and (tops[0] / "plugin.json").is_file():
        return tops[0]
    raise PluginError("包里没有 plugin.json（要在最上层，或者唯一的那个文件夹里）")


# -- what a plugin brings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    """技能: a way of doing something, in Markdown, that she reads when she needs it (read_skill)."""

    plugin: str
    name: str
    description: str
    path: Path


def read_skill_head(path: Path) -> tuple[str, str]:
    """(name, description): from front matter (name: / description:), else the first heading and line."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return path.stem, ""
    name, description = "", ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, _, value = line.partition(":")
            if key.strip() == "name":
                name = value.strip()
            elif key.strip() == "description":
                description = value.strip()
    for line in lines:
        stripped = line.strip()
        if not name and stripped.startswith("#"):
            name = stripped.lstrip("#").strip()
        elif (
            not description
            and stripped
            and not stripped.startswith(("#", "---"))
            and ":" not in stripped[:20]
        ):
            description = stripped
    return (name or path.stem)[:60], description[:200]


PACK_ITEMS = 60
PACK_FILES = (".png", ".gif", ".webp", ".jpg", ".jpeg")
PET_FILES = (".png", ".json")
PET_FILE_MAX = 8 * 1024**2


SKILL_SHOWN = 20_000  # characters of a skill shown before it is installed


def _skill_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= SKILL_SHOWN else text[:SKILL_SHOWN] + "\n……（后面还有，没显示全）"


def pet_item(folder: Path) -> dict[str, Any]:
    """What a plugin's pet folder is: a pet or a skin (pet.json; "skin_of" names whose skin it is) or an
    accessory (acc.json); its files; a picture for the console."""
    files = sorted(
        f.name
        for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in PET_FILES and f.stat().st_size <= PET_FILE_MAX
    )
    item: dict[str, Any] = {"files": files, "label": folder.name, "preview": "", "frame": 0}
    if (folder / "acc.json").is_file():
        meta = _json(folder / "acc.json")
        item |= {"kind": "accessory", "for": str(meta.get("for") or ""), "preview": "item.png"}
        item["label"] = str(meta.get("label") or folder.name)[:40]
        return item
    meta = _json(folder / "pet.json")
    actions = meta.get("actions") if isinstance(meta.get("actions"), dict) else {}
    idle = actions.get("idle") if isinstance(actions.get("idle"), dict) else {}
    item |= {
        "kind": "skin" if meta.get("skin_of") else "pet",
        "for": str(meta.get("skin_of") or meta.get("name") or ""),
        "label": str(meta.get("label") or folder.name)[:40],
        "preview": PurePosixPath(str(idle.get("file") or "")).name,
        "frame": int(meta.get("frame") or 0) if str(meta.get("frame") or "0").isdigit() else 0,
    }
    return item


def _json(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


# -- the plugins nest has ----------------------------------------------------------------------------------


class PluginManager:
    def __init__(self, db: Database, root: Path) -> None:
        self.db = db
        self.root = root
        self.installed: dict[str, tuple[Manifest, Path]] = {}
        self.broken: dict[str, list[str]] = {}  # folder name -> problems (a plugin.json edited by hand)
        self.state: dict[str, dict[str, Any]] = {}
        self.skill_index: dict[str, list[Skill]] = {}  # plugin id -> its skills (names and descriptions)
        # Trusted plugins' code (nest.plugin_code), loaded when nest starts: id -> {"added": [...]} or
        # {"problem": "..."}. Not here: not loaded this run (off, or installed since).
        self.code: dict[str, dict[str, Any]] = {}
        # A package from the market waiting to be installed: its stage -> the key it was signed with.
        self.stage_keys: dict[str, str] = {}

    async def load(self) -> None:
        await asyncio.to_thread(self._purge_staging)
        stored = (await SettingsRepo(self.db).all()).get(STATE)
        self.state = {
            str(k): v for k, v in (stored.items() if isinstance(stored, dict) else []) if isinstance(v, dict)
        }
        self.installed, self.broken = await asyncio.to_thread(self._scan)
        self.skill_index = await asyncio.to_thread(
            lambda: {pid: self._skills_of(m, f) for pid, (m, f) in self.installed.items()}
        )

    def _scan(self) -> tuple[dict[str, tuple[Manifest, Path]], dict[str, list[str]]]:
        found: dict[str, tuple[Manifest, Path]] = {}
        broken: dict[str, list[str]] = {}
        if not self.root.is_dir():
            return found, broken
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            manifest, problems = read_manifest(folder)
            if manifest is None or manifest.id != folder.name:
                broken[folder.name] = problems or ["文件夹名和 plugin.json 里的 id 对不上"]
            else:
                found[manifest.id] = (manifest, folder)
        return found, broken

    async def load_code(self) -> None:
        """Load the code of the trusted plugins that are on (only at startup: Python cannot unload it)."""
        from nest import plugin_code

        for manifest, folder in self.installed.values():
            module = (
                (manifest.provides.get("python") or {}).get("module") if manifest.trust == "trusted" else None
            )
            if not module or not self._switch(manifest.id).get("enabled"):
                continue
            if not self._switch(manifest.id).get("trusted_at"):
                # Not put in through the install that asks for trust (copied in by hand, or an older nest).
                self.code[manifest.id] = {"problem": NO_TRUST_RECORD}
                log.warning("插件 %s 没有信任记录，代码没载入", manifest.id)
                continue
            try:
                added = await asyncio.to_thread(plugin_code.load, manifest.id, folder, str(module))
                self.code[manifest.id] = {"added": added}
            except plugin_code.PluginCodeError as exc:
                self.code[manifest.id] = {"problem": str(exc)}
                log.warning("插件 %s 的代码没载入：%s", manifest.id, exc)

    # -- switches ------------------------------------------------------------------------------------------

    def _switch(self, plugin_id: str) -> dict[str, Any]:
        builtin = next((b for b in BUILTIN if b.id == plugin_id), None)
        default: dict[str, Any] = {
            "enabled": True,
            "bots": None if builtin is None or builtin.on_for_all else [],
        }
        return default | self.state.get(plugin_id, {})

    def on_for(self, plugin_id: str, bot_id: str) -> bool:
        switch = self._switch(plugin_id)
        bots = switch.get("bots")
        return bool(switch.get("enabled")) and (bots is None or bot_id in bots)

    def tool_allowed(self, bot_id: str, tool: str) -> bool:
        owner = TOOL_PLUGIN.get(tool)
        return owner is None or self.on_for(owner, bot_id)

    async def configure(self, plugin_id: str, *, enabled: bool | None = None, bots: Any = ...) -> None:
        if plugin_id not in TOOL_PLUGIN.values() and plugin_id not in self.installed:
            raise PluginError("没有这个插件")
        switch = self._switch(plugin_id)
        if enabled is not None:
            switch["enabled"] = enabled
        if bots is not ...:
            switch["bots"] = None if bots is None else sorted({str(b) for b in bots})
        self.state[plugin_id] = switch
        await SettingsRepo(self.db).set_many({STATE: self.state})

    # -- what the console shows ---------------------------------------------------------------------------

    def views(self) -> list[dict[str, Any]]:
        shown = [b.view() | self._switch(b.id) for b in BUILTIN]
        for manifest, folder in self.installed.values():
            skills = [
                {"name": k.name, "description": k.description} for k in self._skills_of(manifest, folder)
            ]
            pets = [{"path": path} | pet_item(folder / path) for path in self._pet_folders(manifest, folder)]
            theme_names = [
                {"name": t["name"], "description": t["description"]}
                for t in self._themes_of(manifest, folder)
            ]
            shown.append(
                manifest.view()
                | {k: v for k, v in self._switch(manifest.id).items() if k != "author_key"}
                | {"signed": bool(self.author_key(manifest.id))}
                | {
                    "folder": folder.name,
                    "skills": skills,
                    "pets": pets,
                    "themes": theme_names,
                    "code": self.code.get(manifest.id),
                }
            )
        return shown

    # -- contributions -------------------------------------------------------------------------------------

    @staticmethod
    def _skills_of(manifest: Manifest, folder: Path) -> list[Skill]:
        found = []
        for relative in manifest.provides.get("skills") or []:
            path = folder / Path(*PurePosixPath(relative).parts)
            if path.is_file():
                name, description = read_skill_head(path)
                found.append(Skill(manifest.id, name, description, path))
        return found

    def skills_for(self, bot_id: str) -> list[Skill]:
        """Her skills: those of the plugins on for her."""
        found: list[Skill] = []
        seen: set[str] = set()
        for manifest, _ in self.installed.values():
            if not self.on_for(manifest.id, bot_id):
                continue
            for skill in self.skill_index.get(manifest.id, []):
                name = skill.name if skill.name not in seen else f"{skill.name}（{manifest.name}）"
                seen.add(name)
                found.append(Skill(skill.plugin, name, skill.description, skill.path))
        return found

    @staticmethod
    def _themes_of(manifest: Manifest, folder: Path) -> list[dict[str, Any]]:
        found = []
        for relative in manifest.provides.get("themes") or []:
            pure = PurePosixPath(relative)
            try:
                theme = themes.read_theme_bytes((folder / Path(*pure.parts)).read_bytes())
            except (OSError, themes.ThemeError) as exc:
                log.warning("插件 %s 的主题 %s 读不了：%s", manifest.id, relative, exc)
                continue
            found.append(
                theme
                | {
                    "id": f"{manifest.id}/{pure.stem}",
                    "plugin": manifest.id,
                    "author": manifest.author or manifest.id,
                }
            )
        return found

    def themes(self) -> list[dict[str, Any]]:
        """The themes of the plugins that are on (for everyone: a theme is not a companion's)."""
        found = []
        for manifest, folder in self.installed.values():
            if manifest.provides.get("themes") and self._switch(manifest.id).get("enabled"):
                found.extend(self._themes_of(manifest, folder))
        return found

    @staticmethod
    def _pet_folders(manifest: Manifest, folder: Path) -> list[str]:
        return [
            str(PurePosixPath(p))
            for p in manifest.provides.get("pets") or []
            if (folder / Path(*PurePosixPath(p).parts)).is_dir()
        ]

    def pets(self) -> list[dict[str, Any]]:
        """Pets, skins and accessories of the plugins that are on, for the desktop app to fetch: with the
        plugin's version (to know when to fetch again) and who may use them (None: every companion)."""
        shown = []
        for manifest, folder in self.installed.values():
            switch = self._switch(manifest.id)
            paths = self._pet_folders(manifest, folder)
            if not paths or not switch.get("enabled"):
                continue
            shown.append(
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "bots": switch.get("bots"),
                    "items": [{"path": path} | pet_item(folder / path) for path in paths],
                }
            )
        return shown

    def author_key(self, plugin_id: str) -> str:
        """The key the installed one was signed with ("" : not signed, or not from the market)."""
        return str(self.state.get(plugin_id, {}).get("author_key") or "")

    def sticker_packs(self) -> list[dict[str, Any]]:
        """Sticker packs of the plugins that are on: {plugin, plugin_name, path, name, count}."""
        shown = []
        for manifest, folder in self.installed.values():
            if not self._switch(manifest.id).get("enabled"):
                continue
            for relative in manifest.provides.get("stickers") or []:
                try:
                    pack = read_sticker_pack(folder / Path(*PurePosixPath(relative).parts))
                except PluginError:
                    continue
                shown.append(
                    {
                        "plugin": manifest.id,
                        "plugin_name": manifest.name,
                        "path": relative,
                        "name": pack["name"],
                        "count": len(pack["items"]),
                    }
                )
        return shown

    def sticker_pack(self, plugin_id: str, path: str) -> dict[str, Any]:
        """One of sticker_packs(), read; PluginError when it is not there."""
        if not any(p["plugin"] == plugin_id and p["path"] == path for p in self.sticker_packs()):
            raise PluginError("没有这套表情包（插件没装，或者关着）")
        _, folder = self.installed[plugin_id]
        return read_sticker_pack(folder / Path(*PurePosixPath(path).parts))

    def pet_file(self, plugin_id: str, relative: str) -> Path:
        """One file of a plugin's pet folder; PluginError for anything else."""
        found = self.installed.get(plugin_id)
        if found is None:
            raise PluginError("没有这个插件")
        manifest, folder = found
        pure = PurePosixPath(relative)
        if ".." in pure.parts or pure.is_absolute() or pure.suffix.lower() not in PET_FILES:
            raise PluginError("没有这个文件")
        if str(pure.parent) not in self._pet_folders(manifest, folder):
            raise PluginError("没有这个文件")
        path = (folder / Path(*pure.parts)).resolve()
        if (
            not path.is_relative_to(folder.resolve())
            or not path.is_file()
            or path.stat().st_size > PET_FILE_MAX
        ):
            raise PluginError("没有这个文件")
        return path

    # -- installing ------------------------------------------------------------------------------------------

    def _staging(self) -> Path:
        return self.root / ".staging"

    def _purge_staging(self) -> int:
        """At startup: packages read but never installed nor cancelled, older than STAGING_KEPT, are removed
        (the console forgets them anyway). How many."""
        staging = self._staging()
        if not staging.is_dir():
            return 0
        cutoff = time.time() - STAGING_KEPT
        gone = 0
        for holder in staging.iterdir():
            with contextlib.suppress(OSError):
                if holder.lstat().st_mtime < cutoff:
                    if holder.is_dir() and not holder.is_symlink():
                        shutil.rmtree(holder, True)
                    else:
                        holder.unlink()
                    gone += 1
        if gone:
            log.info("清掉了 %d 个没装完的插件包", gone)
        return gone

    async def stage(self, data: bytes) -> dict[str, Any]:
        """Unpack and read a plugin, without installing it: {stage, plugin | problems, upgrade}."""
        stage = hashlib.sha256(data).hexdigest()[:16]
        folder = await asyncio.to_thread(unpack, data, self._staging() / stage)
        manifest, problems = await asyncio.to_thread(read_manifest, folder)
        if manifest is None:
            await asyncio.to_thread(shutil.rmtree, self._staging() / stage, True)
            raise PluginError("这个插件包有问题：" + "；".join(problems))
        old = self.installed.get(manifest.id)
        # Its skills in full: they tell her what to do, so the user reads them before saying yes.
        skills = await asyncio.to_thread(self._skills_of, manifest, folder)
        texts = [await asyncio.to_thread(_skill_text, skill.path) for skill in skills]
        return {
            "stage": stage,
            "plugin": manifest.view()
            | {
                "skills": [
                    {"name": k.name, "description": k.description, "text": text}
                    for k, text in zip(skills, texts, strict=True)
                ]
            },
            "upgrade": old[0].version if old else None,
        }

    def staged(self, stage: str) -> tuple[Path, Path]:
        """(the staging folder, the plugin's folder in it) of a package read but not installed."""
        if not re.fullmatch(r"[0-9a-f]{16}", stage):
            raise PluginError("没有这个待装的插件包")
        holder = self._staging() / stage
        if not holder.is_dir():
            raise PluginError("没有这个待装的插件包（可能已经装过了，重新选一次文件）")
        if (holder / "plugin.json").is_file():
            return holder, holder
        return holder, next(p for p in holder.iterdir() if p.is_dir())

    async def discard(self, stage: str) -> None:
        holder, _ = self.staged(stage)
        await asyncio.to_thread(shutil.rmtree, holder, True)

    async def install(self, stage: str, *, trusted: bool = False) -> Manifest:
        """Install a staged package. One whose code runs inside nest (trust "trusted") only with `trusted`:
        the user said they trust it. That is kept with its switch, and its code is loaded only then
        (load_code), whichever way it was put in."""
        holder, folder = self.staged(stage)
        manifest, problems = await asyncio.to_thread(read_manifest, folder)
        if manifest is None:
            raise PluginError("；".join(problems))
        if manifest.trust == "trusted" and not trusted:
            raise UntrustedPlugin(UNTRUSTED)
        target = self.root / manifest.id

        def move() -> None:
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(folder), str(target))
            shutil.rmtree(holder, ignore_errors=True)

        await asyncio.to_thread(move)
        fresh = manifest.id not in self.installed
        self.installed[manifest.id] = (manifest, target)
        self.skill_index[manifest.id] = await asyncio.to_thread(self._skills_of, manifest, target)
        switch = (
            # On, but for no companion yet: the user picks who gets it.
            {"enabled": True, "bots": [], "installed_at": time.time()} if fresh else self._switch(manifest.id)
        )
        # Signed (from the market): an upgrade must be signed with the same key (nest.core.market).
        if key := self.stage_keys.pop(stage, ""):
            switch["author_key"] = key
        if manifest.trust == "trusted":
            switch["trusted_at"] = time.time()
        else:
            switch.pop("trusted_at", None)
        self.state[manifest.id] = switch
        await SettingsRepo(self.db).set_many({STATE: self.state})
        return manifest

    async def uninstall(self, plugin_id: str) -> None:
        if plugin_id not in self.installed:
            raise PluginError("没有这个插件（nest 自带的不能卸载，只能关掉）")
        _, folder = self.installed.pop(plugin_id)
        self.skill_index.pop(plugin_id, None)
        await asyncio.to_thread(shutil.rmtree, folder, True)
        self.state.pop(plugin_id, None)
        await SettingsRepo(self.db).set_many({STATE: self.state})
