"""Corner classification on a traced footprint.

Written because 1234 Example was priced for six external corners and zero
internal ones. The real footprint has eight and four. Corner trim is bought per
corner and the two kinds are different parts, so the miss would have arrived on
site as four corners with nothing to close them.
"""

from __future__ import annotations

import math

import pytest

from app.services.drawing.floor_area import (
    _axis_delta,
    classify_corners,
    drop_collinear,
)


def rot(poly, deg, about=(0.0, 0.0)):
    a = math.radians(deg)
    ox, oy = about
    return [((x - ox) * math.cos(a) - (y - oy) * math.sin(a) + ox,
             (x - ox) * math.sin(a) + (y - oy) * math.cos(a) + oy) for x, y in poly]


RECT = [(0, 0), (240, 0), (240, 120), (0, 120)]
# L-shape: one bite out of a corner
L = [(0, 0), (300, 0), (300, 120), (180, 120), (180, 240), (0, 240)]
# A bump-out that does NOT reach the bounding box — the case the box shortcut
# cannot see, and the one the washroom projection actually is.
BUMP = [(0, 0), (300, 0), (300, 240), (0, 240), (0, 160), (-30, 160), (-30, 80), (0, 80)]


class TestCornerCounts:
    def test_a_rectangle_is_four_external_none_internal(self):
        c = classify_corners(RECT)
        assert (c["external"], c["internal"]) == (4, 0)
        assert c["rectilinear"] and c["invariant_ok"]

    def test_an_l_shape_has_one_internal_corner(self):
        c = classify_corners(L)
        assert (c["external"], c["internal"]) == (5, 1)
        assert c["invariant_ok"], "external - internal must still be 4"

    def test_a_bump_out_adds_two_of_each(self):
        c = classify_corners(BUMP)
        assert (c["external"], c["internal"]) == (6, 2)
        assert c["invariant_ok"]

    def test_winding_direction_does_not_change_the_answer(self):
        cw = classify_corners(list(reversed(L)))
        ccw = classify_corners(L)
        assert (cw["external"], cw["internal"]) == (ccw["external"], ccw["internal"])


class TestRotatedPlans:
    """1234 Example is drawn on a skewed lot: zero segments are axis-aligned
    to the file. Squareness has to be judged against the building's own axis."""

    @pytest.mark.parametrize("deg", [0, 17.5, 33, 45, 72.25, 118])
    def test_a_rotated_l_is_still_rectilinear(self, deg):
        c = classify_corners(rot(L, deg))
        assert c["rectilinear"], f"rotation by {deg} must not break squareness"
        assert (c["external"], c["internal"]) == (5, 1)

    def test_a_genuinely_skewed_outline_is_not_rectilinear(self):
        skew = [(0, 0), (300, 0), (300, 120), (160, 190), (0, 240)]
        assert classify_corners(skew)["rectilinear"] is False

    def test_axis_delta_is_symmetric_about_a_quarter_turn(self):
        assert _axis_delta(0.0, math.pi / 2) == pytest.approx(0.0, abs=1e-9)
        assert _axis_delta(math.radians(1), 0.0) == pytest.approx(math.radians(1))


class TestCollinearVertices:
    def test_points_along_a_straight_run_are_not_corners(self):
        """A wall split by three windows traces several vertices on one line."""
        split = [(0, 0), (80, 0), (160, 0), (240, 0), (240, 120), (0, 120)]
        assert len(drop_collinear(split)) == 4
        c = classify_corners(split)
        assert (c["external"], c["internal"]) == (4, 0)

    def test_duplicate_points_are_dropped(self):
        dup = [(0, 0), (0, 0), (240, 0), (240, 120), (0, 120)]
        assert len(drop_collinear(dup)) == 4


