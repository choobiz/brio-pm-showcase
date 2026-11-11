"""Reconcile many sheet takeoffs into one building takeoff.

A takeoff run per sheet is not a project takeoff. A drawing set shows the same
building several times — a basement on one sheet and a level 1 on the next, both
storeys together on a third, the footprint again on the site plan — and the sheets
disagree, because they were drawn at different times for different purposes.

Summing sheets is the obvious thing and it is wrong twice over. It double-counts
any storey drawn on more than one sheet, and it silently absorbs a disagreement
that should have been surfaced: if the basement measures 1293 sf on A6 and the
site plan puts the footprint at 1150, somebody needs to know before that number is
priced.

So: group views by the STOREY they show, keep one figure per storey, and report
every disagreement as a finding rather than averaging it away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: Sheet roles, inferred from the filename or the view title.
ROLE_PATTERNS: list[tuple[str, str]] = [
    ("site_plan", r"\bsite\s*plan\b|\bA-?0?2\b"),
    ("section", r"\bsection|\belevation|\bA-?1[2-9]\b"),
    ("roof_plan", r"\broof\s*plan\b"),
    ("floor_plan", r"\bfloor\s*plan|\bplan\b"),
]

#: Storey names, in building order. The key is what two sheets must agree on to be
#: describing the same storey.
STOREY_PATTERNS: list[tuple[str, str]] = [
    ("basement", r"\bbasement|\bcellar|\blower\s*level\b"),
    ("level_1", r"\blevel\s*0?1\b|\bmain\s*floor|\bground\s*floor|\bfirst\s*floor"),
    ("level_2", r"\blevel\s*0?2\b|\bsecond\s*floor|\bupper\s*floor"),
    ("level_3", r"\blevel\s*0?3\b|\bthird\s*floor"),
    ("roof", r"\broof\b"),
]

#: Two figures for the same storey are the same figure within this.
AGREE_TOL = 0.05


def classify_sheet(name: str) -> str:
    n = (name or "").replace("_", " ")
    for role, pat in ROLE_PATTERNS:
        if re.search(pat, n, re.I):
            return role
    return "unknown"


def storeys_named(text: Optional[str]) -> list[str]:
    """Every storey a piece of text names, in building order."""
    if not text:
        return []
    return [k for k, pat in STOREY_PATTERNS if re.search(pat, text, re.I)]


def classify_storey(*texts: Optional[str]) -> Optional[str]:
    """The single storey these texts name, or None if they name none or several.

    A filename is a useful fallback for a sheet showing ONE plan and actively
    harmful for a sheet showing two: "109 Maple A6 - BASEMENT & LEVEL 1 FLOOR
    PLANS" names both, and matching the first pattern would file the level 1 view
    under "basement". Both views then key to the same storey, one overwrites the
    other, and the building loses a floor. Ambiguity has to return None so the
    caller keys by position instead.
    """
    for t in texts:
        found = storeys_named(t)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


def building_key(name: str) -> str:
    """Which building a sheet belongs to.

    A civic number at the start of the filename is the strongest signal on a
    multi-building site — "107 Maple ... A6" and "109 Maple ... A6" are two
    different basements, and merging them by storey would silently halve a
    three-unit project.
    """
    m = re.match(r"\s*(\d{2,5})\b", name or "")
    return m.group(1) if m else "default"


@dataclass
class Storey:
    building: str
    storey: Optional[str]
    title: Optional[str]
    sources: list[str] = field(default_factory=list)
    schedule_sf: Optional[float] = None
    scheduled_rooms: int = 0
    dwelling_units: int = 0
    mirrored_pct: Optional[float] = None
    rooms_by_name: dict[str, dict] = field(default_factory=dict)
    envelope_sf: Optional[float] = None
    wall_lf: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    sizes: dict[str, dict[str, int]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


@dataclass
class ProjectTakeoff:
    audit: Any = None                       # SetAudit — coverage, read this FIRST
    storeys: list[Storey] = field(default_factory=list)
    buildings: dict[str, dict[str, Any]] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)


def _agree(a: Optional[float], b: Optional[float], tol: float = AGREE_TOL) -> bool:
    if not a or not b:
        return True
    return abs(a - b) <= tol * max(a, b)


def reconcile(sheets: Iterable[tuple[str, Any]]) -> ProjectTakeoff:
    """Fold ``(sheet_name, Takeoff)`` pairs into one building takeoff.

    Views that name their storey are keyed on it. A view that does not is keyed by
    position within its sheet, which keeps two plans on one sheet apart without
    pretending to know which is which.
    """
    out = ProjectTakeoff()
    slots: dict[tuple[str, str], Storey] = {}

    for name, t in sheets:
        role = classify_sheet(name)
        if role in ("section", "roof_plan"):
            # Sections carry heights, not areas. Counting their line-work as floor
            # area is how a building grows a storey it does not have.
            out.unplaced.append(f"{name}: {role}, no area contribution")
            continue
        bld = building_key(name)
        for v in getattr(t, "views", []) or []:
            if not v.get("included", True):
                continue
            storey = classify_storey(v.get("title"), name)
            key = (bld, storey or f"{name}#view{v['index']}")
            if storey is None and len(storeys_named(name)) > 1:
                out.findings.append(
                    f"{bld}: '{name}' names {len(storeys_named(name))} storeys but "
                    f"view {v['index']} carries no title of its own — kept as a "
                    f"separate storey. Confirm which floor it is.")
            st = slots.get(key)
            if st is None:
                st = Storey(building=bld, storey=storey, title=v.get("title"))
                slots[key] = st
                out.storeys.append(st)
            _merge_view(st, name, v, out)

    for st in out.storeys:
        st.sources = sorted(set(st.sources))
    _totals(out)
    return out


def _merge_view(st: Storey, sheet: str, v: dict, out: ProjectTakeoff) -> None:
    st.sources.append(sheet)
    sched = v.get("room_schedule_summary") or {}
    new_sf = sched.get("total_sf")
    env = v.get("envelope_sf") or v.get("geom_area_sf")

    if st.schedule_sf is not None and new_sf:
        if not _agree(st.schedule_sf, new_sf):
            msg = (f"{st.building} {st.storey or st.title or 'view'}: room schedule "
                   f"{st.schedule_sf} sf ({', '.join(st.sources[:-1])}) vs {new_sf} sf "
                   f"({sheet}) — {abs(new_sf - st.schedule_sf) / max(st.schedule_sf, 1) * 100:.0f}% apart")
            st.conflicts.append(msg)
            out.findings.append(msg)
        st.schedule_sf = max(st.schedule_sf, new_sf)      # keep the fuller sheet
    elif new_sf:
        st.schedule_sf = new_sf
    st.scheduled_rooms = max(st.scheduled_rooms, sched.get("rooms") or 0)
    units, mirrored = _units_in_view(v)
    st.dwelling_units = max(st.dwelling_units, units)
    if mirrored is not None:
        st.mirrored_pct = max(st.mirrored_pct or 0.0, mirrored)
    for nm, agg in (sched.get("by_name") or {}).items():
        cur = st.rooms_by_name.get(nm)
        if cur is None or agg["area_sf"] > cur["area_sf"]:
            st.rooms_by_name[nm] = dict(agg)

    if st.envelope_sf is not None and env:
        if not _agree(st.envelope_sf, env):
            msg = (f"{st.building} {st.storey or st.title or 'view'}: measured envelope "
                   f"{st.envelope_sf} sf vs {env} sf ({sheet})")
            st.conflicts.append(msg)
            out.findings.append(msg)
        st.envelope_sf = max(st.envelope_sf, env)
    elif env:
        st.envelope_sf = env

    for layer, lf in (v.get("wall_lf") or {}).items():
        st.wall_lf[layer] = max(st.wall_lf.get(layer, 0.0), lf)
    for cat, blocks in (v.get("counts") or {}).items():
        st.counts[cat] = max(st.counts.get(cat, 0), sum(blocks.values()))
    for cat, ss in (v.get("sizes") or {}).items():
        cur = st.sizes.setdefault(cat, {})
        for k, n in ss.items():
            cur[k] = max(cur.get(k, 0), n)


def _units_in_view(v: dict) -> tuple[int, Optional[float]]:
    """How many dwellings this plan shows, and how symmetric it is.

    A kitchen is one dwelling. It is the one room a self-contained unit cannot do
    without and cannot have two of, which makes counting kitchens a better unit
    count than counting front doors or reading the address off the sheet.

    Symmetry is reported alongside because a side-by-side duplex is drawn as two
    mirrored halves: splitting the room tags about the plan's centre line and
    comparing the two sets of room TYPES says so directly. 107's basement came back
    9 rooms each side with 89% of types matched, its level 1 100% — which is what a
    duplex looks like and what a single dwelling does not.
    """
    sched = v.get("room_schedule") or []
    if not sched:
        return 0, None
    kitchens = sum(1 for r in sched
                   if "KITCHEN" in (r.get("name") or "").upper())
    xs = [r["at"][0] for r in sched]
    if len(xs) < 4:
        return kitchens, None
    mid = (min(xs) + max(xs)) / 2.0
    left = [r for r in sched if r["at"][0] < mid]
    right = [r for r in sched if r["at"][0] >= mid]
    if not left or not right:
        return kitchens, None
    ln: dict[str, int] = {}
    rn: dict[str, int] = {}
    for r in left:
        k = (r.get("name") or "?").upper()
        ln[k] = ln.get(k, 0) + 1
    for r in right:
        k = (r.get("name") or "?").upper()
        rn[k] = rn.get(k, 0) + 1
    shared = sum(min(ln.get(k, 0), rn.get(k, 0)) for k in set(ln) | set(rn))
    return kitchens, round(shared / max(1, min(len(left), len(right))), 2)


def _totals(out: ProjectTakeoff) -> None:
    for st in out.storeys:
        b = out.buildings.setdefault(st.building, {
            "storeys": 0, "schedule_sf": 0.0, "envelope_sf": 0.0,
            "rooms": 0, "dwelling_units": 0, "counts": {}, "wall_lf": {}})
        b["storeys"] += 1
        b["schedule_sf"] = round(b["schedule_sf"] + (st.schedule_sf or 0.0), 1)
        b["envelope_sf"] = round(b["envelope_sf"] + (st.envelope_sf or 0.0), 1)
        b["rooms"] += st.scheduled_rooms
        b["dwelling_units"] = max(b.get("dwelling_units", 0), st.dwelling_units)
        for cat, n in st.counts.items():
            b["counts"][cat] = b["counts"].get(cat, 0) + n
        for layer, lf in st.wall_lf.items():
            b["wall_lf"][layer] = round(b["wall_lf"].get(layer, 0.0) + lf, 1)

    out.totals = {
        "buildings": len(out.buildings),
        "storeys": len(out.storeys),
        "schedule_sf": round(sum(b["schedule_sf"] for b in out.buildings.values()), 1),
        "envelope_sf": round(sum(b["envelope_sf"] for b in out.buildings.values()), 1),
        "rooms": sum(b["rooms"] for b in out.buildings.values()),
        "dwelling_units": sum(b["dwelling_units"] for b in out.buildings.values()),
        "counts": {},
        "wall_lf": {},
    }
    for b in out.buildings.values():
        for cat, n in b["counts"].items():
            out.totals["counts"][cat] = out.totals["counts"].get(cat, 0) + n
        for layer, lf in b["wall_lf"].items():
            out.totals["wall_lf"][layer] = round(
                out.totals["wall_lf"].get(layer, 0.0) + lf, 1)

    for st in out.storeys:
        if st.storey is None and not any(
                len(storeys_named(src)) > 1 for src in st.sources):
            out.findings.append(
                f"{st.building}: a view on {', '.join(st.sources)} names no storey — "
                f"kept separate, but it may duplicate another sheet. Confirm.")
        if st.schedule_sf and st.envelope_sf:
            r = st.envelope_sf / st.schedule_sf
            if not (0.95 <= r <= 1.5):
                out.findings.append(
                    f"{st.building} {st.storey or 'view'}: envelope {st.envelope_sf} sf "
                    f"is {r:.2f}x the room schedule {st.schedule_sf} sf.")
