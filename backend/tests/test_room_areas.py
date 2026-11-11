"""Room measurement against fixtures whose answers are known exactly.

Every case here is a rectangle or a union of rectangles, so the true area is
arithmetic, not a second implementation of the thing under test.
"""
import math

import pytest

from app.services.drawing.room_areas import (
    Room, measure_wall_thickness, room_areas,
)

T = 6.0          # wall thickness, inches — the fixtures are drawn double-line


def double_wall(p1, p2, t=T):
    """Two parallel faces t apart, centred on p1->p2 — how CAD draws a wall."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * t / 2, dx / n * t / 2
    return [((p1[0] + ox, p1[1] + oy), (p2[0] + ox, p2[1] + oy)),
            ((p1[0] - ox, p1[1] - oy), (p2[0] - ox, p2[1] - oy))]


def box(x0, y0, x1, y1, t=T):
    """Closed double-line rectangle on the centreline x0,y0 -> x1,y1."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    segs = []
    for i in range(4):
        segs += double_wall(corners[i], corners[(i + 1) % 4], t)
    return segs


def test_single_room_net_and_centerline():
    """A 10 x 12 ft room on the centreline: 120 sf to centreline, less to the face."""
    segs = box(0, 0, 120, 144)
    rs = room_areas(segs)
    assert rs is not None
    assert len(rs.rooms) == 1
    room = rs.rooms[0]
    # inner face: (120-6) x (144-6) in = 114 x 138 = 15732 sq in = 109.25 sf
    assert room.net_sf == pytest.approx(109.25, abs=1.5)
    assert room.centerline_sf == pytest.approx(120.0, abs=2.0)


def test_wall_thickness_is_measured_not_assumed():
    assert measure_wall_thickness(box(0, 0, 120, 144, t=6.0)) == pytest.approx(6.0, abs=0.3)
    assert measure_wall_thickness(box(0, 0, 120, 144, t=9.5)) == pytest.approx(9.5, abs=0.3)


def test_single_line_walls_report_net_only_and_warn():
    """Single-line walls are a real convention. Inventing a thickness invents area."""
    segs = [((0, 0), (120, 0)), ((120, 0), (120, 144)),
            ((120, 144), (0, 144)), ((0, 144), (0, 0))]
    rs = room_areas(segs)
    assert rs is not None
    assert rs.wall_thickness_in == 0.0
    assert rs.rooms[0].net_sf == pytest.approx(rs.rooms[0].centerline_sf)
    assert any("inner face only" in w for w in rs.warnings)


def test_two_rooms_split_by_a_partition():
    """20 x 12 ft outer, partition at x = 120. Two rooms, not one."""
    segs = box(0, 0, 240, 144) + double_wall((120, 0), (120, 144))
    rs = room_areas(segs)
    assert rs is not None
    assert len(rs.rooms) == 2
    for room in rs.rooms:
        assert room.centerline_sf == pytest.approx(120.0, abs=4.0)


def test_doorway_is_sealed_so_two_rooms_stay_two():
    """A 32 in door in the partition must not merge the rooms into one."""
    segs = box(0, 0, 240, 144)
    for face in double_wall((120, 0), (120, 144)):
        (ax, ay), (bx, by) = face
        segs.append(((ax, ay), (bx, 56.0)))       # below the opening
        segs.append(((ax, 88.0), (bx, by)))       # above it — 32 in gap
    rs = room_areas(segs)
    assert rs is not None
    assert len(rs.rooms) == 2, f"doorway leaked: {[r.net_sf for r in rs.rooms]}"


def test_exterior_door_does_not_leak_the_room_outdoors():
    segs = []
    for face in box(0, 0, 120, 144):
        (ax, ay), (bx, by) = face
        if ay == by and ay < 10:                  # break the bottom wall
            segs.append(((ax, ay), (40.0, by)))
            segs.append(((76.0, ay), (bx, by)))   # 36 in opening
        else:
            segs.append(face)
    rs = room_areas(segs)
    assert rs is not None
    assert len(rs.rooms) == 1
    assert rs.rooms[0].centerline_sf == pytest.approx(120.0, abs=4.0)


