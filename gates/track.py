#!/usr/bin/env python3
"""SAM 3.1 video tracking, one short concept per request. Every API call is cached (gates/cache).

    python gates/track.py clip.mp4 box router charger
    python gates/track.py --selftest

Saves tracks to gates/out/<clip>_tracks.json and prints ID stability per concept and
when each item enters/leaves the box. Track rows: [frame, object_id, x1, y1, x2, y2, conf]
in prepped-video pixels, x2/y2 exclusive.
"""
import json
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.meta_client import SAM_MODEL, client  # noqa: E402
from cache import DIR, cached, file_sha, key  # noqa: E402

FPS = 5
USD_PER_FRAME = 0.20 / 1000  # SAM video, 50-frame minimum per request
OUT = Path(__file__).resolve().parent / "out"
HEADER = re.compile(r"^<(\d+)f(?:;[^>]*)?>(.*)$")
BOX = re.compile(r"<\|box;([^|]*)\|>")


def probe(path):
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,nb_read_packets:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout)
    s = j["streams"][0]
    return {"w": s["width"], "h": s["height"], "frames": int(s["nb_read_packets"]),
            "duration": float(j["format"]["duration"])}


def prep(src, fps=FPS):
    """fps, short edge <= 720, H.264, no audio. Named by source hash, so made once."""
    dst = DIR / "prep" / f"{file_sha(src)[:16]}_{fps}fps.mp4"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp.mp4")
        vf = f"fps={fps},scale='if(gt(iw,ih),-2,min(720,iw))':'if(gt(iw,ih),min(720,ih),-2)'"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-an", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(tmp)], check=True)
        tmp.replace(dst)
    return dst


def parse(text):
    """SAM grammar: one line per frame, '<Nf>' then records 'ID<|box;..|><|mask;..|>' (tokens in any order)."""
    rows = []
    for line in text.splitlines():
        m = HEADER.match(line.strip())
        if not m:
            continue
        parts = re.split(r",?(\d+)(?=<\|)", m.group(2))
        for oid, seg in zip(parts[1::2], parts[2::2]):
            b = BOX.search(seg)
            if b:
                d = dict(kv.split("=", 1) for kv in b.group(1).split(";") if "=" in kv)
                rows.append([int(m.group(1)), int(oid), int(d["x1"]), int(d["y1"]), int(d["x2"]) + 1,
                             int(d["y2"]) + 1, float(d["c"]) if "c" in d else None])
    return rows


