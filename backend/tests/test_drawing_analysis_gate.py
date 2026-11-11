"""The analysis pack gate: a signed review, not just confirmed rows."""
import pytest
from fastapi import HTTPException

from app.services.quantity_gate import _assert_pack_approved


class _Row(tuple):
    pass


class _DB:
    """Answers the two queries _assert_pack_approved makes, in order."""

    def __init__(self, pack_status, has_cad=True):
        self._pack = pack_status
        self._cad = has_cad

    def execute(self, stmt, params=None):
        sql = str(stmt)
        class R:
            def __init__(self, v):
                self._v = v
            def first(self_inner):
                return self_inner._v
        if "drawing_analysis_packs" in sql:
            return R((self._pack,) if self._pack else None)
        return R((1,) if self._cad else None)


def test_no_cad_means_nothing_to_analyse():
    """A PDF-only or scope-only project must not be blocked by a gate about CAD."""
    _assert_pack_approved(_DB(None, has_cad=False), "p1")  # must not raise


def test_cad_with_no_analysis_is_refused():
    with pytest.raises(HTTPException) as ei:
        _assert_pack_approved(_DB(None, has_cad=True), "p1")
    assert ei.value.status_code == 409
    assert ei.value.detail["error"] == "drawing_analysis_required"


def test_a_draft_analysis_is_not_a_signed_one():
    """Confirming forty numbers and signing the analysis are different acts.
    Without this an estimate could price a set whose missing storey nobody looked
    at, because each quantity that DID get read was ticked."""
    with pytest.raises(HTTPException) as ei:
        _assert_pack_approved(_DB("draft"), "p1")
    assert ei.value.detail["error"] == "drawing_analysis_not_approved"
    assert "draft" in ei.value.detail["message"]


def test_a_superseded_analysis_does_not_count():
    """A new reading of the drawings invalidates the sign-off given against the
    old one — otherwise a revised drawing set inherits the previous approval."""
    with pytest.raises(HTTPException):
        _assert_pack_approved(_DB("superseded"), "p1")


def test_an_approved_analysis_passes():
    _assert_pack_approved(_DB("approved"), "p1")  # must not raise
