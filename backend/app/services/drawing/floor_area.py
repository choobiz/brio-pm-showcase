"""Derive floor area and perimeter from wall line-work, with no closed geometry.

Why this exists
---------------
Real architectural DXFs do not contain the polygon you want. Measured on the
1234 Example garage (1.4 MB, from the architect):

    closed polylines in the whole file ......... 0
    A-WALL segments on the new-work side ...... 63, with 14 open ends
    A-FLOR segments on the new-work side ...... 45, with 24 open ends
    A-WALL bounding box ....................... 1001 sf, against a real ~668 sf

Walls are drawn as loose segments and they are *deliberately* broken at every
door and window. So there is nothing to run a shoelace over, and the bounding box
overestimates a non-rectangular building by ~50%.

The pipeline here closes that gap in three steps — snap, bridge, then extract
planar faces — and returns the enclosed region. No LLM, fully deterministic, and
every step is inspectable.

Approach
--------
1. **SNAP** endpoints that are within a tolerance of each other onto one node.
   CAD line-work is drawn to a grid but stored as floats; two "touching" walls
   are routinely 0.001-0.5 in apart. Union-find over a spatial hash, so the merge
   is transitive and order-independent.

2. **BRIDGE** the openings. Every remaining degree-1 node is a wall end at a door
   or window. Pair them up when they are (a) within `max_gap`, and (b) the gap is
   roughly parallel to one of the two wall directions — so a doorway gets closed
   but two unrelated stubs across the room do not.

3. **FACES** by planar traversal. Walk each directed edge always taking the
   clockwise-most turn at the next node; each walk closes one face. Interior
   faces come back with positive signed area, the outer boundary negative — so
   the building footprint is simply the largest positive face.

Returning nothing is a valid answer: if the line-work will not close, this
reports `None` rather than a bounding box, because a confident wrong area is
worse than no area (it lands in a price).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

Point = tuple[float, float]
Segment = tuple[Point, Point]


@dataclass
class Footprint:
    area_sf: float
    perimeter_lf: float
    polygon: list[Point]
    bridged_gaps: int
    snapped_nodes: int
    faces_found: int
    # Which network this came from, and whether it was already closed. An
    # outline that needed no bridging is far more trustworthy than one
    # assembled by guessing which loose ends belong together.
    component_index: int = 0
    components: int = 1
    needed_bridging: bool = False
    interior_faces_sf: list[float] = field(default_factory=list)
    # ── articulation ────────────────────────────────────────────────────────
    # Corner trim is priced PER CORNER, so a footprint that reports only area
    # and perimeter cannot produce a correct cladding order. On 1234 Example
    # six corners were priced against an actual twelve, and the four INTERNAL
    # ones need a different trim profile from the external ones.
    external_corners: int = 0
    internal_corners: int = 0
    #: External minus internal is 4 for any simple rectilinear polygon. False
    #: means the trace is not a simple closed rectilinear loop and the corner
    #: counts cannot be trusted — not that the building is unusual.
    corner_invariant_ok: bool = True
    #: True when every edge lies on one of two perpendicular axes.
    rectilinear: bool = True
    #: 2 x (width + height) of the bounding box. Equals the traced perimeter for
    #: a plain rectangle or an L; exceeds it never; falls SHORT whenever the
    #: outline has a recess or bump-out interior to the box.
    bbox_perimeter_lf: float = 0.0
    #: True when the outline could only be closed by bridging ACROSS separate
    #: networks. Weaker provenance than a component that closed on its own, so
    #: it is recorded rather than hidden.
    cross_component_bridged: bool = False


# ── 1. snap ──────────────────────────────────────────────────────────────────

class _UF:
    def __init__(self) -> None:
        self.p: dict[int, int] = {}

    def find(self, a: int) -> int:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def snap_segments(segments: Iterable[Segment], tol: float) -> tuple[list[Segment], int]:
    """Merge endpoints within `tol` onto a single representative point.

    Uses a spatial hash of cell size `tol` and checks the 3x3 neighbourhood, so
    two points either side of a cell boundary still merge — a plain
    round-to-grid would split them.
    """
    pts: list[Point] = []
    for a, b in segments:
        pts.append(a)
        pts.append(b)
    if not pts:
        return [], 0

    cell = tol if tol > 0 else 1e-9
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(pts):
        buckets.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append(i)

    uf = _UF()
    for (cx, cy), idxs in buckets.items():
        near: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near.extend(buckets.get((cx + dx, cy + dy), ()))
        for i in idxs:
            for j in near:
                if i < j and math.dist(pts[i], pts[j]) <= tol:
                    uf.union(i, j)

    rep: dict[int, Point] = {}
    for i in range(len(pts)):
        r = uf.find(i)
        rep.setdefault(r, pts[i])

    out: list[Segment] = []
    for k in range(0, len(pts), 2):
        a = rep[uf.find(k)]
        b = rep[uf.find(k + 1)]
        if a != b:
            out.append((a, b))
    # drop exact duplicates (walls drawn twice, or two layers stacked)
    seen: set[tuple[Point, Point]] = set()
    uniq: list[Segment] = []
    for a, b in out:
        key = (a, b) if a <= b else (b, a)
        if key not in seen:
            seen.add(key)
            uniq.append((a, b))
    merged = len(pts) - len(rep)
    return uniq, merged


# ── 2. bridge openings ───────────────────────────────────────────────────────

def _degree(segments: list[Segment]) -> dict[Point, int]:
    d: dict[Point, int] = {}
    for a, b in segments:
        d[a] = d.get(a, 0) + 1
        d[b] = d.get(b, 0) + 1
    return d


def _dir(a: Point, b: Point) -> Point:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return (dx / n, dy / n)


def bridge_openings(segments: list[Segment], max_gap: float,
                    angle_tol_deg: float = 20.0) -> tuple[list[Segment], int]:
    """Close door/window gaps by joining nearby loose wall ends.

    A pair is bridged only when the connector runs roughly ALONG one of the two
    walls it joins. That is what distinguishes a doorway — where the wall
    continues past the opening on the same line — from two unrelated stubs
    facing each other across a room.
    """
    ends = [p for p, d in _degree(segments).items() if d == 1]
    if len(ends) < 2:
        return segments, 0

    wall_dir: dict[Point, Point] = {}
    for a, b in segments:
        if a in wall_dir or _degree(segments).get(a) == 1:
            wall_dir.setdefault(a, _dir(b, a))
        if b in wall_dir or _degree(segments).get(b) == 1:
            wall_dir.setdefault(b, _dir(a, b))

    cands: list[tuple[float, Point, Point]] = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            p, q = ends[i], ends[j]
            g = math.dist(p, q)
            if g <= 1e-9 or g > max_gap:
                continue
            conn = _dir(p, q)
            ok = False
            for e in (p, q):
                w = wall_dir.get(e)
                if not w:
                    continue
                dot = abs(conn[0] * w[0] + conn[1] * w[1])
                if dot >= math.cos(math.radians(angle_tol_deg)):
                    ok = True
                    break
            if ok:
                cands.append((g, p, q))

    cands.sort(key=lambda c: c[0])          # shortest gaps first
    used: set[Point] = set()
    added: list[Segment] = []
    for g, p, q in cands:
        if p in used or q in used:
            continue
        used.add(p)
        used.add(q)
        added.append((p, q))
    return segments + added, len(added)


# ── 3. planar faces ──────────────────────────────────────────────────────────

def _shoelace(poly: list[Point]) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def extract_faces(segments: list[Segment]) -> list[list[Point]]:
    """Planar face traversal: always take the clockwise-most turn."""
    adj: dict[Point, list[Point]] = {}
    for a, b in segments:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    for node, nbrs in adj.items():
        nbrs.sort(key=lambda n: math.atan2(n[1] - node[1], n[0] - node[0]))

    visited: set[tuple[Point, Point]] = set()
    faces: list[list[Point]] = []
    for a, b in segments:
        for u, v in ((a, b), (b, a)):
            if (u, v) in visited:
                continue
            face: list[Point] = []
            cu, cv = u, v
            while (cu, cv) not in visited:
                visited.add((cu, cv))
                face.append(cu)
                nbrs = adj[cv]
                i = nbrs.index(cu)
                nxt = nbrs[i - 1]          # clockwise-most turn
                cu, cv = cv, nxt
                if len(face) > 10000:      # pathological input guard
                    break
            if len(face) >= 3:
                faces.append(face)
    return faces


# ── components ───────────────────────────────────────────────────────────────

def connected_components(segments: list[Segment]) -> list[list[Segment]]:
    """Split line-work into disconnected networks.

    Drawings hold several unrelated closed shapes in the same layers. On the
    1234 Example garage the new-work side splits into a wall network
    (A-WALL + A-FLOR, 65 segments) and a SEPARATE pure-A-FLOR loop of 20
    segments sitting ~12 in outside it — the building outline. Measured as one
    soup they produce a single 783 sf answer with no way to tell which shape it
    came from; separated, the outline and the rooms are distinguishable.
    """
    adj: dict[Point, set[Point]] = {}
    for a, b in segments:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen: set[Point] = set()
    groups: list[set[Point]] = []
    for n in adj:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            c = stack.pop()
            if c in comp:
                continue
            comp.add(c)
            seen.add(c)
            stack.extend(adj[c] - comp)
        groups.append(comp)
    out: list[list[Segment]] = []
    for g in groups:
        segs = [(a, b) for a, b in segments if a in g and b in g]
        if segs:
            out.append(segs)
    out.sort(key=len, reverse=True)
    return out


# ── public entry point ───────────────────────────────────────────────────────

#: A cleanly-closed face is only preferred over a larger bridged one when it is at
#: least this fraction of the largest face found. Below it, "closed without
#: guessing" is describing a duct chase, not the building.
CLEAN_PREFERENCE_FLOOR = 0.60


#: Half-degree either side of square. Survey-derived line-work is never exactly
#: orthogonal, and the drawing itself may be rotated on the sheet.
_SQUARE_TOL_DEG = 1.5


def _edges(poly: list[Point]):
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        L = math.hypot(x2 - x1, y2 - y1)
        if L > 0:
            yield math.atan2(y2 - y1, x2 - x1) % (math.pi / 2), L


def _dominant_axis(poly: list[Point]) -> float:
    """The angle the building is drawn at, in radians, modulo a quarter turn.

    Plans are routinely rotated to the site grid — 1234 Example is drawn on a
    skewed lot, so NOT ONE of its 65 wall segments is axis-aligned to the file.
    Testing squareness against the file's axes would call every real building
    non-rectilinear, so squareness is tested against the building's own axis.
    Weighted by edge length, because long walls set the grid and short jogs
    follow it.
    """
    edges = list(_edges(poly))
    if not edges:
        return 0.0
    tol = math.radians(_SQUARE_TOL_DEG)
    best_a, best_w = edges[0][0], -1.0
    for a, _ in edges:
        w = sum(L for b, L in edges if _axis_delta(b, a) <= tol)
        if w > best_w:
            best_a, best_w = a, w
    return best_a


def _axis_delta(a: float, b: float) -> float:
    """Smallest angle between two directions taken modulo a quarter turn."""
    d = abs(a - b) % (math.pi / 2)
    return min(d, math.pi / 2 - d)


def drop_collinear(poly: list[Point], tol_deg: float = 1.0) -> list[Point]:
    """Remove vertices that do not turn.

    A traced outline carries a vertex wherever two source segments met, which on
    a wall split by three windows is several points along one straight run. They
    are not corners and must not be counted or trimmed.
    """
    if len(poly) < 3:
        return list(poly)
    # Collapse repeated points FIRST. Testing the turn at a duplicate gives a
    # zero-length edge on both sides of it, which discards the real corner as
    # well as the duplicate.
    poly = [p for i, p in enumerate(poly)
            if math.dist(p, poly[(i - 1) % len(poly)]) > 1e-9]
    if len(poly) < 3:
        return list(poly)
    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        ax, ay = poly[(i - 1) % n]
        bx, by = poly[i]
        cx, cy = poly[(i + 1) % n]
        v1 = (bx - ax, by - ay)
        v2 = (cx - bx, cy - by)
        if math.hypot(*v1) == 0 or math.hypot(*v2) == 0:
            continue
        ang = abs(math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0],
                                          v1[0] * v2[0] + v1[1] * v2[1])))
        if ang > tol_deg:
            out.append((bx, by))
    return out


def classify_corners(poly: list[Point]) -> dict:
    """Count and classify the corners of a traced outline.

    Returns external/internal counts, whether the outline is rectilinear about
    its own axis, and whether the rectilinear invariant holds.

    ⚠️ The invariant is the useful part. For ANY simple closed rectilinear
    polygon, external - internal == 4, whatever its shape. A takeoff reporting
    six external and zero internal corners is not describing an unusual building;
    it is describing a trace that missed the articulation.
    """
    pts = drop_collinear(poly)
    n = len(pts)
    if n < 4:
        return {"external": 0, "internal": 0, "rectilinear": False,
                "invariant_ok": False, "vertices": []}

    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                for i in range(n))
    ccw = area2 > 0

    axis = _dominant_axis(pts)
    rect = True
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if math.hypot(x2 - x1, y2 - y1) == 0:
            continue
        if _axis_delta(math.atan2(y2 - y1, x2 - x1), axis) > math.radians(_SQUARE_TOL_DEG):
            rect = False
            break

    ext = inn = 0
    verts = []
    for i in range(n):
        ax, ay = pts[(i - 1) % n]
        bx, by = pts[i]
        cx, cy = pts[(i + 1) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        is_ext = (cross > 0) == ccw
        ext += is_ext
        inn += not is_ext
        verts.append({"x": round(bx, 3), "y": round(by, 3),
                      "kind": "external" if is_ext else "internal"})

    return {"external": ext, "internal": inn, "rectilinear": rect,
            "invariant_ok": (ext - inn == 4) if rect else None,
            "vertices": verts}


def building_footprint(segments: list[Segment], u: float,
                       snap_tol_in: float = 2.0,
                       max_gap_in: float = 60.0,
                       min_area_sf: float = 20.0) -> Optional[Footprint]:
    """Largest enclosed region from line-work, chosen with provenance.

    `u` converts one drawing unit to one inch, so tolerances stay physical: 2 in
    of snap, and openings up to 60 in (a wide double door) get bridged.

    The line-work is split into disconnected networks FIRST. A drawing routinely
    carries the building outline and the wall network as separate shapes — on the
    1234 Example garage the outline is a pure closed loop 12 in outside the
    walls. Measuring the union of both gives one number with no provenance;
    measuring each network separately says which shape produced it.

    Preference order:
      1. the largest face from a network that closes WITHOUT bridging, since an
         already-closed outline involves no guessing about loose ends — but only
         if it is at least ``CLEAN_PREFERENCE_FLOOR`` of the largest face found at
         all. A clean closure is evidence about *provenance*, not about *scale*:
         on the Maple sheets a 46 sf duct chase closed cleanly and beat the
         actual 700 sf building outline, which needed one bridge.
      2. otherwise the largest face from any network.
    Interior faces from the wall network are reported alongside as rooms.
    """
    if not segments:
        return None
    tol = snap_tol_in / u if u else snap_tol_in
    gap = max_gap_in / u if u else max_gap_in

    snapped, merged = snap_segments(segments, tol)
    comps = connected_components(snapped)
    if not comps:
        return None

    cands: list[tuple[bool, float, list[Point], int, int, list[float]]] = []
    for idx, comp in enumerate(comps):
        # try the component as-is first; only bridge if it will not close
        for bridged, n_bridged in ((comp, 0), bridge_openings(comp, gap)):
            faces = extract_faces(bridged)
            pos = sorted((_shoelace(f), f) for f in faces if _shoelace(f) > 0)
            if not pos:
                if n_bridged == 0:
                    continue
                break
            area_u, poly = pos[-1]
            area_sf = area_u * (u * u) / 144.0
            if area_sf < min_area_sf:
                if n_bridged == 0:
                    continue
                break
            rooms = sorted(
                (round(a * (u * u) / 144.0, 1) for a, _ in pos[:-1]
                 if a * (u * u) / 144.0 >= min_area_sf), reverse=True)
            closed_without_bridging = (n_bridged == 0)
            cands.append((closed_without_bridging, area_sf, poly, idx, n_bridged, rooms))
            break

    # ── whole-network bridge ────────────────────────────────────────────────
    # `connected_components` runs before any bridging, so a loop broken at TWO
    # or more openings arrives here as two or more chains and no component can
    # ever close — measured: 0 or 1 opening closes correctly, 2 gives nothing,
    # 3 gives nothing. Every real building has many openings, so the wall
    # network essentially never closed and the tracer silently fell back to
    # whatever simple outline existed, which is how a 12-corner building was
    # measured as a 4-corner box.
    #
    # Bridging the whole network is safe to attempt because `bridge_openings`
    # only joins loose ends whose connector runs ALONG one of the walls it
    # joins; it will not stitch an outline to unrelated line-work facing it.
    # It is used only when it recovers something the per-component pass could
    # not, and it is recorded as weaker provenance.
    cross = None
    if len(comps) > 1:
        whole, n_whole = bridge_openings(snapped, gap)
        if n_whole:
            pos = sorted((_shoelace(f), f) for f in extract_faces(whole)
                         if _shoelace(f) > 0)
            if pos:
                area_u, poly_w = pos[-1]
                area_w = area_u * (u * u) / 144.0
                if area_w >= min_area_sf:
                    cross = (False, area_w, poly_w, -1, n_whole, [])

    if cross is not None:
        best_per_comp = max((c[1] for c in cands), default=0.0)
        # Only when it recovers materially more than the per-component pass, so
        # a file that already traces cleanly keeps the answer it has today.
        if cross[1] >= max(best_per_comp * 1.15, min_area_sf):
            cands = [cross]

    if not cands:
        return None
    biggest = max(c[1] for c in cands)
    clean = [c for c in cands if c[0] and c[1] >= CLEAN_PREFERENCE_FLOOR * biggest]
    best = max(clean, key=lambda c: c[1]) if clean else max(cands, key=lambda c: c[1])
    clean, area_sf, poly, idx, n_bridged, rooms = best

    # Rooms come from the WALL network, which is usually a different component
    # from the outline: the outline is one closed loop with a single face, while
    # the walls subdivide the interior. Collect enclosed faces from every other
    # component so per-room quantities are available, not just the envelope.
    for j, comp in enumerate(comps):
        if j == idx:
            continue
        bridged, _ = bridge_openings(comp, gap)
        for f in extract_faces(bridged):
            a = _shoelace(f)
            if a <= 0:
                continue
            a_sf = a * (u * u) / 144.0
            # a room must be meaningful and cannot exceed the envelope
            if min_area_sf <= a_sf <= area_sf * 1.01:
                rooms.append(round(a_sf, 1))
    rooms = sorted(set(rooms), reverse=True)
    per_in = sum(math.dist(poly[i], poly[(i + 1) % len(poly)])
                 for i in range(len(poly))) * u
    corners = classify_corners(poly)
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    bbox_per_lf = round(2.0 * ((max(xs) - min(xs)) + (max(ys) - min(ys))) * u / 12.0, 1)

    return Footprint(
        external_corners=corners["external"],
        internal_corners=corners["internal"],
        corner_invariant_ok=bool(corners["invariant_ok"]),
        rectilinear=corners["rectilinear"],
        bbox_perimeter_lf=bbox_per_lf,
        cross_component_bridged=(idx == -1),
        area_sf=round(area_sf, 1),
        perimeter_lf=round(per_in / 12.0, 1),
        polygon=poly,
        bridged_gaps=n_bridged,
        snapped_nodes=merged,
        faces_found=len(comps),
        component_index=idx,
        components=len(comps),
        needed_bridging=not clean,
        interior_faces_sf=rooms,
    )
