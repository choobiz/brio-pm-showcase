"""Stage the starter assembly library for review (rate libraries plan, phase 2).

Two sets, both composed as material + loaded labour × hours:

- the 35 July drafts (docs/rate-table/installed-rates-DRAFT.json): Renovo
  material prices, labour re-priced from the $50/hr placeholder to the trade's
  Job Bank wage × (1 + labour_burden_pct), which Amir set at 14% on 2026-10-15;
- about 34 residential starters (framing, paint, flooring, tile, doors, trim,
  plumbing fixtures) priced from the materials already in the library.

Every assembly is written as status "draft", or "held" for the five commercial
items Amir has not confirmed (steel studs, bulkhead, TPO, polyiso). A draft or
held assembly never prices anything (rate_pricing.resolve_best_entry refuses
it); `--release` makes the reviewed drafts active, after Amir's go.

Labour hours on the starters are estimates from common residential norms, not
measured BRIO data. Each entry says so in its payload, and its confidence is
"fallback" so a measured or subcontract rate always outranks it.

    PYTHONPATH=/app python scripts/seed_starter_assemblies.py /tmp/installed-rates-DRAFT.json           # dry run
    PYTHONPATH=/app python scripts/seed_starter_assemblies.py /tmp/installed-rates-DRAFT.json --apply
    PYTHONPATH=/app python scripts/seed_starter_assemblies.py --release CODE[,CODE...] --apply
    PYTHONPATH=/app python scripts/seed_starter_assemblies.py /tmp/drafts.json --recompose CODE[,CODE...] --apply
"""
from __future__ import annotations

import json
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CREATED_BY = "starter-library"
DRAFT_AS_OF = date(2026, 7, 7)
EST_NOTE = ("Labour hours: Claude's estimate from common residential estimating norms, 2026-10-16. "
            "Unverified; replace with installed units / crew hours from a BRIO job.")

#: Roofers aren't in the wage table yet (the July drafts used a flat $50/hr).
ROOFER = {
    "trade": "roofer", "base_rate": Decimal("39.00"),
    "source_ref": "Job Bank wage report NOC 73110 Roofers and shinglers, BC high (reference period 2023-2024)",
    "payload": {"jobbank": {"low": 24.0, "med": 30.0, "high": 39.0, "title": "Roofers and shinglers"},
                "wage_basis": "high", "derivation": "Job Bank BC high, as posted",
                "url": "https://www.jobbank.gc.ca/marketreport/wages-occupation/17037/39070"},
}

#: Draft group → (trade key of the work, labour occupation that does it).
#: Stucco is NOC 73102 (plasterers, drywall installers and lathers), the same
#: occupation as the drywaller wage.
DRAFT_GROUPS = {
    "Drywall & Steel Framing": ("drywall", "drywaller"),
    "Insulation": ("insulation", "insulator"),
    "Roofing & Membranes": ("roofing", "roofer"),
    "Acoustic Ceilings": ("drywall", "drywaller"),
    "Stucco & Exterior": ("siding", "drywaller"),
    "Adhesives & Sealants": ("other", "labourer"),
}

