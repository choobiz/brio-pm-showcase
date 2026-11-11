"""Review fixes to an estimate update the rate library (plan phase 3).

Amir, 2026-10-16: "When we are doing the reviews on whether an estimate is too
high or too low, while we are fixing the estimate, we're always also going to
update the rate libraries for material and rate as well." Approved design
("Phase 3: Linked Pricing"): the save step lists each rate a fix implies,
ticked by default; a rate can be kept to "this job only" with a reason.

What counts as a fix: a linked line (rate_link.status == "linked") whose
labour + materials no longer equal what the library priced it at. The
comparison is against the line's own `rate_link`, so a line nobody touched
never proposes anything, even if the library has moved since.

What is written, and never overwritten: a new entry with source "own" (the
most trusted), naming the estimate, line, person and the value it replaced.
The previous "own" entry is superseded, not deleted. Material and labour
rates get a new "own" row the same way.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

#: Below this relative change a line is treated as unchanged (rounding).
_TOLERANCE = 0.005


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _lines(doc: dict[str, Any]):
    for ci, c in enumerate(doc.get("categories") or []):
        if not isinstance(c, dict):
            continue
        for ii, it in enumerate(c.get("items") or []):
            if isinstance(it, dict) and not it.get("contingency"):
                yield f"categories[{ci}].items[{ii}]", c, it


def _settled_total(link: dict[str, Any]) -> float | None:
    wb = link.get("writeback") or {}
    return wb.get("total") if wb.get("status") in ("written", "job_only") else None


def proposals(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every library change the document's fixes imply, for the save step."""
    out: list[dict[str, Any]] = []
    for path, cat, it in _lines(doc):
        link = it.get("rate_link") or {}
        total = round(_num(it.get("labor")) + _num(it.get("materials")), 2)
        qty = _num(it.get("qty"))
        if link.get("status") == "linked":
            base = link.get("library_total") or 0
            if not base or abs(total - base) / base <= _TOLERANCE:
                continue
            settled = _settled_total(link)
            if settled is not None and abs(total - settled) <= 0.01:
                continue  # already written or kept to this job at this value
            r, lab = link.get("rate") or {}, link.get("labour") or {}
            p: dict[str, Any] = {"key": path, "line": it.get("name"), "category": cat.get("name"),
                                 "default": True, "old_total": base, "new_total": total}
            if r.get("kind") == "assembly" and qty > 0:
                p.update(kind="assembly", code=r["code"], unit=r["unit"], old_rate=r["rate"],
                         new_rate=round(total / qty, 4),
                         material_per_unit=round(_num(it.get("materials")) / qty, 4),
                         labour_per_unit=round(_num(it.get("labor")) / qty, 4),
                         hours_per_unit=(round(_num(it.get("laborHours")) / qty, 4)
                                         if it.get("laborHours") else None))
            elif r.get("kind") == "material" and qty > 0:
                p.update(kind="material", code=r["code"], unit=r["unit"], old_rate=r["rate"],
                         new_rate=round(_num(it.get("materials")) / qty, 4), material_id=r.get("material_id"))
                if abs(p["new_rate"] - r["rate"]) / max(r["rate"], 1e-9) <= _TOLERANCE:
                    p = {}  # only the labour moved on a material line
            else:
                p = {}
            hours = _num(it.get("laborHours"))
            if lab and hours > 0:
                new_hr = round(_num(it.get("labor")) / hours, 2)
                if abs(new_hr - lab["rate"]) / lab["rate"] > _TOLERANCE:
                    out.append({"key": path + "#labour", "line": it.get("name"), "category": cat.get("name"),
                                "default": True, "kind": "labour", "code": lab["code"], "unit": "hr",
                                "old_rate": lab["rate"], "new_rate": new_hr, "labour_id": lab.get("labour_id"),
                                "old_total": base, "new_total": total})
            if p:
                out.append(p)
        elif link.get("status") in ("unlinked", "refused") and qty > 0 and total > 0 and it.get("unit"):
            if (link.get("writeback") or {}).get("status") == "written":
                continue
            out.append({"key": path, "line": it.get("name"), "category": cat.get("name"), "default": False,
                        "kind": "new", "unit": it.get("unit"), "new_rate": round(total / qty, 4),
                        "material_per_unit": round(_num(it.get("materials")) / qty, 4),
                        "labour_per_unit": round(_num(it.get("labor")) / qty, 4), "new_total": total})
    return out


class DecisionError(ValueError):
    pass


def _trade_for_category(name: str | None) -> str:
    from app.services.library_vocab import TRADES

    m = re.match(r"\s*(\d{2})\b", name or "")
    if m:
        for t in TRADES:
            if t.residential == m.group(1):
                return t.key
    return "other"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", (s or "").upper()).strip("-")[:32] or "LINE"


