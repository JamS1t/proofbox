#!/usr/bin/env python3
"""Muse Spark check, one cached call per clip: the order screenshots + the 3 sharpest crops of each kit item
(from SAM boxes, cut from the full-resolution original), label crops, and closed-box (buyer) or final
(seller) frames. Strict JSON out. Run through run_all.py; crops are saved to gates/out/<clip>/."""
import hashlib
import json
import os
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.meta_client import SPARK_MODEL, client, data_url  # noqa: E402
from cache import cached, key  # noqa: E402
from track import biggest, union  # noqa: E402

# ponytail: Spark price isn't in our notes; only the spend estimate uses these. Override per 1M tokens.
USD_IN = float(os.environ.get("SPARK_USD_IN", "1.00")) / 1e6
USD_OUT = float(os.environ.get("SPARK_USD_OUT", "4.00")) / 1e6
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

ITEM = {"type": "object", "additionalProperties": False,
        "required": ["name", "item_type", "same_kind", "matches_listing", "attributes", "mismatches",
                     "screen_state", "power_test", "confidence"],
        "properties": {
            "name": {"type": "string"},
            "item_type": {"type": "string"},
            "same_kind": {"type": "boolean"},
            "matches_listing": {"type": "boolean"},
            "attributes": {"type": "object", "additionalProperties": False,
                           "required": ["colour", "count", "model_variant"],
                           "properties": {"colour": {"type": "string"}, "count": {"type": "integer"},
                                          "model_variant": {"type": "string"}}},
            "mismatches": {"type": "array", "items": {"type": "string"}},
            "screen_state": {"type": "string", "enum": ["on", "off", "none"]},
            "power_test": {"type": "string", "enum": ["working", "no_display", "not_tried"]},
            "confidence": {"type": "number"}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["items", "label_readable", "label_text", "box_sides_seen", "seal_visible", "confidence"],
          "properties": {"items": {"type": "array", "items": ITEM}, "label_readable": {"type": "boolean"},
                         "label_text": {"type": "string"}, "box_sides_seen": {"type": "integer"},
                         "seal_visible": {"type": "boolean"}, "confidence": {"type": "number"}}}

PROMPT = """You check marketplace evidence videos. Mode: {mode} ({what}). Order: {product}.
The parcel must contain this kit (from the listing):
{kit}
Images follow, each group introduced by a text line. Judge only from the images.
Return one entry in `items` per KIT ITEM, in the order given, with `name` exactly as given.
- item_type: what the video crops actually show.
- same_kind: the crops show the same type of product as the kit item. Another colour, model or rating is still
  same_kind (that is a mismatch); a different product is not (a relay module is not a single-board computer,
  a board game is not a router). No crops -> same_kind=false, matches_listing=false.
- matches_listing: model, colour and any printed rating or capacity visible on the item (volts, amps, GB)
  match the kit spec and the order screenshots. A visible rating that differs -> false. Fewer copies than
  the kit qty is not a mismatch (quantity is checked separately).
- attributes: colour; count = how many of this item are visible at once; model_variant = model/rating text on it.
- mismatches: short reasons it differs; empty if it matches.
- screen_state: only for items with a digital display (LCD digits; status LEDs don't count): on if any crop
  shows a lit display or reading, off if the display is blank in every crop; none for items without a display.
- power_test: working = display on; no_display = someone tries to power it and the display stays blank;
  not_tried otherwise.
label_text: every character you can read on the shipping label (label crops or any frame), '' if none.
label_readable: the label text can be read. box_sides_seen: distinct outer sides (0-6) of the closed box
across the before-opening frames, 0 if none given. seal_visible: the last frames show the package closed and
sealed with tape, false if none given. confidence: 0-1."""


def jpg(img, edge):
    s = edge / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


def source(t, src):
    """Decode the original (full resolution) when its shape matches the prepped video, else the prepped one.
    Returns (capture, scale from prepped pixels, timed)."""
    cap = cv2.VideoCapture(str(src))
    ok, fr = cap.read()
    cap.release()
    if ok and abs(fr.shape[1] / fr.shape[0] - t["w"] / t["h"]) < 0.02:
        return cv2.VideoCapture(str(src)), fr.shape[1] / t["w"], True
    return cv2.VideoCapture(t["video"]), 1.0, False


def gather(t, src, concepts, items, k=3, n_box=8, n_end=3):
    """One pass over the video. Per concept (items: box around all copies, others: biggest box): sharpest crop
    (Laplacian variance) in each of k equal slices of its visible frames, so crops spread over time (a screen
    turning on gets caught). Plus n_box closed-box frames before any item appears and n_end final frames."""
    per = {c: (union if c in items else biggest)(t["tracks"].get(c, [])) for c in concepts}
    slot = {(c, f): i * k // len(b) for c, b in per.items() for i, f in enumerate(sorted(b))}
    n, fps = t["frames"], t["fps"]
    opened = min((min(per[c]) for c in items if per[c]), default=n // 2)
    closed = [f for f in t["box_frames"] if f < opened] or list(range(opened))
    pre = sorted({closed[i * len(closed) // n_box] for i in range(n_box)}) if closed else []
    end = sorted({max(0, n - 1 - i * fps) for i in range(n_end)})
    cap, scale, timed = source(t, src)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    best, full, done, i = {}, {}, set(), -1
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        i += 1
        ms = cap.get(cv2.CAP_PROP_POS_MSEC) if timed else 0
        f = round(ms * fps / 1000) if ms else (round(i * fps / src_fps) if timed else i)  # prepped frame index
        if f in done:
            continue
        done.add(f)
        for c, boxes in per.items():
            if f in boxes:
                x1, y1, x2, y2 = (int(v * scale) for v in boxes[f])
                pw, ph = (x2 - x1) // 10, (y2 - y1) // 10
                crop = fr[max(0, y1 - ph):y2 + ph, max(0, x1 - pw):x2 + pw]
                if crop.size:
                    sharp = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
                    s = (c, slot[(c, f)])
                    if s not in best or sharp > best[s][0]:
                        best[s] = (sharp, jpg(crop, 1024))
        if f in pre or f in end or f >= n - 1:
            full[f] = jpg(fr, 1024)
    cap.release()
    end = [x if x in full else max(full, default=x) for x in end]  # frame count can be off by one
    crops = {c: [best[(c, j)][1] for j in range(k) if (c, j) in best] for c in concepts}
    return crops, [full[x] for x in pre if x in full], [full[x] for x in dict.fromkeys(end) if x in full]


def spark(content, schema, name):
    """One cached strict-JSON Muse Spark call."""
    def call():
        r = client().responses.create(
            model=SPARK_MODEL, input=[{"role": "user", "content": content}],
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}})
        u = getattr(r, "usage", None)
        return json.loads(r.output_text), (u.input_tokens * USD_IN + u.output_tokens * USD_OUT if u else 0.05)

    digest = hashlib.sha256(json.dumps(content).encode()).hexdigest()
    return cached("spark", key(SPARK_MODEL, digest, schema), 0.05, call)


def image(path):
    return {"type": "input_image", "image_url": data_url(Path(path).read_bytes(), MIME[Path(path).suffix.lower()])}


def compare(t, src, mode, kit, shots, label="shipping label", save_dir=None):
    """kit: one kits.json entry (product, order_id_last6, items). shots: order screenshot paths."""
    items = [i for i in kit["items"] if i["checked"]]
    nouns = list(dict.fromkeys(i["sam_noun"] for i in items))
    crops, pre, end = gather(t, src, nouns + [label], nouns)
    what = "seller packing the order" if mode == "seller" else "buyer opening the parcel"
    lines = "\n".join(f"- {i['qty']}x {i['name']}" + (f" (spec: {i['spec']})" if i["spec"] else "") for i in items)
    content = [{"type": "input_text", "text": PROMPT.format(mode=mode, what=what, product=kit["product"], kit=lines)}]

    def add(text, images):
        content.append({"type": "input_text", "text": text})
        content.extend({"type": "input_image", "image_url": data_url(b)} for b in images)

    if shots:
        content.append({"type": "input_text", "text": "ORDER SCREENSHOTS (listing / order details):"})
        content.extend(image(p) for p in shots)
    for i in items:
        c = crops[i["sam_noun"]]
        add(f"KIT ITEM '{i['name']}' crops from the video:" if c else f"KIT ITEM '{i['name']}': not found in the video, no crops.", c)
    add("SHIPPING LABEL crops:" if crops[label] else "SHIPPING LABEL: not found by the tracker.", crops[label])
    add("CLOSED BOX before opening, frames in time order:" if mode == "buyer" else "LAST FRAMES of the packing video:",
        pre if mode == "buyer" else end)

    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        for c, imgs in crops.items():
            for j, b in enumerate(imgs):
                (Path(save_dir) / f"{c}_{j}.jpg").write_bytes(b)
        for j, b in enumerate(pre if mode == "buyer" else end):
            (Path(save_dir) / f"{'closed' if mode == 'buyer' else 'end'}_{j}.jpg").write_bytes(b)
    return spark(content, SCHEMA, "proofbox")
