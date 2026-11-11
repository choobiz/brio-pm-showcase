"""Load the Renovo price list into the read-only supplier catalogue (plan phase 2).

Amir approved the preload on 2026-10-15 ("Preload the Renovo catalogue? 7,912
lines at July 2026 prices, read-only, searchable, never in the estimate
prompt"). Writes only `catalogue_items`; never material_rates, which the
estimate prompt reads.

Dry run by default; `--apply` writes. Idempotent: a line is keyed by
(supplier, sku), so a re-run updates prices in place and adds nothing twice.

    PYTHONPATH=/app python scripts/load_renovo_catalogue.py /tmp/material-catalog-clean.json
    PYTHONPATH=/app python scripts/load_renovo_catalogue.py /tmp/material-catalog-clean.json --apply
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal

SUPPLIER = "Renovo"
AS_OF = date(2026, 7, 7)  # the price list was cleaned and filed on 2026-07-07
SOURCE_REF = "Renovo Properties price list, July 2026 (docs/rate-table/material-catalog-clean.json)"

# The unit reading and trade filing are shared with the price-sheet import
# (services/catalogue_import.py), so a re-import files a line the same way.
from app.services.catalogue_import import trade_for, unit_and_cost  # noqa: E402


def rows_from(path: str) -> list[dict]:
    data = json.load(open(path))
    out = []
    for r in data["skus"]:
        unit, cost = unit_and_cost(r["unit"], r["price"])
        out.append({
            "supplier": SUPPLIER, "sku": str(r["item"]), "description": r["description"],
            "supplier_group": r.get("group"), "supplier_category": r.get("supplier_category"),
            "raw_unit": r["unit"], "raw_price": Decimal(str(r["price"])),
            "unit": unit, "unit_cost": cost,
            "trade_key": trade_for(r.get("group"), r.get("supplier_category")),
            "as_of_date": AS_OF, "source_ref": SOURCE_REF,
        })
    return out


def apply(db, rows: list[dict]) -> tuple[int, int]:
    from app.models.catalogue import CatalogueItem

    existing = {c.sku: c for c in db.query(CatalogueItem).filter(CatalogueItem.supplier == SUPPLIER)}
    added = updated = 0
    for r in rows:
        c = existing.get(r["sku"])
        if c is None:
            db.add(CatalogueItem(**r))
            added += 1
        elif any(getattr(c, k) != v for k, v in r.items()):
            for k, v in r.items():
                setattr(c, k, v)
            updated += 1
    return added, updated


def main() -> None:
    import app.main  # noqa: F401  (registers every model so FKs resolve)
    from app.db import SessionLocal

    rows = rows_from(sys.argv[1])
    print(f"{len(rows)} lines; {len({r['sku'] for r in rows})} distinct SKUs")
    print("by trade:", Counter(r["trade_key"] for r in rows).most_common())
    print("by unit:", Counter(r["unit"] for r in rows).most_common())
    unread = Counter(r["raw_unit"] for r in rows if r["unit"] is None)
    print("printed units left unread (price kept as printed):", dict(unread))
    with SessionLocal() as db:
        added, updated = apply(db, rows)
        print(f"would add {added}, update {updated}")
        if "--apply" in sys.argv:
            db.commit()
            print("APPLIED.")
        else:
            db.rollback()
            print("Dry run: nothing written. Run with --apply.")


if __name__ == "__main__":
    main()
