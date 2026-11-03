"""ProofBox app pipeline: read the order, track items, check rules, and build the verdict page.
Reuses gates/ (SAM 3.1 + Muse Spark, every API call cached).

    python app/pipeline.py sample G1       # turn a gates run (gates/out/G1.json + its clip) into app/samples/G1/
"""
import base64
import html
import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "gates"), str(ROOT)]
import compare  # noqa: E402
import orders  # noqa: E402
import rules  # noqa: E402
import run_all  # noqa: E402
import track  # noqa: E402
from cache import file_sha  # noqa: E402

STAGES = ["Reading order", "Tracking items", "Checking rules", "Verdict"]
COLORS = [(80, 200, 120), (235, 150, 50), (60, 120, 230), (200, 80, 200), (40, 200, 220), (230, 70, 70)]
PLAIN = {
    "FAIL wrong item": "The parcel doesn't contain the ordered item.",
    "FAIL missing item": "Not everything in the order was shown.",
    "FAIL quantity": "Fewer pieces were shown than the order includes.",
    "FAIL mismatch": "Text printed on an item contradicts the order's specs.",
    "FAIL cut": "The recording has a cut, so it isn't one continuous take.",
    "FAIL rule length": "The video is longer than the marketplace's 60-second limit.",
    "FAIL rule size": "The file is bigger than the marketplace's 30 MB limit.",
    "FAIL label mismatch": "The shipping label belongs to a different order.",
    "FAIL rule label": "The shipping label was never shown readably.",
    "FAIL rule seal": "The package was never shown sealed.",
    "FAIL rule sides": "The box's sides weren't shown before opening.",
    "FLAG power test inconclusive": "The device's screen stayed blank; check whether batteries are included before claiming a defect.",
    "WARN continuity": "The box or an item left the camera's view for 4 seconds or more.",
    "PASS": "Every ordered item, the label and the marketplace rules check out.",
    "PASS tested working": "Every ordered item, the label and the rules check out, and the device was shown working.",
}
CHECKS = [("Video is 60 s or shorter", ("rule length",), None), ("File is 30 MB or smaller", ("rule size",), None),
          ("One continuous take, no cuts", ("cut",), None),
          ("Shipping label shown and matches the order", ("label mismatch", "rule label"), None),
          ("Every ordered item shown", ("wrong item", "missing item"), None), ("Right quantity", ("quantity",), None),
          ("Printed specs match the order", ("mismatch",), None),
          ("All box sides shown before opening", ("rule sides",), "buyer"),
          ("Package sealed on camera", ("rule seal",), "seller"),
          ("Box and items stay in view", ("continuity",), None)]


def read_order(files=(), text=""):
    """Order screenshots and/or typed order -> kit (items with SAM nouns, label = last 6 of the Order ID)."""
    prompt = orders.READ_PROMPT + (f"\n\nThe order as typed by the user:\n{text}" if text else "")
    r = compare.spark(orders.content(prompt, list(files)), orders.READ_SCHEMA, "order")
    items = [{"name": p["name"], "qty": max(1, p["quantity"]), "spec": p["printed_spec"],
              "status": "required" if p["required"] else "gift" if p["gift"] else "optional"}
             for p in r["package_includes"]]
    orders.pick_nouns([i for i in items if i["status"] != "optional"], list(files))
    oid = "".join(ch for ch in r["order_id"] if ch.isalnum()).upper()
    ctx = {k: r[k] for k in ("product_title", "brand", "model", "variant")}
    ctx["key_specs"] = {s["name"]: s["value"] for s in r["key_specs"]}
    ctx["printed_specs"] = {i["name"]: i["spec"] for i in items if i["spec"]}
    return {"label": oid[-6:], "product": r["product_title"], "items": items, "context": ctx,
            "conflicts": r["listing_conflicts"]}


def check(video, mode, kit, out_dir, progress=lambda stage: None, fps=track.FPS):
    t0 = time.perf_counter()
    progress("Tracking items")
    f = run_all.facts_for(video, {"mode": mode}, kit, fps, out=out_dir, progress=progress)
    items = [i for i in kit["items"] if i["status"] != "optional"]
    got, reasons, notes = rules.verdict(mode, items, kit["label"], f)
    t = json.loads((Path(out_dir) / f"{Path(video).stem}_tracks.json").read_text())
    return build(video, mode, kit, f, got, reasons, notes, t, out_dir, round(time.perf_counter() - t0, 1))


def spans(frames, fps, gap=1.0):
    out = []
    for f in sorted(frames):
        if out and f / fps - out[-1][1] <= gap:
            out[-1][1] = f / fps
        else:
            out.append([f / fps, f / fps])
    return [[round(a, 1), round(b + 1 / fps, 1)] for a, b in out]


