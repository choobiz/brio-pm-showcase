"""Contract › Billing plan: the signed stages, when each is billed, and the
schedule of values + cash flow built from them.

Built 2026-11-02 (Amir: dates from the LATEST schedule; money out = bills +
a per-project share of what's left of the budget, default 85%; the signed
stages' extra $0.28 stays as signed, with a warning). Before this, nothing in
the app made a schedule of values or a cash flow (both tables were empty in
prod), and regenerate-downstream would have rescaled any budget-built SOV by
estimate direct cost.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import billing_plan as router  # noqa: E402
from app.routers.workbench import regenerate_downstream  # noqa: E402
from app.services import actuals as actuals_svc  # noqa: E402
from app.services import billing_plan as svc  # noqa: E402
from app.services import costs as costs_svc  # noqa: E402

from tests.test_narrative_pdf import _weasyprint_available  # noqa: E402

# Checked once at collection: importing weasyprint late in a run on a Mac
# without pango segfaulted the whole suite (2026-11-02).
_WEASY = _weasyprint_available()

V7 = json.loads((Path(__file__).parent / "fixtures" / "schedule_sample_v7.json").read_text())
TITLES = [
    "General Conditions, Permits & Closeout", "Excavation, Demolition & Site Clearing", "Foundation & Concrete",
    "Rough Carpentry & Framing", "Doors, Frames, Hardware & Glazing", "Insulation, Drywall & Acoustic",
    "Painting & Finishes", "Flooring & Tile", "Millwork & Casework", "Specialties & Fire Detection",
    "Appliances & Equipment", "Mechanical / HVAC", "Plumbing & Fixtures", "Electrical & Low-Voltage", "Roofing",
    "Exterior Cladding, Siding & Trim", "Site Work", "Stormwater Management",
]
LABELS = {t: f"{i:02d} {t}" for i, t in enumerate(TITLES, 1)}
# The 1234 budget as previewed live on 30 Oct (v2 proportions, Stormwater line → 18).
BUDGET = {
    "01": 8743.16, "02": 13489.44, "03": 23368.15, "04": 22890.58, "05": 14486.21, "06": 14666.62,
    "07": 2142.93, "08": 2647.44, "09": 3428.69, "10": 1996.80, "12": 1102.08, "13": 5914.49,
    "14": 9698.29, "15": 8675.40, "16": 14657.65, "18": 22306.07,
}
STAGES = [  # as signed, CONTRACT-…-001
    {"pct": 10.0, "amount": 18041.9, "milestone": "Deposit — on signing", "detail": ""},
    {"pct": 20.0, "amount": 36082.86, "milestone": "Building permit issued — mobilization", "detail": ""},
    {"pct": 30.0, "amount": 54123.81, "milestone": "Foundation, framing & weathertight (lock-up)", "detail": ""},
    {"pct": 30.0, "amount": 54123.81, "milestone": "MEP rough-in, insulation, drywall & interior finishes",
     "detail": ""},
    {"pct": 10.0, "amount": 18041.9, "milestone": "Substantial completion & occupancy — holdback", "detail": ""},
]
TODAY = date(2026, 10, 30)
LOCKUP, DRYWALL = "A250", "A300"  # cladding done 3 Dec, drywall 14 Dec in v7


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    """schedules / activities / contracts / SOV / cash flows / billing_plans are migration-only."""
    db = db_session
    db.execute(_T("""CREATE TABLE IF NOT EXISTS schedules (
        id uuid PRIMARY KEY, project_id uuid, version_number int, project_start date, working_hours text,
        holidays jsonb, deterministic_finish_date date, p50_finish_date date, p80_finish_date date,
        project_expected_wd numeric, project_std_dev_wd numeric, critical_path_activity_codes jsonb,
        notes text, parent_version_id uuid, created_by_id uuid, is_baseline boolean DEFAULT false,
        client_visible boolean NOT NULL DEFAULT true, created_at timestamptz DEFAULT clock_timestamp())"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS activities (
        id uuid PRIMARY KEY, schedule_id uuid, activity_code text, name text, phase text,
        predecessor_codes jsonb, lag_wd int, optimistic_wd numeric, most_likely_wd numeric,
        pessimistic_wd numeric, expected_wd numeric, variance numeric, early_start_wd numeric,
        early_finish_wd numeric, late_start_wd numeric, late_finish_wd numeric, total_float_wd numeric,
        free_float_wd numeric, start_date date, finish_date date, is_critical boolean, is_milestone boolean)"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS audit_logs (
        id bigserial PRIMARY KEY, project_id uuid, table_name text, row_id uuid, action text,
        user_id uuid, diff jsonb, created_at timestamptz DEFAULT now())"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY, project_id uuid, version_number int, base_contract_value numeric,
        status text, payload jsonb)"""))
    for col in ("reference text", "signed_at timestamptz", "project_start_date date",
                "substantial_completion_date date", "substantial_completion_actual date",
                "based_on_estimate_id uuid", "option_contract_value numeric", "signed_copy_document_id uuid",
                "template_type text", "owner_name text", "owner_address text", "based_on_schedule_id uuid",
                "final_completion_date date", "notes text", "parent_version_id uuid", "created_by_id uuid"):
        db.execute(_T(f"ALTER TABLE contracts ADD COLUMN IF NOT EXISTS {col}"))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS schedules_of_values (
        id uuid PRIMARY KEY, project_id uuid, version_number int, scenario text, based_on_estimate_id uuid,
        based_on_schedule_id uuid, total_pretax numeric, months jsonb, rows jsonb, monthly_totals jsonb,
        cumulative_totals jsonb, lien_holdback_10pct jsonb, owner_net_monthly jsonb, notes text,
        parent_version_id uuid, created_at timestamptz DEFAULT now(), created_by_id uuid)"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS cash_flows (
        id uuid PRIMARY KEY, project_id uuid, version_number int, scenario text, based_on_estimate_id uuid,
        based_on_schedule_id uuid, owner_net_days int, lien_holdback_pct numeric,
        lien_release_days_post_substantial int, deficiency_days_post_occupancy int, total_pretax numeric,
        final_cash_position numeric, min_cash_position numeric, min_position_week_end date,
        bridge_financing_required numeric, weekly_periods jsonb, phase_costs jsonb, milestone_dates jsonb,
        key_dates jsonb, notes text, parent_version_id uuid, created_at timestamptz DEFAULT now(),
        created_by_id uuid)"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS billing_plans (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL UNIQUE, contract_id uuid,
        stage_activities jsonb NOT NULL DEFAULT '{}'::jsonb, division_phases jsonb NOT NULL DEFAULT '{}'::jsonb,
        cost_share_pct numeric(5,2) NOT NULL DEFAULT 85, owner_net_days int NOT NULL DEFAULT 0,
        created_by_id uuid, updated_by_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())"""))
    for col in ("gst numeric(12,2)", "invoice_number text", "created_by_id uuid"):
        db.execute(_T(f"ALTER TABLE project_costs ADD COLUMN IF NOT EXISTS {col}"))
    for col in ("project_id uuid", "version_number int", "label text", "payload jsonb", "is_active boolean"):
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
    for mod in (actuals_svc, svc, costs_svc):
        monkeypatch.setattr(mod, "site_today", lambda: TODAY)


