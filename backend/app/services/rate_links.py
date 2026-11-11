"""Link every estimate line to the library rate that priced it (plan phase 3).

Amir, 2026-10-16: "when estimates are prepared and the pricing is provided, we
take it as clearly linked to what rate in the library they're being connected
to." Approved in "Phase 3: Linked Pricing": the library price wins on a linked
line, and a gap over 10% from the AI's own figure is flagged.

How it works, and why it isn't the model's word alone:

1. The prompt lists the library with a code per rate (`catalog`): assemblies by
   their own code (FL-LVP), materials as M:<id8>, labour as L:<trade>.
2. The model puts `rate_ref` (assembly or material) and/or `labour_ref` on a
   line, with the line's `unit`, or a `basis` (quote / allowance / no_rate).
3. `link_lines` checks each cited code exists, checks the units convert, and
   then PRICES the line from the library: quantity × rate. The model's figures
   are kept beside it (`rate_link.ai`) so the gap is visible.

A code that doesn't exist or a unit that doesn't match is refused: the line
keeps the model's price and is labelled an allowance. A wrong link is worse
than none, because review fixes write back to whatever rate a line names.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: A linked line whose AI figure differs from the library by more than this is
#: flagged for review (Amir approved 10%, 2026-10-16).
GAP_FLAG_PCT = 10.0


def burden_pct(db: Session) -> float:
    """Labour burden on posted wages (Amir: 14%, 2026-10-15); 0 if unset."""
    from app.models.prep import AppSetting

    s = db.get(AppSetting, "labour_burden_pct")
    return float((s.value or {}).get("pct", 0)) if s is not None else 0.0


def material_code(material_id: Any) -> str:
    return f"M:{str(material_id)[:8]}"


def labour_code(trade: str) -> str:
    return f"L:{trade}"


def catalog(db: Session, project_type: str = "any") -> dict[str, dict[str, Any]]:
    """Every rate a line can cite, by code, priced the way the line will be."""
    from app.models.rate_library import LabourRate, MaterialRate, RateAssembly
    from app.services.library_vocab import UnknownUnit, cost_per_measure, normalize_unit
    from app.services.rate_pricing import best_labour_rate, best_material_rate, resolve_best_entry

    out: dict[str, dict[str, Any]] = {}
    load = 1 + burden_pct(db) / 100

    for a in db.scalars(select(RateAssembly).where(RateAssembly.status == "active")):
        e = resolve_best_entry(db, assembly_code=a.code, project_type=project_type)
        if e is None:
            continue
        rate = float(e.unit_rate)
        p = e.payload or {}
        mat, lab = p.get("material"), p.get("labour")
        if mat is None or lab is None:
            # No split recorded (the two hand-entered originals): the whole
            # installed rate is carried as one figure, and said so.
            mat, lab, split = rate, 0.0, False
        else:
            # Each half was rounded to cents when composed, so they can miss the
            # installed rate by a cent (5.38 + 1.12 = 6.50 against 6.49). The
            # rate is what was approved: the split is scaled to it.
            split = True
            mat, lab = float(mat), float(lab)
            if mat + lab > 0:
                mat, lab = rate * mat / (mat + lab), rate * lab / (mat + lab)
        out[a.code] = {
            "kind": "assembly", "code": a.code, "name": a.name, "unit": normalize_unit(e.unit),
            "rate": rate, "material_per_unit": float(mat), "labour_per_unit": float(lab),
            "hours_per_unit": p.get("hours_per_unit"), "split_known": split,
            "trade_key": a.trade_key, "source": e.source_type, "confidence": e.confidence,
            "source_ref": e.source_ref,
            "as_of": e.as_of_date.isoformat() if e.as_of_date else None, "entry_id": str(e.id),
        }

    names = {n for (n,) in db.execute(select(MaterialRate.name).where(MaterialRate.is_active.is_(True)))}
    for n in sorted(names):
        m = best_material_rate(db, name=n, project_type=project_type)
        if m is None:
            continue
        try:
            per = cost_per_measure(m.unit_cost, m.unit, measure_unit=m.measure_unit,
                                   coverage=m.coverage, waste_pct=m.waste_pct)
            unit = normalize_unit(m.unit)
        except UnknownUnit:
            continue
        if per is not None:
            rate, unit = float(per[0]), per[1]
        else:
            rate = float(m.unit_cost) * (1 + float(m.waste_pct or 0) / 100)
        code = material_code(m.id)
        out[code] = {
            "kind": "material", "code": code, "name": m.name, "unit": unit, "rate": round(rate, 4),
            "trade_key": m.trade_key, "source": m.source, "confidence": None,
            "as_of": m.as_of_date.isoformat() if m.as_of_date else None, "material_id": str(m.id),
        }

    trades = {t for (t,) in db.execute(select(LabourRate.trade).where(LabourRate.is_active.is_(True)))}
    for t in sorted(trades):
        r = best_labour_rate(db, trade=t, project_type=project_type)
        if r is None:
            continue
        code = labour_code(t)
        out[code] = {
            "kind": "labour", "code": code, "name": t, "unit": "hr",
            "rate": round(float(r.effective_rate) * load, 2), "wage": float(r.effective_rate),
            "burden_pct": burden_pct(db), "trade_key": r.trade_key, "source": r.source,
            "confidence": None, "as_of": r.as_of_date.isoformat() if r.as_of_date else None,
            "labour_id": str(r.id),
        }
    return out


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def _ref_info(ref: dict[str, Any]) -> dict[str, Any]:
    keep = ("code", "kind", "name", "unit", "rate", "source", "confidence", "source_ref", "as_of",
            "entry_id", "material_id", "labour_id")
    return {k: ref[k] for k in keep if k in ref}


def link_item(item: dict[str, Any], cat_: dict[str, dict[str, Any]],
              takeoff: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Link and price one line in place. Returns its `rate_link`."""
    from app.services.library_vocab import UnknownUnit, factor

    ai = {"labor": _num(item.get("labor")), "materials": _num(item.get("materials")),
          "unitCost": _num(item.get("unitCost")), "qty": _num(item.get("qty")),
          "laborHours": _num(item.get("laborHours"))}
    rate_ref, labour_ref = item.get("rate_ref"), item.get("labour_ref")
    link: dict[str, Any] = {"status": "unlinked", "ai": ai}

    if not rate_ref and not labour_ref:
        item["basis"] = item.get("basis") or "allowance"
        link["basis"] = item["basis"]
        item["rate_link"] = link
        return link

    problems: list[str] = []
    ref = cat_.get(rate_ref) if rate_ref else None
    lref = cat_.get(labour_ref) if labour_ref else None
    if rate_ref and (ref is None or ref["kind"] == "labour"):
        problems.append(f"no assembly or material with code {rate_ref}")
    if labour_ref and (lref is None or lref["kind"] != "labour"):
        problems.append(f"no labour rate with code {labour_ref}")

    qty = ai["qty"]
    qty_source = "line"
    unit_assumed = False
    if ref is not None and not problems:
        src = str(item.get("qty_source") or "")
        if src.startswith("takeoff:") and takeoff and src[8:] in takeoff:
            t = takeoff[src[8:]]
            try:
                f = factor(t["unit"], ref["unit"])
            except UnknownUnit:
                f = None
            if f is None:
                problems.append(f"takeoff {src[8:]} is in {t['unit']}, the rate is per {ref['unit']}")
            else:
                qty, qty_source = float(t["value"]) * float(f), src
        else:
            unit = item.get("unit")
            if unit:
                try:
                    f = factor(unit, ref["unit"])
                except UnknownUnit:
                    f = None
                if f is None:
                    problems.append(f"line is in {unit}, {ref['code']} is per {ref['unit']}")
                elif qty is not None:
                    qty = qty * float(f)
            else:
                unit_assumed = True
        if qty is None or qty <= 0:
            problems.append("no quantity to price the rate against")

    if problems:
        item["basis"] = "allowance"
        link.update(status="refused", reason="; ".join(problems), basis="allowance")
        item["rate_link"] = link
        return link

    labor = ai["labor"] or 0.0
    materials = ai["materials"] or 0.0
    hours = ai["laborHours"]
    if ref is not None and ref["kind"] == "assembly":
        materials = qty * ref["material_per_unit"]
        labor = qty * ref["labour_per_unit"]
        if ref.get("hours_per_unit") is not None:
            hours = qty * float(ref["hours_per_unit"])
        item["unitCost"] = ref["rate"]
    elif ref is not None and ref["kind"] == "material":
        materials = qty * ref["rate"]
    if lref is not None and ref is not None and ref["kind"] == "assembly":
        # An assembly's rate already includes its labour. A labour code cited
        # beside it would price the same hours twice over (seen live on ZZ v3,
        # 2026-10-16: baseboard install at $120 instead of the assembly's $105).
        lref = None
    if lref is not None:
        if hours is None or hours <= 0:
            item["rate_link"] = {**link, "status": "refused", "basis": "allowance",
                                 "reason": f"{labour_ref} cited with no labour hours"}
            item["basis"] = "allowance"
            return item["rate_link"]
        labor = hours * lref["rate"]

    if qty is not None and ref is not None:
        item["qty"] = round(qty, 4)
        item["unit"] = ref["unit"]
    if hours is not None:
        item["laborHours"] = round(hours, 2)
    item["labor"], item["materials"] = _r(labor), _r(materials)
    if ref is not None and ref["kind"] == "material" and qty:
        item["unitCost"] = round((item["labor"] + item["materials"]) / qty, 4)

    lib_total = item["labor"] + item["materials"]
    ai_total = (ai["labor"] or 0.0) + (ai["materials"] or 0.0)
    gap = None if lib_total <= 0 or ai_total <= 0 else round((ai_total / lib_total - 1) * 100, 1)
    item["basis"] = "library"
    link.update(
        status="linked", basis="library",
        rate=_ref_info(ref) if ref else None, labour=_ref_info(lref) if lref else None,
        qty=item.get("qty"), qty_source=qty_source, unit_assumed=unit_assumed,
        library_total=_r(lib_total), ai_total=_r(ai_total), gap_pct=gap,
        flagged=gap is not None and abs(gap) > GAP_FLAG_PCT,
    )
    item["rate_link"] = link
    return link


def link_categories(categories: list[dict[str, Any]], cat_: dict[str, dict[str, Any]],
                    takeoff: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Link every line of every category, in place. Category totals are the
    caller's to re-roll (the generation paths already roll up after this)."""
    n = {"linked": 0, "refused": 0, "unlinked": 0, "flagged": 0, "library_priced_usd": 0.0}
    for c in categories:
        if not isinstance(c, dict):
            continue
        for it in c.get("items") or []:
            if not isinstance(it, dict) or it.get("contingency"):
                continue
            link = link_item(it, cat_, takeoff)
            n[link["status"]] += 1
            if link.get("flagged"):
                n["flagged"] += 1
            if link["status"] == "linked":
                n["library_priced_usd"] += link["library_total"]
        if c.get("items"):
            c["total"] = round(sum(float(i.get("labor") or 0) + float(i.get("materials") or 0)
                                   for i in c["items"] if isinstance(i, dict) and not i.get("contingency")))
    n["library_priced_usd"] = _r(n["library_priced_usd"])
    return n
