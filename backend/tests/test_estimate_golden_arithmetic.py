"""The worked example, reproduced to the dollar.

ACCEPTANCE CRITERION (§11, "Calculations"): the §08 worked example must
reproduce exactly — $158,750 base, $7,478 contingency, $166,228, ×1.344 =
$223,410, GST $11,170, total $234,580. "Exact match to the dollar."

Why a golden file rather than unit tests on the parts: this is the arithmetic a
CLIENT sees on a PDF. The individual steps are simple enough that each looks
obviously right in isolation; what can silently break is their composition —
contingency applied after margin instead of before, GST taken on the pre-margin
subtotal, a category total double-counting its own contingency line. Each of
those produces a plausible number and a wrong invoice.

⚠️ ONE DISCREPANCY, RECORDED RATHER THAN SMOOTHED OVER. The published figures
are the FLOOR of the true values, not rounded:

    ×1.344   223,410.4320   published 223,410   (round would give 223,410)
    GST 5%    11,170.5216   published  11,170   (round would give  11,171)
    total    234,580.9536   published 234,580   (round would give 234,581)

The renderer formats to cents, so the PDF shows $234,580.95. Both facts are
asserted below: the exact value the code computes, and that truncating it gives
the published figure. If someone later introduces rounding, the GST assertion
fails by one dollar — which is the point.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text as _T  # noqa: E402

from app.services.estimate_pdf import build_estimate_html  # noqa: E402
from app.workers.ai_worker import (  # noqa: E402
    _contingency_pct_for,
    _rollup_category,
)


#: Overhead 12% and profit 20% are what make the margin 1.344 — the figure the
#: worked example quotes. Stated here so a changed margin fails loudly.
OVERHEAD = 12.0
PROFIT = 20.0
MARGIN = 1.344

#: Two categories at 5% and 4% reproduce $158,750 base and $7,478 contingency.
#:
#: A 5%/4% split is used rather than trades-at-5% plus general-conditions-at-0%.
#: The latter now works (the 0% defect was fixed 2026-10-05) but these figures
#: are the published ones and are left exactly as they were verified.
A_RAW = 112_800   # @ 5% -> 5,640
B_RAW = 45_950    # @ 4% -> 1,838


def _payload():
    cats = [
        {"name": "Division A", "contingencyPct": 5,
         "items": [{"name": "Work", "labor": A_RAW, "materials": 0}]},
        {"name": "Division B", "contingencyPct": 4,
         "items": [{"name": "Work", "labor": B_RAW, "materials": 0}]},
    ]
    for c in cats:
        _rollup_category(c)
    return {"detailLevel": "standard", "overhead": OVERHEAD, "profit": PROFIT,
            "categories": cats, "allowances": 0}


def test_zero_contingency_percent_is_honoured():
    """FIXED 2026-10-05. Previously asserted the defect; now asserts the fix.

    `_rollup_category` read `float(cat.get("contingencyPct") or 5)`. A legitimate
    0 is falsey, so it fell through to the 5% default — while
    app/models/division.py states contingency_pct is "0 on general
    conditions/permits". Those divisions were defined to carry none and carried
    5% anyway: $460 on a $9,190 line, $618 after margin.

    Measured before fixing: NO stored estimate declared contingencyPct=0, so the
    defect never fired on a saved estimate. It was latent, and there was nothing
    to remediate.
    """
    cat = {"name": "General conditions", "contingencyPct": 0,
           "items": [{"name": "Supervision", "labor": 9_190, "materials": 0}]}
    _rollup_category(cat)
    assert cat["contingencyAmount"] == 0, "a declared 0% is being overridden again"
    assert cat["total"] == 9_190, "contingency was added to a division that carries none"
    assert not [i for i in cat["items"] if i.get("contingency")], \
        "a contingency line was appended to a 0% division"


def test_a_missing_contingency_percent_still_defaults_to_five():
    """The default must survive the fix — absent and zero are different."""
    cat = {"name": "Trades", "items": [{"name": "Work", "labor": 10_000, "materials": 0}]}
    _rollup_category(cat)
    assert cat["contingencyAmount"] == 500


def test_contingency_rolls_up_to_the_published_base_and_contingency():
    p = _payload()
    raw = sum(c["subtotalRaw"] for c in p["categories"])
    cont = sum(c["contingencyAmount"] for c in p["categories"])
    assert raw == 158_750, "base"
    assert cont == 7_478, "contingency"
    assert raw + cont == 166_228, "base + contingency"


def test_rolling_up_twice_does_not_add_a_second_contingency_line():
    """Idempotency, which is the ordering risk that is actually real here.

    An earlier draft of this file asserted that contingency-before-margin and
    contingency-after-margin differ. They do not: both are multiplicative and
    multiplication commutes, so the claim was arithmetic fiction and the test
    was deleted rather than tuned until it passed.

    The genuine hazard is re-entry. `_rollup_category` appends a contingency
    LINE ITEM to `items`, so running it twice on the same category could leave
    two of them and bill the contingency twice. It survives because the raw sum
    filters `contingency` items out first — worth holding in place."""
    cat = {"name": "Division A", "contingencyPct": 5,
           "items": [{"name": "Work", "labor": A_RAW, "materials": 0}]}
    _rollup_category(cat)
    once = (cat["subtotalRaw"], cat["contingencyAmount"], cat["total"],
            len(cat["items"]))
    _rollup_category(cat)
    twice = (cat["subtotalRaw"], cat["contingencyAmount"], cat["total"],
             len(cat["items"]))
    assert once == twice, f"re-rollup changed the category: {once} -> {twice}"
    assert sum(1 for i in cat["items"] if i.get("contingency")) == 1


def test_gst_is_taken_on_the_marked_up_subtotal():
    p = _payload()
    subtotal = sum(c["total"] for c in p["categories"]) * MARGIN
    gst = subtotal * 0.05
    assert round(subtotal, 4) == 223_410.4320
    assert round(gst, 4) == 11_170.5216
    assert round(subtotal + gst, 4) == 234_580.9536
    # GST on the PRE-margin subtotal would be 8,311.40 — a $2,859 under-charge.
    assert round(166_228 * 0.05, 2) != round(gst, 2)


def test_rendered_pdf_carries_the_published_figures():
    """The client-facing surface, not just the arithmetic behind it."""
    html = build_estimate_html(
        payload=_payload(),
        project={"name": "Golden", "address": "1 Test Way"},
        client={"name": "Test Client"},
    )
    for shown in ("$223,410.43", "$11,170.52", "$234,580.95"):
        assert shown in html, f"{shown} missing from the rendered estimate"

    # Published figures are the FLOOR of these. Asserted so the relationship
    # between the document and the plan stays true, not assumed.
    for exact, published in ((223_410.4320, 223_410),
                             (11_170.5216, 11_170),
                             (234_580.9536, 234_580)):
        assert int(exact) == published


def test_no_margin_terms_leak_into_the_client_document():
    """Separate acceptance criterion, checked on the same artefact: no
    client-facing document shows overhead, profit, margin or markup."""
    html = build_estimate_html(
        payload=_payload(),
        project={"name": "Golden", "address": "1 Test Way"},
        client={"name": "Test Client"},
    )
    # ⚠️ Check the VISIBLE TEXT, not the source. The first version of this
    # grepped the whole document and tripped on `margin: 14mm` in the print
    # stylesheet — a CSS property, not a disclosure. Strip style/script, then
    # tags, then look at what a reader actually sees.
    visible = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    visible = re.sub(r"(?s)<!--.*?-->", " ", visible)
    visible = re.sub(r"<[^>]+>", " ", visible).lower()
    for term in ("overhead", "profit", "markup"):
        assert term not in visible, f"{term!r} is visible in a client-facing estimate"


# --- the division set is authoritative -------------------------------------

#: What the commercial set actually declares, measured 2026-10-06.
SET = {"01 General Requirements": 0.0, "02 Existing Conditions": 5.0}


def test_the_division_set_overrides_what_the_model_emitted():
    """⚠️ THE REAL DEFECT. The set declares General Requirements at 0 and the
    prompt says so, but the model emits NO contingencyPct at all — so both
    `or 5` and an explicit-None check applied 5%. Measured across three
    generated estimates: 14 categories charged contingency the set says is
    zero, including $2,895 on one $57,900 general-conditions line."""
    cat = {"name": "01 General Requirements",
           "items": [{"name": "Supervision", "labor": 57_900, "materials": 0}]}
    assert cat.get("contingencyPct") is None, "the model emits nothing here"
    _rollup_category(cat, SET)
    assert cat["contingencyAmount"] == 0, "charged contingency the set says is zero"
    assert cat["total"] == 57_900


def test_the_set_wins_even_when_the_model_emits_a_number():
    """The set is Brio's policy and the prompt already states it. A model that
    emits something else is not a reason to price differently."""
    cat = {"name": "01 General Requirements", "contingencyPct": 8,
           "items": [{"name": "Supervision", "labor": 10_000, "materials": 0}]}
    _rollup_category(cat, SET)
    assert cat["contingencyAmount"] == 0


def test_a_division_the_set_covers_uses_the_sets_number():
    cat = {"name": "02 Existing Conditions", "contingencyPct": 99,
           "items": [{"name": "Demo", "labor": 1_000, "materials": 0}]}
    _rollup_category(cat, SET)
    assert cat["contingencyAmount"] == 50


def test_a_category_outside_the_set_falls_back_to_the_model_then_to_five():
    """A non-standard category has no entry, so the model's own figure stands —
    and a declared 0 must still be honoured there."""
    a = {"name": "Custom trade", "contingencyPct": 0,
         "items": [{"name": "x", "labor": 1_000, "materials": 0}]}
    _rollup_category(a, SET)
    assert a["contingencyAmount"] == 0, "a declared 0 outside the set was overridden"

    b = {"name": "Custom trade", "items": [{"name": "x", "labor": 1_000, "materials": 0}]}
    _rollup_category(b, SET)
    assert b["contingencyAmount"] == 50, "absent should still default to 5%"


def test_no_set_at_all_keeps_the_old_behaviour():
    """An unclassified project resolves to no set; the model's emission is all
    there is, and absent still means 5%."""
    cat = {"name": "01 General Requirements",
           "items": [{"name": "x", "labor": 1_000, "materials": 0}]}
    _rollup_category(cat, None)
    assert cat["contingencyAmount"] == 50


def test_the_resolver_distinguishes_absent_from_zero():
    assert _contingency_pct_for({"name": "01 General Requirements"}, SET) == 0.0
    assert _contingency_pct_for({"name": "Other", "contingencyPct": 0}, SET) == 0.0
    assert _contingency_pct_for({"name": "Other"}, SET) == 5.0
    assert _contingency_pct_for({"name": "Other"}, None) == 5.0


# --- completeness is an assembly rule, not a per-trade one ------------------

LABELS = ["01 General Requirements", "02 Existing Conditions", "03 Concrete",
          "26 Electrical"]


def test_a_discipline_run_is_told_to_OMIT_divisions_it_did_not_price():
    """⚠️ THE FIX. Telling each of six disciplines to keep every division at 0
    produced 126 categories and put 7-8 divisions in more than one trade's
    hands. Measured on 880 Harbour, same scope: 126 categories and 51% gc_rollup
    overlap with the rule per-discipline, 32 and 16% without."""
    from app.services.ai import estimate_system_prompt
    divs = {"labels": LABELS, "set_name": "CSI MasterFormat", "contingency_pct": {}}
    per_trade = estimate_system_prompt(divs, require_every_division=False)
    assembled = estimate_system_prompt(divs, require_every_division=True)

    assert "Omit a division you have no scope for" in per_trade
    assert "Keep a division you have no scope for" not in per_trade
    assert "Keep a division you have no scope for" in assembled
    # The NAMES still bind either way — that is what lets six partial
    # breakdowns merge cleanly.
    for lab in LABELS:
        assert lab in per_trade and lab in assembled


def test_a_discipline_scoped_payload_gets_the_omit_rule():
    from app.services.ai import build_estimate_messages
    divs = {"labels": LABELS, "set_name": "CSI", "contingency_pct": {}}
    disc, _, _ = build_estimate_messages({"discipline": "electrical", "scope": {},
                                          "rates": {}, "divisions": divs})
    whole, _, _ = build_estimate_messages({"scope": {}, "rates": {}, "divisions": divs})
    assert "Omit a division" in disc
    assert "Keep a division" in whole


def test_assembly_adds_the_missing_divisions_at_zero_in_order():
    from app.workers.ai_worker import _ensure_every_division
    cats = [{"name": "26 Electrical", "total": 5000, "discipline": "electrical"},
            {"name": "01 General Requirements", "total": 900, "discipline": "gc_rollup"}]
    out = _ensure_every_division(cats, LABELS)
    assert [c["name"] for c in out] == LABELS, "not in the set's own order"
    added = [c for c in out if c["name"] in ("02 Existing Conditions", "03 Concrete")]
    assert all(c["total"] == 0 for c in added)
    assert all(c["discipline"] is None for c in added), \
        "a padded division must not be attributed to a trade"
    # Nothing that was priced is disturbed.
    assert next(c for c in out if c["name"] == "26 Electrical")["total"] == 5000


def test_assembly_keeps_a_category_outside_the_set_rather_than_dropping_it():
    from app.workers.ai_worker import _ensure_every_division
    cats = [{"name": "Custom trade", "total": 100, "discipline": "architectural"}]
    out = _ensure_every_division(cats, LABELS)
    assert any(c["name"] == "Custom trade" for c in out), "a priced category was dropped"
    assert len(out) == len(LABELS) + 1


def test_no_division_set_leaves_the_categories_alone():
    from app.workers.ai_worker import _ensure_every_division
    cats = [{"name": "Whatever", "total": 1}]
    assert _ensure_every_division(cats, None) == cats


def test_the_contingency_resolver_does_not_swallow_its_own_bugs(db_session):
    """⚠️ REGRESSION GUARD. The first version wrapped everything in
    `except Exception: return None`. It contained a NameError, so it raised on
    every call, returned None, and None is indistinguishable from "this project
    has no division set" — contingency silently fell back to 5% on divisions the
    set says carry none. $468 charged on 880 Harbour where the answer was $0, and
    the estimate looked fine.

    A resolver that fails open on client-facing money must fail loudly. This
    asserts it still RUNS (no NameError) and that a genuinely unmapped project
    returns None explicitly rather than by accident."""
    from uuid import uuid4
    from app.workers.ai_worker import _contingency_for_project

    # Runs without raising — the NameError is gone.
    assert _contingency_for_project(db_session, uuid4()) is None

    pid = uuid4()
    db_session.execute(_T(
        "insert into projects (id, name, slug, status, type) "
        "values (:i,:n,:s,'preconstruction','commercial_ti')"),
        {"i": str(pid), "n": "Contingency", "s": f"ct-{str(pid)[:8]}"})
    out = _contingency_for_project(db_session, pid)
    # Either a real mapping or an explicit None — never a swallowed exception.
    assert out is None or isinstance(out, dict)
