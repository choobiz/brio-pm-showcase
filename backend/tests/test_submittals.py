"""Submittal review (wireframes approved by Amir 2026-10-17).

What these hold, each from his answers or the 1234 tank review:
- every check in the trade's checklist is reported; one the model leaves out
  is shown as "not shown", never dropped (a dropped check reads as a pass);
- the reviewer is told to cite a label and never to join pipes by adjacency;
- the PM's calls drive the questions, grouped by who answers, as one email per
  party that Keystone never sends (BCC'd project address, [KS-Sn] tag);
- "cleared to order" is a marker: signed copy in, no critical open, outcome
  "no exceptions" or "as noted";
- a revision is checked against the one before it and keeps the trail.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _T

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.models.ai_job import AIJob
from app.models.milestone import ProjectTeamMember
from app.models.submittal import Submittal
from app.services import submittal_checklists as checklists
from app.services import submittal_review as review
from app.services.milestones import link_inbound

REPLY = {
    "summary": "Barr Plastics EcoBloc tank #2: wrap and inverts conflict with the SMP.",
    "findings": [
        {"check_key": "modules", "check": "Modules", "shop": "9 (3 x 3 x 1)", "shop_ref": "Shop p1 BOM",
         "plan": "9", "plan_ref": "SMP R3 Plate 3R3", "result": "match", "question": "should be dropped"},
        {"check_key": "wrap", "check": "Wrap", "shop": "Geomembrane all round", "shop_ref": "Shop p4",
         "plan": "Filter cloth top and 4 sides", "plan_ref": "SMP R3 Plate 3R3", "result": "critical",
         "why": "An infiltration tank needs an open base.", "ask_who": "engineer",
         "question": "Please confirm the infiltration detail before we order."},
        {"check_key": "pipe_sizes", "check": "Pipe sizes", "shop": "Not shown", "plan": "Not shown",
         "result": "not_shown", "ask_who": "supplier", "question": "Please re-quote with 4 inch connections."},
        {"check_key": "orientation", "check": "Orientation", "shop": "No north arrow", "plan": "Inlet faces Sump #4",
         "result": "maybe", "ask_who": "nobody", "question": "Please add a north arrow."},
    ],
    "quote": {"supplier": "Barr Plastics", "number": "1129540", "date": "2026-10-06", "subtotal": "$2,673.69",
              "tax": "320.90", "total": "2,994.59", "bom_matches": True, "notes": ["Assumes 6 inch connections"]},
}


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, address) values (:i, '1234 Test', :s, 'in_progress', "
                  "'1234 Example Way')"), {"i": str(pid), "s": f"sb-{pid.hex[:8]}"})
    ids = {}
    for key, name in (("shop", "REV 1 - SHOP DWG - ECOBLOC INF TANK.pdf"), ("quote", "SW Tank quote.pdf"),
                      ("smp", "1234 Example Way - SMP (R3) Drawings.pdf"), ("site", "A-101 Site plan.pdf"),
                      ("other", "Kitchen selections.pdf")):
        did = uuid4()
        db.execute(_T("insert into documents (id, project_id, category, phase, filename, r2_key, mime) "
                      "values (:i, :p, 'drawing', 'construction', :f, :k, 'application/pdf')"),
                   {"i": str(did), "p": str(pid), "f": name, "k": f"k/{did}"})
        ids[key] = str(did)
    db.flush()
    return pid, ids


@pytest.fixture
def office(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=None, email="pm@t.local", role="superadmin")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _start(office, pid, ids, **kw):
    body = {"name": "EcoBloc infiltration tank #2", "trade": "stormwater", "supplier": "Barr Plastics",
            "revision": "REV 1", "submittal_doc_ids": [ids["shop"], ids["quote"]],
            "governing_doc_ids": [ids["smp"]], "approve_usd": 2.0, **kw}
    return office.post(f"/projects/{pid}/submittals", json=body)


def _reviewed(db, sid, reply=REPLY):
    """What the worker does with the model's reply."""
    from app.workers.ai_worker import _persist_submittal_review

    s = db.get(Submittal, sid)
    job = db.get(AIJob, s.ai_job_id)
    err = _persist_submittal_review(db, job, SimpleNamespace(text=json.dumps(reply), cost_usd=0.31))
    db.flush()
    return err


