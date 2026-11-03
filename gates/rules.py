#!/usr/bin/env python3
"""Plain-code marketplace evidence rules (Shopee) -> verdict. No AI in this file.

want = {kit item name: qty} (checked items only); oid = last 6 digits of the Order ID ('' = unknown).
facts = {duration, size_mb, hard_cuts, jump_cuts, continuity, label_text, label_readable, seal_visible,
         sides_seen, items: {name: {seen_s, count, same_kind, matches_listing, screen_state, power_test}}}
"""
import re

MAX_S, MAX_MB = 60, 30
MIN_SEEN_S = 1.0   # an item must be tracked at least this long to count as shown
# ponytail: rule says "all sides"; Spark only sees 8 sampled frames. Calibrate on G1/U1 vs U5.
SIDES_MIN = 4


def label_ok(f, oid):
    """The label must show the order's 6 digits (any spacing); without an ID, just be readable."""
    return oid in re.sub(r"\D", "", f["label_text"]) if oid else f["label_readable"]


def verdict(mode, want, oid, f):
    """Returns (headline, all_reasons). Headline = first reason: FAIL > FLAG > WARN, else PASS."""
    items = f["items"]
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
    if f["hard_cuts"] or f["jump_cuts"]:
        fails.append("cut")
    if f["duration"] > MAX_S:
        fails.append("rule length")
    if f["size_mb"] > MAX_MB:
        fails.append("rule size")
    if not label_ok(f, oid):
        fails.append("rule label")
    if mode == "seller" and not f["seal_visible"]:
        fails.append("rule seal")
    if mode == "buyer" and f["sides_seen"] < SIDES_MIN:
        fails.append("rule sides")
    # a buyer's device with a display must light up; Spark may also see a failed power-on directly
    if any(items[n]["power_test"] == "no_display" or (mode == "buyer" and items[n]["screen_state"] == "off")
           for n in present):
        flags.append("power test no display")
    if f["continuity"]:
        warns.append("continuity")
    reasons = [f"FAIL {r}" for r in fails] + [f"FLAG {r}" for r in flags] + [f"WARN {r}" for r in warns]
    # buyers must power devices on anyway; for a seller a lit display is extra proof
    tested = mode == "seller" and any(items[n]["screen_state"] == "on" for n in present)
    return (reasons[0] if reasons else "PASS tested working" if tested else "PASS"), reasons


def _selftest():
    ok = {"seen_s": 5, "count": 1, "same_kind": True, "matches_listing": True, "screen_state": "none",
          "power_test": "not_tried"}
    base = {"duration": 40, "size_mb": 10, "hard_cuts": [], "jump_cuts": [], "continuity": [],
            "label_text": "Order ...48 2913", "label_readable": True, "seal_visible": True, "sides_seen": 6,
            "items": {"router": ok, "adapter": ok}}
    want = {"router": 1, "adapter": 1}
    assert verdict("buyer", want, "482913", base) == ("PASS", [])
    assert verdict("buyer", want, "482914", base)[0] == "FAIL rule label"
    gone = dict(ok, seen_s=0)
    assert verdict("buyer", want, "", dict(base, items={"router": ok, "adapter": gone}))[0] == "FAIL missing item"
    assert verdict("buyer", want, "", dict(base, items={"router": gone, "adapter": gone}))[0] == "FAIL wrong item"
    assert verdict("seller", {"stand": 2}, "", dict(base, items={"stand": ok}))[0] == "FAIL quantity"
    assert verdict("buyer", want, "", dict(base, jump_cuts=[3.0]))[0] == "FAIL cut"
    assert verdict("seller", want, "", dict(base, continuity=["x"]))[0] == "WARN continuity"
    blank = dict(ok, screen_state="off")
    assert verdict("buyer", {"m": 1}, "", dict(base, items={"m": blank}))[0] == "FLAG power test no display"
    assert verdict("seller", {"m": 1}, "", dict(base, items={"m": blank}))[0] == "PASS"
    on = dict(ok, screen_state="on", power_test="working")
    assert verdict("seller", {"m": 1}, "", dict(base, items={"m": on}))[0] == "PASS tested working"
    assert verdict("buyer", {"m": 1}, "", dict(base, items={"m": on}))[0] == "PASS"


if __name__ == "__main__":
    _selftest()
    print("rules selftest ok")
