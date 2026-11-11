"""Ask assistant (UI round 1, phase 9): read-only tools scoped by project
access, missing data reported as missing, and the question loop."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.services import assistant as A  # noqa: E402

TODAY = date(2026, 10, 14)  # a Wednesday


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    """schedules/activities/inspections/contracts are migration-only; created
    inside the test transaction."""
    db_session.execute(_T("CREATE TABLE IF NOT EXISTS schedules (id uuid PRIMARY KEY, project_id uuid, "
                          "created_at timestamptz DEFAULT now())"))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS activities (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), schedule_id uuid, activity_code text, name text,
        phase text, predecessor_codes jsonb, start_date date, finish_date date,
        is_critical boolean DEFAULT false, is_milestone boolean DEFAULT false)"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS inspections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), schedule_id uuid, activity_id uuid,
        authority text, inspection_type text, lead_time_days int, gates jsonb, notes text)"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY, project_id uuid, version_number int, status text,
        base_contract_value numeric, option_contract_value numeric, payload jsonb)"""))
    from tests.test_scope_page import _estimate_versions_table

    _estimate_versions_table(db_session)
    monkeypatch.setattr(A, "_today", lambda: TODAY)
    import app.services.home as H

    monkeypatch.setattr(H, "today", lambda: TODAY)


def _manager(db, *projects):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
               {"i": str(uid), "e": f"m-{uid.hex[:8]}@example.com"})
    for p in projects:
        db.execute(_T("insert into project_members (project_id, user_id, role) values (:p, :u, 'manager')"),
                   {"p": str(p), "u": str(uid)})
    db.flush()
    return CurrentUser(id=uid, email="m@example.com", role="manager")


def _project(db, name, status="in_progress"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i, :n, :s, :st)"),
               {"i": str(pid), "n": name, "s": f"as-{pid.hex[:8]}", "st": status})
    return pid


def _schedule(db, pid, acts):
    sid = uuid4()
    db.execute(_T("insert into schedules (id, project_id) values (:i, :p)"), {"i": str(sid), "p": str(pid)})
    for code, name, phase, start, finish, preds in acts:
        db.execute(_T("""insert into activities (schedule_id, activity_code, name, phase, start_date,
                         finish_date, predecessor_codes) values (:s, :c, :n, :ph, :a, :b, cast(:pr as jsonb))"""),
                   {"s": str(sid), "c": code, "n": name, "ph": phase, "a": start, "b": finish,
                    "pr": json.dumps(preds)})
    return sid


def _co(db, pid, number, status, amount, days=0, submitted=None):
    db.execute(_T("""insert into change_orders (id, project_id, number, title, cost_impact, status,
                     schedule_impact_days, submitted_at) values (:i, :p, :n, 'Extra', :a, :s, :d, :sub)"""),
               {"i": str(uuid4()), "p": str(pid), "n": number, "a": amount, "s": status, "d": days,
                "sub": submitted})


def test_tools_only_see_the_users_projects(db_session):
    mine, theirs = _project(db_session, "Example"), _project(db_session, "Maple")
    _co(db_session, mine, 1, "pending", 500)
    _co(db_session, theirs, 1, "pending", 900)
    user = _manager(db_session, mine)
    waiting = A.change_orders_waiting(db_session, user)["waiting"]
    assert [c["project"] for c in waiting] == ["Example"]
    assert [m["name"] for m in A.find_project(db_session, user, "glen")["matches"]] == []
    # Asking about someone else's project by id is refused, and reported to the model, not raised.
    out = A._run_tool(db_session, user, "project_status", {"project_id": str(theirs)})
    assert out == {"error": "not allowed or not found"}


def test_a_week_with_no_crews_or_inspections_says_so(db_session):
    pid = _project(db_session, "Example")
    _schedule(db_session, pid, [("A1", "Excavation", "Foundation", TODAY, TODAY + timedelta(days=2), [])])
    user = _manager(db_session, pid)
    w = A.week_plan(db_session, user, str(pid))
    assert [a["name"] for a in w["activities"]] == ["Excavation"]
    assert "crew assignments" in w["not_recorded"] and "subcontractors booked" in w["not_recorded"]
    assert any(n.startswith("inspections") for n in w["not_recorded"])