def test_the_five_checklists_are_there_with_distinct_checks():
    assert set(checklists.CHECKLISTS) == {"stormwater", "trusses", "windows", "rebar", "hvac"}
    for c in checklists.CHECKLISTS.values():
        keys = [k.key for k in c.checks]
        assert len(keys) == len(set(keys)) >= 6


def test_every_check_is_reported_and_a_dropped_one_shows_as_not_shown():
    out = review.parse_review(json.dumps(REPLY), checklists.get("stormwater"), has_previous=False)
    f = {x["check_key"]: x for x in out["findings"]}
    assert len(out["findings"]) == len(checklists.STORMWATER.checks)
    assert f["volume"]["result"] == "not_shown" and "did not report" in f["volume"]["why"]
    assert f["modules"]["question"] == "" and f["modules"]["ask_who"] == "none"  # a match asks nothing
    assert (f["orientation"]["result"], f["orientation"]["ask_who"]) == ("confirm", "none")  # unknown words tamed
    assert all(x["previous"] is None for x in out["findings"])
    assert out["quote"]["subtotal"] == 2673.69 and out["quote"]["total"] == 2994.59


def test_the_reviewer_is_told_to_cite_labels_and_sides_are_labelled():
    system, msgs, _ = review.build_submittal_review_messages({
        "name": "Tank", "revision": "REV 1", "checklist": checklists.listing()[0],
        "submittal_documents": [{"caption": "shop.pdf", "data": "QQ==", "media_type": "application/pdf"}],
        "governing_documents": [{"caption": "smp.pdf", "data": "QQ==", "media_type": "application/pdf"}]})
    assert "never by an elevation" in system and '"Not shown"' in system
    texts = [c["text"] for c in msgs[0]["content"] if c["type"] == "text"]
    assert "SUBMITTAL FILE: shop.pdf" in texts and "GOVERNING FILE: smp.pdf" in texts


def test_starting_a_review_queues_one_job_and_refuses_another_projects_file(db_session, office):
    pid, ids = _project(db_session)
    other_pid, other = _project(db_session)
    r = _start(office, pid, ids)
    assert r.status_code == 201, r.text
    s = db_session.get(Submittal, r.json()["id"])
    assert (s.ref, s.status) == ("S1", "reviewing")
    job = db_session.get(AIJob, s.ai_job_id)
    assert job.kind == "submittal_review" and job.input["approve_usd"] == 2.0
    assert job.input["governing_doc_ids"] == [ids["smp"]] and job.input["previous_findings"] is None
    assert _start(office, pid, {**ids, "smp": other["smp"]}).status_code == 422
    assert _start(office, pid, ids, governing_doc_ids=[]).status_code == 422
    assert _start(office, pid, ids).json()["ref"] == "S2"


def test_suggest_picks_the_stormwater_plan_and_prices_the_pages(db_session, office):
    pid, ids = _project(db_session)
    r = office.post(f"/projects/{pid}/submittals/suggest",
                    json={"trade": "stormwater", "submittal_doc_ids": [ids["shop"]]}).json()
    assert ids["smp"] in r["suggested"] and ids["site"] in r["suggested"] and ids["other"] not in r["suggested"]
    assert r["cost"]["usd"] >= 0.25 and r["cost"]["pages"] > 0


def test_a_usable_reply_lands_and_an_unusable_one_says_why(db_session, office):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    assert _reviewed(db_session, sid) is None
    d = office.get(f"/projects/{pid}/submittals/{sid}").json()
    assert d["status"] == "needs_call" and d["counts"]["critical"] == 1 and d["quote"]["number"] == "1129540"
    assert d["history"][-1]["text"].startswith("Reviewed: 1 critical")
    sid2 = _start(office, pid, ids).json()["id"]
    from app.workers.ai_worker import _persist_submittal_review

    s2 = db_session.get(Submittal, sid2)
    err = _persist_submittal_review(db_session, db_session.get(AIJob, s2.ai_job_id),
                                    SimpleNamespace(text="I could not read the drawings.", cost_usd=0.1))
    assert err and s2.status == "review_failed" and "usable" in s2.error
    assert office.post(f"/projects/{pid}/submittals/{sid2}/run", json={"approve_usd": 2.0}).status_code == 202


