"""A signed-off takeoff counts as chosen for quantities (rollout test, 2026-10-15).

On ZZ Rollout Test a DXF was uploaded, read on the Takeoff page, checked and
signed off. The project had used the document-selection screen before, and
nobody had ticked the new file, so the estimate routed to "scope + rates" and
ignored the signed-off numbers, and its grade said "no CAD, upload the DXF".
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text as _T

from app.models.document import Document
from app.models.drawing_analysis_pack import DrawingAnalysisPack
from app.services import document_selection as DS
from app.services.estimate_readiness import compute_readiness


def _setup(db, *, pack_status="approved", untick=False):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'ZZ',:s,'lead')"),
               {"i": str(pid), "s": f"st-{str(pid)[:8]}"})
    pdf = Document(project_id=pid, category="drawing", phase="preconstruction", filename="A-101 plan.pdf",
                   r2_key=f"k/{uuid4()}", mime="application/pdf", size_bytes=10)
    dxf = Document(project_id=pid, category="drawing", phase="preconstruction", filename="ZZ-garage-plan.dxf",
                   r2_key=f"k/{uuid4()}", mime="application/octet-stream", size_bytes=10)
    db.add_all([pdf, dxf])
    db.flush()
    # The selection screen was used before the DXF arrived: the PDF is ticked.
    db.execute(_T("insert into document_selections (project_id, document_id, use_for_scope, use_for_quantities) "
                  "values (:p, :d, true, true)"), {"p": str(pid), "d": str(pdf.id)})
    if untick:
        db.execute(_T("insert into document_selections (project_id, document_id, use_for_scope, use_for_quantities) "
                      "values (:p, :d, false, false)"), {"p": str(pid), "d": str(dxf.id)})
    db.add(DrawingAnalysisPack(project_id=pid, version_number=1, status=pack_status, payload={},
                               source_document_ids=[str(dxf.id)]))
    db.flush()
    return pid, str(dxf.id)


def test_the_signed_off_drawing_is_priced_from(db_session):
    pid, dxf = _setup(db_session)
    assert dxf in DS.selected_ids(db_session, pid, purpose="quantities")
    assert compute_readiness(db_session, pid)["takeoff_mode"] == "cad"
    assert DS.accuracy_for_selection(db_session, pid)["accuracy"]["grade"] != "read_from_pdf"


def test_an_unsigned_reading_is_not_a_choice(db_session):
    pid, dxf = _setup(db_session, pack_status="draft")
    assert dxf not in DS.selected_ids(db_session, pid, purpose="quantities")


def test_an_explicit_untick_still_wins(db_session):
    pid, dxf = _setup(db_session, untick=True)
    assert dxf not in DS.selected_ids(db_session, pid, purpose="quantities")
