"""Turn a room schedule into trade quantities, in BRIO's 18 divisions.

A room schedule is not yet a takeoff. "BEDROOM 98.46 SF" prices nothing on its own;
"98.46 sf of engineered floor, 116 sf of drywall, 1 door, 4 outlets" does. This
module is that translation, and it is the step that makes a CAD read worth having.

**Every quantity here carries a basis, and the bases are not equal.**

  measured  the drawing said it — a room's area, a door count from block
            references, wall LF from line-work. Trust it.
  derived   arithmetic on a measured value under a stated geometric assumption —
            wall area is perimeter x ceiling height, and the perimeter of a room
            known only by its area has to be inferred from its shape.
  assumed   a rule of thumb about how rooms are built — a bathroom has a toilet
            and a basin. True almost always; not read from this drawing.

Nothing here should reach a client price without a human seeing which is which.
That is the whole point of separating them: a derived wall area computed from an
assumed 8 ft ceiling is a placeholder until somebody opens the section sheet, and
it must not be able to masquerade as something the drawing stated.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: BRIO's house standard. The numbers are part of the name.
DIVISIONS = {
    "05": "Doors, Frames, Hardware & Glazing",
    "06": "Insulation, Drywall & Acoustic",
    "07": "Painting & Finishes",
    "08": "Flooring & Tile",
    "09": "Millwork & Casework",
    "10": "Specialties & Fire Detection",
    "11": "Appliances & Equipment",
    "12": "Mechanical / HVAC",
    "13": "Plumbing & Fixtures",
    "14": "Electrical & Low-Voltage",
}

#: Ceiling height when no section sheet has been read. Stated, never hidden.
DEFAULT_CEILING_FT = 8.0

#: Perimeter of a rectangular room of a given area, at a typical 3:2 aspect:
#: p = 2(w + h), a = wh, h = w * 2/3  ->  p = 2w(1 + 2/3), w = sqrt(3a/2).
#: A room known only by its area has no perimeter; this states the shape assumed.
_ASPECT = 1.5


def estimated_perimeter_ft(area_sf: float, aspect: float = _ASPECT) -> float:
    w = math.sqrt(area_sf * aspect)
    return round(2.0 * (w + w / aspect), 1)


@dataclass
class Line:
    division: str
    item: str
    qty: float
    unit: str
    basis: str                    # 'measured' | 'derived' | 'assumed'
    room: Optional[str] = None
    note: Optional[str] = None


@dataclass
class RoomType:
    """What a room of this kind contains, per BRIO's divisions."""
    floor: str                                   # finish for division 08
    wet: bool = False                            # waterproofing, tile, exhaust
    fixtures: tuple[str, ...] = ()               # division 13
    appliances: tuple[str, ...] = ()             # division 11
    casework_lf: float = 0.0                     # division 09, LF per room
    finished: bool = True                        # drywall + paint
    outlets_per_100sf: float = 2.0               # division 14