#: Draft name → (code, trade_key override or None, status, review flag or None).
DRAFT_CODES = {
    'Steel stud partition — 3-5/8" 25ga @16" o.c. (non-LB)': ("STL-STUD-358-25GA", "framing", "held", "Commercial; Amir has not confirmed the $/sf"),
    'Steel stud partition — 6" 20ga @16" o.c.': ("STL-STUD-6-20GA", "framing", "held", "Commercial; Amir has not confirmed the $/sf"),
    'Furring / hat channel on concrete @16" o.c.': ("STL-FURRING-HAT", "framing", "draft", None),
    'Resilient channel (acoustic) @24" o.c.': ("DW-RESILIENT-CHANNEL", None, "draft", None),
    "Steel stud bulkhead / soffit framing": ("STL-BULKHEAD", "framing", "held", "Commercial; Amir has not confirmed the $/lf"),
    'Drywall 1/2" — walls, hung + taped + L4 finish': ("DW-12-WALL-L4", None, "draft", None),
    'Drywall 5/8" Type-X — walls, hung + L4': ("DW-58X-WALL-L4", None, "draft", None),
    'Drywall 5/8" Type-X — ceilings, hung + L4': ("DW-58X-CEIL-L4", None, "draft", None),
    'Drywall double-layer 5/8" (fire/STC), walls': ("DW-58X-DOUBLE-WALL", None, "draft", None),
    'Drywall 1/2" moisture-resistant — walls': ("DW-12-MR-WALL", None, "draft", None),
    'Cement board 1/2" (Durock) tile backer': ("DW-CEMENT-BOARD", "tile", "draft", None),
    "Level 5 finish upgrade (skim coat)": ("DW-L5-UPGRADE", None, "draft", None),
    "Corner bead — installed + finished": ("DW-CORNER-BEAD", None, "draft", None),
    "Tape & fill existing seams only": ("DW-TAPE-FILL", None, "draft", None),
    "Drywall patch/repair — small (<4 SF)": ("DW-PATCH-SMALL", None, "draft", None),
    "Access panel 12x12 — supply + install": ("DW-ACCESS-PANEL", None, "draft", None),
    "Batt R20 fiberglass — 2x6 walls": ("INS-BATT-R20", None, "draft", "300 sf/hr per installer looks fast; check against a job"),
    "Batt mineral wool (Roxul) — walls": ("INS-BATT-ROXUL", None, "draft", "250 sf/hr per installer looks fast; check against a job"),
    "Attic batt R40 — flat ceiling": ("INS-ATTIC-R40", None, "draft", "350 sf/hr per installer looks fast; check against a job"),
    'Rigid EPS board 2" — walls': ("INS-EPS-2IN", None, "draft", "Material $0.10/sf is far below what 2\" EPS costs; check the SKU"),
    'Closed-cell spray foam 2" — walls': ("INS-SPRAY-CC-2IN", None, "draft", "Usually a subcontract price, not material + labour; labour at 500 sf/hr is a placeholder"),
    "6-mil poly vapour barrier": ("INS-POLY-6MIL", None, "draft", None),
    "Sound batt (Safe'n'Sound) in partition": ("INS-SOUND-BATT", None, "draft", None),
    "2-ply SBS mod-bit (base+cap, torch)": ("RF-SBS-2PLY", None, "draft", "Installed $/sf looks low for 2-ply torch-on; check with a roofer's quote"),
    "Single-ply TPO membrane (adhered)": ("RF-TPO-ADHERED", None, "held", "Commercial; material estimated, not a catalogue price"),
    "Asphalt shingles 3-tab — sloped roof": ("RF-SHINGLE-3TAB", None, "draft", "3-tab is rarely used now; laminate (architectural) is the usual residential shingle"),
    'Polyiso roof insulation board 2"': ("RF-POLYISO-2IN", "insulation", "held", "Commercial; material estimated, not a catalogue price"),
    "Building paper / felt underlay": ("RF-UNDERLAY-FELT", None, "draft", None),
    "Suspended T-bar grid + 2x4 lay-in tile": ("CLG-TBAR-2X4", None, "draft", None),
    "Suspended drywall ceiling grid (grid only)": ("CLG-DW-GRID", None, "draft", None),
    "3-coat cement stucco on metal lath": ("EXT-STUCCO-3COAT", None, "draft", None),
    "Stucco accessories — corner/casing/weep": ("EXT-STUCCO-ACCESS", None, "draft", None),
    "Joint sealant + backer rod": ("SEAL-JOINT-BACKER", None, "draft", None),
    "Firestop — pipe/cable penetration": ("FS-PENETRATION", "drywall", "draft", None),
    "Firestop — head-of-wall joint": ("FS-HEAD-OF-WALL", "drywall", "draft", None),
}

#: Amir, 2026-10-15: stucco three-coat "should be ... closer to 10" $/sf.
INSTALLED_OVERRIDE = {"EXT-STUCCO-3COAT": (Decimal("10.00"), "Amir 2026-10-15: closer to $10/sf")}

PREMIUM, STANDARD, PRIMER = "Interior latex (premium)", "Interior latex (standard)", "Primer"
_NO_SETTING = "Tile only: thinset, grout and membrane have no price in the library yet."
_FINISH_SET = "Finish set only: rough-in, supply/drain lines and permits excluded."

