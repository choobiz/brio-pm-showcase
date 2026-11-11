"""One vocabulary for the rate and trade libraries: units and trades.

Rate and Trade Libraries plan, phase 1 (approved by Amir 2026-10-15). Until
then the library spoke four dialects that never met:

- units spelled eight ways on materials (sf, each, sheet, gal, bf, box, kg,
  roll), and differently again on assemblies (sqft, sf_floor) and the takeoff
  (sf, ea, lf), with nothing turning a $28 drywall *sheet* into a price per sf;
- trades named as people on rates ("plumber", "tilesetter"), as work in the
  subcontractor directory ("plumbing", "tile"), as "framing" on assemblies, and
  as numbered divisions on estimates.

Everything that stores a unit or a trade now resolves it here. Unknown units are
refused rather than guessed; an unknown trade is left unresolved (None), never
forced into "other", so the review can see it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# ── Units ────────────────────────────────────────────────────────────────────
#: code → dimension. A "package" unit (sheet, box, roll, bag, gal, l, kg, ton)
#: is how a material is BOUGHT; it reaches a takeoff measure only through a
#: coverage rate (STACK's purchase unit / unit of measure / coverage rate).
UNITS: dict[str, str] = {
    "ea": "count",
    "lf": "length",
    "sf": "area",
    "sy": "area",
    "cf": "volume",
    "cy": "volume",
    "bf": "volume",      # board feet: lumber's own measure
    "sheet": "package",
    "box": "package",
    "roll": "package",
    "bag": "package",
    "gal": "package",
    "l": "package",
    "kg": "package",
    "ton": "package",
    "lb": "package",
    "bundle": "package",
    "pail": "package",
    "tube": "package",
    "carton": "package",
    "can": "package",
    "pack": "package",
    "pair": "package",
    "drum": "package",
    "tote": "package",
    "hr": "time",
    "ls": "lump",        # lump sum: one of the whole thing
}

#: Measure units a takeoff produces (what an assembly or a coverage rate targets).
MEASURE_UNITS = frozenset(u for u, d in UNITS.items() if d in ("count", "length", "area", "volume"))

_ALIASES: dict[str, str] = {
    "each": "ea", "pc": "ea", "pcs": "ea", "piece": "ea", "unit": "ea", "count": "ea",
    "sqft": "sf", "sq ft": "sf", "sq. ft.": "sf", "ft2": "sf", "sft": "sf", "sf_floor": "sf",
    "sf_wall": "sf", "sf_roof": "sf",
    "lin ft": "lf", "lft": "lf", "linear ft": "lf", "ft": "lf",
    "sq yd": "sy", "yd2": "sy",
    "cu ft": "cf", "ft3": "cf", "cu yd": "cy", "yd3": "cy",
    "board ft": "bf", "fbm": "bf",
    "sheets": "sheet", "boxes": "box", "rolls": "roll", "bags": "bag",
    # Supplier price-list spellings (Renovo, 2026-07): SHT, BDL, CTN, LBS ...
    "sht": "sheet", "bdl": "bundle", "bundles": "bundle", "ctn": "carton", "pails": "pail",
    "tubes": "tube", "cans": "can", "pk": "pack", "lbs": "lb", "foot": "lf",
    "gallon": "gal", "gallons": "gal", "litre": "l", "liter": "l", "litres": "l",
    "hour": "hr", "hours": "hr", "hrs": "hr", "lump sum": "ls", "lot": "ls",
}

#: Exact conversions between measure units of one dimension.
_CONVERT: dict[tuple[str, str], Decimal] = {
    ("sy", "sf"): Decimal(9), ("sf", "sy"): Decimal(1) / Decimal(9),
    ("cy", "cf"): Decimal(27), ("cf", "cy"): Decimal(1) / Decimal(27),
}


class UnknownUnit(ValueError):
    pass


def normalize_unit(raw: str | None) -> str:
    """The canonical code for a unit as anyone wrote it, or UnknownUnit."""
    key = (raw or "").strip().lower()
    if key in UNITS:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    raise UnknownUnit(f"unknown unit {raw!r}; use one of {', '.join(UNITS)}")


def dimension(unit: str) -> str:
    return UNITS[normalize_unit(unit)]


def factor(from_unit: str, to_unit: str) -> Decimal | None:
    """Multiplier from one unit to another, or None when they don't convert
    (different dimensions, or a package unit that needs a coverage rate)."""
    a, b = normalize_unit(from_unit), normalize_unit(to_unit)
    if a == b:
        return Decimal(1)
    return _CONVERT.get((a, b))


# ── Trades ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Trade:
    key: str
    label: str
    residential: str | None   # BRIO Residential — 18 Divisions, by number
    masterformat: str | None  # CSI MasterFormat division, by number
    aliases: tuple[str, ...] = ()


TRADES: tuple[Trade, ...] = (
    Trade("general_conditions", "General conditions & PM", "01", "01", ("gc-pm", "gc", "pm", "project management")),
    Trade("general_labour", "General labour", "01", "01", ("labourer", "laborer", "labour")),
    Trade("engineering", "Engineering", "01", "01"),
    Trade("architecture", "Architecture & design", "01", "01", ("design",)),
    Trade("survey", "Survey", "01", "01"),
    Trade("cleaning", "Cleaning", "01", "01"),
    Trade("demolition", "Demolition", "02", "02", ("demo",)),
    Trade("disposal", "Disposal", "02", "02"),
    Trade("excavation", "Excavation & earthwork", "02", "31", ("earthwork",)),
    Trade("concrete", "Concrete & foundations", "03", "03", ("foundation", "foundations")),
    Trade("waterproofing", "Waterproofing", "03", "07"),
    Trade("masonry", "Masonry", "03", "04", ("mason",)),
    Trade("framing", "Framing & rough carpentry", "04", "06", ("framer", "rough carpentry", "carpenter")),
    Trade("windows_doors", "Windows & doors", "05", "08", ("windows", "doors", "glazing")),
    Trade("insulation", "Insulation", "06", "07", ("insulator",)),
    Trade("drywall", "Drywall", "06", "09", ("drywaller", "taper")),
    Trade("painting", "Painting & finishes", "07", "09", ("painter", "cabinet-refinisher", "cabinet refinisher")),
    Trade("flooring", "Flooring", "08", "09", ("flooring-installer", "flooring installer")),
    Trade("tile", "Tile", "08", "09", ("tilesetter", "tile setter", "tiler")),
    Trade("finish_carpentry", "Finish carpentry & interior doors", "09", "06",
          ("finish-carpenter", "finish carpenter", "trim")),
    Trade("cabinets_millwork", "Cabinets & millwork", "09", "06", ("cabinets", "millwork")),
    Trade("fire_protection", "Fire protection", "10", "21", ("sprinklers",)),
    Trade("appliances", "Appliances & equipment", "11", "11"),
    Trade("hvac", "HVAC & mechanical", "12", "23", ("mechanical", "hvac-tech")),
    Trade("plumbing", "Plumbing", "13", "22", ("plumber", "plumber-apprentice")),
    Trade("electrical", "Electrical", "14", "26", ("electrician", "electrician-apprentice")),
    Trade("low_voltage", "Low voltage", "14", "27", ("low-voltage", "data")),
    Trade("roofing", "Roofing", "15", "07", ("roofer",)),
    Trade("siding", "Exterior cladding & siding", "16", "07", ("cladding",)),
    Trade("landscaping", "Landscaping & site work", "17", "32", ("site work", "landscaper")),
    Trade("stormwater", "Stormwater & drainage", "18", "33", ("drainage",)),
    Trade("supplier", "Supplier", None, None),
    Trade("other", "Other", None, None),
)

TRADE_KEYS: tuple[str, ...] = tuple(t.key for t in TRADES)
_BY_KEY = {t.key: t for t in TRADES}
_TRADE_LOOKUP: dict[str, str] = {}
for _t in TRADES:
    for _name in (_t.key, _t.key.replace("_", " "), _t.key.replace("_", "-"), *_t.aliases):
        _TRADE_LOOKUP[_name.lower()] = _t.key


def normalize_trade(raw: str | None) -> str | None:
    """The trade key for a trade as anyone wrote it, or None if unknown."""
    if not raw:
        return None
    return _TRADE_LOOKUP.get(raw.strip().lower())


def trade(key: str) -> Trade:
    return _BY_KEY[key]


def division_for(trade_key: str | None, project_type: str = "residential") -> str | None:
    """The division number a trade's work is priced under."""
    if not trade_key or trade_key not in _BY_KEY:
        return None
    t = _BY_KEY[trade_key]
    return t.masterformat if "commercial" in (project_type or "") else t.residential


