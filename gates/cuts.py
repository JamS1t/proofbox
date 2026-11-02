#!/usr/bin/env python3
"""Recording cuts without AI: frame-difference spikes + SAM track jumps + container out of view.

    python gates/cuts.py gates/out/<clip>_tracks.json
"""
import json
import sys

import cv2
import numpy as np

# ponytail: fixed thresholds, calibrate on G3 (cut) and P7 (continuity) once filmed
CUT_MIN = 15      # mean abs grey diff (0-255) a cut frame must exceed
CUT_RATIO = 4     # ...and be this many times the surrounding motion
JUMP_FRAC = 0.25  # tracked object moves > this share of the frame diagonal in one frame
GAP_S = 3         # container out of view longer than this mid-video


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


def track_jumps(tracks, fps, w, h, skip=()):
    diag, out = (w * w + h * h) ** 0.5, []
    for c, rows in tracks.items():
        if c in skip:
            continue
        last = {}
        for f, oid, x1, y1, x2, y2, _ in rows:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            p = last.get(oid)
            if p and f - p[0] == 1 and ((cx - p[1]) ** 2 + (cy - p[2]) ** 2) ** 0.5 > JUMP_FRAC * diag:
                out.append({"t": round(f / fps, 1), "concept": c, "id": oid})
            last[oid] = (f, cx, cy)
    return out


def box_gaps(tracks, fps, container="box"):
    fs = sorted({r[0] for r in tracks.get(container, [])})
    return [{"from": round(a / fps, 1), "to": round(b / fps, 1)} for a, b in zip(fs, fs[1:]) if (b - a) / fps > GAP_S]


def cuts(t, container="box", skip=()):
    return {"hard_cuts": hard_cuts(frame_diffs(t["video"]), t["fps"]),
            "track_jumps": track_jumps(t["tracks"], t["fps"], t["w"], t["h"], skip),
            "box_gaps": box_gaps(t["tracks"], t["fps"], container)}


def _selftest():
    d = np.array([0, 2, 2, 3, 2, 40, 2, 3, 2, 2, 2], dtype=float)
    assert hard_cuts(d, 10) == [0.5], hard_cuts(d, 10)
    rows = [[0, 1, 0, 0, 10, 10, None], [1, 1, 500, 500, 510, 510, None]]
    assert track_jumps({"router": rows}, 10, 720, 1280) == [{"t": 0.1, "concept": "router", "id": 1}]


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(print("cuts selftest ok"))
    print(json.dumps(cuts(json.load(open(sys.argv[1]))), indent=1))
