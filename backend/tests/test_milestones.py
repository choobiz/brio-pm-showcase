"""Milestone bookings and letters (plan approved by Amir 2026-10-16).

What is tested:
- the list is built from the library and the project's own documents (the
  engineer's notice period, the permit number, a stormwater plan), once;
- dates come from the schedule, counted back in business days;
- an email is never sent without the PM's explicit confirmation, never with a
  [confirm ...] placeholder left in, and always with the project address in BCC
  and Reply-To so the reply files back (Amir: "always be a confirmation before
  the emails go out");
- replies carrying the [KS-Mn] tag file against their item; our own BCC copy
  is recognised as ours.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _T

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.models.milestone import MilestoneEmail, ProjectMilestone, ProjectTeamMember
from app.services import milestones as svc
from app.services.milestone_email import parse_email_draft
from app.services.milestone_library import DNV

ACTS = [
    {"code": "A45", "name": "GEO inspection — soil bearing, after excavation", "start_date": date(2026, 10, 7), "finish_date": None},
    {"code": "A100", "name": "Form Footings", "start_date": date(2026, 10, 15), "finish_date": None},
    {"code": "A105", "name": "GEO inspection — formed footings, before pour", "start_date": date(2026, 10, 19), "finish_date": None},
    {"code": "A107", "name": "Pour Footings", "start_date": date(2026, 10, 20), "finish_date": None},
    {"code": "A125", "name": "DNV inspection — infiltration field, before cover", "start_date": date(2026, 10, 20), "finish_date": None},
    {"code": "A20", "name": "Locate existing yard piping (safety survey before excavation)", "start_date": date(2026, 10, 5), "finish_date": None},
    {"code": "A60", "name": "Install GRAF EcoBloc Infiltration Tank", "start_date": date(2026, 10, 13), "finish_date": None},
    {"code": "A40", "name": "Excavation for Foundation & Stormwater System (bulk dig & trenching)", "start_date": date(2026, 10, 7), "finish_date": None},
]


def _project(db, with_docs=True):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, address) values (:i, '1234 Test', :s, 'in_progress', '1234 Example Way')"),
               {"i": str(pid), "s": f"ms-{pid.hex[:8]}"})
    if with_docs:
        for name, text in (
            ("1234 EXAMPLE WAY - BLD2026-00000 - PERMIT.PDF", "Permit"),
            ("STRUCTURAL PLAN-6 NOV 2025.pdf",
             "GENERAL NOTES 4. NOTIFY GEO ENGINEERS MINIMUM 3 DAYS IN ADVANCE FOR INSPECTION: - SOIL BEARING BEFORE "
             "FOUNDATION FORMING - FORMED FOUNDATION BEFORE CONCRETE POURING 1. AFTER EXCAVATION"),
            ("1234 Example Way - SMP (R3) Drawings.pdf", "ECOBLOC INFILTRATION TANK"),
        ):
            did = uuid4()
            db.execute(_T("insert into documents (id, project_id, category, phase, filename, r2_key) "
                          "values (:i, :p, 'drawing', 'construction', :f, :k)"),
                       {"i": str(did), "p": str(pid), "f": name, "k": f"k/{did}"})
            db.execute(_T("insert into document_text_pages (document_id, page_no, project_id, text) values (:d, 1, :p, :t)"),
                       {"d": str(did), "p": str(pid), "t": text})
    db.flush()
    return pid


@pytest.fixture
def office(db_session, monkeypatch):
    monkeypatch.setattr(svc, "schedule_activities", lambda db, pid: list(ACTS))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=None, email="pm@t.local", role="superadmin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_project_documents_set_the_notice_permit_and_stormwater(db_session):
    pid = _project(db_session)
    f = svc.project_facts(db_session, pid)
    assert f["permit_number"]["value"] == "BLD2026-00000"
    assert f["engineer_notice"]["value"] == 3 and "Geo Engineers" in f["engineer_notice"]["firm"]
    assert f["stormwater"]["value"] is True


def test_the_list_is_built_once_and_dated_from_the_schedule(db_session, office):
    pid = _project(db_session)
    r = office.post(f"/projects/{pid}/milestones/build", json={})
    assert r.status_code == 200, r.text
    items = {i["template_key"]: i for i in r.json()["items"]}
    assert len(items) == len(DNV)  # stormwater rows included: the project has a stormwater plan
    ff = items["ff.structural"]
    assert ff["activity_code"] == "A105" and ff["need_by"] == "2026-10-19"
    assert ff["book_by"] == "2026-10-14"  # 3 business days before Mon 19 Oct
    assert ff["lead_days"] == 3 and "MINIMUM 3 DAYS" in ff["lead_note"]
    assert items["ff.dnv"]["activity_code"] == "A107" and items["ff.dnv"]["book_by"] == "2026-10-19"
    assert {d["name"] for d in items["ff.dnv"]["required_docs"]} >= {"Non-encroachment certificate", "Site survey"}
    assert items["exc.letter"]["confirmed"] is False and "Confirm whether it applies" in items["exc.letter"]["why"]
    again = office.post(f"/projects/{pid}/milestones/build", json={}).json()
    assert again["added"] == 0


def test_without_a_stormwater_plan_there_are_no_stormwater_items(db_session, office):
    pid = _project(db_session, with_docs=False)
    keys = {i["template_key"] for i in office.post(f"/projects/{pid}/milestones/build", json={}).json()["items"]}
    assert "storm.engineer" not in keys and "ff.structural" in keys


def test_business_days_skip_weekends():
    assert svc.minus_business_days(date(2026, 10, 19), 3) == date(2026, 10, 14)  # Mon -> Wed
    assert svc.minus_business_days(date(2026, 10, 20), 1) == date(2026, 10, 19)


def _draft(db, pid, **kw):
    m = ProjectMilestone(project_id=pid, ref="M7", stage="Footings and forms", title="Formed footing review",
                         party_role="structural_engineer", status="to_book")
    db.add(m)
    db.flush()
    e = MilestoneEmail(milestone_id=m.id, project_id=pid, to_email="eng@example.com", to_name="Jordan",
                       subject="1234 Example Way — formed footing review", body="Hi Jordan, please attend Monday.",
                       status="draft", **kw)
    db.add(e)
    db.flush()
    return m, e


def test_nothing_is_sent_without_confirmation_or_with_a_placeholder(db_session, office, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.email.deliver", lambda msg, rcpts=None: sent.append((msg, rcpts)))
    pid = _project(db_session, with_docs=False)
    _m, e = _draft(db_session, pid)
    assert office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={}).status_code == 428
    e.body = "Please attend on [confirm date]."
    db_session.flush()
    r = office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={"confirm": True})
    assert r.status_code == 422 and "placeholder" in r.text
    assert sent == []


def test_a_confirmed_email_bccs_the_project_and_files_replies_back(db_session, office, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.email.deliver", lambda msg, rcpts=None: sent.append((msg, rcpts)))
    pid = _project(db_session, with_docs=False)
    m, e = _draft(db_session, pid)
    r = office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={"confirm": True})
    assert r.status_code == 200, r.text
    msg, rcpts = sent[0]
    proj = r.json()["project_address"]
    assert msg["Subject"].endswith("[KS-M7]") and "Bcc" not in msg  # a BCC is a recipient, never a header
    assert proj in rcpts and "eng@example.com" in rcpts
    assert proj in msg["Reply-To"] and "pm@t.local" in msg["Reply-To"]
    db_session.refresh(m)
    assert m.status == "requested" and m.thread[-1]["direction"] == "out"
    assert office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={"confirm": True}).status_code == 409
    # Our own BCC copy arrives at the project address: recognised, not a reply.
    from app.config import settings

    own = svc.link_inbound(db_session, pid, subject=msg["Subject"], from_email=settings.aws_ses_from_email,
                           communication_id="c1", document_ids=[])
    assert own == {"milestone": "M7", "own_copy": True}
    reply = svc.link_inbound(db_session, pid, subject=f"RE: {msg['Subject']}", from_email="eng@example.com",
                             communication_id="c2", document_ids=["d1"])
    assert reply == {"milestone": "M7", "own_copy": False}
    db_session.refresh(m)
    assert m.thread[-1]["direction"] == "in" and m.thread[-1]["document_ids"] == ["d1"]


def test_an_ses_refusal_leaves_a_draft_with_the_reason(db_session, office, monkeypatch):
    def boom(msg, rcpts=None):
        raise RuntimeError("Email address is not verified")

    monkeypatch.setattr("app.services.email.deliver", boom)
    pid = _project(db_session, with_docs=False)
    _m, e = _draft(db_session, pid)
    r = office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={"confirm": True})
    assert r.status_code == 502 and "not verified" in r.text
    got = office.get(f"/projects/{pid}/milestone-emails/{e.id}").json()
    assert got["status"] == "draft" and "not verified" in got["error"]


def test_the_draft_request_queues_a_job_and_never_sends(db_session, office, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.email.deliver", lambda msg, rcpts=None: sent.append(msg))
    pid = _project(db_session)
    items = office.post(f"/projects/{pid}/milestones/build", json={}).json()["items"]
    ff = next(i for i in items if i["template_key"] == "ff.structural")
    office.put(f"/projects/{pid}/team/structural_engineer",
               json={"name": "Jordan Engineer", "company": "GEO Engineers", "email": "engineer@geo.example"})
    r = office.post(f"/projects/{pid}/milestones/{ff['id']}/draft", json={"instructions": "ask about the wall forms"})
    assert r.status_code == 202, r.text
    e = db_session.get(MilestoneEmail, r.json()["email_id"])
    assert (e.status, e.to_email) == ("drafting", "engineer@geo.example")
    from app.models.ai_job import AIJob

    job = db_session.get(AIJob, r.json()["job_id"])
    ctx = job.input["context"]
    assert job.kind == "email_draft" and ctx["project"]["permit_number"] == "BLD2026-00000"
    assert ctx["item"]["need_by"] == "2026-10-19" and job.input["instructions"] == "ask about the wall forms"
    assert sent == []
    # 2026-10-17: a second `_sender` in the router replaced this one and drafts
    # were signed with a bare From line, no company or phone.
    assert isinstance(ctx["sender"], dict) and ctx["sender"]["phone"] and ctx["sender"]["company"]


def test_the_drafter_reply_is_read_tolerantly():
    assert parse_email_draft('```json\n{"subject": "S", "body": "B"}\n```') == {"subject": "S", "body": "B"}
    with pytest.raises(ValueError):
        parse_email_draft('{"subject": "", "body": "x"}')


def test_crew_cannot_reach_milestones(db_session):
    pid = _project(db_session, with_docs=False)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=str(uuid4()), email="c@t.local", role="crew")
    try:
        assert TestClient(app).get(f"/projects/{pid}/milestones").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_items_anchor_to_the_right_activity_not_the_first_that_mentions_a_word(db_session, office):
    """Live on 1234 (2026-10-16): "infiltration" matched the tank install before
    the District's before-cover inspection, and "excavat" matched the locate."""
    pid = _project(db_session)
    items = {i["template_key"]: i for i in office.post(f"/projects/{pid}/milestones/build", json={}).json()["items"]}
    assert items["storm.dnv"]["activity_code"] == "A125" and items["storm.engineer"]["activity_code"] == "A125"
    assert items["exc.esc"]["activity_code"] == "A40" and items["exc.letter"]["activity_code"] == "A40"


