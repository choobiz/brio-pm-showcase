"""Render a plan view to SVG, with the source of one quantity highlighted.

A reviewer asked to confirm "7 windows" cannot do it from the number. They need
to see which seven, on the drawing, beside the number — which is the whole point
of the analysis review. This draws the view's line-work and marks whatever the
selected quantity was measured from:

    door_count / window_count / plumbing_fixture_count
        a ring at each counted block's insertion point
    floor_area_sf / roof_area_sf
        the measured rooms, outlined, each labelled with its own area
    wall_lf_<layer>
        that layer's line-work, drawn in the highlight colour
    perimeter_lf
        nothing to point at — it is traced from the envelope, and on a drawing
        where the trace was refused there is deliberately no line to show

SVG rather than a raster: it is vector like the source, it scales to whatever
pane the browser gives it, and it carries no image dependency.
"""
from __future__ import annotations

import html
import threading
from collections import OrderedDict
from typing import Any, Callable, Iterable, Optional

#: Structural and architectural wall groups, drawn heavier than everything else.
#: A prefix match on the AIA major group, not a list of layer names — a set that
#: names its walls differently still draws, just without the emphasis.
_WALL_GROUPS = ("A-WALL", "I-WALL")

_STYLE = """
.bg{fill:var(--paper,#fff)}
.ln{stroke:#94a3b8;stroke-width:0.6;fill:none;vector-effect:non-scaling-stroke}
.wall{stroke:#334155;stroke-width:1.2}
.hl{stroke:#dc2626;stroke-width:2.2;fill:none;vector-effect:non-scaling-stroke}
.mk{fill:none;stroke:#dc2626;stroke-width:2;vector-effect:non-scaling-stroke}
.mkf{fill:#dc2626;fill-opacity:.12}
.room{stroke:#2563eb;stroke-width:1.6;fill:#2563eb;fill-opacity:.06;
      vector-effect:non-scaling-stroke}
.lbl{fill:#1d4ed8;font:600 11px ui-sans-serif,system-ui;text-anchor:middle;
     paint-order:stroke;stroke:#fff;stroke-width:3px}
.num{fill:#dc2626;font:700 10px ui-sans-serif,system-ui;text-anchor:middle;
     paint-order:stroke;stroke:#fff;stroke-width:3px;dominant-baseline:middle}
"""


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _polyline(pts: Iterable[tuple[float, float]], cls: str) -> str:
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return f'<polyline class="{cls}" points="{d}"/>'


def _read(path: str):
    """The expensive part: convert, load and measure the drawing once."""
    from app.services.drawing.cad_extractor import (
        _inventory, load_doc, run_takeoff, unit_scale,
    )

    doc = load_doc(path)
    unit_scale(_inventory(doc).get("units"))
    return doc, run_takeoff(path)


#: Read drawings, keyed by the stored file's identity. A stored file never
#: changes under its key (a new upload is a new key), and a deploy restarts the
#: process, so an entry can only ever describe the same bytes measured by the
#: same code. That is what makes caching safe here: the earlier worry, a stale
#: picture beside a fresh number, needs either the file or the engine to change,
#: and both empty this cache. Small, because a loaded DXF is tens of MB.
_READ_CACHE: "OrderedDict[str, tuple[Any, Any]]" = OrderedDict()
_READ_CACHE_MAX = 4
_READ_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}


