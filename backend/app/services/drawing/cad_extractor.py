"""CAD (DWG/DXF) quantity-takeoff extractor.

Implements the mandatory takeoff sequence:
  1. tooling (ezdxf + dwg2dxf)   2. convert + inventory
  3. SCOPE to the project boundary (never count the whole model)
  4. extract counts / areas / wall centerlines
  (5. reconcile against the PDF — done by the caller/skill; 6. feed estimate)

CAD/Revit exports contain the whole site (existing building + new work +
presentation furniture). This module therefore ALWAYS scopes to the new-work
cluster before counting — the #1 lesson from the 1234 Example garage, where a
whole-model count returned 14 doors / 2 toilets / a full kitchen versus the real
garage scope of 5 doors / 1 toilet / 0 appliances.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import ezdxf

# ── classification: block INSERTs → takeoff category (layer first, name second) ──
_PLUMB = ("toilet", "wc", "lav", "basin", "sink", "tub", "shower", "urinal", "bidet")
_APPL = ("cooktop", "range", "oven", "fridge", "refriger", "dishwash", "microwave",
         "hood", "washer", "dryer", "stove")
_DOOR = ("door", "flush", "slider", "pocket", "overhead", "bifold", "barn")
_WINDOW = ("window", "fixed", "glaz", "casement", "awning", "hung", "sliding window")
_FURN = ("chair", "stool", "table", "desk", "sofa", "couch", "bed", "seating",
         "coalesse", "steelcase", "bench")
_MARKER = ("grid", "elevation", "section", "detail", "callout", "north", "revision",
           "tag", "symbol", "head", "key plan")
UNITS = {0: "unitless", 1: "inches", 2: "feet", 4: "mm", 5: "cm", 6: "m"}

# Drawing units → INCHES. Every measurement below is computed in inches and then
# converted once, so a metric DXF yields the same answer as the imperial one.
# Before this existed the /144 and /12 conversions were unconditional: a
# millimetre drawing silently produced numbers ~25x wrong, and the scope-gap
# threshold (also in inches) split the model in the wrong place and dropped
# entities entirely.
_TO_INCHES = {
    "inches": 1.0,
    "feet": 12.0,
    "mm": 1.0 / 25.4,
    "cm": 1.0 / 2.54,
    "m": 39.37007874,
    "unitless": 1.0,   # assume inches, and warn
}


def unit_scale(units: str | None) -> float:
    """Multiplier converting one drawing unit into one inch."""
    return _TO_INCHES.get(units or "unitless", 1.0)


# Layer → category. Keys are matched against the UPPERCASED layer name; a key
# ending in "*" is a prefix match. Defaults follow the AIA CAD Layer Guidelines,
# which most North American architectural offices use.
#
# This is overridable per drawing set because layer standards are a CONVENTION,
# not a guarantee: an office with its own scheme (or a consultant's export) will
# classify to "other" and silently vanish from the takeoff. Pass `layer_map=` to
# run_takeoff() to map a client's layers onto these categories.
DEFAULT_LAYER_MAP: dict[str, str] = {
    "P-SANR-FIXT": "plumbing_fixture",
    "M-EQPM": "appliance",
    "A-DOOR*": "door",
    "A-GLAZ*": "window",
    "I-FURN": "furniture",
}

# AIA layer names are MAJOR-MINOR-MINOR (A-GLAZ-CWMG). A prefix match on the major
# group swallows every minor group under it, and the minor group is often not the
# thing at all — it is a component of it, or an annotation about it.
#
# Measured on the Maple set: `A-GLAZ*` matched 85 `Rectangular Mullion` blocks
# on A-GLAZ-CWMG and glazed system panels on A-GLAZ-CURT, reporting **100 windows
# on one duplex level**. A mullion is a piece of a curtain wall, not a window unit;
# counting them is not an overcount of windows, it is a different quantity.
_COMPONENT_MINORS = {
    "CWMG": "curtain_wall_mullion",   # curtain-wall mullion
    "CURT": "curtain_wall_panel",     # curtain-wall system panel
    "SILL": "component",
    "FRAM": None,                     # door/window FRAME — still the opening; keep it
}
# Minor groups that annotate rather than build. Never a countable element.
_ANNOTATION_MINORS = {
    "PATT",   # poche / hatch pattern
    "IDEN",   # identification tags
    "ANNO",   # annotation
    "DIMS",   # dimensions
    "TEXT",
    "SYMB",
    "NPLT",   # non-plotting
}


def _minor_groups(layer: str) -> list[str]:
    return [p for p in (layer or "").upper().split("-")[2:] if p]


def is_annotation_layer(layer: str) -> bool:
    """True for a layer that annotates the building rather than being part of it."""
    return any(m in _ANNOTATION_MINORS for m in _minor_groups(layer))


def _layer_category(layer: str, layer_map: dict[str, str]) -> Optional[str]:
    l = (layer or "").upper()
    if is_annotation_layer(l):
        return "marker"
    for m in _minor_groups(l):
        if m in _COMPONENT_MINORS:
            mapped = _COMPONENT_MINORS[m]
            if mapped is not None:
                return mapped
    for key, cat in layer_map.items():
        k = key.upper()
        if k.endswith("*"):
            if l.startswith(k[:-1]):
                return cat
        elif l == k:
            return cat
    return None


def _classify(name: str, layer: str, layer_map: Optional[dict[str, str]] = None) -> Optional[str]:
    """Classify a block reference. Layer first (authoritative), name second.

    The name fallback matters: not every office layers correctly, and a block
    called "DOOR-36x80" sitting on layer "0" is still a door.
    """
    n = (name or "").lower()
    by_layer = _layer_category(layer, layer_map or DEFAULT_LAYER_MAP)
    if by_layer:
        return by_layer
    if any(k in n for k in _PLUMB):
        return "plumbing_fixture"
    if any(k in n for k in _APPL):
        return "appliance"
    if any(k in n for k in _DOOR) and "downspout" not in n:
        return "door"
    if any(k in n for k in _WINDOW):
        return "window"
    if any(k in n for k in _FURN):
        return "furniture"
    if any(k in n for k in _MARKER):
        return "marker"
    return "other"


# Attribute tags that carry an opening's dimensions. CAD offices differ, so several
# spellings are accepted; the first present wins.
_WIDTH_TAGS = ("WIDTH", "W", "WD", "NOMINAL_WIDTH", "ROUGH_WIDTH")
_HEIGHT_TAGS = ("HEIGHT", "H", "HT", "NOMINAL_HEIGHT", "ROUGH_HEIGHT")


def _attr_map(insert) -> dict[str, str]:
    """Flatten a block reference's ATTRIB entities into {TAG: value}."""
    out: dict[str, str] = {}
    for a in getattr(insert, "attribs", []) or []:
        tag = (getattr(a.dxf, "tag", "") or "").strip().upper()
        val = (getattr(a.dxf, "text", "") or "").strip()
        if tag and val:
            out[tag] = val
    return out


