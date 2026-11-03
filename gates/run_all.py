#!/usr/bin/env python3
"""Run clips against the answer key -> gates/RESULTS.md. All API calls are cached, so re-runs are free.

    python gates/run_all.py                          # every clip in gates/clips/
    python gates/run_all.py --only G1,G2,G3          # these rows (and their second takes / demo shots)
    python gates/run_all.py --only G1 --fps 10 --out gates/RESULTS_10fps.md

Clips: gates/clips/<ID>.mp4 (G1, G1_b, DEMO_U2...). G3a + G3b are joined into G3.mp4 first.
Kits (truth + SAM nouns): gates/kits.json from orders.py.
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
import rules
import track
from cache import BUDGET, spent

HERE = Path(__file__).resolve().parent
CONTAINER = "box"
LABEL_NOUNS = ("shipping label", "label", "paper")  # tried in order until one finds the label
VIDEO = (".mp4", ".mov")
# both parts onto one 1080p landscape frame (a portrait part gets side bars), so mixed takes still join
TARGET_MB = 27  # under the 30 MB rule with room for audio and container overhead


def size_args(seconds):
    """H.264 settings that keep a clip under 30 MB, the way marketplace apps compress uploads."""
    kbps = int(min(4500, TARGET_MB * 8000 / max(seconds, 1) - 96))
    return ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k",
            "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p"]


FIT = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"


def base_of(stem):
    m = re.fullmatch(r"(?:DEMO_)?([GUP]\d)(?:_b)?", stem)
    return m.group(1) if m else None


def join_parts(clips, rows):
    """G3a + G3b -> G3.mp4 (re-encoded, no audio): a two-take recording is one clip with a cut."""
    for r in rows:
        parts = [next((clips / f"{r}{p}{e}" for e in VIDEO if (clips / f"{r}{p}{e}").exists()), None) for p in "ab"]
        out = clips / f"{r}.mp4"
        if all(parts) and not out.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(parts[0]), "-i", str(parts[1]), "-filter_complex",
                            f"[0:v]{FIT}[a];[1:v]{FIT}[b];[a][b]concat=n=2:v=1:a=0",
                            *size_args(sum(track.probe(p)["duration"] for p in parts)), str(out)], check=True)
            print(f"joined {parts[0].name} + {parts[1].name} -> {out.name}")


def facts_for(clip, row, kit, fps, out=HERE / "out", progress=lambda stage: None):
    items = [i for i in kit["items"] if i["status"] != "optional"]
    nouns = list(dict.fromkeys(i["noun"] for i in items))
    t = track.track(clip, [CONTAINER] + nouns, fps)
    label = ""
    for n in LABEL_NOUNS:
        rows = track.track(clip, [n], fps)["tracks"][n]
        if len({r[0] for r in rows}) >= 3:
            t["tracks"][n], label = rows, n
            break
    t["box_frames"] = sorted({r[0] for r in t["tracks"][CONTAINER]})
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"{Path(clip).stem}_tracks.json").write_text(json.dumps(t))
    progress("Checking rules")
    c = cuts.cuts(t, clip, [CONTAINER] + nouns, skip=LABEL_NOUNS)
    sp = compare.compare(t, clip, row["mode"], kit, items, label, save_dir=Path(out) / Path(clip).stem)
    seen = lambda k: len({r[0] for r in t["tracks"].get(k, [])}) / fps  # noqa: E731
    at_once = lambda k: max(Counter(r[0] for r in t["tracks"].get(k, [])).values(), default=0)  # noqa: E731
    by_name = {i["name"]: i for i in sp["items"]}
    per_item = {}
    for idx, i in enumerate(items):
        s = by_name.get(i["name"]) or (sp["items"][idx] if idx < len(sp["items"]) else {})
        per_item[i["name"]] = {"seen_s": seen(i["noun"]), "count": at_once(i["noun"]), "noun": i["noun"],
                               "same_kind": s.get("same_kind", False), "spec_check": s.get("spec_check", "not_verifiable"),
                               "spec_note": s.get("spec_note", ""), "printed_text": s.get("printed_text", ""),
                               "screen_state": s.get("screen_state", "none"), "power_test": s.get("power_test", "not_tried"),
                               "item_type": s.get("item_type", "")}
    return {"duration": round(track.probe(clip)["duration"], 1), "size_mb": round(os.path.getsize(clip) / 1e6, 1),
            **c, "label_noun": label, "label_code": sp["label_code"], "label_readable": sp["label_readable"],
            "seal_visible": sp["seal_visible"], "sides_seen": sp["box_sides_seen"], "found_instead": sp["found_instead"],
            "items": per_item, "spark": sp, "tracking": track.report(t, CONTAINER)}


def core(expected):
    return re.sub(r"\s*\(.*?\)", "", expected).strip()  # "(wrong adapter)", "(tiny item)" are notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default=HERE / "clips")
    ap.add_argument("--key", default=HERE / "answer_key.csv")
    ap.add_argument("--kits", default=HERE / "kits.json")
    ap.add_argument("--out", default=HERE / "RESULTS.md")
    ap.add_argument("--fps", type=int, default=track.FPS)
    ap.add_argument("--only", default="", help="comma-separated row IDs, e.g. G1,G2,G3")
    a = ap.parse_args()

    key = {r["file"]: r for r in csv.DictReader(open(a.key, newline="", encoding="utf-8"))}
    kits = json.load(open(a.kits, encoding="utf-8"))
    clips_dir = Path(a.clips)
    join_parts(clips_dir, key)
    only = set(filter(None, a.only.split(",")))
    runs = []
    for c in sorted(p for p in clips_dir.iterdir() if p.suffix.lower() in VIDEO):
        b = base_of(c.stem)
        if b and b in key and (not only or b in only):
            runs.append((c, key[b]))
        elif not b and not re.fullmatch(r"[GUP]\d[ab]", c.stem):
            print(f"skip {c.name}: not a clip ID")

    spend0, rows = spent(), []
    (HERE / "out").mkdir(exist_ok=True)
    for clip, row in runs:
        kit = kits[row["kit"]]
        items = [i for i in kit["items"] if i["status"] != "optional"]
        print(f"{clip.name}: {row['mode']} {row['kit']} -> expect {row['expected']}", flush=True)
        out = HERE / "out" / f"{clip.stem}.json"
        t0 = time.perf_counter()
        try:
            f = facts_for(clip, row, kit, a.fps)
            got, reasons, notes = rules.verdict(row["mode"], items, kit["label"], f)
            secs = round(time.perf_counter() - t0, 1)
            first = json.loads(out.read_text()).get("first_run_s", secs) if out.exists() else secs
            out.write_text(json.dumps({**f, "verdict": got, "reasons": reasons, "notes": notes, "seconds": secs,
                                       "first_run_s": first, "fps": a.fps}, indent=1))
        except Exception as e:  # one bad clip must not stop the batch; budget stops still do (SystemExit)
            traceback.print_exc()
            got, reasons, notes, first = f"ERROR {type(e).__name__}: {str(e)[:80]}", [], [], None
        exp = core(row["expected"])
        rows.append({"clip": clip.stem, "mode": row["mode"], "kit": row["kit"], "expected": row["expected"], "core": exp,
                     "got": got, "exact": got == exp, "level": got.split()[0] == exp.split()[0],
                     "found": got == exp or exp in reasons, "reasons": "; ".join(reasons) or "-",
                     "notes": "; ".join(notes), "time": first})
        print(f"  got {got}  ({'; '.join(reasons) or 'no issues'})  {first}s", flush=True)

    n, yn = len(rows), (lambda b: "Y" if b else "N")
    yes = lambda k: sum(r[k] for r in rows)  # noqa: E731
    reading = next((ln for ln in (HERE / "ORDERS_READ.md").read_text(encoding="utf-8").splitlines()
                    if ln.startswith("**Fields")), "") if (HERE / "ORDERS_READ.md").exists() else ""
    md = ["# ProofBox gate results", "",
          f"Run {time.strftime('%Y-%m-%d %H:%M')}, {n} clip(s), SAM at {a.fps} fps. API spend this run "
          f"${spent() - spend0:.3f}, ledger total ${spent():.3f} (budget ${BUDGET:.2f}).", "",
          f"**Correct verdicts: {yes('exact')}/{n}** (verdict level {yes('level')}/{n}, expected reason among the "
          f"reasons {yes('found')}/{n}).", f"Order reading: {reading.replace('**', '')}", "",
          "| Clip | Mode | Kit | Expected | Got | Exact | Level | Reason found | Time (s, first run) | Notes |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['clip']} | {r['mode']} | {r['kit']} | {r['expected']} | {r['got']} | {yn(r['exact'])} | "
           f"{yn(r['level'])} | {yn(r['found'])} | {r['time']} | {r['notes'] or '-'} |" for r in rows]
    md += ["", "## By expected verdict", "", "| Expected | Correct |", "|---|---|"]
    for e in dict.fromkeys(r["core"] for r in rows):
        g = [r for r in rows if r["core"] == e]
        md.append(f"| {e} | {sum(r['exact'] for r in g)}/{len(g)} |")
    md += ["", "## Misses", ""] + ([f"- **{r['clip']}**: expected {r['expected']}, got {r['got']}. "
                                    f"Reasons: {r['reasons']}. Notes: {r['notes'] or '-'}"
                                    for r in rows if not r["exact"]] or ["None."])
    read = HERE / "orders_read.json"
    if read.exists():
        conf = json.loads(read.read_text(encoding="utf-8"))
        md += ["", "## Listing conflicts Spark found while reading the orders", ""]
        md += [f"- {k}: {c}" for k in dict.fromkeys(r["kit"] for r in rows) for c in conf.get(k, {}).get("listing_conflicts", [])]
    md += ["", "Not run: " + (", ".join(k for k in key if k not in {base_of(r['clip']) for r in rows}) or "none"),
           "", "Details per clip: gates/out/<clip>.json (facts, Spark JSON, tracking report) and gates/out/<clip>/ (crops)."]
    Path(a.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
