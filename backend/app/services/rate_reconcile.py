"""Deterministic rate-library reconciliation — Track 2 Phase 6 (last step).

Runs AFTER the LLM has priced every line item and BEFORE the estimate is
persisted. It does NOT touch the scope or estimate prompts — it only reconciles
priced categories against BRIO's own installed rates, using the CAD takeoff for
the quantity (which carries a unit) rather than the LLM's unit-less item `qty`.

Each binding maps one rate-library assembly to one estimate category + the
takeoff `quantity_type` it consumes, and declares a mode:

  - ``override``  — the assembly's scope EQUALS the category's scope, so the
    authoritative cost (rate × takeoff qty) replaces the category. Use only when
    that equality genuinely holds.
  - ``advisory``  — the assembly is a SUBSET of the category (e.g. framing ⊂
    Structural, which also holds foundation/beams/seismic). We attach the
    rate-derived figure as a non-destructive benchmark on the category and in
    the report, but do NOT change the price. Prevents dropping the rest of the
    category's scope.

Coverage is intentionally small today (one advisory binding). It grows as
assembly/category/takeoff units line up or line-item bindings are added.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.models.quantity_override import QuantityOverride
from app.services import rate_pricing


# assembly_code · takeoff quantity_type · target estimate category · mode · why
RATE_BINDINGS: list[dict[str, str]] = [
    {
        "assembly_code": "FRAME-WOOD-PKG-RES",
        "quantity_type": "floor_area_sf",
        "category": "Structural",
        "mode": "advisory",
        "note": (
            "Framing package is a subset of the Structural category (which also "
            "includes foundation, engineered beams, seismic). Benchmark only — "
            "flip to override only where Structural == framing (e.g. simple wood "
            "structures with the slab/footings priced in a separate category)."
        ),
    },
]


#: How a quantity combines across the sheets of one set.
#:
#: `sum` is the default and is right for anything that accumulates: a basement's
#: floor area plus a level one's IS the building's floor area, and the same goes
#: for counts and wall runs.
#:
#: `envelope` is for quantities a BUILDING has exactly one of however many
#: storeys are drawn — its roof, its outline. These take the largest sheet
#: WITHIN each building and then sum ACROSS buildings, because three duplexes on
#: one site have three roofs and three foundation perimeters, but a basement
#: sheet and a level-1 sheet of the same duplex describe one of each.
#:
#: Getting this wrong is expensive in both directions and silent in both. On the
#: Maple set, summing every sheet gave 4,990 sf of roof over a largest
#: storey of 1,415; taking one global maximum gave 1,415 sf of roof over three
#: buildings. Neither reads as an error.
_AGGREGATION = {"roof_area_sf": "envelope", "perimeter_lf": "envelope"}


def _takeoff_quantities(db: Session, project_id: UUID | str) -> dict[str, dict[str, Any]]:
    """The reviewed takeoff quantity per quantity_type for the project.

    Two rules that are easy to get wrong and expensive when wrong:

    * A drawing set is many sheets. Each sheet carries its OWN row for a type —
      that is what the (document, candidate_key) unique index models. Floor area
      on the basement sheet and floor area on the level-1 sheet are two real,
      additive numbers, so sheets SUM. Last-write-wins silently priced a
      two-storey house off whichever sheet happened to be read last.
    * An EXCLUDED row is a reviewer saying "do not use this number". It counts
      as resolved for the gate, so without this filter an estimate could sail
      through the gate and then price the very quantity a person struck out.

    A row with no source document is a person stating the project total by hand;
    it REPLACES the per-sheet sum rather than adding to it."""
    rows = (
        db.query(QuantityOverride)
        .filter(QuantityOverride.project_id == project_id)
        .order_by(QuantityOverride.created_at)
        .all()
    )
    # Which building each drawing belongs to, so an envelope quantity can be
    # resolved per building rather than per sheet or per project. Derived from
    # the filename at read time rather than stored, so it always reflects the
    # current parser instead of whatever it said when the row was written.
    from app.services.drawing.project_takeoff import building_key

    buildings = {
        r["id"]: building_key(r["filename"] or "")
        for r in db.execute(
            _T("SELECT id, filename FROM documents WHERE project_id = :p"),
            {"p": str(project_id)}).mappings().all()
    }

    per_sheet: dict[str, dict[Any, tuple[float, str, str]]] = {}
    manual: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.value is None or r.status == "excluded":
            continue
        if r.source_document_id is None:
            manual[r.quantity_type] = {
                "value": float(r.value), "unit": r.unit, "source": r.source}
        else:
            # Later row for the same sheet supersedes the earlier one.
            per_sheet.setdefault(r.quantity_type, {})[r.source_document_id] = (
                float(r.value), r.unit, r.source)

    out: dict[str, dict[str, Any]] = {}
    for qtype, sheets in per_sheet.items():
        vals = list(sheets.values())
        how = _AGGREGATION.get(qtype, "sum")
        if how == "envelope":
            by_building: dict[str, float] = {}
            for doc_id, (v, _u, _s) in sheets.items():
                b = buildings.get(doc_id, "default")
                by_building[b] = max(by_building.get(b, 0.0), v)
            value = sum(by_building.values())
            extra = {"buildings": len(by_building)}
        else:
            value = sum(v[0] for v in vals)
            extra = {}
        out[qtype] = {"value": value, "unit": vals[0][1], "source": vals[0][2],
                      "sheets": len(vals), "aggregation": how, **extra}
    out.update(manual)
    return out


def _category_raw(cat: dict[str, Any]) -> float:
    """Sum of a category's non-contingency item costs (labor + materials)."""
    return sum(
        float(it.get("labor") or 0) + float(it.get("materials") or 0)
        for it in (cat.get("items") or [])
        if isinstance(it, dict) and not it.get("contingency")
    )


