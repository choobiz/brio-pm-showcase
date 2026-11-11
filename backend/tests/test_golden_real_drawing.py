"""Golden regression against line-work from a real production drawing.

Every other fixture in this directory is a synthetic axis-aligned rectangle with
unbroken walls. On that shape the corner invariant holds trivially, the bounding
box equals the traced perimeter, and articulation is zero — so none of the three
faults found on 1234 Example could be observed. This file carries the actual
line-work the engine runs on, so a change in what the engine measures shows up
as a failing test rather than as a wrong number on a purchase order.

⚠️ These are SNAPSHOTS, not verified ground truth. They record what the engine
measures today. If a change moves them, that is not automatically a bug — but it
must be explained and the numbers updated deliberately, never by re-running until
green. Where a figure is known to disagree with the architect, that is stated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.drawing.floor_area import building_footprint

FIXTURE = Path(__file__).parent / "fixtures" / "sample_garage_linework.json"


@pytest.fixture(scope="module")
def linework():
    return json.loads(FIXTURE.read_text())


def _segments(data, *layers):
    return [((s[0], s[1]), (s[2], s[3]))
            for lay in layers for s in data.get(lay, [])]


class TestTheFixtureItself:
    def test_it_carries_real_fragmented_linework(self, linework):
        """The point of this fixture is the awkwardness, so assert it is there."""
        assert len(linework["A-WALL"]) == 63
        assert len(linework["A-FLOR"]) == 43

    def test_not_one_segment_is_axis_aligned_to_the_file(self, linework):
        """Drawn on a skewed lot. Squareness must be judged against the
        building's own axis, which is why a naive orthogonality test would
        reject this real plan outright."""
        aligned = sum(1 for s in linework["A-WALL"]
                      if abs(s[0] - s[2]) < 1e-6 or abs(s[1] - s[3]) < 1e-6)
        assert aligned == 0


class TestFloorOutlineGolden:
    """The A-FLOR outline is what the engine actually closes on this drawing."""

    def test_measured_figures_have_not_moved(self, linework):
        fp = building_footprint(_segments(linework, "A-FLOR", "A-AREA"), u=1.0)
        assert fp is not None
        assert fp.area_sf == pytest.approx(783.0, abs=0.5)
        assert fp.perimeter_lf == pytest.approx(156.9, abs=0.2)
        assert fp.bbox_perimeter_lf == pytest.approx(136.3, abs=0.2)

    def test_the_outline_is_articulated_not_a_box(self, linework):
        """The regression that matters, and now verified ground truth.

        A 4-corner answer here means the tracer has fallen back to a simplified
        outline again. 12 external and 8 internal was confirmed by hand count on
        2026-10-02, so this is no longer only a snapshot.
        """
        fp = building_footprint(_segments(linework, "A-FLOR", "A-AREA"), u=1.0)
        assert (fp.external_corners, fp.internal_corners) == (12, 8)
        assert fp.external_corners + fp.internal_corners > 4

    def test_the_rectilinear_invariant_holds_on_real_geometry(self, linework):
        fp = building_footprint(_segments(linework, "A-FLOR", "A-AREA"), u=1.0)
        assert fp.rectilinear, "a rotated plan must still read as square"
        assert fp.corner_invariant_ok
        assert fp.external_corners - fp.internal_corners == 4

    def test_the_bounding_box_shortcut_is_short_on_this_building(self, linework):
        """20.6 lf of real wall that 2 x (width + height) cannot see. This is the
        error that under-measured the cladding perimeter."""
        fp = building_footprint(_segments(linework, "A-FLOR", "A-AREA"), u=1.0)
        assert fp.perimeter_lf - fp.bbox_perimeter_lf == pytest.approx(20.6, abs=0.3)


class TestKnownDisagreements:
    """Recorded so they are not rediscovered as surprises."""

    def test_traced_area_runs_over_the_architects_figure(self, linework):
        """Architect: 399.6 sf garage + 268 sf office = 668 sf; the gross areas
        on the same sheet total 817 sf. The A-FLOR trace gives 783 sf, between
        the two, because it follows the slab edge rather than the wall line.

        The AREA disagreement is real and unresolved. The CORNER COUNT is not:
        Amir counted the building by hand on 2026-10-02 and got 12 external and
        8 internal, exactly what this trace reports. An earlier reading of the
        site plan gave 8 and 4 and was wrong — the plan is at 3/16 in = 1 ft and
        several jogs sit inside the line weight at that scale, while the DXF
        carries them exactly. Where a hand count and this trace disagree on
        corners, check the DXF before assuming the trace is over-reading.
        """
        fp = building_footprint(_segments(linework, "A-FLOR", "A-AREA"), u=1.0)
        assert 668 < fp.area_sf < 817

    def test_the_wall_layer_alone_still_does_not_close(self, linework):
        """A-WALL is drawn as paired faces, two lines per wall, so there is no
        single loop to trace — cross-component bridging does not help here. The
        outer-face extraction needed to close double-line walls is not built.
        """
        fp = building_footprint(_segments(linework, "A-WALL"), u=1.0)
        assert fp is None