#: Residential starters. materials: [(material_rates.name, qty per assembly unit)],
#: qty in the material's measure unit (sf of coverage, bf, ea).
STARTERS = [
    # code, name, trade_key, labour occupation, unit, materials, hours/unit, scope note
    ("FR-INT-2X4-8FT", 'Interior partition 2x4 @16" o.c., 8\' high', "framing", "framer", "lf",
     [("2x4 SPF #2 KD", 7.33)], 0.25, "Studs (1 per lf incl. ends) + triple plate. Headers, blocking excluded."),
    ("FR-INT-2X4-9FT", 'Interior partition 2x4 @16" o.c., 9\' high', "framing", "framer", "lf",
     [("2x4 SPF #2 KD", 8.0)], 0.27, "Studs (1 per lf incl. ends) + triple plate. Headers, blocking excluded."),
    ("FR-EXT-2X6-8FT", 'Exterior wall 2x6 @16" o.c., 8\' high, OSB sheathed', "framing", "framer", "lf",
     [("2x6 SPF #2 KD", 11.0), ('1/2" OSB sheathing (4x8)', 8.0)], 0.35, "Studs + triple plate + 1/2\" OSB. Headers, hold-downs excluded."),
    ("FR-EXT-2X6-9FT", 'Exterior wall 2x6 @16" o.c., 9\' high, OSB sheathed', "framing", "framer", "lf",
     [("2x6 SPF #2 KD", 12.0), ('1/2" OSB sheathing (4x8)', 9.0)], 0.38, "Studs + triple plate + 1/2\" OSB. Headers, hold-downs excluded."),
    ("PT-WALL-REPAINT-2C", "Repaint walls, 2 coats premium", "painting", "painter", "sf",
     [(PREMIUM, 2)], 2 / 110, "Per sf of wall. Light prep, cut and roll; patching excluded."),
    ("PT-WALL-NEW-P2C", "New drywall walls, primer + 2 coats premium", "painting", "painter", "sf",
     [(PRIMER, 1), (PREMIUM, 2)], 3 / 120, "Per sf of wall."),
    ("PT-CEIL-2C", "Ceilings, 2 coats standard flat", "painting", "painter", "sf",
     [(STANDARD, 2)], 2 / 100, "Per sf of ceiling."),
    ("PT-TRIM-2C", "Baseboard or casing, 2 coats", "painting", "painter", "lf",
     [(PREMIUM, 0.67)], 0.04, "Per lf of trim (about 4\" face)."),
    ("PT-DOOR-2C", "Interior door + frame, 2 coats both sides", "painting", "painter", "ea",
     [(PREMIUM, 92)], 1.5, "Slab both sides + frame, about 46 sf a coat."),
    ("FL-LVP", "LVP, floating", "flooring", "flooring-installer", "sf",
     [("LVP (luxury vinyl plank)", 1)], 0.025, "Underlay, transitions and subfloor prep excluded."),
    ("FL-LAMINATE", "Laminate, floating", "flooring", "flooring-installer", "sf",
     [("Laminate flooring", 1)], 0.025, "Underlay, transitions and subfloor prep excluded."),
    ("FL-ENG-HARDWOOD", "Engineered hardwood, glued or nailed", "flooring", "flooring-installer", "sf",
     [("Engineered hardwood", 1)], 0.04, "Adhesive, underlay and subfloor prep excluded."),
    ("FL-SOLID-HARDWOOD", "Solid hardwood, nail-down (prefinished)", "flooring", "flooring-installer", "sf",
     [("Solid hardwood", 1)], 0.05, "Underlay and subfloor prep excluded; site sanding/finish excluded."),
    ("FL-CARPET", "Carpet, stretched", "flooring", "flooring-installer", "sf",
     [("Carpet", 1)], 0.015, "Pad and tack strip excluded."),
    ("TL-FLOOR-PORCELAIN", "Porcelain floor tile", "tile", "tilesetter", "sf",
     [("Porcelain tile — floor", 1)], 0.10, _NO_SETTING),
    ("TL-FLOOR-CERAMIC", "Ceramic floor tile", "tile", "tilesetter", "sf",
     [("Ceramic tile — floor", 1)], 0.08, _NO_SETTING),
    ("TL-WALL-SUBWAY", "Subway wall tile (ceramic)", "tile", "tilesetter", "sf",
     [("Subway tile (ceramic) — wall", 1)], 0.12, _NO_SETTING),
    ("TL-WALL-LARGE-FORMAT", "Large-format porcelain wall tile", "tile", "tilesetter", "sf",
     [("Large-format porcelain tile — wall", 1)], 0.12, _NO_SETTING),
    ("TL-WALL-MOSAIC", "Mosaic / accent wall tile", "tile", "tilesetter", "sf",
     [("Mosaic / accent tile", 1)], 0.20, _NO_SETTING),
    ("FC-BASEBOARD-INSTALL", "Baseboard, install only", "finish_carpentry", "finish-carpenter", "lf",
     [], 0.05, "Labour only: the baseboard itself is priced by selection."),
    ("FC-CASING-INSTALL", "Door or window casing, install only", "finish_carpentry", "finish-carpenter", "lf",
     [], 0.06, "Labour only: the casing itself is priced by selection."),
    ("DR-INT-HOLLOW", "Interior door, hollow-core, supply + hang", "windows_doors", "finish-carpenter", "ea",
     [("Interior door — hollow-core", 1)], 1.5, "Pre-hung; hardware and casing excluded."),
    ("DR-INT-SOLID", "Interior door, solid-core, supply + hang", "windows_doors", "finish-carpenter", "ea",
     [("Interior door — solid-core", 1)], 1.75, "Pre-hung; hardware and casing excluded."),
    ("DR-BARN", "Barn door with hardware, supply + install", "windows_doors", "finish-carpenter", "ea",
     [("Barn door (with hardware)", 1)], 2.5, "Includes the track; blocking in the wall excluded."),
    ("DR-EXT-ENTRY", "Exterior entry door, supply + install", "windows_doors", "finish-carpenter", "ea",
     [("Exterior entry door", 1)], 4.0, "Pre-hung; flashing and sill pan included in labour, hardware excluded."),
    ("PL-TOILET", "Toilet, supply + set", "plumbing", "plumber", "ea", [("Toilet", 1)], 1.5, _FINISH_SET),
    ("PL-VANITY-30-36", 'Vanity 30–36", supply + set', "plumbing", "plumber", "ea",
     [('Bathroom vanity (30-36")', 1)], 2.5, _FINISH_SET + " Faucet separate."),
    ("PL-VANITY-48-60", 'Vanity 48–60", supply + set', "plumbing", "plumber", "ea",
     [('Bathroom vanity (48-60")', 1)], 3.0, _FINISH_SET + " Faucet separate."),
    ("PL-FAUCET-BATH", "Bathroom faucet, supply + install", "plumbing", "plumber", "ea",
     [("Bathroom faucet", 1)], 1.0, _FINISH_SET),
    ("PL-FAUCET-KITCHEN", "Kitchen faucet, supply + install", "plumbing", "plumber", "ea",
     [("Kitchen faucet", 1)], 1.25, _FINISH_SET),
    ("PL-SHOWER-VALVE", "Shower valve + trim, supply + install", "plumbing", "plumber", "ea",
     [("Shower valve + trim", 1)], 3.0, "Valve rough + trim; supply lines to the valve excluded."),
    ("PL-TUB-ALCOVE", "Alcove tub, supply + set", "plumbing", "plumber", "ea",
     [("Bathtub (alcove)", 1)], 4.0, _FINISH_SET),
    ("PL-TUB-FREESTANDING", "Freestanding tub, supply + set", "plumbing", "plumber", "ea",
     [("Freestanding tub", 1)], 4.0, _FINISH_SET + " Filler separate."),
    ("PL-SHOWER-BASE", "Acrylic shower base, supply + set", "plumbing", "plumber", "ea",
     [("Shower base (acrylic)", 1)], 3.0, _FINISH_SET),
]


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def burden_pct(db) -> Decimal:
    from app.models.prep import AppSetting

    s = db.get(AppSetting, "labour_burden_pct")
    if s is None or "pct" not in (s.value or {}):
        raise SystemExit("app_settings.labour_burden_pct is not set; it decides every labour cost")
    return Decimal(str(s.value["pct"]))


