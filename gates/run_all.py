#!/usr/bin/env python3
"""Run every clip against the answer key and write RESULTS.md. All API calls are cached, so re-runs are free.

    python gates/run_all.py                       # gates/clips/* vs gates/answer_key.csv
    python gates/run_all.py --clips DIR --key K.csv --orders DIR --kits K.json --out R.md

Clips are named by key row (G1.mp4); a second take adds _b (G1_b.mp4) and uses the same row.
A row recorded in two parts (G3a + G3b) is joined into G3.mp4 first.
Kits come from gates/kits.json; orders missing there are read from gates/orders/<prefix>* screenshots.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

import compare
import cuts
import orders
import rules
import track
from cache import BUDGET, spent

HERE = Path(__file__).resolve().parent
CONTAINER, LABEL = "box", "shipping label"  # "label" also matched product stickers in the dry run
VIDEO = (".mp4", ".mov")


def join_parts(clips, rows):
    """G3a + G3b -> G3.mp4 (re-encoded, no audio): a two-take recording is one clip with a cut."""
    for r in rows:
        parts = [next((clips / f"{r}{p}{e}" for e in VIDEO if (clips / f"{r}{p}{e}").exists()), None) for p in "ab"]
        out = clips / f"{r}.mp4"
        if all(parts) and not out.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(parts[0]), "-i", str(parts[1]), "-filter_complex",
                            "[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0", "-c:v", "libx264", "-crf", "20",
                            "-pix_fmt", "yuv420p", str(out)], check=True)
            print(f"joined {parts[0].name} + {parts[1].name} -> {out.name}")


def facts_for(clip, row, kit, shots, fps):
    items = [i for i in kit["items"] if i["checked"]]
    nouns = list(dict.fromkeys(i["sam_noun"] for i in items))
    t = track.track(clip, [CONTAINER, LABEL] + nouns, fps)
    t["box_frames"] = sorted({r[0] for r in t["tracks"][CONTAINER]})
    c = cuts.cuts(t, row["mode"], CONTAINER, nouns, skip=(LABEL,))
    sp = compare.compare(t, clip, row["mode"], kit, shots, LABEL, save_dir=HERE / "out" / Path(clip).stem)
    seen = lambda k: len({r[0] for r in t["tracks"].get(k, [])}) / fps  # noqa: E731
    at_once = lambda k: max(Counter(r[0] for r in t["tracks"].get(k, [])).values(), default=0)  # noqa: E731
    by_name = {i["name"]: i for i in sp["items"]}
    none = {"same_kind": False, "matches_listing": False, "screen_state": "none", "power_test": "not_tried"}
    per_item = {}
    for idx, i in enumerate(items):
        s = by_name.get(i["name"]) or (sp["items"][idx] if idx < len(sp["items"]) else none)
        per_item[i["name"]] = {"seen_s": seen(i["sam_noun"]), "count": at_once(i["sam_noun"]),
                               **{k: s.get(k, none.get(k)) for k in ("same_kind", "matches_listing", "screen_state",
                                                                     "power_test", "item_type", "mismatches")}}
    return {"duration": round(track.probe(clip)["duration"], 1), "size_mb": round(os.path.getsize(clip) / 1e6, 1),
            **c, "label_text": sp["label_text"], "label_readable": sp["label_readable"],
            "seal_visible": sp["seal_visible"], "sides_seen": sp["box_sides_seen"], "items": per_item,
            "spark": sp, "tracking": track.report(t, CONTAINER)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default=HERE / "clips")
    ap.add_argument("--key", default=HERE / "answer_key.csv")
    ap.add_argument("--orders", default=HERE / "orders", help="order screenshots, <prefix>*.png|jpg")
    ap.add_argument("--kits", default=HERE / "kits.json")
    ap.add_argument("--out", default=HERE / "RESULTS.md")
    ap.add_argument("--fps", type=int, default=track.FPS)
    a = ap.parse_args()

    key = {r["file"]: r for r in csv.DictReader(open(a.key, newline="", encoding="utf-8"))}
    clips_dir = Path(a.clips)
    join_parts(clips_dir, key)
    clips = sorted(p for p in clips_dir.iterdir() if p.suffix.lower() in VIDEO)
    runs = [(c, key.get(c.stem[:-2] if c.stem.endswith("_b") else c.stem)) for c in clips]
    for c, r in runs:
        if not r and not re.fullmatch(r".+[ab]", c.stem):
            print(f"skip {c.name}: no row for it in {a.key}")
    runs = [(c, r) for c, r in runs if r]
    kits = orders.kits(list(dict.fromkeys(r["order"] for _, r in runs)), a.orders, a.kits)

    spend0, rows, done = spent(), [], set()
    for clip, row in runs:
        done.add(row["file"])
        kit = kits[row["order"]]
        want = {i["name"]: i["qty"] for i in kit["items"] if i["checked"]}
        print(f"{clip.name}: {row['mode']} {row['order']} -> expect {row['expected']}", flush=True)
        try:
            f = facts_for(clip, row, kit, orders.shots(row["order"], a.orders), a.fps)
            got, reasons = rules.verdict(row["mode"], want, kit["order_id_last6"], f)
            (HERE / "out").mkdir(exist_ok=True)
            (HERE / "out" / f"{clip.stem}.json").write_text(json.dumps({**f, "verdict": got, "reasons": reasons}, indent=1))
        except Exception as e:  # one bad clip must not stop the batch; budget stops still do (SystemExit)
            traceback.print_exc()
            got, reasons = f"ERROR {type(e).__name__}: {str(e)[:80]}", []
        exp = re.sub(r"\s*\(.*?\)", "", row["expected"]).strip()  # "(spec)", "(tiny item)" are notes
        rows.append({"clip": clip.stem, "mode": row["mode"], "order": row["order"], "expected": row["expected"],
                     "got": got, "exact": got == exp, "level": got.split()[0] == exp.split()[0],
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
