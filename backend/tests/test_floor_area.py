"""Falsifiable tests for planar footprint extraction.

Each case has an exact known area, including the failure cases — a method that
only ever returns an answer is useless, so the "should return None" tests matter
as much as the positive ones.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.drawing.floor_area import (  # noqa: E402
    bridge_openings, building_footprint, extract_faces, snap_segments,
)

IN = 1.0  # fixtures are authored in inches


def _rect(w, h, x0=0.0, y0=0.0):
    return [((x0, y0), (x0 + w, y0)), ((x0 + w, y0), (x0 + w, y0 + h)),
            ((x0 + w, y0 + h), (x0, y0 + h)), ((x0, y0 + h), (x0, y0))]


def test_closed_rectangle():
    # 120 in x 120 in = 100 sf, perimeter 40 lf
    fp = building_footprint(_rect(120, 120), IN)
    assert fp is not None
    assert abs(fp.area_sf - 100.0) < 0.5, fp
    assert abs(fp.perimeter_lf - 40.0) < 0.2, fp


def test_doorway_gap_is_bridged():
    """A wall broken by a 36 in doorway must still enclose."""
    segs = [((0, 0), (42, 0)), ((78, 0), (120, 0)),          # 36 in gap
            ((120, 0), (120, 120)), ((120, 120), (0, 120)), ((0, 120), (0, 0))]
    fp = building_footprint(segs, IN)
    assert fp is not None, "doorway gap not bridged"
    assert fp.bridged_gaps == 1, f"expected 1 bridge, got {fp.bridged_gaps}"
    assert abs(fp.area_sf - 100.0) < 0.5, fp


def test_endpoints_within_tolerance_are_snapped():
    """CAD stores 'touching' walls fractions of an inch apart."""
    segs = [((0, 0), (120, 0.0)), ((120.4, 0.3), (120, 120)),
            ((120, 120), (0.2, 119.8)), ((0, 120), (0, 0))]
    fp = building_footprint(segs, IN)
    assert fp is not None, "near-coincident endpoints not snapped"
    assert abs(fp.area_sf - 100.0) < 1.0, fp


def test_l_shaped_plan():
    """Non-rectangular: bbox would say 100 sf, truth is 75 sf."""
    segs = [((0, 0), (120, 0)), ((120, 0), (120, 60)), ((120, 60), (60, 60)),
            ((60, 60), (60, 120)), ((60, 120), (0, 120)), ((0, 120), (0, 0))]
    fp = building_footprint(segs, IN)
    assert fp is not None
    assert abs(fp.area_sf - 75.0) < 0.5, f"L-plan {fp.area_sf} sf != 75.0"


def test_open_linework_returns_none_not_a_guess():
    """Three sides of a rectangle with a 40 ft hole cannot close — must decline."""
    segs = [((0, 0), (480, 0)), ((480, 0), (480, 480))]
    assert building_footprint(segs, IN) is None


def test_gap_wider_than_max_is_not_bridged():
    segs = [((0, 0), (20, 0)), ((100, 0), (120, 0)),          # 80 in gap > 60 in
            ((120, 0), (120, 120)), ((120, 120), (0, 120)), ((0, 120), (0, 0))]
    assert building_footprint(segs, IN) is None


def test_metric_matches_imperial():
    """Same building in millimetres must give the same square feet."""
    mm = 25.4
    segs = [((a[0] * mm, a[1] * mm), (b[0] * mm, b[1] * mm)) for a, b in _rect(120, 120)]
    fp = building_footprint(segs, u=1.0 / mm)
    assert fp is not None
    assert abs(fp.area_sf - 100.0) < 0.5, fp


def test_tiny_regions_are_rejected():
    """A 6 in x 6 in artifact must not be reported as a footprint."""
    assert building_footprint(_rect(6, 6), IN) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                print(f"  FAIL  {name}: {e}")