#: MasterFormat division → the division name in StatCan's building construction
#: price index (table 18-10-0289-01), which ages stored prices. 05 maps to
#: "Metal fabrications": the residential work it covers is misc metals, not
#: structural steel framing.
STATCAN_DIVISIONS: dict[str, str] = {
    "01": "General requirements", "02": "Demolition", "03": "Concrete", "04": "Masonry",
    "05": "Metal fabrications", "06": "Wood, plastics and composites",
    "07": "Thermal and moisture protection", "08": "Openings", "09": "Finishes",
    "10": "Specialities", "11": "Equipment", "14": "Conveying equipment",
    "21": "Fire Suppression", "22": "Plumbing", "23": "Heating, ventilation and air conditioning",
    "25": "Integrated automation", "26": "Electrical", "27": "Communications",
    "28": "Electrical safety and security", "31": "Earthwork", "32": "Exterior improvements",
    "33": "Utilities",
}
STATCAN_COMPOSITE = "Division composite"


def statcan_division(trade_key: str | None) -> str:
    """The StatCan index division a trade's prices move with (composite if unknown)."""
    t = _BY_KEY.get(trade_key or "")
    return STATCAN_DIVISIONS.get(t.masterformat if t else "", STATCAN_COMPOSITE)


# ── Pricing through a coverage rate ──────────────────────────────────────────
def cost_per_measure(unit_cost: Decimal, unit: str, *, measure_unit: str | None,
                     coverage: Decimal | None, waste_pct: Decimal | None = None) -> tuple[Decimal, str] | None:
    """A material's cost per takeoff measure unit, waste included.

    A sheet of 5/8" drywall at $28 with coverage 32 (sf per sheet) and 10% waste
    costs 28 / 32 × 1.10 = $0.9625 per sf. A material already bought by a measure
    unit (LVP by the sf) needs no coverage. A package unit with no coverage
    returns None: it cannot be priced against a takeoff, and saying so beats a
    number that means nothing.
    """
    u = normalize_unit(unit)
    waste = Decimal(1) + (Decimal(waste_pct or 0) / Decimal(100))
    if measure_unit:
        m = normalize_unit(measure_unit)
        if u == m:
            return (Decimal(unit_cost) * waste, m)
        if not coverage or Decimal(coverage) <= 0:
            return None
        return (Decimal(unit_cost) / Decimal(coverage) * waste, m)
    if u in MEASURE_UNITS:
        return (Decimal(unit_cost) * waste, u)
    return None