def keyframes(video, t, picks, out_dir):
    """Frames with every SAM box drawn, from the original video. picks: [(sam frame, caption)]."""
    names = list(t["tracks"])
    out = []
    for n, (f, caption) in enumerate(picks):
        cap, scale, _ = compare.source(t, video)
        cap.set(cv2.CAP_PROP_POS_MSEC, f / t["fps"] * 1000)
        ok, fr = cap.read()
        cap.release()
        if not ok:
            continue
        lw = max(2, fr.shape[1] // 300)
        for c, rows in t["tracks"].items():
            col = COLORS[names.index(c) % len(COLORS)]
            for r in rows:
                if r[0] == f:
                    x1, y1, x2, y2 = (int(v * scale) for v in r[2:6])
                    cv2.rectangle(fr, (x1, y1), (x2, y2), col, lw)
                    cv2.putText(fr, c, (x1 + 4, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, lw / 2.5, col, lw)
        name = f"frame_{n}.jpg"
        (Path(out_dir) / name).write_bytes(compare.jpg(fr, 960))
        out.append({"file": name, "caption": f"{caption} ({f / t['fps']:.1f} s)"})
    return out


def build(video, mode, kit, f, got, reasons, notes, t, out_dir, seconds):
    fps, items = t["fps"], [i for i in kit["items"] if i["status"] != "optional"]
    nouns = list(dict.fromkeys(i["noun"] for i in items))
    concepts = ["box"] + ([f["label_noun"]] if f["label_noun"] else []) + nouns
    biggest = lambda c: max(t["tracks"].get(c, []), key=lambda r: (r[4] - r[2]) * (r[5] - r[3]), default=None)  # noqa: E731
    picks = []
    if mode == "buyer" and t["box_frames"]:
        picks.append((t["box_frames"][0], "Box before opening"))
    if f["label_noun"] and biggest(f["label_noun"]):
        picks.append((biggest(f["label_noun"])[0], "Shipping label"))
    picks += [(biggest(i["noun"])[0], i["name"]) for i in items if biggest(i["noun"])][:4]
    if mode == "seller":
        picks.append((t["frames"] - 1, "End of video"))
    codes = [r.split(" ", 1)[1] for r in reasons]
    status = lambda c: "FAIL" if f"FAIL {c}" in reasons else "WARN" if any(r.startswith(f"WARN {c}") for r in reasons) else "OK"  # noqa: E731
    res = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sha256": file_sha(video), "mode": mode,
        "marketplace": "Shopee rules", "product": kit["product"], "label": kit["label"],
        "verdict": got, "level": got.split()[0], "reasons": reasons, "notes": notes,
        "plain": [PLAIN.get(r, r.replace("WARN gift missing: ", "Free gift not seen: ")) for r in reasons]
                 or [PLAIN.get(got, "")],
        "checks": [{"rule": name, "status": next((status(c) for c in cs if status(c) != "OK"), "OK")}
                   for name, cs, m in CHECKS if m in (None, mode)],
        "specs": [{"item": n, "printed": v["printed_text"], "check": v["spec_check"], "note": v["spec_note"]}
                  for n, v in f["items"].items()],
        "items": [{"name": i["name"], "qty": i["qty"], "status": i["status"], "seen_s": f["items"][i["name"]]["seen_s"],
                   "count": f["items"][i["name"]]["count"]} for i in items],
        "label_read": f["label_code"], "conflicts": kit.get("conflicts", []),
        "timeline": {"duration": f["duration"], "cuts": sorted(set(f["hard_cuts"] + f["jump_cuts"])),
                     "opened": round(compare.opened_at(t, nouns) / fps, 1) if mode == "buyer" else None,
                     "rows": [{"name": "shipping label" if c == f["label_noun"] else c,
                               "spans": spans({r[0] for r in t["tracks"].get(c, [])}, fps)} for c in concepts]},
        "frames": keyframes(video, t, picks, out_dir), "seconds": seconds, "codes": codes,
    }
    (Path(out_dir) / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


# --------------------------------------------------------------------------- verdict page (HTML fragment)
def render(res, src):
    """Verdict HTML. src(file) gives each frame's URL (or a data: URI for the evidence pack)."""
    e = lambda s: html.escape(str(s))  # noqa: E731
    dur = max(res["timeline"]["duration"], 0.1)
    pct = lambda x: f"{min(100, max(0, 100 * x / dur)):.2f}%"  # noqa: E731
    rows = "".join(
        f'<div class="tl-row"><span class="tl-name">{e(r["name"])}</span><div class="tl-bar">'
        + "".join(f'<i style="left:{pct(a)};width:calc({pct(b)} - {pct(a)})"></i>' for a, b in r["spans"])
        + "".join(f'<b class="cut" style="left:{pct(c)}" title="cut at {c} s"></b>' for c in res["timeline"]["cuts"])
        + (f'<b class="open" style="left:{pct(res["timeline"]["opened"])}" title="box opened"></b>'
           if res["timeline"]["opened"] is not None else "") + "</div></div>"
        for r in res["timeline"]["rows"])
    checks = "".join(f'<li class="{c["status"].lower()}"><span>{c["status"]}</span>{e(c["rule"])}</li>' for c in res["checks"])
    specs = "".join(f'<tr><td>{e(s["item"])}</td><td>{e(s["printed"] or "-")}</td><td class="{e(s["check"])}">'
                    f'{e(s["check"].replace("_", " "))}</td><td>{e(s["note"])}</td></tr>' for s in res["specs"])
    frames = "".join(f'<figure><img src="{src(fr["file"])}" alt="{e(fr["caption"])}"><figcaption>{e(fr["caption"])}'
                     f'</figcaption></figure>' for fr in res["frames"])
    lst = lambda xs: "".join(f"<li>{e(x)}</li>" for x in xs)  # noqa: E731
    return f"""
<section class="verdict {res['level'].lower()}"><div class="badge">{e(res['level'])}</div>
<div><h2>{e(res['verdict'])}</h2><ul class="plain">{lst(res['plain'])}</ul>
{f'<ul class="notes">{lst(res["notes"])}</ul>' if res['notes'] else ''}</div></section>
<section><h3>Timeline <small>{res['timeline']['duration']} s &middot; red = cut, dashed = box opened</small></h3>{rows}</section>
<section><h3>Key frames <small>SAM 3.1 boxes</small></h3><div class="frames">{frames}</div></section>
<section class="two"><div><h3>Rule checklist <small>{e(res['marketplace'])}, {e(res['mode'])}</small></h3>
<ul class="checks">{checks}</ul></div>
<div><h3>Spec check <small>text printed on the item vs the order</small></h3>
<table><tr><th>Item</th><th>Printed</th><th>Result</th><th>Why</th></tr>{specs}</table>
<p class="label">Label read: <b>{e(res['label_read'] or 'none')}</b> &middot; order needs <b>{e(res['label'] or 'any readable label')}</b></p></div></section>
{f'<section><h3>Listing conflicts</h3><ul>{lst(res["conflicts"])}</ul></section>' if res['conflicts'] else ''}
<section class="meta">Order: {e(res['product'])}<br>Video SHA-256: <code>{e(res['sha256'])}</code><br>
Checked {e(res['checked_at'])} in {res['seconds']} s</section>"""


def evidence(res, folder):
    """One self-contained HTML file: the verdict page with frames inlined."""
    css = (Path(__file__).parent / "static" / "style.css").read_text(encoding="utf-8")
    src = lambda f: "data:image/jpeg;base64," + base64.b64encode((Path(folder) / f).read_bytes()).decode()  # noqa: E731
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>ProofBox evidence {html.escape(res['sha256'][:12])}"
            f"</title><style>{css}</style></head><body><main><h1>ProofBox evidence pack</h1>{render(res, src)}"
            "</main></body></html>")


def sample(clip_id):
    """gates/out/<clip>.json + its clip -> app/samples/<clip>/ (result + frames). Samples are added on purpose."""
    g = ROOT / "gates"
    facts = json.loads((g / "out" / f"{clip_id}.json").read_text())
    t = json.loads((g / "out" / f"{clip_id}_tracks.json").read_text())
    row = next(r for r in run_all.csv.DictReader(open(g / "answer_key.csv", encoding="utf-8"))
               if r["file"] == run_all.base_of(clip_id))
    kit = json.loads((g / "kits.json").read_text(encoding="utf-8"))[row["kit"]]
    read = json.loads((g / "orders_read.json").read_text(encoding="utf-8")).get(row["kit"], {})
    kit = dict(kit, conflicts=read.get("listing_conflicts", kit.get("conflicts", [])))
    clip = next((g / "clips").glob(f"{clip_id}.*"))
    out = Path(__file__).parent / "samples" / clip_id
    out.mkdir(parents=True, exist_ok=True)
    res = build(clip, row["mode"], kit, facts, facts["verdict"], facts["reasons"], facts["notes"], t, out,
                facts.get("first_run_s", facts.get("seconds")))
    print(f"sample {clip_id}: {res['verdict']} -> {out}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["sample"]:
        for c in sys.argv[2:]:
            sample(c)
