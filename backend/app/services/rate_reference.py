"""Build the estimate prompt's rate baseline from the rate library.

The baseline used to be a hardcoded string condensed from a retired reference
file, which meant editing a rate in the admin UI changed nothing about what the
AI priced against. Two sources of truth, free to drift, and the one that drifted
was the one most estimates actually go through.

⚠️ THE IMPORTANT SEMANTIC CHANGE. The hardcoded block gave the model BILLABLE
labour rates ("$/hr, billable incl. overhead": framer 55/70/90) while the same
prompt told it to emit BASE costs with no overhead, and ``estimate_pdf`` then
multiplied every category by (1+overhead)(1+profit). Overhead was therefore
counted twice on the labour component. ``labour_rates`` holds a cost basis
(as-posted Job Bank wage), so generating from it removes the double count.

Consequence worth knowing: a framer goes to the model at 43.71/hr instead of
70/hr. With margin applied downstream the billable figure lands near 58-59/hr at
OH 20 / profit 12 — inside the documented 55-90 band, where 70 × 1.344 = 94 was
not.

Labour is given as a SINGLE cost figure with no band, deliberately: the
low/mid/high band on a labour row is *billable*, and handing it to the model is
exactly how overhead gets baked in again. Material bands ARE supply-only cost,
so those are passed through and the choose-a-band guidance applies to them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.rate_pricing import best_labour_rate, best_material_rate
from app.models.rate_library import LabourRate, MaterialRate
from sqlalchemy import select


def rate_library_context(db: Session, project_type: str = "any") -> dict[str, Any] | None:
    """The rate block attached to an estimate payload, or None if the library is
    too thin to price from (caller then falls back to the static baseline)."""
    # One row per trade — whichever the pricing resolver would actually pick, so
    # the prompt and the composed-assembly path cannot disagree about a rate.
    trades = [
        t for (t,) in db.execute(
            select(LabourRate.trade).where(LabourRate.is_active.is_(True)).distinct()
        ).all()
    ]
    # Loaded cost: the posted wage plus BRIO's labour burden (14%, Amir
    # 2026-10-15). Until phase 3 the prompt carried the bare wage while the
    # library's assemblies carried wage x 1.14, so the two priced labour
    # differently (Amir approved the switch, 2026-10-16).
    from app.services.rate_links import burden_pct, catalog, labour_code, material_code

    load = 1 + burden_pct(db) / 100
    labour = []
    for t in sorted(trades):
        r = best_labour_rate(db, trade=t, project_type=project_type)
        if r is not None:
            labour.append({"trade": t, "code": labour_code(t),
                           "rate": round(float(r.effective_rate) * load, 2),
                           "wage": round(float(r.effective_rate), 2)})

    names = [
        n for (n,) in db.execute(
            select(MaterialRate.name).where(MaterialRate.is_active.is_(True)).distinct()
        ).all()
    ]
    materials = []
    for n in sorted(names):
        m = best_material_rate(db, name=n, project_type=project_type)
        if m is None:
            continue
        band = (m.payload or {}).get("band") or {}
        materials.append({
            "code": material_code(m.id),
            "name": m.name,
            "trade": m.trade,
            "unit": m.unit,
            "cost": round(float(m.unit_cost), 2),
            "low": band.get("low"),
            "high": band.get("high"),
            "waste_pct": float(m.waste_pct) if m.waste_pct is not None else 0.0,
        })

    # Too thin to be a baseline — better the curated static block than a prompt
    # that names three materials and implies they are the whole catalogue.
    if len(labour) < 5 or len(materials) < 10:
        return None
    cat_ = catalog(db, project_type)
    assemblies = [
        {k: v for k, v in r.items() if k in ("code", "name", "unit", "rate", "trade_key")}
        for r in cat_.values() if r["kind"] == "assembly"
    ]
    measure = {r["code"]: (r["rate"], r["unit"]) for r in cat_.values() if r["kind"] == "material"}
    for m in materials:
        m["per_measure"] = measure.get(m["code"])
    return {"labour": labour, "materials": materials, "assemblies": assemblies,
            "burden_pct": round((load - 1) * 100, 2), "project_type": project_type}


def render_rate_reference(ctx: dict[str, Any] | None, *, static_tail: str) -> str | None:
    """Format the library into prompt text. None when there is nothing to render."""
    if not ctx or not ctx.get("labour") or not ctx.get("materials"):
        return None

    lab = " · ".join(f"{r['trade']} [{r['code']}] {r['rate']:.2f}" if r.get("code")
                     else f"{r['trade']} {r['rate']:.2f}" for r in ctx["labour"])

    by_trade: dict[str, list[dict[str, Any]]] = {}
    for m in ctx["materials"]:
        by_trade.setdefault(m["trade"] or "other", []).append(m)

    mat_lines = []
    for trade in sorted(by_trade):
        parts = []
        for m in by_trade[trade]:
            # Show the band only where one exists — the yard-survey rows carry a
            # single surveyed price, and inventing a range around it would imply
            # a precision we do not have.
            if m["low"] is not None and m["high"] is not None:
                price = f"${m['cost']:g}/{m['unit']} (range {m['low']:g}-{m['high']:g})"
            else:
                price = f"${m['cost']:g}/{m['unit']}"
            waste = f" +{m['waste_pct']:g}% waste" if m["waste_pct"] else ""
            code = f" [{m['code']}]" if m.get("code") else ""
            pm = m.get("per_measure")
            per = (f" = ${pm[0]:.2f}/{pm[1]} installed-quantity basis"
                   if pm and pm[1] != m["unit"] else "")
            parts.append(f"{m['name']}{code} {price}{waste}{per}")
        mat_lines.append(f"  [{trade}] " + " · ".join(parts))
    mats = "\n".join(mat_lines)

    asm_lines = []
    by_t: dict[str, list[dict[str, Any]]] = {}
    for a in ctx.get("assemblies") or []:
        by_t.setdefault(a.get("trade_key") or "other", []).append(a)
    for t in sorted(by_t):
        asm_lines.append(f"  [{t}] " + " · ".join(
            f"{a['name']} [{a['code']}] ${a['rate']:.2f}/{a['unit']}" for a in by_t[t]))
    assemblies = ""
    if asm_lines:
        assemblies = (
            "\nInstalled assemblies (material + loaded labour per unit, BASE cost before margin):\n"
            + "\n".join(asm_lines) + "\n"
        )
    burden = ctx.get("burden_pct")
    labour_basis = (f"loaded cost: the posted wage plus BRIO's {burden:g}% labour burden"
                    if burden else "a wage basis")

    return f"""\
