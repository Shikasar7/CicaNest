"""主题 (外观): the colours of the console, the chat window and the desktop app's own windows.

A theme is a small JSON file — nest's own (BUILTIN) or one a plugin provides ("provides": {"themes":
["themes/sakura.json"]}) — with colours for the light look and the dark one:

    {"name": "樱花", "description": "…",
     "light": {"accent": "#c13d6b", "background": "#f7f1f3", …},
     "dark": {"accent": "#f27fa0"}}

Only colours (#rrggbb) under the names in SLOTS; anything left out comes from nest's own look. What the
user picked (setting "appearance": a theme and, if they like, an accent colour of their own) is kept here,
in nest, so the console, the chat window and the desktop app look the same wherever they are opened.
Light or dark stays a choice of each screen (the console remembers it per browser).

resolve() turns a theme into every colour the interfaces use, and makes sure what has to be read can be
read: body text and secondary text against the backgrounds, a button's label against the accent, the
accent against the page (WCAG 2: 4.5:1 for text, 3:1 for a control's edge). A colour that falls short is
moved toward black or white until it does not.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any

SETTING = "appearance"  # {"theme": theme id, "accent": "#rrggbb" | None}
DEFAULT_ID = "nest.ink"  # the stylesheet's own colours, which every theme is resolved against
CHOSEN_ID = "nest.candy"  # what is shown until another one is picked
MODES = ("light", "dark")
# What a theme may set. The first eight are its own; the rest follow from them when left out.
BASE = ("accent", "background", "surface", "card", "sidebar", "text", "muted", "border")
DERIVED = ("accent_text", "fill", "hover", "input", "ring", "sidebar_active", "popover")
SLOTS = BASE + DERIVED
HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})")
THEME_FILE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}\.json")
FILE_MAX = 64 * 1024
TEXT_MAX = 200
NAME_MAX = 40

TEXT_CONTRAST = 4.5
CONTROL_CONTRAST = 3.0

# nest's own look (web/src/index.css): what every theme starts from.
DEFAULT: dict[str, dict[str, str]] = {
    "light": {
        "accent": "#17202a",
        "background": "#f2f4f5",
        "surface": "#f8f9fa",
        "card": "#ffffff",
        "sidebar": "#eceff1",
        "text": "#17202a",
        "muted": "#5e6b76",
        "border": "#e1e6ea",
        "accent_text": "#f7f9fa",
        "fill": "#eceff1",
        "hover": "#e6eaed",
        "input": "#d3dae0",
        "ring": "#8ea2b2",
        "sidebar_active": "#ffffff",
        "popover": "#ffffff",
    },
    "dark": {
        "accent": "#e8edf1",
        "background": "#121619",
        "surface": "#161b1f",
        "card": "#1a2025",
        "sidebar": "#161b1f",
        "text": "#e8edf1",
        "muted": "#93a1ad",
        "border": "#262e35",
        "accent_text": "#121619",
        "fill": "#20272d",
        "hover": "#262f36",
        "input": "#323c45",
        "ring": "#5b7083",
        "sidebar_active": "#222a30",
        "popover": "#1e252b",
    },
}

# Which colours each derived one is made from: kept from DEFAULT only while those are.
SOURCES = {
    "accent_text": ("accent",),
    "fill": ("background", "text"),
    "hover": ("background", "text"),
    "input": ("border", "text"),
    "ring": ("accent", "background"),
    "sidebar_active": ("card", "sidebar", "text"),
    "popover": ("card", "text"),
}


def _accent_only(name: str, description: str, light: str, dark: str) -> dict[str, Any]:
    return {"name": name, "description": description, "light": {"accent": light}, "dark": {"accent": dark}}


BUILTIN: dict[str, dict[str, Any]] = {
    DEFAULT_ID: {"name": "墨", "description": "nest 原来的样子：冷灰底，墨色按钮", "light": {}, "dark": {}},
    "nest.sea": _accent_only("海蓝", "冷灰底，蓝色按钮", "#2a6cc0", "#6aaef2"),
    "nest.bamboo": _accent_only("青竹", "冷灰底，青绿色按钮", "#157a63", "#4cc7a8"),
    "nest.sakura": _accent_only("樱粉", "冷灰底，粉色按钮", "#c13d6b", "#f27fa0"),
    "nest.wisteria": _accent_only("紫藤", "冷灰底，紫色按钮", "#6b4fc8", "#ad8cf0"),
    "nest.amber": _accent_only("琥珀", "冷灰底，琥珀色按钮", "#a35c0a", "#eba35c"),
    "nest.paper": {
        "name": "暖纸",
        "description": "米白和深褐的暖色底，赭石色按钮",
        "light": {
            "accent": "#8a4b2a",
            "background": "#f4f1ec",
            "surface": "#f9f7f3",
            "card": "#fffdfa",
            "sidebar": "#ece7df",
            "text": "#2b241c",
            "muted": "#6f6456",
            "border": "#e4ddd2",
        },
        "dark": {
            "accent": "#e3a578",
            "background": "#171411",
            "surface": "#1c1915",
            "card": "#221e19",
            "sidebar": "#1c1915",
            "text": "#eee6da",
            "muted": "#a89b8a",
            "border": "#2f2a23",
        },
    },
    "nest.dusk": {
        "name": "暮蓝",
        "description": "带一点蓝的底色，靛蓝按钮",
        "light": {
            "accent": "#3d4fb8",
            "background": "#eef1f7",
            "surface": "#f5f7fb",
            "card": "#ffffff",
            "sidebar": "#e5e9f3",
            "text": "#1a2033",
            "muted": "#5a6380",
            "border": "#dce1ee",
        },
        "dark": {
            "accent": "#8c9cf5",
            "background": "#10131f",
            "surface": "#141827",
            "card": "#191e30",
            "sidebar": "#141827",
            "text": "#e6e9f5",
            "muted": "#949bbb",
            "border": "#252b42",
        },
    },
    # Made for the cute chat window: peach and milk, soft and round.
    "nest.candy": {
        "name": "软糖",
        "description": "蜜桃牛奶：暖杏色底，橙桃色按钮，配可爱的聊天窗口",
        "light": {
            "accent": "#c4532c",
            "background": "#fdf4ec",
            "surface": "#fff9f4",
            "card": "#ffffff",
            "sidebar": "#fae9dc",
            "text": "#3b2a22",
            "muted": "#856a5c",
            "border": "#f3dfd0",
        },
        "dark": {
            "accent": "#f7a584",
            "background": "#1b1612",
            "surface": "#211b16",
            "card": "#2a221c",
            "sidebar": "#211b16",
            "text": "#f7ece4",
            "muted": "#bda999",
            "border": "#3b3027",
        },
    },
}


class ThemeError(Exception):
    """Why a theme file cannot be used, in one Chinese sentence."""


# -- colour arithmetic ---------------------------------------------------------------------------------------


def hex_colour(value: Any) -> str | None:
    """'#abc' or '#AABBCC' as '#aabbcc'; None for anything else."""
    if not isinstance(value, str) or not HEX.fullmatch(value.strip()):
        return None
    digits = value.strip()[1:].lower()
    return "#" + ("".join(c * 2 for c in digits) if len(digits) == 3 else digits)


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    """`amount` of b into a (0: a, 1: b), in sRGB like CSS color-mix."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb, strict=True)))  # type: ignore[arg-type]


