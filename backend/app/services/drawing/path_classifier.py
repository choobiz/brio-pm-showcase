"""Group raw vector paths into linear-quantity candidates.

The extractor returns hundreds of thousands of individual paths. Useful
takeoff requires grouping them into meaningful entities — partition lines,
dimension lines, hatching, room borders.

For orthogonal strokes (most architectural / mechanical line work), this
module performs **endpoint chaining**: paths whose endpoints touch (within
a small tolerance) form a single connected chain. Each chain becomes one
candidate, so a continuous wall run drawn as 5 segments produces 1
candidate — not 5 — and a nearby unrelated stroke is not lumped in.

Straight diagonal lines chain the same way, by the points each line really
starts and ends at: a bbox cannot say which of its corners a diagonal joins.
Until 2026-10-26 they were lumped with every other non-orthogonal path into one
group per page. Other non-orthogonal paths (outlines drawn as one polyline,
arcs, symbols) are shapes, not runs, and still form one group per page and
weight; a group like that spread over the sheet is dropped as implausible.

Walls drawn as solid fills are measured separately (poche.py) and arrive here
as "wall_filled" candidates with their centre-line length.

Heuristics are intentionally project-type-agnostic — same rules apply to
residential renos, new builds, and commercial TIs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.drawing.poche import FilledWall
from app.services.drawing.pymupdf_extractor import DrawingExtract, VectorPath


# Stroke-width buckets (in PDF points). Tuned to typical CAD-export
# conventions; the review UI shows the actual width so the user can adjust.
_BUCKETS = [
    (0.3, "very_thin"),    # hatching / fill detail
    (0.6, "thin"),         # dimension lines, light annotations
    (1.2, "medium"),       # standard line work, doors / fixtures
    (2.0, "thick"),        # walls / partitions
    (float("inf"), "very_thick"),  # borders / title-block outlines
]

# Minimum total length (PDF units) for a group to surface as a candidate.
# Avoids flooding the UI with thousands of single-pixel paths.
_MIN_GROUP_LENGTH = 100.0

# Endpoint-snap tolerance for chaining orthogonal strokes. Two paths chain
# together if any of their endpoints lie within this many PDF units of each
# other. CAD-exported PDFs typically have clean endpoint snapping (sub-unit);
# 2.0 is a forgiving default that handles slight float-roundoff without
# bridging unrelated lines.
_CHAIN_TOLERANCE = 2.0

# 456 W Sample (2026-10-26): what made the measure page show "13,486 ft" for
# a 687 sf corridor. Each rule below drops something that is not a length.
#
# A group spread over this share of the sheet is not one building element: it
# is every line of one weight on the page (notes, title block, both plans).
_MAX_GROUP_PAGE_FRACTION = 0.35
# A group whose length is this many times its own outline's perimeter is
# packed too densely to be walls or runs: lettering, hatching, poché.
_MAX_LENGTH_TO_PERIMETER = 6.0


@dataclass
class PathRef:
    """A single member path inside a candidate group — kept so the review UI
    can highlight each individual stroke instead of just the bbox union."""
    bbox: tuple[float, float, float, float]
    length: float


@dataclass
class LinearCandidate:
    """A grouped set of vector paths that together represent a likely
    linear-quantity element on a drawing page."""

    page: int
    classification: str          # see _classify()
    width_bucket: str            # very_thin | thin | medium | thick | very_thick
    orthogonal: bool             # True ⇒ mostly horizontal/vertical
    total_length_pdf_units: float
    path_count: int
    stroke_width: float          # median stroke width across the group
    bbox: tuple[float, float, float, float]  # union of member bboxes
    paths: list[PathRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bucket(width: float) -> str:
    for upper, name in _BUCKETS:
        if width <= upper:
            return name
    return "very_thick"


def _is_orthogonal(path: VectorPath) -> bool:
    """Lines parallel to page axes have one bbox dimension near zero, or a
    very high aspect ratio."""
    x0, y0, x1, y1 = path.bbox
    w, h = abs(x1 - x0), abs(y1 - y0)
    if w == 0 and h == 0:
        return False
    short = min(w, h)
    long_ = max(w, h)
    if short <= 1.0:
        return True
    if long_ > 20 and short / long_ < 0.02:
        return True
    return False


def _classify(width_bucket: str, orthogonal: bool) -> str:
    """Map (bucket, orientation) → human-readable category."""
    if width_bucket == "thick" and orthogonal:
        return "wall_or_partition"
    if width_bucket == "very_thick":
        return "border_or_outline"
    if width_bucket == "thin" and orthogonal:
        return "dimension_line"
    if width_bucket == "very_thin":
        return "hatch_or_detail"
    if width_bucket == "medium" and orthogonal:
        return "general_lines_ortho"
    if width_bucket == "medium":
        return "general_lines_curve"
    return "other"


def _union_bbox(paths: list[VectorPath]) -> tuple[float, float, float, float]:
    xs0 = [p.bbox[0] for p in paths]
    ys0 = [p.bbox[1] for p in paths]
    xs1 = [p.bbox[2] for p in paths]
    ys1 = [p.bbox[3] for p in paths]
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _endpoints(bbox: tuple[float, float, float, float]) -> tuple[
    tuple[float, float], tuple[float, float]
]:
    """Two likely endpoints of an orthogonal stroke, derived from its bbox.

    Horizontal stroke: endpoints sit on the bbox's horizontal mid-line at
    the left and right edges. Vertical stroke: endpoints on the vertical
    mid-line at top and bottom.
    """
    x0, y0, x1, y1 = bbox
    w = abs(x1 - x0)
    h = abs(y1 - y0)
    if w >= h:  # horizontal-ish (or square — both endpoint pairs reasonable)
        ymid = (y0 + y1) / 2
        return ((min(x0, x1), ymid), (max(x0, x1), ymid))
    xmid = (x0 + x1) / 2
    return ((xmid, min(y0, y1)), (xmid, max(y0, y1)))


def _path_ends(path: VectorPath) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where a path really starts and ends, falling back to its bbox."""
    if path.p0 is not None and path.p1 is not None:
        return path.p0, path.p1
    return _endpoints(path.bbox)


