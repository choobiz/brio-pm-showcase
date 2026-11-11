"""A discipline word alone is weak evidence of a drawing.

Found by dry-running the separator fix over the real corpus: "Mechanical _
Ventilation Checklist.pdf" and "Garden Electrical Issue.docx" were both about to
become `drawing`, and `category='drawing'` feeds the estimate accuracy grade — a
checklist counted as a drawing inflates how measured a project looks.

The rule is not "a discipline needs a drawing noun". The corpus shows bare
discipline suffixes ARE drawings ("- Elec.pdf", "MECH - ... Issued Set.pdf").
It is that a DOCUMENT noun outranks a discipline mention."""
import pytest

from app.routers.documents import _suggest_category


@pytest.mark.parametrize("filename", [
    "Copy of _2020.05.04_ - Mechanical _ Ventilation Checklist.pdf",
    "_2025.12.09_ - Garden Electrical Issue.docx",
    "Electrical Inspection Certificate.pdf",
    "Structural Engineer Letter.pdf",
    "Plumbing Quote - Blueridge.pdf",
    "HVAC Warranty.pdf",
    "Mechanical Site Instructions.pdf",
])
def test_a_document_about_a_trade_is_not_a_drawing(filename):
    assert _suggest_category(filename, None) != "drawing"


@pytest.mark.parametrize("filename", [
    "PO-124225-A-ME - 7580 and 7590 6th Street, Burnaby - Elec.pdf",
    "MECH - 123 Main St - Issued Set.pdf",
    "Structural Drawings.dwg",
    "Mechanical_Plans.pdf",
    "Architectural Set Rev 3.pdf",
])
def test_real_discipline_drawings_still_categorise(filename):
    """The guard must not cost the cases the corpus shows are drawings."""
    assert _suggest_category(filename, None) == "drawing"


def test_a_manual_that_is_signed_is_not_a_contract():
    """"Home Maintenance Manual Signed.pdf" matched on a bare "signed"."""
    assert _suggest_category(
        "Copy of _2021.03.10_ - 357 Home Maintenance Manual Signed.pdf", None
    ) != "contract"


@pytest.mark.parametrize("filename", [
    "CCDC 2 Signed.pdf",
    "Construction Contract - Executed.pdf",
    "Owner Agreement.pdf",
])
def test_real_contracts_still_categorise(filename):
    assert _suggest_category(filename, None) == "contract"


def test_schedule_is_not_treated_as_a_document_noun():
    """"schedule" is deliberately absent from the document-noun list, so it does
    not SUPPRESS a discipline tag. It does not promote one either — "Door
    Schedule.pdf" alone stays uncategorised, which is existing behaviour this
    change does not touch."""
    assert _suggest_category("A10 - Door Schedule.pdf", None) == "other"
    assert _suggest_category("Mechanical Schedule.pdf", None) == "drawing"