Greater Vancouver / North Shore pricing baseline, read live from BRIO's rate
library. These are the authoritative numbers — prefer them over your own priors.

Labour ($/hr, BASE COST — {labour_basis}, EXCLUDING overhead and profit. The
system applies margin to every category afterwards, so do NOT add any yourself,
and do NOT substitute a higher "billable" rate):
  {lab}
An "-apprentice" rate covers supervised support hours working alongside a
journeyman. Never price a journeyman scope at an apprentice rate; if unsure which
applies, use the journeyman.

Materials (supply-only unit cost, before markup). Where a range is given, the
quoted figure is the mid: use the low end for simple repetitive work with easy
access, the high end for premium finishes, complex geometry or difficult access.
Apply the stated waste factor to quantities:
{mats}

Trim/moulding waste: 10% (no catalogue row yet).
{assemblies}
LINKING EVERY PRICE TO THE LIBRARY (fully_itemized lines). Each code in [brackets]
above is a library rate. On every line you price from one, set:
  - "rate_ref": the assembly code (preferred when one matches the work) or the
    material code [M:...]; "qty" and "unit" MUST be in that rate's unit
    (an assembly per lf takes lf, a door assembly takes ea);
  - "labour_ref": the labour code [L:...] when the line's labour is priced from
    that trade's hourly rate (with "laborHours");
  - "qty_source": "takeoff:<quantity_type>" when the quantity is one of the
    USER-VERIFIED QUANTITIES, else omit it.
The system re-prices linked lines from the library, so cite only a code whose
description truly matches the line. A line with no matching rate gets
"rate_ref": null and "basis": "quote" | "allowance" | "no_rate" — never invent a
code.

{static_tail}"""