def test_calls_make_one_email_per_party_never_sent_and_the_gate_is_a_marker(db_session, office):
    pid, ids = _project(db_session)
    db_session.add(ProjectTeamMember(project_id=pid, role="stormwater_engineer", name="Jordan Engineer",
                                     company="IDEA Engineers", email="engineer@geo.example"))
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    f = {x["check_key"]: x for x in office.get(f"/projects/{pid}/submittals/{sid}").json()["findings"]}
    base = f"/projects/{pid}/submittals/{sid}"
    assert office.patch(f"{base}/findings/{f['wrap']['id']}", json={"call": "ask"}).status_code == 200
    assert office.patch(f"{base}/findings/{f['pipe_sizes']['id']}", json={"call": "ask"}).status_code == 200
    assert office.patch(f"{base}/findings/{f['volume']['id']}", json={"call": "ask"}).status_code == 422  # no question
    for k in ("volume", "orientation", "base_invert", "grade_cover", "footprint", "connections", "inspection"):
        office.patch(f"{base}/findings/{f[k]['id']}", json={"call": "fine"})
    eng = office.get(f"{base}/emails/engineer").json()
    assert eng["to_email"] == "engineer@geo.example" and eng["body"].startswith("Hi Jordan,")
    assert "1. Please confirm the infiltration detail" in eng["body"] and "4 inch" not in eng["body"]
    assert "Barr Plastics' EcoBloc infiltration tank #2 (REV 1)" in eng["body"] and ".pdf" not in eng["body"]
    assert eng["subject_as_sent"].endswith("[KS-S1]") and eng["project_address"].startswith("p-")
    assert "4 inch" in office.get(f"{base}/emails/supplier").json()["body"]
    office.post(f"{base}/emails/engineer/copied")
    office.post(f"{base}/emails/supplier/copied")
    d = office.get(base).json()
    assert d["status"] == "waiting" and d["gate"]["cleared"] is False
    # The critical is still open, so a signed copy and an "as noted" don't clear it…
    d = office.patch(base, json={"signed_received": True, "outcome": "as_noted"}).json()
    assert d["gate"] == {"signed_received": True, "no_open_critical": False, "outcome_ok": True, "cleared": False}
    assert office.patch(f"{base}/findings/{f['wrap']['id']}", json={"call": "answered"}).status_code == 409
    office.patch(base, json={"outcome": None})
    office.patch(f"{base}/findings/{f['wrap']['id']}", json={"call": "answered", "call_note": "IDEA: filter cloth"})
    d = office.patch(base, json={"outcome": "as_noted"}).json()
    assert d["gate"]["cleared"] is True and d["status"] == "closed"
    assert any(h["text"] == "Cleared to order" for h in d["history"])
    assert office.patch(base, json={"outcome": "revise_resubmit"}).json()["gate"]["cleared"] is False


def test_a_revision_is_checked_against_the_last_and_keeps_the_trail(db_session, office):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    f = {x["check_key"]: x for x in office.get(f"/projects/{pid}/submittals/{sid}").json()["findings"]}
    office.patch(f"/projects/{pid}/submittals/{sid}/findings/{f['wrap']['id']}", json={"call": "ask"})
    r = office.post(f"/projects/{pid}/submittals/{sid}/revision",
                    json={"revision": "REV 2", "submittal_doc_ids": [ids["shop"]], "approve_usd": 2.0})
    assert r.status_code == 201, r.text
    new = db_session.get(Submittal, r.json()["id"])
    assert new.ref == "S1" and db_session.get(Submittal, sid).status == "superseded"
    prev = db_session.get(AIJob, new.ai_job_id).input["previous_findings"]
    assert any(p["check_key"] == "wrap" and p["call"] == "ask" for p in prev)
    reply = {**REPLY, "findings": [{**REPLY["findings"][1], "result": "match", "previous": "fixed"}]}
    _reviewed(db_session, new.id, reply)
    d = office.get(f"/projects/{pid}/submittals/{new.id}").json()
    assert next(x for x in d["findings"] if x["check_key"] == "wrap")["previous"] == "fixed"
    assert [x["revision"] for x in d["revisions"]] == ["REV 1", "REV 2"]
    assert any("REV 1 received" in h["text"] for h in d["history"])  # the trail came with it


