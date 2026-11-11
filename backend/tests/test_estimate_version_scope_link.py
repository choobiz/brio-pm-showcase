"""estimate_versions.scope_id: set on every new version, backfilled by 0081.

A generated version takes the scope its job was given (only if that scope is
on the same project); a revision inherits its parent's, down any depth. 456 W
Sample: v008 (revision of v006, of v004) belongs to the corridor scope v2.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

from app.services.estimate_scope import NEW_VERSION_SCOPE_SQL, scope_binds


def _schema(db):
    for col, typ in [("project_id", "uuid"), ("version_number", "int"), ("payload", "jsonb"),
                     ("parent_version_id", "uuid"), ("ai_job_id", "uuid"), ("scope_id", "uuid")]:
        db.execute(text(f"ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS {col} {typ}"))


def _project(db):
    pid = uuid4()
    db.execute(text("insert into projects (id, name, slug, status) values (:i, 'S', :s, 'lead')"),
               {"i": str(pid), "s": f"s-{pid.hex[:8]}"})
    return pid


def _scope(db, pid, version):
    sid = uuid4()
    db.execute(text("insert into scopes (id, project_id, version, payload) values (:i, :p, :v, '{}'::jsonb)"),
               {"i": str(sid), "p": str(pid), "v": version})
    return sid


def _insert(db, pid, n, parent=None, hint=None, job=None):
    vid = uuid4()
    db.execute(text("INSERT INTO estimate_versions (id, project_id, version_number, payload, parent_version_id, "
                    "ai_job_id, scope_id) VALUES (:id, :p, :n, '{}'::jsonb, :parent, :job, "
                    + NEW_VERSION_SCOPE_SQL + ")"),
               {"id": str(vid), "p": str(pid), "n": n, "parent": str(parent) if parent else None,
                "job": str(job) if job else None, **scope_binds(pid, parent, hint)})
    return vid


def _scope_of(db, vid):
    return db.execute(text("SELECT scope_id FROM estimate_versions WHERE id = :v"), {"v": str(vid)}).scalar()


def test_new_versions_take_their_jobs_scope_and_revisions_inherit_it(db_session):
    _schema(db_session)
    pid = _project(db_session)
    suite, corridor = _scope(db_session, pid, 1), _scope(db_session, pid, 2)
    v4 = _insert(db_session, pid, 4, hint=corridor)
    v6 = _insert(db_session, pid, 6, parent=v4)
    v8 = _insert(db_session, pid, 8, parent=v6)
    assert _scope_of(db_session, v8) == corridor
    assert _scope_of(db_session, _insert(db_session, pid, 3, hint=suite)) == suite


def test_a_scope_from_another_project_is_not_taken(db_session):
    _schema(db_session)
    pid, other = _project(db_session), _project(db_session)
    foreign = _scope(db_session, other, 1)
    assert _scope_of(db_session, _insert(db_session, pid, 1, hint=foreign)) is None


def test_the_backfill_links_generated_versions_and_their_revisions(db_session):
    _schema(db_session)
    pid = _project(db_session)
    corridor = _scope(db_session, pid, 2)
    job = uuid4()
    db_session.execute(text("insert into ai_jobs (id, project_id, kind, status, input) values "
                            "(:i, :p, 'estimate_full', 'done', CAST(:inp AS jsonb))"),
                       {"i": str(job), "p": str(pid), "inp": json.dumps({"scope_id": str(corridor)})})
    # Rows as they were before 0081: no scope stored anywhere.
    v4 = uuid4(); v6 = uuid4(); v8 = uuid4(); typed = uuid4()
    for vid, parent, j in ((v4, None, job), (v6, v4, None), (v8, v6, None), (typed, None, None)):
        db_session.execute(text("INSERT INTO estimate_versions (id, project_id, version_number, payload, "
                                "parent_version_id, ai_job_id) VALUES (:i, :p, 1, '{}'::jsonb, :par, :j)"),
                           {"i": str(vid), "p": str(pid), "par": str(parent) if parent else None,
                            "j": str(j) if j else None})
    mig = _load_migration()
    mig.op = SimpleNamespace(execute=lambda sql: db_session.execute(text(sql)))
    mig.upgrade()
    assert [_scope_of(db_session, v) for v in (v4, v6, v8)] == [corridor] * 3
    assert _scope_of(db_session, typed) is None, "a typed-in estimate is not guessed"


def _load_migration():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic/versions/0081_estimate_version_scope.py"
    spec = importlib.util.spec_from_file_location("mig0081", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
