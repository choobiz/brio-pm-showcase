"""Supplier price sheets: checked before saving, applied, undone (phase 4).

Approved by Amir 2026-10-16 ("Phase 4: Library Pages and Import"): nothing is
written until the check has been seen; a move over 25% is flagged; library
materials with a SKU on the sheet are offered (ticked) and keep their old
price as history; the last import can be undone; managers can import.
"""
from __future__ import annotations

import io
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.models.catalogue import CatalogueItem
from app.models.rate_library import MaterialRate
from app.services import catalogue_import as CI
from app.services import rate_pricing

SUP = "TestSupply"


@pytest.fixture
def manager(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="mgr@t.local", role="manager")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    buf.write("Acme Supply price list Nov 2026\n")  # a title line before the header
    for r in [["Item #", "Description", "UOM", "Price"], *rows]:
        buf.write(",".join(r) + "\n")
    return buf.getvalue().encode()


def _upload(client, data: bytes, name="sheet.csv", **form):
    return client.post("/rates/imports", files={"file": (name, data, "text/csv")},
                       data={"supplier": SUP, **form})


@pytest.fixture
def seeded(db_session):
    db_session.add_all([
        CatalogueItem(supplier=SUP, sku="A1", description="Board 4x8", raw_unit="SHT", raw_price=Decimal("20"),
                      unit="sheet", unit_cost=Decimal("20")),
        CatalogueItem(supplier=SUP, sku="B2", description="Tape", raw_unit="ROLL", raw_price=Decimal("6"),
                      unit="roll", unit_cost=Decimal("6")),
        CatalogueItem(supplier=SUP, sku="C3", description="Screws", raw_unit="BOX", raw_price=Decimal("23"),
                      unit="box", unit_cost=Decimal("23")),
    ])
    m = MaterialRate(name="T Board 4x8", sku="A1", unit="sheet", unit_cost=Decimal("20"), measure_unit="sf",
                     coverage=Decimal("32"), waste_pct=Decimal("10"), source="market", created_by="t")
    db_session.add(m)
    db_session.flush()
    return m


SHEET = _csv([["A1", "Board 4x8", "SHT", "$26.00"],     # +30%: changed, big move
              ["B2", "Tape", "ROLL", "6.30"],           # +5%: changed
              ["C3", "Screws", "BOX", "23"],            # unchanged
              ["D4", "Sealant", "TUBE", "9.99"],        # new
              ["E5", "Lath", "YARD", "4"],              # unit with no safe reading
              ["F6", "Blank", "EA", ""],                # no price
              ["D4", "Sealant again", "TUBE", "9.50"]])  # repeated SKU


def test_the_header_is_found_and_the_columns_guessed():
    headers, rows = CI.read_sheet("x.csv", SHEET)
    assert headers == ["Item #", "Description", "UOM", "Price"] and len(rows) == 7
    assert CI.guess_map(headers) == {"sku": 0, "description": 1, "unit": 2, "price": 3}


def test_the_check_sorts_every_row_and_writes_nothing(db_session, seeded, manager):
    r = _upload(manager, SHEET)
    assert r.status_code == 201, r.text
    c = r.json()["summary"]["counts"]
    assert (c["changed"], c["unchanged"], c["new"], c["problem"], c["big_move"]) == (2, 1, 1, 3, 1)
    probs = {x["sku"]: x["problem"] for x in manager.get(f"/rates/imports/{r.json()['id']}?bucket=problem").json()["rows"]}
    assert probs["E5"].startswith("unit not recognised") and probs["F6"] == "no price"
    assert probs["D4"].startswith("SKU repeated")
    assert db_session.query(CatalogueItem).filter_by(supplier=SUP, sku="A1").one().raw_price == Decimal("20")
    mats = r.json()["summary"]["materials"]
    assert [(m["sku"], m["old_cost"], m["new_cost"], m["default"]) for m in mats] == [("A1", 20.0, 26.0, True)]


