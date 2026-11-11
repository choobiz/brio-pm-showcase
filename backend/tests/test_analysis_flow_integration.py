"""The whole chain against a real database: candidate → confirmed → approved.

The unit tests around this each mock the database, so every one of them passed
while the chain itself was broken by a unique index and a session that does not
autoflush. This exercises the real tables.
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

from app.services.quantity_gate import assert_gate_closed, gate_status


@pytest.fixture()
def project(db_session):
    pid = uuid4()
    db_session.execute(_T(
        "insert into projects (id, name, slug, status) "
        "values (:i, 'Analysis flow', :s, 'preconstruction')"),
        {"i": str(pid), "s": f"analysis-flow-{str(pid)[:8]}"})
    doc = uuid4()
    db_session.execute(_T(
        "insert into documents (id, project_id, filename, r2_key, category, phase) "
        "values (:i, :p, 'A1 Plan.dxf', 't/a1.dxf', 'drawing', 'design')"),
        {"i": str(doc), "p": str(pid)})
    db_session.commit()
    return pid, doc


def _candidate(db, pid, doc, qtype, val):
    db.execute(_T(
        "insert into quantity_overrides (id, project_id, discipline, quantity_type, "
        "value, unit, source, source_document_id, source_candidate_key, status, "
        "machine_value) values (:i, :p, 'architectural', :q, :v, 'sf', 'cad_extract', "
        ":d, :k, 'candidate', :v)"),
        {"i": str(uuid4()), "p": str(pid), "q": qtype, "v": val,
         "d": str(doc), "k": f"cad:{qtype}"})


def test_estimate_is_refused_until_reviewed_and_then_signed(db_session, project):
    pid, doc = project
    db = db_session
    _candidate(db, pid, doc, "floor_area_sf", 670.1)
    _candidate(db, pid, doc, "door_count", 5)
    db.commit()

    # 1. Unreviewed quantities block the estimate.
    assert gate_status(db, pid).open is True
    with pytest.raises(HTTPException) as ei:
        assert_gate_closed(db, pid)
    assert ei.value.detail["error"] == "quantity_verification_required"

    # 2. Reviewing every quantity closes the quantity half of the gate — but the
    #    estimate is still refused, because confirming forty numbers and
    #    reviewing the drawing set are different acts.
    db.execute(_T("update quantity_overrides set status = 'confirmed' "
                  "where project_id = :p"), {"p": str(pid)})
    db.commit()
    assert gate_status(db, pid).open is False
    with pytest.raises(HTTPException) as ei:
        assert_gate_closed(db, pid)
    assert ei.value.detail["error"] == "drawing_analysis_required"

    # 3. A draft analysis is not a signed one.

    pack = uuid4()
    db.execute(_T(
        "insert into drawing_analysis_packs (id, project_id, version_number, status, "
        "payload) values (:i, :p, 1, 'draft', '{}'::jsonb)"),
        {"i": str(pack), "p": str(pid)})
    db.commit()
    with pytest.raises(HTTPException) as ei:
        assert_gate_closed(db, pid)
    assert ei.value.detail["error"] == "drawing_analysis_not_approved"

    db.execute(_T("update drawing_analysis_packs set status = 'approved', "
                  "approved_at = now() where id = :i"), {"i": str(pack)})
    db.commit()
    assert_gate_closed(db, pid)          # signed — the estimate may run


def test_an_excluded_quantity_neither_blocks_nor_prices(db_session, project):
    """The two halves of 'excluded' have to agree: it resolves the gate AND it
    keeps the number out of the estimate."""
    from app.services.rate_reconcile import _takeoff_quantities

    pid, doc = project
    db = db_session
    _candidate(db, pid, doc, "floor_area_sf", 670.1)
    _candidate(db, pid, doc, "roof_area_sf", 999.0)
    db.execute(_T("update quantity_overrides set status = 'confirmed' "
                  "where project_id = :p and quantity_type = 'floor_area_sf'"),
               {"p": str(pid)})
    db.execute(_T("update quantity_overrides set status = 'excluded' "
                  "where project_id = :p and quantity_type = 'roof_area_sf'"),
               {"p": str(pid)})
    db.commit()

    assert gate_status(db, pid).open is False
    q = _takeoff_quantities(db, pid)
    assert q["floor_area_sf"]["value"] == pytest.approx(670.1)
    assert "roof_area_sf" not in q


def test_approving_does_not_require_an_already_approved_analysis(db_session, project):
    """The approve ROUTE, not the services under it.

    `assert_gate_closed` checks both halves of the gate — quantities reviewed AND
    analysis signed. Approving is the act that satisfies the second half, so
    calling the whole gate before signing made the button refuse itself with "the
    drawing analysis is draft — approve it before generating an estimate". The
    services all passed their own tests; only the route composed them wrongly.
    """
    from types import SimpleNamespace
    from uuid import uuid4

    from app.routers.drawing_analysis import approve_analysis

    pid, doc = project
    db = db_session
    _candidate(db, pid, doc, "floor_area_sf", 670.1)
    db.execute(_T("update quantity_overrides set status = 'confirmed' "
                  "where project_id = :p"), {"p": str(pid)})
    db.execute(_T(
        "insert into drawing_analysis_packs (id, project_id, version_number, "
        "status, payload) values (:i, :p, 1, 'draft', '{}'::jsonb)"),
        {"i": str(uuid4()), "p": str(pid)})
    db.commit()

    out = approve_analysis(pid, None, db=db,
                           user=SimpleNamespace(id=None, role="superadmin"))
    assert out["pack"]["status"] == "approved"
    assert out["pack"]["approved_at"] is not None
    assert_gate_closed(db, pid)          # the estimate may now run


def test_approving_is_still_refused_while_a_quantity_is_unreviewed(db_session, project):
    """Splitting the gate must not drop the half that still applies."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.routers.drawing_analysis import approve_analysis

    pid, doc = project
    db = db_session
    _candidate(db, pid, doc, "floor_area_sf", 670.1)      # left as a candidate
    db.execute(_T(
        "insert into drawing_analysis_packs (id, project_id, version_number, "
        "status, payload) values (:i, :p, 1, 'draft', '{}'::jsonb)"),
        {"i": str(uuid4()), "p": str(pid)})
    db.commit()

    with pytest.raises(HTTPException) as ei:
        approve_analysis(pid, None, db=db,
                         user=SimpleNamespace(id=None, role="superadmin"))
    assert ei.value.detail["error"] == "quantity_verification_required"
