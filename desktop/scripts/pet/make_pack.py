"""Make a 桌宠 sprite pack from generated sheets (伙伴模式).

A sheet is one picture with several poses of the same character on a plain light background, laid out in a
grid. Each cell becomes one frame: the background is taken out from the edges inwards, small separate bits
(a drawn "Zzz", sparkles) are dropped — the app draws those — then the figure is scaled, stood on the
baseline and centred on her lower body (so a raised arm or a swinging twin-tail does not shift her). Pixel
style: frames are reduced to a real pixel grid and share one palette, which also evens out small colour
differences between frames and between sheets.

Each sheet may draw her at another size: --scale-ref names the cell (0-based, row by row) of a standing pose
in that sheet, which is scaled to FIGURE pixels tall; every cell of the sheet gets the same factor. Without
it, the sheet's tallest figure is used.

    python make_pack.py --out <pack dir> --name 阿鲸 --sheet sheet.png --grid 2x4 \\
        --frames idle,blink,wave,sleep,walk,walk,walk,walk [--scale-ref 0] [--sheet ...]

Writes <out>/<action>.png (frames side by side), <out>/pet.json and <out>/preview.png. Needs Pillow.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageChops

# Sizes per rendering style: "pixel" is reduced to a real pixel grid; "smooth" (a hand-drawn skin) keeps its
# lines and is drawn smoothly scaled. Set by --rendering before anything is cut.
STYLES = {"pixel": (112, 84), "smooth": (336, 252)}  # frame, a standing figure's height
FRAME = 112  # pixels of one frame, square: room above her for a raised arm
FIGURE = 84  # a standing figure's height in a frame
BASELINE = FRAME - 4  # where the feet stand
RENDERING = "pixel"
FOOTING = 0.25  # she is centred on the bottom quarter of her figure (skirt hem and feet)
BACKGROUND_TOLERANCE = 38  # how far from the corner colour still counts as background
HOLE_TOLERANCE = 7  # a closed-in patch is background only when it is that very colour
HOLES = 0.0  # the smallest such patch, as a share of the figure (--holes); 0: none are cut
SPECK_SHARE = 0.02  # parts smaller than this share of the main figure are dropped
# MAXCOVERAGE keeps small accent colours (eyes, earrings) that MEDIANCUT folds into the big greys.
COLOURS = 32
PALETTE_METHOD = Image.Quantize.MAXCOVERAGE
# Frame rate and looping per action; actions not listed play at 6 fps and loop.
PLAY = {
    "idle": {"fps": 2, "loop": True},
    "blink": {"fps": 10, "loop": False},
    "smile": {"fps": 1, "loop": True},
    "talk": {"fps": 4, "loop": True},
    "think": {"fps": 1.5, "loop": True},
    "happy": {"fps": 3, "loop": True},
    "shy": {"fps": 1.5, "loop": True},
    "pout": {"fps": 3, "loop": False},
    "peek": {"fps": 2.5, "loop": False},
    "greet": {"fps": 4, "loop": True},
    "walk": {"fps": 10, "loop": True},
    "wave": {"fps": 5, "loop": True},
    "surprised": {"fps": 1, "loop": True},
    "sit": {"fps": 1, "loop": True},
    "yawn": {"fps": 1, "loop": False},
    "sleep": {"fps": 0.7, "loop": True},
    "wake": {"fps": 1, "loop": False},
    "stretch": {"fps": 1, "loop": False},
    "lifted": {"fps": 2, "loop": True},
    "headpat": {"fps": 3, "loop": True},
    "patted": {"fps": 1.2, "loop": True},
    "work": {"fps": 2, "loop": True},
    "raise": {"fps": 3, "loop": True},
    "sad": {"fps": 1, "loop": True},
}


def _close(a: tuple[int, ...], b: tuple[int, ...], tolerance: int) -> bool:
    return sum(abs(x - y) for x, y in zip(a[:3], b[:3], strict=False)) <= tolerance * 3


def cut_background(cell: Image.Image) -> Image.Image:
    """Transparent where the background reaches from the edges (inside white, like an apron, stays; with
    --holes, larger patches of exactly the background inside her outline go too)."""
    image = cell.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    corner = pixels[0, 0]
    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        queue.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        queue.extend(((0, y), (width - 1, y)))
    while queue:
        x, y = queue.popleft()
        if not (0 <= x < width and 0 <= y < height) or seen[y * width + x]:
            continue
        seen[y * width + x] = 1
        if not _close(pixels[x, y], corner, BACKGROUND_TOLERANCE):
            continue
        pixels[x, y] = (0, 0, 0, 0)
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    if HOLES:
        cut_holes(image, corner)
    return image


def cut_holes(image: Image.Image, corner: tuple[int, ...]) -> None:
    """Background closed in by her outline (between an arm or a long curl and her body)."""
    width, height = image.size
    pixels = image.load()
    assert pixels is not None
    figure = sum(1 for a in image.getchannel("A").get_flattened_data() if a)
    seen = bytearray(width * height)
    for start in range(width * height):
        sx, sy = start % width, start // width
        if seen[start] or pixels[sx, sy][3] == 0 or not _close(pixels[sx, sy], corner, HOLE_TOLERANCE):
            continue
        seen[start] = 1
        patch = [(sx, sy)]
        for x, y in patch:  # grows while it is gone through
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if not (0 <= nx < width and 0 <= ny < height) or seen[ny * width + nx]:
                    continue
                value = pixels[nx, ny]
                if value[3] and _close(value, corner, HOLE_TOLERANCE):
                    seen[ny * width + nx] = 1
                    patch.append((nx, ny))
        if len(patch) >= figure * HOLES:
            for x, y in patch:
                # and the soft edge between it and her outline
                for nx in range(x - 1, x + 2):
                    for ny in range(y - 1, y + 2):
                        if 0 <= nx < width and 0 <= ny < height:
                            value = pixels[nx, ny]
                            if value[3] and _close(value, corner, BACKGROUND_TOLERANCE):
                                pixels[nx, ny] = (0, 0, 0, 0)
                pixels[x, y] = (0, 0, 0, 0)


def keep_main(image: Image.Image) -> Image.Image:
    """Only the parts of the figure big enough to belong to it."""
    width, height = image.size
    alpha = image.getchannel("A").load()
    assert alpha is not None
    label = [0] * (width * height)
    sizes: dict[int, int] = {}
    current = 0
    for start in range(width * height):
        sx, sy = start % width, start // width
        if label[start] or alpha[sx, sy] == 0:
            continue
        current += 1
        count = 0
        stack = [start]
        label[start] = current
        while stack:
            index = stack.pop()
            count += 1
            x, y = index % width, index // width
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    n = ny * width + nx
                    if not label[n] and alpha[nx, ny] != 0:
                        label[n] = current
                        stack.append(n)
        sizes[current] = count
    if not sizes:
        return image
    biggest = max(sizes.values())
    keep = {part for part, size in sizes.items() if size >= biggest * SPECK_SHARE}
    out = image.copy()
    pixels = out.load()
    assert pixels is not None
    for index, part in enumerate(label):
        if part and part not in keep:
            pixels[index % width, index // width] = (0, 0, 0, 0)
    return out


def blank_lines(drawn: Image.Image) -> list[bool]:
    """Which pixel columns of a mask have nothing drawn in them."""
    squeezed = drawn.resize((drawn.width, 1), Image.Resampling.BOX)
    return [value == 0 for value in squeezed.get_flattened_data()]


def cuts(blank: list[bool], parts: int) -> list[int]:
    """Where to cut a line of `parts` cells: the middle of the widest blank stretch near each even cut (a jump
    or a raised hand reaches over the even one), else the even one."""
    size = len(blank)
    found = [0]
    for n in range(1, parts):
        even = n * size // parts
        reach = size // parts // 4
        best = (0, even)
        start = None
        for at in range(even - reach, even + reach + 1):
            if at < even + reach and blank[at]:
                start = at if start is None else start
                continue
            if start is not None:
                best = max(best, (at - start, (start + at) // 2))
                start = None
        found.append(best[1])
    return [*found, size]


def cells(sheet: Image.Image, rows: int, cols: int) -> list[Image.Image]:
    picture = sheet.convert("RGB")
    corner = Image.new("RGB", picture.size, picture.getpixel((0, 0)))
    away = ImageChops.difference(picture, corner).convert("L")
    drawn = away.point(lambda v: 255 if v > BACKGROUND_TOLERANCE else 0)
    lines = cuts(blank_lines(drawn.transpose(Image.Transpose.TRANSPOSE)), rows)
    found = []
    for top, bottom in pairwise(lines):
        across = cuts(blank_lines(drawn.crop((0, top, drawn.width, bottom))), cols)
        found += [sheet.crop((left, top, right, bottom)) for left, right in pairwise(across)]
    return found


def footing_centre(figure: Image.Image) -> float:
    """Where her lower body is, across: the middle of what is drawn in the bottom quarter."""
    top = int(figure.height * (1 - FOOTING))
    box = figure.crop((0, top, figure.width, figure.height)).getchannel("A").getbbox()
    return (box[0] + box[2]) / 2 if box else figure.width / 2


def palette_of(figures: list[Image.Image]) -> Image.Image:
    """A palette from the figures' opaque pixels only (empty space would pull it towards black)."""
    pixels = [p[:3] for f in figures for p in f.get_flattened_data() if p[3]]
    width = 1024
    height = max(1, -(-len(pixels) // width))
    pixels += [pixels[-1]] * (width * height - len(pixels))
    strip = Image.new("RGB", (width, height))
    strip.putdata(pixels)
    return strip.quantize(colors=COLOURS, method=PALETTE_METHOD)


def pixelate(figures: list[Image.Image], scales: list[float], groups: list[int]) -> list[Image.Image]:
    """Each figure by its sheet's factor onto a FRAME×FRAME grid, feet on BASELINE, centred on its lower body.
    Each sheet (group) gets its own palette: one shared by many sheets drifts towards what most of them have
    (a blush, another model's tones) and greys or tints the rest."""
    small = []
    for figure, scale in zip(figures, scales, strict=True):
        size = (max(1, round(figure.width * scale)), max(1, round(figure.height * scale)))
        if RENDERING == "smooth":
            small.append(figure.resize(size, Image.Resampling.LANCZOS))
            continue
        reduced = figure.resize(size, Image.Resampling.BOX)
        # Hard edges: a pixel is there or not.
        alpha = reduced.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        reduced.putalpha(alpha)
        small.append(reduced)
    if RENDERING == "smooth":
        return [_placed(f) for f in small]
    palettes = {g: palette_of([f for f, h in zip(small, groups, strict=True) if h == g]) for g in set(groups)}
    frames = []
    for f, group in zip(small, groups, strict=True):
        reduced = f.convert("RGB").quantize(palette=palettes[group], dither=Image.Dither.NONE)
        coloured = reduced.convert("RGBA")
        coloured.putalpha(f.getchannel("A"))
        frames.append(_placed(coloured))
    return frames


def _placed(figure: Image.Image) -> Image.Image:
    """A figure on its frame: feet on BASELINE, lower body in the middle."""
    frame = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    left = round(FRAME / 2 - footing_centre(figure))
    frame.paste(figure, (left, BASELINE - figure.height), figure)
    return frame


def figure_of(cell: Image.Image) -> Image.Image:
    cleaned = keep_main(cut_background(cell))
    box = cleaned.getbbox()
    return cleaned.crop(box) if box else cleaned


def main() -> None:
    raw = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=raw)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--sheet", required=True, type=Path, action="append")
    parser.add_argument("--grid", required=True, action="append", help="rows x cols per sheet, e.g. 2x4")
    parser.add_argument("--frames", required=True, action="append", help="action per cell, comma separated")
    parser.add_argument("--scale-ref", action="append", type=int, help="per sheet: a standing pose's cell")
    parser.add_argument("--rendering", choices=sorted(STYLES), default="pixel")
    parser.add_argument("--label", default="", help="the skin's name in the menu, e.g. 像素 · 冬装")
    parser.add_argument(
        "--holes", type=float, default=0.0, help="cut closed-in background of this share of her, e.g. 0.004"
    )
    args = parser.parse_args()
    global FRAME, FIGURE, BASELINE, RENDERING, HOLES
    HOLES = args.holes
    RENDERING = args.rendering
    FRAME, FIGURE = STYLES[RENDERING]
    BASELINE = FRAME - FRAME // 28
    refs = args.scale_ref or []
    if refs and len(refs) != len(args.sheet):
        parser.error("--scale-ref: give one per sheet, or none")

    named: list[tuple[str, Image.Image]] = []
    scales: list[float] = []
    groups: list[int] = []
    for n, (sheet_path, grid, frames) in enumerate(zip(args.sheet, args.grid, args.frames, strict=True)):
        rows, cols = (int(k) for k in grid.lower().split("x"))
        actions = [a.strip() for a in frames.split(",")]
        figures = [figure_of(cell) for cell in cells(Image.open(sheet_path), rows, cols)]
        reference = figures[refs[n]] if refs else max(figures, key=lambda f: f.height)
        scale = FIGURE / reference.height
        for action, figure in zip(actions, figures, strict=False):
            if action and action != "-":
                named.append((action, figure))
                scales.append(scale)
                groups.append(n)
    frames = pixelate([figure for _, figure in named], scales, groups)

    args.out.mkdir(parents=True, exist_ok=True)
    actions: dict[str, list[Image.Image]] = {}
    for (action, _), frame in zip(named, frames, strict=True):
        actions.setdefault(action, []).append(frame)
    meta = {
        "name": args.name,
        "label": args.label,
        "rendering": RENDERING,
        "frame": FRAME,
        "baseline": BASELINE,
        "figure": FIGURE,
        "actions": {},
    }
    for action, images in actions.items():
        sheet = Image.new("RGBA", (FRAME * len(images), FRAME), (0, 0, 0, 0))
        for n, image in enumerate(images):
            sheet.paste(image, (n * FRAME, 0))
        sheet.save(args.out / f"{action}.png")
        play = PLAY.get(action, {"fps": 6, "loop": True})
        meta["actions"][action] = {"file": f"{action}.png", "frames": len(images), **play}
    (args.out / "pet.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    preview = Image.new("RGBA", (FRAME * len(frames), FRAME), (40, 44, 52, 255))
    for n, frame in enumerate(frames):
        preview.paste(frame, (n * FRAME, 0), frame)
    zoom = 3 if RENDERING == "pixel" else 1
    big = preview.resize((preview.width * zoom, preview.height * zoom), Image.Resampling.NEAREST)
    big.save(args.out / "preview.png")
    listed = ", ".join(f"{a}×{len(i)}" for a, i in actions.items())
    print(f"{args.name}: {len(frames)} frames, actions {listed}")


if __name__ == "__main__":
    main()
