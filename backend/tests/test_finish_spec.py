"""Finishes asked and confirmed (2026-10-13): the Q&A, what the estimate is
told, and the shell rule enforced after the model."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.routers import finish as R  # noqa: E402
from app.services import finish_spec as F  # noqa: E402


def _project(db, delivered=None):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, delivered_as) "
                  "values (:i,'Fin',:s,'lead',:d)"),
               {"i": str(pid), "s": f"fi-{str(pid)[:8]}", "d": delivered})
    return pid


SU = CurrentUser(id=str(uuid4()), email="a@t.local", role="superadmin")


def test_a_shell_asks_no_finishes_and_lists_what_it_leaves_out(db_session):
    pid = _project(db_session, "shell")
    s = F.finish_state(db_session, pid)
    assert s["questions"] == [] and "Painting" in s["shell_excluded"]


def test_confirming_an_answer_reaches_the_estimate_and_the_rest_are_assumptions(db_session):
    pid = _project(db_session, "finished")
    R.answer_question(project_id=pid, key="flooring",
                      body=R.AnswerBody(answer="Engineered oak; porcelain in baths", confirmed=True),
                      db=db_session, user=SU)
    R.answer_question(project_id=pid, key="counters",
                      body=R.AnswerBody(answer="Quartz", confirmed=False), db=db_session, user=SU)
    spec = F.spec_for_estimate(db_session, pid)
    assert spec["confirmed"] == [{"label": "Flooring", "answer": "Engineered oak; porcelain in baths"}]
    assert "Counters" in spec["unconfirmed"]  # typed but not confirmed is not a decision
    block = F.prompt_block(spec)
    assert "Flooring: Engineered oak" in block and "NOT YET CONFIRMED" in block


def test_an_unknown_question_is_refused(db_session):
    pid = _project(db_session, "finished")
    with pytest.raises(HTTPException) as e:
        R.answer_question(project_id=pid, key="hot_tub", body=R.AnswerBody(answer="yes"),
                          db=db_session, user=SU)
    assert e.value.status_code == 404


def test_part_shell_note_is_in_the_prompt(db_session):
    pid = _project(db_session)
    R.set_delivery(project_id=pid, body=R.DeliveryBody(delivered_as="part_shell",
                                                       part_shell_note="Basement suite left as shell"),
                   db=db_session, _=SU)
    assert "Basement suite left as shell" in F.prompt_block(F.spec_for_estimate(db_session, pid))


def test_suggestions_fill_empty_answers_and_confirm_nothing(db_session):
    pid = _project(db_session, "finished")
    R.answer_question(project_id=pid, key="paint", body=R.AnswerBody(answer="Mine"),
                      db=db_session, user=SU)
    reply = SimpleNamespace(text='{"flooring": {"suggested": "LVP", "why": "scope says LVP"}, '
                                 '"paint": {"suggested": "Two coats", "why": "typical"}}')
    F.suggest(db_session, pid, call=lambda system, messages: reply)
    qs = {q["key"]: q for q in F.finish_state(db_session, pid)["questions"]}
    assert qs["flooring"]["answer"] == "LVP" and not qs["flooring"]["confirmed"]
    assert qs["paint"]["answer"] == "Mine" and qs["paint"]["suggested"] == "Two coats"


def test_suggest_is_refused_for_a_shell(db_session):
    pid = _project(db_session, "shell")
    with pytest.raises(HTTPException) as e:
        R.suggest_answers(project_id=pid, db=db_session, _=SU)
    assert e.value.status_code == 409


def test_shell_zeroes_painting_flooring_millwork_but_not_insulation():
    payload = {"categories": [
        {"name": "06 Insulation, Drywall & Acoustic", "total": 9000, "items": [{"name": "Batt"}]},
        {"name": "07 Painting & Finishes", "total": 5000, "items": [{"name": "Paint"}]},
        {"name": "08 Flooring & Tile", "total": 7000, "items": [{"name": "LVP"}]},
        {"name": "09 Millwork & Casework", "total": 8000, "items": [{"name": "Cabs"}]},
    ], "exclusions": ["Permits"]}
    assert F.apply_shell_exclusions(payload, {"delivered_as": "shell"}, "brio-residential-18")
    totals = {c["name"][:2]: c["total"] for c in payload["categories"]}
    assert totals == {"06": 9000, "07": 0, "08": 0, "09": 0}
    assert "Permits" in payload["exclusions"] and any("Painting" in e for e in payload["exclusions"])


def test_a_finished_project_is_left_alone():
    payload = {"categories": [{"name": "07 Painting & Finishes", "total": 5000}]}
    assert not F.apply_shell_exclusions(payload, {"delivered_as": "finished"}, "brio-residential-18")
    assert payload["categories"][0]["total"] == 5000


def test_the_estimate_prompt_carries_the_finish_block():
    from app.services.ai import build_estimate_messages

    system, messages, _ = build_estimate_messages({"scope": {}, "finish_spec": {
        "delivered_as": "shell", "excluded": F.SHELL_EXCLUDED}})
    assert "DELIVERED AS A SHELL" in str(messages)


def test_an_excluded_item_suggests_nothing_and_a_rerun_replaces_old_suggestions(db_session):
    pid = _project(db_session, "finished")
    first = SimpleNamespace(text='{"flooring": {"suggested": "No suggestion possible", "why": "x"}}')
    F.suggest(db_session, pid, call=lambda system, messages: first)
    second = SimpleNamespace(text='{"flooring": {"suggested": null, "why": "Flooring is excluded"}}')
    F.suggest(db_session, pid, call=lambda system, messages: second)
    q = next(q for q in F.finish_state(db_session, pid)["questions"] if q["key"] == "flooring")
    assert q["answer"] is None and q["suggested"] is None and q["why"] == "Flooring is excluded"