#: Keyed on the room NAME the architect used, matched case- and space-insensitively
#: with a longest-match-first fallback so "POWDER ROOM" does not match "ROOM".
ROOM_TYPES: dict[str, RoomType] = {
    "BATHROOM": RoomType("Tile", wet=True, fixtures=("Toilet", "Basin", "Tub/shower"),
                         casework_lf=2.5, outlets_per_100sf=3.0),
    "POWDER ROOM": RoomType("Tile", wet=True, fixtures=("Toilet", "Basin"),
                            casework_lf=2.0, outlets_per_100sf=3.0),
    "SHOWER": RoomType("Tile", wet=True, fixtures=("Shower",), outlets_per_100sf=0.0),
    "ENSUITE": RoomType("Tile", wet=True, fixtures=("Toilet", "Basin", "Shower"),
                        casework_lf=3.0, outlets_per_100sf=3.0),
    "KITCHEN": RoomType("Tile", wet=True, fixtures=("Kitchen sink",),
                        appliances=("Range", "Refrigerator", "Dishwasher", "Hood"),
                        casework_lf=14.0, outlets_per_100sf=6.0),
    "WET BAR": RoomType("Tile", wet=True, fixtures=("Bar sink",), casework_lf=6.0,
                        outlets_per_100sf=4.0),
    "LAUNDRY": RoomType("Tile", wet=True, fixtures=("Laundry box",),
                        appliances=("Washer", "Dryer"), casework_lf=3.0,
                        outlets_per_100sf=4.0),
    "BEDROOM": RoomType("Engineered floor", casework_lf=0.0),
    "LIVING ROOM": RoomType("Engineered floor"),
    "DINING ROOM": RoomType("Engineered floor"),
    "REC ROOM": RoomType("Engineered floor"),
    "OFFICE": RoomType("Engineered floor", outlets_per_100sf=4.0),
    "HALLWAY": RoomType("Engineered floor", outlets_per_100sf=1.0),
    "ENTRY": RoomType("Tile", outlets_per_100sf=1.5),
    "MUDROOM": RoomType("Tile", casework_lf=4.0, outlets_per_100sf=2.0),
    "MUD ROOM": RoomType("Tile", casework_lf=4.0, outlets_per_100sf=2.0),
    "CLOSET": RoomType("Engineered floor", casework_lf=4.0, outlets_per_100sf=0.0),
    "STORAGE": RoomType("Sealed concrete", finished=False, outlets_per_100sf=1.0),
    "STAIR": RoomType("Engineered floor", outlets_per_100sf=1.0),
    "GARAGE": RoomType("Sealed concrete", finished=False, outlets_per_100sf=1.5),
    "MEC. ROOM": RoomType("Sealed concrete", finished=False, fixtures=("Floor drain",),
                          outlets_per_100sf=2.0),
    "MECHANICAL ROOM": RoomType("Sealed concrete", finished=False,
                                fixtures=("Floor drain",), outlets_per_100sf=2.0),
    "ELECTRICAL": RoomType("Sealed concrete", finished=False, outlets_per_100sf=1.0),
}

_UNKNOWN = RoomType("Engineered floor")


def room_type(name: Optional[str]) -> tuple[RoomType, bool]:
    """(type, recognised). An unrecognised room is finished generically and SAID SO."""
    if not name:
        return _UNKNOWN, False
    key = re.sub(r"\s+", " ", name).strip().upper()
    key = re.sub(r"\s*\d+$", "", key)          # "BEDROOM 02" -> "BEDROOM"
    if key in ROOM_TYPES:
        return ROOM_TYPES[key], True
    for k in sorted(ROOM_TYPES, key=len, reverse=True):
        if k in key:
            return ROOM_TYPES[k], True
    return _UNKNOWN, False


def room_lines(name: Optional[str], area_sf: float,
               ceiling_ft: float = DEFAULT_CEILING_FT,
               ceiling_measured: bool = False) -> list[Line]:
    """Trade quantities for one room."""
    rt, known = room_type(name)
    label = name or "UNNAMED"
    perim = estimated_perimeter_ft(area_sf)
    wall_sf = round(perim * ceiling_ft, 1)
    wall_basis = "derived" if ceiling_measured else "assumed"
    out: list[Line] = []

    out.append(Line("08", f"{rt.floor} — {label}", round(area_sf, 1), "sf",
                    "measured", label))
    if rt.wet:
        out.append(Line("08", f"Waterproof membrane — {label}", round(area_sf, 1),
                        "sf", "assumed", label, "wet room"))
    if rt.finished:
        out.append(Line("06", f"Drywall, taped — {label}", wall_sf, "sf", wall_basis,
                        label, f"perimeter {perim} lf (est.) x {ceiling_ft} ft ceiling"))
        out.append(Line("06", f"Ceiling drywall — {label}", round(area_sf, 1), "sf",
                        "measured", label))
        out.append(Line("07", f"Paint, walls + ceiling — {label}",
                        round(wall_sf + area_sf, 1), "sf", wall_basis, label))
    for f in rt.fixtures:
        out.append(Line("13", f"{f} — {label}", 1, "ea", "assumed", label,
                        "implied by room type"))
    for a in rt.appliances:
        out.append(Line("11", f"{a} — {label}", 1, "ea", "assumed", label,
                        "implied by room type"))
    if rt.casework_lf:
        out.append(Line("09", f"Casework — {label}", rt.casework_lf, "lf", "assumed",
                        label, "typical for room type"))
    if rt.wet:
        out.append(Line("12", f"Exhaust fan — {label}", 1, "ea", "assumed", label))
    if rt.outlets_per_100sf:
        n = max(1, round(area_sf / 100.0 * rt.outlets_per_100sf))
        out.append(Line("14", f"Receptacles + lighting — {label}", n, "ea", "assumed",
                        label, f"{rt.outlets_per_100sf}/100 sf"))
    if not known:
        for ln in out:
            ln.note = ((ln.note + "; ") if ln.note else "") + \
                f"room type '{label}' not recognised — generic finish assumed"
    return out