#: Foot marks. ``_`` is here because exports routinely mangle an apostrophe into an
#: underscore \u2014 ``18_ x 8_`` is 18'x8'. It is AMBIGUOUS (the same export writes an
#: inch mark as ``_`` too), and `_to_inches` resolves that by plausibility below.
_FOOT_MARKS = "'\u2019\u02bc_"
_INCH_MARKS = '"\u201d\u02ba'
#: Largest dimension treated as plausible for a single opening, in inches. Used ONLY
#: to disambiguate the `_` mark; an explicit ' or " is always taken at face value.
_MAX_OPENING_IN = 240.0

#: One dimension: a number, optionally a foot mark, optionally a trailing inches part.
#:   36            -> 36 in          8_          -> 8 ft
#:   8'            -> 8 ft           3'-6"       -> 3 ft 6 in
_DIM = (r"(\d+(?:\.\d+)?)\s*"
        r"(?:([" + _FOOT_MARKS + r"])\s*(?:-\s*)?(\d+(?:\.\d+)?)?\s*[" + _INCH_MARKS + r"]?)?")
_DIM_PAIR = re.compile(_DIM + r"\s*[xX\u00d7]\s*" + _DIM)
#: Trailing element id a BIM export appends: "-7393081-L1".
_ELEMENT_ID = re.compile(r"-\d{4,}(?:-[A-Za-z0-9]+)*\s*$")


def _to_inches(whole: str, foot_mark: Optional[str], part: Optional[str]) -> float:
    """One parsed dimension in inches."""
    n = float(whole)
    if not foot_mark:
        return n
    if foot_mark == "_" and part is None and n * 12.0 > _MAX_OPENING_IN:
        # Ambiguous mark on an implausible result: `80_` in "7_x80_" is 80 INCHES,
        # not 80 feet. An unambiguous ' is never second-guessed.
        return n
    return n * 12.0 + float(part or 0)


def _dim_pair(text: str) -> Optional[tuple[float, float]]:
    m = _DIM_PAIR.search(text or "")
    if not m:
        return None
    w = _to_inches(m.group(1), m.group(2), m.group(3))
    h = _to_inches(m.group(4), m.group(5), m.group(6))
    return (w, h) if w > 0 and h > 0 else None