def loaded_rates(db) -> tuple[dict[str, dict], Decimal]:
    """Occupation → {wage, loaded, ref}: the as-posted wage × (1 + burden)."""
    from app.services.rate_pricing import best_labour_rate

    pct = burden_pct(db)
    out = {}
    for occ in {*(o for _, o in DRAFT_GROUPS.values()), *(s[3] for s in STARTERS)}:
        r = best_labour_rate(db, trade=occ)
        if r is None:
            continue
        # effective_rate, not base_rate: a row can carry a deliberate premium
        # (finish carpenter = carpenter x 1.1429), and effective is the rate the
        # library prices with. Using base priced 6 assemblies at $49.83/hr loaded
        # instead of $56.95 (found 2026-10-16).
        wage = Decimal(r.effective_rate)
        out[occ] = {"wage": wage, "loaded": _q(wage * (1 + pct / 100)), "ref": r.source_ref,
                    "as_of": r.as_of_date}
    return out, pct


def ensure_roofer(db) -> bool:
    from app.models.rate_library import LabourRate

    if db.query(LabourRate).filter(LabourRate.trade == "roofer", LabourRate.is_active.is_(True)).first():
        return False
    db.add(LabourRate(trade="roofer", trade_key="roofing", source="workbc", base_rate=ROOFER["base_rate"],
                      discount_pct=Decimal(0), effective_rate=ROOFER["base_rate"],
                      as_of_date=date(2026, 10, 16), source_ref=ROOFER["source_ref"],
                      payload=ROOFER["payload"], created_by=CREATED_BY))
    db.flush()
    return True


