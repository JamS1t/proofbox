#!/usr/bin/env python3
"""Run every clip against the answer key and write RESULTS.md. All API calls are cached, so re-runs are free.

    python gates/run_all.py                       # gates/clips/*.mp4 vs gates/answer_key.csv
    python gates/run_all.py --clips DIR --key K.csv --listings DIR --out R.md

Clip files are named by key row (G1.mp4, U2.mp4); a second take adds _b (U2_b.mp4) and uses the same row.
Listing screenshots: gates/listings/<item name>.png|jpg (e.g. "router.png", "shirt A.png").
"""
import argparse
import csv
import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

import compare
import cuts
import rules
import track
from cache import BUDGET, spent

HERE = Path(__file__).resolve().parent
CONTAINER, LABEL = "box", "shipping label"  # "label" also matched product stickers in the dry run
# ponytail: SAM nouns per order item, guessed. Tune after the first real clips (see gates/out/<clip>/ crops).
CONCEPT = {"multitester": "multimeter", "probes": "probe", "orange pi": "circuit board",
           "usb drive": "usb drive", "shirt A": "shirt", "shirt B": "shirt"}


def concept_of(name):
    return CONCEPT.get(name, name)


def facts_for(clip, row, listings, fps):
    want = rules.parse_order(row["order"])
    items = list(dict.fromkeys(concept_of(n) for n in want))
    t = track.track(clip, [CONTAINER, LABEL] + items, fps)
    t["box_frames"] = sorted({r[0] for r in t["tracks"][CONTAINER]})
    c = cuts.cuts(t, CONTAINER, skip=(LABEL,))
    sp = compare.compare(t, row["mode"], row["order"], want, concept_of, listings, LABEL,
                         save_dir=HERE / "out" / Path(clip).stem)
    seen = lambda k: len({r[0] for r in t["tracks"].get(k, [])}) / fps  # noqa: E731
    at_once = lambda k: max(Counter(r[0] for r in t["tracks"].get(k, [])).values(), default=0)  # noqa: E731
    by_name = {i["name"]: i for i in sp["items"]}
    none = {"same_kind": False, "matches_listing": False, "screen_state": "none", "power_test": "not_tried"}
    per_item = {}
    for idx, n in enumerate(want):
        s = by_name.get(n) or (sp["items"][idx] if idx < len(sp["items"]) else none)
        per_item[n] = {"seen_s": seen(concept_of(n)), "count": at_once(concept_of(n)),
                       **{k: s.get(k, none.get(k)) for k in
                          ("same_kind", "matches_listing", "screen_state", "power_test", "item_type", "mismatches")}}
    return {"duration": round(track.probe(clip)["duration"], 1), "size_mb": round(os.path.getsize(clip) / 1e6, 1),
            **c, "label_seen_s": seen(LABEL), "label_readable": sp["label_readable"],
            "seal_visible": sp["seal_visible"], "sides_seen": sp["box_sides_seen"], "items": per_item,
            "spark": sp, "tracking": track.report(t, CONTAINER)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default=HERE / "clips")
    ap.add_argument("--key", default=HERE / "answer_key.csv")
    ap.add_argument("--listings", default=HERE / "listings")
    ap.add_argument("--out", default=HERE / "RESULTS.md")
    ap.add_argument("--fps", type=int, default=track.FPS)
    a = ap.parse_args()

    key = {r["file"]: r for r in csv.DictReader(open(a.key, newline="", encoding="utf-8"))}
    clips = sorted(p for p in Path(a.clips).iterdir() if p.suffix.lower() in (".mp4", ".mov"))
    spend0, rows, done = spent(), [], set()
    for clip in clips:
        base = clip.stem[:-2] if clip.stem.endswith("_b") else clip.stem
        row = key.get(base)
        if not row:
            print(f"skip {clip.name}: no row '{base}' in {a.key}")
            continue
        done.add(base)
        print(f"{clip.name}: {row['mode']} {row['order']} -> expect {row['expected']}", flush=True)
        try:
            f = facts_for(clip, row, a.listings, a.fps)
            got, reasons = rules.verdict(row["mode"], row["order"], f)
            (HERE / "out").mkdir(exist_ok=True)
            (HERE / "out" / f"{clip.stem}.json").write_text(json.dumps({**f, "verdict": got, "reasons": reasons}, indent=1))
        except Exception as e:  # one bad clip must not stop the batch; budget stops still do (SystemExit)
            traceback.print_exc()
            got, reasons = f"ERROR {type(e).__name__}: {str(e)[:80]}", []
        exp = row["expected"]
        rows.append({"clip": clip.stem, "mode": row["mode"], "order": row["order"], "expected": exp, "got": got,
                     "exact": got == exp, "level": got.split()[0] == exp.split()[0],
                     "found": got == exp or exp in reasons, "reasons": "; ".join(reasons) or "-"})
        print(f"  got {got}  ({'; '.join(reasons) or 'no issues'})", flush=True)

    n = len(rows)
    yes = lambda k: sum(r[k] for r in rows)  # noqa: E731
    md = ["# ProofBox gate results", "",
          f"Run {time.strftime('%Y-%m-%d %H:%M')}, {n} clip(s), fps {a.fps}. "
          f"API spend this run ${spent() - spend0:.3f}, ledger total ${spent():.3f} (budget ${BUDGET:.2f}).", "",
          "| Clip | Mode | Order | Expected | Got | Exact | Level | Reason found | All reasons |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['clip']} | {r['mode']} | {r['order']} | {r['expected']} | {r['got']} | "
           f"{'Y' if r['exact'] else 'N'} | {'Y' if r['level'] else 'N'} | {'Y' if r['found'] else 'N'} | {r['reasons']} |"
           for r in rows]
    md += ["", f"**Totals:** exact verdict {yes('exact')}/{n}, verdict level (PASS/FAIL/WARN/FLAG) {yes('level')}/{n}, "
               f"expected reason found {yes('found')}/{n}." if n else "**Totals:** no clips run.",
           "", "Not filmed yet: " + (", ".join(k for k in key if k not in done) or "none"),
           "", "Details per clip: gates/out/<clip>.json (facts, Spark JSON, tracking report) and gates/out/<clip>/ (crops)."]
    Path(a.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
