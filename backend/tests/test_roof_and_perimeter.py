"""Roof area and perimeter — the two derived quantities that were wrong in a way
that still looked plausible, verified against 1234 Example's drawing.

Ground truth from the architect's own site plan (Bradbury Architecture, sheet
A1.04, 23 December 2025):
    "399.59 sq ft garage plus 268 sq ft office = 668 sq ft proposed"
    garage 20'-6" x 19'-5" = 398 sf; office 19'-9" x 13'-7" = 268 sf
    "Required Effective R-Value for Flat Roof = R30"   -> flat, single storey
"""
import math

from app.services.drawing.cad_extractor import Takeoff, _check_perimeter, _set_roof_area


def _t(**derived):
    t = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
    t.derived.update(derived)
    return t


def test_a_wandering_outline_is_refused_as_a_perimeter():
    """1234 Example: the engine traced 156.9 lf around 799.3 sf. The building
    is 668 sf and its outline measures 107-129 lf. The AREA was only 19% high —
    inside tolerance — while the perimeter was 22-47% high, because a trace that
    wanders around a jog gains length much faster than it gains area."""
    t = _t(geom_area_sf=799.3, geom_perimeter_lf=156.9)
    _check_perimeter(t)
    assert t.derived["geom_perimeter_reliable"] is False
    assert "perimeter_lf" not in t.derived          # never priced silently
    assert any("compactness" in w for w in t.warnings)


def test_a_plausible_outline_is_written_as_a_quantity():
    """The real building: 668 sf inside a 20.5 x 33 ft outline = 107 lf."""
    t = _t(geom_area_sf=668.0, geom_perimeter_lf=107.0)
    _check_perimeter(t)
    assert t.derived["geom_perimeter_reliable"] is True
    assert t.derived["perimeter_lf"] == 107.0


def test_compactness_is_scale_free():
    """Doubling the building must not change the verdict."""
    small = _t(geom_area_sf=668.0, geom_perimeter_lf=107.0)
    big = _t(geom_area_sf=668.0 * 4, geom_perimeter_lf=107.0 * 2)
    _check_perimeter(small); _check_perimeter(big)
    assert small.derived["geom_perimeter_compactness"] == big.derived["geom_perimeter_compactness"]


def test_roof_is_one_storey_not_the_sum_of_storeys():
    """A two-storey house has one roof. Summing the storeys prices two."""
    t = _t(floor_area_sf=1600.0)
    views = [{"rooms_centerline_sf": 900.0}, {"rooms_centerline_sf": 700.0}]
    _set_roof_area(t, views, "measured_rooms", lambda v: v["rooms_centerline_sf"])
    assert t.derived["roof_area_sf"] == 900.0
    assert t.derived["roof_area_source"] == "largest_view:measured_rooms"


def test_roof_states_what_it_excludes():
    """The number is a plan projection. Overhang and slope are not in it, and a
    reviewer who does not know that will under-price the roofing."""
    t = _t(floor_area_sf=670.1)
    _set_roof_area(t, [{"rooms_centerline_sf": 670.1}], "measured_rooms",
                   lambda v: v["rooms_centerline_sf"])
    note = " ".join(t.warnings)
    assert "overhang" in note and "slope" in note
    # One view: it cannot tell a single storey from two drawn together, so it says so.
    assert "one storey" in note or "one view" in note


def test_the_1234_roof_matches_the_drawing_for_a_single_storey_building():
    """Office at FF 214.50 and garage at FF 214.00 are 6 in apart — side by side
    at grade, not stacked. So the flat roof covers the whole 668 sf footprint."""
    t = _t(floor_area_sf=670.1)
    _set_roof_area(t, [{"rooms_centerline_sf": 670.1}], "measured_rooms",
                   lambda v: v["rooms_centerline_sf"])
    assert abs(t.derived["roof_area_sf"] - 668.0) / 668.0 < 0.01
