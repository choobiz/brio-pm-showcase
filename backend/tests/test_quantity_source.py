"""Where a quantity says it was read from, and what the server refuses to believe.

The property under test is that PROVENANCE IS DERIVED, NOT ASSERTED. A caller
can send any `source_revision_label` it likes; what gets stored is whatever the
drawing register recorded, including nothing. A label that round-trips from the
request body would look exactly as checkable as one read from the record, which
is the failure mode worth a test.

The second property is that a locator must be able to describe the thing it
points at. Production is unanimous here: all 50 quantities on record are
`cad_extract` against .dwg/.dxf or null-mime documents, so a bbox against one
would be a coordinate in a space no viewer can draw.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.quantity_source import (  # noqa: E402
    SourceRejected,
    stamp,
)


def _project(db, name="Source"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, :n, :s, 'preconstruction')"),
               {"i": str(pid), "n": name, "s": f"qs-{str(pid)[:8]}"})
    return pid


def _doc(db, pid, filename, *, mime=None, revision=None):
    did = uuid4()
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, "
                  "phase, mime, revision_label) values (:i, :p, :f, :k, 'drawing', "
                  "'design', :m, :r)"),
               {"i": str(did), "p": str(pid), "f": filename, "k": f"t/{did}",
                "m": mime, "r": revision})
    return did


def _payload(**kw):
    base = {
        "project_id": None,
        "discipline": "structural",
        "quantity_type": "wall_length",
        "value": 12.5,
        "unit": "lf",
        "source": "manual_measure",
        "source_document_id": None,
        "source_page": None,
        "source_candidate_key": None,
        "notes": None,
        "source_bbox": None,
        "source_revision_label": None,
    }
    base.update(kw)
    return base


BOX = [10.0, 20.0, 110.0, 70.0]


# --- the label is read from the record, never from the caller ---------------

def test_submitted_revision_label_is_discarded(db_session):
    """The headline property. A caller claiming 'R9' must not get 'R9' stored."""
    pid = _project(db_session)
    did = _doc(db_session, pid, "S-101 Structural.pdf", mime="application/pdf", revision="R3")
    out = stamp(db_session, _payload(source_document_id=did,
                             source_revision_label="R9 — I made this up"), pid)
    assert out["source_revision_label"] == "R3"


def test_unrecorded_revision_stays_null_rather_than_guessing(db_session):
    """An unconfirmed register yields NULL, which provenance renders as
    'not recorded'. It must never be back-filled with something plausible."""
    pid = _project(db_session)
    did = _doc(db_session, pid, "A-200 Plan.pdf", mime="application/pdf", revision=None)
    out = stamp(db_session, _payload(source_document_id=did,
                             source_revision_label="R1"), pid)
    assert out["source_revision_label"] is None


def test_no_document_means_no_revision(db_session):
    pid = _project(db_session)
    out = stamp(db_session, _payload(source_revision_label="R4"), pid)
    assert out["source_revision_label"] is None
    assert out["source_bbox"] is None


# --- a locator must describe the document it points at ----------------------

def test_bbox_recorded_against_a_pdf(db_session):
    pid = _project(db_session)
    did = _doc(db_session, pid, "A-200 Plan.pdf", mime="application/pdf", revision="R2")
    out = stamp(db_session, _payload(source_document_id=did, source_page=3,
                             source_bbox=BOX), pid)
    assert out["source_bbox"] == BOX
    assert out["source_page"] == 3
    assert out["source_revision_label"] == "R2"


@pytest.mark.parametrize("mime", ["image/vnd.dwg", "image/vnd.dxf", None])
def test_bbox_refused_for_cad_and_unknown_types(db_session, mime):
    """Exactly the shape of every source document in production."""
    pid = _project(db_session)
    did = _doc(db_session, pid, "1234 Example - Plan.dwg", mime=mime)
    with pytest.raises(SourceRejected) as e:
        stamp(db_session, _payload(source_document_id=did, source_page=1,
                           source_bbox=BOX), pid)
    assert "PDF" in str(e.value)


def test_cad_source_still_gets_its_revision(db_session):
    """Refusing the BOX must not refuse the whole record — a CAD-derived
    quantity still names its drawing and that drawing's revision."""
    pid = _project(db_session)
    did = _doc(db_session, pid, "Plan.dxf", mime="image/vnd.dxf", revision="R5")
    out = stamp(db_session, _payload(source_document_id=did, source="cad_extract"), pid)
    assert out["source_bbox"] is None
    assert out["source_revision_label"] == "R5"


def test_bbox_without_a_document_is_refused(db_session):
    pid = _project(db_session)
    with pytest.raises(SourceRejected):
        stamp(db_session, _payload(source_page=1, source_bbox=BOX), pid)


def test_bbox_without_a_page_is_refused(db_session):
    pid = _project(db_session)
    did = _doc(db_session, pid, "A.pdf", mime="application/pdf")
    with pytest.raises(SourceRejected) as e:
        stamp(db_session, _payload(source_document_id=did, source_bbox=BOX), pid)
    assert "page" in str(e.value).lower()


# --- cross-project attribution ---------------------------------------------

def test_document_from_another_project_is_refused(db_session):
    """Verified against production as rejecting nothing that exists (0 of 50),
    and it stops an attribution that would be invisible once stored."""
    a, b = _project(db_session, "A"), _project(db_session, "B")
    did = _doc(db_session, b, "Other job.pdf", mime="application/pdf", revision="R1")
    with pytest.raises(SourceRejected) as e:
        stamp(db_session, _payload(source_document_id=did), a)
    assert "different project" in str(e.value)


def test_missing_document_is_refused(db_session):
    pid = _project(db_session)
    with pytest.raises(SourceRejected):
        stamp(db_session, _payload(source_document_id=uuid4()), pid)


# --- box shape --------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    [1, 2, 3],                  # too few
    [1, 2, 3, 4, 5],            # too many
    "10,20,30,40",              # not a list
    [10.0, 20.0, 10.0, 70.0],   # zero width
    [10.0, 20.0, 110.0, 20.0],  # zero height
    [110.0, 20.0, 10.0, 70.0],  # inverted x
    [10.0, 70.0, 110.0, 20.0],  # inverted y
    [float("inf"), 0.0, 1.0, 1.0],
    [float("nan"), 0.0, 1.0, 1.0],
    ["a", "b", "c", "d"],
])
def test_malformed_boxes_are_refused(db_session, bad):
    pid = _project(db_session)
    did = _doc(db_session, pid, "A.pdf", mime="application/pdf")
    with pytest.raises(SourceRejected):
        stamp(db_session, _payload(source_document_id=did, source_page=1,
                           source_bbox=bad), pid)


def test_integer_box_is_accepted_as_floats(db_session):
    """pdf.js hands back numbers; JSON may carry them as ints."""
    pid = _project(db_session)
    did = _doc(db_session, pid, "A.pdf", mime="application/pdf")
    out = stamp(db_session, _payload(source_document_id=did, source_page=1,
                             source_bbox=[10, 20, 110, 70]), pid)
    assert out["source_bbox"] == [10.0, 20.0, 110.0, 70.0]


def test_stamp_does_not_mutate_the_caller_payload(db_session):
    pid = _project(db_session)
    did = _doc(db_session, pid, "A.pdf", mime="application/pdf", revision="R1")
    p = _payload(source_document_id=did, source_revision_label="R9")
    stamp(db_session, p, pid)
    assert p["source_revision_label"] == "R9", "caller's dict was mutated"
