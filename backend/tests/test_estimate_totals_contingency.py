"""One contingency rule for generating AND editing an estimate (2026-10-12).

The edit path's rollup still did `contingencyPct or 5` after the generator was
fixed, so applying any AI edit re-added 5% to divisions the set says carry none.
"""
from app.services.estimate_totals import contingency_pct_for, rollup_category, rollup_payload


def _cat(name="01 General Requirements", pct=None, lines=(1000,)):
    c = {"name": name, "items": [{"name": f"l{i}", "labor": v, "materials": 0} for i, v in enumerate(lines)]}
    if pct is not None:
        c["contingencyPct"] = pct
    return c


def test_the_division_set_beats_what_the_category_says():
    assert contingency_pct_for(_cat(pct=10), {"01 General Requirements": 0.0}) == 0.0


def test_a_declared_zero_is_zero_not_five():
    assert contingency_pct_for(_cat(pct=0), None) == 0.0
    assert contingency_pct_for(_cat(), None) == 5.0


def test_an_edit_rollup_adds_no_contingency_where_the_set_says_none():
    payload = {"categories": [_cat(), _cat("09 Finishes", lines=(2000,))]}
    rollup_payload(payload, {"01 General Requirements": 0.0, "09 Finishes": 5.0})
    gen, fin = payload["categories"]
    assert (gen["contingencyAmount"], gen["total"]) == (0, 1000)
    assert not any(i.get("contingency") for i in gen["items"])
    assert (fin["contingencyAmount"], fin["total"]) == (100, 2100)


def test_a_stale_contingency_line_is_dropped_when_it_goes_to_zero():
    cat = _cat()
    cat["items"].append({"name": "Trade contingency @ 5%", "labor": 0, "materials": 50, "contingency": True})
    rollup_category(cat, {"01 General Requirements": 0.0})
    assert not any(i.get("contingency") for i in cat["items"]) and cat["total"] == 1000


def test_rerunning_does_not_stack():
    cat = _cat("09 Finishes")
    rollup_category(cat, None)
    rollup_category(cat, None)
    assert sum(1 for i in cat["items"] if i.get("contingency")) == 1 and cat["total"] == 1050


def test_version_totals_matches_home_arithmetic():
    from app.services.estimate_totals import version_totals

    t = version_totals({"overhead": 5, "profit": 8, "categories": [
        {"name": "A", "total": 10000}, {"name": "B", "total": 5000},
        {"name": "Hot tub", "total": 9000, "optional": True}]})
    # 15000 * 1.05 * 1.08 = 17010
    assert (t["subtotal"], t["overhead"], t["profit"], t["before_gst"]) == (15000, 750, 1260, 17010)
    # 17010 x 0.05 = 850.5; rounds to even, exactly as Home always has.
    assert (t["gst"], t["with_gst"], t["optional_subtotal"]) == (850, 17860, 9000)


def test_the_parts_add_up_to_the_total():
    from app.services.estimate_totals import version_totals

    # The live case: 1234 v6.
    t = version_totals({"overhead": 12, "profit": 15, "categories": [{"name": "A", "total": 317956}]})
    assert t["subtotal"] + t["overhead"] + t["profit"] == t["before_gst"]
    assert t["before_gst"] + t["gst"] == t["with_gst"]