def test_the_drafter_is_told_the_authority_by_name(db_session, office):
    pid = _project(db_session)
    office.put(f"/projects/{pid}/team/municipality", json={"company": "District of North Vancouver"})
    items = office.post(f"/projects/{pid}/milestones/build", json={}).json()["items"]
    ff = next(i for i in items if i["template_key"] == "ff.structural")
    r = office.post(f"/projects/{pid}/milestones/{ff['id']}/draft", json={})
    from app.models.ai_job import AIJob
    from app.services.milestone_email import SYSTEM

    assert db_session.get(AIJob, r.json()["job_id"]).input["context"]["municipality"] == "District of North Vancouver"
    assert "never \"the City\"" in SYSTEM


def test_a_pm_on_the_verified_domain_sends_as_themselves_and_the_copy_is_not_a_reply(db_session, office, monkeypatch):
    """Amir, 2026-10-17: send from amir@builtbybrio.com so the record is kept.
    Before this, the BCC copy was told apart from a reply only by the default
    sender; sent from the PM's own address it would have been filed as a reply."""
    from app.config import settings

    sent = []
    monkeypatch.setattr("app.services.email.deliver", lambda msg, rcpts=None: sent.append((msg, rcpts)))
    monkeypatch.setattr(settings, "ses_sender_domains", "builtbybrio.com")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=None, email="amir@builtbybrio.com",
                                                                     role="superadmin")
    pid = _project(db_session, with_docs=False)
    m, e = _draft(db_session, pid)
    r = office.post(f"/projects/{pid}/milestone-emails/{e.id}/send", json={"confirm": True})
    assert r.status_code == 200, r.text
    msg, rcpts = sent[0]
    assert "amir@builtbybrio.com" in msg["From"] and "via Keystone" not in msg["From"]
    assert r.json()["project_address"] in rcpts and r.json()["project_address"] in msg["Reply-To"]
    own = svc.link_inbound(db_session, pid, subject=msg["Subject"], from_email="amir@builtbybrio.com",
                           communication_id="c1", document_ids=[])
    assert own == {"milestone": "M7", "own_copy": True}
    mine = svc.link_inbound(db_session, pid, subject=f"RE: {msg['Subject']}", from_email="amir@builtbybrio.com",
                            communication_id="c2", document_ids=[])
    assert mine == {"milestone": "M7", "own_copy": False}


def test_an_address_off_the_verified_domains_goes_out_via_keystone(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ses_sender_domains", "builtbybrio.com")
    monkeypatch.setattr(settings, "aws_ses_from_email", "noreply@builtbybrio.com")
    assert svc.sender_for("amir@sister-co.example", "Amir") == "Amir via Keystone <noreply@builtbybrio.com>"
    assert svc.sender_for("Amir@BuiltByBrio.com", "Amir Omidvar") == "Amir Omidvar <Amir@BuiltByBrio.com>"


def test_the_confirmation_shows_the_from_line_it_will_send(db_session, office, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ses_sender_domains", "builtbybrio.com")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=None, email="amir@builtbybrio.com",
                                                                     role="superadmin")
    pid = _project(db_session, with_docs=False)
    _, e = _draft(db_session, pid)
    assert office.get(f"/projects/{pid}/milestone-emails/{e.id}").json()["from_as_sent"] == "amir@builtbybrio.com"
