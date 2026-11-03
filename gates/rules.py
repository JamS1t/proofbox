#!/usr/bin/env python3
"""Plain-code marketplace evidence rules (Shopee) -> verdict. No AI in this file.

items = [{name, qty, status: required|gift}]; label = the order's 6-character label text.
facts = {duration, size_mb, hard_cuts, jump_cuts, continuity, label_code, label_readable, seal_visible,
         sides_seen, found_instead, items: {name: {seen_s, count, same_kind, spec_check, spec_note,
         screen_state, power_test}}}
"""
MAX_S, MAX_MB = 60, 30
MIN_SEEN_S = 1.0   # an item must be tracked at least this long to count as shown
# ponytail: rule says "all sides"; Spark only sees 8 sampled frames. Calibrate on G1/U1 vs U5.
SIDES_MIN = 4


def verdict(mode, items, label, f):
    """Returns (headline, reasons, notes). Headline = first reason: FAIL > FLAG > WARN, else PASS."""
    got = f["items"]
    present = {i["name"] for i in items if got[i["name"]]["seen_s"] >= MIN_SEEN_S and got[i["name"]]["same_kind"]}
    req = [i for i in items if i["status"] == "required"]
    fails, flags, warns, notes = [], [], [], []
    if not any(i["name"] in present for i in req):
        fails.append("wrong item")
        if f.get("found_instead"):
            notes.append(f"{f['found_instead']} found, {', '.join(i['name'] for i in req)} missing")
    elif any(i["name"] not in present for i in req):
        fails.append("missing item")
        notes += [f"missing: {i['name']}" for i in req if i["name"] not in present]
    short = [i for i in req if i["name"] in present and got[i["name"]]["count"] < i["qty"]]
    if short:
        fails.append("quantity")
        notes += [f"{i['name']}: {got[i['name']]['count']} of {i['qty']}" for i in short]
    if any(got[n]["spec_check"] == "mismatch" for n in present):
        fails.append("mismatch")
    notes += [f"{n}: {got[n]['spec_note'] or 'spec not verifiable from video'}" for n in present
              if got[n]["spec_check"] != "match"]
    if f["hard_cuts"] or f["jump_cuts"]:
        fails.append("cut")
    if f["duration"] > MAX_S:
        fails.append("rule length")
    if f["size_mb"] > MAX_MB:
        fails.append("rule size")
    code = "".join(ch for ch in f["label_code"].upper() if ch.isalnum())
    if code and label and code != label:
        fails.append("label mismatch")
        notes.append(f"label reads {code}, order needs {label}")
    elif not code:
        fails.append("rule label")
    if mode == "seller" and not f["seal_visible"]:
        fails.append("rule seal")
    if mode == "buyer" and f["sides_seen"] < SIDES_MIN:
        fails.append("rule sides")
    if any(got[n]["power_test"] == "no_display" or (mode == "buyer" and got[n]["screen_state"] == "off")
           for n in present):
        flags.append("power test inconclusive")
    warns += [f"gift missing: {i['name']}" for i in items if i["status"] == "gift" and i["name"] not in present]
    if f["continuity"]:
        warns.append("continuity")
    reasons = [f"FAIL {r}" for r in fails] + [f"FLAG {r}" for r in flags] + [f"WARN {r}" for r in warns]
    tested = mode == "seller" and any(got[n]["screen_state"] == "on" for n in present)
    return (reasons[0] if reasons else "PASS tested working" if tested else "PASS"), reasons, notes


def _selftest():
    ok = {"seen_s": 5, "count": 1, "same_kind": True, "spec_check": "match", "spec_note": "",
          "screen_state": "none", "power_test": "not_tried"}
    items = [{"name": "router", "qty": 1, "status": "required"}, {"name": "adapter", "qty": 1, "status": "required"}]
    base = {"duration": 40, "size_mb": 10, "hard_cuts": [], "jump_cuts": [], "continuity": [], "label_code": "tub2jn",
            "label_readable": True, "seal_visible": True, "sides_seen": 6, "found_instead": "",
            "items": {"router": ok, "adapter": ok}}
    v = lambda mode, it, f, label="TUB2JN": verdict(mode, it, label, dict(base, **f))[0]  # noqa: E731
    assert v("buyer", items, {}) == "PASS"
    assert v("buyer", items, {}, "TUB2JX") == "FAIL label mismatch"
    assert v("buyer", items, {"label_code": ""}) == "FAIL rule label"
    gone = dict(ok, seen_s=0)
    assert v("buyer", items, {"items": {"router": ok, "adapter": gone}}) == "FAIL missing item"
    assert v("buyer", items, {"items": {"router": gone, "adapter": gone}}) == "FAIL wrong item"
    leads = [{"name": "test leads", "qty": 2, "status": "required"}]
    assert v("seller", leads, {"items": {"test leads": ok}}) == "FAIL quantity"
    bad = dict(ok, spec_check="mismatch", spec_note="OUTPUT 9V 0.6A, order is 12V 3A")
    assert v("seller", items, {"items": {"router": ok, "adapter": bad}}) == "FAIL mismatch"
    assert v("buyer", items, {"jump_cuts": [3.0]}) == "FAIL cut"
    assert v("seller", items, {"continuity": ["x"]}) == "WARN continuity"
    gift = items + [{"name": "box", "qty": 1, "status": "gift"}]
    assert v("buyer", gift, {"items": {"router": ok, "adapter": ok, "box": gone}}) == "WARN gift missing: box"
    m = [{"name": "m", "qty": 1, "status": "required"}]
    assert v("buyer", m, {"items": {"m": dict(ok, screen_state="off")}}) == "FLAG power test inconclusive"
    on = dict(ok, screen_state="on", power_test="working")
    assert v("seller", m, {"items": {"m": on}}) == "PASS tested working"
    assert v("buyer", m, {"items": {"m": on}}) == "PASS"


if __name__ == "__main__":
    _selftest()
    print("rules selftest ok")
