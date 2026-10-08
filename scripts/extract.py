# -*- coding: utf-8 -*-
"""Turn word-level WinRT OCR dumps into structured records.

Two modes:

  linear   - one column of [title / subtitle] rows (WeChat likes, reading history)
             split items by vertical GAP, classify line by word HEIGHT
  grid     - N-column cards with cover images (WeChat Channels, photo grids)
             split items by GRID PHASE (y mod PERIOD == constant) which filters
             out cover captions / duration badges / like counts whose phase is random

Usage:
  python extract.py --mode linear --src data/cropped --out out/items.csv
  python extract.py --mode grid   --src data/cropped --out out/items.csv \
                    --cols 187,410,632 --period 352

Tuning: run on 5 test frames first, then compare against a zoomed crop you read
with your eyes. Most "parsing bugs" are actually one of:
  - crop cut off the line        -> compare abs x against crop left edge
  - OCR missed a leading char    -> x0 shifts right ~17px, widen X_ALIGN
  - your own filter killed it    -> print the raw rows before filtering
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# ---------------- shared helpers ----------------

FP_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)  # spaces between CJK
    s = re.sub(r"\s+([,。:;!?）】》])", r"\1", s)
    s = re.sub(r"([（【《])\s+", r"\1", s)
    return s.strip()


def fp(s: str) -> str:
    return FP_RE.sub("", unicodedata.normalize("NFKC", s or "")).lower()


WORD_RE = re.compile(r"^L=(\d+) X=(-?\d+) Y=(-?\d+) H=(\d+)\t(.*)$")


def read_words(txt_path: Path):
    """[(line_idx, x, y, h, text)] from one OCR dump.

    OcrLine has no BoundingRect -> only OcrWord carries coordinates. The leading
    L=<n> is the OCR line index: group by it, do NOT bucket y by integer division
    (y//8 splits one visual row across two buckets whenever it straddles a
    multiple of 8 - that silently shreds titles into fragments).
    """
    out = []
    for raw in txt_path.read_text(encoding="utf-8-sig").splitlines():
        m = WORD_RE.match(raw)
        if not m:
            continue
        li, x, y, h, t = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
        if t.strip():
            out.append((li, x, y, h, t))
    return out


def cluster_rows(words, y_tol=8):
    """Cluster (li,x,y,h,text) tuples into visual rows -> [(x0, y_med, h_med, text)] by y."""
    if not words:
        return []
    ws = sorted(words, key=lambda t: (t[2], t[1]))  # y, then x
    lines, cur = [], [ws[0]]
    for w in ws[1:]:
        if abs(w[2] - cur[-1][2]) <= y_tol:
            cur.append(w)
        else:
            lines.append(cur)
            cur = [w]
    lines.append(cur)
    rows = []
    for ln in lines:
        ln2 = sorted(ln, key=lambda t: t[1])
        x0 = ln2[0][1]
        y = sorted(t[2] for t in ln)[len(ln) // 2]
        hs = sorted(t[3] for t in ln)
        rows.append((x0, y, hs[len(hs) // 2], clean_text("".join(t[4] for t in ln2))))
    rows.sort(key=lambda r: r[1])
    return rows


# ---------------- mode: linear ----------------

def extract_linear(txt_path: Path, gap: int, title_h: int, min_title: int,
                   x_min: int, date_re: re.Pattern, skip_text: set):
    words = [w for w in read_words(txt_path) if w[1] >= x_min]
    by_line: dict[int, list] = defaultdict(list)
    for li, x, y, h, t in words:
        by_line[li].append((x, y, h, t))
    lines = []
    for ws in by_line.values():
        y = min(w[1] for w in ws)
        h = statistics.median(w[2] for w in ws)
        lines.append({"y": y, "h": h, "text": clean_text("".join(w[3] for w in sorted(ws)))})
    lines.sort(key=lambda l: l["y"])

    items, cur, last_y, date_hint = [], None, None, ""
    for ln in lines:
        t = ln["text"]
        if not t or t in skip_text:
            continue
        if date_re.match(t):
            date_hint, last_y = t, ln["y"]
            continue
        is_title = ln["h"] >= title_h
        new_item = (cur is None or last_y is None or ln["y"] - last_y > gap
                    or (is_title and cur.get("_src")))
        if new_item:
            if cur and cur["title"]:
                items.append(cur)
            cur = {"title": t if is_title else "", "source": "", "date": date_hint, "_src": False}
            if not is_title:
                cur["source"], cur["_src"] = t, True
        else:
            if is_title:
                cur["title"] += t
            elif cur["title"] and not cur["source"]:
                cur["source"] = t
        cur["_src"] = not is_title
        last_y = ln["y"]
    if cur and cur["title"]:
        items.append(cur)
    return [it for it in items if len(fp(it["title"])) >= min_title]


# ---------------- mode: grid ----------------

DUR_RE = re.compile(r"\d{1,2}[:：]\d{2}")
BRACKET_RE = re.compile(r"^[〔\[【〖（(]")
NOISE_RE = re.compile(r"^[\d\s:：．.,，%％万+\-|丨〇零一二三四五六七八九十]*$")


def author_like(t: str, max_len: int) -> bool:
    if not t or len(t) > max_len:
        return False
    if NOISE_RE.fullmatch(t) or not FP_RE.sub("", t):
        return False
    if DUR_RE.search(t) or BRACKET_RE.match(t) or "\u51f8" in t:
        return False
    return sum(ch.isdigit() for ch in t) < 2


def grid_phase(ys, period, tol, author_off):
    best = (0, -1)
    for base in ys:
        score = 0
        for y in ys:
            d = (y - base) % period
            if d <= tol or period - d <= tol:
                score += 1
            elif abs(d - author_off) <= tol or abs(d - (period - author_off)) <= tol:
                score += 1
        if score > best[1]:
            best = (base, score)
    return best[0]


def on_grid(y, phi, period, tol, author_off):
    d = (y - phi) % period
    return (d <= tol or period - d <= tol
            or abs(d - author_off) <= tol or abs(d - (period - author_off)) <= tol)


def extract_grid(txt_path: Path, cols, col_x0, period, tol, title_h, author_h,
                 dy_min, dy_max, x_align, author_off, max_author_len):
    words = read_words(txt_path)
    by_col: dict[int, list] = defaultdict(list)
    for li, x, y, h, t in words:
        for ci, (a, b) in enumerate(cols):
            if a <= x < b:
                by_col[ci].append((li, x, y, h, t))
                break

    out = []
    for ci, ws in by_col.items():
        rows = cluster_rows(ws)
        if not rows:
            continue
        phi = grid_phase([r[1] for r in rows], period, tol, author_off)
        on = sorted([r for r in rows if on_grid(r[1], phi, period, tol, author_off)],
                    key=lambda r: r[1])
        cx = col_x0[ci]
        for i in range(1, len(on)):
            x0, y, h, text = on[i]
            if h > author_h or not author_like(text, max_author_len):
                continue
            px0, py, ph, ptext = on[i - 1]
            if (ph >= title_h and dy_min <= y - py <= dy_max
                    and abs(px0 - cx) <= x_align and abs(x0 - cx) <= x_align):
                out.append({"title": ptext, "source": text, "date": "", "y": y})
    return out


# ---------------- driver ----------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["linear", "grid"], required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pattern", default="*.txt")

    # linear
    ap.add_argument("--gap", type=int, default=42, help="linear: px gap that starts a new item")
    ap.add_argument("--title-h", type=float, default=15.0, help="linear/grid: title word height >= this")
    ap.add_argument("--min-title", type=int, default=6, help="linear: min fingerprint length")
    ap.add_argument("--x-min", type=int, default=10, help="linear: drop words left of this (edge noise)")
    ap.add_argument("--date-re", default=r"^(今天|昨天|前天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日)$")
    ap.add_argument("--skip", default="", help="linear: comma-separated UI texts to drop")

    # grid
    ap.add_argument("--cols", default="", help="grid: comma-separated column x ranges a1,b1,a2,b2...")
    ap.add_argument("--col-x0", default="", help="grid: comma-separated column title x origins")
    ap.add_argument("--period", type=int, default=352, help="grid: row pitch in px")
    ap.add_argument("--tol", type=int, default=9, help="grid: phase tolerance")
    ap.add_argument("--author-h", type=float, default=14.5, help="grid: author word height <= this")
    ap.add_argument("--dy-min", type=int, default=22, help="grid: title->author min dy")
    ap.add_argument("--dy-max", type=int, default=36, help="grid: title->author max dy")
    ap.add_argument("--author-off", type=int, default=28, help="grid: author y offset within period")
    ap.add_argument("--x-align", type=int, default=22, help="grid: allowed x0 drift from column origin")
    ap.add_argument("--max-author-len", type=int, default=16)
    args = ap.parse_args()

    src = Path(args.src)
    txts = sorted(src.glob(args.pattern))
    rows = []
    for tp in txts:
        frame = tp.name.replace(".png.txt", "").replace(".txt", "")
        if args.mode == "linear":
            items = extract_linear(tp, args.gap, args.title_h, args.min_title, args.x_min,
                                   re.compile(args.date_re),
                                   {s for s in args.skip.split(",") if s})
        else:
            nums = [int(v) for v in args.cols.split(",")]
            cols = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
            col_x0 = {i: int(v) for i, v in enumerate(args.col_x0.split(","))}
            items = extract_grid(tp, cols, col_x0, args.period, args.tol, args.title_h,
                                 args.author_h, args.dy_min, args.dy_max, args.x_align,
                                 args.author_off, args.max_author_len)
        for it in items:
            rows.append({"title": it["title"], "source": it["source"],
                         "date": it.get("date", ""), "frame": frame})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["idx", "title", "source", "date", "frame"])
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({"idx": i, **r})
    print(f"# frames {len(txts)} -> raw items {len(rows)} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
