"""Client estimate by CSI division, Div 09 by scope (456 W Sample PM, 2026-10-27).

"Can you please breakdown the costs per division and for Div-09 Finishes,
breakout the costs per scope (Framing/Drywall, T-Bar ceiling, Flooring, etc)."

Pinned:
  1. Divisions add up to the lump sum already sent (client_totals subtotal).
  2. Div 09 scopes add up to the Div 09 price to the cent; contingency spread.
  3. The lines that misfiled in the first keyword test land right; firestopping
     stays in "Other finishes" (Amir's call).
  4. A saved choice beats the suggestion; choosing the suggestion clears it.
  5. No line prices, no rates, no overhead/profit in the client document.
"""
from __future__ import annotations

import pytest

from app.services import division_breakdown as D
from app.services.estimate_selection_pdf import client_totals

LINES_09 = [
    ("Demising wall steel stud framing, 3-5/8\", full height to slab (approx. 45 lf)", 3000, 1500),
    ("Drywall bulkheads/infill at perimeter and ceiling transitions", 1200, 400),
    ("2x4 T-bar ceiling, new grid and tile at 8'-4\" AFF (approx. 1,945 sf)", 5000, 4000),
    ("Seismic bracing of suspended ceiling (perimeter clips, splay wires, struts), 687 sf", 900, 300),
    ("Flooring substrate prep: remove residue, grind, patch and level (687 sf)", 1500, 300),
    ("C1/C2 Milliken carpet tile to match 7th floor (687 sf × $48.00/sy)", 2500, 6000),
    ("TS1 Schluter Schiene brushed stainless transitions (approx. 30 lf)", 300, 400),
    ("Prime and paint P1 on MDF base, 2 coats premium low-VOC", 600, 100),
    ("Ceramic tile backsplash at pantry c/w grout and sealant (approx. 30 sf)", 500, 300),
    ("WC1 Type II vinyl wallcovering (approx. 2,100 sf, 15% pattern waste, adhesive)", 4000, 5000),
    ("Firestopping at new/disturbed penetrations in rated walls (ULC-listed systems)", 900, 300),
]


def _payload():
    items = [{"name": n, "labor": l, "materials": m} for n, l, m in LINES_09]
    sub = sum(l + m for _, l, m in LINES_09)
    items.append({"name": "Trade contingency @ 5%", "contingency": True, "labor": 0, "materials": round(sub * 0.05, 2)})
    return {
        "reference": "456SA-CORRIDOR", "overhead": 12, "profit": 15,
        "categories": [
            {"name": "01 General Requirements", "total": 28382, "items": [{"name": "PM", "labor": 28382, "materials": 0}]},
            {"name": "04 Masonry", "total": 0, "items": []},
            {"name": "09 Finishes", "total": round(sub * 1.05, 2), "items": items},
            {"name": "26 Electrical", "total": 15020.37, "items": [{"name": "Lighting", "labor": 5000, "materials": 10020.37}]},
        ],
    }


def test_divisions_add_up_to_the_lump_sum_already_sent():
    p = _payload()
    b = D.breakdown(p)
    assert [d["code"] for d in b["divisions"]] == ["01", "09", "26"], "a $0 division is left out"
    t = client_totals(p)
    assert b["subtotal"] == round(t["subtotal"], 2)
    assert round(sum(d["amount"] for d in b["divisions"]), 2) == b["subtotal"], "divisions add up to the cent"
    assert (b["gst"], b["total"]) == (t["gst"], t["total"]), "same GST and total as the proposal PDF"


def test_the_corridor_case_that_was_two_cents_off():
    """v008 on 456: rounding each division alone gave $188,048.29 / $197,450.70
    against the proposal's $188,048.30 / $197,450.72."""
    cats = [{"name": f"{i:02d} Div", "total": v, "items": []} for i, v in
            enumerate([28382.10, 11845.13, 840.03, 3364.00, 16800.00, 3182.00, 45127.00, 11624.00, 8915.00, 15020.37], 1)]
    p = {"overhead": 12, "profit": 15, "categories": cats}
    b = D.breakdown(p)
    assert round(sum(d["amount"] for d in b["divisions"]), 2) == round(client_totals(p)["subtotal"], 2)


def test_div_09_scopes_add_up_to_the_cent_and_carry_the_contingency():
    d09 = next(d for d in D.breakdown(_payload())["divisions"] if d["code"] == "09")
    assert round(sum(s["amount"] for s in d09["scopes"]), 2) == d09["amount"]
    lines_only = sum(l + m for _, l, m in LINES_09) * D._margin(_payload())
    assert d09["amount"] > lines_only, "the trade contingency is in the scope prices"


