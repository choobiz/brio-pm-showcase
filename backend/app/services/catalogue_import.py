"""Supplier price sheets into the catalogue: checked, then applied, then undoable.

Phase 4 of the libraries plan, approved by Amir 2026-10-16:
- nothing is written until the person has seen the check (new, changed,
  moved more than 25%, unchanged, problems);
- library materials whose SKU is on the sheet are offered, ticked, and change
  only if left ticked; each keeps its old price as history;
- the last import can be undone, putting back every price and removing the
  lines it added.

Rows with a problem (no SKU, no price, a unit with no safe reading, a SKU twice)
are skipped and listed, never guessed.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

#: A price that moved more than this is flagged for a look (Amir, 2026-10-16).
BIG_MOVE_PCT = 25.0
FIELDS = ("sku", "description", "unit", "price", "category")

#: Price-list units quoted per thousand: MSF = 1,000 sf, MLF = 1,000 lf.
_PER_THOUSAND = {"MSF": "sf", "MLF": "lf"}

#: Renovo's own filing → trade key (category first, then group). Other
#: suppliers' categories go through the shared trade vocabulary instead.
_GROUP_TRADE = {
    "Drywall & Steel Framing": "drywall", "Insulation": "insulation", "Roofing & Membranes": "roofing",
    "Acoustic Ceilings": "drywall", "Stucco & Exterior": "siding", "Adhesives & Sealants": "other",
}
_CATEGORY_TRADE = {
    "Steel": "framing", "Metal": "framing", "Acrylics, Textures, and Paints": "painting",
    "Moulding": "finish_carpentry", "Air Barriers": "siding", "Building Paper": "siding", "Tyvek": "siding",
    "Firestopping": "drywall", "FRP": "drywall", "Concrete Forms": "concrete", "Lighting": "electrical",
    "Pipe and Fittings": "plumbing",
}


def unit_and_cost(raw_unit: str, price: Decimal | float) -> tuple[str | None, Decimal | None]:
    """Canonical unit and price per it, or (None, None) when the printed unit
    has no safe reading (YARD: linear or square?)."""
    from app.services.library_vocab import UnknownUnit, normalize_unit

    p = Decimal(str(price))
    key = (raw_unit or "").strip().upper()
    if key in _PER_THOUSAND:
        return _PER_THOUSAND[key], (p / 1000).quantize(Decimal("0.0001"))
    try:
        return normalize_unit(key), p
    except UnknownUnit:
        return None, None


def trade_for(group: str | None, category: str | None) -> str | None:
    from app.services.library_vocab import normalize_trade

    return (_CATEGORY_TRADE.get(category or "") or _GROUP_TRADE.get(group or "")
            or normalize_trade(category) or normalize_trade(group))


# ── Reading a sheet ──────────────────────────────────────────────────────────


def read_sheet(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    """(headers, rows) from a CSV or the first sheet of an .xlsx. The header is
    the first row with at least two filled cells (price lists often open with a
    title line)."""
    if (filename or "").lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        grid = [["" if v is None else str(v).strip() for v in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        grid = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    start = next((i for i, r in enumerate(grid) if sum(1 for c in r if c) >= 2), None)
    if start is None:
        return [], []
    headers = grid[start]
    rows = [r for r in grid[start + 1:] if any(c for c in r)]
    return headers, rows


_GUESS = {
    "sku": r"\b(sku|item(\s*(id|no|#|number|code))?|part|product\s*(no|code)|code|article)\b",
    "description": r"\b(desc|description|name|product)\b",
    "unit": r"\b(uom|unit|u/m|per)\b",
    "price": r"\b(price|cost|rate|amount|each)\b",
    "category": r"\b(category|group|class|family|department)\b",
}


def guess_map(headers: list[str]) -> dict[str, int]:
    """Column index per field, by header wording. The person confirms it."""
    out: dict[str, int] = {}
    for field in FIELDS:
        for i, h in enumerate(headers):
            if i in out.values():
                continue
            if re.search(_GUESS[field], h or "", re.I):
                out[field] = i
                break
    return out


# ── Staging: the check before anything is written ───────────────────────────


def _price(raw: str) -> Decimal | None:
    s = re.sub(r"[^\d.\-]", "", raw or "")
    try:
        p = Decimal(s)
    except InvalidOperation:
        return None
    return p if p > 0 else None


def stage(db: Session, *, supplier: str, filename: str, headers: list[str], rows: list[list[str]],
          column_map: dict[str, int], user_email: str) -> Any:
    """Check every row against the catalogue and the library; write nothing but
    the staged import itself."""
    from app.models.catalogue import CatalogueImport, CatalogueItem
    from app.models.rate_library import MaterialRate

    missing = [f for f in ("sku", "description", "unit", "price") if f not in column_map]
    if missing:
        raise ValueError(f"match a column for: {', '.join(missing)}")

    def cell(r: list[str], f: str) -> str:
        i = column_map.get(f)
        return (r[i] if i is not None and i < len(r) else "").strip()

    existing = {c.sku: c for c in db.query(CatalogueItem).filter(CatalogueItem.supplier == supplier)}
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for n, r in enumerate(rows, start=1):
        sku, desc, raw_unit = cell(r, "sku"), cell(r, "description"), cell(r, "unit")
        price = _price(cell(r, "price"))
        row: dict[str, Any] = {"line": n, "sku": sku, "description": desc, "raw_unit": raw_unit,
                               "raw_price": float(price) if price is not None else cell(r, "price"),
                               "category": cell(r, "category") or None}
        problem = None
        if not sku:
            problem = "no SKU"
        elif sku in seen:
            problem = f"SKU repeated (line {seen[sku]})"
        elif price is None:
            problem = "no price"
        else:
            unit, cost = unit_and_cost(raw_unit, price)
            if unit is None:
                problem = f"unit not recognised ({raw_unit or 'blank'})"
            else:
                row.update(unit=unit, unit_cost=float(cost))
        seen.setdefault(sku, n)
        if problem:
            row.update(bucket="problem", problem=problem)
        else:
            cur = existing.get(sku)
            if cur is None:
                row["bucket"] = "new"
            elif Decimal(str(row["raw_price"])) == cur.raw_price and raw_unit == cur.raw_unit:
                row["bucket"] = "unchanged"
            else:
                old = float(cur.raw_price)
                row.update(bucket="changed", old_price=old,
                           change_pct=round((row["raw_price"] / old - 1) * 100, 1) if old else None)
                row["big_move"] = row["change_pct"] is not None and abs(row["change_pct"]) > BIG_MOVE_PCT
        out.append(row)

    # Library materials that use one of these SKUs (at this supplier, or with
    # no supplier named) and whose unit is the sheet's unit.
    priced = {r["sku"]: r for r in out if r["bucket"] in ("new", "changed")}
    matches = []
    if priced:
        for m in db.query(MaterialRate).filter(MaterialRate.is_active.is_(True), MaterialRate.sku.in_(list(priced))):
            if m.supplier not in (None, "", supplier):
                continue
            r = priced[m.sku]
            from app.services.library_vocab import UnknownUnit, normalize_unit

            try:
                same_unit = normalize_unit(m.unit) == r["unit"]
            except UnknownUnit:
                same_unit = False
            matches.append({"material_id": str(m.id), "name": m.name, "sku": m.sku, "unit": m.unit,
                            "old_cost": float(m.unit_cost), "new_cost": r["unit_cost"] if same_unit else None,
                            "default": same_unit,
                            "note": None if same_unit else f"sheet prices per {r['unit']}, material is per {m.unit}"})

    counts = {b: sum(1 for r in out if r["bucket"] == b) for b in ("new", "changed", "unchanged", "problem")}
    counts["big_move"] = sum(1 for r in out if r.get("big_move"))
    imp = CatalogueImport(supplier=supplier, filename=filename, status="staged",
                          column_map={k: int(v) for k, v in column_map.items()}, rows=out,
                          summary={"counts": counts, "materials": matches, "headers": headers, "total": len(out)},
                          created_by=user_email)
    # A newer check of the same supplier replaces an older one nobody applied.
    for old in db.query(CatalogueImport).filter(CatalogueImport.supplier == supplier,
                                                CatalogueImport.status == "staged"):
        old.status = "discarded"
    db.add(imp)
    db.flush()
    return imp


# ── Apply and undo ───────────────────────────────────────────────────────────

_ITEM_FIELDS = ("description", "supplier_group", "supplier_category", "raw_unit", "raw_price", "unit",
                "unit_cost", "trade_key", "as_of_date", "source_ref")


def _snap(c: Any) -> dict[str, Any]:
    out = {}
    for f in _ITEM_FIELDS:
        v = getattr(c, f)
        out[f] = v.isoformat() if isinstance(v, date) else (str(v) if isinstance(v, Decimal) else v)
    return out


def apply(db: Session, imp: Any, material_ids: list[str], user_email: str) -> dict[str, int]:
    from app.models.catalogue import CatalogueItem
    from app.models.rate_library import MaterialRate

    if imp.status != "staged":
        raise ValueError(f"this import is {imp.status}")
    today = date.today()
    ref = f"{imp.supplier} price sheet {imp.filename or ''} ({today.isoformat()})".strip()
    existing = {c.sku: c for c in db.query(CatalogueItem).filter(CatalogueItem.supplier == imp.supplier)}
    changes: list[dict[str, Any]] = []
    n = {"added": 0, "updated": 0, "confirmed": 0, "materials": 0, "skipped": 0}
    for r in imp.rows:
        if r["bucket"] == "problem":
            n["skipped"] += 1
            continue
        fields = {"description": r["description"], "raw_unit": r["raw_unit"],
                  "raw_price": Decimal(str(r["raw_price"])), "unit": r["unit"],
                  "unit_cost": Decimal(str(r["unit_cost"])), "as_of_date": today, "source_ref": ref}
        cur = existing.get(r["sku"])
        if cur is None:
            tk = trade_for(None, r.get("category"))
            c = CatalogueItem(supplier=imp.supplier, sku=r["sku"], supplier_category=r.get("category"),
                              trade_key=tk, **fields)
            db.add(c)
            db.flush()
            changes.append({"target": "catalogue", "id": str(c.id), "action": "added"})
            n["added"] += 1
        else:
            changes.append({"target": "catalogue", "id": str(cur.id), "action": "updated", "before": _snap(cur)})
            for k, v in fields.items():
                setattr(cur, k, v)
            n["updated" if r["bucket"] == "changed" else "confirmed"] += 1

    wanted = set(material_ids or [])
    for m in (imp.summary or {}).get("materials", []):
        if m["material_id"] not in wanted or m.get("new_cost") is None:
            continue
        old = db.get(MaterialRate, m["material_id"])
        if old is None or not old.is_active:
            continue
        new = MaterialRate(name=old.name, sku=old.sku, trade=old.trade, trade_key=old.trade_key,
                           category=old.category, unit=old.unit, unit_cost=Decimal(str(m["new_cost"])),
                           measure_unit=old.measure_unit, coverage=old.coverage, waste_pct=old.waste_pct,
                           source="supplier", supplier=imp.supplier, project_type=old.project_type,
                           as_of_date=today, source_ref=ref,
                           payload={**(old.payload or {}), "import_id": str(imp.id)}, created_by=user_email)
        db.add(new)
        db.flush()
        old.is_active, old.superseded_by = False, new.id
        changes.append({"target": "material", "id": str(new.id), "previous": str(old.id), "action": "updated"})
        n["materials"] += 1

    imp.changes = changes
    imp.status = "applied"
    imp.applied_by, imp.applied_at = user_email, datetime.now(timezone.utc)
    imp.summary = {**(imp.summary or {}), "applied": n}
    return n


class UndoRefused(ValueError):
    pass


def undo(db: Session, imp: Any, user_email: str) -> dict[str, int]:
    """Put back everything the import changed. Only the latest applied import
    can be undone: an earlier one's "before" values may have been overwritten."""
    from app.models.catalogue import CatalogueImport, CatalogueItem
    from app.models.rate_library import MaterialRate

    if imp.status != "applied":
        raise UndoRefused(f"this import is {imp.status}")
    later = (db.query(CatalogueImport)
             .filter(CatalogueImport.status == "applied", CatalogueImport.applied_at > imp.applied_at)
             .order_by(CatalogueImport.applied_at.desc()).first())
    if later is not None:
        raise UndoRefused(f"undo the later import first ({later.supplier}, {later.filename or later.id})")
    n = {"removed": 0, "restored": 0, "materials": 0}
    for ch in reversed(imp.changes or []):
        if ch["target"] == "catalogue":
            c = db.get(CatalogueItem, ch["id"])
            if c is None:
                continue
            if ch["action"] == "added":
                db.delete(c)
                n["removed"] += 1
            else:
                for k, v in ch["before"].items():
                    if k == "as_of_date" and v:
                        v = date.fromisoformat(v)
                    elif k in ("raw_price", "unit_cost") and v is not None:
                        v = Decimal(v)
                    setattr(c, k, v)
                n["restored"] += 1
        else:
            new, prev = db.get(MaterialRate, ch["id"]), db.get(MaterialRate, ch["previous"])
            if new is not None:
                new.is_active = False
            if prev is not None:
                prev.is_active, prev.superseded_by = True, None
            n["materials"] += 1
    imp.status = "undone"
    imp.undone_by, imp.undone_at = user_email, datetime.now(timezone.utc)
    return n
