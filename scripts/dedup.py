# -*- coding: utf-8 -*-
"""Multi-stage dedupe for OCR-derived lists.

OCR noise means one real item shows up many times with slightly different text.
Stages (each fixes a distinct failure mode):

  1 prefix merge     fp()[:30]                       same item truncated to different lengths
  2 fragment drop    short fp is substring of a kept one   partial lines leaking in as items
  3 jaccard >= 0.9   char-set overlap                 single-char OCR swap
  4 SequenceMatcher  title >= 0.78 AND source >= 0.6  two-char OCR swap (e.g. 究竟 -> 宄見, ratio .82)
  5 cross dedupe     vs another CSV                   merging multiple采集 sources

IMPORTANT: compare the RAW strings, not the fp() keys. fp() strips punctuation and
case, which distorts SequenceMatcher ratios in both directions.

Usage:
  python dedup.py --src out/items.csv --out out/clean.csv
  python dedup.py --src out/reads.csv --against out/likes.csv --out out/reads_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
import unicodedata
from pathlib import Path

FP_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def fp(s: str) -> str:
    return FP_RE.sub("", unicodedata.normalize("NFKC", s or "")).lower()


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def load(path: Path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _fuzzy(kept, p, args):
    """Stages 3+4: substring (both directions), char-set jaccard, sequence similarity."""
    seen: list[tuple[str, str, str]] = []  # (fp, title, source)
    final: list[dict] = []
    for e in sorted(kept, key=lambda x: x.get("frame", "")):
        k, title, src = fp(e["title"]), e["title"], e.get("source", "")
        if not k:
            continue
        dup = False
        for sk, stitle, ssrc in seen:
            short, long_ = (k, sk) if len(k) <= len(sk) else (sk, k)
            if (p.get("substring", True) and short
                    and (long_.startswith(short) or (len(short) >= args.frag_min and short in long_))):
                dup = True
                break
            if jaccard(k, sk) >= p["jaccard"]:
                dup = True
                break
            if len(k) >= p["sim_min"] and sim(title, stitle) >= p["sim_title"] and sim(src, ssrc) >= p["sim_src"]:
                dup = True
                break
        if not dup:
            seen.append((k, title, src))
            final.append(e)
    return final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--against", default="", help="optional CSV to cross-dedupe against")
    ap.add_argument("--against-col", default="title")
    ap.add_argument("--preset", choices=["grid", "linear", "off"], default="grid",
                    help="grid=aggressive fuzzy (noisy OCR); linear=conservative (clean text, "
                         "series titles share wording); off=prefix+fragment only")
    ap.add_argument("--prefix", type=int, default=30)
    ap.add_argument("--frag-min", type=int, default=12, help="min fp length for substring drop")
    ap.add_argument("--jaccard", type=float, default=None)
    ap.add_argument("--sim-title", type=float, default=None)
    ap.add_argument("--sim-src", type=float, default=None)
    ap.add_argument("--sim-min", type=int, default=None, help="min title fp length to run similarity")
    args = ap.parse_args()

    presets = {
        # noisy OCR: same item reappears with 1-2 garbled chars -> merge aggressively
        "grid": dict(jaccard=0.90, sim_title=0.78, sim_src=0.60, sim_min=6, substring=True),
        # clean-ish text but many series titles share wording -> stay conservative,
        # otherwise distinct articles from one account collapse into one.
        # substring=False is the key: bidirectional substring merging is what
        # over-collapses short CJK titles.
        "linear": dict(jaccard=0.98, sim_title=0.90, sim_src=0.80, sim_min=12, substring=False),
        "off": dict(jaccard=1.10, sim_title=1.10, sim_src=1.10, sim_min=999, substring=True),
    }
    p = presets[args.preset]
    if args.jaccard is not None:
        p["jaccard"] = args.jaccard
    if args.sim_title is not None:
        p["sim_title"] = args.sim_title
    if args.sim_src is not None:
        p["sim_src"] = args.sim_src
    if args.sim_min is not None:
        p["sim_min"] = args.sim_min
    print(f"# preset={args.preset} {p}")

    rows = load(Path(args.src))

    # 1 prefix merge (keep the longest surface form)
    by_fp: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        k = fp(r.get("title", ""))[: args.prefix]
        if not k:
            continue
        if k in by_fp:
            cur = by_fp[k]
            if len(r["title"]) > len(cur["title"]):
                cur["title"] = r["title"]
            if not cur.get("source") and r.get("source"):
                cur["source"] = r["source"]
            if not cur.get("date") and r.get("date"):
                cur["date"] = r["date"]
        else:
            by_fp[k] = dict(r)
            order.append(k)
    entries = [by_fp[k] for k in order]
    print(f"# prefix-merge {len(rows)} -> {len(entries)}")

    # 2 fragment drop
    entries.sort(key=lambda e: len(e["title"]), reverse=True)
    kept: list[dict] = []
    kept_fps: list[str] = []
    for e in entries:
        f = fp(e["title"])
        if len(f) >= args.frag_min and any(f in kf for kf in kept_fps):
            continue
        kept.append(e)
        kept_fps.append(f)
    print(f"# fragment-drop -> {len(kept)}")

    # 3+4 fuzzy merge  (baseline for tuning: "off" stops here at ~pure prefix+fragment)
    if args.preset == "off":
        print("# fuzzy-merge skipped (preset=off)")
        final = sorted(kept, key=lambda x: x.get("frame", ""))
    else:
        final = _fuzzy(kept, p, args)
    print(f"# fuzzy-merge -> {len(final)}")

    # 5 cross dedupe
    also = ""
    n_dup = 0
    if args.against:
        others = [fp(r.get(args.against_col, "")) for r in load(Path(args.against))]
        oset30 = {o[: args.prefix] for o in others}

        def cross(f: str) -> bool:
            if f[: args.prefix] in oset30:
                return True
            if len(f) >= args.frag_min:
                for o in others:
                    if len(o) >= args.frag_min and (f in o or o in f):
                        return True
            return False

        for e in final:
            hit = cross(fp(e["title"]))
            e["also_in_other"] = "Y" if hit else "N"
            n_dup += hit
        also = "also_in_other"
        print(f"# cross-dup vs {args.against}: {n_dup} / {len(final)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["idx", "title", "source", "date", "frame"] + ([also] if also else [])
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for i, e in enumerate(final, 1):
            e["idx"] = i
            w.writerow(e)
    print(f"# -> {out} ({len(final)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
