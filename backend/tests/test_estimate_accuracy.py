"""Accuracy grading from the documents actually uploaded."""
import pytest

from app.services.estimate_accuracy import DISCIPLINE_IMPACT, grade_accuracy


def cov(**kw):
    """cov(architectural="cad", structural="pdf") -> coverage dict."""
    return {k: {"cad": v == "cad", "pdf": v == "pdf"} for k, v in kw.items()}


def test_no_drawings_is_scope_only():
    a = grade_accuracy({})
    assert a.grade == "scope_only"
    assert "order of magnitude" in a.headline
    assert a.coverage_pct == 0


def test_pdfs_only_are_read_not_measured():
    a = grade_accuracy(cov(architectural="pdf", structural="pdf", civil="pdf"))
    assert a.grade == "read_from_pdf"
    assert "READ, NOT MEASURED" in a.headline
    assert a.measured_divisions == []


def test_architectural_cad_alone_is_only_PARTLY_measured():
    """The failure this grading exists to prevent: calling a set 'measured'
    because it has CAD, when the footings — the expensive half — are a guess."""
    a = grade_accuracy(cov(architectural="cad"))
    assert a.grade == "measured_partial"
    assert "03 Foundation & Concrete" in a.allowance_divisions
    assert "04 Rough Carpentry & Framing" in a.measured_divisions
    assert any("structural" in w for w in a.warnings)


def test_full_set_is_measured_complete():
    a = grade_accuracy(cov(architectural="cad", structural="cad", civil="cad",
                           mechanical="cad", electrical="cad", plumbing="cad"))
    assert a.grade == "measured_complete"
    assert a.coverage_pct == 100
    assert a.allowance_divisions == []


def test_even_a_complete_set_says_geometry_is_not_sizing():
    """Full CAD does not mean the MEP is priced from loads. Claiming otherwise is
    how a measured takeoff gets trusted for something it never measured."""
    a = grade_accuracy(cov(architectural="cad", structural="cad", civil="cad",
                           mechanical="cad", electrical="cad", plumbing="cad"))
    assert any("not loads or sizing" in w for w in a.warnings)


def test_a_division_governed_by_two_disciplines_counts_as_measured_if_either_is():
    """04 Rough Carpentry is governed by architectural AND structural. With
    architectural CAD it is measured, and must not also appear as an allowance."""
    a = grade_accuracy(cov(architectural="cad", structural="none"))
    assert "04 Rough Carpentry & Framing" in a.measured_divisions
    assert "04 Rough Carpentry & Framing" not in a.allowance_divisions


def test_pdf_counts_for_half_of_cad():
    all_pdf = grade_accuracy(cov(**{d: "pdf" for d in DISCIPLINE_IMPACT}))
    all_cad = grade_accuracy(cov(**{d: "cad" for d in DISCIPLINE_IMPACT}))
    assert all_cad.coverage_pct == 100
    assert 48 <= all_pdf.coverage_pct <= 52


def test_next_uploads_are_ordered_by_budget_impact():
    """Naming everything absent is not advice. The first item must be the one
    that moves the estimate most."""
    a = grade_accuracy(cov(architectural="cad"))
    names = [n["discipline"] for n in a.next_uploads]
    assert names[0] == "structural"           # 20% weight, the largest gap
    assert names.index("civil") < names.index("mechanical")
    assert all(n["discipline"] != "architectural" for n in a.next_uploads)


def test_a_pdf_discipline_is_asked_for_its_CAD_not_for_drawings():
    a = grade_accuracy(cov(architectural="cad", structural="pdf"))
    st = [n for n in a.next_uploads if n["discipline"] == "structural"][0]
    assert st["have"] == "pdf" and "DWG" in st["ask"]


class _Audit:
    by_building = {"107": {"storeys_expected": ["basement", "level_1", "level_2"],
                           "storeys_covered": ["basement", "level_1"],
                           "storeys_missing": ["level_2"]}}


def test_a_missing_storey_is_reported_even_when_every_discipline_has_cad():
    """Discipline coverage says nothing about coverage of the BUILDING. Maple
    had architectural CAD and no level 2 plans; every quantity covered two thirds
    of the building and nothing said so."""
    a = grade_accuracy(cov(**{d: "cad" for d in DISCIPLINE_IMPACT}), set_audit=_Audit())
    assert a.grade == "measured_complete"
    assert any("level_2" in w and "2 of 3 storeys" in w for w in a.warnings), a.warnings


def test_unattributed_drawings_are_counted_not_dropped():
    """A drawing whose discipline cannot be read contributes to nothing. Dropping
    it silently makes the estimate look less supported than it is — which is how
    1234 Example reported 'no drawings at all' while holding its own CAD file."""
    a = grade_accuracy(cov(architectural="cad"), unattributed=3)
    assert any("3 drawing(s) could not be attributed" in w for w in a.warnings)


def test_no_warning_when_everything_is_attributed():
    a = grade_accuracy(cov(architectural="cad"), unattributed=0)
    assert not any("could not be attributed" in w for w in a.warnings)


def test_pdf_structural_is_not_measured_complete():
    """Requiring a discipline to be merely PRESENT let 357-359 East 4th grade as
    MEASURED at 62% coverage — a contradiction on its face. A budget-critical
    discipline that only has PDFs is read, not measured."""
    a = grade_accuracy(cov(architectural="cad", structural="pdf", civil="cad",
                           mechanical="cad", electrical="cad", plumbing="cad"))
    assert a.grade == "measured_partial"
    assert any("PDF only for: structural" in w for w in a.warnings)
    assert a.coverage_pct < 100


def test_MEP_on_pdf_still_counts_as_complete():
    """MEP is not budget-critical — holding the grade hostage to it would make
    'complete' unreachable on almost every real project."""
    a = grade_accuracy(cov(architectural="cad", structural="cad", civil="cad",
                           mechanical="pdf", electrical="pdf", plumbing="pdf"))
    assert a.grade == "measured_complete"
