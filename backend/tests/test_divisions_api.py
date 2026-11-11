"""The division sets, readable at last.

Until this endpoint the only way to read a division set was to open a migration.
The case worth testing hardest is the UNRESOLVED one: a project with no type
resolves to no set, and an estimate on it falls back to a generic list that is
not Brio's standard. Saying so is the whole point.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.routers.divisions import list_division_sets  # noqa: E402


def _admin(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"a-{str(uid)[:8]}@example.com"})
    return CurrentUser(id=str(uid), email="a@example.com", role="superadmin")


def _set(db, code, name, standard, types, divisions):
    sid = uuid4()
    db.execute(_T("insert into division_sets (id, code, name, standard, is_active) "
                  "values (:i, :c, :n, :s, true)"),
               {"i": str(sid), "c": code, "n": name, "s": standard})
    for i, (num, title, cont, default, reserved) in enumerate(divisions, start=1):
        db.execute(_T("insert into divisions (id, set_id, number, title, sequence, "
                      "contingency_pct, active_by_default, is_reserved) "
                      "values (:i, :s, :n, :t, :q, :c, :a, :r)"),
                   {"i": str(uuid4()), "s": str(sid), "n": num, "t": title, "q": i,
                    "c": cont, "a": default, "r": reserved})
    for t in types:
        db.execute(_T("insert into project_type_division_sets (project_type, set_id) "
                      "values (:t, :s)"), {"t": t, "s": str(sid)})
    return sid


def _project(db, name, ptype):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status, type) "
                  "values (:i, :n, :s, 'preconstruction', :t)"),
               {"i": str(pid), "n": name, "s": f"d-{str(pid)[:8]}", "t": ptype})
    return pid


def _seed(db):
    _set(db, "brio-residential-18", "BRIO Residential", "brio",
         ["residential_new", "residential_reno"],
         [("01", "General Conditions", 0, True, False),
          ("02", "Excavation", 5, True, False)])
    _set(db, "csi-masterformat", "CSI MasterFormat", "csi",
         ["commercial_ti", "commercial_new"],
         [("01", "General Requirements", 0, True, False),
          ("15", "Reserved for future expansion", 0, False, True),
          ("34", "Transportation", 5, False, False)])


def test_sets_are_listed_with_their_divisions(db_session):
    db = db_session
    _seed(db); db.flush()
    out = list_division_sets(project_id=None, db=db, user=_admin(db))
    codes = {s["code"] for s in out["sets"]}
    assert {"brio-residential-18", "csi-masterformat"} <= codes


def test_reserved_and_default_off_divisions_are_counted_apart(db_session):
    """A reserved number has no meaning and must never be offered; a real
    division that is simply off by default is a different thing."""
    db = db_session
    _seed(db); db.flush()
    csi = next(s for s in list_division_sets(project_id=None, db=db, user=_admin(db))["sets"]
               if s["code"] == "csi-masterformat")
    assert csi["counts"]["total"] == 3
    assert csi["counts"]["reserved"] == 1
    assert csi["counts"]["usable"] == 2
    assert csi["counts"]["used_by_default"] == 1


def test_contingency_is_reported_per_division(db_session):
    """0% on general conditions, 5% on trades — set per division, never
    globally."""
    db = db_session
    _seed(db); db.flush()
    brio = next(s for s in list_division_sets(project_id=None, db=db, user=_admin(db))["sets"]
                if s["code"] == "brio-residential-18")
    by_no = {d["number"]: d["contingency_pct"] for d in brio["divisions"]}
    assert by_no["01"] == 0.0 and by_no["02"] == 5.0


def test_a_typed_project_resolves_and_says_why(db_session):
    db = db_session
    _seed(db)
    pid = _project(db, "A house", "residential_new"); db.flush()
    out = list_division_sets(project_id=pid, db=db, user=_admin(db))
    assert out["resolved"]["set_code"] == "brio-residential-18"
    assert "residential new" in out["resolved"]["why"]


def test_an_unclassified_project_says_what_would_happen(db_session):
    """THE CASE THAT MATTERS. Resolving to nothing is not a blank — it means an
    estimate would use a generic list that is not Brio's standard."""
    db = db_session
    _seed(db)
    pid = _project(db, "Unclassified", None); db.flush()
    out = list_division_sets(project_id=pid, db=db, user=_admin(db))
    assert out["resolved"]["set_code"] is None
    why = out["resolved"]["why"]
    assert "no type set" in why and "not Brio's standard" in why


def test_the_endpoint_says_it_is_read_only_and_why(db_session):
    """So nobody hunts for an edit button that is not there, and so its absence
    reads as a decision rather than an omission."""
    db = db_session
    _seed(db); db.flush()
    out = list_division_sets(project_id=None, db=db, user=_admin(db))
    assert out["editable"] is False
    assert "every future estimate" in out["editable_note"]
