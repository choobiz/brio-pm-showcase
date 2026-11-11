"""One vocabulary for the rate and trade libraries (plan phase 1, 2026-10-15).

On prod that day: materials used 8 unit spellings with nothing converting a
$28 drywall sheet into a price per sf; assemblies used sqft and sf_floor while
the takeoff said sf; trades were named four ways; and the rate reconcile
multiplied a rate by a quantity without checking their units at all.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.services import library_vocab as V
from app.services import rate_reconcile as RR

# Every unit and trade name found on prod, 2026-10-15.
PROD_UNITS = ["sf", "each", "sheet", "gal", "bf", "box", "kg", "roll", "sqft", "sf_floor", "ea", "lf"]
PROD_TRADES = ["drywall", "finish-carpenter", "flooring-installer", "framing", "insulator", "painter",
               "plumber", "tilesetter", "cabinet-refinisher", "drywaller", "electrician",
               "electrician-apprentice", "framer", "gc-pm", "hvac", "labourer", "plumber-apprentice",
               "architecture", "engineering", "supplier"]


def test_every_unit_on_prod_resolves_and_unknown_units_are_refused():
    assert {V.normalize_unit(u) for u in PROD_UNITS} <= set(V.UNITS)
    assert V.normalize_unit("sqft") == V.normalize_unit("sf_floor") == "sf"
    assert V.normalize_unit("each") == "ea"
    with pytest.raises(V.UnknownUnit):
        V.normalize_unit("bundle-ish")


def test_every_trade_name_on_prod_has_one_key():
    keys = {n: V.normalize_trade(n) for n in PROD_TRADES}
    assert all(keys.values()), [n for n, k in keys.items() if not k]
    assert keys["plumber"] == keys["plumber-apprentice"] == "plumbing"
    assert keys["tilesetter"] == "tile" and keys["gc-pm"] == "general_conditions"


def test_every_residential_division_has_a_trade():
    used = {t.residential for t in V.TRADES if t.residential}
    assert used == {f"{n:02d}" for n in range(1, 19)}


def test_a_sheet_is_priced_per_sf_through_its_coverage():
    # 5/8" Type-X at $28 a 4x8 sheet, 10% waste: 28 / 32 x 1.10.
    usd, unit = V.cost_per_measure(Decimal("28"), "sheet", measure_unit="sf", coverage=Decimal("32"),
                                   waste_pct=Decimal("10"))
    assert unit == "sf" and usd == Decimal("0.9625")
    # Bought by the sf already: no coverage needed.
    assert V.cost_per_measure(Decimal("5"), "sf", measure_unit=None, coverage=None,
                              waste_pct=Decimal("7.5")) == (Decimal("5.375"), "sf")
    # A package with no coverage cannot be priced against a takeoff.
    assert V.cost_per_measure(Decimal("23"), "box", measure_unit="sf", coverage=None) is None


def test_the_reconcile_refuses_a_rate_and_a_quantity_in_different_units(monkeypatch):
    monkeypatch.setattr(RR, "RATE_BINDINGS", [{"assembly_code": "X", "quantity_type": "door_count",
                                               "category": "Doors", "mode": "override", "note": ""}])
    monkeypatch.setattr(RR, "_takeoff_quantities",
                        lambda db, pid: {"door_count": {"value": 3, "unit": "ea", "source": "cad"}})
    entry = SimpleNamespace(unit_rate=Decimal("32"), unit="sf", source_type="composed",
                            confidence="derived", source_ref=None, id=uuid4())
    monkeypatch.setattr(RR.rate_pricing, "resolve_best_entry", lambda db, **k: entry)
    cats = [{"name": "Doors", "total": 1000, "items": []}]
    _, report = RR.apply_rate_library(None, cats, uuid4(), project_type="residential")
    assert report[0]["applied"] is False and "unit mismatch" in report[0]["reason"]
    assert cats[0]["total"] == 1000


def test_square_yards_convert_to_square_feet_before_pricing(monkeypatch):
    monkeypatch.setattr(RR, "RATE_BINDINGS", [{"assembly_code": "X", "quantity_type": "carpet_sy",
                                               "category": "Flooring", "mode": "advisory", "note": ""}])
    monkeypatch.setattr(RR, "_takeoff_quantities",
                        lambda db, pid: {"carpet_sy": {"value": 10, "unit": "sy", "source": "cad"}})
    entry = SimpleNamespace(unit_rate=Decimal("2"), unit="sf", source_type="composed",
                            confidence="derived", source_ref=None, id=uuid4())
    monkeypatch.setattr(RR.rate_pricing, "resolve_best_entry", lambda db, **k: entry)
    _, report = RR.apply_rate_library(None, [{"name": "Flooring", "total": 500, "items": []}], uuid4())
    assert report[0]["qty"] == 90 and report[0]["authoritative"] == 180


@pytest.fixture
def admin(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="a@t.local", role="superadmin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_new_material_is_stored_in_the_shared_vocabulary(admin):
    r = admin.post("/rates/materials", json={"name": "5/8 Type-X (4x8)", "unit": "Sheets", "unit_cost": 28,
                                             "trade": "drywaller", "measure_unit": "sqft", "coverage": 32,
                                             "waste_pct": 10, "source": "market"})
    assert r.status_code == 201, r.text
    row = next(m for m in admin.get("/rates/materials").json() if m["id"] == r.json()["id"])
    assert (row["unit"], row["measure_unit"], row["trade_key"], row["division"]) == ("sheet", "sf", "drywall", "06")
    assert row["cost_per_measure"] == {"usd": 0.9625, "unit": "sf"}


def test_a_bad_unit_or_a_missing_coverage_is_refused(admin):
    r = admin.post("/rates/materials", json={"name": "x", "unit": "bundle-ish", "unit_cost": 1})
    assert r.status_code == 422 and "unknown unit" in r.text
    r = admin.post("/rates/materials", json={"name": "x", "unit": "sheet", "unit_cost": 1, "measure_unit": "sf"})
    assert r.status_code == 422 and "how many sf does one sheet cover" in r.text
    r = admin.post("/rates/assemblies", json={"code": f"T-{uuid4().hex[:6]}", "name": "x", "trade": "drywall",
                                              "unit": "sheet"})
    assert r.status_code == 422 and "not something a takeoff measures" in r.text


def test_the_trade_library_page_offers_exactly_the_shared_trades():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "trades.ts").read_text()
    block = src[src.index("TRADE_CATEGORY_LABELS"):src.index("};", src.index("TRADE_CATEGORY_LABELS"))]
    assert set(re.findall(r"(\w+):\s*\"", block)) == set(V.TRADE_KEYS)


def test_the_filing_script_files_what_it_can_and_lists_the_rest(db_session):
    import importlib.util
    from pathlib import Path

    from app.models.rate_library import LabourRate, MaterialRate

    spec = importlib.util.spec_from_file_location(
        "file_library_vocab", Path(__file__).resolve().parents[1] / "scripts" / "file_library_vocab.py")
    F = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(F)
    db = db_session
    db.add_all([
        MaterialRate(name='5/8" Type-X gypsum board (4x8)', trade="drywall", unit="sheet", unit_cost=28,
                     source="market", created_by="t"),
        MaterialRate(name="Interior door — solid-core", trade="finish-carpenter", unit="each", unit_cost=350,
                     source="market", created_by="t"),
        MaterialRate(name="Primer", trade="painter", unit="gal", unit_cost=42.5, source="market", created_by="t"),
        LabourRate(trade="gc-pm", region="metro-vancouver", project_type="any", source="workbc",
                   base_rate=75, discount_pct=0, effective_rate=75, created_by="t"),
    ])
    db.flush()
    changes, review = F.plan(db)
    got = {label: fields for _, label, fields in changes}
    board = got['5/8" Type-X gypsum board (4x8)']
    assert board["trade_key"][1] == "drywall" and board["category"][1] == "Board"
    assert board["measure_unit"][1] == "sf" and board["coverage"][1] == 32
    door = got["Interior door — solid-core"]
    assert door["unit"][1] == "ea" and door["trade_key"][1] == "windows_doors" and door["category"][1] == "Interior doors"
    assert "coverage" not in got["Primer"]  # a judgement, left for a person
    assert any("Primer" in r for r in review) and any("gc-pm" in r for r in review)
    F.apply(db, changes)
    m = db.query(MaterialRate).filter_by(name='5/8" Type-X gypsum board (4x8)').one()
    assert (m.measure_unit, m.coverage, m.trade_key) == ("sf", 32, "drywall")
    assert F.plan(db)[0] == []  # idempotent: a second run changes nothing


def test_the_trade_library_shows_the_same_division_as_the_rates():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "trades.ts").read_text()
    block = src[src.index("TRADE_DIVISION"):src.index("};", src.index("TRADE_DIVISION"))]
    got = {k: (None if v == "null" else v.strip('"')) for k, v in re.findall(r'(\w+):\s*("\d{2}"|null)', block)}
    assert got == {t.key: t.residential for t in V.TRADES}