def test_apply_updates_the_catalogue_and_only_ticked_materials_keeping_history(db_session, seeded, manager):
    imp = _upload(manager, SHEET).json()
    r = manager.post(f"/rates/imports/{imp['id']}/apply", json={"material_ids": [str(seeded.id)]})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == {"added": 1, "updated": 2, "confirmed": 1, "materials": 1, "skipped": 3}
    assert db_session.query(CatalogueItem).filter_by(supplier=SUP, sku="A1").one().raw_price == Decimal("26")
    best = rate_pricing.best_material_rate(db_session, name="T Board 4x8")
    assert best.unit_cost == Decimal("26") and best.source == "supplier" and best.supplier == SUP
    db_session.refresh(seeded)
    assert seeded.is_active is False and seeded.superseded_by == best.id  # the old price is kept
    assert manager.post(f"/rates/imports/{imp['id']}/apply", json={}).status_code == 409


def test_an_unticked_material_is_left_alone(db_session, seeded, manager):
    imp = _upload(manager, SHEET).json()
    manager.post(f"/rates/imports/{imp['id']}/apply", json={"material_ids": []})
    assert rate_pricing.best_material_rate(db_session, name="T Board 4x8").unit_cost == Decimal("20")


def test_undo_puts_every_price_back_and_removes_added_lines(db_session, seeded, manager):
    imp = _upload(manager, SHEET).json()
    manager.post(f"/rates/imports/{imp['id']}/apply", json={"material_ids": [str(seeded.id)]})
    r = manager.post(f"/rates/imports/{imp['id']}/undo")
    assert r.status_code == 200, r.text
    assert r.json()["undone"] == {"removed": 1, "restored": 3, "materials": 1}
    assert db_session.query(CatalogueItem).filter_by(supplier=SUP, sku="D4").count() == 0
    a1 = db_session.query(CatalogueItem).filter_by(supplier=SUP, sku="A1").one()
    assert (a1.raw_price, a1.as_of_date) == (Decimal("20"), None)
    assert rate_pricing.best_material_rate(db_session, name="T Board 4x8").unit_cost == Decimal("20")
    assert manager.post(f"/rates/imports/{imp['id']}/undo").status_code == 409


def test_only_the_latest_import_can_be_undone(db_session, seeded, manager):
    first = _upload(manager, SHEET).json()
    manager.post(f"/rates/imports/{first['id']}/apply", json={})
    second = _upload(manager, _csv([["A1", "Board 4x8", "SHT", "27"]])).json()
    manager.post(f"/rates/imports/{second['id']}/apply", json={})
    r = manager.post(f"/rates/imports/{first['id']}/undo")
    assert r.status_code == 409 and "later import" in r.text


def test_a_sheet_whose_columns_cant_be_guessed_asks_for_a_map(db_session, manager):
    data = b"Col A,Col B,Col C,Col D\nX1,Thing,EA,5\n"
    r = _upload(manager, data)
    assert r.json()["status"] == "needs_map" and r.json()["headers"] == ["Col A", "Col B", "Col C", "Col D"]
    r = _upload(manager, data, column_map=json.dumps({"sku": 0, "description": 1, "unit": 2, "price": 3}))
    assert r.status_code == 201 and r.json()["summary"]["counts"]["new"] == 1


def test_an_xlsx_sheet_reads_the_same(db_session, manager):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in [["SKU", "Description", "Unit", "Price"], ["X9", "Bead", "PC", 1.25]]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    r = manager.post("/rates/imports", files={"file": ("s.xlsx", buf.getvalue(), "application/vnd.ms-excel")},
                     data={"supplier": SUP})
    assert r.status_code == 201 and r.json()["summary"]["counts"]["new"] == 1


def test_crew_cannot_import(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=str(uuid4()), email="c@t.local", role="crew")
    try:
        r = TestClient(app).post("/rates/imports", files={"file": ("s.csv", SHEET, "text/csv")}, data={"supplier": SUP})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_an_assembly_can_be_released_or_retired_with_a_log(db_session, manager):
    from app.models.rate_library import RateAssembly

    db_session.add(RateAssembly(code="T-REL", name="x", trade="drywall", unit="sf", status="draft"))
    db_session.flush()
    r = manager.patch("/rates/assemblies/T-REL", json={"status": "active"})
    assert r.json() == {"code": "T-REL", "status": "active", "was": "draft"}
    assert manager.patch("/rates/assemblies/T-REL", json={"status": "gone"}).status_code == 422
    a = db_session.query(RateAssembly).filter_by(code="T-REL").one()
    assert a.payload["status_log"][0]["by"] == "mgr@t.local"