def _fmt_in(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 0.01 else f"{v:.1f}"


def _size_from_name(block_name: str) -> Optional[tuple[float, float]]:
    """Read a size out of a block name, preferring the MOST SPECIFIC segment.

    BIM tools name a block ``Family - Type - elementid``, and the family may itself
    be named after a size. On the 1234 Example set the family is literally called
    ``30x84``, so ``30x84 - Fixed - 36x24`` is a 36x24 window from a family whose
    name is a different number. Taking the FIRST match in the string reported all
    seven windows as 30x84 and collapsed three real sizes into one wrong one \u2014 a
    confident, well-formed, wrong answer.

    So: strip the element id, then search segments right-to-left. The rightmost
    segment is the type, which is the thing actually placed.
    """
    name = _ELEMENT_ID.sub("", block_name or "").strip()
    for seg in reversed([p.strip() for p in name.split(" - ") if p.strip()]):
        pair = _dim_pair(seg)
        if pair:
            return pair
    return _dim_pair(name)


def _size_key(insert, block_name: str) -> Optional[str]:
    """A 'WxH' size for one opening IN INCHES, from ATTRIBUTES first, name second.

    Attributes are authoritative because they are what the drafter typed into the
    schedule. The block-name fallback exists because plenty of libraries encode the
    size in the name (``DR-36x80``) and carry no attributes at all.

    Normalised to inches so that a 36x24 clerestory and an 8'x8' window are
    comparable keys. Mixing notations made the two uncountable together, and the
    engine measures in inches everywhere else.
    """
    attrs = _attr_map(insert)
    w = next((attrs[t] for t in _WIDTH_TAGS if t in attrs), None)
    h = next((attrs[t] for t in _HEIGHT_TAGS if t in attrs), None)
    if w and h:
        pair = _dim_pair(f"{w}x{h}")
        return f"{_fmt_in(pair[0])}x{_fmt_in(pair[1])}" if pair else f"{w}x{h}"
    pair = _size_from_name(block_name)
    return f"{_fmt_in(pair[0])}x{_fmt_in(pair[1])}" if pair else None


@dataclass
class Takeoff:
    units: str
    scope_boundary: Optional[float]
    scope_axis: str
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    excluded_counts: dict[str, int] = field(default_factory=dict)
    areas_sf: list[float] = field(default_factory=list)
    area_labels: list[str] = field(default_factory=list)
    wall_lf: dict[str, float] = field(default_factory=dict)
    # sizes read from block ATTRIBUTES, e.g. {"door": {"36x80": 2, "30x80": 1}}.
    # This is where CAD actually stores opening dimensions; counts alone can't
    # price a material takeoff.
    sizes: dict[str, dict[str, int]] = field(default_factory=dict)
    # derived geometry — measured bases for the structural/envelope trades that the
    # openings takeoff alone can't price (floor area, footprint, perimeter, roof).
    derived: dict[str, float] = field(default_factory=dict)
    # per-view breakdown. A sheet may show a basement and a level 1 side by side;
    # each is measured independently and the totals are the sum. `counts`, `sizes`,
    # `wall_lf` and `rooms` above are those aggregates.
    views: list[dict[str, Any]] = field(default_factory=list)
    # the architect's own room schedule (name + area per room), read from the room
    # identification layer. The primary source of floor area when present.
    room_schedule: list[dict[str, Any]] = field(default_factory=list)
    # per-room interior areas, measured (see room_areas). Each entry carries BOTH
    # net (inner face — flooring, paint) and centreline (matches the architect's
    # own labels), because different trades price off different ones.
    rooms: list[dict[str, Any]] = field(default_factory=list)
    inventory: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def load_doc(path: str | Path):
    """Load a DXF, converting from DWG first if needed (dwg2dxf / ODA)."""
    p = Path(path)
    if p.suffix.lower() == ".dwg":
        if not shutil.which("dwg2dxf"):
            raise RuntimeError("dwg2dxf (LibreDWG) not found — install libredwg or ODA to read DWG")
        # Fresh temp dir + non-existent output path (dwg2dxf writes a new file and
        # won't overwrite an existing one; avoids spaces/stale-file collisions).
        # dwg2dxf may exit non-zero on non-fatal warnings while still writing a
        # valid DXF, so judge success by the output file, not the return code.
        out = os.path.join(tempfile.mkdtemp(prefix="cadtakeoff_"), "drawing.dxf")
        subprocess.run(["dwg2dxf", "-o", out, str(p)], capture_output=True, timeout=120)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError("dwg2dxf produced no output — DWG may be unsupported")
        p = Path(out)
    try:
        return ezdxf.readfile(str(p))
    except ezdxf.DXFStructureError:
        from ezdxf import recover
        try:
            doc, _ = recover.readfile(str(p))
            return doc
        except ezdxf.DXFStructureError:
            return ezdxf.readfile(_repair_tag_stream(p))


def _repair_tag_stream(path: Path) -> str:
    """Resynchronise a DXF whose group-code/value pairing has slipped.

    dwg2dxf splits a long MTEXT across group-3 continuation chunks and
    occasionally emits a chunk's VALUE without its code line. Every tag after
    that point is then read one line out of phase, so the file fails on some
    innocent sentence — "Invalid group code 'written consent.'" — and ezdxf's
    own recover cannot resynchronise, because it reads the same shifted stream.

    A DXF is strictly alternating code/value lines, so the slip is detectable
    without knowing anything about the drawing: a line where a code belongs that
    is not an integer is a continuation, and repeating the previous code puts
    the stream back in phase. This only ever runs on a file that has already
    failed to parse twice, so it cannot damage a good one.

    Read and written as latin-1 so the round trip is byte-preserving — a DXF may
    carry any encoding, and re-encoding it would corrupt text we do not read.
    """
    lines = path.read_text(encoding="latin-1").splitlines()
    out: list[str] = []
    last_code: str | None = None
    i = 0
    repaired = 0
    while i < len(lines):
        raw = lines[i].strip()
        try:
            int(raw)
        except ValueError:
            if last_code is None:       # slipped before any code — leave it
                out.append(lines[i])
                i += 1
                continue
            out.append(last_code)       # put the stream back in phase
            out.append(lines[i])
            repaired += 1
            i += 1
            continue
        last_code = lines[i]
        out.append(lines[i])
        if i + 1 < len(lines):
            out.append(lines[i + 1])
        i += 2

    fixed = os.path.join(tempfile.mkdtemp(prefix="cadrepair_"), "drawing.dxf")
    Path(fixed).write_text("\n".join(out) + "\n", encoding="latin-1")
    return fixed


def _inventory(doc) -> dict[str, Any]:
    msp = doc.modelspace()
    ents = list(msp)
    return {
        "dxf_version": doc.dxfversion,
        "units": UNITS.get(doc.header.get("$INSUNITS", 0), doc.header.get("$INSUNITS")),
        "entity_count": len(ents),
        "entity_types": dict(Counter(e.dxftype() for e in ents).most_common()),
        "layer_counts": dict(Counter(e.dxf.layer for e in ents).most_common()),
        "layers": sorted(l.dxf.name for l in doc.layers),
        "extents": (doc.header.get("$EXTMIN"), doc.header.get("$EXTMAX")),
    }


def run_takeoff(path: str | Path, layer_map: Optional[dict[str, str]] = None) -> Takeoff:
    """Measure every plan view on the sheet, then aggregate.

    Views come from the paperspace viewports where the file has them, which is
    exact. The previous single-axis "largest gap" scope rule is gone: on a sheet
    showing two plans it kept one and discarded the other, and on one plan with an
    internal courtyard it split the building in half. Both failures were silent.
    """
    from app.services.drawing.views import clip_to_view, detect_views

    doc = load_doc(path)
    msp = doc.modelspace()
    inv = _inventory(doc)
    u = unit_scale(inv.get("units"))          # drawing units -> inches
    views = detect_views(doc, u)

    t = Takeoff(units=inv["units"], scope_boundary=None, scope_axis="x", inventory=inv)
    if (inv.get("units") or "unitless") == "unitless":
        t.warnings.append("Drawing has no $INSUNITS — assuming inches. Verify every measured quantity.")
    if not views:
        t.warnings.append("No wall line-work found — nothing to measure.")
        return t
    if any(v.source == "whole" for v in views):
        t.warnings.append("Could not resolve plan views — measuring the whole sheet. Verify against the PDF.")

    for v in views:
        m = _measure_view(msp, clip_to_view(v, u), u, layer_map, v)
        t.views.append(m)

    _mark_new_work(t)
    _aggregate_views(t, u)
    _check_area_agreement(t)
    _check_size_coverage(t)
    return t


def _check_size_coverage(t: Takeoff) -> None:
    """Warn when counted openings have no size, because that failure is silent.

    A size that cannot be parsed is simply absent from `sizes`, and nothing
    downstream notices: the counts still look right and the size table still looks
    populated. On 1234 Example the 18'x8' overhead door parsed to nothing, so the
    door sizes totalled 4 against a door count of 5 — visible only by subtracting
    one from the other, which nobody was doing.

    A count without a size cannot be priced as material, so an opening missing one
    is a hole in the takeoff, not a cosmetic gap.
    """
    for cat in ("door", "window"):
        counted = sum((t.counts.get(cat) or {}).values())
        sized = sum((t.sizes.get(cat) or {}).values())
        if counted and sized < counted:
            t.warnings.append(
                f"{counted - sized} of {counted} {cat}s carry NO size — their block "
                f"names hold no readable dimensions and they have no size attributes. "
                f"They are counted but cannot be priced as material. Identify them in "
                f"`views[].marks.{cat}` and size them from the drawing.")


def _measure_view(msp, inside, u: float, layer_map, view) -> dict[str, Any]:
    """Everything measurable inside one plan view."""
    from app.services.drawing.views import View

    assert isinstance(view, View)
    out: dict[str, Any] = {
        "index": view.index, "source": view.source, "title": view.title,
        "bbox": [round(c, 1) for c in view.bbox],
        "width_ft": round(view.width * u / 12, 1),
        "height_ft": round(view.height * u / 12, 1),
        "warnings": list(view.notes),
    }

    counts: dict[str, Counter] = defaultdict(Counter)
    sizes: dict[str, Counter] = defaultdict(Counter)
    excluded: Counter = Counter()
    marks: dict[str, list] = defaultdict(list)
    for e in msp.query("INSERT"):
        c = _classify(e.dxf.name, e.dxf.layer, layer_map)
        # furniture is presentation content, not scope. "component" is a piece of a
        # larger element (a sill), counted nowhere.
        if c in (None, "other", "marker", "furniture", "component"):
            continue
        if inside((e.dxf.insert.x, e.dxf.insert.y)):
            counts[c][e.dxf.name] += 1
            # Where it is, not just that it exists. A reviewer asked to confirm
            # "7 windows" cannot do so from the number — they need to see the
            # seven the machine actually counted.
            marks[c].append({"x": round(e.dxf.insert.x, 1),
                             "y": round(e.dxf.insert.y, 1), "name": e.dxf.name})
            if c in ("door", "window"):
                k = _size_key(e, e.dxf.name)
                if k:
                    sizes[c][k] += 1
        else:
            excluded[c] += 1
    out["counts"] = {c: dict(counts[c]) for c in counts}
    out["sizes"] = {c: dict(sizes[c]) for c in sizes}
    out["excluded_counts"] = dict(excluded)
    out["marks"] = {c: marks[c] for c in marks}

    areas: list[float] = []
    for e in msp.query("LWPOLYLINE POLYLINE"):
        if (e.dxf.layer or "").upper() != "A-AREA":
            continue
        try:
            pts = [(p[0], p[1]) for p in e.get_points()]
        except Exception:  # noqa: BLE001
            continue
        if len(pts) < 3 or not inside(pts[0]):
            continue
        sh = sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
                 - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        a = abs(sh) / 2 * (u * u) / 144
        if a > 1:
            areas.append(round(a, 1))
    out["areas_sf"] = areas

    labels: list[dict[str, Any]] = []
    for e in msp.query("MTEXT TEXT"):
        txt = " ".join((e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).split())
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:sf|sq\.?\s*ft)", txt, re.I)
        if not m:
            continue
        # "120 + 549 = 669 sq ft" is a summary note, not a room label, and taking
        # its first number double-counts a room that is already labelled.
        if re.search(r"[+=]", txt) or re.search(r"\ballow(ed|able)\b", txt, re.I):
            continue
        pt = (e.dxf.insert.x, e.dxf.insert.y)
        if inside(pt):
            labels.append({"text": txt[:60], "value": float(m.group(1)),
                           "at": [round(pt[0], 1), round(pt[1], 1)]})
    out["labels"] = labels

    wall_lf: dict[str, float] = {}
    for layer in ("A-WALL", "I-WALL"):
        lines = [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))
                 for e in msp.query("LINE")
                 if (e.dxf.layer or "").upper() == layer
                 and inside((e.dxf.start.x, e.dxf.start.y))
                 and inside((e.dxf.end.x, e.dxf.end.y))]
        if lines:
            wall_lf[layer] = round(_centerline_length(
                lines, thickness_max=14.0 / u if u else 14.0) * u / 12, 1)
    out["wall_lf"] = wall_lf

    out.update(_geometric_footprint_2d(msp, inside, u))
    rooms, room_warnings, room_meta = _room_areas_2d(msp, inside, u)
    # The rasterised envelope supersedes the traced polygon for AREA: tracing needs
    # the exterior wall graph to close and it does not, so on the Maple sheets
    # it returned a 46 sf duct chase. The traced polygon still supplies the
    # PERIMETER, which the mask cannot give without contour extraction.
    if room_meta.get("envelope_sf"):
        out["geom_area_sf"] = room_meta["envelope_sf"]
        out["geom_area_source"] = "raster_envelope"
    elif out.get("geom_area_sf"):
        out["geom_area_source"] = "traced_polygon"
    out["rooms"] = rooms
    out["warnings"] += room_warnings
    out.update(room_meta)

    # The architect's own room schedule — name + area per room, off the room
    # identification layer. Authoritative, in the way block ATTRIBUTES are
    # authoritative for opening sizes; geometry's job is to check it, not to
    # rediscover it. See room_schedule for why.
    from app.services.drawing.room_schedule import read_schedule, schedule_summary

    scheduled = read_schedule(msp, inside, u)
    if scheduled:
        out["room_schedule"] = [
            {"name": r.name, "area_sf": r.area_sf, "at": [round(c, 1) for c in r.at]}
            for r in scheduled
        ]
        out["room_schedule_summary"] = schedule_summary(scheduled)
    if rooms:
        out["rooms_net_sf"] = round(sum(r["net_sf"] for r in rooms), 1)
        out["rooms_centerline_sf"] = round(sum(r["centerline_sf"] for r in rooms), 1)
    out["label_area_sf"] = _label_area(labels, rooms)
    sched = out.get("room_schedule_summary")
    env = out.get("envelope_sf") or out.get("geom_area_sf")
    if sched and env:
        # The independent check. The envelope is measured to the OUTER face and the
        # schedule states net enclosed area, so the envelope should exceed it — by
        # roughly the wall footprint. A schedule LARGER than the envelope, or an
        # envelope more than half again the schedule, means one of them is wrong.
        ratio = env / sched["total_sf"] if sched["total_sf"] else 0.0
        out["schedule_vs_envelope"] = round(ratio, 3)
        if ratio < 0.95:
            out["warnings"].append(
                f"room schedule ({sched['total_sf']} sf over {sched['rooms']} rooms) "
                f"EXCEEDS the measured envelope ({env} sf) — the tags may cover more "
                f"than this view, or the envelope leaked. Reconcile before pricing.")
        elif ratio > 1.5:
            out["warnings"].append(
                f"measured envelope ({env} sf) is {ratio:.2f}x the room schedule "
                f"({sched['total_sf']} sf) — more than wall thickness explains. "
                f"Check what the outline is enclosing.")
    return out


