#!/usr/bin/env python3
"""Section A: read the real orders and pick SAM search words. All API calls cached.

1. Muse Spark reads each order's screenshots blind (<kit>_order, _title, _listing*, _specs*) ->
   gates/orders_read.json, scored against gates/orders/<kit>_truth.json -> gates/ORDERS_READ.md.
2. For each item in the truth kit, Spark proposes 3 short nouns and the screenshot that shows it best;
   SAM 3.1 (image) tests each noun there; the best-confidence noun is kept (< 0.5 flagged) -> gates/kits.json.

    python gates/orders.py
"""
import hashlib
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.meta_client import sam_image_boxes  # noqa: E402
from cache import cached, key  # noqa: E402
from compare import spark  # noqa: E402

HERE = Path(__file__).resolve().parent
ORDERS = HERE / "orders"
KITS = ["router", "orangepi", "adapter", "multitester", "usb", "uvlight"]
MIN_CONF = 0.5
CONTEXT = ("product_title", "brand", "model", "variant", "key_specs", "key_specs_orange_pi_one", "spec_check_rule",
           "visual", "how_to_tell_one_from_pc", "verification_limits", "notes")

READ_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["product_title", "brand", "model", "variant", "quantity", "order_id",
                            "package_includes", "key_specs", "listing_conflicts"],
               "properties": {
                   "product_title": {"type": "string"}, "brand": {"type": "string"}, "model": {"type": "string"},
                   "variant": {"type": "string"}, "quantity": {"type": "integer"}, "order_id": {"type": "string"},
                   "package_includes": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["name", "quantity", "required", "gift", "printed_spec"],
                       "properties": {"name": {"type": "string"}, "quantity": {"type": "integer"},
                                      "required": {"type": "boolean"}, "gift": {"type": "boolean"},
                                      "printed_spec": {"type": "string"}}}},
                   "key_specs": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["name", "value"],
                       "properties": {"name": {"type": "string"}, "value": {"type": "string"}}}},
                   "listing_conflicts": {"type": "array", "items": {"type": "string"}}}}
READ_PROMPT = """These screenshots are one online marketplace order: order details, product title, listing photos and
spec/description pages. Read them for a buyer-protection check.
- product_title, brand, model. variant = the variant actually ordered (from the order details, not a variant
  pre-selected on the listing page). quantity ordered. order_id exactly as shown.
- package_includes: everything the buyer should receive. quantity per item (a "pair" = 2; count leads/cables
  as the list does). required=false for paper inserts (manual, guide), retail packaging, accessories only shown
  in photos and not in any packing list, and anything the listing says is not included. gift=true for promised
  free gifts. printed_spec = what should be printed on the item itself to verify it (model, capacity,
  voltage/current, wavelength); '' if none.
- key_specs: the important specs as name/value pairs.
- listing_conflicts: every place the listing contradicts itself or the ordered variant (different specs,
  different packing lists, photos showing another model or rating). Empty if none."""

NOUN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
               "properties": {"items": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["name", "nouns", "screenshot"],
                   "properties": {"name": {"type": "string"}, "nouns": {"type": "array", "items": {"type": "string"}},
                                  "screenshot": {"type": "integer"}}}}}}
NOUN_PROMPT = """Kit items of this order:
{items}
For each item, in the same order, give: 3 different short generic English nouns (1-2 words, best first) an
open-vocabulary segmentation model should use to find that item in a phone video, and the number of the
numbered screenshot below that shows that item most clearly."""


def screenshots(kit):
    nat = lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]  # noqa: E731
    return [p for pat in ("_order", "_title", "_listing*", "_specs*")
            for p in sorted(ORDERS.glob(f"{kit}{pat}.png"), key=nat)]


def jpeg(path, edge=1600):
    im = Image.open(path).convert("RGB")
    im.thumbnail((edge, edge))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def content(text, files, numbered=False):
    from common.meta_client import data_url
    c = [{"type": "input_text", "text": text}]
    for n, p in enumerate(files, 1):
        if numbered:
            c.append({"type": "input_text", "text": f"Screenshot {n}:"})
        c.append({"type": "input_image", "image_url": data_url(jpeg(p))})
    return c


def read_order(kit):
    return spark(content(READ_PROMPT, screenshots(kit)), READ_SCHEMA, "order")


def sam_conf(img, noun):
    res = cached("sam_img", key(hashlib.sha256(img).hexdigest(), noun), 0.0025,
                 lambda: ({"boxes": sam_image_boxes(img, noun)}, 0.0025))
    return max((b["conf"] or 0 for b in res["boxes"]), default=0.0)


