"""Review fixes to an estimate update the rate library (phase 3).

Amir, 2026-10-16: "while we are fixing the estimate, we're always also going
to update the rate libraries for material and rate as well". Approved: the save
lists each rate a fix implies, ticked by default; "this job only" needs a
reason; nothing is overwritten (new "own" rows supersede, history stays).
"""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser
from app.models.prep import AppSetting
from app.models.rate_library import LabourRate, MaterialRate, RateAssembly, RateEntry
from app.services import rate_links as RL
from app.services import rate_pricing
from app.services import rate_writeback as WB


@pytest.fixture
def est(db_session):
    db = db_session
    db.merge(AppSetting(key="labour_burden_pct", value={"pct": 14}))
    db.add(LabourRate(trade="tilesetter", trade_key="tile", source="workbc", base_rate=Decimal("40"),
                      effective_rate=Decimal("40"), discount_pct=0, created_by="t"))
    a = RateAssembly(code="W-FL-LVP", name="LVP", trade="flooring", trade_key="flooring", unit="sf", status="active")
    db.add(a)
    db.flush()
    db.add(RateEntry(assembly_id=a.id, unit_rate=Decimal("6.50"), unit="sf", source_type="composed",
                     confidence="fallback", as_of_date=date(2026, 1, 1),
                     payload={"material": 5.40, "labour": 1.10, "hours_per_unit": 0.025}))
    m = MaterialRate(name="W board (4x8)", trade="drywall", trade_key="drywall", unit="sheet",
                     unit_cost=Decimal("28"), measure_unit="sf", coverage=Decimal("32"),
                     waste_pct=Decimal("10"), source="market", created_by="t")
    db.add(m)
    db.flush()
    cat = RL.catalog(db)
    doc = {"categories": [
        {"name": "08 Flooring", "items": [
            {"name": "LVP main", "qty": 100, "unit": "sf", "labor": 0, "materials": 0, "rate_ref": "W-FL-LVP"},
            {"name": "Heated mat", "qty": 1, "unit": "ea", "labor": 300, "materials": 600, "basis": "allowance"},
        ]},
        {"name": "06 Drywall", "items": [
            {"name": "Board", "qty": 320, "unit": "sf", "labor": 400, "materials": 0, "laborHours": 10,
             "rate_ref": RL.material_code(m.id), "labour_ref": "L:tilesetter"},
        ]},
    ]}
    RL.link_categories(doc["categories"], cat)
    return doc


def test_an_untouched_estimate_proposes_nothing_but_a_priced_allowance(est):
    props = WB.proposals(est)
    assert [(p["kind"], p["default"]) for p in props] == [("new", False)]


def test_a_fixed_line_is_proposed_ticked_with_its_old_and_new_rate(est):
    doc = copy.deepcopy(est)
    lvp = doc["categories"][0]["items"][0]
    lvp["materials"], lvp["labor"] = 599.0, 111.0  # reviewer: LVP is really $7.10/sf
    p = next(p for p in WB.proposals(doc) if p["kind"] == "assembly")
    assert (p["code"], p["old_rate"], p["new_rate"], p["default"]) == ("W-FL-LVP", 6.5, 7.1, True)
    assert p["material_per_unit"] == 5.99


def test_writing_a_fix_makes_it_the_rate_the_next_estimate_uses(db_session, est):
    doc = copy.deepcopy(est)
    lvp = doc["categories"][0]["items"][0]
    lvp["materials"], lvp["labor"] = 599.0, 111.0
    s = WB.settle(db_session, doc, [{"key": "categories[0].items[0]", "write": True}],
                  source="EST-1 v3", user_email="amir@t.local")
    assert s["written"] == 1
    best = rate_pricing.resolve_best_entry(db_session, assembly_code="W-FL-LVP")
    assert best.source_type == "own" and float(best.unit_rate) == 7.1
    assert best.payload["evidence"]["old_rate"] == 6.5 and best.payload["evidence"]["by"] == "amir@t.local"
    # The starter entry is kept, not deleted.
    assert db_session.query(RateEntry).filter(RateEntry.source_type == "composed",
                                              RateEntry.unit_rate == Decimal("6.50")).count() == 1
    assert lvp["rate_link"]["writeback"]["status"] == "written"
    assert all(p["kind"] != "assembly" for p in WB.proposals(doc))  # saving again proposes nothing
    # A second fix supersedes the first "own" entry rather than piling up.
    lvp["materials"] = 620.0
    WB.settle(db_session, doc, [{"key": "categories[0].items[0]", "write": True}], source="EST-1 v4",
              user_email="amir@t.local")
    own = db_session.query(RateEntry).filter(RateEntry.source_type == "own").all()
    assert sorted(e.is_active for e in own) == [False, True]


