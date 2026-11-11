"""The gate between a machine takeoff and a priced estimate."""
import pytest

from app.services.quantity_gate import ACCURACY, GATED_JOB_KINDS, GateStatus


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _DB:
    """Minimal stand-in for the statements the gate issues.

    `has_cad` defaults to False so these tests exercise the QUANTITY gate alone.
    The analysis-pack gate has its own file — mixing them here would make a
    failure in either look like a failure in both.
    """

    def __init__(self, counts, pending, pack_status=None, has_cad=False):
        self._counts = counts
        self._pending = pending
        self._pack = pack_status
        self._cad = has_cad
        self.seen = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.seen.append(sql)
        if "drawing_analysis_packs" in sql:
            return _First((self._pack,) if self._pack else None)
        if "lower(filename) LIKE" in sql:
            return _First((1,) if self._cad else None)
        if "GROUP BY status" in sql:
            return _Rows([{"status": k, "n": v} for k, v in self._counts.items()])
        return _Rows(self._pending)


class _First:
    def __init__(self, v):
        self._v = v

    def first(self):
        return self._v


def _gate(monkeypatch, counts, pending, mode="cad"):
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": mode}, raising=False)
    from app.services.quantity_gate import gate_status
    return gate_status(_DB(counts, pending), "p1")


def _cand(qt="door_count", v=5.0):
    return {"id": "1", "discipline": "architectural", "quantity_type": qt,
            "value": v, "unit": "ea", "source": "cad_extract", "notes": None,
            # The review shows the drawing beside the number, so the gate has to
            # say which drawing the number came off.
            "source_document_id": None}


def test_unreviewed_candidates_hold_the_gate_open(monkeypatch):
    g = _gate(monkeypatch, {"candidate": 2, "confirmed": 1}, [_cand(), _cand("window_count")])
    assert g.open
    assert len(g.unresolved) == 2
    assert {u["quantity_type"] for u in g.unresolved} == {"door_count", "window_count"}


def test_a_fully_reviewed_project_passes(monkeypatch):
    g = _gate(monkeypatch, {"confirmed": 3, "corrected": 1, "excluded": 2}, [])
    assert not g.open and g.unresolved == []


def test_the_accuracy_of_the_source_is_stated_not_implied(monkeypatch):
    """Someone confirming a hundred numbers has to know whether they were measured
    from CAD or read off a PDF BEFORE they start."""
    cad = _gate(monkeypatch, {"confirmed": 1}, [], mode="cad")
    pdf = _gate(monkeypatch, {"confirmed": 1}, [], mode="pdf")
    none = _gate(monkeypatch, {"confirmed": 1}, [], mode="none")
    assert "MEASURED" in cad.accuracy
    assert "LESS ACCURATE" in pdf.accuracy and "DWG" in pdf.accuracy
    assert "LOWEST CONFIDENCE" in none.accuracy
    assert cad.accuracy != pdf.accuracy != none.accuracy


def test_readiness_failure_does_not_block_the_gate(monkeypatch):
    """Readiness is advisory. If it throws, the gate must still answer."""
    import app.services.estimate_readiness as er

    def boom(db, pid):
        raise RuntimeError("readiness exploded")

    monkeypatch.setattr(er, "compute_readiness", boom, raising=False)
    from app.services.quantity_gate import gate_status
    g = gate_status(_DB({"candidate": 1}, [_cand()]), "p1")
    assert g.open and g.takeoff_mode == "none"


def test_assert_refuses_with_the_outstanding_list_not_just_a_refusal(monkeypatch):
    """'Blocked' without 'on what' just moves the work to whoever must find out."""
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": "cad"}, raising=False)
    from fastapi import HTTPException

    from app.services.quantity_gate import assert_gate_closed
    with pytest.raises(HTTPException) as ei:
        assert_gate_closed(_DB({"candidate": 1}, [_cand()]), "p1")
    assert ei.value.status_code == 409
    d = ei.value.detail
    assert d["error"] == "quantity_verification_required"
    assert d["unresolved"] and d["unresolved"][0]["quantity_type"] == "door_count"
    assert "MEASURED" in d["accuracy"]


def test_assert_passes_when_nothing_is_outstanding(monkeypatch):
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": "cad"}, raising=False)
    from app.services.quantity_gate import assert_gate_closed
    assert_gate_closed(_DB({"confirmed": 4}, []), "p1")     # must not raise


def test_only_pricing_jobs_are_gated():
    """Gating a document summary or an OCR run would block work that prices nothing."""
    assert "estimate_full" in GATED_JOB_KINDS
    assert "estimate_generation" in GATED_JOB_KINDS
    for kind in ("cad_takeoff", "document_summary", "ocr", "email_draft",
                 "presentation", "schedule_from_estimate"):
        assert kind not in GATED_JOB_KINDS


def test_the_discipline_filter_narrows_the_query(monkeypatch):
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": "cad"}, raising=False)
    from app.services.quantity_gate import gate_status
    db = _DB({"candidate": 1}, [_cand()])
    gate_status(db, "p1", discipline="plumbing")
    assert all("discipline = :d" in sql for sql in db.seen)


def test_gate_status_serialises_for_the_api():
    g = GateStatus(open=True, takeoff_mode="cad", accuracy=ACCURACY["cad"],
                   counts={"candidate": 1}, unresolved=[_cand()])
    d = g.as_dict()
    assert set(d) == {"open", "takeoff_mode", "accuracy", "counts", "grade",
                      "unresolved"}


def test_the_gate_headline_is_the_discipline_grade_when_one_exists(monkeypatch):
    """'CAD is present' is not the same claim as 'the footings are measured'. When
    the discipline grade exists it is what the reviewer should read first."""
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness", lambda db, pid: {
        "takeoff_mode": "cad",
        "accuracy": {"grade": "measured_partial",
                     "headline": "PARTLY MEASURED — ...",
                     "allowance_divisions": ["03 Foundation & Concrete"]},
    }, raising=False)
    from app.services.quantity_gate import gate_status
    g = gate_status(_DB({"confirmed": 1}, []), "p1")
    assert g.accuracy.startswith("PARTLY MEASURED")
    assert g.grade["allowance_divisions"] == ["03 Foundation & Concrete"]


def test_the_gate_falls_back_to_the_mode_message_without_a_grade(monkeypatch):
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": "pdf"}, raising=False)
    from app.services.quantity_gate import gate_status
    g = gate_status(_DB({"confirmed": 1}, []), "p1")
    assert "LESS ACCURATE" in g.accuracy and g.grade is None


def test_a_cad_project_also_needs_a_SIGNED_analysis(monkeypatch):
    """All quantities confirmed is not the same as the analysis being signed off.
    A set with a missing storey can have every quantity it DID read ticked."""
    import app.services.estimate_readiness as er
    monkeypatch.setattr(er, "compute_readiness",
                        lambda db, pid: {"takeoff_mode": "cad"}, raising=False)
    from app.services.quantity_gate import assert_gate_closed
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        assert_gate_closed(_DB({"confirmed": 9}, [], pack_status="draft",
                               has_cad=True), "p1")
    assert ei.value.detail["error"] == "drawing_analysis_not_approved"
    # and it passes once signed
    assert_gate_closed(_DB({"confirmed": 9}, [], pack_status="approved",
                           has_cad=True), "p1")
