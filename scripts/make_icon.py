#!/usr/bin/env python3
"""Generate the application icon in every format the desktop shortcuts need.

Produces assets/icon.png (1024), assets/icon.ico (Windows) and, on macOS,
assets/icon.icns. The generated files are committed, so this only needs
re-running if you want to change the artwork.

    python scripts/make_icon.py
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 1024
SS = 2  # supersampling factor for smooth curves

# Deep blue ground with the chart palette's hues on top — the icon is a small
# attention map, which is what the app is.
BG_TOP = (18, 58, 107)
BG_BOTTOM = (8, 30, 60)
EDGE = (120, 170, 230, 110)

NODES = [
    # (x, y, radius, colour) in a 0..1 coordinate space
    (0.30, 0.32, 0.085, (57, 135, 229)),    # blue
    (0.52, 0.22, 0.052, (110, 180, 245)),
    (0.72, 0.36, 0.070, (235, 104, 52)),    # orange
    (0.38, 0.56, 0.062, (27, 175, 122)),    # aqua
    (0.60, 0.60, 0.105, (255, 255, 255)),   # the hub
    (0.80, 0.66, 0.048, (237, 161, 0)),     # yellow
    (0.28, 0.76, 0.055, (232, 123, 164)),   # magenta
    (0.55, 0.82, 0.042, (144, 133, 233)),   # violet
]
EDGES = [(0, 1), (1, 2), (0, 3), (3, 4), (2, 4), (4, 5), (3, 6), (4, 7), (6, 7), (1, 4)]


def _gradient(size: int) -> Image.Image:
    """Vertical gradient ground."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), tuple(
            round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return grad.resize((size, size), Image.NEAREST)


def build(size: int = SIZE) -> Image.Image:
    canvas = size * SS
    # macOS draws icons on a squircle grid; leave a margin so the art is not
    # flush to the edge and use the platform's ~22% corner radius.
    margin = round(canvas * 0.055)
    radius = round(canvas * 0.2237)

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [margin, margin, canvas - margin, canvas - margin], radius=radius, fill=255)

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    image.paste(_gradient(canvas).convert("RGBA"), (0, 0), mask)

    overlay = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def point(i: int) -> tuple[float, float]:
        x, y, _, _ = NODES[i]
        return x * canvas, y * canvas

    width = max(1, round(canvas * 0.011))
    for a, b in EDGES:
        draw.line([point(a), point(b)], fill=EDGE, width=width)

    for x, y, r, colour in NODES:
        cx, cy, rr = x * canvas, y * canvas, r * canvas
        # A ring in the ground colour keeps overlapping nodes readable.
        draw.ellipse([cx - rr - width, cy - rr - width, cx + rr + width, cy + rr + width],
                     fill=(10, 36, 71, 255))
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=colour + (255,))

    image = Image.alpha_composite(image, overlay)
    image.putalpha(Image.composite(image.getchannel("A"), Image.new("L", (canvas, canvas), 0), mask))
    return image.resize((size, size), Image.LANCZOS)


def write_icns(master: Image.Image, target: Path) -> bool:
    if sys.platform != "darwin" or not shutil.which("iconutil"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            master.resize((base, base), Image.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
            master.resize((base * 2, base * 2), Image.LANCZOS).save(
                iconset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
                       check=True, capture_output=True)
    return True


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    master = build()

    png = ASSETS / "icon.png"
    master.save(png)
    print(f"wrote {png.relative_to(ROOT)}")

    ico = ASSETS / "icon.ico"
    master.save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"wrote {ico.relative_to(ROOT)}")

    icns = ASSETS / "icon.icns"
    if write_icns(master, icns):
        print(f"wrote {icns.relative_to(ROOT)}")
    else:
        print("skipped icon.icns (needs macOS iconutil)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
