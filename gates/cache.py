"""Disk cache + spend ledger for every Meta API call, so re-runs cost nothing.
A new call is refused if it would push the ledger total past PROOFBOX_BUDGET (USD, default 2.00)."""
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent / "cache"
LEDGER = DIR / "spend.jsonl"
BUDGET = float(os.environ.get("PROOFBOX_BUDGET", "2.00"))


def key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def spent():
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(line)["usd"] for line in LEDGER.read_text().splitlines() if line.strip())


def cached(kind, k, est_usd, fn):
    """fn() -> (json_result, usd). Returns the cached result when present, else calls fn once."""
    path = DIR / kind / f"{k}.json.gz"
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    if spent() + est_usd > BUDGET:
        raise SystemExit(f"Budget stop: spent ${spent():.3f} + next ~${est_usd:.3f} > ${BUDGET:.2f}. "
                         f"Raise PROOFBOX_BUDGET to continue.")
    result, usd = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(result).encode()))
    with LEDGER.open("a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "key": k, "usd": round(usd, 5)}) + "\n")
    return result