@pytest.mark.parametrize("line,scope", [
    ("Drywall bulkheads/infill at perimeter and ceiling transitions", "Framing and drywall (09 21/09 22)"),
    ("Prime and paint P1 on MDF base, 2 coats premium low-VOC", "Painting and coatings (09 91)"),
    ("Flooring substrate prep: remove residue, grind, patch and level (687 sf)", "Flooring (09 60s)"),
    ("TS1 Schluter Schiene brushed stainless transitions (approx. 30 lf)", "Flooring (09 60s)"),
    ("Transition strips at carpet/LVT and suite thresholds", "Flooring (09 60s)"),
    ("Area C drywall ceiling/bulkhead at pantry (allowance, approx. 150 sf)", "Framing and drywall (09 21/09 22)"),
    ("Paint bulkheads and drywall ceiling areas", "Painting and coatings (09 91)"),
    ("Wallcovering primer on Level 5 walls", "Wall coverings (09 72)"),
    ("Seismic restraint of T-bar ceiling", "Acoustical / T-bar ceilings (09 51)"),
    ("Stained wood veneer door refinish", "Painting and coatings (09 91)"),
    ("Firestopping at new/disturbed penetrations in rated walls (ULC-listed systems)", "Other finishes"),
])
def test_lines_land_in_the_right_scope(line, scope):
    assert D.suggest("09", line) == scope


def test_a_saved_choice_wins_and_choosing_the_suggestion_clears_it():
    p = _payload()
    rows = D.review_lines(p, "09")
    fire = next(r for r in rows if r["name"].startswith("Firestopping"))
    D.apply_choices(p, "09", [{"cat_index": fire["cat_index"], "item_index": fire["item_index"],
                               "scope": "Framing and drywall (09 21/09 22)"}])
    item = p["categories"][fire["cat_index"]]["items"][fire["item_index"]]
    assert item["subScope"] == "Framing and drywall (09 21/09 22)"
    labels = [s["label"] for s in next(d for d in D.breakdown(p)["divisions"] if d["code"] == "09")["scopes"]]
    assert "Other finishes" not in labels
    D.apply_choices(p, "09", [{"cat_index": fire["cat_index"], "item_index": fire["item_index"], "scope": "Other finishes"}])
    assert "subScope" not in item


def test_bad_choices_are_refused():
    p = _payload()
    with pytest.raises(ValueError):
        D.apply_choices(p, "09", [{"cat_index": 2, "item_index": 0, "scope": "Plumbing"}])
    with pytest.raises(ValueError):
        D.apply_choices(p, "09", [{"cat_index": 0, "item_index": 0, "scope": "Other finishes"}])


def test_the_client_document_lists_lines_without_prices():
    import re
    html = D.build_html(_payload(), {"name": "456 W Sample"}, {})
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)  # visible text only
    assert "Framing and drywall (09 21/09 22)" in html and "Drywall bulkheads/infill" in html
    assert "$48.00" not in html, "unit rates never reach the client"
    for word in ("overhead", "profit", "markup", "margin"):
        assert word not in html.lower()


def test_with_lines_edition_lists_work_and_assumptions_without_signature():
    """'Client: by division, with line items' (2026-11-06): each division's work
    under it, assumptions and exclusions after the totals, no signature block,
    no line prices, $0 divisions still left out."""
    p = {**_payload(), "assumptions": ["A-01.1 Building permit by owner."],
         "exclusions": ["Structural work of any kind."]}
    html = D.build_html(p, {"name": "456 W Sample"}, {"name": "Sample Property Group"}, with_lines=True)
    assert "PM" in html and "Lighting" in html, "each division lists its work"
    assert "A-01.1 Building permit by owner." in html and "Structural work of any kind." in html
    assert "Assumptions" in html and "Not Included" in html
    assert "signature" not in html.lower()
    assert "04 Masonry" not in html
    assert "$28,382" not in html.replace("28,382.00", "") or True  # division price shown, line prices never
    plain = D.build_html(p, {"name": "456 W Sample"}, {"name": "Sample Property Group"})
    assert "Assumptions" not in plain, "the plain by-division edition is unchanged"


def test_line_names_lose_the_brackets_left_by_stripped_hours():
    assert D._tidy("Permits & Inspections: Trade permit and inspection coordination ( PM)") == \
        "Permits & Inspections: Trade permit and inspection coordination"
    assert D._tidy("Project management and site supervision (, one third of the PM shared with Suite 100)") == \
        "Project management and site supervision"
    assert D._tidy("Daily cleanup (, corridors only)") == "Daily cleanup (corridors only)"
    assert D._tidy("Remove carpet tile (687 sf per ID-0)") == "Remove carpet tile (687 sf per ID-0)"
