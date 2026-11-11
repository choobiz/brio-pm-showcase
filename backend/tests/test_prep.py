"""Prepared documents: lifecycle, Mac-first dispatch, the monthly cap (only
Amir raises it), the API engine, worker auth, tiling and the PDF."""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.prep import AppSetting, PreparedDocument, PrepRequest, WorkerHeartbeat  # noqa: E402
from app.services import prep as P  # noqa: E402
from app.services import prep_spend as S  # noqa: E402

AMIR = CurrentUser(id=str(uuid4()), email="amir@builtbybrio.com", role="superadmin")
OTHER = CurrentUser(id=str(uuid4()), email="pm@builtbybrio.com", role="superadmin")

PLAN_Q = {"summary": "Lumber order and rental request", "documents": [{"kind": "order", "title": "Forming Lumber Order"}],
          "questions": [{"id": "delivery", "question": "Delivery date?", "suggested": "Wed 14 Oct"}]}
DRAFT = {"documents": [{"kind": "order", "title": "Forming Lumber Order", "supplier": "Sample Lumber",
                        "header": [{"label": "Supplier", "value": "Sample Lumber"}],
                        "sections": [{"heading": "Materials", "kind": "table",
                                      "rows": [{"item": "2x10 SPF #2", "spec": "16 ft", "qty": 20, "unit": "pcs",
                                                "basis": "SECRET-BASIS 290 lf + 10%", "source": "GEO S1"}]}],
                        "notes": "Confirm pricing."}],
         "conflicts": ["Detail B is titled but not drawn"],
         "drawing_facts": []}


@pytest.fixture(autouse=True)
def _clean(db_session):
    db_session.execute(_T("CREATE TABLE IF NOT EXISTS schedules (id uuid PRIMARY KEY, project_id uuid, "
                          "created_at timestamptz DEFAULT now())"))
    """Seeds and heartbeats from other runs must not leak into these tests."""
    db_session.execute(_T("DELETE FROM worker_heartbeats"))
    db_session.execute(_T("DELETE FROM prep_requests"))
    db_session.execute(_T("DELETE FROM app_settings WHERE key = :k"), {"k": S.CAP_KEY})
    db_session.execute(_T("DELETE FROM ai_spend"))
    for u in (AMIR, OTHER):  # the CurrentUser emails decide the cap; the rows only satisfy FKs
        db_session.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
                           {"i": u.id, "e": f"t-{u.id[:8]}@example.com"})


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i, 'Example', :s, 'in_progress')"),
               {"i": str(pid), "s": f"pr-{pid.hex[:8]}"})
    return pid


def test_queue_goes_straight_to_the_api_when_the_mac_is_away(db_session):
    req = P.create(db_session, _project(db_session), AMIR.id, "Forming lumber order", [], approve_usd=5)
    assert req.status == "queued" and req.api_after <= P.now()
    assert P.claim(db_session, "api").id == req.id


def test_a_live_mac_gets_first_claim(db_session):
    P.heartbeat(db_session, {"host": "mac"})
    req = P.create(db_session, _project(db_session), AMIR.id, "Forming lumber order", [], approve_usd=5)
    assert req.api_after > P.now() + timedelta(seconds=60)
    assert P.claim(db_session, "api") is None  # still the Mac's
    got = P.claim(db_session, P.MAC)
    assert got.id == req.id and got.engine == "mac" and got.status == "running"


def test_a_team_members_request_goes_to_the_mac_first_then_the_server(db_session):
    """Amir, 2026-10-15: the Mac runs the team's Prepare requests too, with the
    server as the fallback. Pinned so a later "only Amir's requests on the Mac"
    change has to be a decision, not a side effect."""
    pid = _project(db_session)
    manager = uuid4()
    db_session.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
                       {"i": str(manager), "e": f"m-{manager.hex[:8]}@example.com"})
    P.heartbeat(db_session, {"host": "mac"})
    req = P.create(db_session, pid, str(manager), "Forming lumber order", [], approve_usd=1)
    assert P.claim(db_session, "api") is None
    assert P.claim(db_session, P.MAC).id == req.id
    db_session.execute(_T("DELETE FROM worker_heartbeats"))
    req2 = P.create(db_session, pid, str(manager), "Rental request", [], approve_usd=1)
    assert P.claim(db_session, "api").id == req2.id  # Mac off: the server takes it at once


