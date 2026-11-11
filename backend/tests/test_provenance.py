"""Provenance: say what is known, and say plainly what is not.

The rule under test is narrow and absolute — a link that is not recorded is
reported as "not recorded", never as zero, null, or an empty list. Presenting an
unmeasured link as a measured absence is how an assistant becomes untrustworthy
around numbers, and it is a defect that already reached a design document once.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.provenance import quantity_provenance, NOT_RECORDED  # noqa: E402


def _setup(db, *, status="candidate", value=1006, machine=1006, unit="sf",
           qtype="floor_area_sf", with_doc=True, source="cad_extract"):
    pid, did = uuid4(), uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, 'Prov', :s, 'preconstruction')"),
               {"i": str(pid), "s": f"prov-{str(pid)[:8]}"})
    if with_doc:
        db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, "
                      "phase, size_bytes) values (:i, :p, 'Plan.dxf', :k, 'drawing', 'design', 10)"),
                   {"i": str(did), "p": str(pid), "k": f"t/{did}"})
    qid = uuid4()
    db.execute(_T("insert into quantity_overrides (id, project_id, discipline, quantity_type, "
                  "value, unit, source, status, machine_value, source_document_id, "
                  "source_candidate_key) "
                  "values (:i, :p, 'architectural', :q, :v, :u, :src, :st, :m, :d, 'cad:x')"),
               {"i": str(qid), "p": str(pid), "q": qtype, "v": value, "u": unit,
                "src": source, "st": status, "m": machine,
                "d": str(did) if with_doc else None})
    db.flush()
    return pid, did, qid


# ── the absolute rule ─────────────────────────────────────────────────────

def test_unrecorded_links_say_not_recorded_never_zero(db_session):
    out = quantity_provenance(db_session, _setup(db_session)[2])
    assert out["drawing"]["region"] == NOT_RECORDED
    assert out["drawing"]["sheet"] == NOT_RECORDED
    assert out["used_in_estimates"] == NOT_RECORDED
    for field in (out["drawing"]["region"], out["used_in_estimates"]):
        assert field not in (0, "0", None, [], "none", "None")


def test_an_unreviewed_quantity_does_not_claim_a_reviewer(db_session):
    out = quantity_provenance(db_session, _setup(db_session)[2])
    assert out["standing"]["reviewed_by"] == NOT_RECORDED
    assert out["standing"]["reviewed_at"] == NOT_RECORDED
    assert out["standing"]["may_be_priced"] is False


# ── what it does know ─────────────────────────────────────────────────────

def test_it_names_the_drawing_and_the_method(db_session):
    out = quantity_provenance(db_session, _setup(db_session)[2])
    assert out["drawing"]["name"] == "Plan.dxf"
    assert "tracing the wall faces" in out["method"]
    assert "rectangle" in out["method"], "it should say what it did NOT do"


def test_a_correction_keeps_both_numbers_visible(db_session):
    _, _, qid = _setup(db_session, status="corrected", value=228, machine=214, unit="lf",
                       qtype="wall_lf_a-wall")
    out = quantity_provenance(db_session, qid)
    assert out["what"]["value"] == 228.0
    assert out["machine_reading"]["value"] == 214.0
    assert out["machine_reading"]["differs_from_value"] is True
    assert "214" in out["machine_reading"]["note"] and "228" in out["machine_reading"]["note"]
    assert out["standing"]["may_be_priced"] is True


def test_accepting_as_read_is_not_reported_as_a_correction(db_session):
    _, _, qid = _setup(db_session, status="confirmed", value=1006, machine=1006)
    out = quantity_provenance(db_session, qid)
    assert out["machine_reading"]["differs_from_value"] is False


def test_an_excluded_quantity_cannot_be_priced(db_session):
    _, _, qid = _setup(db_session, status="excluded")
    out = quantity_provenance(db_session, qid)
    assert out["standing"]["may_be_priced"] is False
    assert "not counted" in out["standing"]["plain"]


# ── the limits it must volunteer ──────────────────────────────────────────

def test_it_volunteers_that_it_cannot_point_at_the_drawing(db_session):
    out = quantity_provenance(db_session, _setup(db_session)[2])
    joined = " ".join(out["limits"]).lower()
    assert "cannot point at the part of the drawing" in joined
    assert "open the drawing" in joined, "a limit must come with something to do instead"


def test_a_length_carries_the_verified_accuracy_boundary(db_session):
    """Areas are verified; perimeter is refuted. That belongs on the number."""
    _, _, qid = _setup(db_session, unit="lf", qtype="perimeter_lf")
    joined = " ".join(quantity_provenance(db_session, qid)["limits"]).lower()
    assert "lengths are read rather than measured" in joined


def test_an_area_does_not_carry_the_length_warning(db_session):
    joined = " ".join(quantity_provenance(db_session, _setup(db_session)[2])["limits"]).lower()
    assert "lengths are read rather than measured" not in joined


def test_cad_explains_why_there_is_no_sheet_number(db_session):
    joined = " ".join(quantity_provenance(db_session, _setup(db_session)[2])["limits"])
    assert "CAD models have no pages" in joined


def test_a_missing_document_is_not_invented(db_session):
    _, _, qid = _setup(db_session, with_doc=False)
    out = quantity_provenance(db_session, qid)
    assert out["drawing"]["name"] == NOT_RECORDED
    assert out["drawing"]["id"] == NOT_RECORDED


def test_an_unknown_id_returns_nothing(db_session):
    assert quantity_provenance(db_session, uuid4()) is None


# ── where on the drawing (migration 0058) ─────────────────────────────────

def test_a_recorded_region_is_reported_not_withheld(db_session):
    """Saying "I cannot show you where" while holding the coordinates would be
    false modesty — and a reader who learns to skip this panel will skip it when
    it matters."""
    db = db_session
    _, _, qid = _setup(db, status="confirmed", source="candidate_confirmed")
    db.execute(_T("update quantity_overrides set source_bbox = :b, source_page = 1, "
                  "source_revision_label = 'Rev C' where id = :i"),
               {"b": '[83.1, 38.1, 1687.6, 2545.7]', "i": str(qid)})
    db.flush()

    out = quantity_provenance(db, qid)
    assert out["drawing"]["region"] == [83.1, 38.1, 1687.6, 2545.7]
    assert out["drawing"]["sheet"] == 1
    assert out["drawing"]["revision_read"] == "Rev C"
    joined = " ".join(out["limits"]).lower()
    assert "cannot point at the part of the drawing" not in joined


def test_a_cad_quantity_explains_why_it_has_no_region(db_session):
    """Not "it was not recorded" — CAD model space has no position on a printed
    sheet at all, and the difference tells a reviewer whether to expect one."""
    db = db_session
    _, _, qid = _setup(db, source="cad_extract")
    out = quantity_provenance(db, qid)
    assert out["drawing"]["region"] == NOT_RECORDED
    joined = " ".join(out["limits"])
    assert "no position on a printed sheet" in joined
    assert "open the drawing and compare the outline" in joined


def test_a_pdf_quantity_with_no_region_says_it_was_not_recorded(db_session):
    db = db_session
    _, _, qid = _setup(db, source="candidate_confirmed")
    joined = " ".join(quantity_provenance(db, qid)["limits"])
    assert "not recorded when this was confirmed" in joined