def test_replies_tagged_ks_s_file_on_the_submittal(db_session, office):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    f = {x["check_key"]: x for x in office.get(f"/projects/{pid}/submittals/{sid}").json()["findings"]}
    office.patch(f"/projects/{pid}/submittals/{sid}/findings/{f['wrap']['id']}", json={"call": "ask"})
    subj = office.put(f"/projects/{pid}/submittals/{sid}/emails/engineer",
                      json={"subject": "1234 tank questions", "body": "Hi"}).json()["subject_as_sent"]
    own = link_inbound(db_session, pid, subject=subj, from_email="amir@builtbybrio.com", communication_id="c1",
                       document_ids=[])
    reply = link_inbound(db_session, pid, subject=f"RE: {subj}", from_email="engineer@geo.example",
                         communication_id="c2", document_ids=["d1"])
    assert own == {"submittal": "S1", "own_copy": True} and reply == {"submittal": "S1", "own_copy": False}
    s = db_session.get(Submittal, sid)
    assert [t["direction"] for t in s.thread] == ["out", "in"] and s.thread[-1]["document_ids"] == ["d1"]


def test_crew_cannot_reach_submittals(db_session):
    pid, _ = _project(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=str(uuid4()), email="c@t.local", role="crew")
    try:
        assert TestClient(app).get(f"/projects/{pid}/submittals").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_only_the_latest_revision_of_a_plan_is_suggested():
    """1234, 2026-10-17: the suggestion ticked SMP (R2) and SMP (R3) together."""
    from app.routers.submittals import latest_revisions

    rows = [SimpleNamespace(filename=f) for f in (
        "1234 Example Way - SMP (R3) Drawings.pdf", "1234 Example Way - SMP (R2) Drawings.pdf",
        "SMP-R3-Infiltration-Field-Extract.png", "A-101 Site plan.pdf")]
    assert [r.filename for r in latest_revisions(rows)] == [
        "1234 Example Way - SMP (R3) Drawings.pdf", "SMP-R3-Infiltration-Field-Extract.png", "A-101 Site plan.pdf"]


# ── Citation highlight, quote lines to the rate library, editable checklists ──
# (Amir, 2026-10-17: "Finish the ones that are not yet built.")

def _pdf(pages: list[str]) -> bytes:
    import fitz

    doc = fitz.open()
    for text in pages:
        p = doc.new_page(width=612, height=792)
        if text:
            p.insert_text((72, 300), text, fontsize=11)
    return doc.tobytes()


def test_the_citation_boxes_the_label_and_the_value_on_the_page_that_has_them():
    from app.services import submittal_cite as sc

    assert sc.needles("SMP R3 · Plate 2R3, Sump #2 label", "211.00 on Plate 2R3 vs 211.50") == [
        "Plate 2R3", "2R3", "Sump #2", "211.00", "211.50"]
    # Live audit 2026-10-17: a second citation's "Pos. 48" boxed three stray 48s on
    # p4, and "Plate 3R3" opened page 1.
    assert "48" not in sc.needles("Shop p4 · Non-woven geotextile/geomembrane; p14 · Pos. 48", "")
    assert sc.page_from("SMP R3 · Plate 3R3, Proposed section label") == 3
    assert sc.page_from("Shop p4 · x; p14 · y") == 4
    data = _pdf(["General notes", "SUMP #2 SW INV. (OVERFLOW FROM ECOBLOCK TANK 2)=210.85"])
    out = sc.render(data, "application/pdf", ref="SMP R3 · p1 · Sump #2 label", value="210.85")
    assert out["page"] == 2 and "Sump #2" in out["found"] and "210.85" in out["found"]
    assert "page 1" in out["note"] and out["image"]  # cited p1, found on p2, and says so
    scan = sc.render(_pdf([""]), "application/pdf", ref="Shop p1", value="9 modules")
    assert scan["found"] == [] and "drawn as lines or scanned" in scan["note"]
    pool = [{"filename": "REV 1 - SHOP DWG.pdf"}, {"filename": "SW Tank quote.pdf"}]
    assert sc.pick_doc(pool, "quote p1")["filename"] == "SW Tank quote.pdf"


def test_the_cite_endpoint_reads_the_cited_side(db_session, office, monkeypatch):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    monkeypatch.setattr("app.services.r2.get_bytes", lambda key: _pdf(["x", "Filter cloth top and 4 sides Plate 3R3"]))
    f = {x["check_key"]: x for x in office.get(f"/projects/{pid}/submittals/{sid}").json()["findings"]}
    r = office.get(f"/projects/{pid}/submittals/{sid}/findings/{f['wrap']['id']}/cite", params={"side": "plan"})
    assert r.status_code == 200, r.text
    assert r.json()["document"]["id"] == ids["smp"] and r.json()["page"] == 2 and "3R3" in r.json()["found"]
    assert office.get(f"/projects/{pid}/submittals/{sid}/findings/{f['wrap']['id']}/cite",
                      params={"side": "x"}).status_code == 404


