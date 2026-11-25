"""Make 桌宠 accessories (伙伴模式) from a generated sheet: small items worn on the head over any skin of the
same rendering style — a crown, cat ears, a hair clip.

Each cell of the sheet holds one item on a plain light background. It is cut out like a pose (make_pack.py),
sized in the pack's own pixels and, for pixel style, reduced to the pixel grid with its own small palette.
Where it sits is given relative to her figure, so it fits every skin and pose: --at "dx,dy" moves it from the
top of her head (in shares of a standing figure's height; dy > 0 is down into the hair, dx > 0 is towards
the side she faces).

    python make_accessories.py --out <pack dir>/accessories --sheet items.png --grid 1x4 \\
        --item crown:小皇冠:28:0,0.04 --item ears:猫耳:40:0,0.06 ...

--item is id:label:width:dx,dy (width in pixel-style pack pixels, where a standing figure is 84 tall; a
smooth item keeps its drawn pixels and is scaled to the same share). Items with the same label are one
accessory in different styles: she keeps wearing it when her skin changes style. Writes <out>/<id>/item.png
and <out>/<id>/acc.json. Needs Pillow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_pack import FIGURE, cells, figure_of
from PIL import Image

COLOURS = 16


def pixel_item(figure: Image.Image, width: int) -> Image.Image:
    height = max(1, round(figure.height * width / figure.width))
    small = figure.resize((width, height), Image.Resampling.BOX)
    alpha = small.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    palette = small.convert("RGB").quantize(colors=COLOURS, method=Image.Quantize.MAXCOVERAGE)
    coloured = palette.convert("RGBA")
    coloured.putalpha(alpha)
    return coloured


def main() -> None:
    raw = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=raw)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sheet", required=True, type=Path)
    parser.add_argument("--grid", required=True, help="rows x cols, e.g. 1x4")
    parser.add_argument("--item", required=True, action="append", help="id:label:width:dx,dy per cell")
    parser.add_argument("--rendering", choices=("pixel", "smooth"), default="pixel")
    args = parser.parse_args()

    rows, cols = (int(k) for k in args.grid.lower().split("x"))
    for spec, cell in zip(args.item, cells(Image.open(args.sheet), rows, cols), strict=False):
        item_id, label, width, at = spec.split(":")
        dx, dy = (float(v) for v in at.split(","))
        figure = figure_of(cell)
        image = pixel_item(figure, int(width)) if args.rendering == "pixel" else figure
        folder = args.out / item_id
        folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / "item.png")
        meta = {
            "label": label,
            "rendering": args.rendering,
            "slot": "head",
            # Sizes and place in shares of a standing figure's height: the same on every skin of the style.
            "width": round(int(width) / FIGURE, 4),
            "dx": dx,
            "dy": dy,
        }
        (folder / "acc.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{item_id} ({label}): {image.width}×{image.height}")


if __name__ == "__main__":
    main()
