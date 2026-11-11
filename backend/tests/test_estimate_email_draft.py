"""Drafting the client email that shares estimate packages (Amir, 2026-10-27).

456 W Sample: two estimates (Suite 100, corridor) go to the client as
"scope + selections + lump sum" packages. The assistant drafts, Amir sends.

Pinned:
  1. The figures given to the drafter are the ones the PDF prints.
  2. The prompt forbids contracts (not awarded), internal wording and invented
     facts; missing names become placeholders.
  3. check_draft flags a stray dollar figure, a contract, internal words, a
     missing price, an em dash and unfilled placeholders; a clean draft has none.
  4. The endpoint queues a draft only for this project's estimates and never
     sends anything.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import estimate_email as E
from app.services.estimate_selection_pdf import build_selection_html, client_totals

PAYLOAD = {
    "overhead": 12, "profit": 15,
    "categories": [
        {"name": "09 Finishes", "total": 30000, "items": [
            {"name": "Carpet tile to match 7th floor", "labor": 4000, "materials": 6500},
            {"name": "Contingency", "labor": 0, "materials": 1000}]},
    ],
    "assumptions": ["Work during regular hours except ceiling tile removal"],
    "exclusions": ["Washroom work", "Structural work"],
}
SCOPE = {"reference": "456SA-4FL-CORR", "summary": "4th floor elevator lobby and corridors to match the 7th floor. Carpet, ceilings."}


def _ctx():
    pkg = E.package_context({"payload": PAYLOAD, "reference": "456SA-CORRIDOR(AMIR)", "title": "4th floor corridor"}, SCOPE)
    return {"packages": [pkg], "attachments": ["4th floor corridor (PDF)"]}, pkg


def test_the_drafter_gets_the_figures_the_pdf_prints():
    _, pkg = _ctx()
    t = client_totals(PAYLOAD)
    assert pkg["lump_sum_before_gst"] == f"${t['subtotal']:,.2f}"
    assert pkg["lump_sum_including_gst"] == f"${t['total']:,.2f}"
    html = build_selection_html(PAYLOAD, {"name": "x"}, {}, "scopepack")
    assert pkg["lump_sum_including_gst"] in html, "the same total as the package"
    assert pkg["material_selections_to_confirm"] == 1 and pkg["exclusions"] == ["Washroom work", "Structural work"]
    assert "456SA-CORRIDOR(AMIR)" not in str(pkg), "internal references stay out"


def test_the_prompt_forbids_contracts_internal_figures_and_invention():
    system, msgs, _ = E.build_messages({"context": _ctx()[0], "instructions": "Mention the leasing timeline"})
    low = system.lower()
    assert "never mention, offer or attach a contract" in low
    assert "[name]" in system and "use only facts" in low and "no em dashes" in low
    assert "Mention the leasing timeline" in msgs[0]["content"]


def test_a_clean_draft_has_no_warnings():
    ctx, pkg = _ctx()
    body = (f"Hi Sam,\n\nThe corridor package is attached: {pkg['lump_sum_before_gst']} + GST "
            f"({pkg['lump_sum_including_gst']} including GST).\n\nBest,\nAmir")
    assert E.check_draft({"subject": "456 West Sample: estimate", "body": body}, ctx) == []


@pytest.mark.parametrize("body,expect", [
    ("Price is $1,234 for the corridor.", "not in the packages"),
    ("Please sign the attached contract.", "contract or agreement"),
    ("Our markup is modest.", "Internal wording"),
    ("Hi [name], see attached — thanks.", "em dash"),
    ("Hi [name], see attached.", "placeholders"),
    ("Hi Sam, see attached.", "is not in the email"),
])
def test_check_draft_flags_what_to_fix(body, expect):
    assert any(expect in w for w in E.check_draft({"subject": "456 West Sample", "body": body}, _ctx()[0]))


# ── endpoint ────────────────────────────────────────────────────────────────

PID, V1, V2, FOREIGN = uuid4(), uuid4(), uuid4(), uuid4()


class _Res:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self):
        self.added = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM estimate_versions WHERE project_id = :p ORDER BY" in sql:
            now = datetime(2026, 10, 27)
            return _Res([(V2, 8, "456SA-CORRIDOR(AMIR)", True, PAYLOAD, now),
                         (V1, 7, "456SA-SUITE-425", False, PAYLOAD, now)])
        if "FROM scopes WHERE project_id" in sql:
            return _Res([(uuid4(), 2, SCOPE, datetime(2026, 10, 22))])
        if "SELECT scope_id FROM estimate_versions" in sql:
            return _Res([None])
        if "FROM projects p" in sql:
            return _Res([("456 W Sample", "456 West Sample St", "Sample Property Group", None, None)])
        if "SELECT name FROM users" in sql:
            return _Res(["Amir Omidvar"])
        raise AssertionError(sql[:90])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def _user():
    from types import SimpleNamespace
    return SimpleNamespace(id=uuid4(), email="amir@builtbybrio.com")


def test_drafting_queues_a_job_and_sends_nothing():
    from app.routers.workbench import EstimateEmailBody, draft_estimate_email
    db = FakeDB()
    out = draft_estimate_email(PID, EstimateEmailBody(version_ids=[V2, V1]), db=db, user=_user())
    (job,) = db.added
    assert job.id is not None and out["job_id"] == str(job.id)
    assert job.kind == "email_draft" and job.status == "queued"
    ctx = job.input["context"]
    assert job.input["purpose"] == "estimate_share" and len(ctx["packages"]) == 2
    assert ctx["client"]["organisation"] == "Sample Property Group" and ctx["client"]["contact_name"] is None
    assert ctx["sender"]["name"] == "Amir Omidvar"


def test_an_estimate_from_another_project_is_refused():
    from app.routers.workbench import EstimateEmailBody, draft_estimate_email
    with pytest.raises(HTTPException) as e:
        draft_estimate_email(PID, EstimateEmailBody(version_ids=[V2, FOREIGN]), db=FakeDB(), user=_user())
    assert e.value.status_code == 404


def test_the_draft_has_room_to_finish():
    """First live run (456, 2026-10-27) hit the 2,000-token cap mid-JSON and
    failed. The body is asked to stay under 300 words with room to spare."""
    assert E.MAX_TOKENS >= 4000 and "under 300 words" in E.SYSTEM


def test_previous_drafts_list_only_this_projects_estimate_emails_newest_first(db_session):
    """The panel lost its text when closed (2026-10-27); earlier drafts are listed."""
    from datetime import timedelta, timezone

    from sqlalchemy import text

    from app.models.ai_job import AIJob
    from app.routers.workbench import list_estimate_email_drafts

    pids = []
    for _ in range(2):
        pid = uuid4()
        db_session.execute(text("insert into projects (id, name, slug, status) values (:i, 'E', :s, 'lead')"),
                           {"i": str(pid), "s": f"e-{pid.hex[:8]}"})
        pids.append(pid)
    mine, other = pids
    t0 = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)
    rows = [
        (mine, {"purpose": "estimate_share", "version_ids": ["a"]}, {"draft": {"subject": "older", "body": "x"}}, t0),
        (mine, {"purpose": "estimate_share", "version_ids": ["a", "b"]}, {"draft": {"subject": "newer", "body": "y"}},
         t0 + timedelta(minutes=5)),
        (mine, {"milestone_id": "m"}, {}, t0 + timedelta(minutes=9)),          # a milestone email: not listed
        (other, {"purpose": "estimate_share"}, {"draft": {"subject": "theirs", "body": "z"}}, t0),
    ]
    for pid, inp, out, at in rows:
        db_session.add(AIJob(id=uuid4(), project_id=pid, kind="email_draft", status="done",
                             input=inp, output=out, created_at=at))
    db_session.flush()
    got = list_estimate_email_drafts(mine, db=db_session, _=None)
    assert [g["subject"] for g in got] == ["newer", "older"]
    assert got[0]["version_ids"] == ["a", "b"]