def material_cost(db, name: str) -> tuple[Decimal, date | None, str]:
    """Price per measure unit, waste included, of a library material."""
    from app.services.library_vocab import cost_per_measure
    from app.services.rate_pricing import best_material_rate

    m = best_material_rate(db, name=name)
    if m is None:
        raise SystemExit(f"material '{name}' is not in the library")
    r = cost_per_measure(m.unit_cost, m.unit, measure_unit=m.measure_unit, coverage=m.coverage,
                         waste_pct=m.waste_pct)
    if r is None:
        raise SystemExit(f"material '{name}' can't be priced per measure (no coverage)")
    return r[0], m.as_of_date, str(m.id)


def plan(db, drafts_path: str | None) -> list[dict]:
    """Every assembly to stage, fully priced, with its derivation."""
    from app.services.library_vocab import normalize_unit

    rates, pct = loaded_rates(db)
    out: list[dict] = []
    if drafts_path:
        d = json.load(open(drafts_path))
        for group, items in d["trades"].items():
            trade_key, occ = DRAFT_GROUPS[group]
            lab = rates[occ]
            for a in items:
                code, tk, status, flag = DRAFT_CODES[a["assembly"]]
                mat = Decimal(str(a["material_cost"]))
                hours = Decimal(1) / Decimal(str(a["productivity_per_hr"]))
                labour = lab["loaded"] * hours
                note = None
                if code in INSTALLED_OVERRIDE:
                    target, note = INSTALLED_OVERRIDE[code]
                    labour = target - mat
                    hours = labour / lab["loaded"]
                out.append({
                    "code": code, "name": a["assembly"], "trade_key": tk or trade_key,
                    "unit": normalize_unit(a["unit"]), "status": status, "set": "july-draft",
                    "unit_rate": _q(mat + labour), "as_of": DRAFT_AS_OF, "confidence": "reference",
                    "scope_notes": a.get("note") or a.get("basis"),
                    "payload": {
                        "material": float(_q(mat)), "material_source": "Renovo price list, July 2026",
                        "materials": a.get("materials"),
                        "labour": float(_q(labour)), "labour_occupation": occ,
                        "wage": float(lab["wage"]), "burden_pct": float(pct), "loaded_rate": float(lab["loaded"]),
                        "hours_per_unit": round(float(hours), 4),
                        "productivity_source": a.get("basis") or "July 2026 draft (market research)",
                        "was": {"installed": a["installed_rate"], "labour_rate": a.get("labor_rate_per_hr")},
                        "set_by": note, "review_flag": flag,
                    },
                })
    for code, name, tk, occ, unit, mats, hrs, scope in STARTERS:
        lab = rates[occ]
        hours = Decimal(str(hrs))
        parts, dates = [], []
        mat_total = Decimal(0)
        for mname, qty in mats:
            per, as_of, mid = material_cost(db, mname)
            line = per * Decimal(str(qty))
            mat_total += line
            parts.append({"material": mname, "material_id": mid, "qty": qty, "per_unit": float(_q(per)),
                          "cost": float(_q(line))})
            if as_of:
                dates.append(as_of)
        labour = lab["loaded"] * hours
        out.append({
            "code": code, "name": name, "trade_key": tk, "unit": unit, "status": "draft", "set": "starter",
            "unit_rate": _q(mat_total + labour), "as_of": min(dates) if dates else lab["as_of"],
            "confidence": "fallback", "scope_notes": scope,
            "payload": {
                "material": float(_q(mat_total)), "materials": parts,
                "labour": float(_q(labour)), "labour_occupation": occ, "wage": float(lab["wage"]),
                "burden_pct": float(pct), "loaded_rate": float(lab["loaded"]),
                "hours_per_unit": round(float(hours), 4), "productivity_source": EST_NOTE,
                "set_by": None, "review_flag": None,
            },
        })
    return out


