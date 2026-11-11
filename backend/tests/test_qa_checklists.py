"""On-site QA checklists.

Two properties carry the weight, and both are about what a completed checklist
is EVIDENCE OF:

  · An unanswered check is "not yet looked at", never a pass. A checklist where
    silence reads as satisfactory produces a clean record of an inspection
    nobody did — worse than no checklist, because it is evidence of the wrong
    thing.

  · It is Brio's own quality check and says nothing about code compliance. A
    document that looks like an inspection report and is not one gets read years
    later in a dispute.
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

from app.services import qa_checklists as Q  # noqa: E402


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i,:n,:s,'preconstruction')"),
               {"i": str(pid), "n": "QA", "s": f"qa-{str(pid)[:8]}"})
    return pid


# --- not looked at is not a pass --------------------------------------------

def test_a_new_checklist_has_every_item_unanswered(db_session):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="pre_drywall")
    assert all(i["result"] is None for i in cl["items"])
    p = cl["progress"]
    assert p["not_looked_at"] == p["total"] > 0
    assert p["pass"] == 0, "an untouched checklist reported passes"
    assert p["complete"] is False


def test_completing_is_refused_while_anything_is_unanswered(db_session):
    """Closing a checklist with items nobody reached turns "we did not look"
    into "we looked and it was fine"."""
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="pre_pour")
    with pytest.raises(Q.ChecklistError) as e:
        Q.complete(db_session, cl["id"])
    assert "not been looked at" in str(e.value)
    assert Q.get(db_session, cl["id"])["status"] == "open"


def test_closing_a_partial_walk_is_allowed_but_keeps_the_gaps_visible(db_session):
    """A walk can be genuinely partial. What must not happen is the unanswered
    items quietly becoming passes."""
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="pre_pour")
    Q.set_result(db_session, cl["id"], cl["items"][0]["id"], result="pass")
    out = Q.complete(db_session, cl["id"], allow_unanswered=True)
    assert out["status"] == "complete"
    assert out["progress"]["not_looked_at"] == len(cl["items"]) - 1
    assert out["progress"]["pass"] == 1


def test_a_completed_walk_cannot_be_edited(db_session):
    """It is a record of what was observed, not a working document."""
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="envelope")
    Q.complete(db_session, cl["id"], allow_unanswered=True)
    with pytest.raises(Q.ChecklistError) as e:
        Q.set_result(db_session, cl["id"], cl["items"][0]["id"], result="pass")
    assert "Start a new one" in str(e.value)


# --- recording an observation ----------------------------------------------

def test_a_failure_is_recorded_with_its_note_and_listed(db_session):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="pre_drywall")
    item = cl["items"][1]
    out = Q.set_result(db_session, cl["id"], item["id"], result="fail",
                       note="missing at the north wall")
    got = next(i for i in out["items"] if i["id"] == item["id"])
    assert got["result"] == "fail"
    assert got["note"] == "missing at the north wall"
    assert item["text"] in out["progress"]["failures"]


def test_not_applicable_is_its_own_answer(db_session):
    """"Does not apply here" and "was not looked at" are different facts."""
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="envelope")
    out = Q.set_result(db_session, cl["id"], cl["items"][0]["id"], result="na")
    assert out["progress"]["na"] == 1
    assert out["progress"]["not_looked_at"] == len(cl["items"]) - 1


def test_an_answer_can_be_taken_back_to_unanswered(db_session):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="envelope")
    iid = cl["items"][0]["id"]
    Q.set_result(db_session, cl["id"], iid, result="pass")
    out = Q.set_result(db_session, cl["id"], iid, result=None)
    assert out["progress"]["not_looked_at"] == len(cl["items"])


@pytest.mark.parametrize("bad", ["ok", "PASS", "", "passed", "yes"])
def test_an_invented_result_is_refused(db_session, bad):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="envelope")
    with pytest.raises(Q.ChecklistError):
        Q.set_result(db_session, cl["id"], cl["items"][0]["id"], result=bad)


def test_an_unknown_item_is_refused_rather_than_silently_ignored(db_session):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="envelope")
    with pytest.raises(Q.ChecklistError):
        Q.set_result(db_session, cl["id"], "not-an-item", result="pass")


def test_an_unknown_template_is_refused(db_session):
    pid = _project(db_session)
    with pytest.raises(Q.ChecklistError):
        Q.create(db_session, project_id=pid, template_key="made_up")


# --- what it claims ---------------------------------------------------------

def test_every_checklist_carries_the_disclaimer(db_session):
    pid = _project(db_session)
    cl = Q.create(db_session, project_id=pid, template_key="pre_drywall")
    assert "not a code-compliance determination" in cl["disclaimer"]
    assert "authority having jurisdiction" in cl["disclaimer"]


def test_no_item_claims_code_compliance(db_session):
    """Items are observations a supervisor can stand in front of and answer.
    "Complies with BCBC 9.10.16" is not one, and inviting that answer from a
    supervisor is the overreach this module exists to avoid."""
    banned = re.compile(r"\b(bcbc|building code|code[- ]compliant|complies with|"
                        r"per code|to code)\b", re.I)
    for key, tpl in Q.TEMPLATES.items():
        for text in tpl["items"]:
            assert not banned.search(text), f"{key}: {text}"


def test_templates_are_short_enough_to_actually_be_walked(db_session):
    """A list nobody finishes records nothing."""
    for key, tpl in Q.TEMPLATES.items():
        assert 5 <= len(tpl["items"]) <= 15, f"{key} has {len(tpl['items'])} items"
        assert tpl["when"], f"{key} does not say when it is used"


def test_listing_a_project_shows_progress_without_the_items(db_session):
    pid = _project(db_session)
    Q.create(db_session, project_id=pid, template_key="pre_drywall", location="L2")
    rows = Q.list_for_project(db_session, pid)
    assert len(rows) == 1
    assert rows[0]["location"] == "L2"
    assert rows[0]["progress"]["not_looked_at"] > 0
    assert "items" not in rows[0], "the list view should not carry every item"
