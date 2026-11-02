#!/usr/bin/env python3
"""
Run this FIRST. Checks your Meta API key, lists the models your key can use, and
makes one tiny Muse Spark call (costs a fraction of a cent) to prove billing works.

    python common/preflight.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_client as mc  # noqa: E402

WANT = [mc.SPARK_MODEL, mc.SAM_MODEL, mc.ASR_MODEL, mc.IMAGE_MODEL]


def main():
    c = mc.client()
    try:
        ids = sorted(m.id for m in c.models.list().data)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Could not list models: {e}\nCheck the key, and that a card + credits are on dev.meta.ai/billing.")
    print(f"Your key can see {len(ids)} models.")
    for w in WANT:
        print(f"  {'OK ' if w in ids else 'MISSING'}  {w}")
    missing = [w for w in WANT if w not in ids]
    if missing:
        print("Models you can see:", ", ".join(ids))
    try:
        r = c.responses.create(model=mc.SPARK_MODEL, input="Reply with the single word OK.")
        print("Muse Spark replied:", r.output_text.strip()[:40])
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Muse Spark call failed: {e}")
    print("Preflight passed." if not missing else "Preflight: fix the MISSING models (check IDs) before running tests.")


if __name__ == "__main__":
    main()