def sam_video(file_id, concept, frames):
    # one_bit masks: same boxes, ~50x less text to stream (box on G1: 21 s vs 3.5 min)
    stream = client().responses.create(model=SAM_MODEL, stream=True, metadata={"mask_encoding": "one_bit"}, input=[{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": concept}, {"type": "input_video", "file_id": file_id}]}])
    deltas, final = [], None
    for ev in stream:
        if ev.type == "response.output_text.delta":
            deltas.append(ev.delta)
        elif ev.type == "response.completed":
            final = ev.response
        elif ev.type in ("response.failed", "response.incomplete", "error"):
            raise RuntimeError(f"SAM '{concept}' {ev.type}: {str(ev)[:400]}")
    if final is None:  # only trust a finished response
        raise RuntimeError(f"SAM '{concept}': stream ended without response.completed")
    return {"text": final.output_text or "".join(deltas)}, max(frames, 50) * USD_PER_FRAME


def track(src, concepts, fps=FPS):
    v = prep(src, fps)
    info = probe(v)
    up = {}

    def file_id():  # upload lazily: skipped when every concept is cached
        if "id" not in up:
            up["id"] = cached("upload", key(v.stem), 0, lambda: ({"id": client().files.create(
                file=(v.name, v.read_bytes(), "video/mp4"), purpose="user_data").id}, 0))["id"]
        return up["id"]

    if any(not (DIR / "sam" / f"{key(v.stem, c)}.json.gz").exists() for c in concepts):
        file_id()  # upload once, before the parallel requests

    def one(c):
        return parse(cached("sam", key(v.stem, c), max(info["frames"], 50) * USD_PER_FRAME,
                            lambda: sam_video(file_id(), c, info["frames"]))["text"])

    with ThreadPoolExecutor(max_workers=8) as ex:  # one request per concept, all at once
        tracks = dict(zip(concepts, ex.map(one, concepts)))
    t = {"video": str(v), "fps": fps, **info, "tracks": tracks}
    OUT.mkdir(exist_ok=True)
    (OUT / f"{Path(src).stem}_tracks.json").write_text(json.dumps(t))
    return t


def biggest(rows):
    """frame -> largest box of this concept in that frame."""
    best = {}
    for f, _, x1, y1, x2, y2, _ in rows:
        if f not in best or (x2 - x1) * (y2 - y1) > (best[f][2] - best[f][0]) * (best[f][3] - best[f][1]):
            best[f] = (x1, y1, x2, y2)
    return best


def union(rows):
    """frame -> one box around every detection of this concept (shows all copies, so Spark can count)."""
    u = {}
    for f, _, x1, y1, x2, y2, _ in rows:
        a = u.get(f, (x1, y1, x2, y2))
        u[f] = (min(a[0], x1), min(a[1], y1), max(a[2], x2), max(a[3], y2))
    return u


def box_events(t, container="box", skip=("label", "shipping label"), hold=5):
    """Item centre inside the biggest container box. A change must hold `hold` frames to count."""
    boxes, events = biggest(t["tracks"].get(container, [])), []
    for c, rows in t["tracks"].items():
        if c == container or c in skip:
            continue
        state, run = {}, {}
        for f, oid, x1, y1, x2, y2, _ in rows:
            b = boxes.get(f)
            if not b:
                continue
            inside = b[0] <= (x1 + x2) / 2 < b[2] and b[1] <= (y1 + y2) / 2 < b[3]
            if oid not in state:
                state[oid], run[oid] = inside, 0
            elif inside != state[oid]:
                run[oid] += 1
                if run[oid] >= hold:
                    state[oid], run[oid] = inside, 0
                    events.append({"t": round((f - hold + 1) / t["fps"], 1), "concept": c, "id": oid,
                                   "event": "enters" if inside else "leaves"})
            else:
                run[oid] = 0
    return sorted(events, key=lambda e: e["t"])


def report(t, container="box"):
    fps, lines = t["fps"], []
    for c, rows in t["tracks"].items():
        ids = {}
        for r in rows:
            ids.setdefault(r[1], []).append(r[0])
        at_once = max(Counter(r[0] for r in rows).values(), default=0)
        lines.append(f"{c}: {len(ids)} id(s), visible {len({r[0] for r in rows}) / fps:.1f}s, max {at_once} at once")
        for oid, fs in sorted(ids.items()):
            gaps = sum(b - a > 1 for a, b in zip(fs, fs[1:]))
            lines.append(f"  id {oid}: {fs[0] / fps:.1f}-{fs[-1] / fps:.1f}s, "
                         f"coverage {len(fs) / (fs[-1] - fs[0] + 1):.0%}, {gaps} gap(s)")
    lines += [f"{e['t']}s {e['concept']}#{e['id']} {e['event']} {container}" for e in box_events(t, container)]
    return lines


def _selftest():
    s = ("<0f>1<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10;c=0.9|><|mask;data=1,1,~a|>,"
         "2<|mask;data=1,1,~a|><|box;x1=5;y1=6;x2=7;y2=8;w=10;h=10|>\n<1f>\n")
    assert parse(s) == [[0, 1, 1, 2, 4, 5, 0.9], [0, 2, 5, 6, 8, 9, None]], parse(s)
    rows = [[f, 1, 0, 0, 100, 100, None] for f in range(20)]
    item = [[f, 7, 10, 10, 20, 20, None] if f < 10 else [f, 7, 200, 200, 210, 210, None] for f in range(20)]
    ev = box_events({"fps": 10, "tracks": {"box": rows, "router": item}})
    assert ev == [{"t": 1.0, "concept": "router", "id": 7, "event": "leaves"}], ev


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(print("track selftest ok"))
    print("\n".join(report(track(sys.argv[1], sys.argv[2:]))))
