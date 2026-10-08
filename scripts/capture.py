"""Scrolling screenshot capture of a virtualized list view in any Windows GUI app.

All coordinates are PHYSICAL pixels (process is per-monitor DPI aware).
Auto-stops when the list reaches its end (dhash stops changing + nudge retry).

Usage:
  python capture.py --hwnd 1051982 --rel-x 0.93 --rel-y 0.5 --scroll -2 \
                    --max 800 --delay 0.7 --out data/raw/list

Rule of thumb for --scroll: step must be SMALLER than half a row height so every
item appears in >=3 frames (redundancy is what fixes OCR misses).
  - linear list, row height ~100px  -> -2 (240px)
  - grid cards, row period ~352px   -> -1 (120px)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import win32util as wu  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--rel-x", type=float, default=0.5, help="hover x as fraction of window width")
    ap.add_argument("--rel-y", type=float, default=0.5, help="hover y as fraction of window height")
    ap.add_argument("--scroll", type=int, default=-2, help="wheel clicks per step (neg=down)")
    ap.add_argument("--max", type=int, default=500)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--dup-stop", type=int, default=4, help="consecutive identical frames to call it stuck")
    ap.add_argument("--stuck-retries", type=int, default=3)
    ap.add_argument("--no-rewind", action="store_true", help="skip scrolling back to top first")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    wu.set_dpi_aware()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    wu.focus(args.hwnd)
    time.sleep(0.8)
    l, t, r, b = wu._rect(args.hwnd)
    cx = int(l + (r - l) * args.rel_x)
    cy = int(t + (b - t) * args.rel_y)
    print(f"# physical rect=({l},{t},{r},{b}) wheel_at=({cx},{cy})", flush=True)

    if not args.no_rewind:
        for _ in range(40):  # scroll back to top
            wu.wheel_at(cx, cy, 1)
            time.sleep(0.04)
        time.sleep(1.0)

    prev = None
    dup = 0
    saved = 0
    stuck = 0

    for step in range(args.max):
        img = wu.screenshot_window(args.hwnd)
        h = wu.dhash(img)
        if prev is not None and wu.hamming(h, prev) <= 2:
            dup += 1
        else:
            dup = 0
        prev = h

        img.save(out / f"shot_{step:04d}.png")
        saved += 1
        print(f"[{step}] dup={dup}", flush=True)

        if dup >= args.dup_stop:
            if stuck >= args.stuck_retries:
                print("# end of list")
                break
            stuck += 1
            print(f"# stuck {stuck}: nudge", flush=True)
            for _ in range(2):
                wu.wheel_at(cx, cy, -1)
                time.sleep(0.4)
            time.sleep(3.0)
            prev = None
            continue

        wu.wheel_at(cx, cy, args.scroll)
        time.sleep(args.delay)

    print(f"# saved {saved} frames -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