def _user(db, role="manager"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _schedule(db, pid, version, shift: dict[str, int] | None = None):
    sid = uuid4()
    db.execute(_T("""insert into schedules (id, project_id, version_number, project_start, holidays, p50_finish_date)
                     values (:i, :p, :v, :ps, CAST(:h AS jsonb), :p50)"""),
               {"i": sid, "p": str(pid), "v": version, "ps": V7["project_start"],
                "h": json.dumps(V7["holidays"]), "p50": V7["p50_finish_date"]})
    for a in V7["activities"]:
        fin = date.fromisoformat(a["finish_date"]) + timedelta(days=(shift or {}).get(a["activity_code"], 0))
        db.execute(_T("""insert into activities (id, schedule_id, activity_code, name, phase, start_date,
                         finish_date, is_milestone) values (:i, :s, :c, :n, :ph, :sd, :fd, :m)"""),
                   {"i": uuid4(), "s": sid, "c": a["activity_code"], "n": a["name"], "ph": a["phase"],
                    "sd": a["start_date"], "fd": fin, "m": a["is_milestone"]})
    return sid


def _project_1234(db, member, budget=True):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, type) values (:i,'1234',:s,'in_progress','residential_new')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
               {"p": str(pid), "u": member.id})
    db.execute(_T("""insert into contracts (id, project_id, version_number, reference, base_contract_value, status,
                     payload, substantial_completion_date, signed_at)
                     values (:i, :p, 1, 'CONTRACT-1234-001', 170214, 'executed', CAST(:j AS jsonb), '2026-10-12',
                     '2026-07-31')"""),
               {"i": uuid4(), "p": str(pid), "j": json.dumps({"paymentSchedule": STAGES})})
    db.execute(_T("insert into change_orders (id, project_id, number, title, status, cost_impact) "
                  "values (:i, :p, 3, 'Exterior cladding upgrade', 'approved', 10200)"),
               {"i": uuid4(), "p": str(pid)})
    for num, ms, sub, d, paid in [
        ("INV-1234-001", "Deposit — on signing (10%)", 18041.9, "2026-08-02", datetime(2026, 8, 5, 19, tzinfo=timezone.utc)),
        ("INV-2026-10059", "Building permit issued — mobilization", 36082.86, "2026-10-06",
         datetime(2026, 10, 9, 19, tzinfo=timezone.utc)),
    ]:
        db.execute(_T("""insert into invoices (id, project_id, number, type, status, payload, paid_amount, paid_at)
                         values (:i, :p, :n, 'progress', 'paid', CAST(:j AS jsonb), :a, :pa)"""),
                   {"i": uuid4(), "p": str(pid), "n": f"{num}-{str(pid)[:6]}",
                    "j": json.dumps({"milestone": ms, "subtotal": sub, "date": d}), "a": sub * 1.05, "pa": paid})
    _schedule(db, pid, 7)
    if budget:
        titles = {f"{i:02d}": t for i, t in enumerate(TITLES, 1)}
        for n, amt in BUDGET.items():
            db.execute(_T("""insert into project_cost_budgets (id, project_id, division, amount, source)
                             values (:i, :p, :d, :a, 'Contract share')"""),
                       {"i": uuid4(), "p": str(pid), "d": f"{n} {titles[n]}", "a": amt})
        db.execute(_T("""insert into project_cost_budgets (id, project_id, division, amount, source, note)
                         values (:i, :p, :d, 10200, 'CO-3', 'Exterior cladding upgrade')"""),
                   {"i": uuid4(), "p": str(pid), "d": LABELS["Exterior Cladding, Siding & Trim"]})
    return pid


