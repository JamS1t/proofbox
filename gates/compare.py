#!/usr/bin/env python3
"""Muse Spark check, one cached call per clip: listing screenshot + the 3 sharpest crops of each
tracked item (from SAM boxes), label crops, and closed-box (buyer) or final (seller) frames.
Strict JSON out. Run through run_all.py; crops are saved to gates/out/<clip>/ for inspection."""
import hashlib
import json
import os
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.meta_client import SPARK_MODEL, client, data_url  # noqa: E402
from cache import cached, key  # noqa: E402
from track import biggest  # noqa: E402


def union(rows):
    """frame -> one box around every detection of this concept (shows all copies, so Spark can count)."""
    u = {}
    for f, _, x1, y1, x2, y2, _ in rows:
        a = u.get(f, (x1, y1, x2, y2))
        u[f] = (min(a[0], x1), min(a[1], y1), max(a[2], x2), max(a[3], y2))
    return u

# ponytail: Spark price isn't in our notes; only the spend estimate uses these. Override per 1M tokens.
USD_IN = float(os.environ.get("SPARK_USD_IN", "1.00")) / 1e6
USD_OUT = float(os.environ.get("SPARK_USD_OUT", "4.00")) / 1e6

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

PROMPT = """You check marketplace {mode} evidence videos. Mode: {mode} ({what}). Order: {order}.
Images follow, each group introduced by a text line. Judge only from the images.
Return one entry in `items` per ITEM, in the order given, with `name` exactly as given.
- item_type: what the video crops actually show.
- same_kind: the crops show the same kind of product as the ordered item (another colour or model is still
  same_kind; a different product is not). No crops -> same_kind=false, matches_listing=false.
- matches_listing: colour, model/variant and visible details match the listing screenshot (or the name if none).
- attributes: colour, count = how many of this item are visible at once, model_variant (text on it if any).
- mismatches: short reasons it differs from the listing; empty if it matches.
- screen_state: on/off for devices with a display, none otherwise.
- power_test: working = display on; no_display = someone visibly tries to power it (switch, dial, button)
  and the screen stays blank; not_tried otherwise.
label_readable: name/address/tracking text on the shipping label can be read in the label crops.
label_text: the readable label text, short. box_sides_seen: distinct outer sides (0-6) of the closed box seen
across the before-opening frames, 0 if none given. seal_visible: the last frames show the package closed and
sealed with tape, false if none given. confidence: 0-1."""


def jpg(img, edge):
    s = edge / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def gather(t, concepts, items, k=3, n_box=8, n_end=3):
    """One pass over the prepped video. Per concept (items: all copies, others: biggest box): sharpest crop (Laplacian variance) in each of k equal
    slices of its visible frames, so crops spread over time (a screen turning on gets caught).
    Plus n_box closed-box frames before any item appears and n_end final frames, 1 s apart."""
    per = {c: (union if c in items else biggest)(t["tracks"].get(c, [])) for c in concepts}
    slot = {(c, f): i * k // len(b) for c, b in per.items() for i, f in enumerate(sorted(b))}
    n, fps = t["frames"], t["fps"]
    opened = min((min(per[c]) for c in items if per[c]), default=n // 2)
    closed = [f for f in sorted(t["box_frames"]) if f < opened] or list(range(opened))
    pre = sorted({closed[i * len(closed) // n_box] for i in range(n_box)}) if closed else []
    end = sorted({max(0, n - 1 - i * fps) for i in range(n_end)})
    best, full, cap, f = {}, {}, cv2.VideoCapture(t["video"]), 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        for c, boxes in per.items():
            if f in boxes:
                x1, y1, x2, y2 = boxes[f]
                pw, ph = (x2 - x1) // 10, (y2 - y1) // 10
                crop = fr[max(0, y1 - ph):y2 + ph, max(0, x1 - pw):x2 + pw]
                if crop.size:
                    sharp = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
                    s = (c, slot[(c, f)])
                    if s not in best or sharp > best[s][0]:
                        best[s] = (sharp, jpg(crop, 512))
        if f in pre or f in end or f == n - 1:
            full[f] = jpg(fr, 768)
        f += 1
    if end and end[-1] not in full and full:  # frame count off by one: use the last decoded frame
        end[-1] = max(full)
    crops = {c: [best[(c, i)][1] for i in range(k) if (c, i) in best] for c in concepts}
    return crops, [full[x] for x in pre if x in full], [full[x] for x in end if x in full]


def listing(folder, name):
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"), ("webp", "image/webp")):
        p = Path(folder) / f"{name}.{ext}"
        if p.exists():
            return p.read_bytes(), mime
    return None


def compare(t, mode, order, want, concept_of, listings, label="shipping label", save_dir=None):
    """want: {item name: qty}. Returns Spark's strict JSON (see SCHEMA)."""
    item_concepts = list(dict.fromkeys(concept_of(n) for n in want))
    crops, pre, end = gather(t, item_concepts + [label], item_concepts)
    what = "seller packing the order" if mode == "seller" else "buyer opening the parcel"
    content = [{"type": "input_text", "text": PROMPT.format(mode=mode, what=what, order=order)}]

    def add(text, images, mime="image/jpeg"):
        content.append({"type": "input_text", "text": text})
        content.extend({"type": "input_image", "image_url": data_url(b, mime)} for b in images)

    for n in want:
        lst = listing(listings, n)
        if lst:
            add(f"ITEM '{n}' listing screenshot:", [lst[0]], lst[1])
        else:
            add(f"ITEM '{n}': no listing screenshot, judge by the name.", [])
        c = crops[concept_of(n)]
        add(f"ITEM '{n}' crops from the video:" if c else f"ITEM '{n}': not found in the video, no crops.", c)
    add("SHIPPING LABEL crops:" if crops[label] else "SHIPPING LABEL: not found in the video.", crops[label])
    if mode == "buyer":
        add("CLOSED BOX before opening, frames in time order:", pre)
    else:
        add("LAST FRAMES of the packing video:", end)

    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        for c, imgs in crops.items():
            for i, b in enumerate(imgs):
                (Path(save_dir) / f"{c}_{i}.jpg").write_bytes(b)
        for i, b in enumerate(pre if mode == "buyer" else end):
            (Path(save_dir) / f"{'closed' if mode == 'buyer' else 'end'}_{i}.jpg").write_bytes(b)

    def call():
        r = client().responses.create(
            model=SPARK_MODEL, input=[{"role": "user", "content": content}],
            text={"format": {"type": "json_schema", "name": "proofbox", "schema": SCHEMA, "strict": True}})
        u = getattr(r, "usage", None)
        usd = u.input_tokens * USD_IN + u.output_tokens * USD_OUT if u else 0.05
        return json.loads(r.output_text), usd

    digest = hashlib.sha256(json.dumps(content).encode()).hexdigest()
    return cached("spark", key(SPARK_MODEL, digest, SCHEMA), 0.05, call)