def test_a_quote_line_goes_to_the_library_once_marked_with_its_source(db_session, office):
    from app.models.rate_library import MaterialRate

    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    reply = {**REPLY, "quote": {**REPLY["quote"], "lines": [
        {"description": "GRAF EcoBloc Inspect smart module", "sku": "402100", "qty": 9, "unit": "ea",
         "unit_price": "212.40", "amount": "1911.60"},
        {"description": "", "qty": 1}]}}
    _reviewed(db_session, sid, reply)
    lines = office.get(f"/projects/{pid}/submittals/{sid}").json()["quote"]["lines"]
    assert len(lines) == 1 and lines[0]["unit_price"] == 212.4 and lines[0]["material_id"] is None
    url = f"/projects/{pid}/submittals/{sid}/quote-lines/0/to-library"
    assert office.post(url, json={"name": "EcoBloc module", "unit": "furlong", "unit_cost": 212.4}).status_code == 422
    r = office.post(url, json={"name": "EcoBloc Inspect smart module", "unit": "each", "unit_cost": 212.4})
    assert r.status_code == 201, r.text
    m = db_session.get(MaterialRate, UUID(r.json()["material_id"]))
    assert (m.unit, m.supplier, m.sku, m.source) == ("ea", "Barr Plastics", "402100", "supplier")
    assert "1129540" in m.source_ref and "S1" in m.source_ref
    assert office.post(url, json={"name": "again", "unit": "ea", "unit_cost": 1}).status_code == 409
    d = office.get(f"/projects/{pid}/submittals/{sid}").json()
    assert d["quote"]["lines"][0]["material_id"] == r.json()["material_id"]
    assert "Added to the rate library" in d["history"][-1]["text"]


