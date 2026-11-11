"""Segment a model space into the plan VIEWS a sheet actually shows.

The takeoff must never count the whole model — a CAD export holds the existing
building, the new work, details, and presentation furniture. The original rule
was "find the largest gap between block clusters along one axis; the new work is
the side holding the area labels." That was inferred from a single stripped
drawing and it is wrong on a real coordination sheet in two opposite ways:

  * On a sheet showing BASEMENT and LEVEL 1 side by side it kept one and threw the
    other away — 39 of 57 glazing blocks discarded on Maple 109, leaving 3
    rooms out of 9.
  * On a single plan with an internal courtyard it split one building in half,
    because a 52 ft gap inside a plan looks exactly like a gap between plans.

A sheet already carries the answer. Paperspace viewports state, exactly, which
model-space rectangle each view shows — no heuristic, no threshold. Where a file
has no paperspace (a bare model-space DXF export), fall back to clustering the
line-work, which at least never silently discards half a building.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

Point = tuple[float, float]
BBox = tuple[float, float, float, float]        # x0, y0, x1, y1

#: A viewport showing less than this many wall segments is not a plan — it is the
#: paperspace's own dummy viewport, a key plan, or a north arrow.
MIN_VIEW_SEGMENTS = 20

#: Two viewports may show the same model region at different scales (a detail
#: zoomed into part of a plan). Counting both double-counts every door in the
#: overlap, so a view mostly contained in a larger one is dropped as a detail.
CONTAINMENT_DROP = 0.70


@dataclass
class View:
    """One plan view: a model-space rectangle plus what it was derived from."""

    index: int
    bbox: BBox
    source: str                      # 'viewport' | 'cluster' | 'whole'
    segment_count: int = 0
    title: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def contains(self, p: Point, margin: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bbox
        return (x0 - margin <= p[0] <= x1 + margin
                and y0 - margin <= p[1] <= y1 + margin)

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _overlap(a: BBox, b: BBox) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


_ANNOTATION_MINORS = {"PATT", "IDEN", "ANNO", "DIMS", "TEXT", "SYMB", "NPLT"}


def _is_annotation(layer: str) -> bool:
    return any(p in _ANNOTATION_MINORS for p in (layer or "").upper().split("-")[2:])


def wall_segments(msp, prefixes: Optional[tuple[str, ...]] = ("A-WALL", "I-WALL", "A-FLOR")):
    """Line-work as (layer, p1, p2). The geometry views are found from.

    ``prefixes=None`` takes every non-annotation layer. View detection must not
    depend on AIA layer names — an office with its own scheme would otherwise get
    no views at all, and therefore no takeoff, rather than a slightly rougher one.
    """
    out: list[tuple[str, Point, Point]] = []
    for e in msp.query("LINE LWPOLYLINE"):
        lay = (e.dxf.layer or "").upper()
        if _is_annotation(lay):
            continue
        if prefixes is not None and not lay.startswith(prefixes):
            continue
        if e.dxftype() == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        else:
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            if e.closed and len(pts) > 2:
                pts.append(pts[0])
        for i in range(len(pts) - 1):
            out.append((lay, pts[i], pts[i + 1]))
    return out


def _viewport_rects(doc) -> list[BBox]:
    """Model-space rectangle shown by each paperspace viewport.

    ``view_height`` is the model height the viewport displays; the model width
    follows from the paper aspect ratio. Every layout carries one dummy viewport
    representing the sheet itself — it is discarded downstream by the segment
    count rather than by trusting its id, which is not reliably distinct.
    """
    rects: list[BBox] = []
    for name in doc.layout_names():
        if name.lower() == "model":
            continue
        try:
            layout = doc.layouts.get(name)
        except Exception:                                   # noqa: BLE001
            continue
        for vp in layout.query("VIEWPORT"):
            d = vp.dxf
            try:
                cx = d.view_center_point.x
                cy = d.view_center_point.y
                vh = float(d.view_height)
                pw, ph = float(d.width), float(d.height)
            except (AttributeError, TypeError, ValueError):
                continue
            if vh <= 0 or ph <= 0:
                continue
            mw = vh * (pw / ph)
            rects.append((cx - mw / 2, cy - vh / 2, cx + mw / 2, cy + vh / 2))
    return rects


def _cluster_rects(segs, gap: float) -> list[BBox]:
    """Fallback for a bare model-space export: bounding boxes of line-work clusters.

    Union-find over segments whose bounding boxes come within `gap` of each other.
    The gap is deliberately SMALL, because distance alone cannot tell a detached
    garage from the far wing of one building — 1234 Example's house and garage sit
    closer together than the two wings of a courtyard plan. Overlapping boxes are
    merged afterwards, which is what actually separates buildings: a building's
    outline and its wall network overlap, two buildings' do not.
    """
    boxes = [(min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
             for _lay, p, q in segs]
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # Bucket by grid cell so this is not O(n^2) on a 1400-segment sheet.
    cell = max(gap, 1.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, b in enumerate(boxes):
        for gx in range(int(b[0] // cell), int(b[2] // cell) + 1):
            for gy in range(int(b[1] // cell), int(b[3] // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    for key, idxs in grid.items():
        gx, gy = key
        near = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near += grid.get((gx + dx, gy + dy), [])
        for i in idxs:
            bi = boxes[i]
            for j in near:
                if j <= i:
                    continue
                bj = boxes[j]
                if (bi[0] - gap <= bj[2] and bj[0] - gap <= bi[2]
                        and bi[1] - gap <= bj[3] and bj[1] - gap <= bi[3]):
                    union(i, j)

    groups: dict[int, BBox] = {}
    for i, b in enumerate(boxes):
        r = find(i)
        if r in groups:
            g = groups[r]
            groups[r] = (min(g[0], b[0]), min(g[1], b[1]),
                         max(g[2], b[2]), max(g[3], b[3]))
        else:
            groups[r] = b
    return list(groups.values())


def _merge_overlapping(rects: list[BBox], pad: float = 0.0) -> list[BBox]:
    """Merge boxes that overlap, repeatedly, until nothing more merges.

    A building is drawn as several disconnected networks — the wall network, the
    slab outline 12 in outside it, the roof line. They occupy the same space, so
    they merge. A separate building does not overlap, so it stays separate. This
    is the discriminator a distance threshold cannot provide.
    """
    boxes = list(rects)
    changed = True
    while changed:
        changed = False
        out: list[BBox] = []
        for b in boxes:
            for i, o in enumerate(out):
                if (b[0] - pad <= o[2] and o[0] - pad <= b[2]
                        and b[1] - pad <= o[3] and o[1] - pad <= b[3]):
                    out[i] = (min(b[0], o[0]), min(b[1], o[1]),
                              max(b[2], o[2]), max(b[3], o[3]))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


_TITLE_RE = re.compile(
    r"\b(basement|level\s*\d+|ground|main|upper|lower|roof|second|third|first)\b"
    r"[^\n]{0,24}\b(floor\s*)?plan\b", re.I)


def _titles(msp) -> list[tuple[str, Point]]:
    out = []
    for e in msp.query("MTEXT TEXT"):
        t = " ".join((e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).split())
        if _TITLE_RE.search(t):
            out.append((t[:60], (e.dxf.insert.x, e.dxf.insert.y)))
    return out


def detect_views(doc, unit_scale: float = 1.0,
                 min_segments: int = MIN_VIEW_SEGMENTS) -> list[View]:
    """The plan views on this sheet, largest first.

    Never returns an empty list when there is line-work: a single 'whole' view is
    always better than discarding the drawing.
    """
    msp = doc.modelspace()
    segs = wall_segments(msp)
    if not segs:
        segs = wall_segments(msp, prefixes=None)
    # Element positions join the geometry: a door insert sits ON its wall, and a
    # view whose extent came from line-work alone can crop it out by a hair.
    anchors = [(e.dxf.insert.x, e.dxf.insert.y) for e in msp.query("INSERT")
               if not _is_annotation((e.dxf.layer or "").upper())]
    if not segs:
        # Model space can hold nothing but block references — the line-work lives
        # inside the block definitions. Counts are still measurable; areas are not.
        if not anchors:
            return []
        xs = [a[0] for a in anchors]
        ys = [a[1] for a in anchors]
        return [View(index=0, bbox=(min(xs), min(ys), max(xs), max(ys)),
                     source="whole", segment_count=0,
                     notes=["No line-work in model space — counts only, no areas."])]

    def count_in(b: BBox) -> int:
        return sum(1 for _l, p, q in segs
                   if b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]
                   and b[0] <= q[0] <= b[2] and b[1] <= q[1] <= b[3])

    def grow(b: BBox, near: float) -> BBox:
        """Extend a bbox over the element anchors sitting on its edge."""
        x0, y0, x1, y1 = b
        for ax, ay in anchors:
            if x0 - near <= ax <= x1 + near and y0 - near <= ay <= y1 + near:
                x0, y0 = min(x0, ax), min(y0, ay)
                x1, y1 = max(x1, ax), max(y1, ay)
        return (x0, y0, x1, y1)

    source = "viewport"
    rects = [(r, count_in(r)) for r in _viewport_rects(doc)]
    rects = [(r, c) for r, c in rects if c >= min_segments]
    if not rects:
        # 30 ft between clusters, in drawing units. Two plans on a sheet sit far
        # apart; a courtyard inside one plan does not.
        source = "cluster"
        gap = 24.0 / unit_scale if unit_scale else 24.0        # 2 ft — walls that touch
        raw = _merge_overlapping(_cluster_rects(segs, gap))
        rects = [(r, count_in(r)) for r in raw]
        rects = [(r, c) for r, c in rects if c >= min_segments]

    if not rects:
        xs = [c for _l, p, q in segs for c in (p[0], q[0])] + [a[0] for a in anchors]
        ys = [c for _l, p, q in segs for c in (p[1], q[1])] + [a[1] for a in anchors]
        return [View(index=0, bbox=(min(xs), min(ys), max(xs), max(ys)),
                     source="whole", segment_count=len(segs),
                     notes=["No viewport or cluster resolved — measuring the whole sheet."])]

    if source == "cluster":
        near = 60.0 / unit_scale if unit_scale else 60.0     # 5 ft
        rects = [(grow(r, near), c) for r, c in rects]
    rects.sort(key=lambda rc: -_area(rc[0]))
    kept: list[tuple[BBox, int]] = []
    for r, c in rects:
        if any(_overlap(r, k) >= CONTAINMENT_DROP * _area(r) for k, _ in kept):
            continue                        # a detail zoomed into a view already kept
        kept.append((r, c))

    titles = _titles(msp)
    views = []
    for i, (b, c) in enumerate(kept):
        title = next((t for t, p in titles
                      if b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]), None)
        views.append(View(index=i, bbox=b, source=source, segment_count=c, title=title))
    return views


def clip_to_view(view: View, unit_scale: float = 1.0):
    """Predicate: is this model-space point inside the view?

    A small margin covers dimension witness lines and wall tags that hang just
    outside the viewport crop but belong to the plan.
    """
    margin = 24.0 / unit_scale if unit_scale else 24.0

    def inside(p: Point) -> bool:
        return view.contains(p, margin)

    return inside
