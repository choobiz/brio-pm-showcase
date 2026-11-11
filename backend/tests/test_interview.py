"""The guided interview — what it asks, and what it refuses to claim.

Two properties carry the weight:

  · A QUESTION APPEARS BECAUSE SOMETHING IS DEMONSTRABLY MISSING, and stops
    appearing when it is not. That is what bounds the set and makes reaching the
    end of it mean something.

  · NO DOLLAR FIGURE IS INVENTED. The approved wireframe shows "$19,700 I am
    guessing" and "swings roughly $14,000". Those cannot be derived before an
    estimate exists, and a plausible invented one on the screen where an
    estimator decides what to chase is worse than none.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import interview as I  # noqa: E402


def _project(db, *, type_=None, year=None, name="Interview"):
    pid = uuid4()
    db.execute(_T("""insert into projects (id, name, slug, status, type, building_age)
                     values (:i,:n,:s,'preconstruction',:t,:y)"""),
               {"i": str(pid), "n": name, "s": f"iv-{str(pid)[:8]}",
                "t": type_, "y": year})
    return pid


def _ids(bundle):
    return {q["id"] for q in [*bundle["blocking"], *bundle["material"]]}


# --- questions appear only when something is missing ------------------------

def test_an_unclassified_project_is_asked_for_its_type_and_it_blocks(db_session):
    pid = _project(db_session, type_=None)
    iv = I.build_interview(db_session, pid)
    q = next(q for q in iv["blocking"] if q["id"] == "project_type")
    assert q["blocking"] is True
    assert iv["can_estimate"] is False
    # The question carries its price, which is the whole point of asking it
    # this way rather than as a form field.
    assert "no division set" in q["why"].lower() or "NO division set" in q["why"]


def test_a_classified_project_is_not_asked_again(db_session):
    pid = _project(db_session, type_="residential_new")
    assert "project_type" not in _ids(I.build_interview(db_session, pid))


def test_year_built_is_asked_only_for_work_on_existing_fabric(db_session):
    """A new build has no existing fabric, so asbestos cannot arise. Asking
    anyway trains people to answer questions that do not matter."""
    new = _project(db_session, type_="residential_new")
    reno = _project(db_session, type_="residential_reno")
    assert "year_built" not in _ids(I.build_interview(db_session, new))
    assert "year_built" in _ids(I.build_interview(db_session, reno))


def test_a_recorded_year_stops_the_question(db_session):
    pid = _project(db_session, type_="residential_reno", year=1985)
    assert "year_built" not in _ids(I.build_interview(db_session, pid))


def test_the_year_question_names_the_hazmat_threshold(db_session):
    pid = _project(db_session, type_="commercial_ti")
    q = next(q for q in I.build_interview(db_session, pid)["material"]
             if q["id"] == "year_built")
    assert str(I.HAZMAT_YEAR) in q["why"]


# --- answering shrinks the set ----------------------------------------------

def test_answers_remove_questions_without_writing_anything(db_session):
    pid = _project(db_session, type_=None)
    before = I.build_interview(db_session, pid)
    after = I.build_interview(db_session, pid, {"project_type": "residential_new"})
    assert "project_type" in _ids(before)
    assert "project_type" not in _ids(after)
    # Nothing was written: the project is still unclassified.
    t = db_session.execute(_T("select type from projects where id = :i"),
                           {"i": str(pid)}).scalar()
    assert t is None


# --- the discipline about figures -------------------------------------------

def test_no_question_states_a_dollar_amount(db_session):
    """The wireframe's "$19,700" and "$14,000" cannot be derived before an
    estimate exists. A plausible invented figure is worse than none."""
    pid = _project(db_session, type_="residential_reno")
    iv = I.build_interview(db_session, pid)
    money = re.compile(r"\$\s?[\d,]+")
    for q in [*iv["blocking"], *iv["material"]]:
        assert not money.search(q["question"]), q["question"]
        assert not money.search(q["why"]), q["why"]
    assert "cannot be derived before an estimate exists" in iv["no_figures_note"]


# --- it terminates with a choice --------------------------------------------

def test_the_scale_is_declared_up_front(db_session):
    """An open-ended interrogation is worse than a form. A reader needs to know
    it ends before they will start it."""
    pid = _project(db_session, type_=None)
    iv = I.build_interview(db_session, pid)
    assert re.match(r"\d+ things? blocks?, \d+ would change the number\.", iv["summary"])


def test_stopping_is_always_allowed_and_said_so(db_session):
    pid = _project(db_session, type_="residential_new")
    iv = I.build_interview(db_session, pid)
    assert "Stop whenever you like" in iv["stopping_rule"]
    # It used to promise answers were "written into the scope's
    # information-required list". Nothing ever wrote them; it says what is true.
    assert "until it is answered" in iv["stopping_rule"]


def test_outstanding_questions_become_scope_lines(db_session):
    """They land in the scope's existing informationRequired list rather than a
    parallel place for open questions to be forgotten in."""
    pid = _project(db_session, type_="residential_reno")
    lines = I.outstanding_for_scope(db_session, pid)
    assert lines and all(" — " in ln for ln in lines)
    assert any("year" in ln.lower() for ln in lines)


def test_judgement_questions_are_asked_because_nothing_else_will(db_session):
    """Shell-vs-finished swings a budget and nothing else asks it. The finish
    tier is no longer asked (2026-10-13): finishes are asked one by one, and
    only once the space is known to be finished."""
    pid = _project(db_session, type_="residential_new")
    ids = _ids(I.build_interview(db_session, pid))
    assert "shell_or_finished" in ids
    assert "finish_level" not in ids and "finishes" not in ids


def test_a_project_that_is_gone_raises_rather_than_returning_an_empty_set(db_session):
    """An empty interview reads as "nothing outstanding", which about a missing
    project is the opposite of true."""
    with pytest.raises(ValueError):
        I.build_interview(db_session, uuid4())


def test_a_project_with_no_drawings_gets_one_question_not_nine(db_session):
    """⚠️ FOUND BY RUNNING IT AGAINST REAL PROJECTS. A project with nothing
    uploaded produced nine near-identical questions — "are there architectural
    drawings", "are there structural drawings", and so on — which is exactly the
    open-ended interrogation §11b warns is worse than a form. When EVERY
    discipline is missing the useful question is the single one."""
    pid = _project(db_session, type_="residential_new")
    ids = _ids(I.build_interview(db_session, pid))
    per_discipline = [i for i in ids if i.startswith("drawings_") and i != "drawings_none"]
    assert not per_discipline, f"asked per-discipline with nothing uploaded: {per_discipline}"
    assert "drawings_none" in ids


def test_one_blocker_reads_as_blocks_not_block(db_session):
    pid = _project(db_session, type_=None)
    assert I.build_interview(db_session, pid)["summary"].startswith("1 thing blocks,")


# --- the drawing analysis is asked exactly when the estimate gate refuses ----

def _cad_doc(db, pid):
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, phase, size_bytes) "
                  "values (:i, :p, 'Plan.dxf', :k, 'drawing', 'design', 1024)"),
               {"i": str(uuid4()), "p": str(pid), "k": f"t/{uuid4()}"})


def _pack(db, pid, status):
    db.execute(_T("insert into drawing_analysis_packs (id, project_id, version_number, status, payload) "
                  "values (:i, :p, 1, :s, '{}'::jsonb)"),
               {"i": str(uuid4()), "p": str(pid), "s": status})


def test_cad_never_analysed_blocks_like_the_gate_does(db_session):
    db = db_session
    pid = _project(db, type_="residential_new", year=2000)
    _cad_doc(db, pid)
    iv = I.build_interview(db, pid)
    q = next(q for q in iv["blocking"] if q["id"] == "analysis_unsigned")
    assert "not been analysed" in q["question"]
    assert iv["can_estimate"] is False


def test_an_approved_analysis_is_not_asked_about(db_session):
    # PACK_STATUSES is draft | approved | superseded. Testing for "signed" — a
    # status that does not exist — kept an approved analysis blocking forever.
    db = db_session
    pid = _project(db, type_="residential_new", year=2000)
    _cad_doc(db, pid)
    _pack(db, pid, "approved")
    assert "analysis_unsigned" not in _ids(I.build_interview(db, pid))


def test_a_draft_analysis_still_blocks(db_session):
    db = db_session
    pid = _project(db, type_="residential_new", year=2000)
    _cad_doc(db, pid)
    _pack(db, pid, "draft")
    q = next(q for q in I.build_interview(db, pid)["blocking"] if q["id"] == "analysis_unsigned")
    assert q["current"] == "draft"


def test_no_cad_means_no_analysis_question(db_session):
    db = db_session
    pid = _project(db, type_="residential_new", year=2000)
    assert "analysis_unsigned" not in _ids(I.build_interview(db, pid))


# --- answers live on the project (migration 0066) ----------------------------

def _set(db, pid, **cols):
    for k, v in cols.items():
        if k in ("drawing_decisions", "finish_selections"):
            db.execute(_T(f"update projects set {k} = cast(:v as jsonb) where id = :p"),
                       {"v": __import__("json").dumps(v), "p": str(pid)})
        else:
            db.execute(_T(f"update projects set {k} = :v where id = :p"), {"v": v, "p": str(pid)})


def test_a_finished_space_asks_for_its_finishes_and_a_shell_does_not(db_session):
    pid = _project(db_session, type_="residential_new")
    _set(db_session, pid, delivered_as="finished")
    assert "finishes" in _ids(I.build_interview(db_session, pid))
    _set(db_session, pid, delivered_as="shell")
    assert "finishes" not in _ids(I.build_interview(db_session, pid))


def test_the_finishes_question_goes_once_all_are_confirmed(db_session):
    from app.services.finish_spec import KEYS

    pid = _project(db_session, type_="residential_new")
    _set(db_session, pid, delivered_as="part_shell",
         finish_selections={k: {"answer": "x", "confirmed": True} for k in KEYS})
    assert "finishes" not in _ids(I.build_interview(db_session, pid))


def test_a_recorded_delivery_stops_the_shell_question(db_session):
    pid = _project(db_session, type_="residential_new")
    _set(db_session, pid, delivered_as="shell")
    assert "shell_or_finished" not in _ids(I.build_interview(db_session, pid))


def test_every_answerable_option_maps_to_a_stored_value(db_session):
    """The page answers in place by writing `answer.values[i]` for option i. A
    values list shorter than the options would write the wrong thing, silently."""
    pid = _project(db_session, type_=None)
    iv = I.build_interview(db_session, pid)
    for q in [*iv["blocking"], *iv["material"]]:
        a = q.get("answer")
        if a and "values" in a:
            assert len(a["values"]) == len(q["options"]), q["id"]


def test_judgement_answers_name_their_column(db_session):
    pid = _project(db_session, type_="residential_new")
    iv = I.build_interview(db_session, pid)
    by_id = {q["id"]: q for q in iv["material"]}
    assert by_id["shell_or_finished"]["answer"]["field"] == "delivered_as"


def test_price_from_scope_answers_the_no_drawings_question(db_session):
    pid = _project(db_session, type_="residential_new")
    assert "drawings_none" in _ids(I.build_interview(db_session, pid))
    _set(db_session, pid, drawing_decisions={"all": "allowance"})
    ids = _ids(I.build_interview(db_session, pid))
    assert "drawings_none" not in ids
    # …and does not fall through into nine per-discipline questions.
    assert not [i for i in ids if i.startswith("drawings_")]


def test_no_action_points_into_the_preview_workspace(db_session):
    """/workspace is being retired; the live app owns every answer now."""
    pid = _project(db_session, type_="residential_reno")
    iv = I.build_interview(db_session, _project(db_session, type_=None))
    iv2 = I.build_interview(db_session, pid)
    for q in [*iv["blocking"], *iv["material"], *iv2["blocking"], *iv2["material"]]:
        assert not (q.get("action") or "").startswith("/workspace"), q["id"]