def test_status_with_no_reports_is_not_good_news(db_session):
    pid = _project(db_session, "Example")
    _schedule(db_session, pid, [("A1", "Excavation", "Foundation", TODAY - timedelta(days=3), TODAY + timedelta(days=2), [])])
    user = _manager(db_session, pid)
    s = A.project_status(db_session, user, str(pid))
    assert s["project"]["name"] == "Example" and s["phase_now"] == ["Foundation"]
    assert s["last_daily_report"] is None
    assert any("daily reports" in n for n in s["not_recorded"])
    assert {x["href"] for x in s["sources"]} >= {f"/projects/{pid}/overview", f"/projects/{pid}/money"}


def test_missing_daily_reports_count_only_scheduled_weekdays(db_session):
    pid = _project(db_session, "Example")
    # Work scheduled Mon 12 – Fri 16 Oct; today is Wed 14. Mon 12 and Tue 13 have no report.
    _schedule(db_session, pid, [("A1", "Framing", "Structure", date(2026, 10, 12), date(2026, 10, 16), [])])
    user = _manager(db_session, pid)
    out = A.needs_attention(db_session, user, str(pid))
    assert out["daily_reports_missing"][0]["days"] == ["2026-10-12", "2026-10-13"]


def test_a_quiet_attention_answer_still_names_the_projects_it_covered(db_session):
    """Rollout test 2026-10-15 as a manager: "covering 1 project in your scope.
    The tool didn't return the project's name." """
    pid = _project(db_session, "ZZ Rollout Test")
    out = A.needs_attention(db_session, _manager(db_session, pid))
    assert out["project_names"] == ["ZZ Rollout Test"]


def test_blockers_name_the_next_phase_and_its_unfinished_prerequisites(db_session):
    pid = _project(db_session, "Example")
    _schedule(db_session, pid, [
        ("A1", "Footings", "Foundation", TODAY - timedelta(days=5), TODAY + timedelta(days=3), []),
        ("B1", "Wall framing", "Structure", TODAY + timedelta(days=7), TODAY + timedelta(days=20), ["A1"]),
    ])
    _co(db_session, pid, 4, "pending", 19530, days=3, submitted=datetime(2026, 10, 1, 19, tzinfo=timezone.utc))
    user = _manager(db_session, pid)
    b = A.blockers(db_session, user, str(pid))
    assert b["next_phase"] == "Structure"
    assert b["prerequisites_still_scheduled_to_finish"][0]["prerequisite"]["name"] == "Footings"
    assert b["change_orders_pending_with_schedule_impact"][0]["number"] == "CO-4"
    assert "RFIs" in b["not_recorded"]


def test_blockers_ignore_later_work_in_the_same_phase(db_session):
    pid = _project(db_session, "Example")
    _schedule(db_session, pid, [
        ("A75", "Forms delivered", "Foundation", TODAY, TODAY, []),
        ("A100", "Form footings", "Foundation", TODAY + timedelta(days=1), TODAY + timedelta(days=2), ["A75"]),
        ("A140", "Under-slab plumbing", "Plumbing", TODAY + timedelta(days=20), TODAY + timedelta(days=21), []),
        ("A210", "Slab prep", "Foundation", TODAY + timedelta(days=40), TODAY + timedelta(days=42), ["A140"]),
    ])
    user = _manager(db_session, pid)
    b = A.blockers(db_session, user, str(pid))
    assert [p["prerequisite"]["code"] for p in b["prerequisites_still_scheduled_to_finish"]] == ["A75"]
    assert [a["code"] for a in b["next_phase_work_in_next_14_days"]] == ["A100"]


def test_invoices_report_supplier_bills_as_not_recorded(db_session):
    pid = _project(db_session, "Example")
    db_session.execute(_T("""insert into invoices (id, project_id, number, type, status, payload, paid_amount, sent_at)
        values (:i, :p, :n, 'progress', 'sent', cast(:pl as jsonb), 0, :sa)"""),
        {"i": str(uuid4()), "p": str(pid), "n": f"I-{pid.hex[:6]}", "pl": json.dumps({"total": 1000, "terms": "Net 7"}),
         "sa": datetime(2026, 10, 1, 19, tzinfo=timezone.utc)})
    user = _manager(db_session, pid)
    out = A.invoices_attention(db_session, user)
    assert out["projects"][0]["overdue"][0]["due"] == "2026-10-08"
    assert "supplier bills awaiting approval" in out["not_recorded"]


class _FakeClient:
    """Answers with one tool call, then text; records what it was sent."""

    def __init__(self, tool, args):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._script = [
            SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                            content=[{"type": "tool_use", "id": "t1", "name": tool, "input": args}]),
            SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=20, output_tokens=8),
                            content=[{"type": "text", "text": "Two change orders are waiting."}]),
        ]

    def _create(self, **kw):
        self.calls.append(kw)
        return self._script[len(self.calls) - 1]


