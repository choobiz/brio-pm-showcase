"""Walls drawn as solid fills ("poché"), measured as centre-line lengths.

456 W Sample ID-1/ID-2 (2026-10-26): every wall on the plan is a grey fill,
and the PostScript print split each one into hundreds of triangles. A line
detector never sees them, so the measure page listed fixtures, doors and stairs
and no walls at all.

A wall of thickness t and centre-line length L covers an area of about L x t,
so L = area / t. Both are read from a raster of the filled shapes:

  * area: the pixels the shapes cover, which also merges triangles, overlaps and
    any other way a print happened to cut the wall up;
  * thickness: at each pixel, the shorter of its horizontal and vertical run
    through the fill. On a straight wall that is the wall's width everywhere
    except at corners and ends, so the median over the wall is its thickness.

Each connected mass of one fill colour becomes one candidate, like a chain of
touching lines does. Shapes too thin to be a wall (lettering, hairlines drawn as
fills) or too squat to be a run (columns, solid symbols) are left out.

Limits, said plainly: a wall drawn at an angle reads thicker than it is, so its
length comes out short (about 30% at 45 degrees); the length of a wall mass that
branches includes the overlap at each junction once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.drawing.room_areas import _components

#: Raster resolution, pixels per PDF unit. A 9-unit (6" at 1/4") wall spans 18 px,
#: so a 1 px error in thickness is ~5%. The median over the wall averages it out.
PX_PER_UNIT = 2.0
#: This thin or thinner (PDF units) is lettering or a hairline, not a wall. On the
#: corridor set the junk was 2.5 and the thinnest wall 6.5: 2" at 1/4" = 1'-0".
MIN_THICKNESS = 3.0
#: Thicker than this is a slab, a column or a solid symbol: 32" at 1/4".
MAX_THICKNESS = 48.0
#: A wall is a run: its length must be at least this many times its thickness.
MIN_LENGTH_TO_THICKNESS = 4.0
#: Shapes are only rasterised when they could belong to a wall: a piece whose
#: bounding box is smaller than this in both directions is a letter or a dot.
MIN_PIECE_SPAN = 6.0
#: Largest raster per fill colour (a 42x30 in sheet is 26M px). Bounds the
#: worker's memory to a few hundred MB on the 8 GB VM.
MAX_PIXELS = 30_000_000
#: Near-white fills are paper (masking boxes, title-block backgrounds).
MAX_LUMINANCE = 0.95


@dataclass
class FilledWall:
    page: int
    bbox: tuple[float, float, float, float]
    length: float                     # centre-line length, PDF units
    thickness: float                  # PDF units
    colour: tuple[float, float, float]
    #: Member shapes' bboxes, so the review page can highlight the wall itself.
    pieces: list[tuple[float, float, float, float]] = field(default_factory=list)


def _flatten(items: list) -> list[list[tuple[float, float]]]:
    """A fill's outline as closed polygons (bezier curves as 8 chords)."""
    polys: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def close() -> None:
        if len(cur) >= 3:
            polys.append(list(cur))
        cur.clear()

    for it in items:
        op = it[0]
        if op == "re":
            close()
            r = it[1]
            polys.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
        elif op == "qu":
            close()
            q = it[1]
            polys.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
        elif op in ("l", "c"):
            p0 = (it[1].x, it[1].y)
            if not cur or abs(cur[-1][0] - p0[0]) > 1e-3 or abs(cur[-1][1] - p0[1]) > 1e-3:
                close()
                cur.append(p0)
            if op == "l":
                cur.append((it[2].x, it[2].y))
            else:
                a, b, c, d = (np.array([p.x, p.y]) for p in it[1:5])
                for t in np.linspace(0.125, 1.0, 8):
                    u = 1 - t
                    pt = u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                    cur.append((float(pt[0]), float(pt[1])))
    close()
    return polys


def _spans(poly: list[tuple[float, float]], ox: float, oy: float, h: int, w: int):
    """Even-odd scanline spans of one polygon: (rows, x_start, x_end) in pixels,
    for pixel centres. Memory is a few numbers per row, never one per pixel:
    a sheet-sized fill used to cost a sheet-sized float array per edge."""
    pts = np.array(poly, dtype=float)
    px = (pts[:, 0] - ox) * PX_PER_UNIT
    py = (pts[:, 1] - oy) * PX_PER_UNIT
    xb, yb = np.roll(px, -1), np.roll(py, -1)
    rows, xs = [], []
    for xa_, ya_, xb_, yb_ in zip(px, py, xb, yb):
        if ya_ == yb_:
            continue
        lo, hi = min(ya_, yb_), max(ya_, yb_)
        r = np.arange(max(int(np.ceil(lo - 0.5)), 0), min(int(np.ceil(hi - 0.5)), h))
        if r.size == 0:
            continue
        rows.append(r)
        xs.append(xa_ + (r + 0.5 - ya_) * (xb_ - xa_) / (yb_ - ya_))
    if not rows:
        return None
    r = np.concatenate(rows)
    x = np.concatenate(xs)
    order = np.lexsort((x, r))
    r, x = r[order], x[order]
    if r.size % 2:
        return None  # an open outline; nothing to fill reliably
    r0, r1 = r[0::2], r[1::2]
    keep = r0 == r1
    s0 = np.clip(np.ceil(x[0::2][keep] - 0.5), 0, w).astype(np.int64)
    s1 = np.clip(np.ceil(x[1::2][keep] - 0.5), 0, w).astype(np.int64)
    good = s1 > s0
    return r0[keep][good], s0[good], s1[good]


