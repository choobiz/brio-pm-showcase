"""Audit a drawing set for what is MISSING before anything is measured.

A takeoff that silently prices two thirds of a building is worse than no takeoff,
because it looks finished. The Maple set is the example: sheet numbering runs
A2, A6, A7, A12 — and the building sections on A12/A14 name BASEMENT, Level 1,
Level 2 and Roof, while the only floor plans present are basement and level 1. The
level 2 plans are simply not in the upload. Every quantity downstream was for two
storeys of a three-storey building, and nothing said so.

The building states its own storey count. Sections and elevations label every
level they cut through, so the set can be checked against itself: the storeys the
sections name are what the building has, and the storeys the floor plans cover are
what the takeoff measured. The difference is the gap, and it is knowable before a
single quantity is produced.

This runs FIRST. Coverage is not a caveat printed under a total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: Level names as an architect writes them on a section, in building order.
LEVEL_PATTERNS: list[tuple[str, str]] = [
    ("basement", r"\bbasement\b|\bcellar\b|\blower\s*level\b"),
    ("level_1", r"\blevel\s*0?1\b|\bmain\s*floor\b|\bground\s*floor\b|\bfirst\s*floor\b"),
    ("level_2", r"\blevel\s*0?2\b|\bsecond\s*floor\b|\bupper\s*floor\b"),
    ("level_3", r"\blevel\s*0?3\b|\bthird\s*floor\b"),
    ("roof", r"\broof\s*(?:peak|eave|deck|line)?\b"),
]
LEVEL_ORDER = [k for k, _ in LEVEL_PATTERNS]

#: Sheet types a set needs before a takeoff is complete, and what each unlocks.
EXPECTED_TYPES: dict[str, str] = {
    "site_plan": "siting, setbacks, hard/soft landscaping quantities",
    "floor_plan": "room areas, openings, partition lengths — the takeoff itself",
    "elevation": "cladding areas and openings by face",
    "section": "storey heights, which turn wall lengths into wall AREAS",
    "schedule": "door and window sizes when the blocks carry no attributes",
    "roof_plan": "roof area, slope and drainage",
}

# Sheet titles are pluralised freely — "BASEMENT & LEVEL 1 FLOOR PLANS",
# "BUILDING SECTIONS". A singular-only pattern reported "no floor plan" for a
# sheet whose name ends in FLOOR PLANS, which is the opposite of the truth and
# exactly the kind of false alarm that trains people to ignore the audit.
_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("schedule", r"\b(door|window|room|finish)\s*schedules?\b"),
    ("site_plan", r"\bsite\s*plans?\b"),
    ("roof_plan", r"\broof\s*plans?\b"),
    ("section", r"\bsections?\b"),
    ("elevation", r"\belevations?\b"),
    ("floor_plan", r"\bfloor\s*plans?\b|\bplans?\b"),
]


def sheet_type(name: str) -> Optional[str]:
    n = (name or "").replace("_", " ")
    for t, pat in _TYPE_PATTERNS:
        if re.search(pat, n, re.I):
            return t
    return None


def sheet_number(name: str) -> Optional[tuple[str, int]]:
    """('A', 6) from '... - A6 - BASEMENT FLOOR PLAN.dwg'."""
    m = re.search(r"\b([A-Z])[-\s]?(\d{1,2})\b(?!\d)", name or "")
    return (m.group(1).upper(), int(m.group(2))) if m else None


def levels_named(texts: Iterable[str]) -> list[str]:
    """Which building levels this text names, in building order."""
    found: set[str] = set()
    for t in texts:
        if not t or len(t) > 80:
            continue
        for key, pat in LEVEL_PATTERNS:
            if re.search(pat, t, re.I):
                found.add(key)
    return [k for k in LEVEL_ORDER if k in found]


def level_names_from(msp) -> list[str]:
    """Levels named by the text in a model space — a section labels every one."""
    out = []
    for e in msp.query("MTEXT TEXT"):
        try:
            raw = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        except Exception:  # noqa: BLE001
            continue
        t = " ".join((raw or "").split())
        if t:
            out.append(t)
    return levels_named(out)


@dataclass
class Sheet:
    name: str
    building: str
    number: Optional[tuple[str, int]] = None
    type: Optional[str] = None
    levels_named: list[str] = field(default_factory=list)
    storeys_covered: list[str] = field(default_factory=list)


@dataclass
class SetAudit:
    sheets: list[Sheet] = field(default_factory=list)
    by_building: dict[str, dict] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    complete: bool = True

    @property
    def blocking(self) -> list[str]:
        return [f for f in self.findings if f.startswith("MISSING")]


def audit_set(sheets: Iterable[Sheet]) -> SetAudit:
    """What the set covers, what it does not, and what that costs downstream."""
    audit = SetAudit(sheets=list(sheets))
    if not audit.sheets:
        audit.complete = False
        audit.findings.append("MISSING: no drawings at all.")
        return audit

    for sh in audit.sheets:
        b = audit.by_building.setdefault(sh.building, {
            "sheets": [], "types": set(), "numbers": [],
            "levels_in_building": set(), "storeys_covered": set()})
        b["sheets"].append(sh.name)
        if sh.type:
            b["types"].add(sh.type)
        if sh.number:
            b["numbers"].append(sh.number)
        # Only a section or elevation is evidence of what the BUILDING has; a floor
        # plan naming "LEVEL 1" is evidence only about itself.
        if sh.type in ("section", "elevation"):
            b["levels_in_building"].update(sh.levels_named)
        b["storeys_covered"].update(sh.storeys_covered)

    for bld, b in sorted(audit.by_building.items()):
        levels = [k for k in LEVEL_ORDER if k in b["levels_in_building"] and k != "roof"]
        covered = [k for k in LEVEL_ORDER if k in b["storeys_covered"]]
        b["storeys_expected"] = levels
        b["storeys_missing"] = [k for k in levels if k not in covered]
        b["coverage"] = (round(len(covered) / len(levels), 2) if levels else None)

        if levels and b["storeys_missing"]:
            audit.complete = False
            audit.findings.append(
                f"MISSING — {bld}: the sections name {len(levels)} storeys "
                f"({', '.join(levels)}) but the set has floor plans for "
                f"{len(covered)} ({', '.join(covered) or 'none'}). "
                f"No plan for: {', '.join(b['storeys_missing'])}. "
                f"Every quantity below covers {len(covered)} of {len(levels)} storeys.")
        elif not levels:
            # "Floor plans present: none" on a set whose only file IS a floor plan
            # (1234, rollout 2026-10-20): `covered` holds the storeys the plans
            # NAME, and a single-storey plan often names none. Say which it is.
            if covered:
                plans = f"Floor plans cover: {', '.join(covered)}."
            elif "floor_plan" in b["types"]:
                plans = "Floor plans are present but do not name a storey."
            else:
                plans = "No floor plans."
            audit.findings.append(
                f"{bld}: no section or elevation in the analysed drawing set, so the building's storey count is "
                f"unknown and coverage cannot be checked. {plans}")

        for t, unlocks in EXPECTED_TYPES.items():
            if t not in b["types"]:
                sev = "MISSING" if t in ("floor_plan", "section") else "absent"
                # "in the analysed set": other files (PDF plans, surveys) may hold it;
                # "no site plan" read as if the project had none (rollout test 2026-10-14).
                audit.findings.append(f"{sev} — {bld}: no {t.replace('_', ' ')} in the analysed drawing set — {unlocks}.")
                if sev == "MISSING":
                    audit.complete = False

        nums = sorted({n for _p, n in b["numbers"]})
        if len(nums) >= 2:
            gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
            if gaps:
                pfx = b["numbers"][0][0]
                audit.findings.append(
                    f"{bld}: sheet numbers {pfx}{nums[0]}-{pfx}{nums[-1]} are missing "
                    f"{', '.join(f'{pfx}{g}' for g in gaps)} — whatever those sheets "
                    f"carry is not in this set.")

    # Sets are the natural structure to build this with and cannot be stored:
    # the audit is persisted as jsonb on the analysis pack, and a set raises at
    # the driver rather than anywhere near here. Ordered lists also make two
    # audits of the same set compare equal, which sets do not guarantee in text.
    for b in audit.by_building.values():
        for key in ("types", "levels_in_building", "storeys_covered"):
            if isinstance(b.get(key), set):
                b[key] = sorted(b[key])
        b["numbers"] = [list(n) for n in b["numbers"]]
    return audit
