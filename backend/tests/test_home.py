"""Home and the road to an estimate.

⚠️ TEST-SCHEMA NOTE. `estimate_versions` is a stub model and `contracts`,
`schedules`, `activities` are not mapped at all; migrations own them. They are
given the columns the service reads here, inside the test's transaction, the
same way test_drafts does.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.services import home as H  # noqa: E402

TODAY = date(2026, 10, 12)


@pytest.fixture(autouse=True)
def _schema(db_session):
    for ddl in (
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS project_id uuid",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS is_active boolean DEFAULT false",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS version_number integer DEFAULT 1",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS label text",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS payload jsonb DEFAULT '{}'::jsonb",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now()",
        """CREATE TABLE IF NOT EXISTS contracts (id uuid PRIMARY KEY, project_id uuid,
             version_number int, status text, base_contract_value numeric,
             option_contract_value numeric)""",
        """CREATE TABLE IF NOT EXISTS schedules (id uuid PRIMARY KEY, project_id uuid,
             version_number int, created_at timestamptz DEFAULT now())""",
        """CREATE TABLE IF NOT EXISTS activities (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
             schedule_id uuid, name text, phase text, start_date date, finish_date date,
             is_milestone boolean DEFAULT false)""",
    ):
        db_session.execute(_T(ddl))
    db_session.flush()


def _project(db, name="Home", status="preconstruction", created_days_ago=400):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,:n,:s,:st)"),
               {"i": str(pid), "n": name, "s": f"hm-{str(pid)[:8]}", "st": status})
    return str(pid)


def _doc(db, pid, filename="plan.pdf", category="drawing", days_ago=0):
    db.execute(_T("""insert into documents (id, project_id, category, phase, filename, r2_key, created_at)
                     values (:i,:p,:c,'pre',:f,:k, now() - make_interval(days => :d))"""),
               {"i": str(uuid4()), "p": pid, "c": category, "f": filename, "k": str(uuid4()), "d": days_ago})


def _estimate(db, pid, version=1, active=True, cats=(1000,), oh=10, pr=10):
    db.execute(_T("""insert into estimate_versions (id, project_id, is_active, version_number, payload)
                     values (:i,:p,:a,:v, cast(:d as jsonb))"""),
               {"i": str(uuid4()), "p": pid, "a": active, "v": version,
                "d": json.dumps({"overhead": oh, "profit": pr,
                                 "categories": [{"total": t} for t in cats]})})


# --- active jobs ---------------------------------------------------------------

def test_a_job_is_active_by_status_even_with_nothing_recent(db_session):
    pid = _project(db_session, status="in_progress")
    jobs = H.active_jobs(db_session, [pid], TODAY)
    assert [j["why_active"] for j in jobs] == ["status"]


def test_recent_work_makes_a_preconstruction_job_active(db_session):
    """The case that forced the rule: 1234 was framing and still preconstruction."""
    pid = _project(db_session)
    _doc(db_session, pid, days_ago=2)
    jobs = H.active_jobs(db_session, [pid], TODAY)
    assert [j["why_active"] for j in jobs] == ["recent_work"]


def test_a_quiet_preconstruction_job_is_not_active(db_session):
    pid = _project(db_session)
    _doc(db_session, pid, days_ago=H.ACTIVE_DAYS + 5)
    assert H.active_jobs(db_session, [pid], TODAY) == []


def test_archived_jobs_never_show(db_session):
    pid = _project(db_session, status="archived")
    _doc(db_session, pid, days_ago=0)
    assert H.active_jobs(db_session, [pid], TODAY) == []


def test_active_jobs_only_reads_the_projects_it_is_given(db_session):
    mine = _project(db_session, "Mine", status="in_progress")
    _project(db_session, "Theirs", status="in_progress")
    assert [j["name"] for j in H.active_jobs(db_session, [mine], TODAY)] == ["Mine"]


# --- money ---------------------------------------------------------------------

def test_estimate_total_matches_the_portfolio_arithmetic(db_session):
    pid = _project(db_session)
    _estimate(db_session, pid, cats=(1000, 500), oh=10, pr=20)
    est = H.active_estimate(db_session, pid)
    # 1500 * 1.10 * 1.20 = 1980 before GST, 2079 with
    assert (est["total_before_gst"], est["total_with_gst"]) == (1980, 2079)


def test_the_contract_record_wins_over_the_estimate(db_session):
    pid = _project(db_session)
    _estimate(db_session, pid)
    db_session.execute(_T("""insert into contracts (id, project_id, version_number, status,
                             base_contract_value) values (:i,:p,1,'draft',170214)"""),
                       {"i": str(uuid4()), "p": pid})
    c = H.contract_value(db_session, pid, H.active_estimate(db_session, pid))
    assert (c["amount"], c["source"]) == (170214, "contract")


def test_without_a_contract_the_estimate_is_used_and_says_so(db_session):
    pid = _project(db_session)
    _estimate(db_session, pid, version=5)
    c = H.contract_value(db_session, pid, H.active_estimate(db_session, pid))
    assert c["source"] == "estimate" and c["version_number"] == 5


def test_an_inactive_draft_is_not_the_estimate(db_session):
    pid = _project(db_session)
    _estimate(db_session, pid, active=False)
    assert H.active_estimate(db_session, pid) is None


# --- schedule -------------------------------------------------------------------

def _schedule(db, pid, acts, created=None):
    sid = str(uuid4())
    db.execute(_T("insert into schedules (id, project_id, version_number, created_at) values (:i,:p,1,:c)"),
               {"i": sid, "p": pid, "c": created or datetime.now(timezone.utc)})
    for name, start, finish, ms in acts:
        db.execute(_T("""insert into activities (schedule_id, name, phase, start_date, finish_date, is_milestone)
                         values (:s,:n,'x',:a,:b,:m)"""),
                   {"s": sid, "n": name, "a": start, "b": finish, "m": ms})


def test_next_milestone_is_the_first_upcoming_one_never_a_past_one(db_session):
    pid = _project(db_session)
    _schedule(db_session, pid, [("Dig", TODAY - timedelta(days=5), None, True),
                                ("Framing", date(2026, 11, 4), None, True)])
    assert H.next_milestone(db_session, pid, TODAY) == {"label": "Framing", "date": "2026-11-04"}


def test_only_the_latest_schedule_counts(db_session):
    pid = _project(db_session)
    _schedule(db_session, pid, [("Framing (old)", date(2026, 10, 29), None, True)],
              created=datetime(2026, 9, 1, tzinfo=timezone.utc))
    _schedule(db_session, pid, [("Framing", date(2026, 11, 4), None, True)])
    assert H.next_milestone(db_session, pid, TODAY)["label"] == "Framing"


def test_this_week_lists_starts_and_finishes_inside_seven_days_only(db_session):
    pid = _project(db_session)
    _schedule(db_session, pid, [
        ("Forms", TODAY + timedelta(days=1), TODAY + timedelta(days=20), False),
        ("Backfill", TODAY - timedelta(days=10), TODAY + timedelta(days=3), False),
        ("Roofing", TODAY + timedelta(days=30), TODAY + timedelta(days=40), False),
    ])
    got = {(i["name"], i["event"]) for i in H.this_week(db_session, [pid], TODAY)}
    assert got == {("Forms", "starts"), ("Backfill", "finishes")}


def test_this_week_is_in_the_order_of_the_dates_it_shows(db_session):
    """Rollout test 2026-10-14: Home listed Mon 19, Fri 16, Wed 14... because it
    sorted by start date while each line shows its event's date (an activity
    that started weeks ago is listed on the day it finishes)."""
    pid = _project(db_session)
    _schedule(db_session, pid, [
        ("Sump", TODAY - timedelta(days=10), TODAY + timedelta(days=5), False),
        ("Tank", TODAY - timedelta(days=2), TODAY + timedelta(days=2), False),
        ("Forms", TODAY + timedelta(days=1), TODAY + timedelta(days=20), False),
        ("Inspection", TODAY + timedelta(days=4), None, True),
    ])
    dates = [i["date"] for i in H.this_week(db_session, [pid], TODAY)]
    assert dates == sorted(dates), dates


# --- the road -------------------------------------------------------------------

def _road(db, pid):
    return {s["key"]: s for s in H.road(db, pid)["steps"]}


def test_a_new_project_is_at_documents(db_session):
    pid = _project(db_session)
    r = H.road(db_session, pid)
    assert r["current"] == "documents"
    assert [s["key"] for s in r["steps"]] == [
        "documents", "scope", "drawings_read", "checked", "signed_off", "estimate"]


def test_generated_pdfs_do_not_count_as_documents_in(db_session):
    pid = _project(db_session)
    _doc(db_session, pid, "scope.pdf", category="scope_pdf")
    assert _road(db_session, pid)["documents"]["state"] == "todo"


def test_no_cad_means_the_analysis_steps_are_not_needed(db_session):
    pid = _project(db_session)
    _doc(db_session, pid, "plan.pdf")
    steps = _road(db_session, pid)
    assert {steps[k]["state"] for k in ("drawings_read", "checked", "signed_off")} == {"not_needed"}


def test_cad_with_no_analysis_puts_reading_the_drawings_next(db_session):
    pid = _project(db_session)
    _doc(db_session, pid, "A101.dwg")
    db_session.execute(_T("insert into scopes (id, project_id, version, payload) values (:i,:p,1,'{}')"),
                       {"i": str(uuid4()), "p": pid})
    r = H.road(db_session, pid)
    assert r["current"] == "drawings_read"
    assert _road(db_session, pid)["signed_off"]["state"] == "todo"


def test_the_estimate_step_names_version_and_total(db_session):
    pid = _project(db_session)
    _estimate(db_session, pid, version=5, cats=(1000,), oh=0, pr=0)
    assert _road(db_session, pid)["estimate"]["detail"] == "v5 · $1,050 with GST"