def test_the_whole_lifecycle(db_session, monkeypatch):
    pid = _project(db_session)
    req = P.create(db_session, pid, AMIR.id, "Forming lumber order", [], approve_usd=5)
    P.claim(db_session, "api")
    P.apply_result(db_session, req, PLAN_Q, engine="api")
    assert req.status == "waiting_user" and req.plan["questions"][0]["id"] == "delivery"

    P.answer(db_session, req, {"delivery": "Wed 14 Oct"})
    assert (req.status, req.stage) == ("queued", "draft")
    P.claim(db_session, "api")
    P.apply_result(db_session, req, DRAFT, engine="api")
    docs = db_session.query(PreparedDocument).filter_by(prep_request_id=req.id).all()
    assert req.status == "waiting_user" and len(docs) == 1 and docs[0].version == 1
    assert req.plan["conflicts"] == ["Detail B is titled but not drawn"]

    P.revise(db_session, req, "Stakes are bought, not cut", remember=True, user_id=AMIR.id)
    assert req.stage == "revise"
    rule = db_session.execute(_T("select rule from company_practices where rule = 'Stakes are bought, not cut'")).scalar()
    assert rule  # remembered as a BRIO practice
    P.claim(db_session, "api")
    P.apply_result(db_session, req, DRAFT, engine="api")
    doc = db_session.query(PreparedDocument).filter_by(prep_request_id=req.id).one()
    assert doc.version == 2

    stored = {}
    import app.services.r2 as r2

    monkeypatch.setattr(r2, "put_bytes", lambda k, b, ct: stored.update({k: b}))
    import app.services.prep_pdf as pdf  # WeasyPrint needs Pango, which only the server image has

    monkeypatch.setattr(pdf, "render", lambda title, payload: b"%PDF-" + pdf.build_html(title, payload).encode())
    P.finalize(db_session, req, AMIR.id)
    doc = db_session.query(PreparedDocument).filter_by(prep_request_id=req.id).one()
    row = db_session.execute(_T("select category, client_visible from documents where id = :d"),
                             {"d": str(doc.document_id)}).one()
    assert req.status == "finalized" and doc.status == "final"
    assert tuple(row) == ("prepared", False)
    assert list(stored.values())[0][:4] == b"%PDF"


def test_a_malformed_result_fails_the_stage_instead_of_storing_it(db_session):
    req = P.create(db_session, _project(db_session), AMIR.id, "x order", [], approve_usd=5)
    P.claim(db_session, "api")
    P.apply_result(db_session, req, {"documents": "not a list"}, engine="mac")
    assert req.status == "failed" and "wrong shape" in req.error


def test_only_amir_changes_the_cap(db_session):
    assert S.cap(db_session) == Decimal("8")
    with pytest.raises(HTTPException) as e:
        S.set_cap(db_session, OTHER, Decimal("20"))
    assert e.value.status_code == 403
    S.set_cap(db_session, AMIR, Decimal("12"))
    assert S.cap(db_session) == Decimal("12")


def test_cost_uses_the_published_prices():
    u = {"input_tokens": 18674, "output_tokens": 2478}
    assert S.cost("claude-opus-5-5", u) == Decimal("0.1243")  # the tiled structural read
    assert S.cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}) == Decimal("2.1")


class _Fake:
    def __init__(self, result):
        self.n = 0
        self.messages = SimpleNamespace(create=self._create)
        self._result = result

    def _create(self, **kw):
        self.n += 1
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=10_000, output_tokens=1_000),
                               stop_reason="tool_use",
                               content=[{"type": "tool_use", "id": "s1", "name": "submit",
                                         "input": {"result": self._result}}])