def _chain_by_endpoints(
    paths: list[VectorPath],
    tolerance: float = _CHAIN_TOLERANCE,
) -> list[list[VectorPath]]:
    """Find connected components where paths share endpoints (within
    tolerance). Each component is a continuous run — typically one real
    wall (possibly L- or U-shaped) drawn as multiple segments.

    Spatial-hashed for O(n) expected behaviour: endpoints are bucketed into
    `tolerance`-sized grid cells, so neighbour lookup is a constant-cost
    check of 9 cells regardless of n.
    """
    n = len(paths)
    if n == 0:
        return []

    eps = [_path_ends(p) for p in paths]

    cell = tolerance * 2  # grid cell size: an endpoint's neighbours all sit
    # inside the path's own cell plus the 8 adjacent cells.
    bucket: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = defaultdict(list)
    for i, (a, b) in enumerate(eps):
        for ep in (a, b):
            bucket[(int(ep[0] // cell), int(ep[1] // cell))].append((i, ep))

    visited = [False] * n
    chains: list[list[VectorPath]] = []

    tol_sq = tolerance * tolerance

    for start in range(n):
        if visited[start]:
            continue
        chain: list[VectorPath] = []
        stack = [start]
        while stack:
            idx = stack.pop()
            if visited[idx]:
                continue
            visited[idx] = True
            chain.append(paths[idx])
            # Probe 9 grid cells around each of this path's endpoints for
            # neighbours within tolerance.
            for ep in eps[idx]:
                bx = int(ep[0] // cell)
                by = int(ep[1] // cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j, jp in bucket.get((bx + dx, by + dy), ()):
                            if j == idx or visited[j]:
                                continue
                            ddx = ep[0] - jp[0]
                            ddy = ep[1] - jp[1]
                            if ddx * ddx + ddy * ddy <= tol_sq:
                                stack.append(j)
        chains.append(chain)
    return chains


def _make_candidate(
    page: int,
    bucket: str,
    ortho: bool,
    paths: list[VectorPath],
) -> LinearCandidate | None:
    total = sum(p.length for p in paths)
    if total < _MIN_GROUP_LENGTH:
        return None
    widths = sorted(p.stroke_width for p in paths if p.stroke_width > 0)
    median = widths[len(widths) // 2] if widths else 0.0
    return LinearCandidate(
        page=page,
        classification=_classify(bucket, ortho),
        width_bucket=bucket,
        orthogonal=ortho,
        total_length_pdf_units=round(total, 1),
        path_count=len(paths),
        stroke_width=round(median, 2),
        bbox=tuple(round(v, 1) for v in _union_bbox(paths)),
        paths=[
            PathRef(
                bbox=tuple(round(v, 1) for v in p.bbox),
                length=round(p.length, 1),
            )
            for p in paths
        ],
    )


def classify(extract: DrawingExtract) -> list[LinearCandidate]:
    """Group every page's paths into candidates.

    Strokes go through endpoint-chaining: each connected run becomes one
    candidate. Walls drawn as solid fills come from the extractor already
    measured, one candidate per connected wall mass.
    """
    pages = {pg.page: pg for pg in extract.pages}
    # A sheet whose plan is a picture has nothing measurable: what is left is
    # title block and notes. The page says so instead (raster_plan).
    picture_pages = {pg.page for pg in extract.pages if getattr(pg, "raster_plan", False)}

    # First split paths by (page, width_bucket, orthogonality).
    groups: dict[tuple[int, str, bool], list[VectorPath]] = defaultdict(list)
    for p in extract.paths:
        # A filled shape's outline is not a line: its perimeter counts a
        # hairline twice and a letter all the way round. PDFs printed through
        # PostScript turn all line-work and text into fills. Walls drawn as
        # filled poché need their own measure, not a perimeter.
        if p.op_type == "fill" or p.page in picture_pages:
            continue
        groups[(p.page, _bucket(p.stroke_width), _is_orthogonal(p))].append(p)

    candidates: list[LinearCandidate] = []
    for (page, bucket, ortho), paths in groups.items():
        # Each connected run becomes one candidate, so an L-shaped wall drawn
        # as 2 segments stays one candidate, and an unrelated nearby stroke
        # stays separate.
        runs = paths if ortho else [p for p in paths if p.straight]
        for chain in _chain_by_endpoints(runs):
            cand = _make_candidate(page, bucket, ortho, chain)
            if cand is not None:
                candidates.append(cand)
        if not ortho:
            # Shapes, not runs (see the module docstring): one group, as before.
            shapes = [p for p in paths if not p.straight]
            cand = _make_candidate(page, bucket, False, shapes) if shapes else None
            if cand is not None:
                candidates.append(cand)

    for wall in getattr(extract, "filled_walls", None) or ():
        if wall.page in picture_pages:
            continue
        cand = _filled_wall_candidate(wall)
        if cand is not None:
            candidates.append(cand)

    candidates = [c for c in candidates if _plausible(c, pages.get(c.page))]
    candidates.sort(key=lambda c: (c.page, -c.total_length_pdf_units))
    return candidates


def _filled_wall_candidate(wall: FilledWall) -> LinearCandidate | None:
    """A wall drawn as a solid fill, as a candidate measured on its centre line.
    ``stroke_width`` carries the wall's drawn thickness (PDF units)."""
    if wall.length < _MIN_GROUP_LENGTH:
        return None
    pieces = wall.pieces or [wall.bbox]
    # Highlighting needs a length per member only to rank them; share the
    # wall's length by each piece's long side.
    spans = [max(b[2] - b[0], b[3] - b[1]) for b in pieces]
    total_span = sum(spans) or 1.0
    return LinearCandidate(
        page=wall.page,
        classification="wall_filled",
        width_bucket="fill",
        orthogonal=True,
        total_length_pdf_units=round(wall.length, 1),
        path_count=len(pieces),
        stroke_width=round(wall.thickness, 2),
        bbox=tuple(round(v, 1) for v in wall.bbox),
        paths=[PathRef(bbox=tuple(round(v, 1) for v in b), length=round(wall.length * sp / total_span, 1))
               for b, sp in zip(pieces, spans)],
    )


def _plausible(c: LinearCandidate, page) -> bool:
    """False for a group that cannot be one run of building: spread across the
    sheet, or packed denser than any wall run could be."""
    x0, y0, x1, y1 = c.bbox
    w, h = abs(x1 - x0), abs(y1 - y0)
    if page is not None and page.width and page.height:
        if (w * h) / (page.width * page.height) > _MAX_GROUP_PAGE_FRACTION:
            return False
    perimeter = 2 * (w + h)
    if perimeter > 0 and c.total_length_pdf_units / perimeter > _MAX_LENGTH_TO_PERIMETER:
        return False
    return True
