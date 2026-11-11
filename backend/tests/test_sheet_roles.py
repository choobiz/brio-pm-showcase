"""Which sheets may contribute a measured quantity.

Found by running the takeoff over every CAD drawing in the system rather than
the one it was built against: six Maple section sheets between them reported
~14,800 sf of floor area, because rasterising a vertical cut finds closed
regions and calls them rooms. Since sheets SUM, that would have been ADDED to
the real plans."""
from app.services.drawing_analysis import _NOT_MEASURABLE
from app.services.drawing.set_completeness import sheet_type


def test_a_section_sheet_is_refused():
    name = "107 Maple-Coordination Set-Sheet - A14 - BUILDING SECTIONS.dwg"
    assert sheet_type(name) == "section"
    assert _NOT_MEASURABLE.get(sheet_type(name))


def test_a_site_plan_is_refused_because_it_repeats_the_floor_plan():
    assert _NOT_MEASURABLE.get(sheet_type("111 Maple-Sheet - A2 - SITE PLAN.dwg"))


def test_a_floor_plan_is_measured():
    for name in ("107 Maple-Coordination Set-Sheet - A6 - BASEMENT FLOOR PLAN.dwg",
                 "109 Maple--Sheet - A6 - BASEMENT _ LEVEL 1 FLOOR PLANS.dwg"):
        assert sheet_type(name) == "floor_plan"
        assert not _NOT_MEASURABLE.get(sheet_type(name))


def test_an_unclassified_sheet_is_still_measured():
    """A sheet named only 'Plan' is what an architect's own export is usually
    called. Refusing to measure it would cost far more than the noise it admits
    — it is the file the whole engine was validated against."""
    assert not _NOT_MEASURABLE.get(sheet_type("1234 Example - Plan.dxf") or "")


def test_elevations_and_schedules_are_refused():
    assert _NOT_MEASURABLE.get(sheet_type("A9 - BUILDING ELEVATIONS.dwg"))
    assert _NOT_MEASURABLE.get(sheet_type("A10 - DOOR SCHEDULE.dwg"))