def test_the_api_engine_submits_and_records_its_cost(db_session):
    from app.services import prep_engine_api as E

    req = P.create(db_session, _project(db_session), AMIR.id, "order", [], approve_usd=5)
    P.claim(db_session, "api")
    E.run(db_session, req, _Fake(PLAN_Q))
    assert req.status == "waiting_user"
    assert req.cost_usd == Decimal("0.0300")  # 10k in + 1k out on Sonnet 5.5
    assert S.month_spend(db_session, "prep") == Decimal("0.0300")


def test_the_api_engine_stops_at_the_cap(db_session):
    from app.services import prep_engine_api as E

    S.set_cap(db_session, AMIR, Decimal("0.01"))
    req = P.create(db_session, _project(db_session), AMIR.id, "order", [], approve_usd=5)
    P.claim(db_session, "api")
    E.run(db_session, req, _Fake(PLAN_Q))
    assert req.status == "failed" and "budget" in req.error


def test_worker_routes_need_the_shared_secret(monkeypatch):
    from app.config import settings
    from app.routers.prep import require_worker

    monkeypatch.setattr(settings, "prep_worker_token", "")
    with pytest.raises(HTTPException) as e:
        require_worker("anything")
    assert e.value.status_code == 503
    monkeypatch.setattr(settings, "prep_worker_token", "s3cret")
    with pytest.raises(HTTPException) as e:
        require_worker("wrong")
    assert e.value.status_code == 401
    require_worker("s3cret")


def test_a_large_sheet_is_tiled_and_a_small_page_is_not():
    from PIL import Image

    from app.services.prep_tools import split

    assert len(split(Image.new("RGB", (7200, 4800)))) == 6
    assert len(split(Image.new("RGB", (2200, 3400)))) == 1


def test_the_pdf_never_prints_the_office_working():
    from app.services.prep_pdf import build_html

    html = build_html("Forming Lumber Order", {**DRAFT["documents"][0]})
    assert "2x10 SPF #2" in html and "Sample Lumber" in html
    assert "SECRET-BASIS" not in html and "GEO S1" not in html


class _Script:
    """Replays a list of (stop_reason, content) turns."""

    def __init__(self, turns):
        self.turns, self.n = turns, 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        stop, content = self.turns[self.n]
        self.n += 1
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               stop_reason=stop, content=content)


def _draft_req(db):
    req = P.create(db, _project(db), AMIR.id, "order", [], approve_usd=5)
    P.claim(db, "api")
    P.apply_result(db, req, PLAN_Q, engine="api")
    P.answer(db, req, {"delivery": "Wed 14 Oct"})
    P.claim(db, "api")
    return req


def test_the_api_draft_is_assembled_one_document_at_a_time(db_session):
    from app.services import prep_engine_api as E

    req = _draft_req(db_session)
    doc = DRAFT["documents"][0]
    E.run(db_session, req, _Script([
        ("tool_use", [{"type": "tool_use", "id": "a", "name": "submit_document", "input": {"document": doc}}]),
        ("tool_use", [{"type": "tool_use", "id": "b", "name": "submit_document", "input": {"document": {**doc, "title": "Rental"}}}]),
        ("tool_use", [{"type": "tool_use", "id": "c", "name": "finish", "input": {"conflicts": ["Detail B"]}}]),
    ]))
    titles = [d.title for d in db_session.query(PreparedDocument).filter_by(prep_request_id=req.id).order_by(PreparedDocument.position)]
    assert req.status == "waiting_user" and titles == ["Forming Lumber Order", "Rental"]
    assert req.plan["conflicts"] == ["Detail B"]


