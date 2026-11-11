"""Separator handling in filename categorisation.

Underscores are WORD characters, so `\\bscope\\b` does not match
"Project_Scope_Summary.docx". Every filename restored from R2 is
underscore-separated, which is how 158 documents ended up in "other" — including
the scope document whose absence blocked an estimate on a live project.

The same defect was found and fixed in `estimate_readiness._discipline` earlier;
it was present in the categoriser too and only one of the two was corrected."""
import pytest

from app.routers.documents import _suggest_category


@pytest.mark.parametrize("filename,expected", [
    ("1234_Example_Way_Project_Scope_Summary.docx", "scope"),
    ("Mechanical_Plans.pdf", "drawing"),
    ("Structural_Drawings_Rev2.pdf", "drawing"),
    ("Geotechnical_Report_Final.pdf", "report"),
    ("Site_Survey_2025.pdf", "report"),
])
def test_underscore_separated_names_categorise(filename, expected):
    assert _suggest_category(filename, None) == expected


@pytest.mark.parametrize("filename,expected", [
    ("Project Scope Summary.docx", "scope"),
    ("Mechanical Plans.pdf", "drawing"),
    ("Geotechnical Report.pdf", "report"),
])
def test_space_separated_names_still_categorise(filename, expected):
    """The fix must not trade one separator for another."""
    assert _suggest_category(filename, None) == expected


def test_statement_of_work_stays_uncategorised_by_deliberate_choice():
    """Not a gap: the billing rule excludes it by lookahead so it is never
    mislabelled as money, and nothing else claims it. Whether a "statement of
    work" is a scope or a contract exhibit differs by firm, so it is left for a
    person to file — see test_document_categorisation."""
    assert _suggest_category("Statement_of_Work.pdf", None) == "other"


def test_hyphen_separated_names_categorise():
    assert _suggest_category("1234-Example-Scope-of-Work.pdf", None) == "scope"


def test_a_photo_is_still_decided_by_mime_not_name():
    assert _suggest_category("Scope_Photo.jpg", "image/jpeg") == "photo"
