"""File the existing rate-library rows into the shared vocabulary (plan phase 1).

Dry run by default: prints every change it would make and changes nothing.
`--apply` writes them in one transaction. More than 25 rows change, so per the
production rule the dry run goes to Amir first and --apply runs only on his go.

    PYTHONPATH=/app python scripts/file_library_vocab.py            # dry run
    PYTHONPATH=/app python scripts/file_library_vocab.py --apply    # after "go"

What it sets, per row:
- unit → the canonical code (each → ea, sqft / sf_floor → sf);
- trade_key from the trade name (plumber → plumbing), with door materials filed
  under windows_doors (division 05), not finish carpentry;
- category, from the item's name (Board, Fasteners, Interior doors …);
- measure_unit + coverage only where the size is in the name (a 4x8 sheet
  covers 32 sf). Boxes, rolls, kg and gallons are LEFT EMPTY and listed: their
  coverage is a judgement (sf per gallon of paint), not arithmetic.
It also lists, without changing, rows that need a person: a labour rate whose
source label looks wrong, and directory contacts whose category looks wrong.
"""
from __future__ import annotations

import re
import sys
from decimal import Decimal

from app.db import SessionLocal
from app.models.rate_library import LabourRate, MaterialRate, RateAssembly, RateEntry
from app.models.trade import Trade
from app.services.library_vocab import normalize_trade, normalize_unit

#: name pattern → category, per trade key. First match wins.
CATEGORY_RULES: dict[str, list[tuple[str, str]]] = {
    "drywall": [(r"gypsum|board", "Board"), (r"screw", "Fasteners"), (r"tape", "Tape"),
                (r"compound|mud", "Compound")],
    "windows_doors": [(r"exterior", "Exterior doors"), (r"door", "Interior doors")],
    "flooring": [(r"carpet", "Carpet"), (r"hardwood", "Wood flooring"), (r"lvp|vinyl|laminate", "Resilient & laminate")],
    "framing": [(r"osb|plywood|sheathing", "Sheathing"), (r"2x\d", "Dimensional lumber")],
    "insulation": [(r"batt", "Batt"), (r"rigid|board", "Rigid board"), (r"spray", "Spray foam")],
    "painting": [(r"primer", "Primer"), (r"paint|latex", "Paint")],
    "plumbing": [(r"vanity", "Vanities"), (r"faucet|valve", "Faucets & valves"),
                 (r"toilet|tub|shower base", "Fixtures")],
    "tile": [(r"wall|subway|feature", "Wall tile"), (r"floor", "Floor tile"), (r"mosaic|accent", "Wall tile")],
}

_SHEET = re.compile(r"\((\d+)\s*x\s*(\d+)\)")  # "(4x8)" in feet


def _category(trade_key: str | None, name: str) -> str | None:
    for pattern, cat in CATEGORY_RULES.get(trade_key or "", []):
        if re.search(pattern, name, re.I):
            return cat
    return None


def plan(db) -> tuple[list[tuple[str, str, dict]], list[str]]:
    """(changes, needs_a_person). A change = (table, row label, {field: (old, new)})."""
    changes: list[tuple[str, str, dict]] = []
    review: list[str] = []

    for m in db.query(MaterialRate).order_by(MaterialRate.trade, MaterialRate.name):
        new: dict = {}
        unit = normalize_unit(m.unit)
        key = normalize_trade(m.trade)
        if key == "finish_carpentry" and re.search(r"door", m.name, re.I):
            key = "windows_doors"
        cat = _category(key, m.name)
        if unit != m.unit:
            new["unit"] = (m.unit, unit)
        if key != m.trade_key:
            new["trade_key"] = (m.trade_key, key)
        if cat and cat != m.category:
            new["category"] = (m.category, cat)
        if unit == "sheet" and m.coverage is None:
            s = _SHEET.search(m.name)
            if s:
                new["measure_unit"] = (m.measure_unit, "sf")
                new["coverage"] = (m.coverage, Decimal(int(s.group(1)) * int(s.group(2))))
        if unit in ("box", "roll", "kg", "gal") and m.coverage is None:
            review.append(f"material '{m.name}': bought by the {unit}; how many sf does one cover? (left empty)")
        if not key:
            review.append(f"material '{m.name}': trade '{m.trade}' has no key")
        if new:
            changes.append(("material_rates", m.name, new))

    for r in db.query(LabourRate).order_by(LabourRate.trade):
        key = normalize_trade(r.trade)
        if key != r.trade_key:
            changes.append(("labour_rates", r.trade, {"trade_key": (r.trade_key, key)}))
        if r.trade == "gc-pm" and r.source == "workbc":
            review.append(f"labour '{r.trade}' ${r.base_rate}/hr is labelled source=workbc, but Job Bank "
                          "publishes no PM rate: is it BRIO's own (own) or an estimate (market)?")

    for a in db.query(RateAssembly).order_by(RateAssembly.code):
        new = {}
        unit = normalize_unit(a.unit)
        if unit != a.unit:
            new["unit"] = (a.unit, unit)
            if a.unit == "sf_floor":
                new["payload.measure_basis"] = ((a.payload or {}).get("measure_basis"), "floor area")
        key = normalize_trade(a.trade)
        if key != a.trade_key:
            new["trade_key"] = (a.trade_key, key)
        if new:
            changes.append(("rate_assemblies", a.code, new))

    for e in db.query(RateEntry):
        unit = normalize_unit(e.unit)
        if unit != e.unit:
            changes.append(("rate_entries", str(e.id)[:8], {"unit": (e.unit, unit)}))

    for t in db.query(Trade).order_by(Trade.company_name):
        if (t.company_name or "").lower().startswith("renovo") and t.trade_category == "engineering":
            review.append(f"directory '{t.company_name}': filed as engineering, but Renovo's price list is a "
                          "supplier catalogue: change to supplier?")
    return changes, review


def apply(db, changes) -> int:
    rows = {"material_rates": MaterialRate, "labour_rates": LabourRate, "rate_assemblies": RateAssembly}
    n = 0
    for table, label, fields in changes:
        if table == "rate_entries":
            obj = next(e for e in db.query(RateEntry) if str(e.id)[:8] == label)
        else:
            model = rows[table]
            col = getattr(model, {"material_rates": "name", "labour_rates": "trade",
                                  "rate_assemblies": "code"}[table])
            matches = db.query(model).filter(col == label).all()
            assert len(matches) == 1, (table, label, len(matches))
            obj = matches[0]
        for field, (_, value) in fields.items():
            if field.startswith("payload."):
                obj.payload = {**(obj.payload or {}), field.split(".", 1)[1]: value}
            else:
                setattr(obj, field, value)
        n += 1
    return n


def main() -> None:
    with SessionLocal() as db:
        changes, review = plan(db)
        for table, label, fields in changes:
            print(f"{table:16} {label[:40]:40} " + "; ".join(f"{k}: {o!r} -> {v!r}" for k, (o, v) in fields.items()))
        print(f"\n{len(changes)} rows would change.")
        print("\nNeeds a person (not changed):")
        for line in review:
            print(" -", line)
        if "--apply" in sys.argv:
            n = apply(db, changes)
            db.commit()
            print(f"\nAPPLIED to {n} rows.")
        else:
            print("\nDry run: nothing changed. Run with --apply after the go.")


if __name__ == "__main__":
    main()