def settle(db: Session, doc: dict[str, Any], decisions: list[dict[str, Any]] | None, *,
           source: str, user_email: str) -> dict[str, Any]:
    """Apply the save step's decisions, in the caller's transaction.

    `decisions`: [{key, write: bool, reason?}]. A proposal with no decision is
    recorded on its line as pending, never dropped. Keeping a linked rate to
    this job needs a reason (DecisionError otherwise, before anything is written).
    """
    from app.models.rate_library import LabourRate, MaterialRate, RateAssembly, RateEntry
    from app.services.rate_links import burden_pct

    props = proposals(doc)
    by_key = {d["key"]: d for d in (decisions or []) if isinstance(d, dict) and d.get("key")}
    for p in props:
        d = by_key.get(p["key"])
        if d is not None and not d.get("write") and p["kind"] != "new" and not (d.get("reason") or "").strip():
            raise DecisionError(f"keeping {p.get('code')} to this job needs a reason ({p['line']})")

    items = {path: it for path, _c, it in _lines(doc)}
    now = datetime.now(timezone.utc).isoformat()
    today = date.today()
    summary = {"written": 0, "job_only": 0, "pending": 0, "skipped": 0}
    evidence = {"estimate": source, "by": user_email, "at": now}

    for p in props:
        it = items[p["key"].split("#")[0]]
        link = it.setdefault("rate_link", {})
        d = by_key.get(p["key"])
        if d is None:
            if p["kind"] != "new":
                link["writeback"] = {"status": "pending", "proposal": p, "at": now}
                summary["pending"] += 1
            continue
        if not d.get("write"):
            if p["kind"] == "new":
                summary["skipped"] += 1
                continue
            link["writeback"] = {"status": "job_only", "reason": d["reason"].strip(),
                                 "total": p["new_total"], "by": user_email, "at": now}
            summary["job_only"] += 1
            continue

        ev = {**evidence, "line": p["line"], "old_rate": p.get("old_rate"), "path": p["key"]}
        if p["kind"] in ("assembly", "new"):
            if p["kind"] == "new":
                code = f"OWN-{_slug(p['line'])}"
                a = db.query(RateAssembly).filter(RateAssembly.code == code).one_or_none()
                if a is None:
                    tk = _trade_for_category(p.get("category"))
                    from app.services.library_vocab import normalize_unit

                    a = RateAssembly(code=code, name=p["line"], trade=tk, trade_key=tk,
                                     unit=normalize_unit(p["unit"]), status="active",
                                     created_by=user_email, payload={"set": "estimate-review"})
                    db.add(a)
                    db.flush()
            else:
                a = db.query(RateAssembly).filter(RateAssembly.code == p["code"]).one()
            e = RateEntry(assembly_id=a.id, unit_rate=Decimal(str(p["new_rate"])), unit=a.unit,
                          source_type="own", confidence="derived", project_type="any", as_of_date=today,
                          source_ref=f"Estimate review: {source}",
                          payload={"material": p["material_per_unit"], "labour": p["labour_per_unit"],
                                   "hours_per_unit": p.get("hours_per_unit"), "evidence": ev},
                          created_by=user_email)
            db.add(e)
            db.flush()
            for old in db.query(RateEntry).filter(RateEntry.assembly_id == a.id, RateEntry.source_type == "own",
                                                  RateEntry.is_active.is_(True), RateEntry.id != e.id):
                old.is_active, old.superseded_by = False, e.id
            ref = {"code": a.code, "kind": "assembly", "unit": a.unit, "rate": p["new_rate"],
                   "source": "own", "entry_id": str(e.id), "as_of": today.isoformat()}
            row_id = str(e.id)
        elif p["kind"] == "material":
            src = db.get(MaterialRate, p["material_id"])
            per = Decimal(str(p["new_rate"]))
            waste = 1 + Decimal(src.waste_pct or 0) / 100
            unit_cost = per / waste * (Decimal(src.coverage) if src.coverage and src.measure_unit != src.unit else 1)
            m = MaterialRate(name=src.name, sku=src.sku, trade=src.trade, trade_key=src.trade_key,
                             category=src.category, unit=src.unit, unit_cost=unit_cost.quantize(Decimal("0.0001")),
                             measure_unit=src.measure_unit, coverage=src.coverage, waste_pct=src.waste_pct,
                             source="own", supplier=src.supplier, project_type=src.project_type,
                             as_of_date=today, source_ref=f"Estimate review: {source}",
                             payload={"evidence": ev}, created_by=user_email)
            db.add(m)
            db.flush()
            for old in db.query(MaterialRate).filter(MaterialRate.name == src.name, MaterialRate.source == "own",
                                                     MaterialRate.is_active.is_(True), MaterialRate.id != m.id):
                old.is_active, old.superseded_by = False, m.id
            ref = {**(link.get("rate") or {}), "rate": p["new_rate"], "source": "own", "material_id": str(m.id)}
            row_id = str(m.id)
        else:  # labour
            src = db.get(LabourRate, p["labour_id"])
            wage = Decimal(str(p["new_rate"])) / (1 + Decimal(str(burden_pct(db))) / 100)
            r = LabourRate(trade=src.trade, trade_key=src.trade_key, region=src.region,
                           project_type=src.project_type, source="own", base_rate=wage.quantize(Decimal("0.01")),
                           discount_pct=Decimal(0), effective_rate=wage.quantize(Decimal("0.01")), as_of_date=today,
                           source_ref=f"Estimate review: {source}", payload={"evidence": ev}, created_by=user_email)
            db.add(r)
            db.flush()
            for old in db.query(LabourRate).filter(LabourRate.trade == src.trade, LabourRate.source == "own",
                                                   LabourRate.is_active.is_(True), LabourRate.id != r.id):
                old.is_active, old.superseded_by = False, r.id
            link["labour"] = {**(link.get("labour") or {}), "rate": p["new_rate"], "source": "own",
                              "labour_id": str(r.id)}
            row_id = str(r.id)
            ref = None
        if ref is not None:
            link["rate"] = ref
            link["status"] = "linked"
            it["basis"] = "library"
        link["writeback"] = {"status": "written", "row_id": row_id, "kind": p["kind"],
                             "total": p["new_total"], "by": user_email, "at": now}
        if p["kind"] != "labour":
            link["library_total"] = p["new_total"]
        summary["written"] += 1
    return summary
