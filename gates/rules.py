#!/usr/bin/env python3
"""Plain-code marketplace evidence rules (Shopee) -> verdict. No AI in this file.

facts = {duration, size_mb, hard_cuts, track_jumps, box_gaps, label_seen_s, label_readable,
         seal_visible, sides_seen, items: {name: {seen_s, count, same_kind, matches_listing,
         screen_state, power_test}}}
"""
import re

MAX_S, MAX_MB = 60, 30
MIN_SEEN_S = 1.0   # an item must be tracked at least this long to count as shown
LABEL_SEEN_S = 0.5
# ponytail: rule says "all sides"; Spark only sees 8 sampled frames. Calibrate on G1/U1 vs U5.
SIDES_MIN = 4


def parse_order(order):
    """'2x shirt A+charger' -> {'shirt A': 2, 'charger': 1}"""
    items = {}
    for part in order.split("+"):
        m = re.match(r"\s*(\d+)\s*x\s+(.+?)\s*$", part)
        items[m.group(2) if m else part.strip()] = int(m.group(1)) if m else 1
    return items


def verdict(mode, order, f):
    """Returns (headline, all_reasons). Headline = worst reason: FAIL > FLAG > WARN > PASS."""
    want, items = parse_order(order), f["items"]
    present = {n for n, i in items.items() if i["seen_s"] >= MIN_SEEN_S and i["same_kind"]}
    fails, flags, warns = [], [], []
    if not present:
        fails.append("wrong item")
    else:
        if set(want) - present:
            fails.append("missing item")
        if any(not items[n]["matches_listing"] for n in present):
            fails.append("mismatch")
        if any(items[n]["count"] < q for n, q in want.items() if n in present):
            fails.append("quantity")
    if f["hard_cuts"]:
        fails.append("cut")
    if f["duration"] > MAX_S:
        fails.append("rule length")
    if f["size_mb"] > MAX_MB:
        fails.append("rule size")
    if f["label_seen_s"] < LABEL_SEEN_S or not f["label_readable"]:
        fails.append("rule label")
    if mode == "seller" and not f["seal_visible"]:
        fails.append("rule seal")
    if mode == "buyer" and f["sides_seen"] < SIDES_MIN:
        fails.append("rule sides")
    if any(items[n]["power_test"] == "no_display" for n in present):
        flags.append("power test no display")
    if f["track_jumps"] or f["box_gaps"]:
        warns.append("continuity")
    reasons = [f"FAIL {r}" for r in fails] + [f"FLAG {r}" for r in flags] + [f"WARN {r}" for r in warns]
    tested = any(items[n]["screen_state"] == "on" for n in present)
    return (reasons[0] if reasons else "PASS tested working" if tested else "PASS"), reasons


def _selftest():
    assert parse_order("2x shirt A") == {"shirt A": 2}
    assert parse_order("router+charger") == {"router": 1, "charger": 1}
    ok = {"seen_s": 5, "count": 1, "same_kind": True, "matches_listing": True, "screen_state": "none",
          "power_test": "not_tried"}
    base = {"duration": 40, "size_mb": 10, "hard_cuts": [], "track_jumps": [], "box_gaps": [], "label_seen_s": 2,
            "label_readable": True, "seal_visible": True, "sides_seen": 6, "items": {"router": ok, "charger": ok}}
    assert verdict("buyer", "router+charger", base) == ("PASS", [])
    gone = dict(ok, seen_s=0)
    assert verdict("buyer", "router+charger", dict(base, items={"router": ok, "charger": gone}))[0] == "FAIL missing item"
    assert verdict("buyer", "router+charger", dict(base, items={"router": gone, "charger": gone}))[0] == "FAIL wrong item"
    assert verdict("seller", "2x router", dict(base, items={"router": ok}))[0] == "FAIL quantity"
    assert verdict("buyer", "router+charger", dict(base, hard_cuts=[3.0]))[0] == "FAIL cut"
    assert verdict("seller", "router+charger", dict(base, box_gaps=[{}]))[0] == "WARN continuity"
    on = dict(ok, screen_state="on", power_test="working")
    assert verdict("seller", "router", dict(base, items={"router": on}))[0] == "PASS tested working"


if __name__ == "__main__":
    _selftest()
    print("rules selftest ok")
