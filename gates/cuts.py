#!/usr/bin/env python3
"""Recording cuts and continuity without AI.

- hard cuts: one-frame spikes in frame difference
- jump cuts: 2+ tracked objects jump at once (the whole scene moved = a cut the frame diff missed)
- continuity (seller only): box out of view, or an item leaving the frame and coming back

    python gates/cuts.py gates/out/<clip>_tracks.json seller
"""
import json
import sys

import cv2
import numpy as np

from track import union

# ponytail: fixed thresholds, calibrate on G3 (cut) and P7 (continuity) once filmed
CUT_MIN = 15      # mean abs grey diff (0-255) a cut frame must exceed
CUT_RATIO = 4     # ...and be this many times the surrounding motion
JUMP_FRAC = 0.25  # object moves > this share of the frame diagonal in one frame
GAP_S = 3         # out of view longer than this counts
EDGE = 0.04       # box within this share of the frame edge = left the frame


def frame_diffs(video):
    cap, prev, d = cv2.VideoCapture(str(video)), None, []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(fr, (160, max(1, 160 * fr.shape[0] // fr.shape[1]))),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        d.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
        prev = g
    return np.array(d)


def hard_cuts(d, fps):
    """A cut is a one-frame spike well above the motion on both sides of it."""
    out = []
    for i in range(1, len(d)):
        around = np.concatenate([d[max(1, i - 10):i], d[i + 1:i + 11]])
        base = float(np.median(around)) if len(around) else 0.0
        if d[i] > CUT_MIN and d[i] > CUT_RATIO * max(base, 1.0):
            out.append(round(i / fps, 1))
    return out


def jump_cuts(tracks, fps, w, h, skip=()):
    """Frames where objects of 2+ different concepts jump together (e.g. box and router). Jumps within one
    concept are usually identical items swapping IDs, so they are ignored."""
    diag, at = (w * w + h * h) ** 0.5, {}
    for c, rows in tracks.items():
        if c in skip:
            continue
        last = {}
        for f, oid, x1, y1, x2, y2, _ in rows:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            p = last.get(oid)
            if p and f - p[0] == 1 and ((cx - p[1]) ** 2 + (cy - p[2]) ** 2) ** 0.5 > JUMP_FRAC * diag:
                at.setdefault(f, set()).add((c, oid))
            last[oid] = (f, cx, cy)
    return [round(f / fps, 1) for f, objs in sorted(at.items()) if len({c for c, _ in objs}) >= 2]


def gaps(frames, fps):
    fs = sorted(frames)
    return [(a, b) for a, b in zip(fs, fs[1:]) if (b - a) / fps > GAP_S]


def continuity(t, container, items):
    fps, w, h, out = t["fps"], t["w"], t["h"], []
    out += [f"box out of view {a / fps:.1f}-{b / fps:.1f}s"
            for a, b in gaps({r[0] for r in t["tracks"].get(container, [])}, fps)]
    for c in items:
        u = union(t["tracks"].get(c, []))
        for a, b in gaps(u, fps):
            x1, y1, x2, y2 = u[a]
            if x1 <= EDGE * w or y1 <= EDGE * h or x2 >= (1 - EDGE) * w or y2 >= (1 - EDGE) * h:
                out.append(f"{c} left the frame {a / fps:.1f}-{b / fps:.1f}s")
    return out


def cuts(t, mode, container="box", items=(), skip=()):
    return {"hard_cuts": hard_cuts(frame_diffs(t["video"]), t["fps"]),
            "jump_cuts": jump_cuts(t["tracks"], t["fps"], t["w"], t["h"], skip),
            "continuity": continuity(t, container, items) if mode == "seller" else []}


def _selftest():
    d = np.array([0, 2, 2, 3, 2, 40, 2, 3, 2, 2, 2], dtype=float)
    assert hard_cuts(d, 10) == [0.5], hard_cuts(d, 10)
    a = [[0, 1, 0, 0, 10, 10, None], [1, 1, 500, 500, 510, 510, None]]
    b = [[0, 2, 600, 900, 610, 910, None], [1, 2, 0, 0, 10, 10, None]]
    assert jump_cuts({"router": a}, 10, 720, 1280) == []
    assert jump_cuts({"test lead": a + [[r[0], 9] + r[2:] for r in b]}, 10, 720, 1280) == []  # pair swapping IDs
    assert jump_cuts({"router": a, "box": b}, 10, 720, 1280) == [0.1]
    board = [[f, 3, 650, 600, 719, 700, None] for f in range(10)] + [[f, 3, 300, 600, 400, 700, None] for f in range(60, 70)]
    box = [[f, 1, 100, 100, 600, 900, None] for f in range(70)]
    t = {"fps": 10, "w": 720, "h": 1280, "tracks": {"box": box, "circuit board": board}}
    assert continuity(t, "box", ["circuit board"]) == ["circuit board left the frame 0.9-6.0s"], continuity(t, "box", ["circuit board"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(print("cuts selftest ok"))
    print(json.dumps(cuts(json.load(open(sys.argv[1])), sys.argv[2]), indent=1))