def test_an_edited_checklist_drives_new_reviews_and_old_ones_keep_theirs(db_session, office):
    pid, ids = _project(db_session)
    old = _start(office, pid, ids).json()["id"]
    lst = {c["key"]: c for c in office.get("/submittal-checklists").json()}
    storm = lst["stormwater"]
    body = {"label": "Stormwater (BRIO)", "engineer_role": "stormwater_engineer",
            "checks": [*storm["checks"], {"label": "Anti-flotation"}], "governing": storm["governing"],
            "governing_words": ["ISMP"]}
    r = office.put("/submittal-checklists/stormwater", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["checks"][-1]["key"] == "anti_flotation" and r.json()["edited"] is True
    new = _start(office, pid, ids).json()["id"]
    snap = db_session.get(AIJob, db_session.get(Submittal, new).ai_job_id).input["checklist"]
    assert snap["checks"][-1]["label"] == "Anti-flotation" and "ISMP" in snap["governing"]
    _reviewed(db_session, new)
    assert any(f["check_key"] == "anti_flotation" for f in office.get(f"/projects/{pid}/submittals/{new}").json()["findings"])
    _reviewed(db_session, old)  # ran with the copy from before the edit
    d = office.get(f"/projects/{pid}/submittals/{old}").json()
    assert d["trade_label"] == storm["label"] and not any(f["check_key"] == "anti_flotation" for f in d["findings"])
    # A company-added checklist, then reset: a built-in returns to default, an added one goes.
    assert office.put("/submittal-checklists/drywall", json={"label": "Drywall", "checks": [{"label": "Board type"}]}).status_code == 200
    office.delete("/submittal-checklists/stormwater")
    office.delete("/submittal-checklists/drywall")
    keys = {c["key"]: c for c in office.get("/submittal-checklists").json()}
    assert "drywall" not in keys and keys["stormwater"]["edited"] is False
    assert office.put("/submittal-checklists/x", json={"label": "X", "checks": [{"label": "a"}]}).status_code == 422
    assert office.put("/submittal-checklists/ok_key", json={"label": "X", "checks": []}).status_code == 422



# ── Audit fixes (2026-10-17): the first live run and an independent code review ──

def test_a_cut_off_reply_is_a_failed_review_and_is_repaired_once_with_more_room(db_session, office, monkeypatch):
    from app.workers import ai_worker as w

    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    s = db_session.get(Submittal, sid)
    from decimal import Decimal

    from dataclasses import replace

    from app.services.ai import AnthropicResult

    cut = AnthropicResult(text=json.dumps(REPLY)[:400], cost_usd=Decimal("0.4"), stop_reason="max_tokens",
                          output_tokens=24000, input_tokens=45000, model="m")
    assert w._persist_submittal_review(db_session, db_session.get(AIJob, s.ai_job_id), cut)
    assert s.status == "review_failed" and "cut off" in s.error
    # The worker repairs this kind even with the env switch off: more room, once.
    calls = []

    def fake(**kw):
        calls.append(kw["max_tokens"])
        return cut if len(calls) == 1 else replace(cut, text=json.dumps(REPLY), stop_reason="end_turn")

    monkeypatch.setattr(w, "_call_with_retry", fake)
    monkeypatch.setattr(w, "SELF_CORRECT_KINDS", set())
    out = w._call_self_correcting(kind="submittal_review", model="m", system=None, messages=[], max_tokens=24000,
                                  approved_usd=5.0)
    assert calls == [24000, 48000] and out.stop_reason == "end_turn"


def test_a_failed_job_never_leaves_the_review_spinning(db_session, office):
    from app.workers.ai_worker import _fail_submittal

    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    s = db_session.get(Submittal, sid)
    _fail_submittal(db_session, s.ai_job_id, "The review didn't run: APIError")
    assert s.status == "review_failed"
    assert office.post(f"/projects/{pid}/submittals/{sid}/run", json={"approve_usd": 2.0}).status_code == 202


def test_the_approval_must_cover_the_quote_and_stay_under_the_ceiling(db_session, office):
    pid, ids = _project(db_session)
    low = _start(office, pid, ids, approve_usd=0.01)
    assert low.status_code == 422 and "approve at least" in low.json()["detail"]
    assert _start(office, pid, ids, approve_usd=500).status_code == 422


def test_calls_are_safe_from_reruns_and_superseded_revisions_are_frozen(db_session, office):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    f1 = "f1"
    assert office.patch(f"/projects/{pid}/submittals/{sid}/findings/{f1}", json={"call": "fine"}).status_code == 409
    _reviewed(db_session, sid)
    assert office.post(f"/projects/{pid}/submittals/{sid}/run", json={"approve_usd": 2.0}).status_code == 409
    r = office.post(f"/projects/{pid}/submittals/{sid}/revision",
                    json={"revision": "REV 2", "submittal_doc_ids": [ids["shop"]], "approve_usd": 2.0})
    assert r.status_code == 201
    base = f"/projects/{pid}/submittals/{sid}"
    assert office.patch(base, json={"outcome": "as_noted"}).status_code == 409
    assert office.patch(base, json={"signed_received": True}).status_code == 409
    assert office.post(f"{base}/revision", json={"revision": "REV 2b", "submittal_doc_ids": [ids["shop"]],
                                                 "approve_usd": 2.0}).status_code == 409


def test_the_same_file_on_both_sides_says_so(db_session, office):
    pid, ids = _project(db_session)
    r = _start(office, pid, ids, governing_doc_ids=[ids["shop"]])
    assert r.status_code == 422 and "same file" in r.json()["detail"]


def test_waiting_means_every_party_with_questions_was_emailed(db_session, office):
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    base = f"/projects/{pid}/submittals/{sid}"
    for f in office.get(base).json()["findings"]:
        if f["result"] != "match":
            office.patch(f"{base}/findings/{f['id']}", json={"call": "ask" if f["question"] else "fine"})
    office.post(f"{base}/emails/engineer/copied")
    assert office.get(base).json()["status"] == "needs_call"  # the supplier's questions aren't out yet
    office.post(f"{base}/emails/supplier/copied")
    assert office.get(base).json()["status"] == "waiting"


def test_a_non_pdf_citation_and_a_redelivered_reply_are_handled(db_session, office):
    from app.services import submittal_cite as sc

    assert sc.render(b"PK\x03\x04 docx", "application/vnd.openxmlformats", ref="x", value="")["kind"] == "other"
    pid, ids = _project(db_session)
    sid = _start(office, pid, ids).json()["id"]
    _reviewed(db_session, sid)
    for _ in range(2):
        link_inbound(db_session, pid, subject="RE: tank [KS-S1]", from_email="e@x.com", communication_id="same",
                     document_ids=[])
    assert len(db_session.get(Submittal, sid).thread) == 1
