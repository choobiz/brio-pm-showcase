"""Every estimate line linked to the library rate that priced it (phase 3).

Amir, 2026-10-16: pricing must be "clearly linked to what rate in the library
they're being connected to". Before this, zero lines on 29 estimate versions
named a rate: the model wrote every number and nothing recorded its source.
Approved rules tested here: the library price wins on a linked line, the AI's
figure is kept beside it, a gap over 10% is flagged, a bad link is refused,
and the prompt's labour is the loaded rate (wage x 1.14).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.prep import AppSetting
from app.models.rate_library import LabourRate, MaterialRate, RateAssembly, RateEntry
from app.services import rate_links as RL


@pytest.fixture
def lib(db_session):
    db = db_session
    db.merge(AppSetting(key="labour_burden_pct", value={"pct": 14}))
    db.add(LabourRate(trade="flooring-installer", trade_key="flooring", source="workbc",
                      base_rate=Decimal("39.20"), effective_rate=Decimal("39.20"), discount_pct=0,
                      created_by="t"))
    lvp = RateAssembly(code="T-FL-LVP", name="LVP, floating", trade="flooring", trade_key="flooring",
                       unit="sf", status="active")
    draft = RateAssembly(code="T-FL-DRAFT", name="draft", trade="flooring", unit="sf", status="draft")
    db.add_all([lvp, draft])
    db.flush()
    db.add_all([
        RateEntry(assembly_id=lvp.id, unit_rate=Decimal("6.49"), unit="sf", source_type="composed",
                  confidence="fallback", as_of_date=date(2026, 1, 1),
                  payload={"material": 5.38, "labour": 1.12, "hours_per_unit": 0.025}),
        RateEntry(assembly_id=draft.id, unit_rate=Decimal("1"), unit="sf", source_type="composed"),
    ])
    m = MaterialRate(name="T 5/8 board (4x8)", trade="drywall", trade_key="drywall", unit="sheet",
                     unit_cost=Decimal("28"), measure_unit="sf", coverage=Decimal("32"),
                     waste_pct=Decimal("10"), source="market", created_by="t")
    db.add(m)
    db.flush()
    return {"cat": RL.catalog(db), "board": RL.material_code(m.id)}


def test_the_catalogue_codes_every_rate_with_loaded_labour_and_no_drafts(lib):
    c = lib["cat"]
    assert c["L:flooring-installer"]["rate"] == 44.69  # 39.20 x 1.14
    # The split (5.38 + 1.12 = 6.50) is scaled to the approved 6.49, so qty x rate is exact.
    assert c["T-FL-LVP"]["rate"] == 6.49
    assert round(c["T-FL-LVP"]["material_per_unit"] + c["T-FL-LVP"]["labour_per_unit"], 6) == 6.49
    assert c[lib["board"]]["unit"] == "sf" and c[lib["board"]]["rate"] == 0.9625
    assert "T-FL-DRAFT" not in c  # a draft can't be cited, so it can't price


def test_a_linked_line_is_priced_from_the_library_and_keeps_the_ai_figure(lib):
    it = {"name": "LVP main floor", "qty": 640, "unit": "sf", "labor": 900, "materials": 3900,
          "rate_ref": "T-FL-LVP"}
    link = RL.link_item(it, lib["cat"])
    assert link["status"] == "linked" and it["basis"] == "library"
    assert (it["materials"], it["labor"], it["unitCost"]) == (3437.9, 715.7, 6.49)  # = 640 x 6.49
    assert it["laborHours"] == 16.0
    assert link["ai"]["materials"] == 3900 and link["rate"]["code"] == "T-FL-LVP"
    assert link["gap_pct"] == 15.6 and link["flagged"] is True  # 4800 vs 4153.60


def test_a_small_gap_is_not_flagged(lib):
    it = {"name": "LVP", "qty": 100, "unit": "sf", "labor": 110, "materials": 560, "rate_ref": "T-FL-LVP"}
    link = RL.link_item(it, lib["cat"])
    assert link["gap_pct"] == 3.2 and link["flagged"] is False


def test_an_unknown_code_or_a_wrong_unit_is_refused_and_keeps_the_ai_price(lib):
    bad = {"name": "x", "qty": 10, "unit": "sf", "labor": 10, "materials": 20, "rate_ref": "NOPE"}
    assert RL.link_item(bad, lib["cat"])["status"] == "refused"
    assert (bad["labor"], bad["materials"], bad["basis"]) == (10, 20, "allowance")
    door = {"name": "door", "qty": 3, "unit": "ea", "labor": 300, "materials": 400, "rate_ref": "T-FL-LVP"}
    link = RL.link_item(door, lib["cat"])
    assert link["status"] == "refused" and "per sf" in link["reason"]
    labour_as_rate = {"name": "x", "qty": 1, "unit": "hr", "labor": 1, "materials": 0,
                      "rate_ref": "L:flooring-installer"}
    assert RL.link_item(labour_as_rate, lib["cat"])["status"] == "refused"


def test_square_yards_convert_and_a_takeoff_quantity_wins(lib):
    it = {"name": "LVP", "qty": 10, "unit": "sy", "labor": 0, "materials": 0, "rate_ref": "T-FL-LVP"}
    RL.link_item(it, lib["cat"])
    assert it["qty"] == 90 and it["unit"] == "sf"
    it2 = {"name": "LVP", "qty": 500, "unit": "sf", "labor": 0, "materials": 0, "rate_ref": "T-FL-LVP",
           "qty_source": "takeoff:floor_area_sf"}
    link = RL.link_item(it2, lib["cat"], {"floor_area_sf": {"value": 612.0, "unit": "sf"}})
    assert it2["qty"] == 612 and link["qty_source"] == "takeoff:floor_area_sf"


def test_a_material_link_prices_material_and_a_labour_link_prices_hours(lib):
    it = {"name": "Board", "qty": 320, "unit": "sf", "labor": 500, "materials": 400, "laborHours": 10,
          "rate_ref": lib["board"], "labour_ref": "L:flooring-installer"}
    RL.link_item(it, lib["cat"])
    assert it["materials"] == 308.0  # 320 x 0.9625
    assert it["labor"] == 446.9  # 10 h x 44.69


def test_an_unlinked_line_is_labelled_not_hidden(lib):
    it = {"name": "Heated floor mat", "qty": 1, "labor": 300, "materials": 600, "basis": "quote"}
    link = RL.link_item(it, lib["cat"])
    assert link["status"] == "unlinked" and it["basis"] == "quote" and it["materials"] == 600


def test_category_totals_follow_the_library_prices(lib):
    cats = [{"name": "08 Flooring", "total": 4800, "items": [
        {"name": "LVP", "qty": 640, "unit": "sf", "labor": 900, "materials": 3900, "rate_ref": "T-FL-LVP"},
        {"name": "Mat", "qty": 1, "labor": 300, "materials": 600, "basis": "allowance"}]}]
    n = RL.link_categories(cats, lib["cat"])
    assert cats[0]["total"] == 5054  # 4153.60 + 900
    assert (n["linked"], n["unlinked"], n["flagged"], n["library_priced_usd"]) == (1, 1, 1, 4153.6)


def test_the_prompt_lists_codes_and_loaded_labour(db_session, lib):
    from app.services.rate_reference import rate_library_context, render_rate_reference

    for i in range(4):
        db_session.add(LabourRate(trade=f"t{i}", source="workbc", base_rate=Decimal("30"),
                                  effective_rate=Decimal("30"), discount_pct=0, created_by="t"))
    for i in range(10):
        db_session.add(MaterialRate(name=f"T mat {i}", unit="ea", unit_cost=Decimal("2"),
                                    source="market", created_by="t"))
    db_session.flush()
    ctx = rate_library_context(db_session)
    text = render_rate_reference(ctx, static_tail="")
    assert "flooring-installer [L:flooring-installer] 44.69" in text
    assert "[T-FL-LVP] $6.49/sf" in text and "T-FL-DRAFT" not in text
    assert f"[{lib['board']}]" in text and "$0.96/sf" in text
    assert "14% labour burden" in text and '"rate_ref"' in text


def test_a_labour_code_beside_an_assembly_does_not_price_its_labour_twice(lib):
    it = {"name": "LVP", "qty": 100, "unit": "sf", "labor": 300, "materials": 600, "laborHours": 5,
          "rate_ref": "T-FL-LVP", "labour_ref": "L:flooring-installer"}
    link = RL.link_item(it, lib["cat"])
    assert it["labor"] + it["materials"] == 649.0  # the assembly's 100 x 6.49, nothing added
    assert link["labour"] is None
