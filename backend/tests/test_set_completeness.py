"""Set completeness — what is MISSING, established before anything is measured."""
from app.services.drawing.set_completeness import (
    Sheet, audit_set, levels_named, sheet_number, sheet_type,
)


def _set(**kw):
    """A complete 3-storey set for one building, overridable per test."""
    base = [
        Sheet("107 A2 SITE PLAN.dwg", "107", ("A", 2), "site_plan"),
        Sheet("107 A6 BASEMENT FLOOR PLAN.dwg", "107", ("A", 6), "floor_plan",
              storeys_covered=["basement"]),
        Sheet("107 A7 LEVEL 1 FLOOR PLAN.dwg", "107", ("A", 7), "floor_plan",
              storeys_covered=["level_1"]),
        Sheet("107 A8 LEVEL 2 FLOOR PLAN.dwg", "107", ("A", 8), "floor_plan",
              storeys_covered=["level_2"]),
        Sheet("107 A9 ROOF PLAN.dwg", "107", ("A", 9), "roof_plan"),
        Sheet("107 A10 ELEVATIONS.dwg", "107", ("A", 10), "elevation",
              levels_named=["basement", "level_1", "level_2", "roof"]),
        Sheet("107 A11 DOOR SCHEDULE.dwg", "107", ("A", 11), "schedule"),
        Sheet("107 A12 BUILDING SECTIONS.dwg", "107", ("A", 12), "section",
              levels_named=["basement", "level_1", "level_2", "roof"]),
    ]
    drop = kw.get("drop", ())
    return [s for s in base if s.type not in drop and s.name not in drop]


def test_a_complete_set_is_complete():
    a = audit_set(_set())
    assert a.complete, a.findings
    assert a.by_building["107"]["coverage"] == 1.0


def test_a_missing_upper_storey_is_found_from_the_sections():
    """The Maple case. Sections name three storeys; only two have plans. Nothing
    downstream said so, and every quantity was for two thirds of a building."""
    a = audit_set(_set(drop=("107 A8 LEVEL 2 FLOOR PLAN.dwg",)))
    assert not a.complete
    m = [f for f in a.findings if f.startswith("MISSING") and "storeys" in f]
    assert m and "level_2" in m[0], a.findings
    assert a.by_building["107"]["storeys_missing"] == ["level_2"]
    assert a.by_building["107"]["coverage"] == 0.67


def test_without_a_section_or_elevation_coverage_is_unknown_not_assumed_complete():
    """Sections and elevations are the only sheets that say how many floors the
    building has. Without either, reporting 'complete' is a guess dressed as fact."""
    a = audit_set(_set(drop=("section", "elevation")))
    assert any("storey count is unknown" in f for f in a.findings), a.findings
    assert any(f.startswith("MISSING") and "no section" in f for f in a.findings)


def test_a_floor_plan_naming_a_level_is_not_evidence_about_the_building():
    """A basement plan says 'BASEMENT'. That tells you what the sheet shows, not
    how many floors the building has — otherwise every set looks complete."""
    a = audit_set([
        Sheet("107 A6 BASEMENT FLOOR PLAN.dwg", "107", ("A", 6), "floor_plan",
              levels_named=["basement", "level_1", "level_2"],
              storeys_covered=["basement"]),
    ])
    assert a.by_building["107"]["storeys_expected"] == []
    assert any("storey count is unknown" in f for f in a.findings)


def test_sheet_number_gaps_are_named():
    a = audit_set(_set(drop=("107 A8 LEVEL 2 FLOOR PLAN.dwg",
                             "107 A9 ROOF PLAN.dwg")))
    g = [f for f in a.findings if "sheet numbers" in f]
    assert g and "A8" in g[0] and "A9" in g[0], a.findings


def test_each_building_is_audited_separately():
    sheets = _set() + [
        Sheet("109 A6 BASEMENT & LEVEL 1 FLOOR PLANS.dwg", "109", ("A", 6),
              "floor_plan", storeys_covered=["basement", "level_1"]),
        Sheet("109 A12 BUILDING SECTIONS.dwg", "109", ("A", 12), "section",
              levels_named=["basement", "level_1", "level_2", "roof"]),
    ]
    a = audit_set(sheets)
    assert a.by_building["107"]["storeys_missing"] == []
    assert a.by_building["109"]["storeys_missing"] == ["level_2"]


def test_an_empty_set_is_not_silently_complete():
    a = audit_set([])
    assert not a.complete and a.blocking


def test_plural_sheet_titles_are_classified():
    """'BASEMENT & LEVEL 1 FLOOR PLANS' is a floor plan. A singular-only pattern
    reported 'no floor plan' for it — a false alarm that trains people to ignore
    the audit entirely."""
    assert sheet_type("109 Maple - A6 - BASEMENT & LEVEL 1 FLOOR PLANS.dwg") == "floor_plan"
    assert sheet_type("107 A14 BUILDING SECTIONS.dwg") == "section"
    assert sheet_type("A10 - BUILDING ELEVATIONS.dwg") == "elevation"
    assert sheet_type("A11 - DOOR & WINDOW SCHEDULES.dwg") == "schedule"


def test_classifiers():
    assert sheet_type("107 A6 BASEMENT FLOOR PLAN.dwg") == "floor_plan"
    assert sheet_type("107 A14 BUILDING SECTIONS.dwg") == "section"
    assert sheet_type("A11 - DOOR SCHEDULE.dwg") == "schedule"
    assert sheet_number("107 Maple-Sheet - A14 - BUILDING SECTIONS.dwg") == ("A", 14)
    assert levels_named(["Roof Peak", "Level 2", "Level 1", "BASEMENT", "TO Footing"]) == \
        ["basement", "level_1", "level_2", "roof"]


def test_a_floor_plan_that_names_no_storey_is_not_reported_as_no_floor_plan():
    """1234, 2026-10-20: the set was one single-storey floor plan, and the audit
    said "Floor plans present: none" beside a page showing that plan. The list
    held the storeys the plans NAME, and this plan names none."""
    a = audit_set([Sheet("1234 Example - Plan.dwg", "1234", None, "floor_plan")])
    unknown = [f for f in a.findings if "storey count is" in f]
    assert unknown, a.findings
    assert "Floor plans present: none" not in unknown[0]
    assert "present but do not name a storey" in unknown[0]


def test_a_set_with_no_floor_plan_still_says_so():
    a = audit_set([Sheet("1234 A1.04 SITE PLAN.dwg", "1234", ("A", 1), "site_plan")])
    unknown = [f for f in a.findings if "storey count is" in f]
    assert unknown and "No floor plans." in unknown[0]
