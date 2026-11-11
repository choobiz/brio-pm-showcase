"""A human correction must survive a re-analysis.

⚠️ THE DEFECT THESE EXIST FOR. `takeoff_to_overrides` compared the value IN
FORCE against the fresh extraction:

    if existing.value != val:
        existing.value = val

For a CORRECTED row those two are a human's number and a machine's number, so
they always differed — and the branch overwrote the human's value on every
rebuild, including a rebuild of an UNCHANGED drawing. Re-running a takeoff
silently replaced a site measurement with the reading it had been corrected away
from, and sent the row back to unreviewed.

No existing test caught it: the only test that called the function twice passed
the SAME value both times, so the branch never executed.

The comparison is now machine-to-machine — did the drawing's reading move? —
which is the question that was always meant.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.drawing.cad_extractor import Takeoff, takeoff_to_overrides  # noqa: E402


def _takeoff(window_count: int) -> Takeoff:
    t = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
    t.counts = {"window": {"W1": window_count}}
    return t


def _project_with_drawing(db, category: str = "drawing"):
    pid, did = uuid4(), uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, 'Correction durability', :s, 'preconstruction')"),
               {"i": str(pid), "s": f"corr-{str(pid)[:8]}"})
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, phase) "
                  "values (:i, :p, 'Plan.dxf', :k, :c, 'design')"),
               {"i": str(did), "p": str(pid), "k": f"t/{did}.dxf", "c": category})
    return pid, did


def _row(db, pid):
    from app.models.quantity_override import QuantityOverride
    return db.query(QuantityOverride).filter_by(
        project_id=pid, source_candidate_key="cad:window_count").one()


# ── the defect, stated as a test ──────────────────────────────────────────

def test_a_correction_survives_re_extraction_of_an_unchanged_drawing(db_session):
    """THE REGRESSION. Nothing about the drawing changed, so nothing about the
    human's number should change either."""
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()

    r = _row(db, pid)
    r.value, r.status, r.notes = Decimal("11"), "corrected", "counted on site"
    db.flush()

    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)   # same reading
    db.flush()

    r = _row(db, pid)
    assert float(r.value) == 11.0, "the human's corrected value was overwritten"
    assert r.status == "corrected", "an unchanged drawing un-reviewed a corrected row"
    assert float(r.machine_value) == 8.0, "the machine reading should still be recorded"


def test_a_correction_is_kept_when_the_drawing_moves_but_goes_back_for_review(db_session):
    """The basis changed, so it needs another look — but the human's number is
    preserved so they can see both and decide. Losing it is data loss, not a
    correction."""
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    r = _row(db, pid)
    r.value, r.status = Decimal("11"), "corrected"
    db.flush()

    takeoff_to_overrides(db, pid, _takeoff(9), document_id=did)   # drawing changed
    db.flush()

    r = _row(db, pid)
    assert float(r.value) == 11.0, "the human's value must not be overwritten"
    assert float(r.machine_value) == 9.0, "the new reading must be recorded"
    assert r.status == "candidate", "a moved basis must go back for review"
    assert "11" in (r.notes or "") and "9" in (r.notes or ""), \
        "the note should name both numbers so the reviewer can compare"


# ── the behaviour that was already right, now pinned ──────────────────────

def test_a_confirmation_survives_an_unchanged_re_extraction(db_session):
    """Re-reviewing forty unchanged numbers to reach the two that moved is how a
    review stops being done at all."""
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    r = _row(db, pid)
    r.status, r.confirmed_by_id = "confirmed", None
    db.flush()

    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    assert _row(db, pid).status == "confirmed"


def test_a_confirmation_is_withdrawn_when_the_drawing_moves(db_session):
    """The human agreed with a reading that has since changed, so the agreement
    no longer stands and the new reading takes its place."""
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    r = _row(db, pid)
    r.status = "confirmed"
    db.flush()

    takeoff_to_overrides(db, pid, _takeoff(9), document_id=did)
    db.flush()

    r = _row(db, pid)
    assert r.status == "candidate"
    assert float(r.value) == 9.0, "a confirmed row takes the new reading"
    assert r.confirmed_by_id is None and r.confirmed_at is None


def test_an_exclusion_is_a_scope_decision_and_survives_a_changed_reading(db_session):
    """'This does not apply here' is not a judgement about the number, so a
    different number does not reverse it."""
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    r = _row(db, pid)
    r.status = "excluded"
    db.flush()

    takeoff_to_overrides(db, pid, _takeoff(9), document_id=did)
    db.flush()

    r = _row(db, pid)
    assert r.status == "excluded", "an exclusion must not be reopened by a re-read"
    assert float(r.machine_value) == 9.0, "but the record stays current"


def test_an_untouched_candidate_takes_the_newer_reading(db_session):
    db = db_session
    pid, did = _project_with_drawing(db)
    takeoff_to_overrides(db, pid, _takeoff(8), document_id=did)
    db.flush()
    takeoff_to_overrides(db, pid, _takeoff(9), document_id=did)
    db.flush()

    r = _row(db, pid)
    assert float(r.value) == 9.0 and r.status == "candidate"


# ── the filing-category defect ────────────────────────────────────────────

def test_cad_is_read_whatever_drawer_it_was_filed_in(db_session):
    """A DWG is a DWG. Gating the takeoff on `category` is what hid most of a
    stamped sheet set on Maple, and it hid a structural model here."""
    from app.services.drawing_analysis import _cad_documents

    db = db_session
    pid, did = _project_with_drawing(db, category="other")
    found = _cad_documents(db, pid)
    assert [d["id"] for d in found] == [did], \
        "a CAD file filed as 'other' was invisible to the analysis builder"


def test_the_gate_and_the_builder_agree_about_what_counts_as_cad(db_session):
    """If they disagree, a project is either gated on files nobody analysed or
    analysed without ever being gated."""
    import pytest
    from fastapi import HTTPException

    from app.services.drawing_analysis import _cad_documents
    from app.services.quantity_gate import _assert_pack_approved

    db = db_session
    pid, _ = _project_with_drawing(db, category="permit")
    db.flush()

    assert _cad_documents(db, pid), "builder must see the CAD file"
    with pytest.raises(HTTPException) as e:
        _assert_pack_approved(db, pid)
    assert e.value.detail["error"] == "drawing_analysis_required", \
        "the gate must require an analysis for the same file the builder reads"