def kit_from_truth(kit):
    """Truth kit + best SAM noun per required/gift item."""
    t = json.load(open(ORDERS / f"{kit}_truth.json", encoding="utf-8"))
    items = [{"name": p["name"], "qty": p.get("quantity", 1),
              "status": "required" if p.get("required") else "gift" if p.get("gift") else "optional"}
             for p in t["package_includes"] if p.get("included", True)]
    check = [i for i in items if i["status"] != "optional"]
    files = [p for p in screenshots(kit) if "_listing" in p.name or "_title" in p.name] or screenshots(kit)
    lines = "\n".join(f"- {i['name']}" for i in check)
    nouns = spark(content(NOUN_PROMPT.format(items=lines), files, numbered=True), NOUN_SCHEMA, "nouns")["items"]
    for i, n in zip(check, nouns):
        img = jpeg(files[min(max(n["screenshot"], 1), len(files)) - 1])
        i["tried"] = {w.lower(): round(sam_conf(img, w.lower()), 3) for w in n["nouns"][:3]}
        i["noun"], i["conf"] = max(i["tried"].items(), key=lambda kv: kv[1])
        i["flag"] = i["conf"] < MIN_CONF
        i["photo"] = files[min(max(n["screenshot"], 1), len(files)) - 1].name
    return {"order_id": t["order_id"], "label": t["label_text"], "product": t["product_title"], "items": items,
            "context": {k: t[k] for k in CONTEXT if k in t}, "conflicts": t.get("listing_conflicts", [])}


# ------------------------------------------------------------------ scoring (plain code)
STOP = {"the", "and", "with", "for", "one", "set", "pcs", "piece", "unit", "usb"}


def words(s):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def score(read, truth):
    req_t = [p for p in truth["package_includes"] if p.get("required")]
    req_r = [p for p in read["package_includes"] if p["required"]]
    found = [p for p in req_t if any(words(p["name"]) & words(r["name"]) for r in req_r)]
    qty_ok = all(any(words(p["name"]) & words(r["name"]) and r["quantity"] == p["quantity"] for r in req_r)
                 for p in req_t if "quantity" in p)
    tc = truth.get("listing_conflicts", [])
    hit = [c for c in tc if any(len({w for w in words(c) if len(w) > 3} & words(r)) >= 2 for r in read["listing_conflicts"])]
    tv = re.sub(r"\[.*?\]", "", truth["variant"])
    return {"order_id": norm(read["order_id"]) == norm(truth["order_id"]),
            "model": norm(truth["model"]) in norm(read["model"] + read["product_title"]),
            "variant": len(words(tv) & words(read["variant"])) >= max(1, round(0.6 * len(words(tv)))),
            "quantity": read["quantity"] == truth["quantity"],
            "required_items": f"{len(found)}/{len(req_t)}", "required_all": len(found) == len(req_t),
            "item_qty": qty_ok, "extra_required": max(0, len(req_r) - len(req_t)),
            "conflicts": f"{len(hit)}/{len(tc)}", "conflicts_all": len(hit) == len(tc)}


def main():
    read, kits, rows = {}, {}, []
    for k in KITS:
        read[k] = read_order(k)
        truth = json.load(open(ORDERS / f"{k}_truth.json", encoding="utf-8"))
        s = score(read[k], truth)
        rows.append((k, s))
        kits[k] = kit_from_truth(k)
        print(k, s, [(i["name"][:24], i.get("noun"), i.get("conf")) for i in kits[k]["items"] if "noun" in i])
    (HERE / "orders_read.json").write_text(json.dumps(read, indent=1, ensure_ascii=False), encoding="utf-8")
    (HERE / "kits.json").write_text(json.dumps(kits, indent=1, ensure_ascii=False), encoding="utf-8")

    yn = lambda b: "Y" if b else "N"  # noqa: E731
    fields = ("order_id", "model", "variant", "quantity", "required_all", "item_qty", "conflicts_all")
    md = ["# Order reading accuracy (Muse Spark vs human-checked truth)", "",
          "| Kit | Order ID | Model | Variant | Qty | Required items | Item qty | Extra required | Conflicts found |",
          "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {k} | {yn(s['order_id'])} | {yn(s['model'])} | {yn(s['variant'])} | {yn(s['quantity'])} | "
           f"{s['required_items']} | {yn(s['item_qty'])} | {s['extra_required']} | {s['conflicts']} |" for k, s in rows]
    total = sum(s[f] for _, s in rows for f in fields)
    md += ["", f"**Fields correct: {total}/{len(rows) * len(fields)}** (order ID, model, variant, quantity, all "
               f"required items, item quantities, all listing conflicts; {len(rows)} orders).", "",
           "## SAM search words (SAM 3.1 image test on the listing photo Spark picked)", "",
           "| Kit | Item | Status | Best noun | Confidence | Tried |", "|---|---|---|---|---|---|"]
    md += [f"| {k} | {i['name']} | {i['status']} | {i['noun']} | {i['conf']:.2f}{' **LOW**' if i['flag'] else ''} | "
           + ", ".join(f"{w} {c:.2f}" for w, c in i["tried"].items()) + " |"
           for k, kit in kits.items() for i in kit["items"] if "noun" in i]
    (HERE / "ORDERS_READ.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"fields correct {total}/{len(rows) * len(fields)}; wrote gates/ORDERS_READ.md, orders_read.json, kits.json")


if __name__ == "__main__":
    main()