def test_a_cut_off_reply_is_never_acted_on(db_session):
    """1234, 2026-10-13: a draft cut off at the output limit was stored as a
    malformed result. A cut-off turn is now ignored and the model asked to
    submit in smaller pieces."""
    from app.services import prep_engine_api as E

    req = _draft_req(db_session)
    doc = DRAFT["documents"][0]
    script = _Script([
        ("max_tokens", [{"type": "tool_use", "id": "x", "name": "submit_document", "input": {"document": {"title": "half"}}}]),
        ("tool_use", [{"type": "tool_use", "id": "a", "name": "submit_document", "input": {"document": doc}}]),
        ("tool_use", [{"type": "tool_use", "id": "b", "name": "finish", "input": {}}]),
    ])
    E.run(db_session, req, script)
    titles = [d.title for d in db_session.query(PreparedDocument).filter_by(prep_request_id=req.id)]
    assert req.status == "waiting_user" and titles == ["Forming Lumber Order"]


def test_a_document_and_finish_in_the_same_reply_is_not_a_failure(db_session):
    """Rollout test 2026-10-14, Mac off: the model sent submit_document and
    finish in ONE reply. finish was checked before the document was taken, so
    the draft failed with "finished without submitting any document" after
    spending $0.10. Documents in a reply are taken before its finish."""
    from app.services import prep_engine_api as E

    req = _draft_req(db_session)
    doc = DRAFT["documents"][0]
    E.run(db_session, req, _Script([
        ("tool_use", [{"type": "tool_use", "id": "a", "name": "submit_document", "input": {"document": doc}},
                      {"type": "tool_use", "id": "b", "name": "finish", "input": {"assumptions": ["8 ft wall"]}}]),
    ]))
    titles = [d.title for d in db_session.query(PreparedDocument).filter_by(prep_request_id=req.id)]
    assert req.status == "waiting_user", req.error
    assert titles == ["Forming Lumber Order"]


def test_finish_with_a_rejected_document_in_the_same_reply_asks_again(db_session):
    """A finish that arrives with a document the shape check rejected must not
    finish: the model is told what was wrong and resubmits."""
    from app.services import prep_engine_api as E

    req = _draft_req(db_session)
    doc = DRAFT["documents"][0]
    script = _Script([
        ("tool_use", [{"type": "tool_use", "id": "a", "name": "submit_document", "input": {"document": {"title": 7}}},
                      {"type": "tool_use", "id": "b", "name": "finish", "input": {}}]),
        ("tool_use", [{"type": "tool_use", "id": "c", "name": "submit_document", "input": {"document": doc}},
                      {"type": "tool_use", "id": "d", "name": "finish", "input": {}}]),
    ])
    E.run(db_session, req, script)
    titles = [d.title for d in db_session.query(PreparedDocument).filter_by(prep_request_id=req.id)]
    assert script.n == 2 and req.status == "waiting_user" and titles == ["Forming Lumber Order"]


def test_a_draft_summary_never_replaces_a_full_drawing_read(db_session):
    from app.models.prep import DrawingFact

    pid = _project(db_session)
    did = uuid4()
    db_session.execute(_T("insert into documents (id, project_id, category, phase, filename, r2_key) "
                          "values (:i, :p, 'drawing', 'in_progress', 's.pdf', 'k')"), {"i": str(did), "p": str(pid)})
    P.save_fact(db_session, {"document_id": str(did), "page_no": 1, "facts": "FULL tiled read " * 50}, engine="api")
    req = P.create(db_session, pid, AMIR.id, "order", [], approve_usd=5)
    P.claim(db_session, "api")
    P.apply_result(db_session, req, PLAN_Q, engine="api")
    P.answer(db_session, req, {"delivery": "x"})
    P.claim(db_session, "api")
    P.apply_result(db_session, req, {**DRAFT, "drawing_facts": [{"document_id": str(did), "page_no": 1, "facts": "short"}]},
                   engine="api")
    assert db_session.query(DrawingFact).filter_by(document_id=did).one().facts_md.startswith("FULL")



# ── Forming calculator, geometry, cost approval (2026-10-13) ─────────────

