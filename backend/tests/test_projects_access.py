"""Projects: who can see and change them, and what a change may hold.

Until 2026-10-12 every project endpoint took any signed-in user. In production
that included the one client (owner role) account: it could list all 40
projects and PATCH any of them.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.routers import projects as R  # noqa: E402
from app.schemas.project import ProjectUpdate  # noqa: E402


def _project(db, name="Access"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,:n,:s,'lead')"),
               {"i": str(pid), "n": name, "s": f"ac-{str(pid)[:8]}"})
    return pid


def _user(db, role):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _member(db, pid, user):
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p, :u, 'member')"),
               {"p": str(pid), "u": user.id})


def test_a_client_lists_only_their_own_projects(db_session):
    mine, other = _project(db_session, "Mine"), _project(db_session, "Other")
    owner = _user(db_session, "owner")
    _member(db_session, mine, owner)
    names = {p.name for p in R.list_projects(db=db_session, user=owner)}
    assert names == {"Mine"}


def test_a_user_with_no_projects_lists_none(db_session):
    _project(db_session)
    assert R.list_projects(db=db_session, user=_user(db_session, "owner")) == []


def test_superadmin_still_lists_everything(db_session):
    _project(db_session, "A"), _project(db_session, "B")
    su = _user(db_session, "superadmin")
    assert {"A", "B"} <= {p.name for p in R.list_projects(db=db_session, user=su)}


def test_a_non_member_cannot_read_or_edit_a_project(db_session):
    pid = _project(db_session)
    mgr = _user(db_session, "manager")
    with pytest.raises(HTTPException) as e:
        R.get_project(project_id=pid, db=db_session, user=mgr)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        R.update_project(project_id=pid, body=ProjectUpdate(name="x"), db=db_session, user=mgr)
    assert e.value.status_code == 403


def test_a_member_can_set_finish_and_delivery(db_session):
    pid = _project(db_session)
    mgr = _user(db_session, "manager")
    _member(db_session, pid, mgr)
    p = R.update_project(
        project_id=pid,
        body=ProjectUpdate(finish_level="mid", delivered_as="shell",
                           drawing_decisions={"plumbing": "not_in_scope"}),
        db=db_session, user=mgr)
    assert (p.finish_level, p.delivered_as) == ("mid", "shell")
    assert p.drawing_decisions == {"plumbing": "not_in_scope"}


def test_an_explicit_null_leaves_drawing_decisions_alone(db_session):
    pid = _project(db_session)
    su = _user(db_session, "superadmin")
    R.update_project(project_id=pid, body=ProjectUpdate(drawing_decisions={"all": "allowance"}),
                     db=db_session, user=su)
    p = R.update_project(project_id=pid, body=ProjectUpdate(drawing_decisions=None),
                         db=db_session, user=su)
    assert p.drawing_decisions == {"all": "allowance"}


@pytest.mark.parametrize("bad", [
    {"finish_level": "luxury"},
    {"delivered_as": "half"},
    {"drawing_decisions": {"plumbing": "maybe"}},
    {"building_age": 1700},
    {"building_age": 2200},
])
def test_out_of_range_values_are_refused(bad):
    with pytest.raises(ValidationError):
        ProjectUpdate(**bad)