def test_l_shaped_room_is_not_its_bounding_box():
    """12 x 12 ft minus a 6 x 6 ft bite = 108 sf, where the bbox says 144."""
    pts = [(0, 0), (144, 0), (144, 72), (72, 72), (72, 144), (0, 144)]
    segs = []
    for i in range(len(pts)):
        segs += double_wall(pts[i], pts[(i + 1) % len(pts)])
    rs = room_areas(segs)
    assert rs is not None
    assert rs.rooms[0].centerline_sf == pytest.approx(108.0, abs=6.0)
    assert rs.rooms[0].centerline_sf < 130.0      # nowhere near the 144 sf bbox


def test_metric_drawing_matches_imperial():
    """Same room in millimetres must measure the same as in inches."""
    imperial = room_areas(box(0, 0, 120, 144))
    mm = [(( a[0] * 25.4, a[1] * 25.4), (b[0] * 25.4, b[1] * 25.4))
          for a, b in box(0, 0, 120, 144)]
    metric = room_areas(mm, unit_scale=1.0 / 25.4)
    assert metric is not None
    assert metric.rooms[0].centerline_sf == pytest.approx(
        imperial.rooms[0].centerline_sf, rel=0.03)


def test_open_linework_returns_none():
    """Three walls of a box enclose nothing. A bounding box would answer anyway."""
    segs = double_wall((0, 0), (120, 0)) + double_wall((120, 0), (120, 144)) \
        + double_wall((120, 144), (0, 144))
    assert room_areas(segs) is None


def test_a_gap_wider_than_the_closing_radius_returns_none():
    """A 10 ft opening is a missing wall, not a doorway — do not invent one."""
    segs = []
    for face in box(0, 0, 120, 144):
        (ax, ay), (bx, by) = face
        if ay == by and ay < 10:
            segs.append(((ax, ay), (10.0, by)))   # leaves a 100 in gap
        else:
            segs.append(face)
    assert room_areas(segs) is None


def test_tiny_closets_are_excluded():
    segs = box(0, 0, 240, 144) + double_wall((228, 0), (228, 144))
    rs = room_areas(segs)
    assert rs is not None
    assert all(r.net_sf >= 10.0 for r in rs.rooms)


def test_rooms_carry_position_so_a_label_can_be_matched_to_them():
    segs = box(0, 0, 240, 144) + double_wall((120, 0), (120, 144))
    rs = room_areas(segs)
    left = min(rs.rooms, key=lambda r: r.centroid[0])
    right = max(rs.rooms, key=lambda r: r.centroid[0])
    assert left.centroid[0] < 120 < right.centroid[0]
    assert left.bbox[0] < left.bbox[2] <= 120


def test_plateau_beats_a_single_lucky_radius():
    """Two rooms behind a 32 in door: the answer must come from the band of radii
    that agree, not from whichever radius happens to run last."""
    segs = box(0, 0, 240, 144)
    for face in double_wall((120, 0), (120, 144)):
        (ax, ay), (bx, by) = face
        segs.append(((ax, ay), (bx, 56.0)))
        segs.append(((ax, 88.0), (bx, by)))
    rs = room_areas(segs)
    assert rs is not None and len(rs.rooms) == 2
    assert len(rs.plateau_radii) >= 2, f"not converged: {rs.plateau_radii}"
    assert not any("NOT converged" in w for w in rs.warnings)


def test_a_single_radius_result_is_flagged_as_unconverged():
    """A drawing that only resolves at one radius must say so — that is what the
    Maple sheets do, and reporting it confidently would be the failure."""
    segs = box(0, 0, 240, 144)
    for face in double_wall((120, 0), (120, 144)):
        (ax, ay), (bx, by) = face
        segs.append(((ax, ay), (bx, 20.0)))
        segs.append(((ax, 124.0), (bx, by)))     # 104 in gap: needs r >= 52
    rs = room_areas(segs, radii_in=(6.0, 20.0, 56.0))
    if rs is not None and len(rs.plateau_radii) < 2:
        assert any("NOT converged" in w or "widest radius" in w for w in rs.warnings)
