"""Money tab (UI round 1, phase 7): the summary, the next uninvoiced milestone,
derived overdue, and preparing the next invoice."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.routers import money as R  # noqa: E402
from app.services import money as M  # noqa: E402

SU = CurrentUser(id=str(uuid4()), email="a@t.local", role="superadmin")
SCHEDULE = [
    {"pct": 10, "amount": 18041.9, "milestone": "Deposit — on signing"},
    {"pct": 20, "amount": 36082.86, "milestone": "Building permit issued — mobilization"},
    {"pct": 30, "amount": 54123.81, "milestone": "Foundation, framing & weathertight (lock-up)"},
]


@pytest.fixture(autouse=True)
def _contracts_table(db_session):
    """`contracts` is migration-only (raw SQL); created inside the test transaction."""
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY, project_id uuid, version_number int, status text,
        base_contract_value numeric, option_contract_value numeric, payload jsonb)"""))
    db_session.execute(_T("CREATE TABLE IF NOT EXISTS schedules (id uuid PRIMARY KEY, project_id uuid, created_at timestamptz DEFAULT now())"))
    from tests.test_scope_page import _estimate_versions_table

    _estimate_versions_table(db_session)


def _project(db, schedule=SCHEDULE, contract=170214):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Money',:s,'lead')"),
               {"i": str(pid), "s": f"mo-{str(pid)[:8]}"})
    db.execute(_T("""insert into contracts (id, project_id, version_number, status, base_contract_value, payload)
        values (:i, :p, 1, 'executed', :v, cast(:pl as jsonb))"""),
               {"i": str(uuid4()), "p": str(pid), "v": contract,
                "pl": json.dumps({"paymentSchedule": schedule})})
    return pid


def _invoice(db, pid, number, status, total, paid=0, milestone=None, terms=None, sent=None, due=None):
    pl = {"total": total}
    if milestone:
        pl["milestone"] = milestone
    if terms:
        pl["terms"] = terms
    if due:
        pl["dueDate"] = due
    db.execute(_T("""insert into invoices (id, project_id, number, type, status, payload, paid_amount, sent_at)
        values (:i, :p, :n, 'progress', :st, cast(:pl as jsonb), :pa, :sa)"""),
               {"i": str(uuid4()), "p": str(pid), "n": number, "st": status, "pl": json.dumps(pl),
                "pa": paid, "sa": sent})


def _co(db, pid, number, status, amount, submitted=None):
    db.execute(_T("""insert into change_orders (id, project_id, number, title, cost_impact, status, submitted_at)
        values (:i, :p, :n, 'CO', :a, :s, :sub)"""),
               {"i": str(uuid4()), "p": str(pid), "n": number, "a": amount, "s": status, "sub": submitted})


def test_the_1234_shape(db_session):
    pid = _project(db_session)
    _invoice(db_session, pid, f"A-{pid.hex[:6]}", "paid", 18944, paid=18944, milestone="Deposit — on signing (10%)")
    _invoice(db_session, pid, f"B-{pid.hex[:6]}", "sent", 37887, milestone="Building permit issued — mobilization",
             terms="Payable upon receipt", sent=datetime(2026, 9, 1, tzinfo=timezone.utc))
    _co(db_session, pid, 3, "approved", 10200)
    _co(db_session, pid, 4, "draft", 19530)
    s = M.summary(db_session, pid, today=date(2026, 10, 13))
    assert s["contract"]["amount"] == 170214 and s["change_orders"]["approved_before_gst"] == 10200
    assert (s["invoiced"], s["paid"], s["owing"]) == (56831, 18944, 37887)
    # "Payable upon receipt" has no due date to be late against.
    assert [i["status"] for i in s["invoices"]] == ["paid", "sent"]
    assert s["next_milestone"]["milestone"] == "Foundation, framing & weathertight (lock-up)"
    assert s["next_milestone"]["amount_before_gst"] == 54123.81


def test_overdue_is_derived_from_net_terms_and_drafts_are_not_invoiced(db_session):
    pid = _project(db_session)
    _invoice(db_session, pid, f"C-{pid.hex[:6]}", "sent", 1000, terms="Net 7",
             sent=datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc))  # noon in Vancouver
    _invoice(db_session, pid, f"D-{pid.hex[:6]}", "draft", 500)
    s = M.summary(db_session, pid, today=date(2026, 10, 13))
    assert s["invoices"][0]["status"] == "overdue" and s["invoices"][0]["due"] == "2026-10-08"
    assert s["invoiced"] == 1000


def test_waiting_days_count_from_submission(db_session):
    pid = _project(db_session)
    _co(db_session, pid, 1, "pending", 500, submitted=datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc))
    _co(db_session, pid, 2, "pending", 500)  # sent before submitted_at existed: unknown
    s = M.summary(db_session, pid, today=date(2026, 10, 13))
    assert [c["waiting_days"] for c in s["change_orders"]["items"]] == [12, None]


def test_prepare_next_goes_through_the_create_path_once_per_milestone(db_session, monkeypatch):
    pid = _project(db_session)
    _invoice(db_session, pid, f"E-{pid.hex[:6]}", "paid", 18944, paid=18944, milestone="Deposit — on signing")
    seen = []

    def fake_create(body, response, db, user):
        seen.append(body)
        return SimpleNamespace(id=uuid4(), number="INV-TEST")

    import app.routers.invoices as inv
    monkeypatch.setattr(inv, "create_invoice", fake_create)
    out = R.prepare_next_invoice(project_id=pid, db=db_session, user=SU)
    body = seen[0]
    assert out["milestone"] == "Building permit issued — mobilization"
    assert body.type == "progress" and body.payload["scheduleIndex"] == 1
    assert body.payload["lineItems"][0]["amount"] == 36082.86
    assert body.idempotency_key == f"next-milestone-{pid}-1"


def test_prepare_next_refuses_when_everything_is_invoiced(db_session):
    pid = _project(db_session, schedule=SCHEDULE[:1])
    _invoice(db_session, pid, f"F-{pid.hex[:6]}", "sent", 1, milestone="Deposit — on signing")
    with pytest.raises(HTTPException) as e:
        R.prepare_next_invoice(project_id=pid, db=db_session, user=SU)
    assert e.value.status_code == 409


def test_milestone_names_match_across_brackets_and_punctuation():
    assert M._same_milestone("Deposit — on signing (10%)", "Deposit - on signing")
    assert not M._same_milestone("Deposit", "Holdback")


def test_an_evening_send_is_dated_that_day_in_vancouver(db_session):
    pid = _project(db_session)
    # 02:00 UTC on the 2nd is 7pm on the 1st in Vancouver.
    _invoice(db_session, pid, f"G-{pid.hex[:6]}", "sent", 1000, terms="Net 7",
             sent=datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc))
    assert M.summary(db_session, pid, today=date(2026, 10, 13))["invoices"][0]["due"] == "2026-10-08"
