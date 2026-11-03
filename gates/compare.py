#!/usr/bin/env python3
"""Muse Spark check, one cached call per clip: the order facts (human-checked truth or Spark's own reading),
the 3 sharpest crops of each kit item and of the label (from SAM boxes, cut from the full-resolution
original), closed-box frames (buyer) or last frames (seller), and whole frames after opening when SAM found
no ordered item. Specs are judged from text printed on the item, never from listing photos."""
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

# ponytail: Spark price isn't in our notes; only the spend estimate uses these. Override per 1M tokens.
USD_IN = float(os.environ.get("SPARK_USD_IN", "1.00")) / 1e6
USD_OUT = float(os.environ.get("SPARK_USD_OUT", "4.00")) / 1e6

ITEM = {"type": "object", "additionalProperties": False,
        "required": ["name", "item_type", "same_kind", "seen_in_frames", "printed_text", "spec_check", "spec_note",
                     "count_visible", "screen_state", "power_test", "confidence"],
        "properties": {
            "name": {"type": "string"}, "item_type": {"type": "string"}, "same_kind": {"type": "boolean"},
            "seen_in_frames": {"type": "boolean"},
            "printed_text": {"type": "string"},
            "spec_check": {"type": "string", "enum": ["match", "mismatch", "not_verifiable"]},
            "spec_note": {"type": "string"}, "count_visible": {"type": "integer"},
            "screen_state": {"type": "string", "enum": ["on", "off", "none"]},
            "power_test": {"type": "string", "enum": ["working", "no_display", "not_tried"]},
            "confidence": {"type": "number"}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["items", "label_code", "label_readable", "found_instead", "box_sides_seen", "seal_visible",
                       "confidence"],
          "properties": {"items": {"type": "array", "items": ITEM}, "label_code": {"type": "string"},
                         "label_readable": {"type": "boolean"}, "found_instead": {"type": "string"},
                         "box_sides_seen": {"type": "integer"}, "seal_visible": {"type": "boolean"},
                         "confidence": {"type": "number"}}}

PROMPT = """You check a marketplace evidence video. Mode: {mode} ({what}). Order: {product}.
Kit items to look for:
{kit}
What the order says about the product (use it for specs and looks; listing photos can be wrong):
{context}
Images follow, each group introduced by a text line. Judge only from the images.
Return one entry in `items` per KIT ITEM, in the order given, with `name` exactly as given.
- item_type: what the crops actually show. same_kind: the crops show this kit item. Another variant or rating
  is still same_kind (that is a spec mismatch); a different product is not (a relay module is not an Orange Pi,
  a board game is not a router). Crops come from separate tracked objects; a picture of the item printed on
  packaging (the router photo on its own box) is NOT the item. same_kind=true only if at least one crop shows
  the physical item itself. No crops -> same_kind=false.
- seen_in_frames: the physical item is clearly visible in the WHOLE FRAMES AFTER OPENING (false if none given or
  you are not sure; e.g. a coiled white cable in someone's hand counts as an ethernet cable only if it clearly is one).
- printed_text: text printed on the item itself that you can read in the crops (model, GB, V/A, nm); '' if none.
- spec_check: printed_text vs the order's specs. match; mismatch = clear conflict (e.g. OUTPUT 9V 0.6A when the
  order is a 12V 3A adapter); not_verifiable = the spec is not printed or not readable. spec_note: short reason.
- count_visible: most copies visible at once. screen_state: on/off for a digital display (status LEDs don't
  count), none otherwise. power_test: working = display on; no_display = someone tries to power it and the
  display stays blank; not_tried otherwise.
label_code: the large 6-character code on the shipping label exactly as printed ('' if no label is readable).
label_readable: a label is visible and its code can be read.
found_instead: when no kit item is in the crops or the whole frames, name what the WHOLE FRAMES AFTER OPENING
show; '' otherwise.
box_sides_seen: distinct outer sides (0-6) shown while the box is still CLOSED, across the box frames from the
first half of the video (ignore frames where it is already open); 0 if none given.
seal_visible: the last frames show the package closed and sealed with tape, false if none given.
confidence: 0-1."""


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


def opened_at(t, items):
    """First prepped frame where any kit item is seen; the middle of the clip if none is."""
    return min((min(r[0] for r in t["tracks"][c]) for c in items if t["tracks"].get(c)), default=t["frames"] // 2)


def split(boxes, parts):
    """frame->box dict cut into `parts` consecutive time slices."""
    fs = sorted(boxes)
    return [s for s in ({f: boxes[f] for f in fs[i * len(fs) // parts:(i + 1) * len(fs) // parts]}
                        for i in range(parts)) if s]


def slots_for(rows, k=3):
    """Crop slots for a kit item: one per tracked object (most seen first, up to k), so a picture printed on
    the packaging and the real item each get their own crop; spare slots split the main object's time."""
    ids = {}
    for f, oid, x1, y1, x2, y2, _ in rows:
        ids.setdefault(oid, {})[f] = (x1, y1, x2, y2)
    top = sorted(ids.values(), key=len, reverse=True)[:k]
    return split(top[0], k - len(top) + 1) + top[1:] if top else []


def gather(t, src, concepts, items, k=3, n_box=8, n_end=3, n_after=6):
    """One pass over the video. Sharpest crop (Laplacian variance) per slot: kit items get one slot per tracked
    object, other concepts (the label) k time slices of their biggest box. Plus box frames from the first half
    of the clip (for the sides), the last frames, and whole frames after opening."""
    slots = {c: slots_for(t["tracks"].get(c, []), k) if c in items else split(biggest(t["tracks"].get(c, [])), k)
             for c in concepts}
    n, fps = t["frames"], t["fps"]
    early = [f for f in t["box_frames"] if f < n // 2] or list(range(n // 2))
    pre = sorted({early[i * len(early) // n_box] for i in range(n_box)}) if early else []
    end = sorted({max(0, n - 1 - i * fps) for i in range(n_end)})
    after = sorted({int(n * (0.4 + 0.6 * (i + 0.5) / n_after)) for i in range(n_after)})  # items come out late
    cap, scale, timed = source(t, src)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    best, full, done, i = {}, {}, set(), -1
    need = {f for ss in slots.values() for s in ss for f in s} | set(pre) | set(end) | set(after) | {n - 1}
    while cap.grab():  # decode every frame, but only convert the ones we use
        i += 1
        ms = cap.get(cv2.CAP_PROP_POS_MSEC) if timed else 0
        f = round(ms * fps / 1000) if ms else (round(i * fps / src_fps) if timed else i)  # prepped frame index
        if f in done or (f not in need and f < n - 1):
            continue
        ok, fr = cap.retrieve()
        if not ok:
            continue
        done.add(f)
        for c, j, boxes in ((c, j, s) for c, ss in slots.items() for j, s in enumerate(ss)):
            if f in boxes:
                x1, y1, x2, y2 = (int(v * scale) for v in boxes[f])
                pw, ph = (x2 - x1) // 10, (y2 - y1) // 10
                crop = fr[max(0, y1 - ph):y2 + ph, max(0, x1 - pw):x2 + pw]
                if crop.size:
                    sharp = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
                    s = (c, j)
                    if s not in best or sharp > best[s][0]:
                        best[s] = (sharp, jpg(crop, 1024))
        if f in pre or f in end or f in after or f >= n - 1:
            full[f] = jpg(fr, 768)
    cap.release()
    end = [x if x in full else max(full, default=x) for x in end]  # frame count can be off by one
    crops = {c: [best[(c, j)][1] for j in range(len(slots[c])) if (c, j) in best] for c in concepts}
    pick = lambda xs: [full[x] for x in dict.fromkeys(xs) if x in full]  # noqa: E731
    return crops, pick(pre), pick(end), pick(after)


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


def compare(t, src, mode, kit, items, label, save_dir=None):
    """kit: one kits.json entry. items: kit items to check (required + gift), each with a SAM `noun`.
    label: the SAM noun that found the label ('' if none did)."""
    nouns = list(dict.fromkeys(i["noun"] for i in items))
    crops, pre, end, after = gather(t, src, nouns + ([label] if label else []), nouns)
    crops.setdefault("", [])
    what = "seller packing the order" if mode == "seller" else "buyer opening the parcel"
    lines = "\n".join(f"- {i['qty']}x {i['name']} ({i['status']})" for i in items)
    content = [{"type": "input_text", "text": PROMPT.format(mode=mode, what=what, product=kit["product"], kit=lines,
                                                             context=json.dumps(kit["context"], ensure_ascii=False))}]

    def add(text, images):
        content.append({"type": "input_text", "text": text})
        content.extend({"type": "input_image", "image_url": data_url(b)} for b in images)

    for i in items:
        c = crops[i["noun"]]
        add(f"KIT ITEM '{i['name']}' crops from the video:" if c else f"KIT ITEM '{i['name']}': not found in the video, no crops.", c)
    add("SHIPPING LABEL crops:" if crops[label] else "SHIPPING LABEL: not found by the tracker.", crops[label])
    add("BOX in the first half of the video, frames in time order:" if mode == "buyer" else "LAST FRAMES of the packing video:",
        pre if mode == "buyer" else end)
    if any(not crops[i["noun"]] for i in items):  # the tracker missed something: let Spark look at whole frames
        add("WHOLE FRAMES AFTER OPENING (the tracker missed at least one kit item):", after)

    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        for c, imgs in crops.items():
            for j, b in enumerate(imgs):
                (Path(save_dir) / f"{c.replace(' ', '_')}_{j}.jpg").write_bytes(b)
        for j, b in enumerate(pre if mode == "buyer" else end):
            (Path(save_dir) / f"{'closed' if mode == 'buyer' else 'end'}_{j}.jpg").write_bytes(b)
    return spark(content, SCHEMA, "proofbox")
