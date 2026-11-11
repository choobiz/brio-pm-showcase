"""Money › Costs: bills BRIO receives, and the budget per division.

Built 2026-10-30. project_costs existed since the Drive migration and had never
held a row. Rules pinned here:
  * a supplier's invoice number can be entered once per project (a second
    "Sample Lumber 12-345" is refused, whatever the spacing or case);
  * slips are refused before anything is written (future date, the total typed
    into GST, a division the project doesn't use);
  * the budget is the contract's base spread by an estimate's direct-cost
    proportions, to the cent, plus each APPROVED change order as its own line;
    1234's EST-10058 carries the 18 divisions' titles under old numbers
    ("11 Mechanical / HVAC" is division 12), matched by title;
  * entered bills never reach the estimate writer's cost benchmarks.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import costs as router  # noqa: E402
from app.services import actuals as actuals_svc  # noqa: E402
from app.services import costs as svc  # noqa: E402
from app.workers.ai_worker import _brio_cost_benchmarks  # noqa: E402

TITLES = [
    "General Conditions, Permits & Closeout", "Excavation, Demolition & Site Clearing", "Foundation & Concrete",
    "Rough Carpentry & Framing", "Doors, Frames, Hardware & Glazing", "Insulation, Drywall & Acoustic",
    "Painting & Finishes", "Flooring & Tile", "Millwork & Casework", "Specialties & Fire Detection",
    "Appliances & Equipment", "Mechanical / HVAC", "Plumbing & Fixtures", "Electrical & Low-Voltage", "Roofing",
    "Exterior Cladding, Siding & Trim", "Site Work", "Stormwater Management",
]
LABELS = [f"{i:02d} {t}" for i, t in enumerate(TITLES, 1)]


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    """contracts / audit_logs are migration-only; estimate_versions is mapped by id only."""
    db = db_session
    db.execute(_T("""CREATE TABLE IF NOT EXISTS audit_logs (
        id bigserial PRIMARY KEY, project_id uuid, table_name text, row_id uuid, action text,
        user_id uuid, diff jsonb, created_at timestamptz DEFAULT now())"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY, project_id uuid, version_number int, base_contract_value numeric,
        status text, payload jsonb)"""))
    # create_all doesn't add 0084's columns to a test table made before them.
    for col in ("gst numeric(12,2)", "invoice_number text", "created_by_id uuid"):
        db.execute(_T(f"ALTER TABLE project_costs ADD COLUMN IF NOT EXISTS {col}"))
    for col in ("project_id uuid", "version_number int", "label text", "payload jsonb"):
        db.execute(_T(f"ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS {col}"))
    if not db.execute(_T("SELECT 1 FROM project_type_division_sets WHERE project_type = 'residential_new'")).first():
        sid = uuid4()
        db.execute(_T("""INSERT INTO division_sets (id, code, name, standard, is_active)
                         VALUES (:i, :c, 'BRIO residential', 'brio', true)"""), {"i": sid, "c": f"t-{sid}"})
        for n, t in enumerate(TITLES, 1):
            db.execute(_T("""INSERT INTO divisions (id, set_id, number, title, sequence)
                             VALUES (:i, :s, :n, :t, :q)"""),
                       {"i": uuid4(), "s": sid, "n": f"{n:02d}", "t": t, "q": n})
        db.execute(_T("INSERT INTO project_type_division_sets (project_type, set_id) VALUES ('residential_new', :s)"),
                   {"s": sid})
    monkeypatch.setattr(actuals_svc, "site_today", lambda: date(2026, 10, 30))
    monkeypatch.setattr(svc, "site_today", lambda: date(2026, 10, 30))


def _user(db, role="manager"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _project(db, member):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, type) values (:i,'1234',:s,'in_progress','residential_new')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
               {"p": str(pid), "u": member.id})
    return pid


def _bill(**kw):
    b = {"kind": "supplier_invoice", "vendor": "Sample Lumber", "invoice_number": "12-345",
         "cost_date": date(2026, 10, 14), "amount": 4212.0, "gst": 210.60,
         "division": "04 Rough Carpentry & Framing"}
    b.update(kw)
    return router.BillIn(**b)


def _count(db, pid):
    return db.execute(_T("select count(*) from project_costs where project_id = :p"), {"p": str(pid)}).scalar()


def test_a_bill_is_saved_with_gst_apart_and_totals_by_division(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    router.add_bill(pid, _bill(), db_session, me)
    router.add_bill(pid, _bill(vendor="Sample Form Rentals", invoice_number="FR-88", amount=1500, gst=75,
                               division="03 Foundation & Concrete"), db_session, me)
    s = router.get_costs(pid, db_session, me)
    assert s["spent_before_gst"] == 5712.0 and s["gst"] == 285.6
    by = {d["division"]: d for d in s["by_division"]}
    assert by["04 Rough Carpentry & Framing"]["spent"] == 4212.0
    assert by["04 Rough Carpentry & Framing"]["budget"] is None
    assert s["divisions"] == LABELS
    src = db_session.execute(_T("select payload->>'source' from project_costs where project_id = :p limit 1"),
                             {"p": str(pid)}).scalar()
    assert src == "entered"
    assert db_session.execute(_T("select count(*) from audit_logs where project_id = :p and action = 'cost-entered'"),
                              {"p": str(pid)}).scalar() == 2


def test_the_same_supplier_invoice_twice_is_refused(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    first = router.add_bill(pid, _bill(), db_session, me)
    with pytest.raises(HTTPException) as e:
        router.add_bill(pid, _bill(vendor=" sample lumber ", invoice_number="12 345"), db_session, me)
    assert e.value.status_code == 409
    # Another supplier may use the same number, and editing the bill doesn't trip on itself.
    router.add_bill(pid, _bill(vendor="Sample Form Rentals"), db_session, me)
    router.edit_bill(pid, first["id"], router.BillIn(amount=4300.0, gst=215.0), db_session, me)
    assert _count(db_session, pid) == 2


@pytest.mark.parametrize("kw, words", [
    ({"cost_date": date(2026, 11, 2)}, "future"),
    ({"gst": 4422.6}, "15%"),
    ({"division": "Structural"}, "divisions"),
    ({"vendor": "  "}, "who"),
    ({"amount": 0}, "zero"),
    ({"amount": -500.0, "gst": 25.0}, "sign"),
])
def test_slips_are_refused_and_nothing_is_written(db_session, kw, words):
    me = _user(db_session)
    pid = _project(db_session, me)
    with pytest.raises(HTTPException) as e:
        router.add_bill(pid, _bill(**kw), db_session, me)
    assert e.value.status_code == 422 and words in e.value.detail
    assert _count(db_session, pid) == 0


def test_a_credit_note_is_allowed(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    router.add_bill(pid, _bill(invoice_number="CR-1", amount=-300.0, gst=-15.0), db_session, me)
    assert router.get_costs(pid, db_session, me)["spent_before_gst"] == -300.0


def test_delete_is_logged_with_what_was_removed(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    b = router.add_bill(pid, _bill(), db_session, me)
    router.delete_bill(pid, b["id"], db_session, me)
    assert _count(db_session, pid) == 0
    diff = db_session.execute(_T("select diff from audit_logs where action = 'cost-deleted' and project_id = :p"),
                              {"p": str(pid)}).scalar()
    assert diff["before"]["vendor"] == "Sample Lumber"


# ── Budget ────────────────────────────────────────────────────────────────

EST_10058 = {  # 1234's v2, direct cost per category (labor + materials), old numbering
    "01 General Conditions, Permits & Closeout": 10710, "02 Excavation, Demolition & Site Clearing": 16524,
    "03 Foundation & Concrete": 28625, "04 Rough Carpentry & Framing": 28040,
    "05 Doors, Frames, Hardware & Glazing": 17745, "06 Insulation, Drywall & Acoustic": 17966,
    "07 Painting & Finishes": 2625, "08 Flooring & Tile": 3243, "09 Millwork & Casework": 4200,
    "10 Specialties & Fire Detection": 2446, "11 Mechanical / HVAC": 1350, "12 Plumbing & Fixtures": 7245,
    "13 Electrical & Low-Voltage": 11880, "14 Roofing": 10627, "15 Exterior Cladding, Siding & Trim": 17955,
    "16 Stormwater & Site Work": 27324,
}


def _contract_1234(db, pid):
    db.execute(_T("insert into contracts (id, project_id, version_number, base_contract_value, status) "
                  "values (:i, :p, 1, 170214, 'executed')"), {"i": uuid4(), "p": str(pid)})
    for n, title, st, amt in [(3, "Exterior cladding upgrade — NewTechWood horizontal shiplap", "approved", 10200),
                              (4, "Exterior remediation — soffit, flashing and front walkway", "draft", 19530)]:
        db.execute(_T("insert into change_orders (id, project_id, number, title, status, cost_impact) "
                      "values (:i, :p, :n, :t, :s, :a)"),
                   {"i": uuid4(), "p": str(pid), "n": n, "t": title, "s": st, "a": amt})
    vid = uuid4()
    cats = [{"name": k, "items": [{"name": "x", "labor": v * 0.4, "materials": v * 0.6}]} for k, v in EST_10058.items()]
    db.execute(_T("insert into estimate_versions (id, project_id, version_number, label, payload) "
                  "values (:i, :p, 2, 'v2 — EST-10058', CAST(:j AS jsonb))"),
               {"i": vid, "p": str(pid), "j": json.dumps({"categories": cats})})
    return vid


def test_budget_spreads_the_contract_to_the_cent_and_adds_approved_change_orders(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    vid = _contract_1234(db_session, pid)
    body = router.BudgetIn(estimate_version_id=vid)

    p = router.preview_budget(pid, body, db_session, me)
    cats = {c["name"]: c for c in p["categories"]}
    assert cats["11 Mechanical / HVAC"]["division"] == "12 Mechanical / HVAC"  # title, not number
    assert cats["15 Exterior Cladding, Siding & Trim"]["division"] == "16 Exterior Cladding, Siding & Trim"
    assert cats["16 Stormwater & Site Work"]["division"] is None  # no such title: the office picks
    assert [c["number"] for c in p["change_orders"]] == [3]  # the draft CO-4 is not budget
    assert p["change_orders"][0]["division"] == "16 Exterior Cladding, Siding & Trim"
    assert not p["complete"]
    assert db_session.execute(_T("select count(*) from project_cost_budgets where project_id = :p"),
                              {"p": str(pid)}).scalar() == 0  # preview writes nothing

    with pytest.raises(HTTPException) as e:
        router.save_budget(pid, body, db_session, me)
    assert e.value.status_code == 422 and "16 Stormwater & Site Work" in e.value.detail

    body.mapping = {"16 Stormwater & Site Work": "18 Stormwater Management"}
    s = router.save_budget(pid, body, db_session, me)
    assert s["budget_total"] == 180414.0
    rows = s["budget_rows"]
    assert round(sum(r["amount"] for r in rows if r["source"].startswith("Contract")), 2) == 170214.0
    co = [r for r in rows if r["source"] == "CO-3"]
    assert co[0]["amount"] == 10200.0 and co[0]["division"] == "16 Exterior Cladding, Siding & Trim"
    by = {d["division"]: d for d in s["by_division"]}
    # Structural share: 28,040 of 208,505 direct cost.
    assert by["04 Rough Carpentry & Framing"]["budget"] == pytest.approx(170214 * 28040 / 208505, abs=0.01)

    # Saving again replaces, never doubles.
    s = router.save_budget(pid, body, db_session, me)
    assert s["budget_total"] == 180414.0


def test_spent_left_and_over(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    vid = _contract_1234(db_session, pid)
    router.save_budget(pid, router.BudgetIn(estimate_version_id=vid,
                                            mapping={"16 Stormwater & Site Work": "17 Site Work"}), db_session, me)
    router.add_bill(pid, _bill(division="07 Painting & Finishes", amount=5000.0, gst=250.0), db_session, me)
    by = {d["division"]: d for d in router.get_costs(pid, db_session, me)["by_division"]}
    paint = by["07 Painting & Finishes"]
    assert paint["over"] is True and paint["left"] == round(paint["budget"] - 5000, 2)


def test_no_contract_means_no_budget(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    vid = uuid4()
    db_session.execute(_T("insert into estimate_versions (id, project_id, version_number, payload) "
                          "values (:i, :p, 1, '{}'::jsonb)"), {"i": vid, "p": str(pid)})
    with pytest.raises(HTTPException) as e:
        router.preview_budget(pid, router.BudgetIn(estimate_version_id=vid), db_session, me)
    assert "No contract value" in e.value.detail


# ── Who can see it, and what it feeds ─────────────────────────────────────


def test_entered_bills_stay_out_of_the_estimate_writers_benchmarks(db_session):
    """Before: _brio_cost_benchmarks averaged every project_costs row per
    category, so the second lumber bill entered would start telling the
    estimator what framing 'costs'."""
    me = _user(db_session)
    pid = _project(db_session, me)
    router.add_bill(pid, _bill(), db_session, me)
    router.add_bill(pid, _bill(invoice_number="12-346"), db_session, me)
    assert not [b for b in _brio_cost_benchmarks(db_session) if b["category"] == "04 Rough Carpentry & Framing"]


def test_every_cost_route_is_office_only_and_checks_the_project(db_session):
    for route in router.router.routes:
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert require_office in deps, route.path
    with pytest.raises(HTTPException) as e:
        require_office(CurrentUser(id=str(uuid4()), email="crew@t.local", role="worker"))
    assert e.value.status_code == 403

    outsider = _user(db_session)
    pid = _project(db_session, _user(db_session))
    with pytest.raises(HTTPException) as e:
        router.get_costs(pid, db_session, outsider)
    assert e.value.status_code in (403, 404)