def _complete(**kw):
    b = {"stage_activities": {"3": LOCKUP, "4": DRYWALL},
         "division_phases": {LABELS["Excavation, Demolition & Site Clearing"]: "Site Work"}}
    b.update(kw)
    return router.PlanIn(**b)


def _n(db, table, pid):
    return db.execute(_T(f"select count(*) from {table} where project_id = :p"), {"p": str(pid)}).scalar()


def test_the_plan_reads_the_signed_stages_and_the_invoices_already_issued(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    v = router.get_plan(pid, db_session, me)
    s = v["stages"]
    assert [x["amount"] for x in s] == [18041.9, 36082.86, 54123.81, 54123.81, 18041.9]
    assert s[0]["invoice"]["number"].startswith("INV-1234-001") and s[0]["paid_date"] == "2026-08-05"
    assert s[1]["invoice"]["number"].startswith("INV-2026-10059")
    assert s[2]["needs_pick"] and s[3]["needs_pick"]
    # holdback: billed at the latest schedule's P50 finish, paid 55 days after
    assert s[4]["holdback"] and s[4]["bill_date"] == V7["p50_finish_date"]
    assert s[4]["paid_date"] == str(date.fromisoformat(V7["p50_finish_date"]) + timedelta(days=55))
    # signed stages are 0.28 over the contract + CO-3, and stay as signed
    assert v["stage_total"] == 180414.28 and v["stages_off_by"] == 0.28
    # phases matched by title: Site Work is 17, not 02 "... Site Clearing"; 02 needs a pick
    by = {d["division"]: d for d in v["divisions"]}
    assert by[LABELS["Excavation, Demolition & Site Clearing"]]["needs_pick"]
    assert by[LABELS["Rough Carpentry & Framing"]]["phases"] == ["Framing"]
    assert by[LABELS["General Conditions, Permits & Closeout"]]["whole_job"]
    assert not v["complete"] and len(v["problems"]) == 3


def test_saving_is_refused_until_every_stage_and_division_has_its_dates(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    with pytest.raises(HTTPException) as e:
        router.save_plan(pid, router.PlanIn(stage_activities={"3": LOCKUP}), db_session, me)
    assert e.value.status_code == 422 and "stage 4" in e.value.detail
    assert _n(db_session, "schedules_of_values", pid) == 0 and _n(db_session, "cash_flows", pid) == 0
    assert _n(db_session, "billing_plans", pid) == 0


def test_no_budget_means_no_plan(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me, budget=False)
    v = router.get_plan(pid, db_session, me)
    assert "Set the budget on Money › Costs first." in v["problems"]


def test_save_builds_a_schedule_of_values_that_adds_up_to_the_budget(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    out = router.save_plan(pid, _complete(), db_session, me)
    assert out["saved"]["sov"]["version_number"] == 1
    sov = db_session.execute(_T("select * from schedules_of_values where project_id = :p"),
                             {"p": str(pid)}).mappings().one()
    rows = sov["rows"]
    assert len(rows) == 17  # 16 divisions + CO-3
    for r in rows:
        assert round(sum(r["monthly"].values()), 2) == r["scheduledValue"], r["tradeNumber"]
    assert float(sov["total_pretax"]) == round(sum(BUDGET.values()) + 10200, 2)
    assert round(sum(sov["monthly_totals"].values()), 2) == float(sov["total_pretax"])
    by = {r["tradeNumber"]: r for r in rows}
    # 02 Excavation picked onto Site Work (5 Oct – 4 Dec): nothing in January
    assert by["02"]["monthly"]["Oct 2026"] > 0 and by["02"]["monthly"].get("Jan 2027", 0) == 0
    # General Conditions runs the whole job, so January has a share
    assert by["01"]["monthly"]["Jan 2027"] > 0 and by["01"]["monthly"]["Oct 2026"] > 0
    assert by["CO-3"]["division"] == LABELS["Exterior Cladding, Siding & Trim"]
    assert all(r["basis"] == "budget" for r in rows)


def test_save_builds_a_cash_flow_of_the_signed_stages_and_the_cost_share(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    db_session.execute(_T("""insert into project_costs (id, project_id, category, amount, gst, cost_date, kind,
                             vendor, payload) values (:i, :p, :d, 4212, 210.6, '2026-10-14', 'supplier_invoice',
                             'Sample Lumber', '{"source": "entered"}')"""),
                       {"i": uuid4(), "p": str(pid), "d": LABELS["Foundation & Concrete"]})
    router.save_plan(pid, _complete(cost_share_pct=85), db_session, me)
    cf = db_session.execute(_T("select * from cash_flows where project_id = :p"), {"p": str(pid)}).mappings().one()
    weeks = cf["weekly_periods"]
    assert float(cf["total_pretax"]) == 180414.28
    assert round(sum(w["draws_total"] for w in weeks), 2) == 180414.28
    # money out = bills entered + 85% of what's left of each division's budget
    expected_out = 4212 + sum(
        max(0.0, round(amt * 0.85 - (4212 if n == "03" else 0), 2)) for n, amt in BUDGET.items()
    ) + 10200 * 0.85
    assert round(sum(w["outflow_total"] for w in weeks), 2) == round(expected_out, 2)
    when = {d["milestone"]: w["week_end"] for w in weeks for d in w["draws"]}
    sc = date.fromisoformat(V7["p50_finish_date"])
    hb = date.fromisoformat(when["Substantial completion & occupancy — holdback"])
    assert hb - timedelta(days=6) <= sc + timedelta(days=55) <= hb
    lock = date.fromisoformat(when["Foundation, framing & weathertight (lock-up)"])
    assert lock - timedelta(days=6) <= date(2026, 12, 3) <= lock
    assert cf["key_dates"]["basis"] == "billing-plan" and float(cf["lien_holdback_pct"]) == 0
    assert float(cf["bridge_financing_required"]) == max(0.0, -float(cf["min_cash_position"]))


def test_nothing_signed_changes_and_resaving_keeps_the_old_versions(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    before = db_session.execute(_T("select base_contract_value, payload from contracts where project_id = :p"),
                                {"p": str(pid)}).one()
    router.save_plan(pid, _complete(), db_session, me)
    out = router.save_plan(pid, _complete(cost_share_pct=100), db_session, me)
    assert out["saved"]["sov"]["version_number"] == 2 and out["saved"]["cash_flow"]["version_number"] == 2
    after = db_session.execute(_T("select base_contract_value, payload from contracts where project_id = :p"),
                               {"p": str(pid)}).one()
    assert after == before
    v = db_session.execute(_T("""select version_number, parent_version_id, id from cash_flows
                                 where project_id = :p order by version_number"""), {"p": str(pid)}).all()
    assert v[1].parent_version_id == v[0].id
    assert db_session.execute(_T("""select count(*) from audit_logs where project_id = :p
                                    and action = 'billing-plan-saved'"""), {"p": str(pid)}).scalar() == 2


def test_dates_follow_the_latest_schedule_and_a_recorded_finish(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    router.save_plan(pid, _complete(), db_session, me)
    assert router.get_plan(pid, db_session, me)["stages"][2]["bill_date"] == "2026-12-03"
    _schedule(db_session, pid, 8, shift={LOCKUP: 7})  # a re-plan moves cladding a week
    assert router.get_plan(pid, db_session, me)["stages"][2]["bill_date"] == "2026-12-10"
    actuals_svc.record(db_session, pid, LOCKUP, {"status": "done", "actual_start": date(2026, 10, 20),
                                                 "actual_finish": date(2026, 10, 28)}, None)
    assert router.get_plan(pid, db_session, me)["stages"][2]["bill_date"] == "2026-10-28"


def test_substantial_completion_is_recorded_on_the_contract_but_never_in_the_future(db_session):
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    with pytest.raises(HTTPException):
        router.save_plan(pid, _complete(substantial_completion_actual=TODAY + timedelta(days=1)), db_session, me)
    with pytest.raises(HTTPException):
        router.save_plan(pid, _complete(cost_share_pct=0), db_session, me)
    out = router.save_plan(pid, _complete(substantial_completion_actual=date(2026, 10, 29)), db_session, me)
    assert out["contract"]["substantial_completion_actual"] == "2026-10-29"
    assert out["stages"][4]["paid_date"] == "2026-12-23"  # 29 Oct + 55 days
    # leaving it out of the next save keeps it
    out = router.save_plan(pid, _complete(), db_session, me)
    assert out["contract"]["substantial_completion_actual"] == "2026-10-29"


def test_regenerate_downstream_leaves_a_plan_built_sov_alone(db_session):
    """Old code rescaled every SOV row by the active estimate's trade totals
    (direct cost) — a budget-built row would have dropped to the estimate's
    number for that trade."""
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    db_session.execute(_T("""insert into estimate_versions (id, project_id, version_number, label, payload, is_active)
                             values (:i, :p, 3, 'v3', CAST(:j AS jsonb), true)"""),
                       {"i": uuid4(), "p": str(pid),
                        "j": json.dumps({"categories": [{"name": "04 Framing", "total": 1000}]})})
    router.save_plan(pid, _complete(), db_session, me)
    before = db_session.execute(_T("select rows, total_pretax from schedules_of_values where project_id = :p"),
                                {"p": str(pid)}).one()
    regenerate_downstream(pid, db_session, me)
    after = db_session.execute(_T("select rows, total_pretax from schedules_of_values where project_id = :p"),
                               {"p": str(pid)}).one()
    assert after == before


def test_every_billing_plan_route_is_office_only_and_checks_the_project(db_session):
    for route in router.router.routes:
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert require_office in deps, route.path
    with pytest.raises(HTTPException) as e:
        require_office(CurrentUser(id=str(uuid4()), email="crew@t.local", role="worker"))
    assert e.value.status_code == 403
    outsider = _user(db_session)
    pid = _project_1234(db_session, _user(db_session))
    with pytest.raises(HTTPException) as e:
        router.get_plan(pid, db_session, outsider)
    assert e.value.status_code in (403, 404)


def test_activating_an_estimate_never_rewrites_a_signed_contracts_value(db_session):
    """Found 2026-11-02 on 1234: the Contract page offered "Update from active
    estimate" on the EXECUTED contract, and the same recompute runs whenever an
    estimate is activated. The active estimate's $180,414 already includes
    CO-3, so the base would have become $180,414 and CO-3 counted twice."""
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    db_session.execute(_T("""insert into estimate_versions (id, project_id, version_number, label, payload, is_active)
                             values (:i, :p, 3, 'v3', CAST(:j AS jsonb), true)"""),
                       {"i": uuid4(), "p": str(pid),
                        "j": json.dumps({"categories": [{"name": "01 Lump sum", "total": 180414}]})})
    out = regenerate_downstream(pid, db_session, me)
    assert out["contract"]["status"] == "kept (executed)"
    base = db_session.execute(_T("select base_contract_value from contracts where project_id = :p"),
                              {"p": str(pid)}).scalar()
    assert float(base) == 170214


def test_sov_progress_shows_done_earned_billed_and_left(db_session):
    """R2Billing: % done from recorded progress, billed = invoiced stages
    spread over the lines by value (exact total), left = value − billed."""
    from app.routers.billing_plan import sov_pdf
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    assert router.sov_progress(pid, db_session, me) == {"progress": None}  # nothing built yet
    router.save_plan(pid, _complete(), db_session, me)
    actuals_svc.record(db_session, pid, LOCKUP, {"status": "done", "actual_start": date(2026, 10, 20),
                                                 "actual_finish": date(2026, 10, 28)}, None)
    p = router.sov_progress(pid, db_session, me)["progress"]
    t = p["total"]
    assert t["value"] == round(sum(BUDGET.values()) + 10200, 2)
    assert t["billed"] == 54124.76  # deposit + permit stages, before GST
    assert round(sum(r["billed"] for r in p["rows"]), 2) == 54124.76
    by = {r["line"]: r for r in p["rows"]}
    assert by["16"]["pct_done"] == 100.0 and by["CO-3"]["pct_done"] == 100.0  # cladding (A250) recorded done
    assert by["04"]["pct_done"] == 0.0 and by["04"]["earned"] == 0.0  # framing: nothing recorded
    assert 0 < by["01"]["pct_done"] < 100  # general conditions: share of the job elapsed
    assert by["16"]["left"] == round(by["16"]["value"] - by["16"]["billed"], 2)
    assert round(sum(m["in"] for m in p["months"]), 2) == 180414.28
    if not _WEASY:  # needs pango; CI installs it, a bare Mac may not
        return
    pdf = sov_pdf(pid, db_session, me)
    assert pdf.body[:4] == b"%PDF"



def test_contract_checks_time_extension_signed_copy_and_amend(db_session):
    """R2Contract: the date passed banner drafts a no-cost time-extension CO;
    checks name the 28 cents and the stated GST; amend copies the signed
    contract into a new draft and leaves the signed one alone."""
    from app.routers import contract_checks as cc
    me = _user(db_session)
    pid = _project_1234(db_session, me)
    db_session.execute(_T("""update contracts set payload = payload || CAST(:w AS jsonb) where project_id = :p"""),
                       {"p": str(pid), "w": json.dumps({"warnings": [
                           "The signed contract states GST 9,021.00 and total 189,435.00; 5% of 180,414.00 is 9,020.70."]})})
    out = cc.contract_checks(pid, db_session, me)
    kinds = {c["kind"]: c["text"] for c in out["checks"]}
    assert "$18,041.62" in kinds["stages"] and "9,021.00" in kinds["gst"] and "signed_copy" in kinds
    assert out["late"]["days"] == (date.fromisoformat(V7["p50_finish_date"]) - date(2026, 10, 12)).days
    assert out["late"]["passed"] is True and out["late"]["extension_co"] is None
    co = cc.time_extension(pid, db_session, me)
    assert co["schedule_impact_days"] == out["late"]["days"]
    row = db_session.execute(_T("select status, cost_impact from change_orders where id = :i"), {"i": co["id"]}).one()
    assert row.status == "draft" and float(row.cost_impact) == 0
    assert cc.contract_checks(pid, db_session, me)["late"]["extension_co"] == co["number"]
    doc = uuid4()
    db_session.execute(_T("""insert into documents (id, project_id, filename, r2_key, category, phase)
                             values (:i, :p, 'signed.pdf', :k, 'contract', 'construction')"""), {"i": doc, "p": str(pid), "k": f"k/{doc}"})
    after = cc.signed_copy(pid, cc.SignedCopyIn(document_id=doc), db_session, me)
    assert "signed_copy" not in {c["kind"] for c in after["checks"]}
    before = db_session.execute(_T("select id, status, payload from contracts where project_id = :p"), {"p": str(pid)}).one()
    a = cc.amend(pid, db_session, me)
    assert a["version_number"] == 2
    still = db_session.execute(_T("select status, payload from contracts where id = :i"), {"i": before.id}).one()
    assert still.status == "executed" and still.payload == before.payload
    with pytest.raises(HTTPException):
        cc.amend(pid, db_session, me)