def _run_lengths(mask: np.ndarray) -> np.ndarray:
    """For each set pixel, the length of the horizontal run it sits in."""
    out = np.zeros(mask.shape, dtype=np.int16)  # runs <= sheet width x 2 px < 32k
    for y in np.flatnonzero(mask.any(axis=1)):
        row = mask[y]
        d = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            out[y, s:e] = e - s
    return out


def _colour_key(fill) -> tuple[float, float, float] | None:
    if not fill or len(fill) < 3:
        return None
    c = tuple(round(float(v) * 50) / 50 for v in fill[:3])
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return None if lum > MAX_LUMINANCE else c


def filled_walls(page: int, drawings: list[dict]) -> list[FilledWall]:
    """Wall runs drawn as solid fills on one page, from ``page.get_drawings()``."""
    by_colour: dict[tuple, list[tuple[tuple, list]]] = {}
    for d in drawings:
        if d.get("type") not in ("f", "fs"):
            continue
        key = _colour_key(d.get("fill"))
        r = d.get("rect")
        if key is None or r is None:
            continue
        if max(r.width, r.height) < MIN_PIECE_SPAN:
            continue
        by_colour.setdefault(key, []).append(((r.x0, r.y0, r.x1, r.y1), d.get("items") or []))

    walls: list[FilledWall] = []
    for colour, shapes in by_colour.items():
        ox = min(b[0] for b, _ in shapes) - 2
        oy = min(b[1] for b, _ in shapes) - 2
        w = int((max(b[2] for b, _ in shapes) - ox + 4) * PX_PER_UNIT) + 1
        h = int((max(b[3] for b, _ in shapes) - oy + 4) * PX_PER_UNIT) + 1
        if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
            continue
        # Coverage by scanline spans, summed across shapes: overlapping pieces
        # of one wall union instead of cancelling.
        cov = np.zeros((h, w + 1), dtype=np.int16)
        for _, items in shapes:
            for poly in _flatten(items):
                sp = _spans(poly, ox, oy, h, w)
                if sp is not None:
                    np.add.at(cov, (sp[0], sp[1]), 1)
                    np.add.at(cov, (sp[0], sp[2]), -1)
        mask = np.cumsum(cov, axis=1, dtype=np.int16)[:, :w] > 0
        del cov
        if not mask.any():
            continue
        # Per-wall area, median thickness and extent from the set pixels only;
        # each full-sheet array is dropped as soon as its values are taken.
        idx = np.flatnonzero(mask)
        local = _run_lengths(mask)
        np.minimum(local, _run_lengths(mask.T).T, out=local)
        thick = np.clip(local.ravel()[idx], 0, 255).astype(np.int64)
        del local
        labels, count = _components(mask)
        if count == 0:
            continue
        lab = labels.ravel()[idx].astype(np.int64)
        hist = np.bincount(lab * 256 + thick, minlength=(count + 1) * 256).reshape(count + 1, 256)
        area = hist.sum(axis=1)
        median_px = (np.cumsum(hist, axis=1) >= (area[:, None] + 1) / 2).argmax(axis=1)
        ys, xs = np.divmod(idx, w)
        big = np.iinfo(np.int64).max
        x_lo, y_lo = np.full(count + 1, big), np.full(count + 1, big)
        x_hi, y_hi = np.full(count + 1, -1), np.full(count + 1, -1)
        np.minimum.at(x_lo, lab, xs)
        np.minimum.at(y_lo, lab, ys)
        np.maximum.at(x_hi, lab, xs)
        np.maximum.at(y_hi, lab, ys)
        found: dict[int, FilledWall] = {}
        for lb in range(1, count + 1):
            if area[lb] == 0:
                continue
            t = float(median_px[lb]) / PX_PER_UNIT
            if not (MIN_THICKNESS < t <= MAX_THICKNESS):
                continue
            length = float(area[lb]) / (PX_PER_UNIT ** 2) / t
            if length < MIN_LENGTH_TO_THICKNESS * t:
                continue
            bbox = (round(float(ox + x_lo[lb] / PX_PER_UNIT), 1), round(float(oy + y_lo[lb] / PX_PER_UNIT), 1),
                    round(float(ox + (x_hi[lb] + 1) / PX_PER_UNIT), 1),
                    round(float(oy + (y_hi[lb] + 1) / PX_PER_UNIT), 1))
            found[lb] = FilledWall(page=page, bbox=bbox, length=round(length, 1), thickness=round(t, 2),
                                   colour=colour)
        # Hand each shape to the wall it lies in (by its vertex centroid, which is
        # inside any triangle or convex piece), so the page can highlight it.
        for b, items in shapes:
            for poly in _flatten(items):
                cx = int((sum(p[0] for p in poly) / len(poly) - ox) * PX_PER_UNIT)
                cy = int((sum(p[1] for p in poly) / len(poly) - oy) * PX_PER_UNIT)
                if 0 <= cy < h and 0 <= cx < w and labels[cy, cx] in found:
                    found[int(labels[cy, cx])].pieces.append(tuple(round(float(v), 1) for v in b))
                    break
        walls.extend(found.values())
    return walls
