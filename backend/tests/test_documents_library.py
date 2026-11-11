"""Documents page (UI round 1, phase 2): rename, confirm a category, archive
and restore, bulk changes, and where each file is used.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.routers import documents as R  # noqa: E402
from app.schemas.document import (  # noqa: E402
    CategorySuggestRequest,
    DocumentBulkUpdate,
    DocumentUpdate,
)
from app.services.document_usage import used_in  # noqa: E402


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Docs',:s,'lead')"),
               {"i": str(pid), "s": f"dl-{str(pid)[:8]}"})
    return pid


def _su(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"{uid}@t.local"})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role="superadmin")


def _doc(db, pid, name="plan.pdf", category="other", archived=False):
    d = Document(project_id=pid, category=category, phase="preconstruction",
                 filename=name, r2_key=f"k/{uuid4()}", mime="application/pdf",
                 size_bytes=10, archived=archived)
    db.add(d)
    db.flush()
    return d


def test_rename_keeps_the_file_type(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid, "scan_0042.pdf")
    out = R.update_document(document_id=d.id, body=DocumentUpdate(filename="Geotech report"),
                            db=db_session, user=su)
    assert out.filename == "Geotech report.pdf"


def test_rename_refuses_a_blank_name(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid)
    with pytest.raises(HTTPException) as e:
        R.update_document(document_id=d.id, body=DocumentUpdate(filename="   "),
                          db=db_session, user=su)
    assert e.value.status_code == 422


def test_existing_documents_start_unconfirmed(db_session):
    d = _doc(db_session, _project(db_session))
    assert d.category_confirmed_at is None


def test_choosing_a_category_confirms_it(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid)
    out = R.update_document(document_id=d.id, body=DocumentUpdate(category="report"),
                            db=db_session, user=su)
    assert out.category == "report" and out.category_confirmed_at is not None
    assert str(out.category_confirmed_by_id) == su.id


def test_confirming_other_keeps_other_but_records_the_confirmation(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid)
    out = R.update_document(document_id=d.id, body=DocumentUpdate(category_confirmed=True),
                            db=db_session, user=su)
    assert out.category == "other" and out.category_confirmed_at is not None


def test_sharing_alone_does_not_confirm_the_category(db_session):
    pid, su = _project(db_session), _su(db_session)
    d = _doc(db_session, pid)
    out = R.update_document(document_id=d.id, body=DocumentUpdate(client_visible=True),
                            db=db_session, user=su)
    assert out.client_visible and out.category_confirmed_at is None


def test_archived_list_and_restore(db_session):
    pid, su = _project(db_session), _su(db_session)
    live, gone = _doc(db_session, pid, "a.pdf"), _doc(db_session, pid, "b.pdf", archived=True)
    assert [x.id for x in R.list_documents(project_id=pid, archived=False, db=db_session, user=su)] == [live.id]
    assert [x.id for x in R.list_documents(project_id=pid, archived=True, db=db_session, user=su)] == [gone.id]
    R.restore_document(document_id=gone.id, db=db_session, user=su)
    assert R.list_documents(project_id=pid, archived=True, db=db_session, user=su) == []


def test_bulk_change_category_counts_what_changed(db_session):
    pid, su = _project(db_session), _su(db_session)
    a, b = _doc(db_session, pid, category="other"), _doc(db_session, pid, category="drawing")
    b.category_confirmed_at = b.created_at  # already confirmed as a drawing
    db_session.flush()
    res = R.bulk_update_documents(
        body=DocumentBulkUpdate(project_id=pid, document_ids=[a.id, b.id], category="drawing"),
        db=db_session, user=su)
    assert (res.updated, res.unchanged) == (1, 1)
    assert a.category == "drawing" and a.category_confirmed_at is not None


def test_bulk_share_and_archive(db_session):
    pid, su = _project(db_session), _su(db_session)
    a, b = _doc(db_session, pid), _doc(db_session, pid)
    R.bulk_update_documents(body=DocumentBulkUpdate(project_id=pid, document_ids=[a.id, b.id],
                                                    client_visible=True), db=db_session, user=su)
    assert a.client_visible and b.client_visible
    R.bulk_update_documents(body=DocumentBulkUpdate(project_id=pid, document_ids=[a.id],
                                                    archived=True), db=db_session, user=su)
    assert a.archived and not b.archived


def test_bulk_refuses_a_document_from_another_project(db_session):
    pid, other, su = _project(db_session), _project(db_session), _su(db_session)
    mine, theirs = _doc(db_session, pid), _doc(db_session, other)
    with pytest.raises(HTTPException) as e:
        R.bulk_update_documents(
            body=DocumentBulkUpdate(project_id=pid, document_ids=[mine.id, theirs.id], archived=True),
            db=db_session, user=su)
    assert e.value.status_code == 404
    assert not mine.archived and not theirs.archived


def test_bulk_with_nothing_to_change_is_refused(db_session):
    pid, su = _project(db_session), _su(db_session)
    a = _doc(db_session, pid)
    with pytest.raises(HTTPException) as e:
        R.bulk_update_documents(body=DocumentBulkUpdate(project_id=pid, document_ids=[a.id]),
                                db=db_session, user=su)
    assert e.value.status_code == 422


def test_used_in_reads_the_rows_that_point_at_a_document(db_session):
    pid = _project(db_session)
    sel, note, unused = _doc(db_session, pid), _doc(db_session, pid, "photo.jpg"), _doc(db_session, pid)
    db_session.execute(_T(
        "insert into document_selections (project_id, document_id, use_for_scope, use_for_quantities) "
        "values (:p, :d, true, true)"), {"p": str(pid), "d": str(sel.id)})
    db_session.execute(_T(
        "insert into narratives (id, project_id, parent_table, parent_id, title, content_md, version_number) "
        "values (:i, :p, 'projects', :p, 'Site', :c, 1)"),
        {"i": str(uuid4()), "p": str(pid), "c": f"Footing ![](doc:{note.id}) poured"})
    got = used_in(db_session, pid)
    assert got[str(sel.id)] == ["Takeoff", "Scope"]
    assert got[str(note.id)] == ["Notes"]
    assert str(unused.id) not in got


def test_suggest_categories_uses_the_server_rules(db_session):
    out = R.suggest_categories(
        body=CategorySuggestRequest(filenames=["105_Elec.pdf", "IMG_1.jpg", "notes.txt"]),
        _=None)
    assert out["105_Elec.pdf"] == "drawing"
    assert out["IMG_1.jpg"] == "photo"
