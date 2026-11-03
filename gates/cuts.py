#!/usr/bin/env python3
"""Recording cuts and continuity without AI.

- hard cuts: one-frame spikes in frame difference, on the ORIGINAL video at its full frame rate (no API)
- jump cuts: objects of 2+ concepts jump together in the SAM tracks (the scene moved = a cut)
- continuity: the box or an ordered item is out of view 4+ s with no cut (any instance counts, never track IDs)

    python gates/cuts.py clip.mp4 gates/out/<clip>_tracks.json
"""
import json
import subprocess
import sys

import cv2
import numpy as np

# ponytail: fixed thresholds, calibrate on G3 (cut) and P7 (continuity) once filmed
CUT_MIN = 15      # mean abs grey diff (0-255) a cut frame must exceed
CUT_RATIO = 4     # ...and be this many times the surrounding motion
JUMP_FRAC = 0.25  # object moves > this share of the frame diagonal in one SAM frame
GAP_S = 4         # out of view this long or longer = continuity warning


def frame_diffs(video):
    """Mean grey difference between consecutive frames at the full frame rate, with each frame's time (s).
    ffmpeg decodes straight to 160 px grey frames, which is much faster than full-size OpenCV frames."""
    cap = cv2.VideoCapture(str(video))
    fps, w, h = cap.get(cv2.CAP_PROP_FPS) or 30, cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cap.release()
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream_side_data=rotation", "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout
    if probe.strip().lstrip("-") in ("90", "270"):
        w, h = h, w  # ffmpeg auto-rotates portrait phone video
    gh = max(2, int(160 * h / w) // 2 * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf", f"scale=160:{gh},format=gray",
                          "-vsync", "passthrough", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(-1, gh, 160).astype(np.float32)
    d = np.concatenate([[0.0], np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))]) if len(g) else np.array([])
    return d, [i / fps for i in range(len(d))]


def hard_cuts(d, ts):
    """A cut is a one-frame spike well above the motion on both sides of it."""
    out = []
    for i in range(1, len(d)):
        around = np.concatenate([d[max(1, i - 10):i], d[i + 1:i + 11]])
        base = float(np.median(around)) if len(around) else 0.0
        if d[i] > CUT_MIN and d[i] > CUT_RATIO * max(base, 1.0):
            out.append(round(ts[i], 1))
    return out


def jump_cuts(tracks, fps, w, h, skip=()):
    """SAM frames where objects of 2+ different concepts jump together. Jumps within one concept are usually
    identical items swapping IDs, so they are ignored."""
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


def continuity(tracks, fps, concepts, cuts):
    """Out-of-view stretches of GAP_S+ seconds between two sightings, unless a cut explains them."""
    out = []
    for c in concepts:
        fs = sorted({r[0] for r in tracks.get(c, [])})
        for a, b in zip(fs, fs[1:]):
            ta, tb = a / fps, b / fps
            if tb - ta >= GAP_S and not any(ta <= x <= tb for x in cuts):
                out.append(f"{c} out of view {ta:.1f}-{tb:.1f}s")
    return out


def cuts(t, src, concepts, skip=()):
    hc = hard_cuts(*frame_diffs(src))
    jc = jump_cuts(t["tracks"], t["fps"], t["w"], t["h"], skip)
    return {"hard_cuts": hc, "jump_cuts": jc, "continuity": continuity(t["tracks"], t["fps"], concepts, hc + jc)}


def _selftest():
    d = np.array([0, 2, 2, 3, 2, 40, 2, 3, 2, 2, 2], dtype=float)
    assert hard_cuts(d, [i / 30 for i in range(11)]) == [0.2], hard_cuts(d, [i / 30 for i in range(11)])
    a = [[0, 1, 0, 0, 10, 10, None], [1, 1, 500, 500, 510, 510, None]]
    b = [[0, 2, 600, 900, 610, 910, None], [1, 2, 0, 0, 10, 10, None]]
    assert jump_cuts({"router": a}, 5, 720, 1280) == []
    assert jump_cuts({"probe": a + [[r[0], 9] + r[2:] for r in b]}, 5, 720, 1280) == []  # pair swapping IDs
    assert jump_cuts({"router": a, "box": b}, 5, 720, 1280) == [0.2]
    seen = [[f, 1, 0, 0, 9, 9, None] for f in list(range(5)) + list(range(30, 35))]  # gap 0.8-6.0 s at 5 fps
    assert continuity({"circuit board": seen}, 5, ["circuit board"], []) == ["circuit board out of view 0.8-6.0s"]
    assert continuity({"circuit board": seen}, 5, ["circuit board"], [3.0]) == []


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(print("cuts selftest ok"))
    t = json.load(open(sys.argv[2]))
    print(json.dumps(cuts(t, sys.argv[1], list(t["tracks"])), indent=1))
