"""Draw the nest mark (a bowl holding three dots on an ink square) as PNG and ICO files.

Usage: python make_icons.py            (writes desktop/assets/icons/)
Needs Pillow. Every size is drawn at 8x and scaled down, so edges stay smooth at 16 px.

Geometry (a 32-unit square, same as assets/nest-mark.svg): the bowl is the curve
M6.5 13.3 C6.5 18.8 10.5 21.3 16 21.3 C21.5 21.3 25.5 18.8 25.5 13.3, and the three dots
rest inside it at (11.6,14.4), (16,12.2), (20.4,14.4) -- eggs in a nest, one per bot.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icons"
SS = 8  # supersampling factor

INK = (23, 32, 42, 255)  # #17202A
BOWL = (242, 244, 245, 255)  # #F2F4F5
HUES = [(47, 127, 208, 255), (226, 85, 127, 255), (30, 156, 130, 255)]  # #2F7FD0 #E2557F #1E9C82
MUTED = [(120, 130, 140, 255)] * 3  # dots when nest is down
AMBER = (232, 163, 58, 255)  # #E8A33A
RED = (229, 72, 77, 255)  # #E5484D

APP_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512]
TRAY_SIZES = [16, 20, 24, 32, 40, 48, 64]


def bezier(p0, p1, p2, p3, steps):
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        yield (
            u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
        )


BOWL_CURVES = (
    ((6.5, 13.3), (6.5, 18.8), (10.5, 21.3), (16, 21.3)),
    ((16, 21.3), (21.5, 21.3), (25.5, 18.8), (25.5, 13.3)),
)
DOTS = ((11.6, 14.4), (16, 12.2), (20.4, 14.4))


def bowl_points(steps=240):
    for curve in BOWL_CURVES:
        yield from bezier(*curve, steps)


def mark(size: int, *, dots=HUES, badge=None) -> Image.Image:
    """The mark at `size` px. badge: None, ("ring", color) or ("dot", color), bottom right."""
    px = size * SS
    small = size <= 24
    # Small sizes: no margin, a heavier bowl and bigger dots so the shape survives 16 px.
    margin = 0 if small else round(size * 0.03) * SS
    stroke = 2.9 if small else 2.4
    radius = 2.9 if small else 2.6
    corner = 7 if small else 8

    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    unit = (px - 2 * margin) / 32

    def at(x, y):
        return margin + x * unit, margin + y * unit

    draw.rounded_rectangle((margin, margin, px - margin - 1, px - margin - 1), radius=corner * unit, fill=INK)
    half = stroke / 2 * unit
    for x, y in bowl_points():
        cx, cy = at(x, y)
        draw.ellipse((cx - half, cy - half, cx + half, cy + half), fill=BOWL)
    for (x, y), color in zip(DOTS, dots, strict=True):
        cx, cy = at(x, y)
        r = radius * unit
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    if badge is not None:
        kind, color = badge
        cx, cy = at(25.4, 25.4)
        r = (6.4 if small else 5.6) * unit
        gap = (2.0 if small else 1.8) * unit
        # Cut a transparent ring out of the mark so the badge reads on any taskbar colour.
        hole = Image.new("L", (px, px), 0)
        ImageDraw.Draw(hole).ellipse((cx - r - gap, cy - r - gap, cx + r + gap, cy + r + gap), fill=255)
        alpha = img.getchannel("A")
        alpha.paste(0, mask=hole)
        img.putalpha(alpha)
        draw = ImageDraw.Draw(img)
        if kind == "dot":
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        else:
            width = (2.6 if small else 2.2) * unit
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
            inner = r - width
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
        "down": {"dots": MUTED, "badge": ("dot", RED)},
    }
    for name, kwargs in states.items():
        save_ico(OUT / f"tray-{name}.ico", TRAY_SIZES, **kwargs)
        # PNG strip at 1x/1.25x/1.5x/2x for anything that prefers PNG (and for eyeballing).
        for size, suffix in ((16, ""), (20, "@1.25x"), (24, "@1.5x"), (32, "@2x")):
            mark(size, **kwargs).save(OUT / f"tray-{name}{suffix}.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