def _mark_new_work(t: Takeoff) -> None:
    """Decide which views are the NEW WORK, and exclude the rest from the totals.

    A CAD export routinely shows the existing building beside the new one. The old
    rule cut the drawing at the largest gap and kept "the side with the area
    labels" — the guess was right on 1234 Example (5 doors, not 14) but the
    mechanism was wrong, because it also discarded a whole second storey on a sheet
    that showed two plans.

    Views make the same signal safe to use: an architect labels the areas of what
    is being built and does not re-label the neighbour's house. So when SOME views
    carry area labels and others carry none, the unlabelled ones are context. They
    are still measured and reported — only excluded from the totals, and named in a
    warning, because a silent exclusion is how a whole building goes missing.
    """
    if len(t.views) < 2:
        for v in t.views:
            v["included"] = True
        return
    labelled = [v for v in t.views if v["labels"]]
    if not labelled or len(labelled) == len(t.views):
        for v in t.views:
            v["included"] = True
        return
    for v in t.views:
        v["included"] = bool(v["labels"])
    dropped = [v for v in t.views if not v["included"]]
    t.warnings.append(
        "Excluded {} view(s) with no area labels from the totals as existing/context "
        "({}). They are measured in `views` — include them if that is wrong.".format(
            len(dropped),
            "; ".join(f"{v['width_ft']:.0f}x{v['height_ft']:.0f} ft, "
                      f"{sum(sum(b.values()) for b in v['counts'].values())} elements"
                      for v in dropped)))


def _label_area(labels: list[dict], rooms: list[dict]) -> Optional[float]:
    """Floor area from the architect's own labels, by matching each label to the
    room that contains it.

    The old rule took the LARGEST label as the building total. That was inferred
    from a drawing which labelled its total twice ("120 + 549 = 669" AND
    "399.6 + 268"), where summing would double-count. On a set that labels every
    room instead, `max` returns one room as the whole building — 202.6 sf where the
    labels summed to 1415.1.

    Matching each label to a measured room settles it without a heuristic: a label
    inside a room states that room's area, and a label inside no room is a sheet
    note (a zoning allowance, another storey's total) and is excluded. Falls back
    to the old max-of-labels only when no room could be measured.
    """
    if not labels:
        return None
    if not rooms:
        return round(max(l["value"] for l in labels), 1)
    total, matched = 0.0, 0
    for room in rooms:
        x0, y0, x1, y1 = room["bbox"]
        cx, cy = room["centroid"]
        inside = [l for l in labels
                  if x0 <= l["at"][0] <= x1 and y0 <= l["at"][1] <= y1]
        if not inside:
            continue
        # ONE label per room, the one nearest its centre. A room bbox is a
        # rectangle over an L-shaped region, so it also covers whatever sits in the
        # notch — sheet notes included. Summing everything inside double-counts.
        best = min(inside, key=lambda l: (l["at"][0] - cx) ** 2 + (l["at"][1] - cy) ** 2)
        total += best["value"]
        matched += 1
    if not matched:
        return round(max(l["value"] for l in labels), 1)
    return round(total, 1)