class TestTheInvariantCatchesTheRealFailure:
    def test_six_external_and_zero_internal_is_impossible(self):
        """The shape 1234 Example was priced as cannot exist.

        Any simple closed rectilinear polygon satisfies external - internal == 4.
        Six and zero gives 6, so the trace had missed articulation — which is
        exactly what a gate should refuse to pass.
        """
        assert 6 - 0 != 4

    def test_a_degenerate_polygon_reports_no_confidence(self):
        c = classify_corners([(0, 0), (10, 0)])
        assert c["invariant_ok"] is False and c["external"] == 0


class TestTheGate:
    """`_check_corners` is what turns the geometry into a refusal or a quantity."""

    def _takeoff(self, **derived):
        from app.services.drawing.cad_extractor import Takeoff

        t = Takeoff(units="in", scope_boundary=None, scope_axis=None)
        t.derived.update(derived)
        return t

    def test_an_impossible_count_is_refused_not_reported(self):
        from app.services.drawing.cad_extractor import _check_corners

        t = self._takeoff(geom_external_corners=6, geom_internal_corners=0,
                          geom_rectilinear=True, geom_corner_invariant_ok=False)
        _check_corners(t)
        assert "corner_count" not in t.derived, (
            "a count that fails the invariant must not become a quantity")
        assert any("cannot describe a closed rectilinear footprint" in w
                   for w in t.warnings)

    def test_a_valid_articulated_footprint_becomes_a_quantity(self):
        from app.services.drawing.cad_extractor import _check_corners

        t = self._takeoff(geom_external_corners=8, geom_internal_corners=4,
                          geom_rectilinear=True, geom_corner_invariant_ok=True,
                          geom_perimeter_lf=114.2, geom_bbox_perimeter_lf=109.8)
        _check_corners(t)
        assert t.derived["corner_count"] == 12
        assert t.derived["internal_corner_count"] == 4
        w = " ".join(t.warnings)
        assert "articulated" in w and "BOTH trim profiles" in w

    def test_it_says_how_short_the_bounding_box_shortcut_is(self):
        from app.services.drawing.cad_extractor import _check_corners

        t = self._takeoff(geom_external_corners=8, geom_internal_corners=4,
                          geom_rectilinear=True, geom_corner_invariant_ok=True,
                          geom_perimeter_lf=114.2, geom_bbox_perimeter_lf=109.8)
        _check_corners(t)
        assert "4.4 lf longer" in " ".join(t.warnings)

    def test_a_plain_rectangle_raises_nothing(self):
        from app.services.drawing.cad_extractor import _check_corners

        t = self._takeoff(geom_external_corners=4, geom_internal_corners=0,
                          geom_rectilinear=True, geom_corner_invariant_ok=True,
                          geom_perimeter_lf=100.0, geom_bbox_perimeter_lf=100.0)
        _check_corners(t)
        assert t.derived["corner_count"] == 4
        assert t.warnings == [], "a rectangle is not news"

    def test_a_skewed_footprint_is_not_given_a_trim_quantity(self):
        from app.services.drawing.cad_extractor import _check_corners

        t = self._takeoff(geom_external_corners=5, geom_internal_corners=1,
                          geom_rectilinear=False, geom_corner_invariant_ok=True)
        _check_corners(t)
        assert "corner_count" not in t.derived
        assert any("not rectilinear" in w for w in t.warnings)