def test_this_job_only_needs_a_reason_and_leaves_the_library_alone(db_session, est):
    doc = copy.deepcopy(est)
    doc["categories"][0]["items"][0]["materials"] = 700.0
    with pytest.raises(WB.DecisionError, match="needs a reason"):
        WB.settle(db_session, doc, [{"key": "categories[0].items[0]", "write": False}], source="x",
                  user_email="a")
    WB.settle(db_session, doc, [{"key": "categories[0].items[0]", "write": False, "reason": "tiny room"}],
              source="x", user_email="a")
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="W-FL-LVP").source_type == "composed"
    wb = doc["categories"][0]["items"][0]["rate_link"]["writeback"]
    assert wb["status"] == "job_only" and wb["reason"] == "tiny room"
    assert all(p["kind"] != "assembly" for p in WB.proposals(doc))


def test_a_save_with_no_decision_records_the_fix_as_pending(db_session, est):
    doc = copy.deepcopy(est)
    doc["categories"][0]["items"][0]["materials"] = 700.0
    s = WB.settle(db_session, doc, None, source="x", user_email="a")
    assert s["pending"] == 1
    assert doc["categories"][0]["items"][0]["rate_link"]["writeback"]["status"] == "pending"


def test_a_material_fix_stores_the_purchase_price_and_a_wage_fix_the_wage(db_session, est):
    doc = copy.deepcopy(est)
    board = doc["categories"][1]["items"][0]
    board["materials"] = 352.0  # $1.10/sf of board, waste in
    board["labor"] = 570.0  # 10 h at $57/hr loaded
    props = {p["kind"]: p for p in WB.proposals(doc)}
    assert props["material"]["new_rate"] == 1.1 and props["labour"]["new_rate"] == 57.0
    WB.settle(db_session, doc, [{"key": props["material"]["key"], "write": True},
                                {"key": props["labour"]["key"], "write": True}],
              source="x", user_email="a")
    m = rate_pricing.best_material_rate(db_session, name="W board (4x8)")
    assert m.source == "own" and m.unit_cost == Decimal("32.0000")  # 1.10 / 1.10 x 32 sf a sheet
    r = rate_pricing.best_labour_rate(db_session, trade="tilesetter")
    assert r.source == "own" and r.effective_rate == Decimal("50.00")  # 57 / 1.14


def test_a_priced_allowance_can_become_a_rate_in_its_trade(db_session, est):
    doc = copy.deepcopy(est)
    WB.settle(db_session, doc, [{"key": "categories[0].items[1]", "write": True}], source="x", user_email="a")
    a = db_session.query(RateAssembly).filter(RateAssembly.code == "OWN-HEATED-MAT").one()
    assert (a.trade_key, a.unit, a.status) == ("flooring", "ea", "active")
    assert float(rate_pricing.resolve_best_entry(db_session, assembly_code="OWN-HEATED-MAT").unit_rate) == 900


def test_the_save_endpoints_refuse_a_job_only_without_a_reason(db_session, est):
    from app.routers.workbench import _settle_rates

    doc = copy.deepcopy(est)
    doc["categories"][0]["items"][0]["materials"] = 700.0
    user = CurrentUser(id="u", email="a@t.local", role="manager")
    with pytest.raises(HTTPException) as e:
        _settle_rates(db_session, doc, {"rate_updates": [{"key": "categories[0].items[0]", "write": False}]},
                      user, "EST-1 v2")
    assert e.value.status_code == 422