def render_cached(key: str, fetch: Callable[[], str],
                  view_index: int | None = None,
                  highlight: Optional[str] = None,
                  max_marks: int = 200) -> dict[str, Any]:
    """`render_view_svg`, but the drawing is read once per stored file.

    Measured on 1234 Example: every click re-downloaded the DWG, converted it
    twice and re-ran the takeoff, about 15 s per quantity, so a reviewer
    stepping through seven quantities waited close to two minutes and read the
    pane as broken. Switching the highlight or the view needs none of that.

    ``fetch`` returns a local path to the file and is only called on a miss.
    """
    with _READ_LOCK:
        hit = _READ_CACHE.get(key)
        if hit is not None:
            _READ_CACHE.move_to_end(key)
        lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    if hit is None:
        # One reader per file: two clicks during a cold read wait for the first
        # rather than converting the same DWG twice.
        with lock:
            with _READ_LOCK:
                hit = _READ_CACHE.get(key)
            if hit is None:
                hit = _read(fetch())
                with _READ_LOCK:
                    _READ_CACHE[key] = hit
                    while len(_READ_CACHE) > _READ_CACHE_MAX:
                        old, _ = _READ_CACHE.popitem(last=False)
                        _KEY_LOCKS.pop(old, None)
    doc, t = hit
    return _render(doc, t, view_index, highlight, max_marks)


def render_view_svg(path: str, view_index: int | None = None,
                    highlight: Optional[str] = None,
                    max_marks: int = 200) -> dict[str, Any]:
    """Read the CAD file and return {svg, view, views, marks, rooms, warnings}.

    The takeoff runs once and supplies both the view geometry and what was
    counted in each, so the picture and the number cannot disagree.
    """
    doc, t = _read(path)
    return _render(doc, t, view_index, highlight, max_marks)


