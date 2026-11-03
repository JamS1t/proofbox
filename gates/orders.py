#!/usr/bin/env python3
"""Read each order's screenshots with Muse Spark -> gates/kits.json: product, last 6 digits of the Order ID,
and the kit ("package includes") with a short SAM noun and the printed spec per item.
Screenshots: gates/orders/<prefix>*.png|jpg|jpeg|webp. An order already in kits.json is never re-read,
so hand edits win. Cached like every API call.

    python gates/orders.py router orangepi ...     # default: every prefix in the answer key
"""
import csv
import json
import sys
from pathlib import Path

from compare import MIME, image, spark

HERE = Path(__file__).resolve().parent
SHOTS, KITS = HERE / "orders", HERE / "kits.json"
HINT = {"router": "TP-Link TL-WR840N", "orangepi": "Orange Pi One 1GB", "multitester": "ANENG SZ305 (its 2 test leads, red + black, are one item: test lead, qty 2)",
        "usb": "SanDisk Ultra Luxe 32GB", "adapter": "DC 12V 3A power adapter",
        "uvlight": "Alonefire SV98 UV flashlight (listing packing list: flashlight + USB cable)"}

KIT_ITEM = {"type": "object", "additionalProperties": False,
            "required": ["name", "qty", "sam_noun", "spec", "checked"],
            "properties": {"name": {"type": "string"}, "qty": {"type": "integer"}, "sam_noun": {"type": "string"},
                           "spec": {"type": "string"}, "checked": {"type": "boolean"}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["product", "order_id_last6", "items"],
          "properties": {"product": {"type": "string"}, "order_id_last6": {"type": "string"},
                         "items": {"type": "array", "items": KIT_ITEM}}}
PROMPT = """These screenshots show one marketplace order: {hint}. Return:
- product: the product name as listed.
- order_id_last6: the last 6 digits of the Order ID shown, '' if no Order ID is visible.
- items: everything the buyer should find in the parcel, from "package includes" / "packing list" /
  "in the box" (if there is no list, just the product). One entry per distinct item:
  name (short), qty (a "pair" = 2), sam_noun (1-2 generic English words a segmentation model can find,
  e.g. router, power adapter, cable, multimeter, test lead, flashlight, usb drive, circuit board),
  spec (printed rating/model/colour/capacity to check on the item, e.g. "9V 0.6A", "32GB", "black"; '' if none),
  checked (false for manuals, cards, warranty slips, stickers, and batteries installed inside the product)."""


def shots(prefix, folder=SHOTS):
    return sorted(p for p in Path(folder).glob(f"{prefix}*") if p.suffix.lower() in MIME)


def extract(prefix, folder=SHOTS):
    files = shots(prefix, folder)
    if not files:
        raise SystemExit(f"No screenshots for order '{prefix}' in {folder} (name them {prefix}_1.png, ...)")
    content = [{"type": "input_text", "text": PROMPT.format(hint=HINT.get(prefix, prefix))}]
    return spark(content + [image(p) for p in files], SCHEMA, "kit")


def kits(prefixes, folder=SHOTS, path=KITS):
    """kits.json, reading any order that is not in it yet."""
    k = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    for p in [p for p in prefixes if p not in k]:
        k[p] = extract(p, folder)
        Path(path).write_text(json.dumps(k, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"read order {p}: {k[p]['product']}, ID ...{k[p]['order_id_last6']}, "
              + ", ".join(f"{i['qty']}x {i['name']}" + ("" if i["checked"] else " (not checked)") for i in k[p]["items"]))
    return k


if __name__ == "__main__":
    want = sys.argv[1:] or list(dict.fromkeys(r["order"] for r in csv.DictReader(open(HERE / "answer_key.csv"))))
    kits(want)
