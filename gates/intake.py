#!/usr/bin/env python3
"""Clip intake: name the clips in gates/clips/raw/ (copied in filming order) by the ID spoken in the first
seconds ("clip G one", "clip G one B", "clip demo U two"). Muse Voice Transcribe hears the first 4 s; when that
is unclear, the next ID in filming order is used. Prints the mapping; --apply copies the clips to
gates/clips/<ID>.mp4 (raw files are kept) and joins G3a + G3b into G3.mp4.

    python gates/intake.py            # show the mapping only
    python gates/intake.py --apply
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "print"))
from common.meta_client import transcribe_wav  # noqa: E402
from cache import cached, file_sha, key  # noqa: E402
from make_print import filming_rows  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW, CLIPS = HERE / "clips" / "raw", HERE / "clips"
LETTER = {"g": "G", "gee": "G", "ji": "G", "jee": "G", "u": "U", "you": "U", "yu": "U", "p": "P", "pee": "P",
          "pea": "P", "pi": "P"}
NUM = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())}
NUM.update({"won": "1", "to": "2", "too": "2", "for": "4", "ate": "8"})
SUFFIX = {"a": "a", "ay": "a", "eh": "a", "b": "b", "be": "b", "bee": "b"}


def heard(video):
    """Transcript of the first 4 s (cached; ~$0.0002 per clip)."""
    def call():
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "head.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-t", "4", "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", str(wav)], check=True)
            r = transcribe_wav(str(wav))
        return {"text": r.get("transcript") or " ".join(t.get("transcript", "") for t in r.get("turns", []))}, 0.0002
    return cached("asr", key(file_sha(video), 4), 0.001, call)["text"]


def parse(text):
    """'Clip G one B.' -> 'G1_b'; 'clip G three A' -> 'G3a'; 'clip demo U two' -> 'DEMO_U2'; None if unclear."""
    s = ""
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if re.fullmatch(r"[gup][1-9][ab]?", w):
            s += w[0].upper() + w[1:]
        elif w == "demo":
            s += "D"
        else:
            s += LETTER.get(w) or NUM.get(w) or SUFFIX.get(w) or " "
    m = re.search(r"(D)?([GUP])([1-9])([ab])?", s)
    if not m:
        return None
    demo, cid, suf = m.group(1), m.group(2) + m.group(3), m.group(4)
    if demo:
        return f"DEMO_{cid}"
    if cid == "G3" and suf:
        return f"G3{suf}"
    return f"{cid}_b" if suf == "b" else cid


def mapping():
    order = [r["file"] for r in filming_rows()]
    raw = sorted(p for p in RAW.iterdir() if p.suffix.lower() in (".mp4", ".mov"))
    out, pos = [], -1
    for p in raw:
        text = heard(p)
        cid = parse(text)
        how = "voice" if cid in order else "filming order"
        if how == "voice":
            pos = order.index(cid)
        else:
            pos += 1
            cid = order[pos] if pos < len(order) else None
        out.append((p, text, cid, how))
    return out


def _selftest():
    assert parse("Clip G one.") == "G1" and parse("clip G one B") == "G1_b" and parse("Clip G three A") == "G3a"
    assert parse("clip demo U two") == "DEMO_U2" and parse("clip P5") == "P5" and parse("Clip, pee five, be.") == "P5_b"
    assert parse("hello") is None


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(print("intake selftest ok"))
    rows = mapping()
    ids = [c for _, _, c, _ in rows]
    for p, text, cid, how in rows:
        dup = " DUPLICATE" if cid and ids.count(cid) > 1 else ""
        print(f"{p.name:40} heard {text.strip()[:40]!r:44} -> {cid} ({how}){dup}")
    if "--apply" in sys.argv:
        for p, _, cid, _ in rows:
            if cid:
                shutil.copy2(p, CLIPS / f"{cid}{p.suffix.lower()}")
        import run_all
        import csv
        run_all.join_parts(CLIPS, [r["file"] for r in csv.DictReader(open(HERE / "answer_key.csv", encoding="utf-8"))])
        print(f"copied {sum(1 for c in ids if c)} clips into {CLIPS}")
