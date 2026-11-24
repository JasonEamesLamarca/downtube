"""Render the DownTube icon set from tools/brand/icon.svg into frontend/public/.

Needs a Chromium binary (default /usr/bin/chromium, override with CHROMIUM=...). Stdlib only:
PNGs are rendered with headless Chromium; favicon.ico packs the 16/32/48 PNGs.

    python3 tools/brand/make_icons.py
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "tools" / "brand" / "icon.svg"
OUT = ROOT / "frontend" / "public"
CHROMIUM = os.environ.get("CHROMIUM", "/usr/bin/chromium")

PNGS = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
}


def maskable_svg(svg: str) -> str:
    """Full-bleed square background with the letters inside the 80 % safe zone."""
    body = re.sub(r'<rect [^>]*/>', "", svg.split(">", 1)[1].rsplit("</svg>", 1)[0])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 64 64">'
        '<rect width="64" height="64" fill="#e11d48"/>'
        f'<g transform="translate(32 32) scale(0.8) translate(-32 -32)">{body}</g></svg>'
    )


def render(svg: str, size: int, dest: Path, tmp: Path) -> None:
    page = tmp / f"r{size}.html"
    page.write_text(
        "<!doctype html><html><head><style>html,body{margin:0;background:transparent}"
        f"img{{display:block;width:{size}px;height:{size}px}}</style></head><body>"
        f'<img src="icon.svg"></body></html>'
    )
    (tmp / "icon.svg").write_text(svg)
    shot = tmp / f"s{size}.png"
    subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         "--default-background-color=00000000", f"--window-size={size},{size}",
         f"--screenshot={shot}", page.as_uri()],
        check=True, capture_output=True, timeout=60,
    )
    w, h = struct.unpack(">II", shot.read_bytes()[16:24])
    if (w, h) != (size, size):
        raise SystemExit(f"{dest.name}: Chromium produced {w}x{h}, expected {size}x{size}")
    shutil.move(shot, dest)


def write_ico(dest: Path, pngs: list[Path]) -> None:
    blobs = [p.read_bytes() for p in pngs]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for blob in blobs:
        w, h = struct.unpack(">II", blob[16:24])
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    dest.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    svg = SRC.read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "favicon.svg").write_text(svg)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name, size in PNGS.items():
            render(svg, size, OUT / name, tmp)
        render(maskable_svg(svg), 512, OUT / "icon-maskable-512.png", tmp)
    small = [OUT / f"favicon-{n}.png" for n in (16, 32, 48)]
    write_ico(OUT / "favicon.ico", small)
    for p in small[::2]:  # 16 and 48 only live inside favicon.ico
        p.unlink()
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