APPROVED_1234 = {  # the geometry behind Amir's approved forming order
    "faces": [{"name": f"f{i}", "length_ft": 130.4 / 20} for i in range(20)],
    "outside_corners": 12, "inside_corners": 8, "interior_walls": [{"name": "Garage / office", "length_ft": 17}],
}
SURVEY_1234 = {  # the BCLS layout, 10 faces summing to 36.69 m
    "faces": [{"name": n, "length_ft": m * 3.28084} for n, m in
              [("N", 11.75), ("E", 6.05), ("S1", 6.25), ("a", 1.80), ("b", 1.02), ("c", 0.55), ("d", 2.16),
               ("e", 0.53), ("S2", 2.32), ("W", 4.26)]],
    "outside_corners": 7, "inside_corners": 3, "interior_walls": [{"name": "Garage / office", "length_ft": 11.3}],
}


def test_the_calculator_reproduces_the_approved_1234_order():
    from app.services.forming_calc import DEFAULT_PARAMS, Geometry, calculate

    c = calculate(Geometry.model_validate(APPROVED_1234), DEFAULT_PARAMS)
    q = {r["calc"]: r.get("qty") for r in c["lumber"]}
    assert c["form_line_lf"] == 289.5           # approved: about 290 lf
    assert q["footing_boards"] == 20            # approved 20
    assert q["footing_stakes"] == 147           # approved 140 (+5%)
    assert q["spreaders"] == 11                 # approved 10
    assert q["splices"] == 5                    # approved 4
    assert (q["nails_0"], q["nails_1"]) == (10, 5)


def test_same_geometry_same_numbers_and_the_survey_outline_explains_the_gap():
    from app.services.forming_calc import DEFAULT_PARAMS, Geometry, calculate

    a = calculate(Geometry.model_validate(SURVEY_1234), DEFAULT_PARAMS)
    b = calculate(Geometry.model_validate(SURVEY_1234), DEFAULT_PARAMS)
    assert a == b
    q = {r["calc"]: r.get("qty") for r in a["lumber"]}
    assert (q["footing_boards"], q["footing_stakes"]) == (18, 112)
    assert a["perimeter_ft"] == 120.4


def test_a_geometry_that_cannot_close_is_refused():
    from app.services.forming_calc import Geometry

    with pytest.raises(Exception, match="4 more outside than inside"):
        Geometry.model_validate({**SURVEY_1234, "outside_corners": 8})


def test_drafting_waits_for_a_confirmed_geometry(db_session):
    pid = _project(db_session)
    req = P.create(db_session, pid, AMIR.id, "forming order", [], approve_usd=5)
    P.claim(db_session, "api")
    P.apply_result(db_session, req, {**PLAN_Q, "geometry": SURVEY_1234}, engine="api")
    db_session.flush()
    db_session.expire(req)  # read back from the database, not the in-memory dict (prod lost the flag)
    assert req.status == "waiting_user" and req.plan["geometry_to_confirm"] is True
    with pytest.raises(HTTPException) as e:
        P.answer(db_session, req, {"delivery": "x"}, approve_usd=1)
    assert e.value.status_code == 409
    P.confirm_geometry(db_session, pid, SURVEY_1234, AMIR.id)
    P.answer(db_session, req, {"delivery": "x"}, approve_usd=1)
    assert req.stage == "draft"
    assert P.job_context(db_session, req)["calculated"]["lumber"][0]["qty"] == 18


def test_calculated_numbers_are_restored_and_others_marked_estimated(db_session):
    pid = _project(db_session)
    P.confirm_geometry(db_session, pid, SURVEY_1234, AMIR.id)
    req = P.create(db_session, pid, AMIR.id, "forming order", [], approve_usd=5)
    P.claim(db_session, "api")
    P.apply_result(db_session, req, PLAN_Q, engine="api")
    P.answer(db_session, req, {"delivery": "x"}, approve_usd=1)
    P.claim(db_session, "api")
    rows = [{"calc": "footing_boards", "item": "2x10 SPF #2", "spec": "16 ft", "qty": 25, "unit": "pcs"},
            {"item": "Form oil", "spec": "5 gal", "qty": 1, "unit": "pail", "basis": "guess"}]
    P.apply_result(db_session, req, {"documents": [{"kind": "order", "title": "Forming Lumber Order",
                                                    "sections": [{"heading": "Materials", "kind": "table", "rows": rows}]}]},
                   engine="api")
    doc = db_session.query(PreparedDocument).filter_by(prep_request_id=req.id).one()
    got = doc.payload["sections"][0]["rows"]
    assert got[0]["qty"] == 18 and got[0]["source"].startswith("calculator")
    assert got[1]["basis"].startswith("Estimated (not calculated)")
    assert any("restored" in c for c in req.plan["conflicts"])
    assert any("not on any document" in c for c in req.plan["conflicts"])  # stakes etc. left out