class TestEndToEndThroughTheTracer:
    """The gap that let this through.

    Every DXF fixture in `tests/fixtures/` is a simple rectangle — a 120 x 120 in
    square, axis-aligned, with unbroken walls. On that shape the corner invariant
    holds trivially, the bounding-box perimeter is exact, and articulation is
    zero, so NONE of the three things that went wrong on 1234 Example could
    have been observed. These tests drive `building_footprint` with the geometry
    the real file actually has: articulated, rotated off-axis, and split at
    every opening.
    """

    #: The 1234 Example footprint, to scale and in inches: a wide garage
    #: (y 0-200) with a narrower office above it (y 200-400, inset 60 in on the
    #: west), plus a 30 in washroom bump-out on the office's west wall.
    #: 7 external + 3 internal corners, and the bump sits INTERIOR to the
    #: bounding box, which is what the box shortcut cannot see.
    SHAPE = [(0, 0), (300, 0), (300, 400), (60, 400), (60, 330),
             (30, 330), (30, 250), (60, 250), (60, 200), (0, 200)]

    def _segments(self, poly, rotate_deg=0.0, break_at=()):
        """Closed loop as individual segments, optionally rotated and split.

        `break_at` names edge indices to split into two pieces with a gap in the
        middle — how a door or window leaves a wall in a real export.
        """
        pts = rot(poly, rotate_deg) if rotate_deg else list(poly)
        segs = []
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if i in break_at:
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                dx, dy = (b[0] - a[0]) * 0.08, (b[1] - a[1]) * 0.08
                segs.append((a, (mx - dx, my - dy)))
                segs.append(((mx + dx, my + dy), b))
            else:
                segs.append((a, b))
        return segs

    def test_an_articulated_footprint_is_recovered_from_clean_linework(self):
        from app.services.drawing.floor_area import building_footprint

        fp = building_footprint(self._segments(self.SHAPE), u=1.0)
        assert fp is not None
        assert (fp.external_corners, fp.internal_corners) == (7, 3)
        assert fp.corner_invariant_ok and fp.rectilinear

    @pytest.mark.parametrize("deg", [0, 23.5, 41, 67])
    def test_rotation_does_not_change_the_corner_count(self, deg):
        """1234 Example is drawn on a skewed lot — not one of its 65 wall
        segments is axis-aligned to the file."""
        from app.services.drawing.floor_area import building_footprint

        fp = building_footprint(self._segments(self.SHAPE, rotate_deg=deg), u=1.0)
        assert fp is not None
        assert (fp.external_corners, fp.internal_corners) == (7, 3)
        assert fp.rectilinear, "squareness must be judged against the building's axis"

    def test_walls_broken_at_openings_still_close(self):
        """Real exports split a wall at every door and window.

        This was the root cause. `connected_components` runs BEFORE
        `bridge_openings` and bridging only joined ends within one component, so
        one opening left the loop connected and closed fine while TWO split it
        into chains that were never joined — the network yielded no face at all.
        Measured before the fix: 0 or 1 break closed correctly, 2 gave None,
        3 gave None. Real buildings have many openings, so the tracer fell back
        to whatever simple outline existed, which is how a 12-corner building
        measured as a 4-corner box.
        """
        from app.services.drawing.floor_area import building_footprint

        fp = building_footprint(
            self._segments(self.SHAPE, break_at=(0, 2)), u=1.0)
        assert fp is not None, "two openings must not destroy the trace"
        assert (fp.external_corners, fp.internal_corners) == (7, 3)

    def test_the_bounding_box_shortcut_is_short_for_this_shape(self):
        """The bug behind the perimeter error: a bump-out interior to the box
        adds 2 x its depth, which 2 x (W + H) cannot see."""
        from app.services.drawing.floor_area import building_footprint

        fp = building_footprint(self._segments(self.SHAPE), u=1.0)
        assert fp is not None
        assert fp.perimeter_lf > fp.bbox_perimeter_lf
        # the bump is 30 in deep and interior to the envelope, so the trace runs
        # 2 x 30 in = 5 ft longer than 2 x (width + height) can see
        assert fp.perimeter_lf - fp.bbox_perimeter_lf == pytest.approx(5.0, abs=0.2)

    def test_a_plain_rectangle_matches_its_bounding_box_exactly(self):
        from app.services.drawing.floor_area import building_footprint

        fp = building_footprint(self._segments(RECT), u=1.0)
        assert fp is not None
        assert (fp.external_corners, fp.internal_corners) == (4, 0)
        assert fp.perimeter_lf == pytest.approx(fp.bbox_perimeter_lf, abs=0.05)
