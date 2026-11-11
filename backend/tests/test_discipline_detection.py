"""Discipline detection from a filename.

Every case here is a real filename from the brio-pm document store.
"""
import pytest

from app.services.estimate_readiness import _discipline


@pytest.mark.parametrize("filename,expected", [
    # A bare "Plan" with no discipline marker is architectural — that is what an
    # architect's file is called. This one is the CAD file the whole takeoff
    # engine was built against, and it used to resolve to "general".
    ("1234 Example - Plan.dxf", "architectural"),
    ("1234 Example- Architectural Drawings.pdf", "architectural"),
    ("107 Maple-Sheet - A6 - BASEMENT FLOOR PLAN.dwg", "architectural"),
    # Underscores are word characters, so \bmechanical\b does not match
    # "Mechanical_Plans" unless separators are normalised first. Every filename
    # recovered from R2 is underscore-separated.
    ("Mechanical_Plans.pdf", "mechanical"),
    ("STRUCTURAL_PLAN-6_NOV_2025.pdf", "structural"),
    ("Sample_Tower-Electrical.pdf", "electrical"),
    ("SCEDULE B- BCBC 2024 Plumbing.pdf", "plumbing"),
    ("Stormwater Management Plan.pdf", "civil"),
    ("2406-015 T2 IMP 17APRIL2025.dwg", "architectural"),
])
def test_discipline_from_filename(filename, expected):
    assert _discipline(filename) == expected


def test_structural_wins_over_the_architectural_catch_all():
    """'Structural Plan' contains 'plan'. Order matters: the catch-all must be
    tested last or every consultant's drawing reads as architectural."""
    assert _discipline("STRUCTURAL PLAN.pdf") == "structural"
    assert _discipline("Civil Site Plan.pdf") == "civil"
    assert _discipline("Mechanical Plan.pdf") == "mechanical"


def test_a_non_drawing_is_not_forced_into_a_discipline():
    assert _discipline("Deposit_Invoice.pdf") == "general"
    assert _discipline("Client Information.pdf") == "general"


@pytest.mark.parametrize("filename,expected", [
    # Consultants stamp the discipline into the filename as a single letter. It
    # is written by the issuer, so it beats anything we infer.
    ("105-107 Maple Cres_A_DWG_Arhitectural BP.pdf", "architectural"),
    ("105-107 Maple Cres_C_DWG_SMP Drawings.pdf", "civil"),
    ("105-107 Maple Cres_S_DWG_Structural.pdf", "structural"),
    # The architect's output under other names.
    ("20221213-109 Maple_Issued for Coordination _2_ _1_.pdf", "architectural"),
    ("202211215-107 Maple_In-progress drawing _1_ _1_.pdf", "architectural"),
    ("1234-Example-Window-Door-Schedule-PRELIM.pdf", "architectural"),
    ("Copy of _2020.11.26_ - 4100 Spruce First Plans.pdf", "architectural"),
    # Civil under other names.
    ("Copy of _2018.06.26_ - SWMP Details DWG.pdf", "civil"),
    ("Copy of _2019.03.25_ - Streetlight IFC Drawing.pdf", "civil"),
    ("Copy of _2018.09.17_ - Catch Basin Insert Specs.pdf", "civil"),
])
def test_real_filenames_from_the_document_store(filename, expected):
    assert _discipline(filename) == expected


def test_plurals_are_matched():
    """'Plans', 'Sections', 'Elevations'. A singular-only pattern left
    '4100 Spruce First Plans.pdf' unattributed — the same plural bug that made
    the sheet audit report 'no floor plan' for a file called FLOOR PLANS."""
    for f in ("Site Plans.pdf", "Building Sections.pdf", "Elevations.pdf"):
        assert _discipline(f) != "general", f


def test_a_fee_schedule_is_not_a_drawing():
    """Not everything filed under 'drawing' is one. This must stay 'general' so
    it shows up as needing recategorisation rather than inflating the grade."""
    assert _discipline("Schedule of Fees _ Deposits.pdf") == "general"


def test_smp_and_swmp_are_both_civil():
    """SMP (Stormwater Management Plan) and SWMP are the same document. The
    optional letter is the W — a pattern of 's?wmp' matches 'wmp' and misses
    every SMP drawing, which is what 1234 Example's civil set is called."""
    assert _discipline("Garage & Office Construction - SMP (R3) Drawings.pdf") == "civil"
    assert _discipline("Copy of _2018.06.26_ - SWMP Details DWG.pdf") == "civil"
