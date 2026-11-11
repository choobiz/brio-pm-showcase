"""Drawing register: duplicates first, then revisions.

⚠️ EVERY FILENAME IN THIS FILE IS REAL. They are taken verbatim from the
production document set, because a parser tested against filenames I invented
would be tested against my idea of how consultants name things rather than how
they actually do. The awkward cases here — a file uploaded twice with different
separators, six "Copy of X" pairs, a date-only revision, an "(R2)" — are all
things that are genuinely in there.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.drawing_register import (  # noqa: E402
    build_drawing_register,
    extract_issue_date,
    extract_revision,
    extract_sheet_number,
    normalise,
    sheet_key,
)


def _doc(i, filename, size, created="2026-01-01"):
    return {"id": f"d{i}", "filename": filename, "size_bytes": size, "created_at": created}


# ── the same file arriving twice ──────────────────────────────────────────

def test_separators_do_not_make_a_new_document():
    """Both of these are in the set, and both are 386,732 bytes."""
    a = "STRUCTURAL PLAN-6 NOV 2025-1234 EXAMPLE WAY, NORTH VANCOUVER, B.C..pdf"
    b = "STRUCTURAL_PLAN-6_NOV_2025-1234_EXAMPLE_WAY_NORTH_VANCOUVER_B.C..pdf"
    assert normalise(a) == normalise(b)


def test_copy_of_is_not_a_revision():
    assert normalise("Copy of 107 Maple_Coordination Set.pdf") == normalise(
        "107 Maple_Coordination Set.pdf")


def test_download_suffixes_are_not_revisions():
    """"_1_" and "_2_" come from downloading the same file twice."""
    assert normalise("202211215-107 Maple_In-progress drawing _1_ _1_.pdf") == normalise(
        "202211215-107 Maple_In-progress drawing.pdf")
    assert normalise("TOPO-NV4848dxf _2_.dwg") == normalise("TOPO-NV4848dxf.dwg")


def test_duplicates_are_collapsed_before_revisions_are_inferred():
    """The whole reason for the ordering: three identical uploads must not be
    reported as three revisions."""
    docs = [_doc(i, "105-107 Maple Cres_C_DWG_Erosion Control Plan.pdf", 725360)
            for i in range(3)]
    reg = build_drawing_register(docs)
    assert reg["summary"]["duplicates_found"] == 2
    assert reg["summary"]["distinct_drawings"] == 1
    assert reg["sheets"][0]["has_history"] is False, \
        "one file uploaded three times is not a revision history"


def test_same_name_different_size_is_not_a_duplicate():
    """Size is what separates 'uploaded twice' from 'genuinely changed'."""
    docs = [_doc(1, "Plan.pdf", 100), _doc(2, "Plan.pdf", 200)]
    assert build_drawing_register(docs)["summary"]["duplicates_found"] == 0


# ── genuine revisions ─────────────────────────────────────────────────────

def test_the_smp_revision_pair_is_recognised():
    """R2 and R3 of the same drawing — 4,804,232 and 4,792,306 bytes."""
    docs = [
        _doc(1, "1234 Example Way, North Vancouver - Garage & Office Construction - SMP (R2) Drawings.pdf", 4804232),
        _doc(2, "1234 Example Way, North Vancouver - Garage & Office Construction - SMP (R3) Drawings.pdf", 4792306),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 1
    sheet = reg["sheets"][0]
    assert sheet["revision_count"] == 2
    assert [r["revision"] for r in sheet["revisions"]] == ["R3", "R2"]
    assert sheet["revisions"][0]["is_current"] is True
    assert sheet["revisions"][1]["is_current"] is False


def test_a_duplicate_of_one_revision_does_not_become_a_third():
    """The set contains an underscore-separated copy of the R2 file."""
    docs = [
        _doc(1, "1234 Example Way, North Vancouver - Garage & Office Construction - SMP (R2) Drawings.pdf", 4804232),
        _doc(2, "1234_Example_Way_North_Vancouver_-_Garage_Office_Construction_-_SMP_R2_Drawings.pdf", 4804232),
        _doc(3, "1234 Example Way, North Vancouver - Garage & Office Construction - SMP (R3) Drawings.pdf", 4792306),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["duplicates_found"] == 1
    assert reg["sheets"][0]["revision_count"] == 2


def test_a_date_can_be_the_revision():
    """The structural plans carry no revision code — only an issue date."""
    docs = [
        _doc(1, "Structural Plan-28 Oct 2025-1234 Example Way, North Vancouver, BC.pdf", 392338),
        _doc(2, "STRUCTURAL PLAN-6 NOV 2025-1234 EXAMPLE WAY, NORTH VANCOUVER, B.C..pdf", 386732),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 1, \
        "removing the date token is what joins these two"
    revs = reg["sheets"][0]["revisions"]
    assert revs[0]["issued"] == "2025-11-06" and revs[0]["is_current"] is True
    assert revs[1]["issued"] == "2025-10-28"
    assert reg["sheets"][0]["grouped_because"] == "date_token"


def test_preliminary_never_outranks_an_issued_revision():
    docs = [
        _doc(1, "1234-Example-Window-Schedule-PRELIM.pdf", 100),
        _doc(2, "1234-Example-Window-Schedule-Rev A.pdf", 120),
    ]
    revs = build_drawing_register(docs)["sheets"][0]["revisions"]
    assert revs[0]["revision"] == "Rev A" and revs[0]["is_current"] is True


# ── extraction, piece by piece ────────────────────────────────────────────

def test_revision_forms_seen_in_the_real_set():
    assert extract_revision("… SMP (R2) Drawings.pdf")["label"] == "R2"
    assert extract_revision("…_SMP_R3_Drawings.pdf")["label"] == "R3"
    assert extract_revision("960-W-Willow-Floor-Plan-RevE.png")["label"] == "Rev E"
    assert extract_revision("1234-Example-Window-Schedule-PRELIM.pdf")["kind"] == "preliminary"
    assert extract_revision("107 Maple_Coordination Set.pdf") is None


def test_a_street_number_is_not_a_revision():
    """"105-107 Maple" must not read as revision 107."""
    assert extract_revision("105-107 Maple Cres_C_DWG_Erosion Control Plan.pdf") is None


def test_date_forms_seen_in_the_real_set():
    assert extract_issue_date("Structural Plan-28 Oct 2025-1234 Example Way.pdf")["date"] == (2025, 10, 28)
    assert extract_issue_date("STRUCTURAL PLAN-6 NOV 2025-….pdf")["date"] == (2025, 11, 6)
    assert extract_issue_date("Copy of _2021.08.12_ - Final Structural Plans 1.pdf")["date"] == (2021, 8, 12)
    assert extract_issue_date("109 Maple_Issued for coordination 20230123.pdf")["date"] == (2023, 1, 23)
    assert extract_issue_date("107 Maple_Coordination Set.pdf") is None


def test_sheet_numbers_are_read_only_where_labelled():
    assert extract_sheet_number("107 Maple-Coordination Set-Sheet - A14 - BUILDING SECTIONS.dwg") == "A14"
    assert extract_sheet_number("109 Maple--Sheet - A2 - SITE PLAN.dwg") == "A2"
    assert extract_sheet_number("1234ExamplWay2025_-_Sheet_-_A1-04_-_Site_Plan-Cover_Sheet.pdf") == "A104"


def test_a_bare_code_is_not_taken_as_a_sheet_number():
    """A wrong sheet number silently merges two unrelated drawings, which is
    worse than having none. Only the labelled form is trusted."""
    assert extract_sheet_number("M3415-15 _2_.dwg") is None
    assert extract_sheet_number("Copy of 03-03E-S-02.pdf") is None
    assert extract_sheet_number("P3 CNV E27 PCA June 25 2025 A1.pdf") is None


def test_different_sheets_of_one_set_stay_separate():
    docs = [
        _doc(1, "107 Maple-Coordination Set-Sheet - A2 - SITE PLAN.dwg", 10),
        _doc(2, "107 Maple-Coordination Set-Sheet - A14 - BUILDING SECTIONS.dwg", 20),
        _doc(3, "107 Maple-Coordination Set-Sheet - A15 - BUILDING SECTIONS.dwg", 30),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 3
    assert {s["sheet_number"] for s in reg["sheets"]} == {"A2", "A14", "A15"}


def test_the_same_sheet_number_on_different_buildings_is_not_a_revision():
    """⚠️ THE REGRESSION. 105/107 Maple is a multi-lot site: 107, 109 and
    111 are three buildings whose drawing sets share a numbering scheme.

    Keying on the sheet number merged them into one drawing with two superseded
    revisions — and four of the eight histories found across the whole
    production set were this same mistake. Only the real filenames show it."""
    docs = [
        _doc(1, "107 Maple-Coordination Set-Sheet - A2 - SITE PLAN.dwg", 460462),
        _doc(2, "109 Maple--Sheet - A2 - SITE PLAN.dwg", 415560),
        _doc(3, "111 Maple-Sheet - A2 - SITE PLAN.dwg", 393220),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 3, \
        "three buildings, not three revisions of one drawing"
    assert reg["summary"]["drawings_with_more_than_one_revision"] == 0
    # The sheet number is still reported — it is just not what identifies them.
    assert all(s["sheet_number"] == "A2" for s in reg["sheets"])


def test_a_reissue_of_the_same_sheet_still_groups():
    """The name carries the building, so a genuine revision of ONE building's
    sheet still joins."""
    docs = [
        _doc(1, "107 Maple-Coordination Set-Sheet - A2 - SITE PLAN.dwg", 460462),
        _doc(2, "107 Maple-Coordination Set-Sheet - A2 - SITE PLAN Rev B.dwg", 470000),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 1
    assert reg["sheets"][0]["revisions"][0]["revision"] == "Rev B"


# ── honesty ───────────────────────────────────────────────────────────────

def test_every_group_says_why_it_was_grouped():
    docs = [_doc(1, "107 Maple_Coordination Set.pdf", 10)]
    sheet = build_drawing_register(docs)["sheets"][0]
    assert sheet["grouped_because"] == "name_only"
    assert "no sheet number" in sheet["grouped_because_plain"]


def test_a_lone_file_is_not_called_a_revision_history():
    sheet = build_drawing_register([_doc(1, "Plan.pdf", 10)])["sheets"][0]
    assert sheet["has_history"] is False
    assert sheet["revision_count"] == 1


def test_an_empty_set_is_handled():
    reg = build_drawing_register([])
    assert reg["sheets"] == [] and reg["summary"]["distinct_drawings"] == 0


def test_a_dwg_and_dxf_of_one_plan_are_not_two_revisions():
    """Real pair, real sizes. They survive the duplicate check because the two
    formats compress differently — and without a rule they read as a revision
    history that never existed. The takeoff already treats them as one sheet;
    the register must agree, or one of the two is lying to the estimator."""
    docs = [
        _doc(1, "1234 Example - Plan.dwg", 209000),
        _doc(2, "1234 Example - Plan.dxf", 1419225),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 1
    assert reg["summary"]["drawings_with_more_than_one_revision"] == 0
    assert reg["sheets"][0]["revisions"][0]["filename"].endswith(".dxf"), \
        "DXF wins, matching drawing_analysis._dedupe_formats"
    assert reg["duplicates"][0]["reason"] == "same drawing exported in another CAD format"


def test_a_lone_dwg_is_untouched():
    reg = build_drawing_register([_doc(1, "Plan.dwg", 100)])
    assert reg["summary"]["duplicates_found"] == 0
    assert reg["summary"]["distinct_drawings"] == 1


def test_the_original_survives_a_duplicate_not_the_copy():
    """Real pair. Showing "Copy of X" as the current revision while X sits
    beside it marked superseded is backwards."""
    docs = [
        _doc(1, "Copy of 109 Maple_Issued for coordination 20230123.pdf", 3590229),
        _doc(2, "109 Maple_Issued for coordination 20230123.pdf", 3590229),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["duplicates_found"] == 1
    kept = reg["sheets"][0]["revisions"][0]["filename"]
    assert not kept.startswith("Copy of"), f"kept the copy: {kept}"
    assert reg["duplicates"][0]["filename"].startswith("Copy of")


def test_the_survivor_does_not_depend_on_input_order():
    """It changed with the order rows were read, so the same set could answer
    differently on two runs."""
    a = _doc(1, "Copy of Plan _1_.pdf", 100)
    b = _doc(2, "Plan.pdf", 100)
    forward = build_drawing_register([a, b])["sheets"][0]["revisions"][0]["filename"]
    reverse = build_drawing_register([b, a])["sheets"][0]["revisions"][0]["filename"]
    assert forward == reverse == "Plan.pdf"


def test_the_readable_name_survives_not_the_underscore_mangled_one():
    """Real pair, identical bytes. Replacing ", " and " - " with "_" makes the
    mangled name SHORTER, so a length-only rule picked the wrong one to show."""
    docs = [
        _doc(1, "1234_Example_Way_North_Vancouver_-_Garage_Office_Construction_-_SMP_R2_Drawings.pdf", 4804232),
        _doc(2, "1234 Example Way, North Vancouver - Garage & Office Construction - SMP (R2) Drawings.pdf", 4804232),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["duplicates_found"] == 1
    kept = reg["sheets"][0]["revisions"][0]["filename"]
    assert kept.startswith("1234 Example Way,"), f"kept the mangled name: {kept}"


def test_an_underscore_mangled_name_still_groups_with_its_revisions():
    """⚠️ A silent failure: the token is FOUND in a space-normalised form and
    was REMOVED from the raw one, so "6_NOV_2025" kept its date in the key and
    the file sat alone. Nothing looked wrong — the drawing still appeared, just
    without its history."""
    spaced = "Structural Plan-28 Oct 2025-1234 Example Way, North Vancouver, BC.pdf"
    mangled = "STRUCTURAL_PLAN-6_NOV_2025-1234_EXAMPLE_WAY_NORTH_VANCOUVER_B.C..pdf"
    assert sheet_key(spaced) == sheet_key(mangled)

    reg = build_drawing_register([_doc(1, spaced, 392338), _doc(2, mangled, 386732)])
    assert reg["summary"]["distinct_drawings"] == 1
    assert reg["sheets"][0]["revisions"][0]["issued"] == "2025-11-06"


def test_an_underscored_revision_code_also_groups():
    docs = [
        _doc(1, "Garage_Office_Construction_-_SMP_R2_Drawings.pdf", 100),
        _doc(2, "Garage_Office_Construction_-_SMP_R3_Drawings.pdf", 110),
    ]
    reg = build_drawing_register(docs)
    assert reg["summary"]["distinct_drawings"] == 1
    assert [r["revision"] for r in reg["sheets"][0]["revisions"]] == ["R3", "R2"]