def _aggregate_views(t: Takeoff, u: float) -> None:
    """Roll the per-view measurements up to sheet totals.

    Summing across views is correct here because overlapping views were already
    dropped as details in ``detect_views`` — what remains are distinct storeys or
    distinct buildings, and a building's door count IS the sum over its floors.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    sizes: dict[str, Counter] = defaultdict(Counter)
    excluded: Counter = Counter()
    marks: dict[str, list] = defaultdict(list)
    wall_lf: Counter = Counter()
    label_total = geom_area = geom_perim = rooms_net = rooms_cl = 0.0
    geom_ext = geom_int = 0
    geom_bbox_perim = 0.0
    geom_corner_bad = False
    for v in t.views:
        if not v.get("included", True):
            for c, blocks in v["counts"].items():
                excluded.update({c: sum(blocks.values())})
            continue
        for c, blocks in v["counts"].items():
            counts[c].update(blocks)
        for c, ss in v["sizes"].items():
            sizes[c].update(ss)
        excluded.update(v["excluded_counts"])
        for layer, lf in v["wall_lf"].items():
            wall_lf[layer] += lf
        t.areas_sf += v["areas_sf"]
        t.area_labels += [l["text"] for l in v["labels"]]
        for r in v["rooms"]:
            t.rooms.append({**r, "view": v["index"], "view_title": v["title"]})
        label_total += v.get("label_area_sf") or 0.0
        for r in v.get("room_schedule", []):
            t.room_schedule.append({**r, "view": v["index"], "view_title": v["title"]})
        geom_area += v.get("geom_area_sf") or 0.0
        geom_perim += v.get("geom_perimeter_lf") or 0.0
        geom_ext += v.get("geom_external_corners") or 0
        geom_int += v.get("geom_internal_corners") or 0
        geom_bbox_perim += v.get("geom_bbox_perimeter_lf") or 0.0
        if v.get("geom_corner_invariant_ok") is False:
            geom_corner_bad = True
        rooms_net += v.get("rooms_net_sf") or 0.0
        rooms_cl += v.get("rooms_centerline_sf") or 0.0
        t.warnings += [f"view {v['index']}"
                       f"{' (' + v['title'] + ')' if v['title'] else ''}: {w}"
                       for w in v["warnings"]]

    t.counts = {c: dict(counts[c]) for c in counts}
    t.sizes = {c: dict(sizes[c]) for c in sizes}
    t.excluded_counts = dict(excluded)
    t.wall_lf = {k: round(vv, 1) for k, vv in wall_lf.items()}
    # Floor area now comes from MEASURED rooms, with the architect's labels as the
    # cross-check. It used to be the other way round, because geometry could not
    # measure a room; it can now, to +0.4% on the validated file. Labels stay in the
    # output because they are the only independent check we have — and because a
    # sheet can carry ANOTHER storey's labels, which is exactly why they should not
    # be the primary source.
    included = [v for v in t.views if v.get("included", True)]
    if t.room_schedule:
        from app.services.drawing.room_schedule import ScheduledRoom, schedule_summary
        t.derived["schedule_area_sf"] = round(
            sum(r["area_sf"] for r in t.room_schedule), 1)
        t.derived["scheduled_rooms"] = len(t.room_schedule)
    if label_total:
        t.derived["label_area_sf"] = round(label_total, 1)
    if t.room_schedule:
        # The schedule is the floor area. Geometry checks it; see room_schedule.
        t.derived["floor_area_sf"] = t.derived["schedule_area_sf"]
        t.derived["floor_area_source"] = "room_schedule"
        _set_roof_area(t, included, "room_schedule", lambda v: sum(
            r["area_sf"] for r in v.get("room_schedule", [])))
    elif rooms_cl:
        t.derived["floor_area_sf"] = round(rooms_cl, 1)
        t.derived["floor_area_source"] = "measured_rooms"
        _set_roof_area(t, included, "measured_rooms",
                       lambda v: v.get("rooms_centerline_sf") or 0.0)
    elif label_total:
        t.derived["floor_area_sf"] = round(label_total, 1)
        t.derived["floor_area_source"] = "area_labels"
        _set_roof_area(t, included, "area_labels",
                       lambda v: v.get("label_area_sf") or 0.0)
    if geom_area:
        t.derived["geom_area_sf"] = round(geom_area, 1)
    if geom_perim:
        t.derived["geom_perimeter_lf"] = round(geom_perim, 1)
        t.derived["geom_external_corners"] = geom_ext
        t.derived["geom_internal_corners"] = geom_int
        t.derived["geom_corner_count"] = geom_ext + geom_int
        t.derived["geom_bbox_perimeter_lf"] = round(geom_bbox_perim, 1)
        t.derived["geom_corner_invariant_ok"] = not geom_corner_bad
    if rooms_net:
        t.derived["rooms_net_sf"] = round(rooms_net, 1)
    if rooms_cl:
        t.derived["rooms_centerline_sf"] = round(rooms_cl, 1)
    t.derived["views"] = len(t.views)


def _set_roof_area(t: "Takeoff", included: list, source: str, per_view) -> None:
    """Roof plan area — the LARGEST STOREY's footprint, not the sum of storeys.

    Three things this number is not, each of which has been mistaken for it:

    * It is not the building's total floor area. A two-storey house has one roof.
      The largest view is the best available proxy for the largest storey, and it
      is only a proxy: when an architect draws both storeys on ONE view — which
      is normal on a small residential sheet — this returns their SUM and reads
      high by a whole storey. That case cannot be detected from the geometry, so
      it is stated in a warning rather than guessed at.
    * It is not the roof's SURFACE area. This is the plan projection. A sloped
      roof has more surface than its projection (a 6:12 pitch is 1.118x), and no
      slope is readable from a floor plan.
    * It does not include the eave or rake overhang, which on a residential roof
      is commonly 12-24 in all round and adds roughly perimeter x overhang.

    A reviewer correcting this number is the intended path, which is why it is
    written as a candidate and the caveats are attached to it.
    """
    per_storey = round(max((per_view(v) for v in included), default=0.0), 1)
    total = t.derived.get("floor_area_sf") or 0.0
    t.derived["roof_area_sf"] = per_storey or total
    t.derived["roof_area_source"] = f"largest_view:{source}"
    note = ("Roof area is the PLAN PROJECTION of the largest measured storey. It "
            "excludes the eave overhang (commonly 12-24 in all round) and any roof "
            "slope (a 6:12 pitch adds 12%). Adjust before pricing roofing.")
    if included and len(included) == 1:
        note += (" Only one view was measured, so if the drawing shows more than one "
                 "storey on that view this reads high by a whole storey.")
    t.warnings.append(note)


def _check_area_agreement(t: Takeoff) -> None:
    """Cross-check the architect's labels against the ROOM total, not the envelope.

    A label states enclosed area to the wall centreline; the envelope is measured
    to the outer face and is legitimately ~15% larger on a small building. Checking
    labels against the envelope reported a 17% "disagreement" between two
    quantities that were never meant to match.
    """
    if t.room_schedule:
        # The schedule is the answer and each view already reported its own
        # schedule-vs-envelope ratio. Re-running the older label heuristic here
        # only prints a second, worse number beside the right one.
        sched = t.derived.get("schedule_area_sf") or 0.0
        geom = t.derived.get("geom_area_sf") or 0.0
        if sched and geom and not (0.95 <= geom / sched <= 1.5):
            t.warnings.append(
                f"Room schedule {sched} sf vs measured envelope {geom} sf "
                f"({geom / sched:.2f}x). The envelope should exceed the schedule by "
                f"about the wall footprint; this does not. Reconcile before pricing.")
        return
    label_a = t.derived.get("label_area_sf")
    rooms_a = t.derived.get("rooms_centerline_sf")
    geom_a = t.derived.get("geom_area_sf")
    check_a, check_src = (rooms_a, "rooms") if rooms_a else (geom_a, "envelope")
    if label_a and check_a:
        drift = abs(check_a - label_a) / label_a
        if drift > 0.05:
            t.warnings.append(
                f"Floor area disagrees: labels {label_a} sf vs measured {check_a} sf "
                f"({check_src}, {drift * 100:.0f}%). Reconcile against the drawing "
                f"before pricing."
            )
    elif check_a and not label_a:
        t.warnings.append(
            f"No area labels found — floor area {check_a} sf is geometry-derived "
            f"({check_src}) only. Verify."
        )
    if rooms_a and geom_a and geom_a > rooms_a * 1.4:
        t.warnings.append(
            f"Envelope {geom_a} sf is {geom_a / rooms_a:.1f}x the measured rooms "
            f"{rooms_a} sf — the footprint line-work probably bounds more than the "
            f"building (slab, deck, or the lot line). Check before using it."
        )
    _check_perimeter(t)
    _check_corners(t)


#: A rectilinear building's outline is compact: perimeter / (4 x sqrt(area)) is 1.0
#: for a square, ~1.04 for a 2:3 rectangle, and stays under ~1.25 even for a long
#: L. Well above that, the traced outline is following jogs, hatch edges or stray
#: line-work rather than the building, and its LENGTH is wrong even when its AREA
#: looks reasonable.
_PERIM_COMPACTNESS_LIMIT = 1.30


def _check_perimeter(t: "Takeoff") -> None:
    """Decide whether the traced perimeter can be trusted, and say so.

    Perimeter is checked SEPARATELY from area because the two fail
    independently: on 1234 Example the envelope area was 19% high — inside the
    area tolerance — while its perimeter was 22-47% high, because a trace that
    wanders around a jog gains length far faster than it gains area. Perimeter
    drives footing, foundation wall, and exterior cladding, so a quietly wrong
    one is expensive.
    """
    import math

    area = t.derived.get("geom_area_sf") or 0.0
    per = t.derived.get("geom_perimeter_lf") or 0.0
    if not (area > 0 and per > 0):
        return
    compact = per / (4.0 * math.sqrt(area))
    t.derived["geom_perimeter_compactness"] = round(compact, 2)
    reliable = compact <= _PERIM_COMPACTNESS_LIMIT
    t.derived["geom_perimeter_reliable"] = reliable
    if reliable:
        t.derived["perimeter_lf"] = per
        return
    t.warnings.append(
        f"Perimeter {per} lf is not consistent with the {area} sf it encloses "
        f"(compactness {compact:.2f}, expected under {_PERIM_COMPACTNESS_LIMIT}). "
        f"A simple rectangle of that area would measure about "
        f"{4.0 * math.sqrt(area):.0f} lf. The trace is following jogs or stray "
        f"line-work, so the perimeter is NOT written as a quantity — measure it "
        f"off the drawing before pricing footings, foundation wall or cladding.")


def _check_corners(t: "Takeoff") -> None:
    """Report the footprint's articulation, and refuse a corner count that
    cannot describe a real building.

    Corner trim is bought PER CORNER and the external and internal profiles are
    different parts, so a takeoff reporting area and perimeter but no corners
    cannot produce a correct cladding order. On 1234 Example six external
    corners were priced against an actual eight external and four internal — the
    order would have arrived with nothing to close four of them.

    Two facts are checked, because they fail independently.
    """
    ext = t.derived.get("geom_external_corners")
    inn = t.derived.get("geom_internal_corners")
    if ext is None or inn is None or (ext + inn) == 0:
        return

    # 1. The invariant. For ANY simple closed rectilinear polygon,
    #    external - internal == 4. Anything else means the trace missed
    #    articulation or is not a single loop — either way it is not a quantity.
    if not t.derived.get("geom_rectilinear", True):
        t.warnings.append(
            f"Footprint is not rectilinear, so the {ext + inn} corners found are "
            f"reported as-is and NOT as a trim quantity — a non-square corner "
            f"needs a measured angle, not a stock corner profile.")
    elif t.derived.get("geom_corner_invariant_ok") is False or (ext - inn) != 4:
        t.warnings.append(
            f"Corner count rejected: {ext} external and {inn} internal corners "
            f"cannot describe a closed rectilinear footprint, where external "
            f"minus internal is always 4 (this gives {ext - inn}). The trace has "
            f"missed articulation or is not a single loop. Count corners off the "
            f"drawing before ordering corner trim.")
        return
    else:
        t.derived["corner_count"] = ext + inn
        t.derived["external_corner_count"] = ext
        t.derived["internal_corner_count"] = inn

    # 2. Articulation. A rectangle has 4 corners and its traced perimeter equals
    #    2 x (width + height). Any excess is real wall the bounding box cannot
    #    see — a recess or bump-out INTERIOR to the envelope, which is exactly
    #    where the box shortcut silently under-measures.
    if ext + inn > 4:
        per = t.derived.get("geom_perimeter_lf") or 0.0
        bbox = t.derived.get("geom_bbox_perimeter_lf") or 0.0
        extra = round(per - bbox, 1)
        msg = (f"Footprint is articulated: {ext} external and {inn} internal "
               f"corners, not the 4 of a rectangle. Cladding needs BOTH trim "
               f"profiles, and {inn} internal corners are easy to leave off an "
               f"order.")
        if bbox > 0 and extra > 0.5:
            msg += (f" Its perimeter also runs {extra} lf longer than the "
                    f"{bbox} lf of its bounding box, so a perimeter taken as "
                    f"2 x (width + height) is short by that much.")
        t.warnings.append(msg)


def _geometric_footprint_2d(msp, inside, u: float) -> dict[str, Any]:
    """Independent, geometry-only footprint from wall + floor line-work.

    Reported ALONGSIDE the label-derived area, never instead of it. The two are
    measured differently — a label states enclosed area to the wall centreline,
    while line-work may bound the slab including porches and overhangs — so a
    disagreement is information for the reconcile step, not an error to resolve
    silently.
    """
    from app.services.drawing.floor_area import building_footprint

    segs = _line_segments(msp, ("A-WALL", "A-FLOR"), inside)
    fp = building_footprint(segs, u) if segs else None
    if fp is None:
        return {}
    return {
        "geom_area_sf": fp.area_sf,
        "geom_perimeter_lf": fp.perimeter_lf,
        "geom_bridged_gaps": fp.bridged_gaps,
        "geom_faces": fp.faces_found,
        "geom_external_corners": fp.external_corners,
        "geom_internal_corners": fp.internal_corners,
        "geom_corner_invariant_ok": fp.corner_invariant_ok,
        "geom_rectilinear": fp.rectilinear,
        "geom_bbox_perimeter_lf": fp.bbox_perimeter_lf,
    }


def _line_segments(msp, prefixes: tuple[str, ...], inside) -> list:
    """Line-work on the given AIA major groups, inside the view.

    Annotation minor groups (A-WALL-PATT poche, A-WALL-IDEN tags) are excluded —
    a prefix match on the major group would otherwise feed wall hatching into the
    geometry as if it were wall.
    """
    out = []
    for e in msp.query("LINE LWPOLYLINE"):
        lay = (e.dxf.layer or "").upper()
        if not lay.startswith(prefixes) or is_annotation_layer(lay):
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
        if len(pts) < 2 or not all(inside(pt) for pt in pts):
            continue
        out += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    return out


def _widest_opening_in(msp, inside, u: float, layer_map=None) -> float:
    """The widest opening the DRAWING declares, in inches.

    This bounds how wide a gap the envelope may seal. A drawing whose largest
    opening is a 36 in door has no business closing a 10 ft hole — that hole is a
    missing wall, and sealing it invents a building.
    """
    widest = 0.0
    for ins in msp.query("INSERT"):
        if _classify(ins.dxf.name, ins.dxf.layer, layer_map) not in ("door", "window"):
            continue
        if not inside((ins.dxf.insert.x, ins.dxf.insert.y)):
            continue
        key = _size_key(ins, ins.dxf.name)
        if not key:
            continue
        try:
            widest = max(widest, float(key.split("x")[0]))
        except ValueError:
            continue
    return widest


def _room_areas_2d(msp, inside, u: float) -> tuple[list[dict[str, Any]], list[str], dict]:
    """Per-room interior areas, by rasterisation (see room_areas for why).

    Interior-wall layers are included here but NOT in the footprint: a partition
    that must be ignored when tracing the building outline is exactly what divides
    one room from the next.
    """
    from app.services.drawing.room_areas import measure_wall_thickness, room_areas

    segs = _line_segments(msp, ("A-WALL", "I-WALL", "A-FLOR"), inside)
    walls = _line_segments(msp, ("A-WALL", "I-WALL"), inside)
    if not segs:
        return [], [], {}
    # Thickness from the WALL layers only. A-FLOR carries thresholds and slab edges
    # that pair into plausible-looking but meaningless "wall thicknesses".
    thickness = measure_wall_thickness(walls, u) if walls else None
    # The traced outline seals the envelope, so the closing radius only has to
    # bridge interior doorways — see room_areas for why one radius cannot do both.
    envelope = _line_segments(msp, ("A-WALL", "A-FLOR"), inside)
    rs = room_areas(segs, unit_scale=u, wall_thickness_in=thickness,
                    envelope_segments=envelope or None,
                    max_opening_in=_widest_opening_in(msp, inside, u))
    if rs is None:
        return [], ["room line-work would not close — no per-room areas measured."], {}
    rooms = [{
        "net_sf": r.net_sf,
        "centerline_sf": r.centerline_sf,
        "perimeter_lf": r.perimeter_lf,
        "centroid": list(r.centroid),
        "bbox": list(r.bbox),
    } for r in rs.rooms]
    meta = {"room_close_radius_in": rs.close_radius_in,
            "room_plateau_radii": rs.plateau_radii,
            "wall_thickness_in": rs.wall_thickness_in,
            "envelope_sf": rs.envelope_sf,
            "envelope_radius_in": rs.envelope_radius_in}
    return rooms, list(rs.warnings), meta


def _centerline_length(lines: list, thickness_max: float = 14.0,
                       angle_tol: float = 3.0, overlap_min: float = 4.0) -> float:
    """Pair opposing parallel wall faces (double-lines) and count the overlapping
    run once; unpaired lines counted at full length. Better than blanket ÷2."""
    segs = []
    for (p1, p2) in lines:
        L = math.dist(p1, p2)
        if L <= 1:
            continue
        ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) % 180
        segs.append((p1, p2, ang, L))
    used = [False] * len(segs)
    total = 0.0
    for i in range(len(segs)):
        if used[i]:
            continue
        p1i, p2i, ai, Li = segs[i]
        best = None
        for j in range(len(segs)):
            if j == i or used[j]:
                continue
            p1j, p2j, aj, Lj = segs[j]
            if min(abs(ai - aj), 180 - abs(ai - aj)) > angle_tol:
                continue
            d = _perp_dist((p1i, p2i), (p1j, p2j))
            if d < 0.5 or d > thickness_max:
                continue
            ov = _overlap((p1i, p2i), (p1j, p2j), ai)
            if ov < overlap_min:
                continue
            if best is None or ov > best[1]:
                best = (j, ov)
        if best:
            used[i] = used[best[0]] = True
            total += best[1]
        else:
            used[i] = True
            total += Li
    return total


def _perp_dist(a, b) -> float:
    (x1, y1), (x2, y2) = b
    mx, my = (a[0][0] + a[1][0]) / 2, (a[0][1] + a[1][1]) / 2
    den = math.hypot(y2 - y1, x2 - x1) or 1.0
    return abs((y2 - y1) * mx - (x2 - x1) * my + x2 * y1 - y2 * x1) / den


def _overlap(a, b, ang: float) -> float:
    ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    pa = sorted([a[0][0] * ux + a[0][1] * uy, a[1][0] * ux + a[1][1] * uy])
    pb = sorted([b[0][0] * ux + b[0][1] * uy, b[1][0] * ux + b[1][1] * uy])
    return max(0.0, min(pa[1], pb[1]) - max(pa[0], pb[0]))


# ── output helpers ────────────────────────────────────────────────────────

# takeoff category → estimate discipline (for QuantityOverride.discipline). Must
# match the estimate engine's disciplines (_DISCIPLINE_ESTIMATE_CATEGORIES): doors,
# windows and walls are the ARCHITECTURAL takeoff; fixtures are plumbing. NOT "gc"
# — that normalizes to gc_rollup, which prices overhead only, never the trades.
_CAT_DISCIPLINE = {"door": "architectural", "window": "architectural",
                   "plumbing_fixture": "plumbing", "appliance": "mechanical"}

# derived-quantity → (discipline, unit)
_DERIVED_META = {
    "floor_area_sf": ("architectural", "sf"),
    "roof_area_sf": ("structural", "sf"),
    # Written only when the traced outline passes the compactness check in
    # _check_perimeter — it drives footings, foundation wall and cladding, and a
    # wrong one is worse than an absent one.
    "perimeter_lf": ("structural", "lf"),
}


def takeoff_summary(t: Takeoff) -> dict[str, Any]:
    """JSON-safe summary for AIJob.output / workbench review."""
    return {
        "units": t.units,
        "scope_boundary": t.scope_boundary,   # legacy; views supersede it
        "scope_axis": t.scope_axis,
        "views": [{k: v[k] for k in
                   ("index", "source", "title", "bbox", "width_ft", "height_ft",
                    "counts", "sizes", "wall_lf", "rooms", "label_area_sf",
                    "rooms_centerline_sf", "geom_area_sf", "warnings")
                   if k in v} for v in t.views],
        "counts": {c: sum(b.values()) for c, b in t.counts.items() if c != "furniture"},
        "counts_detail": t.counts,
        # The generated schedule: openings broken down by size, read from block
        # ATTRIBUTES. This is what makes a material takeoff possible — a count of
        # "5 doors" cannot be priced, "3 x 36x80 + 2 x 30x80" can.
        "sizes": t.sizes,
        "excluded_existing_building": t.excluded_counts,
        "area_labels": t.area_labels,
        "wall_lf": t.wall_lf,
        "rooms": t.rooms,
        "room_schedule": t.room_schedule,
        "derived": t.derived,
        "warnings": t.warnings,
    }


def takeoff_to_overrides(db, project_id, t: Takeoff, document_id=None, page=None) -> int:
    """Persist a Takeoff as `quantity_overrides` rows (source='cad_extract'),
    idempotent by source_candidate_key. Written as CANDIDATES for human review
    (per the takeoff sequence's reconcile+confirm gate) — the caller/skill is
    responsible for surfacing them before they feed the estimate."""
    from decimal import Decimal
    from app.models.quantity_override import QuantityOverride

    rows: list[tuple[str, str, Decimal, str]] = []
    for cat, blocks in (t.counts or {}).items():
        if cat in ("furniture", "other", "marker"):
            continue
        n = sum(blocks.values())
        if n > 0:
            rows.append((_CAT_DISCIPLINE.get(cat, "architectural"), f"{cat}_count", Decimal(n), "ea"))
    for layer, lf in (t.wall_lf or {}).items():
        if lf and lf > 0:
            rows.append(("architectural", f"wall_lf_{layer.lower()}", Decimal(str(lf)), "lf"))
    for qtype, val in (t.derived or {}).items():
        # `derived` carries provenance strings alongside the numbers —
        # floor_area_source, geom_area_source. Comparing those to 0 raises, and
        # the exception was being swallowed per-sheet, so a project reported
        # "0 sheets read" with no quantities and no obvious cause.
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if val > 0 and qtype in _DERIVED_META:
            disc, unit = _DERIVED_META[qtype]
            rows.append((disc, qtype, Decimal(str(val)), unit))

    written = 0
    for disc, qtype, val, unit in rows:
        key = f"cad:{qtype}"
        # Look up by (document, key) — that is what the unique index is on.
        # Matching on project alone made a second sheet's window count overwrite
        # the first sheet's row and reassign its source document, so a two-sheet
        # set silently reported one sheet's quantities.
        existing = (
            db.query(QuantityOverride)
            .filter_by(project_id=project_id, source_document_id=document_id,
                       source_candidate_key=key)
            .one_or_none()
        )
        if existing:
            # Did the MACHINE's reading move? Compare machine-to-machine.
            #
            # This used to compare `existing.value` — the value in force — against
            # the fresh extraction. For a CORRECTED row those are a human's number
            # and a machine's number, so they always differed, and the branch below
            # overwrote the human's value on every single rebuild. Re-running a
            # takeoff against an UNCHANGED drawing silently replaced a site
            # measurement with the reading it had been corrected away from.
            prior_machine = existing.machine_value
            machine_moved = prior_machine is None or prior_machine != val

            existing.machine_value = val
            existing.unit, existing.discipline = unit, disc
            existing.source, existing.source_document_id = "cad_extract", document_id

            if not machine_moved:
                # The drawing still reads the same. Keep the value in force, keep
                # the review state, keep any correction. Re-reviewing forty
                # unchanged numbers to reach the two that moved is how a review
                # stops being done at all.
                pass
            elif existing.status == "corrected":
                # The basis moved, so this needs another look — but the human's
                # number is PRESERVED. They can see both and decide. Overwriting a
                # site measurement with a machine reading is not a correction, it
                # is data loss.
                existing.status = "candidate"
                existing.confirmed_by_id = None
                existing.confirmed_at = None
                existing.notes = ((existing.notes or "") + (
                    f" | re-extracted: drawing now reads {val}, was {prior_machine}. "
                    f"Your corrected value {existing.value} kept — re-confirm.")).strip()
            elif existing.status == "confirmed":
                # The human agreed with a reading that has since changed, so the
                # agreement no longer stands and the new reading takes its place.
                existing.value = val
                existing.status = "candidate"
                existing.confirmed_by_id = None
                existing.confirmed_at = None
                existing.notes = ((existing.notes or "") +
                                  " | re-extracted and CHANGED — re-confirm.").strip()
            elif existing.status == "excluded":
                # Excluding is a scope decision ("this does not apply here"), not a
                # judgement about the number. A different reading does not reverse
                # it, so the exclusion stands and the row stays out of the review
                # queue. machine_value above keeps the record current.
                pass
            else:
                # Still a candidate — nobody has looked at it, so just take the
                # newer reading.
                existing.value = val
        else:
            db.add(QuantityOverride(
                project_id=project_id, discipline=disc, quantity_type=qtype,
                value=val, unit=unit, source="cad_extract",
                source_document_id=document_id, source_page=page,
                source_candidate_key=key,
                status="candidate",
                machine_value=val,
                notes="Auto-extracted from CAD (scoped to new work). Reconcile vs PDF and confirm before estimate.",
            ))
            # The session runs with autoflush=False, so a pending row is invisible
            # to the next lookup. Without this flush a second build_pack in the
            # same session re-added every candidate and died on the unique index
            # at commit — losing the whole pack, not just the duplicate.
            db.flush()
        written += 1
    return written