def test_ask_runs_the_tool_and_returns_its_sources(db_session):
    pid = _project(db_session, "Example")
    _co(db_session, pid, 3, "pending", 500)
    user = _manager(db_session, pid)
    fake = _FakeClient("change_orders_waiting", {})
    out = A.ask(db_session, user, "Which change orders are waiting?", project_id=str(pid), client=fake)
    assert out["answer"] == "Two change orders are waiting."
    assert out["tools"] == ["change_orders_waiting"]
    assert out["sources"][0]["href"] == f"/projects/{pid}/change-orders"
    assert out["usage"] == {"input_tokens": 30, "output_tokens": 13}
    # The project the user is on is in the system prompt; the tool result went back to the model.
    assert "Example" in fake.calls[0]["system"]
    sent = fake.calls[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and "CO-3" in sent["content"]


def test_ask_refuses_a_project_the_user_cannot_see(db_session):
    from fastapi import HTTPException

    theirs = _project(db_session, "Maple")
    user = _manager(db_session)
    with pytest.raises(HTTPException) as e:
        A.ask(db_session, user, "status?", project_id=str(theirs), client=_FakeClient("find_project", {}))
    assert e.value.status_code in (403, 404)


def test_ask_marks_an_answer_cut_off_at_the_length_limit(db_session):
    """Rollout test 2026-10-14: "What needs my attention today?" ended mid-line
    ("**10-") and was shown as a complete answer. A reply stopped by max_tokens
    must lose its half-written line and say it was cut off."""
    user = _manager(db_session, _project(db_session, "Example"))
    fake = _FakeClient("find_project", {})
    fake._script = [SimpleNamespace(stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                    content=[{"type": "text", "text": "- 10-15: Form footings start.\n- **10-"}])]
    out = A.ask(db_session, user, "What needs my attention today?", client=fake)
    assert "**10-" not in out["answer"]
    assert "Form footings start." in out["answer"]
    assert out["answer"].endswith(A.CUT_OFF_NOTE)


def test_budget_vs_actual_answers_from_costs_and_names_what_isnt_recorded(db_session):
    """C3 (2026-11-02): Ask said "budget versus actual cost (actual costs are
    not recorded)" for every project; Money › Costs now records both."""
    for col in ("gst numeric(12,2)", "invoice_number text", "created_by_id uuid"):
        db_session.execute(_T(f"ALTER TABLE project_costs ADD COLUMN IF NOT EXISTS {col}"))
    pid = _project(db_session, "Example")
    user = _manager(db_session, pid)
    out = A.budget_vs_actual(db_session, user, str(pid))
    assert out["budget_total_before_gst"] is None and "the budget (not set on Money › Costs)" in out["not_recorded"]
    for div, amt in (("03 Foundation & Concrete", 20000), ("04 Rough Carpentry & Framing", 4000)):
        db_session.execute(_T("""insert into project_cost_budgets (id, project_id, division, amount, source)
                                 values (:i, :p, :d, :a, 'Contract share')"""),
                           {"i": uuid4(), "p": str(pid), "d": div, "a": amt})
    db_session.execute(_T("""insert into project_costs (id, project_id, category, amount, gst, cost_date, kind, vendor, payload)
                             values (:i, :p, '04 Rough Carpentry & Framing', 4212, 210.6, '2026-10-14',
                             'supplier_invoice', 'Sample Lumber', '{"source": "entered"}')"""),
                       {"i": uuid4(), "p": str(pid)})
    out = A.budget_vs_actual(db_session, user, str(pid))
    assert out["budget_total_before_gst"] == 24000 and out["spent_before_gst"] == 4212
    assert out["left_before_gst"] == 19788 and out["over_budget"] == ["04 Rough Carpentry & Framing"]
    assert out["last_bill_date"] == "2026-10-14"
    assert "labour (hours are not costed yet)" in out["not_recorded"]
    assert out["sources"][0]["href"].endswith("/money?view=costs")


def test_ask_no_longer_refuses_budget_questions():
    prompt = A.system_prompt(TODAY, None)
    assert "actual costs are not recorded" not in prompt
    assert "whether a project is on budget" in prompt
    assert any(t["name"] == "budget_vs_actual" for t in A.TOOLS) and "budget_vs_actual" in A._IMPL


def test_someone_without_the_project_cant_ask_its_budget(db_session):
    from fastapi import HTTPException
    pid = _project(db_session, "Example")
    other = _manager(db_session)
    with pytest.raises(HTTPException):
        A.budget_vs_actual(db_session, other, str(pid))
