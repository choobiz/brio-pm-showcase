"""Per-room interior areas from floor-plan line-work, by rasterisation.

The vector approach in ``floor_area`` measures the building *envelope* well and
cannot measure *rooms*. That is not a tuning failure — it was established by
measurement (see ``docs/cad-takeoff-engine.md`` §6.2): walls are drawn as two
parallel faces, a room is bounded by the inner faces, and feeding both faces into
one planar graph yields slivers rather than rooms. Widening the gap tolerance or
adding the interior-wall layer both made the result *worse*.

Rasterising sidesteps the whole problem. On a bitmap a wall is the solid barrier it
physically is, so a double line is just a thick wall and a sliver has no area. What
remains is sealing the door and window breaks, which morphological closing does
without moving any wall face.

Areas come out measured to the **inner** face. Architects dimension to the wall
**centreline**, so ``perimeter x half the wall thickness`` is added back. The wall
thickness is measured from the drawing's own double-line spacing — never assumed.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

Point = tuple[float, float]
Segment = tuple[Point, Point]

#: Closing radii tried, in inches. A correct answer is a *plateau* across several
#: radii, not the output of one lucky value — too small leaks through doorways,
#: too large swallows small rooms. Each radius seals gaps up to 2r, so the range
#: spans a 12 in cased opening to a 6 ft garage/patio opening. The top of the
#: sweep matters: on the Maple sheets the "plateau" was always the largest
#: radius tried, which is not a plateau at all — it is a truncated sweep, and
#: extending it showed the room count still moving.
DEFAULT_RADII_IN = (6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 24.0, 30.0, 36.0)

#: Rooms below this are storage nooks, pipe chases and rasterisation crumbs.
MIN_ROOM_SF = 10.0

#: No habitable space is narrower than this. The filter exists to kill *slivers* —
#: the long thin strips a leaking gap opens up between two wall faces, which pass an
#: area test easily (a 28 ft x 2 ft strip is 56 sf) but are not rooms.
MIN_ROOM_DIM_IN = 30.0


@dataclass
class Room:
    """One enclosed space. ``net_sf`` and ``centerline_sf`` measure the same space
    two different ways — report the one that matches how the trade prices it."""

    net_sf: float          # to the inner wall face — flooring, paint, finishes
    centerline_sf: float   # to the wall centreline — matches architects' labels
    perimeter_lf: float
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1 in drawing units
    centroid: Point


#: Radii tried when sealing the ENVELOPE, in inches. Exterior openings are far
#: wider than doors — a 6 ft patio door, a 16 ft garage door, a window wall — so
#: this band runs well past DEFAULT_RADII_IN. It only ever moves the outline; room
#: areas are measured against the interior walls inside it.
ENVELOPE_RADII_IN = (6.0, 12.0, 18.0, 24.0, 36.0, 48.0, 72.0, 96.0)

#: Widest gap the envelope may seal when the drawing declares no opening at all.
#: A doorway. Beyond this, sealing is inventing a wall: the sweep would happily
#: close a missing exterior wall and report a confident area for a building that
#: is not enclosed.
DEFAULT_MAX_OPENING_IN = 24.0


@dataclass
class RoomSet:
    rooms: list[Room]
    close_radius_in: float
    wall_thickness_in: float
    plateau_radii: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    envelope_sf: Optional[float] = None
    envelope_radius_in: Optional[float] = None

    @property
    def net_sf(self) -> float:
        return round(sum(r.net_sf for r in self.rooms), 1)

    @property
    def centerline_sf(self) -> float:
        return round(sum(r.centerline_sf for r in self.rooms), 1)


def measure_wall_thickness(segments: Sequence[Segment], unit_scale: float = 1.0,
                           max_in: float = 14.0, angle_tol: float = 3.0) -> Optional[float]:
    """Modal spacing between opposing parallel wall faces, in inches.

    Returns ``None`` when the line-work shows no consistent double-line pattern —
    single-line walls are a real convention, and guessing a thickness there would
    invent area. The caller then reports net only.
    """
    segs = []
    for p1, p2 in segments:
        length = math.dist(p1, p2)
        if length <= 1:
            continue
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180
        segs.append((p1, p2, ang, length))

    gaps: list[float] = []
    limit = max_in / unit_scale if unit_scale else max_in
    for i, (p1i, p2i, ai, li) in enumerate(segs):
        for p1j, p2j, aj, lj in segs[i + 1:]:
            d = abs(ai - aj)
            if min(d, 180 - d) > angle_tol:
                continue
            gap = _point_to_line(p1j, p1i, p2i)
            if gap is None or not (0.5 / unit_scale < gap <= limit):
                continue
            # Require real overlap along the shared direction, else two unrelated
            # walls that merely happen to be parallel get paired.
            if _projected_overlap(p1i, p2i, p1j, p2j) < min(li, lj) * 0.3:
                continue
            gaps.append(gap * unit_scale)

    if len(gaps) < 3:
        return None
    # Mode over 0.5 in buckets: real walls repeat one thickness, noise does not.
    # `round`, not `int` — a 6 in wall measured in millimetres comes back as
    # 5.999999999999999, and truncation would file half the pairs under 5.5 in.
    buckets: dict[int, list[float]] = {}
    for g in gaps:
        buckets.setdefault(round(g * 2), []).append(g)
    best = max(buckets.values(), key=len)
    if len(best) < 3:
        return None
    return round(sum(best) / len(best), 2)


def _point_to_line(p: Point, a: Point, b: Point) -> Optional[float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return None
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / n


def _projected_overlap(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    dx, dy = a2[0] - a1[0], a2[1] - a1[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return 0.0
    ux, uy = dx / n, dy / n

    def proj(p: Point) -> float:
        return (p[0] - a1[0]) * ux + (p[1] - a1[1]) * uy

    lo_a, hi_a = 0.0, n
    lo_b, hi_b = sorted((proj(b1), proj(b2)))
    return max(0.0, min(hi_a, hi_b) - max(lo_a, lo_b))


def _rasterize(segments: Sequence[Segment], scale: float,
               origin: Point, shape: tuple[int, int]) -> np.ndarray:
    """Burn the line-work into a boolean mask, one pixel wide.

    Sampling at half-pixel steps guarantees 4-connectivity along the line, so a
    flood fill cannot squeeze diagonally between two touching pixels and leak a
    room into the outdoors.
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    ox, oy = origin
    for (ax, ay), (bx, by) in segments:
        x0, y0 = (ax - ox) * scale, (ay - oy) * scale
        x1, y1 = (bx - ox) * scale, (by - oy) * scale
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
        xs = np.rint(np.linspace(x0, x1, steps)).astype(int)
        ys = np.rint(np.linspace(y0, y1, steps)).astype(int)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        mask[ys[ok], xs[ok]] = True
    return mask


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Dilation by a (2r+1) square, done separably — two 1-D passes, not r^2."""
    if r <= 0:
        return mask
    out = mask
    for axis in (0, 1):
        acc = out.copy()
        for shift in range(1, r + 1):
            acc |= np.roll(out, shift, axis=axis)
            acc |= np.roll(out, -shift, axis=axis)
        out = acc
    return out


def _close(mask: np.ndarray, r: int) -> np.ndarray:
    """Morphological closing: seals every gap up to 2r without moving a wall face.

    Padding by r keeps ``np.roll`` from wrapping the plan around the image edge and
    welding the top wall to the bottom one.
    """
    if r <= 0:
        return mask
    pad = np.pad(mask, r, constant_values=False)
    grown = _dilate(pad, r)
    shrunk = ~_dilate(~grown, r)          # erosion is dilation of the complement
    return shrunk[r:-r, r:-r]


def building_mask(walls: np.ndarray, r: int) -> np.ndarray:
    """Everything the building encloses — interior AND walls — as a filled region.

    Vector face-tracing cannot do this on a real plan: it needs the exterior wall
    graph to close, and exterior walls are broken at every window and door. On the
    Maple sheets the tracer returned a 46 sf duct chase as the "building".

    Closing the wall mask by a radius wide enough to span the largest exterior
    opening, then filling everything unreachable from the image border, gives the
    same answer without needing anything to close. The radius here is deliberately
    large; it moves only the outline, never a room, because rooms are measured
    against the *unclosed* interior walls inside this mask.
    """
    sealed = _close(walls, r)
    labels, count = _components(~sealed)
    if count == 0:
        return ~sealed
    border = set(np.unique(labels[0, :])) | set(np.unique(labels[-1, :])) \
        | set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    border.discard(0)
    return ~np.isin(labels, list(border)) if border else np.ones_like(sealed)


def _components(free: np.ndarray) -> tuple[np.ndarray, int]:
    """Label 4-connected regions of ``free``, working on ROW RUNS not pixels.

    A pixel-by-pixel flood fill in Python is O(10^6) per call and this runs once
    per radius, seventeen times per view — minutes per sheet. A floor plan has a
    few thousand horizontal runs, so unioning overlapping runs between adjacent
    rows is three orders of magnitude less work for exactly the same answer.
    """
    h, w = free.shape
    runs_per_row: list[list[tuple[int, int, int]]] = []   # (x0, x1_exclusive, id)
    parent: list[int] = [0]                               # index 0 = background

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for y in range(h):
        row = free[y]
        if not row.any():
            runs_per_row.append([])
            continue
        # Run boundaries from the diff of the boolean row — no Python loop over x.
        d = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        runs = []
        for x0, x1 in zip(starts.tolist(), ends.tolist()):
            parent.append(len(parent))
            runs.append((x0, x1, len(parent) - 1))
        runs_per_row.append(runs)
        if y == 0:
            continue
        prev = runs_per_row[y - 1]
        i = j = 0
        while i < len(prev) and j < len(runs):
            a, b = prev[i], runs[j]
            if a[1] > b[0] and b[1] > a[0]:        # runs overlap in x
                union(a[2], b[2])
            if a[1] <= b[1]:
                i += 1
            else:
                j += 1

    labels = np.zeros((h, w), dtype=np.int32)
    remap: dict[int, int] = {}
    for y, runs in enumerate(runs_per_row):
        for x0, x1, rid in runs:
            root = find(rid)
            lab = remap.get(root)
            if lab is None:
                lab = len(remap) + 1
                remap[root] = lab
            labels[y, x0:x1] = lab
    return labels, len(remap)


def _label(mask: np.ndarray) -> list[np.ndarray]:
    """Enclosed empty regions, as boolean masks. Anything reachable from the border
    is outdoors and is dropped."""
    labels, count = _components(~mask)
    if count == 0:
        return []
    border = set(np.unique(labels[0, :])) | set(np.unique(labels[-1, :])) \
        | set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    border.discard(0)
    return [labels == i for i in range(1, count + 1) if i not in border]


def _perimeter_px(region: np.ndarray) -> int:
    p = np.pad(region, 1, constant_values=False)
    edges = 0
    for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
        edges += int((p & ~np.roll(p, shift, axis=axis)).sum())
    return edges


def room_areas(segments: Sequence[Segment], unit_scale: float = 1.0,
               wall_thickness_in: Optional[float] = None,
               radii_in: Iterable[float] = DEFAULT_RADII_IN,
               px_per_in: float = 1.0,
               min_room_sf: float = MIN_ROOM_SF,
               boundary: Optional[Sequence[Point]] = None,
               envelope_segments: Optional[Sequence[Segment]] = None,
               max_opening_in: Optional[float] = None) -> Optional[RoomSet]:
    """Measure every enclosed room in a floor plan.

    ``unit_scale`` converts drawing units to inches (see ``cad_extractor``).
    ``wall_thickness_in`` is measured from the line-work when not supplied; if it
    cannot be measured, only ``net_sf`` is meaningful and a warning says so.

    ``boundary`` is the building outline polygon, if one could be traced. Supplying
    it is what makes a multi-room plan converge. A plan has TWO gap scales that a
    single closing radius cannot serve at once: exterior openings (patio doors and
    window walls — 6 ft on the Maple basement) and interior doorways (30-36 in).
    Sized for the exterior, the radius swallows every room narrower than 6 ft; sized
    for the interior, the envelope leaks and the whole floor drains outdoors as one
    region. Measured on Maple 109: a single 969 sf void at every radius up to
    16 in, and a second view that returned nothing at all below 20 in.

    Drawing the outline as a closed boundary removes the exterior scale from the
    problem, leaving the radius free to do the one job it is good at — bridging
    interior doorways.

    Returns ``None`` when no room can be closed. A plan that will not close should
    say so — the plausible fallback, a bounding box, is wrong by 50% on an L-shape.
    """
    segs = [s for s in segments if math.dist(*s) > 0]
    if len(segs) < 4:
        return None

    if wall_thickness_in is None:
        wall_thickness_in = measure_wall_thickness(segs, unit_scale)
    warnings: list[str] = []
    if not wall_thickness_in:
        warnings.append(
            "No consistent double-line wall thickness found — areas are to the "
            "inner face only, and will read low against centreline-dimensioned labels."
        )
        wall_thickness_in = 0.0

    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    # Drop resolution on a big sheet rather than spend minutes on it. 1 px/in on a
    # 92 x 76 ft plan is a million pixels, and every radius in both sweeps pays for
    # it. Half-inch fidelity is far below the accuracy the wall line-work supports.
    span_in = max(max(xs) - min(xs), max(ys) - min(ys)) * unit_scale
    while px_per_in > 0.125 and span_in * px_per_in > 1400:
        px_per_in /= 2.0
    scale = px_per_in * unit_scale       # drawing units -> pixels
    radii = sorted(radii_in)
    pad_in = max(radii) + 4.0
    pad_du = pad_in / unit_scale if unit_scale else pad_in
    origin = (min(xs) - pad_du, min(ys) - pad_du)
    w = int((max(xs) - min(xs) + 2 * pad_du) * scale) + 1
    h = int((max(ys) - min(ys) + 2 * pad_du) * scale) + 1
    if w * h > 40_000_000:
        return None                      # a plan this large is a scale error
    base = _rasterize(segs, scale, origin, (h, w))
    if boundary and len(boundary) >= 3:
        ring = list(boundary) + [list(boundary)[0]]
        base |= _rasterize([(ring[i], ring[i + 1]) for i in range(len(ring) - 1)],
                           scale, origin, (h, w))

    env_segs = list(envelope_segments) if envelope_segments else segs
    inside_mask, env_r, env_sf = _envelope(
        _rasterize(env_segs, scale, origin, (h, w)), px_per_in,
        max_opening_in=max(max_opening_in or 0.0, DEFAULT_MAX_OPENING_IN))
    if inside_mask is None:
        warnings.append("Could not seal the building envelope — rooms measured "
                        "without an outline, so any leak drains a room outdoors.")

    # Sweep the closing radius and keep the plateau — the band of radii that agree.
    # A single radius that happens to look right is not evidence; agreement is.
    results: dict[float, list[Room]] = {}
    for r_in in radii:
        closed = _close(base, int(round(r_in * px_per_in)))
        if inside_mask is not None:
            # Rooms live inside the envelope. Bounding them here is what lets the
            # radius stay small enough to keep a closet while still bridging a door.
            closed = closed | ~inside_mask
        regions = _label(closed)
        rooms = []
        for region in regions:
            per_px = _perimeter_px(region)
            # The rasterised wall line sits ON the inner face, and its pixel is
            # counted as wall — so the free region is half a pixel short all the way
            # round. Add it back, or every room reads low by perimeter/2 px.
            cells = region.sum() + 0.5 * per_px
            net = float(cells) / (px_per_in ** 2) / 144.0
            per_in = per_px / px_per_in
            ry, rx = np.nonzero(region)
            if net < min_room_sf:
                continue
            if (min(rx.max() - rx.min(), ry.max() - ry.min()) / px_per_in
                    < MIN_ROOM_DIM_IN):
                continue
            rooms.append(Room(
                net_sf=round(net, 1),
                centerline_sf=round(net + per_in * wall_thickness_in / 2 / 144.0, 1),
                perimeter_lf=round(per_in / 12.0, 1),
                bbox=(round(origin[0] + rx.min() / scale, 1),
                      round(origin[1] + ry.min() / scale, 1),
                      round(origin[0] + rx.max() / scale, 1),
                      round(origin[1] + ry.max() / scale, 1)),
                centroid=(round(origin[0] + rx.mean() / scale, 1),
                          round(origin[1] + ry.mean() / scale, 1)),
            ))
        results[r_in] = sorted(rooms, key=lambda x: -x.net_sf)

    plateau = _longest_plateau(results, radii)
    if not plateau:
        return None
    chosen = plateau[len(plateau) // 2]
    if len(plateau) < 2:
        warnings.append(
            f"Room areas are stable at only one closing radius ({chosen:g} in) — "
            "the result is sensitive to the opening size and is NOT converged. "
            "Verify against the drawing before pricing."
        )
    elif plateau[-1] == radii[-1]:
        warnings.append(
            f"The stable band runs to the widest radius tried ({radii[-1]:g} in), so "
            "the true plateau may be wider still. Treat the room split as provisional."
        )
    return RoomSet(rooms=results[chosen], close_radius_in=chosen,
                   wall_thickness_in=wall_thickness_in,
                   plateau_radii=plateau, warnings=warnings,
                   envelope_sf=env_sf, envelope_radius_in=env_r)


def _envelope(walls: np.ndarray, px_per_in: float,
              max_opening_in: float = DEFAULT_MAX_OPENING_IN,
              radii_in: Iterable[float] = ENVELOPE_RADII_IN,
              min_share: float = 0.5, settle: float = 1.15):
    """Seal the envelope at the smallest radius that is BOTH big and settled.

    Filled area grows monotonically with the radius, in two regimes. Below the
    widest exterior opening the outline leaks, the fill escapes outdoors, and the
    area is a fraction of the building. Once the last opening is sealed the area
    steps up and then only creeps, bulging a few percent at a time over a porch or
    a deck. The answer is the start of the second regime.

    Neither half of the test is sufficient alone, and both single-signal rules were
    tried and measured against the architect's own figures on seven views:

      * "first radius that agrees with its successor" cannot tell a sealed envelope
        from a leak that happens to be stable — Maple 109 view 1 leaks at a flat
        125 sf across three radii before sealing at 1149.
      * "radius after the largest step" picks the bulge whenever the envelope was
        already sealed at the smallest radius tried — Maple 107 ran to 72 in and
        returned an outline 51% larger than the building.

    Requiring the area to be at least half the largest seen AND to have stopped
    growing fast picks the correct radius on all seven.

    The sweep is capped by ``max_opening_in`` — the widest opening the DRAWING
    ITSELF declares, read from its door and window blocks. Sealing a gap wider than
    any opening the architect drew is not sealing, it is inventing a wall, and it
    would hand back a confident area for a building that is not enclosed.
    """
    radii = [r for r in sorted(radii_in) if r <= max_opening_in]
    if not radii:
        radii = [min(radii_in)] if radii_in else []
    seq: list[tuple[float, np.ndarray, float]] = []
    for r_in in radii:
        mask = building_mask(walls, int(round(r_in * px_per_in)))
        seq.append((r_in, mask, float(mask.sum()) / (px_per_in ** 2) / 144.0))
    if not seq:
        return None, None, None
    peak = max(a for _r, _m, a in seq)
    if peak <= 0:
        return None, None, None
    for i, (r_in, mask, area) in enumerate(seq):
        if area < min_share * peak:
            continue
        nxt = seq[i + 1][2] if i + 1 < len(seq) else area
        if area > 0 and nxt / area < settle:
            return mask, r_in, round(area, 1)
    # Big but still climbing at the widest radius tried: the sweep is truncated.
    return None, None, None


def _longest_plateau(results: dict[float, list[Room]], radii: list[float],
                     tol: float = 0.02) -> list[float]:
    """Pick a run of adjacent radii whose room areas agree within ``tol``.

    Ranked by **room count first**, plateau length second. Room count leads because
    the two failure modes are not symmetric: too small a radius leaks through a
    doorway and *merges* two rooms into one, while too large only removes a small
    one. A leaky radius therefore reports fewer rooms — and a leak can be stable
    across several radii, so longest-run alone would happily settle on it.
    """
    runs: list[list[float]] = []
    for r in radii:
        rooms = results[r]
        if not rooms:
            continue
        if runs and runs[-1][-1] == _prev(radii, r) and \
                _agrees(results[runs[-1][-1]], rooms, tol):
            runs[-1].append(r)
        else:
            runs.append([r])
    if not runs:
        return []
    return max(runs, key=lambda run: (len(results[run[0]]), len(run), -run[0]))


def _prev(radii: list[float], r: float) -> Optional[float]:
    i = radii.index(r)
    return radii[i - 1] if i else None


def _agrees(a: list[Room], b: list[Room], tol: float) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(x.net_sf - y.net_sf) <= tol * max(x.net_sf, 1.0) for x, y in zip(a, b))