def apply_rate_library(
    db: Session,
    categories: list[dict[str, Any]],
    project_id: UUID | str,
    *,
    project_type: str = "any",
    rollup: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile priced categories against the rate library. Mutates and returns
    `categories`, plus a per-binding report. Only `override` bindings change a
    price; `advisory` bindings attach a `rate_benchmark` and never alter totals.

    `rollup` (pass `_rollup_category`) recomputes subtotalRaw/contingency/total
    after an override so the category invariant holds; only needed for override.
    """
    qtys = _takeoff_quantities(db, project_id)
    by_name: dict[str, dict[str, Any]] = {}
    for c in categories:
        by_name.setdefault((c.get("name") or "").strip().lower(), c)

    report: list[dict[str, Any]] = []
    for b in RATE_BINDINGS:
        row: dict[str, Any] = {
            "assembly": b["assembly_code"],
            "quantity_type": b["quantity_type"],
            "category": b["category"],
            "mode": b["mode"],
            "applied": False,
        }
        q = qtys.get(b["quantity_type"])
        entry = rate_pricing.resolve_best_entry(
            db, assembly_code=b["assembly_code"], project_type=project_type
        )
        cat = by_name.get(b["category"].strip().lower())
        if q is None:
            row["reason"] = "no takeoff quantity"
            report.append(row)
            continue
        if entry is None:
            row["reason"] = "no rate for assembly / project type"
            report.append(row)
            continue
        if cat is None:
            row["reason"] = f"category '{b['category']}' not in estimate"
            report.append(row)
            continue

        # Units must agree before a rate multiplies a quantity. Until 2026-10-15
        # nothing checked: a rate per sheet times a quantity in sf would have
        # been priced without complaint. Exact conversions (sy→sf) are applied;
        # anything else is refused and reported.
        from app.services.library_vocab import UnknownUnit, factor

        try:
            f = factor(q["unit"], entry.unit)
        except UnknownUnit:
            f = None
        if f is None:
            row["reason"] = f"unit mismatch: rate is per {entry.unit}, takeoff is in {q['unit']}"
            report.append(row)
            continue
        q = {**q, "value": float(q["value"]) * float(f), "unit": entry.unit}

        rate = float(entry.unit_rate)
        authoritative = round(rate * q["value"], 2)
        prov = {
            "assembly": b["assembly_code"],
            "rate": rate,
            "rate_unit": entry.unit,
            "qty": q["value"],
            "qty_unit": q["unit"],
            "quantity_type": b["quantity_type"],
            "authoritative": authoritative,
            "source_type": entry.source_type,
            "confidence": entry.confidence,
            "source_ref": entry.source_ref,
        }
        row.update(
            qty=q["value"], unit=q["unit"], rate=rate, authoritative=authoritative,
            source_type=entry.source_type, confidence=entry.confidence,
            category_total=cat.get("total"),
        )

        if b["mode"] == "override":
            original = float(cat.get("total") or 0)
            # Replace the category with a single authoritative BRIO-rate line so
            # sum(items) == total, then re-roll to restore contingency/total.
            cat["items"] = [
                {
                    "name": (
                        f"{b['assembly_code']} — {q['value']} {q['unit']} @ "
                        f"${rate}/{entry.unit} (BRIO {entry.source_type})"
                    ),
                    "qty": q["value"],
                    "unitCost": rate,
                    "labor": 0,
                    "materials": authoritative,
                    "laborHours": 0,
                }
            ]
            cat["rate_provenance"] = prov
            if rollup is not None:
                rollup(cat)
            else:
                cat["total"] = authoritative
            row.update(applied=True, action="override", original_total=original)
        else:  # advisory — non-destructive benchmark
            cat["rate_benchmark"] = {
                **prov,
                "llm_total": cat.get("total"),
                "note": b.get("note"),
            }
            row.update(applied=True, action="advisory", llm_total=cat.get("total"))
        report.append(row)

    return categories, report