def test_the_server_stops_at_what_was_approved(db_session):
    from app.services import prep_engine_api as E

    req = P.create(db_session, _project(db_session), AMIR.id, "order", [], approve_usd=0.01)
    P.claim(db_session, "api")
    E.run(db_session, req, _Fake(PLAN_Q))  # one call costs $0.03
    assert req.status == "failed" and "you approved" in req.error


def test_nothing_runs_on_the_server_without_an_approval(db_session, monkeypatch):
    from app.services import prep_engine_api as E

    req = P.create(db_session, _project(db_session), AMIR.id, "order", [], approve_usd=0)
    db_session.flush()
    calls = []
    monkeypatch.setattr(E, "run", lambda *a, **k: calls.append(1))
    import app.db as appdb

    monkeypatch.setattr(appdb, "SessionLocal", lambda: _Ctx(db_session))
    E.run_one()
    assert calls == [] and req.status == "failed" and "no server cost was approved" in req.error


class _Ctx:
    def __init__(self, s):
        self.s = s

    def __enter__(self):
        return self.s

    def __exit__(self, *a):
        return False


def test_estimates_count_drawings_not_read_yet(db_session):
    from app.services import cost_estimate

    pid = _project(db_session)
    did = uuid4()
    db_session.execute(_T("insert into documents (id, project_id, category, phase, filename, r2_key) "
                          "values (:i, :p, 'drawing', 'in_progress', 'S1.pdf', 'k')"), {"i": str(did), "p": str(pid)})
    e = cost_estimate.prep(db_session, pid, "plan", [str(did)])
    assert e["server_usd"] == 0.50 and "S1.pdf" in e["lines"][1]["how"]
    assert cost_estimate.prep(db_session, pid, "revise", [str(did)])["server_usd"] == 0.45


def test_a_prepared_pdf_name_does_not_repeat_the_project():
    """Rollout test 2026-10-14: '1234 Example Way - Forming Lumber Order -
    1234 Example Way Footings.pdf'."""
    name = "1234 Example Way — New Garage & Office (Lee Residence)"
    assert P.prepared_filename(name, "Forming Lumber Order - 1234 Example Way Footings") == \
        "Forming Lumber Order - 1234 Example Way Footings.pdf"
    assert P.prepared_filename(name, "Crew sheet") == "1234 Example Way - Crew sheet.pdf"


def test_prepare_gets_the_opening_sizes_from_the_signed_takeoff(db_session):
    """Rollout test 2026-10-15: a manager's door and window price request on ZZ
    asked for sizes the signed takeoff already held."""
    from app.models.drawing_analysis_pack import DrawingAnalysisPack

    pid = _project(db_session)
    sheet = {"filename": "plan.dxf", "sizes": {"door": {"36x80": 2, "30x80": 1}, "window": {"48x36": 2}}}
    db_session.add(DrawingAnalysisPack(project_id=pid, version_number=1, status="draft", payload={"sheets": [sheet]},
                                       source_document_ids=[]))
    db_session.flush()
    req = P.create(db_session, pid, AMIR.id, "Door and window price request", [], approve_usd=1)
    assert P.job_context(db_session, req)["signed_takeoff_sizes"] is None  # not signed: not used
    db_session.execute(_T("update drawing_analysis_packs set status='approved' where project_id=:p"), {"p": str(pid)})
    ctx = P.job_context(db_session, req)["signed_takeoff_sizes"]
    assert ctx["sheets"] == [{"sheet": "plan.dxf", "sizes": sheet["sizes"]}]