def apply(db, rows: list[dict], recompose: set[str] = frozenset()) -> dict[str, int]:
    from app.models.rate_library import RateAssembly, RateEntry

    n = {"assemblies_added": 0, "entries_added": 0, "unchanged": 0, "skipped_active": 0}
    for r in rows:
        a = db.query(RateAssembly).filter(RateAssembly.code == r["code"]).one_or_none()
        if a is not None and a.status == "active" and r["code"] not in recompose:
            n["skipped_active"] += 1  # released already: a re-run never edits a live rate
            continue
        if a is None:
            a = RateAssembly(code=r["code"], name=r["name"], trade=r["trade_key"], trade_key=r["trade_key"],
                             unit=r["unit"], project_type="any", status=r["status"],
                             scope_notes=r["scope_notes"], created_by=CREATED_BY,
                             payload={"set": r["set"]})
            db.add(a)
            db.flush()
            n["assemblies_added"] += 1
        elif a.status != "active":
            a.status = r["status"]
        cur = db.query(RateEntry).filter(RateEntry.assembly_id == a.id, RateEntry.is_active.is_(True),
                                         RateEntry.created_by == CREATED_BY).all()
        if any(e.unit_rate == r["unit_rate"] and e.payload == r["payload"] for e in cur):
            n["unchanged"] += 1
            continue
        e = RateEntry(assembly_id=a.id, unit_rate=r["unit_rate"], unit=r["unit"], source_type="composed",
                      confidence=r["confidence"], project_type="any", as_of_date=r["as_of"],
                      source_ref=f"Starter library ({r['set']}), composed 2026-10-16",
                      payload=r["payload"], created_by=CREATED_BY)
        db.add(e)
        db.flush()
        for old in cur:
            old.is_active, old.superseded_by = False, e.id
        n["entries_added"] += 1
    return n


def release(db, codes: list[str]) -> int:
    """Make reviewed draft assemblies active. Held ones are refused by name."""
    from app.models.rate_library import RateAssembly

    rows = db.query(RateAssembly).filter(RateAssembly.code.in_(codes)).all()
    found = {a.code: a for a in rows}
    missing = sorted(set(codes) - set(found))
    held = sorted(c for c, a in found.items() if a.status == "held")
    if missing or held:
        raise SystemExit(f"not released: unknown {missing}, held {held}")
    for a in rows:
        a.status = "active"
    return len(rows)


def main() -> None:
    import app.main  # noqa: F401  (registers every model so FKs resolve)
    from app.db import SessionLocal

    with SessionLocal() as db:
        if "--release" in sys.argv:
            codes = sys.argv[sys.argv.index("--release") + 1].split(",")
            print(f"releasing {release(db, codes)}")
        else:
            path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
            if ensure_roofer(db):
                print("adds labour rate: roofer $39.00 (Job Bank BC high)")
            rows = plan(db, path)
            for r in rows:
                p = r["payload"]
                print(f"{r['status']:5} {r['code']:22} ${float(r['unit_rate']):9.2f}/{r['unit']:2}  "
                      f"mat {p['material']:8.2f} lab {p['labour']:7.2f} ({p['hours_per_unit']} h @ "
                      f"{p['loaded_rate']})  {p.get('review_flag') or ''}")
            # --recompose CODE,...: re-price named LIVE assemblies (new entry,
            # the old one superseded). Only on Amir's OK: it changes a price.
            rc = set(sys.argv[sys.argv.index("--recompose") + 1].split(",")) if "--recompose" in sys.argv else set()
            print(apply(db, [r for r in rows if not rc or r["code"] in rc], rc))
        if "--apply" in sys.argv:
            db.commit()
            print("APPLIED.")
        else:
            db.rollback()
            print("Dry run: nothing written.")


if __name__ == "__main__":
    main()
