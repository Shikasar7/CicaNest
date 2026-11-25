"""The app's icons from the console's icon (web/public/icon.png), as PNG and ICO files, so the desktop app,
its tray and the console look the same.

Usage: python make_icons.py            (writes desktop/assets/icons/)
Needs Pillow. Each size is composed at 8x and scaled down, so the badge stays round at 16 px.

Tray states: ok (the icon), starting (an amber ring, bottom right), attention (an amber dot), down (the icon
greyed, a red dot). Around the badge a transparent ring is cut, so it reads on any taskbar colour.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "assets" / "icons"
SOURCE = HERE.parent.parent / "web" / "public" / "icon.png"
SS = 8  # supersampling factor

AMBER = (232, 163, 58, 255)  # #E8A33A
RED = (229, 72, 77, 255)  # #E5484D

APP_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512]
TRAY_SIZES = [16, 20, 24, 32, 40, 48, 64]


def art() -> Image.Image:
    return Image.open(SOURCE).convert("RGBA")


def mark(size: int, *, grey: bool = False, badge=None) -> Image.Image:
    """The icon at `size` px. badge: None, ("ring", color) or ("dot", color), bottom right."""
    px = size * SS
    img = art().resize((px, px), Image.Resampling.LANCZOS)
    if grey:
        alpha = img.getchannel("A")
        img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(0.0)).enhance(0.8)
        img.putalpha(alpha)
    if badge is not None:
        small = size <= 24
        kind, color = badge
        unit = px / 32
        cx = cy = 25.4 * unit
        r = (6.4 if small else 5.6) * unit
        gap = (2.0 if small else 1.8) * unit
        hole = Image.new("L", (px, px), 0)
        ImageDraw.Draw(hole).ellipse((cx - r - gap, cy - r - gap, cx + r + gap, cy + r + gap), fill=255)
        alpha = img.getchannel("A")
        alpha.paste(0, mask=hole)
        img.putalpha(alpha)
        draw = ImageDraw.Draw(img)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        if kind == "ring":
            inner = r - (2.6 if small else 2.2) * unit
            draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=(0, 0, 0, 0))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(path: Path, sizes: list[int], **kwargs) -> None:
    images = [mark(s, **kwargs) for s in sizes]
    largest = images[-1]
    largest.save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in PNG_SIZES:
        mark(size).save(OUT / f"nest-{size}.png")
    save_ico(OUT / "nest.ico", APP_SIZES)

    states = {
        "ok": {},
        "starting": {"badge": ("ring", AMBER)},
        "attention": {"badge": ("dot", AMBER)},
        "down": {"grey": True, "badge": ("dot", RED)},
    }
    for name, kwargs in states.items():
        save_ico(OUT / f"tray-{name}.ico", TRAY_SIZES, **kwargs)
        # PNG strip at 1x/1.25x/1.5x/2x for anything that prefers PNG (and for eyeballing).
        for size, suffix in ((16, ""), (20, "@1.25x"), (24, "@1.5x"), (32, "@2x")):
            mark(size, **kwargs).save(OUT / f"tray-{name}{suffix}.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
