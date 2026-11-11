"""Collapse double-line walls to centrelines, then repair the graph.

Rasterising the wall FACES makes every wall two curves with a void between them,
and leaves the drafting artefacts intact: a partition drawn to the face of the
wall it meets rather than through it, a run of wall broken into three collinear
pieces, a doorway. Morphological closing can paper over all of that, but only with
a radius wide enough to also swallow small rooms — the two-gap-scale problem in
``room_areas``.

A centreline graph attacks the same artefacts directly and locally:

  * a wall becomes ONE line, so there is no void between faces to leak through;
  * collinear pieces separated by less than a door width merge into one run, which
    is what a doorway is;
  * an endpoint that stops just short of another wall is extended onto it, which is
    what a T-junction is.

None of those operations has a global radius, so none of them can eat a closet.

Areas measured against centrelines are centreline areas — which is the convention
architects dimension in, so no half-wall correction is needed afterwards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

Point = tuple[float, float]
Segment = tuple[Point, Point]

#: A pair of faces further apart than this is two walls, not one wall's two sides.
MAX_WALL_THICKNESS_IN = 16.0
#: Faces must be parallel to this tolerance to be read as one wall.
ANGLE_TOL_DEG = 3.0
#: Collinear runs closer than this merge. A doorway; not an open-plan span.
MAX_MERGE_GAP_IN = 60.0
#: How far off a shared line two runs may sit and still be called collinear.
COLLINEAR_OFFSET_IN = 2.0
#: An endpoint this close to another wall is meant to touch it.
JUNCTION_SNAP_IN = 18.0


@dataclass
class Centerline:
    p1: Point
    p2: Point
    thickness: Optional[float]      # inches, when the two faces were found
    paired: bool

    @property
    def length(self) -> float:
        return math.dist(self.p1, self.p2)

    @property
    def angle(self) -> float:
        return math.degrees(math.atan2(self.p2[1] - self.p1[1],
                                       self.p2[0] - self.p1[0])) % 180.0

    def as_segment(self) -> Segment:
        return (self.p1, self.p2)


def _unit(a: Point, b: Point) -> tuple[float, float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n, n) if n else (0.0, 0.0, 0.0)


def _angle_close(a: float, b: float, tol: float) -> bool:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d) <= tol


def _perp_distance(p: Point, a: Point, b: Point) -> Optional[float]:
    ux, uy, n = _unit(a, b)
    if not n:
        return None
    return abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux)


def _overlap(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    ux, uy, n = _unit(a1, a2)
    if not n:
        return 0.0
    tb = sorted(((b1[0] - a1[0]) * ux + (b1[1] - a1[1]) * uy,
                 (b2[0] - a1[0]) * ux + (b2[1] - a1[1]) * uy))
    return max(0.0, min(n, tb[1]) - max(0.0, tb[0]))


def pair_faces(segments: Sequence[Segment], unit_scale: float = 1.0,
               max_thickness_in: float = MAX_WALL_THICKNESS_IN,
               angle_tol: float = ANGLE_TOL_DEG) -> list[Centerline]:
    """Pair opposing parallel faces into centrelines; keep the rest as they are.

    Greedy by overlap: the longest shared run wins, which stops a short stub from
    claiming a partner that belongs to the long wall running past it.
    """
    segs = [(a, b) for a, b in segments if math.dist(a, b) * unit_scale > 1.0]
    max_t = max_thickness_in / unit_scale if unit_scale else max_thickness_in
    min_t = 0.25 / unit_scale if unit_scale else 0.25

    cands: list[tuple[float, int, int, float]] = []
    for i, (a1, a2) in enumerate(segs):
        ai = math.degrees(math.atan2(a2[1] - a1[1], a2[0] - a1[0])) % 180.0
        for j in range(i + 1, len(segs)):
            b1, b2 = segs[j]
            bj = math.degrees(math.atan2(b2[1] - b1[1], b2[0] - b1[0])) % 180.0
            if not _angle_close(ai, bj, angle_tol):
                continue
            gap = _perp_distance(b1, a1, a2)
            if gap is None or not (min_t < gap <= max_t):
                continue
            ov = _overlap(a1, a2, b1, b2)
            if ov < 0.5 * min(math.dist(a1, a2), math.dist(b1, b2)):
                continue
            cands.append((ov, i, j, gap))

    cands.sort(reverse=True)
    used = [False] * len(segs)
    out: list[Centerline] = []
    for _ov, i, j, gap in cands:
        if used[i] or used[j]:
            continue
        used[i] = used[j] = True
        (a1, a2), (b1, b2) = segs[i], segs[j]
        ux, uy, n = _unit(a1, a2)
        # Project both faces onto the shared direction and take the common run.
        ts = [0.0, n,
              (b1[0] - a1[0]) * ux + (b1[1] - a1[1]) * uy,
              (b2[0] - a1[0]) * ux + (b2[1] - a1[1]) * uy]
        lo, hi = max(min(ts[0], ts[1]), min(ts[2], ts[3])), \
            min(max(ts[0], ts[1]), max(ts[2], ts[3]))
        if hi - lo <= 0:
            continue
        # Midline: halfway between the two faces, along the common run.
        off = _perp_distance(b1, a1, a2) or 0.0
        side = 1.0 if ((b1[0] - a1[0]) * uy - (b1[1] - a1[1]) * ux) > 0 else -1.0
        mx, my = uy * side * off / 2, -ux * side * off / 2
        out.append(Centerline(
            p1=(a1[0] + ux * lo + mx, a1[1] + uy * lo + my),
            p2=(a1[0] + ux * hi + mx, a1[1] + uy * hi + my),
            thickness=round(gap * unit_scale, 2), paired=True))
    for k, (a, b) in enumerate(segs):
        if not used[k]:
            out.append(Centerline(p1=a, p2=b, thickness=None, paired=False))
    return out


def merge_collinear(lines: Sequence[Centerline], unit_scale: float = 1.0,
                    max_gap_in: float = MAX_MERGE_GAP_IN,
                    offset_tol_in: float = COLLINEAR_OFFSET_IN,
                    angle_tol: float = ANGLE_TOL_DEG) -> list[Centerline]:
    """Join collinear runs separated by less than a door width.

    This is the step that spans a doorway without a global radius: the two pieces
    of wall either side of a door are collinear and close, while two walls facing
    each other across a room are not collinear at all.
    """
    gap = max_gap_in / unit_scale if unit_scale else max_gap_in
    tol = offset_tol_in / unit_scale if unit_scale else offset_tol_in

    buckets: dict[tuple[int, int], list[Centerline]] = {}
    for cl in lines:
        if cl.length == 0:
            continue
        th = math.radians(cl.angle)
        c = -cl.p1[0] * math.sin(th) + cl.p1[1] * math.cos(th)
        buckets.setdefault((round(cl.angle / angle_tol), round(c / tol)), []).append(cl)

    out: list[Centerline] = []
    for group in buckets.values():
        ref = group[0]
        ux, uy, _n = _unit(ref.p1, ref.p2)
        spans = []
        for cl in group:
            t1 = (cl.p1[0] - ref.p1[0]) * ux + (cl.p1[1] - ref.p1[1]) * uy
            t2 = (cl.p2[0] - ref.p1[0]) * ux + (cl.p2[1] - ref.p1[1]) * uy
            spans.append((min(t1, t2), max(t1, t2), cl))
        spans.sort(key=lambda sp: (sp[0], sp[1]))
        cur_lo, cur_hi, cur = spans[0]
        for lo, hi, cl in spans[1:]:
            if lo - cur_hi <= gap:
                cur_hi = max(cur_hi, hi)
                cur = cur if cur.paired else cl
            else:
                out.append(Centerline(
                    p1=(ref.p1[0] + ux * cur_lo, ref.p1[1] + uy * cur_lo),
                    p2=(ref.p1[0] + ux * cur_hi, ref.p1[1] + uy * cur_hi),
                    thickness=cur.thickness, paired=cur.paired))
                cur_lo, cur_hi, cur = lo, hi, cl
        out.append(Centerline(
            p1=(ref.p1[0] + ux * cur_lo, ref.p1[1] + uy * cur_lo),
            p2=(ref.p1[0] + ux * cur_hi, ref.p1[1] + uy * cur_hi),
            thickness=cur.thickness, paired=cur.paired))
    return out


def extend_to_junctions(lines: Sequence[Centerline], unit_scale: float = 1.0,
                        snap_in: float = JUNCTION_SNAP_IN) -> list[Centerline]:
    """Push an endpoint that stops just short of another wall onto it.

    A drafter draws a partition to the FACE of the wall it meets; collapsing to
    centrelines leaves it half a wall thickness short, and half a wall thickness is
    a hole a room leaks through.
    """
    snap = snap_in / unit_scale if unit_scale else snap_in
    out = [Centerline(cl.p1, cl.p2, cl.thickness, cl.paired) for cl in lines]
    for cl in out:
        for which in ("p1", "p2"):
            p = getattr(cl, which)
            best: Optional[tuple[float, Point]] = None
            for other in out:
                if other is cl or other.length == 0:
                    continue
                ux, uy, n = _unit(other.p1, other.p2)
                t = (p[0] - other.p1[0]) * ux + (p[1] - other.p1[1]) * uy
                if not (0.0 <= t <= n):
                    continue
                foot = (other.p1[0] + ux * t, other.p1[1] + uy * t)
                d = math.dist(p, foot)
                if d <= snap and d > 0 and (best is None or d < best[0]):
                    best = (d, foot)
            if best is not None:
                setattr(cl, which, best[1])
    return out


def wall_centerlines(segments: Sequence[Segment], unit_scale: float = 1.0,
                     max_merge_gap_in: float = MAX_MERGE_GAP_IN,
                     snap_in: float = JUNCTION_SNAP_IN) -> list[Centerline]:
    """Faces -> centrelines -> merged runs -> junctions closed."""
    cls = pair_faces(segments, unit_scale)
    cls = merge_collinear(cls, unit_scale, max_gap_in=max_merge_gap_in)
    return extend_to_junctions(cls, unit_scale, snap_in=snap_in)