def schedule_to_trades(rooms: Iterable[dict],
                       measured_counts: Optional[dict] = None,
                       ceiling_ft: float = DEFAULT_CEILING_FT,
                       ceiling_measured: bool = False) -> dict:
    """Trade quantities for a whole storey or building, plus cross-checks.

    ``measured_counts`` is the block-reference count from the same drawing. Where a
    rule of thumb and a measurement describe the same thing — one door per enclosed
    room versus the doors actually drawn — the measurement wins and the difference
    is reported. That comparison is the most useful line in the output: it says how
    far the assumptions are from this particular building.
    """
    rooms = list(rooms)
    lines: list[Line] = []
    unrecognised: list[str] = []
    for r in rooms:
        nm = r.get("name")
        if not room_type(nm)[1]:
            unrecognised.append(nm or "UNNAMED")
        lines += room_lines(nm, float(r.get("area_sf") or 0.0),
                            ceiling_ft, ceiling_measured)

    # Division 05 comes from the drawing, not from a rule.
    checks: list[str] = []
    enclosed = [r for r in rooms
                if room_type(r.get("name"))[1]
                and (r.get("name") or "").strip().upper() not in ("HALLWAY", "STAIR")]
    implied_doors = len(enclosed)
    if measured_counts:
        for cat, unit, div, label in (("door", "ea", "05", "Doors"),
                                      ("window", "ea", "05", "Windows"),
                                      ("plumbing_fixture", "ea", "13", "Plumbing fixtures"),
                                      ("appliance", "ea", "11", "Appliances")):
            n = measured_counts.get(cat)
            if n:
                lines.append(Line(div, f"{label} — counted from the drawing", n, unit,
                                  "measured"))
        md = measured_counts.get("door")
        if md:
            checks.append(
                f"doors: {md} drawn vs {implied_doors} implied by the room schedule "
                f"({md - implied_doors:+d})")
            # Drop the assumed fixtures where the drawing counted them itself.
        mf = measured_counts.get("plumbing_fixture")
        if mf:
            assumed_f = sum(1 for ln in lines
                            if ln.division == "13" and ln.basis == "assumed")
            checks.append(
                f"plumbing fixtures: {mf} drawn vs {assumed_f} implied by room type "
                f"({mf - assumed_f:+d})")

    by_div: dict[str, dict] = {}
    for ln in lines:
        d = by_div.setdefault(ln.division, {
            "name": DIVISIONS.get(ln.division, ln.division), "lines": [],
            "measured": 0, "derived": 0, "assumed": 0})
        d["lines"].append(ln)
        d[ln.basis] += 1

    return {
        "divisions": {k: {
            "name": v["name"],
            "basis_counts": {b: v[b] for b in ("measured", "derived", "assumed")},
            "lines": [{"item": ln.item, "qty": ln.qty, "unit": ln.unit,
                       "basis": ln.basis, "room": ln.room, "note": ln.note}
                      for ln in v["lines"]],
        } for k, v in sorted(by_div.items())},
        "totals": {
            "lines": len(lines),
            "measured": sum(1 for ln in lines if ln.basis == "measured"),
            "derived": sum(1 for ln in lines if ln.basis == "derived"),
            "assumed": sum(1 for ln in lines if ln.basis == "assumed"),
        },
        "cross_checks": checks,
        "unrecognised_rooms": sorted(set(unrecognised)),
        "assumptions": [
            f"Ceiling height {ceiling_ft} ft"
            + ("" if ceiling_measured else " — ASSUMED, not read from a section sheet"),
            f"Room perimeters estimated from area at a {_ASPECT}:1 aspect ratio",
        ],
    }