def luminance(colour: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _toward(light_ground: bool) -> str:
    return "#000000" if light_ground else "#ffffff"


def readable(colour: str, grounds: list[str], minimum: float) -> str:
    """`colour`, moved toward black (on light grounds) or white (on dark ones) until it reaches `minimum`
    against every ground; as far as it gets when it never does."""
    light_ground = sum(luminance(g) for g in grounds) / len(grounds) > 0.18
    target = _toward(light_ground)
    best = colour
    for step in range(21):
        candidate = mix(colour, target, step / 20)
        best = candidate
        if all(contrast(candidate, g) >= minimum for g in grounds):
            return candidate
    return best


def label_on(accent: str) -> str:
    """The label for a button filled with `accent`: near white or near black, whichever reads better."""
    return max(("#ffffff", "#11161a"), key=lambda c: contrast(c, accent))


# -- themes --------------------------------------------------------------------------------------------------


def parse_theme(data: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """A theme file's contents, checked: ({name, description, light, dark}, []) or (None, problems)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return None, ["主题文件要是一个对象"]
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("主题要有 name（名字）")
        name = ""
    description = data.get("description", "")
    if not isinstance(description, str):
        problems.append("description 要写成文字")
        description = ""
    theme: dict[str, Any] = {
        "name": name.strip()[:NAME_MAX],
        "description": description.strip()[:TEXT_MAX],
    }
    for mode in MODES:
        colours = data.get(mode) or {}
        if not isinstance(colours, dict):
            problems.append(f"{mode} 要是一个对象")
            colours = {}
        checked = {}
        for slot, value in colours.items():
            if slot not in SLOTS:
                problems.append(f"{mode} 里不认识「{slot}」（能写的：{'、'.join(SLOTS)}）")
                continue
            colour = hex_colour(value)
            if colour is None:
                problems.append(f"{mode}.{slot} 要写成 #rrggbb 这样的颜色")
                continue
            checked[slot] = colour
        theme[mode] = checked
    for key in data:
        if key not in ("name", "description", *MODES):
            problems.append(f"主题里不认识「{key}」")
    if not problems and not any(theme[m] for m in MODES):
        problems.append("主题至少要给一个颜色（比如 light.accent）")
    return (None, problems) if problems else (theme, [])


def read_theme_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > FILE_MAX:
        raise ThemeError(f"主题文件不能超过 {FILE_MAX // 1024} KB")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ThemeError("主题文件不是 JSON") from None
    theme, problems = parse_theme(data)
    if theme is None:
        raise ThemeError("主题文件有问题：" + "；".join(problems))
    return theme


def _other_mode_accent(theme: dict[str, Any], mode: str) -> str | None:
    """A theme that gives an accent for one look only: the other look uses the same hue (made readable)."""
    other = theme.get("dark" if mode == "light" else "light") or {}
    return other.get("accent")


def resolve_mode(theme: dict[str, Any], mode: str, accent: str | None = None) -> dict[str, str]:
    given = dict(theme.get(mode) or {})
    if "accent" not in given and _other_mode_accent(theme, mode):
        given["accent"] = _other_mode_accent(theme, mode)  # type: ignore[assignment]
        given.pop("accent_text", None)
        given.pop("ring", None)
    if accent:
        given["accent"] = accent
        given.pop("accent_text", None)
        given.pop("ring", None)
    base = DEFAULT[mode]
    colours = {slot: given.get(slot, base[slot]) for slot in BASE}
    changed = {slot for slot in BASE if colours[slot] != base[slot]}
    dark = mode == "dark"

    # What is read must be readable.
    grounds = [colours["background"], colours["surface"], colours["card"], colours["sidebar"]]
    colours["text"] = readable(colours["text"], grounds, 7.0 if colours["text"] == base["text"] else 4.5)
    colours["muted"] = readable(colours["muted"], grounds, TEXT_CONTRAST)

    derived = {
        "fill": lambda: mix(colours["background"], colours["text"], 0.05),
        "hover": lambda: mix(colours["background"], colours["text"], 0.08),
        "input": lambda: mix(colours["border"], colours["text"], 0.12),
        "ring": lambda: mix(colours["accent"], colours["background"], 0.4),
        "sidebar_active": lambda: mix(colours["sidebar"], colours["text"], 0.07) if dark else colours["card"],
        "popover": lambda: mix(colours["card"], colours["text"], 0.03) if dark else colours["card"],
    }
    for slot, make in derived.items():
        if slot in given:
            colours[slot] = given[slot]
        elif changed & set(SOURCES[slot]):
            colours[slot] = make()
        else:
            colours[slot] = base[slot]

    # A button filled with the accent: its label reads (4.5), and it stands out from the page (3).
    page = [colours["background"], colours["card"]]
    accent_colour = colours["accent"]
    for step in range(21):
        candidate = mix(colours["accent"], _toward(not dark), step / 20)
        label = given.get("accent_text") if step == 0 and "accent_text" in given else label_on(candidate)
        if contrast(candidate, label) >= TEXT_CONTRAST and all(
            contrast(candidate, g) >= CONTROL_CONTRAST for g in page
        ):
            accent_colour = candidate
            colours["accent_text"] = label
            break
    else:
        accent_colour = colours["accent"]
        colours["accent_text"] = label_on(accent_colour)
    if accent_colour != colours["accent"]:
        colours["accent"] = accent_colour
        if "ring" not in given:
            colours["ring"] = mix(accent_colour, colours["background"], 0.4)
    if colours["accent"] == base["accent"] and "accent_text" not in given:
        colours["accent_text"] = base["accent_text"]  # nest's own look, exactly
    return colours


def resolve(theme: dict[str, Any], accent: str | None = None) -> dict[str, dict[str, str]]:
    """Every colour of both looks."""
    return {mode: resolve_mode(theme, mode, accent) for mode in MODES}


# -- the themes nest has, and the one picked -----------------------------------------------------------------


def catalogue(plugins: Any) -> dict[str, dict[str, Any]]:
    """Theme id -> theme (with "plugin": the plugin id, or None for nest's own), for every theme nest has:
    its own, then those of plugins that are switched on."""
    found = {tid: {**t, "id": tid, "plugin": None, "author": "nest"} for tid, t in BUILTIN.items()}
    for item in plugins.themes() if plugins is not None else []:
        found[item["id"]] = item
    return found


def choice_of(stored: Any) -> dict[str, Any]:
    stored = stored if isinstance(stored, dict) else {}
    theme = stored.get("theme") if isinstance(stored.get("theme"), str) else CHOSEN_ID
    return {"theme": theme, "accent": hex_colour(stored.get("accent"))}


def view(choice: dict[str, Any], themes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the console needs: the choice, the themes to pick from (with a few colours each to show) and
    every colour of the picked one. "plain": nest's own look, nothing to change."""
    picked = themes.get(choice["theme"])
    missing = picked is None
    theme = picked or themes.get(CHOSEN_ID) or themes[DEFAULT_ID]
    listed = []
    for tid, t in themes.items():
        swatch = resolve(t)
        listed.append(
            {
                "id": tid,
                "name": t["name"],
                "description": t.get("description", ""),
                "author": t.get("author", ""),
                "plugin": t.get("plugin"),
                "swatch": {
                    mode: {k: swatch[mode][k] for k in ("accent", "background", "card", "text")}
                    for mode in MODES
                },
            }
        )
    return {
        "theme": choice["theme"],
        "accent": choice["accent"],
        "missing": missing,
        "plain": theme["id"] == DEFAULT_ID and not choice["accent"],
        "themes": listed,
        "colors": resolve(theme, choice["accent"]),
    }


# -- sharing -------------------------------------------------------------------------------------------------

PLUGIN_ID = re.compile(r"[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+")


def package(theme: dict[str, Any], *, author: str = "", plugin_id: str | None = None) -> tuple[str, bytes]:
    """A theme as a plugin zip anyone can install (「插件」page, or a market): (plugin id, zip bytes)."""
    body = {
        "name": theme["name"],
        "description": theme.get("description", ""),
        "light": theme.get("light", {}),
        "dark": theme.get("dark", {}),
    }
    text = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    if plugin_id is None:
        plugin_id = "my.theme-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    if not PLUGIN_ID.fullmatch(plugin_id) or plugin_id.startswith("nest."):
        raise ThemeError("插件 id 要写成 作者.名字 这样的小写字母、数字和点，比如 yourname.sakura")
    manifest = {
        "id": plugin_id,
        "name": f"主题：{theme['name']}",
        "version": "1.0.0",
        "author": author.strip()[:NAME_MAX],
        "description": body["description"] or "一套 nest 的配色",
        "provides": {"themes": ["themes/theme.json"]},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("themes/theme.json", text)
    return plugin_id, buffer.getvalue()
