"""What a submittal review compares, per trade (approved 2026-10-17: "Add the
first checklists": stormwater, trusses, windows, rebar, HVAC).

Each check names what to read from BOTH sides. The reviewer reports every check,
including the ones it cannot find ("Not shown"), so a missing value is visible
instead of silently passing. `governing` lists filename patterns used only to
SUGGEST documents; the PM's ticks decide what is read (never gate on category,
see brio-pm-document-selection). `engineer_role` is who answers a design question.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    what: str


@dataclass(frozen=True)
class Checklist:
    key: str
    label: str
    engineer_role: str
    checks: tuple[Check, ...]
    governing: tuple[str, ...] = ()


STORMWATER = Checklist(
    "stormwater", "Stormwater (infiltration / detention tank)", "stormwater_engineer",
    (
        Check("modules", "Modules", "module type, count and arrangement (e.g. 3 x 3 x 1) against the plan"),
        Check("volume", "Volume", "net storage volume against the plan's required volume"),
        Check("footprint", "Footprint", "plan dimensions and footprint area against the plan's minimum area"),
        Check("base_invert", "Base invert", "tank base / bottom elevation against the plan"),
        Check("grade_cover", "Finished grade and cover", "finished grade over the tank and depth of cover against the minimum for the load rating"),
        Check("wrap", "Wrap", "geotextile filter cloth versus impermeable membrane, and which faces are wrapped; an infiltration tank keeps an open base"),
        Check("connections", "Inlet and overflow inverts", "inlet, outlet and overflow invert elevations, whether each fits the tank height, and the fall to the next structure by its label"),
        Check("inspection", "Inspection port", "inspection or cleanout location and elevation"),
        Check("pipe_sizes", "Pipe sizes", "connection pipe diameters on each side"),
        Check("orientation", "Orientation", "north arrow and which side each connection faces, by the labelled structure it connects to"),
    ),
    governing=(r"storm|smp|swm|rainwater|drainage", r"site plan|a-?10\d"),
)

TRUSSES = Checklist(
    "trusses", "Roof trusses / engineered joists", "structural_engineer",
    (
        Check("layout", "Layout and spacing", "truss or joist layout, spacing and direction against the framing plan"),
        Check("spans", "Spans and bearing", "spans, bearing points and bearing lengths against the structural drawings"),
        Check("loads", "Design loads", "snow, dead and live loads, and any point loads (mechanical units, girder trusses)"),
        Check("profile", "Profile", "pitch, heel height, overhang and ceiling profile against the architectural sections"),
        Check("girders", "Girders and hangers", "girder trusses, ply count and hanger schedule"),
        Check("bracing", "Bracing", "permanent lateral bracing and web reinforcement requirements"),
        Check("seal", "Seal and letter", "engineer's seal on the truss drawings and the design letter"),
    ),
    governing=(r"\bs-?\d|struct", r"\ba-?[23]\d\d|section|roof"),
)

WINDOWS = Checklist(
    "windows", "Windows and doors", "envelope_professional",
    (
        Check("schedule", "Schedule", "each unit's tag, count and location against the window schedule"),
        Check("sizes", "Sizes", "rough opening and unit sizes against the schedule and elevations"),
        Check("operation", "Operation and egress", "operation type and egress units in bedrooms"),
        Check("performance", "Performance", "U-value, SHGC and NAFS / CSA rating against the energy report and the permit"),
        Check("glazing", "Glazing", "tempered or laminated glazing where required (doors, near floors, bathtubs)"),
        Check("finish", "Frame and finish", "frame material, colour and hardware against the selections"),
        Check("install", "Installation", "flashing, sill pan and install detail against the envelope details"),
    ),
    governing=(r"window|door|schedule", r"energy|step code|h2000", r"\ba-?\d|elevation"),
)

REBAR = Checklist(
    "rebar", "Rebar", "structural_engineer",
    (
        Check("sizes", "Bar sizes and grade", "bar sizes and steel grade against the structural notes"),
        Check("spacing", "Spacing", "spacing in footings, walls and slabs against the structural details"),
        Check("cover", "Cover", "concrete cover to each face against the notes"),
        Check("laps", "Laps and development", "lap splice and development lengths"),
        Check("dowels", "Dowels and corners", "dowels, corner bars and hooks at footing-wall joints"),
        Check("quantities", "Quantities", "bar list totals against the elements measured on the drawings"),
    ),
    governing=(r"\bs-?\d|struct|foundation",),
)

HVAC = Checklist(
    "hvac", "HVAC / ventilation", "envelope_professional",
    (
        Check("equipment", "Equipment", "make, model and capacity against the heat-loss / ventilation design"),
        Check("ventilation", "Ventilation rates", "HRV / ERV and exhaust rates against BC Building Code 9.32"),
        Check("ducting", "Ducting", "duct sizes and routing against the mechanical layout and the framing"),
        Check("penetrations", "Penetrations", "wall and roof penetrations, terminations and clearances"),
        Check("electrical", "Electrical", "power requirements against the electrical plan"),
        Check("energy", "Energy", "efficiency ratings against the energy model and Step Code target"),
    ),
    governing=(r"mech|hvac|ventilation|heat ?loss", r"energy|step code"),
)

CHECKLISTS: dict[str, Checklist] = {c.key: c for c in (STORMWATER, TRUSSES, WINDOWS, REBAR, HVAC)}

#: Edited and added checklists (Amir, 2026-10-17: edit them on screen). One
#: app_settings row for the whole company: {"checklists": {key: {...}}}. The
#: five above stay the defaults a "Reset" goes back to.
SETTING_KEY = "submittal_checklists"


def to_dict(c: Checklist, *, edited: bool = False, builtin: bool = True) -> dict:
    return {"key": c.key, "label": c.label, "engineer_role": c.engineer_role,
            "checks": [{"key": k.key, "label": k.label, "what": k.what} for k in c.checks],
            "governing": list(c.governing), "edited": edited, "builtin": builtin}


def from_dict(d: dict) -> Checklist:
    return Checklist(str(d["key"]), str(d["label"]), str(d.get("engineer_role") or "other"),
                     tuple(Check(str(k["key"]), str(k["label"]), str(k.get("what") or "")) for k in d.get("checks") or []),
                     tuple(d.get("governing") or ()))


def _overrides(db) -> dict:
    if db is None:
        return {}
    from app.models.prep import AppSetting

    row = db.get(AppSetting, SETTING_KEY)
    return dict((row.value or {}).get("checklists") or {}) if row else {}


def all_checklists(db=None) -> dict[str, Checklist]:
    out = dict(CHECKLISTS)
    for k, d in _overrides(db).items():
        try:
            out[k] = from_dict({**d, "key": k})
        except (KeyError, TypeError):
            continue
    return out


def get(key: str, db=None) -> Checklist:
    found = all_checklists(db)
    if key not in found:
        raise KeyError(key)
    return found[key]


def listing(db=None) -> list[dict]:
    over = _overrides(db)
    return [to_dict(c, edited=c.key in over and c.key in CHECKLISTS, builtin=c.key in CHECKLISTS)
            for c in all_checklists(db).values()]


def save(db, data: dict, *, user_id=None) -> dict:
    """Store one checklist as edited. Check keys are kept when given (a review's
    findings refer to them) and made from the label when new."""
    import re
    from datetime import datetime, timezone

    from app.models.prep import AppSetting

    checks, seen = [], set()
    for c in data.get("checks") or []:
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        key = str(c.get("key") or "").strip() or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "check"
        base, n = key, 2
        while key in seen:
            key, n = f"{base}_{n}", n + 1
        seen.add(key)
        checks.append({"key": key, "label": label, "what": str(c.get("what") or "").strip() or label})
    if not checks:
        raise ValueError("a checklist needs at least one check")
    governing = [g for g in (data.get("governing") or []) if str(g).strip()]
    words = [w.strip() for w in data.get("governing_words") or [] if str(w).strip()]
    governing += [re.escape(w) for w in words if re.escape(w) not in governing]
    for g in governing:
        re.compile(g)
    entry = {"label": str(data["label"]).strip(), "engineer_role": data.get("engineer_role") or "other",
             "checks": checks, "governing": governing}
    row = db.get(AppSetting, SETTING_KEY)
    value = dict(row.value) if row else {}
    value["checklists"] = {**(value.get("checklists") or {}), data["key"]: entry}
    if row is None:
        db.add(AppSetting(key=SETTING_KEY, value=value, updated_by_id=user_id, updated_at=datetime.now(timezone.utc)))
    else:
        row.value, row.updated_by_id, row.updated_at = value, user_id, datetime.now(timezone.utc)
    db.flush()
    return to_dict(from_dict({**entry, "key": data["key"]}), edited=data["key"] in CHECKLISTS,
                   builtin=data["key"] in CHECKLISTS)


def reset(db, key: str) -> None:
    """A built-in goes back to its default; an added one is removed."""
    from app.models.prep import AppSetting

    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        return
    value = dict(row.value)
    value["checklists"] = {k: v for k, v in (value.get("checklists") or {}).items() if k != key}
    row.value = value
    db.flush()
