"""The starter library (rate libraries plan, phase 2, 2026-10-16).

Three promises, each tested here:
- a staged assembly (draft or held) never prices anything until it is released;
- the 7,912-line supplier catalogue is searchable but never reaches the
  estimate prompt (every active material_rates row does, which is why the
  catalogue has its own table);
- price drift is reported from StatCan's index and never changes a price.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.models.catalogue import CatalogueItem, CostIndexPoint
from app.models.prep import AppSetting
from app.models.rate_library import LabourRate, MaterialRate, RateAssembly, RateEntry
from app.services import rate_pricing
from app.services.price_drift import SERIES, IndexTable


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def admin(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="a@t.local", role="superadmin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _assembly(db, code: str, status: str, rate: str = "10") -> RateAssembly:
    a = RateAssembly(code=code, name=code, trade="drywall", trade_key="drywall", unit="sf", status=status)
    db.add(a)
    db.flush()
    db.add(RateEntry(assembly_id=a.id, unit_rate=Decimal(rate), unit="sf", source_type="composed",
                     confidence="fallback", as_of_date=date(2026, 7, 7)))
    db.flush()
    return a


# ── Staged assemblies never price ──────────────────────────────────────────


def test_a_draft_or_held_assembly_never_prices(db_session, admin):
    _assembly(db_session, "T-DRAFT", "draft")
    _assembly(db_session, "T-HELD", "held")
    _assembly(db_session, "T-LIVE", "active")
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="T-DRAFT") is None
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="T-HELD") is None
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="T-LIVE") is not None
    r = admin.post("/rates/price", json={"assembly_code": "T-DRAFT", "quantity": 100})
    assert r.json() == {"priced": False, "assembly_code": "T-DRAFT"}


def test_the_review_list_shows_drafts_with_their_rate_and_the_live_list_does_not(admin, db_session):
    _assembly(db_session, "T-DRAFT2", "draft", "4.25")
    live = {r["code"] for r in admin.get("/rates/assemblies").json()}
    assert "T-DRAFT2" not in live
    drafts = {r["code"]: r for r in admin.get("/rates/assemblies?status=draft").json()}
    assert drafts["T-DRAFT2"]["unit_rate"] == 4.25 and drafts["T-DRAFT2"]["status"] == "draft"
    assert admin.get("/rates/assemblies?status=bogus").status_code == 422


# ── Catalogue: searchable, read-only, never prompted ───────────────────────


def test_price_list_units_are_read_or_left_alone_never_guessed():
    L = _script("load_renovo_catalogue")
    assert L.unit_and_cost("SHT", 21.0) == ("sheet", Decimal("21.0"))
    assert L.unit_and_cost("PC", 3.0)[0] == L.unit_and_cost("EACH", 3.0)[0] == "ea"
    assert L.unit_and_cost("MSF", 450.0) == ("sf", Decimal("0.4500"))  # $ per 1,000 sf
    assert L.unit_and_cost("YARD", 9.0) == (None, None)  # linear or square: a person decides
    assert L.trade_for("Drywall & Steel Framing", "Steel") == "framing"
    assert L.trade_for("Drywall & Steel Framing", "Tape and Bead") == "drywall"
    assert L.trade_for("Roofing & Membranes", "Tyvek") == "siding"


def test_the_loader_is_idempotent_and_search_needs_every_word(db_session, admin, tmp_path):
    import json

    L = _script("load_renovo_catalogue")
    f = tmp_path / "cat.json"
    f.write_text(json.dumps({"skus": [
        {"group": "Drywall & Steel Framing", "supplier_category": "Drywall", "item": "T1",
         "description": 'GYPSUM 5/8" TYPE X 4X8', "price": 28.5, "unit": "SHT"},
        {"group": "Drywall & Steel Framing", "supplier_category": "Steel", "item": "T2",
         "description": 'STUD 3-5/8" 25GA 10FT', "price": 8.0, "unit": "PC"},
    ]}))
    assert L.apply(db_session, L.rows_from(str(f))) == (2, 0)
    db_session.flush()
    assert L.apply(db_session, L.rows_from(str(f))) == (0, 0)
    db_session.flush()
    hits = admin.get("/rates/catalogue", params={"q": "type x gypsum"}).json()
    assert [i["sku"] for i in hits["items"]] == ["T1"]
    assert hits["items"][0]["unit"] == "sheet" and hits["items"][0]["division"] == "06"
    assert admin.get("/rates/catalogue", params={"q": "gypsum stud"}).json()["total"] == 0
    assert admin.get("/rates/catalogue", params={"trade": "framing"}).json()["items"][0]["sku"] == "T2"
    assert admin.post("/rates/catalogue", json={}).status_code == 405


def test_the_catalogue_never_reaches_the_estimate_prompt(db_session):
    from app.services.rate_reference import rate_library_context

    def names():
        ctx = rate_library_context(db_session) or {"materials": []}
        return {m["name"] for m in ctx["materials"]}

    before = names()
    for i in range(30):
        db_session.add(CatalogueItem(supplier="Renovo", sku=f"P{i}", description=f"PROMPT CANARY {i}",
                                     raw_unit="EA", raw_price=1, unit="ea", unit_cost=1))
    db_session.flush()
    assert names() == before
    assert not any("CANARY" in n for n in names())


# ── Drift: reported, never applied ─────────────────────────────────────────


def _index(points):
    return IndexTable({div: [(date.fromisoformat(q), v) for q, v in pts] for div, pts in points.items()})


def test_drift_compares_the_entry_quarter_with_the_latest():
    idx = _index({"Finishes": [("2025-01-01", 100.0), ("2025-04-01", 104.0), ("2026-04-01", 110.0)],
                  "Division composite": [("2025-01-01", 100.0), ("2026-04-01", 103.0)]})
    d = idx.drift(date(2025, 2, 14), "drywall", today=date(2026, 10, 16))  # drywall → 09 Finishes
    assert (d["division"], d["since"], d["pct"], d["stale"]) == ("Finishes", "2025-01-01", 10.0, True)
    small = idx.drift(date(2025, 5, 1), "painting", today=date(2025, 9, 1))
    assert small["pct"] == 5.8 and small["stale"] is True
    # A division StatCan doesn't publish for houses falls back to the composite.
    assert idx.drift(date(2025, 1, 5), "fire_protection", today=date(2025, 6, 1))["division"] == "Division composite"


def test_a_price_newer_than_the_index_or_with_no_date_is_said_so():
    idx = _index({"Division composite": [("2025-01-01", 100.0), ("2026-04-01", 103.0)]})
    new = idx.drift(date(2026, 7, 7), "roofing", today=date(2026, 10, 16))
    assert new["pct"] is None and "newer" in new["reason"] and new["stale"] is False
    undated = idx.drift(None, "roofing")
    assert undated["stale"] is True and undated["reason"] == "no price date"
    assert IndexTable({}).drift(date(2025, 1, 1), "roofing") is None


def test_the_index_refresh_adds_new_quarters_records_revisions_and_touches_no_price(db_session):
    R = _script("refresh_cost_index")
    m = MaterialRate(name="Drift canary", unit="sf", unit_cost=Decimal("5"), source="market",
                     as_of_date=date(2025, 1, 1), created_by="t")
    db_session.add(m)
    db_session.flush()
    pts = [("Finishes", date(2025, 1, 1), Decimal("100.0"), 1), ("Finishes", date(2026, 4, 1), Decimal("110.0"), 1)]
    assert R.apply(db_session, pts) == (2, 0)
    db_session.flush()
    assert R.apply(db_session, pts) == (0, 0)
    assert R.apply(db_session, [("Finishes", date(2026, 4, 1), Decimal("111.0"), 1)]) == (0, 1)
    db_session.flush()
    p = db_session.query(CostIndexPoint).filter_by(series=SERIES, ref_period=date(2026, 4, 1)).one()
    assert float(p.value) == 111.0 and p.payload["revised_from"] == 110.0
    db_session.refresh(m)
    assert m.unit_cost == Decimal("5")


# ── Seeding: loaded labour, Amir's stucco figure, held items stay held ─────


@pytest.fixture
def seeded(db_session, monkeypatch):
    S = _script("seed_starter_assemblies")
    db = db_session
    db.merge(AppSetting(key="labour_burden_pct", value={"pct": 14}))
    for occ, wage in (("drywaller", "39.84"), ("insulator", "45.99"), ("labourer", "38.50"), ("framer", "43.71")):
        db.add(LabourRate(trade=occ, source="workbc", base_rate=Decimal(wage), effective_rate=Decimal(wage),
                          discount_pct=0, created_by="t"))
    # As on prod: the finish carpenter carries a skill premium over the carpenter wage.
    db.add(LabourRate(trade="finish-carpenter", source="workbc", base_rate=Decimal("43.71"),
                      effective_rate=Decimal("49.96"), discount_pct=0, created_by="t"))
    db.add(MaterialRate(name="2x4 SPF #2 KD", unit="bf", unit_cost=Decimal("0.80"), waste_pct=Decimal("10"),
                        source="market", as_of_date=date(2026, 1, 1), created_by="t"))
    db.flush()
    monkeypatch.setattr(S, "STARTERS", [S.STARTERS[0]])  # FR-INT-2X4-8FT
    return S


def test_seeding_prices_labour_at_wage_plus_burden_and_holds_what_amir_has_not_confirmed(db_session, seeded):
    S = seeded
    drafts = Path(__file__).resolve().parents[2] / "docs" / "rate-table" / "installed-rates-DRAFT.json"
    if not drafts.exists():
        pytest.skip("supplier rate drafts are not part of the public snapshot")
    assert S.ensure_roofer(db_session) is True and S.ensure_roofer(db_session) is False
    rows = {r["code"]: r for r in S.plan(db_session, str(drafts))}
    assert len(rows) == 36  # 35 drafts + 1 starter
    dw = rows["DW-12-WALL-L4"]["payload"]
    assert dw["loaded_rate"] == 45.42 and dw["wage"] == 39.84  # 39.84 x 1.14
    assert rows["EXT-STUCCO-3COAT"]["unit_rate"] == Decimal("10.00")
    held = {c for c, r in rows.items() if r["status"] == "held"}
    assert held == {"STL-STUD-358-25GA", "STL-STUD-6-20GA", "STL-BULKHEAD", "RF-TPO-ADHERED", "RF-POLYISO-2IN"}
    frame = rows["FR-INT-2X4-8FT"]
    # 7.33 bf x $0.80 x 1.10 waste = $6.45, + 0.25 h x $49.83 = $12.46
    assert frame["payload"]["material"] == 6.45 and frame["unit_rate"] == Decimal("18.91")
    assert frame["confidence"] == "fallback" and "Unverified" in frame["payload"]["productivity_source"]

    first = S.apply(db_session, list(rows.values()))
    assert first["assemblies_added"] == 36 and first["entries_added"] == 36
    db_session.flush()
    assert S.apply(db_session, list(rows.values()))["unchanged"] == 36
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="DW-12-WALL-L4") is None


def test_release_activates_reviewed_drafts_and_refuses_held_ones(db_session, seeded):
    S = seeded
    rows = S.plan(db_session, None)
    S.apply(db_session, rows)
    db_session.flush()
    assert S.release(db_session, ["FR-INT-2X4-8FT"]) == 1
    db_session.flush()
    assert rate_pricing.resolve_best_entry(db_session, assembly_code="FR-INT-2X4-8FT") is not None
    _assembly(db_session, "T-HELD2", "held")
    with pytest.raises(SystemExit, match="held"):
        S.release(db_session, ["T-HELD2"])


def test_seeding_uses_the_effective_wage_so_a_trade_premium_is_kept(db_session, seeded, monkeypatch):
    fc = [s for s in _script("seed_starter_assemblies").STARTERS if s[3] == "finish-carpenter"][:1]
    monkeypatch.setattr(seeded, "STARTERS", fc)
    rates, _ = seeded.loaded_rates(db_session)
    assert rates["finish-carpenter"]["loaded"] == Decimal("56.95")  # 49.96 x 1.14, not 43.71 x 1.14


def test_recompose_reprices_a_live_assembly_only_when_named(db_session, seeded):
    S = seeded
    rows = S.plan(db_session, None)
    S.apply(db_session, rows)
    S.release(db_session, ["FR-INT-2X4-8FT"])
    db_session.flush()
    for r in rows:
        r["unit_rate"] += 1  # a corrected price
    assert S.apply(db_session, rows)["skipped_active"] == 1
    assert S.apply(db_session, rows, {"FR-INT-2X4-8FT"})["entries_added"] == 1
    db_session.flush()
    best = rate_pricing.resolve_best_entry(db_session, assembly_code="FR-INT-2X4-8FT")
    assert best.unit_rate == Decimal("19.91")
    assert db_session.query(RateAssembly).filter_by(code="FR-INT-2X4-8FT").one().status == "active"