def _render(doc, t, view_index: int | None, highlight: Optional[str],
            max_marks: int) -> dict[str, Any]:
    from app.services.drawing.cad_extractor import (
        _line_segments, is_annotation_layer,
    )

    msp = doc.modelspace()
    if not t.views:
        return {"svg": None, "view": None, "views": [], "view_count": 0,
                "marks": [], "rooms": [],
                "warnings": ["No plan view could be segmented from this drawing."]}

    catalogue = [{
        "index": v.get("index"),
        "title": v.get("title"),
        "source": v.get("source"),
        "width_ft": v.get("width_ft"),
        "height_ft": v.get("height_ft"),
        # A view the takeoff excluded is existing work or context. It stays
        # drawable, but opening the pane on it would show a reviewer a building
        # that is not the one the numbers describe.
        "included": bool(v.get("included", True)),
    } for v in t.views]

    if view_index is None:
        view_index = next((c["index"] for c in catalogue if c["included"]),
                          catalogue[0]["index"])
    v = next((x for x in t.views if x.get("index") == view_index), t.views[0])

    vx0, vy0, vx1, vy1 = v["bbox"]

    def inside(p):
        return vx0 <= p[0] <= vx1 and vy0 <= p[1] <= vy1

    # Frame the BUILDING, not the view. A view segmented off a site plan is
    # mostly lot: framing it whole leaves the building as a small object in a
    # field of white, which is not something a reviewer can check a window
    # against.
    walls = _line_segments(msp, _WALL_GROUPS, inside)
    x0, y0, x1, y1 = vx0, vy0, vx1, vy1
    if walls:
        xs = [pt[0] for seg in walls for pt in seg]
        ys = [pt[1] for seg in walls for pt in seg]
        wx0, wy0, wx1, wy1 = min(xs), min(ys), max(xs), max(ys)
        # Only frame on the walls if there is a BUILDING there. A survey, a
        # street-lighting plan or a civil sheet may carry a stray wall-named
        # layer holding a single short segment; framing on that zooms into a
        # few inches of nothing. A view is the safe fallback — too wide beats
        # pointed at the wrong thing.
        if max(wx1 - wx0, wy1 - wy0) >= 0.05 * max(vx1 - vx0, vy1 - vy0, 1.0):
            x0, y0, x1, y1 = wx0, wy0, wx1, wy1
    pad = max(x1 - x0, y1 - y0, 1.0) * 0.06
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

    def fully_in_frame(pt) -> bool:
        return x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1

    body: list[str] = [f'<rect class="bg" x="{x0:.1f}" y="{y0:.1f}" '
                       f'width="{w:.1f}" height="{h:.1f}"/>']

    wall_layer = None
    if highlight and highlight.startswith("wall_lf_"):
        wall_layer = highlight[len("wall_lf_"):].upper()

    # Draw whatever the drawing has, rather than a list of layers we happened to
    # meet. Two general rules do the filtering, and neither depends on the set:
    #
    #   * annotation layers are excluded by AIA minor group (dimensions, tags,
    #     hatch poche) — they are labels about the building, not the building;
    #   * a segment is drawn only if it lies ENTIRELY inside the frame. A lot
    #     line, a section path or a match line runs off the building and is
    #     dropped by that alone, with no need to know which layer carries it in
    #     this particular office's template.
    buckets: dict[str, list] = {"ln": [], "wall": [], "hl": []}
    for e in msp.query("LINE LWPOLYLINE"):
        lay = (e.dxf.layer or "").upper()
        if is_annotation_layer(lay):
            continue
        if e.dxftype() == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        else:
            try:
                pts = [(pt[0], pt[1]) for pt in e.get_points("xy")]
            except Exception:  # noqa: BLE001
                continue
            if e.closed and len(pts) > 2:
                pts.append(pts[0])
        if len(pts) < 2 or not all(fully_in_frame(pt) for pt in pts):
            continue
        if wall_layer and lay == wall_layer:
            buckets["hl"].append(pts)
        elif lay.startswith(_WALL_GROUPS):
            buckets["wall"].append(pts)
        else:
            buckets["ln"].append(pts)

    for cls, key in (("ln", "ln"), ("ln wall", "wall"), ("hl", "hl")):
        body += [_polyline(pts, cls) for pts in buckets[key]]

    marks: list[dict[str, Any]] = []
    rooms: list[dict[str, Any]] = []

    if highlight in ("floor_area_sf", "roof_area_sf"):
        for r in t.rooms:
            if r.get("view") != v.get("index"):
                continue
            bx0, by0, bx1, by1 = r["bbox"]
            rooms.append(r)
            body.append(f'<rect class="room" x="{bx0:.1f}" y="{by0:.1f}" '
                        f'width="{bx1-bx0:.1f}" height="{by1-by0:.1f}"/>')
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            body.append(f'<text class="lbl" x="{cx:.1f}" y="{cy:.1f}" '
                        f'transform="scale(1,-1) translate(0,{-2*cy:.1f})">'
                        f'{_esc(round(r["centerline_sf"], 1))} sf</text>')

    elif highlight and highlight.endswith("_count"):
        cat = highlight[: -len("_count")]
        marks = list((v.get("marks") or {}).get(cat) or [])[:max_marks]
        rr = max(w, h) * 0.018
        for i, m in enumerate(marks, 1):
            body.append(f'<circle class="mk mkf" cx="{m["x"]:.1f}" cy="{m["y"]:.1f}" '
                        f'r="{rr:.1f}"/>')
            body.append(f'<text class="num" x="{m["x"]:.1f}" y="{m["y"]:.1f}" '
                        f'transform="scale(1,-1) translate(0,{-2*m["y"]:.1f})">'
                        f'{i}</text>')

    # CAD y grows upward, SVG y grows downward. Flip once, on the whole group,
    # and counter-flip the text so labels are not written upside down.
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{x0:.1f} {-y1:.1f} {w:.1f} {h:.1f}" '
        f'preserveAspectRatio="xMidYMid meet" role="img">'
        f"<style>{_STYLE}</style>"
        f'<g transform="scale(1,-1)">{"".join(body)}</g></svg>'
    )
    return {
        "svg": svg,
        "view": next(c for c in catalogue if c["index"] == v.get("index")),
        "views": catalogue,
        "view_count": len(catalogue),
        "marks": marks,
        "rooms": [{"centerline_sf": r["centerline_sf"], "net_sf": r["net_sf"]}
                  for r in rooms],
        "warnings": [],
    }
