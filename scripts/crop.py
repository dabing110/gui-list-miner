"""Crop every capture frame down to just the list column (drops sidebar/nav/chrome).

Usage:
  python crop.py --src data/raw/list --dst data/cropped --box 1060,140,1938,1048

--box is left,top,right,bottom in PHYSICAL pixels of the full-window screenshot.
Crop BEFORE running OCR: the OCR output coordinates are relative to whatever image
you feed it, so parsing code must match the cropped coordinate space.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--box", required=True, help="left,top,right,bottom")
    ap.add_argument("--pattern", default="*.png")
    args = ap.parse_args()

    box = tuple(int(v) for v in args.box.split(","))
    assert len(box) == 4, "--box needs 4 values"
    src, dst = Path(args.src), Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)

    n = 0
    for i, p in enumerate(sorted(src.glob(args.pattern))):
        try:
            Image.open(p).convert("RGB").crop(box).save(dst / p.name)
            n += 1
        except Exception as e:  # noqa: BLE001
            print("skip", p.name, e)
        if i % 200 == 0:
            print("...", i, flush=True)
    print(f"# cropped {n} frames -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
