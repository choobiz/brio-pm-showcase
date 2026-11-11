"""Read the architect's own room schedule out of the drawing.

Revit and its peers tag every room with a name and an area on the AIA room
identification layer (``A-AREA-IDEN``), as two pieces of text a few inches apart:

    (-226, -83)  '198.58 SF'
    (-231, -77)  'LIVING ROOM'

That is a complete room schedule — name, area, position, per room — and it is
authoritative data in the file, in exactly the way block ATTRIBUTES are
authoritative for opening sizes. It is not an approximation to be improved on.

This matters because geometric room-splitting is not reliably solvable from wall
line-work on real architectural sets: partitions are drawn with gaps, doorways are
often unmarked, and on some sheets whole runs of interior wall simply are not
there. Measured against these very drawings, every purely geometric split tried —
morphological closing, centreline reconstruction, distance-transform watershed —
came back with a median per-room error above 25%.

So the division of labour is: the drawing states the rooms, and geometry
INDEPENDENTLY CHECKS them, by measuring the building envelope and comparing it to
the schedule's total. A disagreement is a real finding for the reviewer. Agreement
is the strongest verification available without a site visit.

When a drawing carries no tags — the case for a client who wants takeoff from CAD
with no schedule of any kind — there is no schedule to read, and the caller falls
back to measured rooms with their convergence warning attached.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

Point = tuple[float, float]

#: Layers that carry room tags. Prefix match on the AIA room-identification group.
TAG_LAYERS = ("A-AREA-IDEN", "A-AREA", "A-ANNO-ROOM", "Q-SPCQ")

#: A name belonging to an area figure sits within this of it, in inches.
NAME_RADIUS_IN = 36.0

_AREA_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(?:SF|sq\.?\s*ft\.?)\s*$", re.I)
#: Text that states an area but is a note about the building, not a room.
_NOT_A_ROOM = re.compile(r"[+=]|\ballow(ed|able)\b|\bcoverage\b|\btotal\b|\bFSR\b", re.I)
#: Names that are areas but not rooms anyone finishes as one.
_NON_ROOM_NAMES = ("SETBACK", "LOT", "PARCEL", "SITE")

#: Text that is a datum, a dimension or another unit of the SAME area — never a
#: room name. Without this the nearest-text rule names a garage "FF 214.00", which
#: is the finished-floor elevation printed beside it.
_NOT_A_NAME = re.compile(
    r"^\s*(?:[-+0-9.,'\"/ ]+|"                    # bare numbers, feet-inches
    r"(?:FF|EL|EL\.|T\.O\.|B\.O\.|FFE)\b.*|"      # datums
    r".*\b(?:M2|M²|SQ\.?\s*M|SM)\b.*|"            # the same area, metric
    r"[A-Z]?\d+[A-Za-z]?)\s*$", re.I)


@dataclass
class ScheduledRoom:
    name: Optional[str]
    area_sf: float
    at: Point
    layer: str

    @property
    def named(self) -> bool:
        return bool(self.name)


def _texts(msp) -> list[tuple[str, Point, str]]:
    out = []
    for e in msp.query("MTEXT TEXT"):
        try:
            raw = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        except Exception:  # noqa: BLE001
            continue
        t = " ".join((raw or "").split())
        if t:
            out.append((t, (e.dxf.insert.x, e.dxf.insert.y), (e.dxf.layer or "").upper()))
    return out


def read_schedule(msp, inside: Optional[Callable[[Point], bool]] = None,
                  unit_scale: float = 1.0,
                  tag_layers: Iterable[str] = TAG_LAYERS,
                  name_radius_in: float = NAME_RADIUS_IN) -> list[ScheduledRoom]:
    """Room tags in this view: name, area and position.

    The tag layer is a strong signal but not a requirement — an office with its own
    layer scheme still gets its rooms read, because the *pattern* (an area figure
    with a name beside it) is what identifies a tag. Layer membership only breaks
    ties and suppresses stray text elsewhere on the sheet.
    """
    texts = _texts(msp)
    radius = name_radius_in / unit_scale if unit_scale else name_radius_in
    prefixes = tuple(p.upper() for p in tag_layers)

    rooms: list[ScheduledRoom] = []
    for t, p, layer in texts:
        m = _AREA_RE.match(t)
        if not m or _NOT_A_ROOM.search(t):
            continue
        if inside is not None and not inside(p):
            continue
        on_tag_layer = layer.startswith(prefixes)
        # Nearest text that is NOT itself an area figure — the room's name.
        best: Optional[tuple[float, str]] = None
        for other, q, olayer in texts:
            if q == p or _AREA_RE.match(other):
                continue
            if on_tag_layer and not olayer.startswith(prefixes):
                continue
            d = math.dist(p, q)
            if d <= radius and (best is None or d < best[0]):
                best = (d, other)
        name = best[1] if best else None
        if name and (_NOT_A_NAME.match(name)
                     or any(k in name.upper() for k in _NON_ROOM_NAMES)):
            name = None
        if not on_tag_layer and not name:
            continue          # a bare area figure off the tag layer is a note
        rooms.append(ScheduledRoom(name=name, area_sf=float(m.group(1)),
                                   at=p, layer=layer))
    return _drop_totals(rooms)


def _drop_totals(rooms: list[ScheduledRoom], tol: float = 0.02) -> list[ScheduledRoom]:
    """Remove an entry that states the SUM of the others.

    Architects routinely print a storey total in the same style as a room tag —
    1234 Example labels its footprint twice, "120 + 549 = 669" alongside
    "399.6 + 268". Keeping the total alongside its own parts doubles the floor
    area, and on Maple 107 A7 a 1084.4 sf storey total sat among twelve rooms.
    """
    # At least four entries, and only the largest, and only once. A summary
    # describes MANY rooms; with two or three, "one equals the sum of the rest" is
    # arithmetic coincidence — 300 = 200 + 100 — and dropping on it cascades until
    # the schedule is gone.
    if len(rooms) < 4:
        return rooms
    biggest = max(rooms, key=lambda x: x.area_sf)
    rest = sum(r.area_sf for r in rooms if r is not biggest)
    if rest > 0 and abs(biggest.area_sf - rest) <= tol * rest:
        return [r for r in rooms if r is not biggest]
    return rooms


def schedule_summary(rooms: list[ScheduledRoom]) -> dict:
    """Totals plus a by-name roll-up, which is what a takeoff actually prices."""
    by_name: dict[str, dict] = {}
    for r in rooms:
        key = (r.name or "UNNAMED").strip().upper()
        entry = by_name.setdefault(key, {"count": 0, "area_sf": 0.0})
        entry["count"] += 1
        entry["area_sf"] = round(entry["area_sf"] + r.area_sf, 1)
    return {
        "rooms": len(rooms),
        "named": sum(1 for r in rooms if r.named),
        "total_sf": round(sum(r.area_sf for r in rooms), 1),
        "by_name": dict(sorted(by_name.items(), key=lambda kv: -kv[1]["area_sf"])),
    }
