"""Money › Costs: bills BRIO receives on a project, and a budget per division.

A bill is a `project_costs` row entered by the office: who it's from, the date
on the bill, the amount BEFORE GST and the GST separately, the division it
belongs to, and optionally the bill's file (a document, category "invoice").
BRIO's own invoices to the client live in `invoices`; the two money
directions never mix.

The budget (Amir, 2026-10-30, option b) spreads the contract's base value
across divisions in the proportions of one itemised estimate, plus each
approved change order as its own line. It is a share of the CONTRACT PRICE, so
it includes overhead and profit: a division that is "on budget" can still be
eating margin. The screen says so.

Bills entered here are kept OUT of the estimate writer's cost benchmarks
(workers/ai_worker._brio_cost_benchmarks). Those average whole rows per
category, which for a stream of lumber bills is not a rate; using them would
quietly change what the estimator is told.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.actuals import site_today
from app.services.divisions import divisions_for_type

ENTRY_KINDS = ("supplier_invoice", "subtrade_invoice", "other")
ENTERED = "entered"  # payload["source"] on rows made here
GST_CAP = Decimal("0.15")  # GST above 15% of the amount is a typing slip (total typed as GST)


class CostError(ValueError):
    """A bill or budget the office must correct before it is saved."""


class Duplicate(CostError):
    """The same supplier's invoice number is already on this project."""


def _d(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def division_labels(db: Session, project_id: UUID | str) -> list[str]:
    ptype = db.execute(text("SELECT type FROM projects WHERE id = :p"), {"p": str(project_id)}).scalar()
    return [d.label for d in divisions_for_type(db, ptype)]


def contract(db: Session, project_id: UUID | str) -> dict[str, Any] | None:
    """The executed contract's base value (before GST) and approved change
    orders. None when no contract value is stored."""
    base = db.execute(text(
        "SELECT base_contract_value FROM contracts WHERE project_id = :p "
        "ORDER BY version_number DESC LIMIT 1"), {"p": str(project_id)}).scalar()
    if base is None:
        return None
    cos = [dict(r) for r in db.execute(text(
        "SELECT number, title, cost_impact FROM change_orders WHERE project_id = :p AND status = 'approved' "
        "ORDER BY number"), {"p": str(project_id)}).mappings().all()]
    cos = [{"number": c["number"], "title": c["title"], "amount": _f(c["cost_impact"])} for c in cos]
    return {"base": _f(base), "approved_change_orders": cos,
            "revised": round(_f(base) + sum(c["amount"] for c in cos), 2)}


def _audit(db: Session, project_id, table: str, row_id, action: str, uid, diff: dict) -> None:
    db.execute(text("""
        INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
        VALUES (:p, :t, :r, :a, :u, CAST(:d AS jsonb))
    """), {"p": str(project_id), "t": table, "r": row_id, "a": action, "u": uid,
           "d": json.dumps(diff, default=str)})


_BILL_COLS = ("id, kind, vendor, invoice_number, category AS division, description, amount, gst, "
              "cost_date, source_document_id, created_at")


def _bill(r: Any) -> dict[str, Any]:
    b = dict(r)
    b["id"] = str(b["id"])
    b["amount"] = _f(b["amount"])
    b["gst"] = _f(b["gst"])
    b["cost_date"] = str(b["cost_date"]) if b["cost_date"] else None
    b["source_document_id"] = str(b["source_document_id"]) if b["source_document_id"] else None
    b["created_at"] = b["created_at"].isoformat() if b["created_at"] else None
    return b


def summary(db: Session, project_id: UUID | str) -> dict[str, Any]:
    pid = str(project_id)
    labels = division_labels(db, pid)
    bills = [_bill(r) for r in db.execute(text(
        f"SELECT {_BILL_COLS} FROM project_costs WHERE project_id = :p "
        "ORDER BY cost_date DESC NULLS LAST, created_at DESC"), {"p": pid}).mappings().all()]
    budget_rows = [{"id": str(r["id"]), "division": r["division"], "amount": _f(r["amount"]),
                    "source": r["source"], "note": r["note"]}
                   for r in db.execute(text(
                       "SELECT id, division, amount, source, note FROM project_cost_budgets "
                       "WHERE project_id = :p ORDER BY created_at, division"), {"p": pid}).mappings().all()]

    budget: dict[str, float] = {}
    for b in budget_rows:
        budget[b["division"]] = round(budget.get(b["division"], 0) + b["amount"], 2)
    spent: dict[str | None, float] = {}
    for b in bills:
        spent[b["division"]] = round(spent.get(b["division"], 0) + b["amount"], 2)

    order = labels + sorted(k for k in set(budget) | {k for k in spent if k} if k not in labels)
    by_division = []
    for lab in order:
        if lab not in budget and lab not in spent:
            continue
        bud = budget.get(lab)
        sp = spent.get(lab, 0.0)
        by_division.append({"division": lab, "budget": bud, "spent": sp,
                            "left": round(bud - sp, 2) if bud is not None else None,
                            "over": bud is not None and sp > bud})
    return {
        "divisions": labels,
        "kinds": list(ENTRY_KINDS),
        "contract": contract(db, pid),
        "bills": bills,
        "spent_before_gst": round(sum(b["amount"] for b in bills), 2),
        "gst": round(sum(b["gst"] for b in bills), 2),
        "unassigned_spent": spent.get(None, 0.0),
        "budget_total": round(sum(budget.values()), 2) if budget else None,
        "budget_rows": budget_rows,
        "by_division": by_division,
        "vendors": sorted({b["vendor"] for b in bills if b["vendor"]} | set(
            db.execute(text("SELECT name FROM suppliers WHERE active IS NOT FALSE")).scalars().all())),
    }


def _clean(db: Session, pid: str, data: dict[str, Any], labels: list[str], exclude_id: str | None) -> dict:
    kind = data.get("kind") or "supplier_invoice"
    if kind not in ENTRY_KINDS:
        raise CostError(f"Kind must be one of {', '.join(ENTRY_KINDS)}.")
    vendor = (data.get("vendor") or "").strip()
    if not vendor:
        raise CostError("Say who the bill is from.")
    when = data.get("cost_date")
    if not when:
        raise CostError("Give the date on the bill.")
    if isinstance(when, str):
        when = date.fromisoformat(when)
    if when > site_today():
        raise CostError("The bill's date is in the future.")
    if data.get("amount") in (None, ""):
        raise CostError("Give the amount before GST.")
    amount = _d(data["amount"])
    if amount == 0:
        raise CostError("The amount can't be zero.")
    gst = _d(data.get("gst") or 0)
    if gst != 0 and (gst > 0) != (amount > 0):
        raise CostError("GST must have the same sign as the amount (a credit note has both negative).")
    if abs(gst) > abs(amount) * GST_CAP:
        raise CostError("GST is more than 15% of the amount. Is the total in the GST box?")
    division = (data.get("division") or "").strip() or None
    if division and labels and division not in labels:
        raise CostError(f"'{division}' isn't one of this project's divisions.")
    number = (data.get("invoice_number") or "").strip() or None
    if number:
        clash = db.execute(text("""
            SELECT cost_date FROM project_costs
            WHERE project_id = :p AND lower(trim(vendor)) = lower(:v)
              AND regexp_replace(lower(coalesce(invoice_number, '')), '[^a-z0-9]', '', 'g') = :n
              AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))
        """), {"p": pid, "v": vendor, "n": _norm(number), "x": exclude_id}).first()
        if clash:
            raise Duplicate(f"{vendor} invoice {number} is already entered (dated {clash[0]}).")
    doc = data.get("source_document_id")
    if doc:
        owner = db.execute(text("SELECT project_id FROM documents WHERE id = :d"), {"d": str(doc)}).scalar()
        if owner is None or str(owner) != pid:
            raise CostError("The attached file isn't one of this project's documents.")
    return {"kind": kind, "vendor": vendor, "cost_date": when, "amount": amount, "gst": gst,
            "category": division, "invoice_number": number,
            "description": (data.get("description") or "").strip() or None,
            "source_document_id": str(doc) if doc else None}


def create(db: Session, project_id: UUID | str, data: dict[str, Any], uid: UUID | None) -> dict[str, Any]:
    pid = str(project_id)
    row = _clean(db, pid, data, division_labels(db, pid), None)
    cid = uuid4()
    db.execute(text("""
        INSERT INTO project_costs (id, project_id, kind, vendor, cost_date, amount, gst, category,
            invoice_number, description, source_document_id, created_by_id, payload)
        VALUES (:id, :p, :kind, :vendor, :cost_date, :amount, :gst, :category, :invoice_number,
            :description, :source_document_id, :uid, CAST(:payload AS jsonb))
    """), {**row, "id": cid, "p": pid, "uid": uid, "payload": json.dumps({"source": ENTERED})})
    _audit(db, pid, "project_costs", cid, "cost-entered", uid, row)
    return get(db, pid, cid)


def get(db: Session, project_id: UUID | str, cost_id: UUID | str) -> dict[str, Any]:
    r = db.execute(text(f"SELECT {_BILL_COLS} FROM project_costs WHERE id = :i AND project_id = :p"),
                   {"i": str(cost_id), "p": str(project_id)}).mappings().first()
    if r is None:
        raise LookupError("bill not found")
    return _bill(r)


def update(db: Session, project_id: UUID | str, cost_id: UUID | str, data: dict[str, Any],
           uid: UUID | None) -> dict[str, Any]:
    pid = str(project_id)
    before = get(db, pid, cost_id)
    merged = {**before, **data}
    row = _clean(db, pid, merged, division_labels(db, pid), str(cost_id))
    db.execute(text("""
        UPDATE project_costs SET kind = :kind, vendor = :vendor, cost_date = :cost_date, amount = :amount,
            gst = :gst, category = :category, invoice_number = :invoice_number, description = :description,
            source_document_id = :source_document_id, updated_at = now()
        WHERE id = :id AND project_id = :p
    """), {**row, "id": str(cost_id), "p": pid})
    _audit(db, pid, "project_costs", cost_id, "cost-edited", uid, {"before": before, "after": row})
    return get(db, pid, cost_id)


def delete(db: Session, project_id: UUID | str, cost_id: UUID | str, uid: UUID | None) -> None:
    pid = str(project_id)
    before = get(db, pid, cost_id)
    db.execute(text("DELETE FROM project_costs WHERE id = :i AND project_id = :p"), {"i": str(cost_id), "p": pid})
    _audit(db, pid, "project_costs", cost_id, "cost-deleted", uid, {"before": before})


# ── Budget ────────────────────────────────────────────────────────────────


def _item_cost(i: dict) -> float:
    lab, mat = i.get("labor"), i.get("materials")
    if lab is not None or mat is not None:
        return _f(lab) + _f(mat)
    return _f(i.get("total") or i.get("amount"))


def _title(label: str) -> str:
    """'11 Mechanical / HVAC' -> 'mechanicalhvac'. Estimates written before the
    18 were renumbered carry the right title under the wrong number."""
    return _norm(re.sub(r"^\s*\d+\s+", "", label or ""))


_STOP = {"and", "the", "of", "a", "to", "for", "with", "upgrade", "new", "existing", "work"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def guess_division(text_: str, labels: list[str]) -> str | None:
    """The division sharing most words with a change order's title (stems by
    prefix, so 'cladding' meets 'Cladding'). None on no overlap or a tie."""
    ws = _words(text_)
    scored = []
    for lab in labels:
        lw = _words(re.sub(r"^\s*\d+\s+", "", lab))
        n = sum(1 for w in ws if any(w[:6] == x[:6] for x in lw))
        scored.append((n, lab))
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def _split(total: Decimal, weights: list[float]) -> list[Decimal]:
    """`total` split by `weights` to the cent, summing to `total` exactly
    (largest remainder)."""
    cents = int((total * 100).to_integral_value(rounding=ROUND_HALF_UP))
    w = sum(weights)
    raw = [cents * x / w for x in weights]
    out = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[: cents - sum(out)]:
        out[i] += 1
    return [Decimal(c) / 100 for c in out]


def estimates_for_budget(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    out = []
    for r in db.execute(text(
            "SELECT id, version_number, label, payload FROM estimate_versions WHERE project_id = :p "
            "ORDER BY version_number"), {"p": str(project_id)}).mappings().all():
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
        cost = sum(_item_cost(i) for c in payload.get("categories") or [] for i in c.get("items") or [])
        if cost > 0:
            out.append({"id": str(r["id"]), "version_number": r["version_number"], "label": r["label"],
                        "direct_cost": round(cost, 2)})
    return out


def budget_preview(db: Session, project_id: UUID | str, estimate_version_id: UUID | str,
                   mapping: dict[str, str] | None = None, co_divisions: dict[str, str] | None = None
                   ) -> dict[str, Any]:
    pid = str(project_id)
    c = contract(db, pid)
    if c is None:
        raise CostError("No contract value is stored for this project, so there is nothing to spread.")
    r = db.execute(text("SELECT version_number, payload FROM estimate_versions WHERE id = :i AND project_id = :p"),
                   {"i": str(estimate_version_id), "p": pid}).mappings().first()
    if r is None:
        raise CostError("That estimate isn't on this project.")
    payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
    labels = division_labels(db, pid)
    by_title = {_title(lab): lab for lab in labels}
    mapping = mapping or {}
    co_divisions = co_divisions or {}

    cats = []
    for cat in payload.get("categories") or []:
        name = cat.get("name") or ""
        cost = sum(_item_cost(i) for i in cat.get("items") or [])
        if cost <= 0:
            continue
        auto = name if name in labels else by_title.get(_title(name))
        chosen = mapping.get(name) or auto
        cats.append({"name": name, "direct_cost": round(cost, 2), "division": chosen, "suggested": auto})
    if not cats:
        raise CostError("That estimate has no priced lines to take proportions from.")
    total_cost = sum(x["direct_cost"] for x in cats)
    for x, amt in zip(cats, _split(_d(c["base"]), [x["direct_cost"] for x in cats])):
        x["share"] = round(x["direct_cost"] / total_cost, 4)
        x["amount"] = float(amt)
    cos = []
    for co in c["approved_change_orders"]:
        key = str(co["number"])
        auto = guess_division(co["title"] or "", labels)
        cos.append({**co, "division": co_divisions.get(key) or auto, "suggested": auto})
    return {"estimate_version_id": str(estimate_version_id), "version_number": r["version_number"],
            "contract_base": c["base"], "categories": cats, "change_orders": cos, "divisions": labels,
            "complete": all(x["division"] in labels for x in cats) and all(x["division"] in labels for x in cos),
            "total": round(sum(x["amount"] for x in cats) + sum(x["amount"] for x in cos), 2)}


def set_budget(db: Session, project_id: UUID | str, estimate_version_id: UUID | str,
               mapping: dict[str, str], co_divisions: dict[str, str], uid: UUID | None) -> dict[str, Any]:
    """Replace the project's budget with the contract spread by this estimate's
    proportions plus each approved change order. Amounts are recomputed here,
    never taken from the browser."""
    pid = str(project_id)
    p = budget_preview(db, pid, estimate_version_id, mapping, co_divisions)
    if not p["complete"]:
        missing = [x["name"] for x in p["categories"] if x["division"] not in p["divisions"]] + \
                  [f"CO-{x['number']}" for x in p["change_orders"] if x["division"] not in p["divisions"]]
        raise CostError("Pick a division for: " + ", ".join(missing))
    old = [dict(r) for r in db.execute(text(
        "SELECT division, amount, source FROM project_cost_budgets WHERE project_id = :p"), {"p": pid}).mappings()]
    db.execute(text("DELETE FROM project_cost_budgets WHERE project_id = :p"), {"p": pid})
    per_div: dict[str, Decimal] = {}
    for x in p["categories"]:
        per_div[x["division"]] = per_div.get(x["division"], Decimal(0)) + _d(x["amount"])
    src = f"Contract share, in estimate v{p['version_number']}'s proportions"
    rows = [(div, amt, src, None) for div, amt in per_div.items()]
    rows += [(x["division"], _d(x["amount"]), f"CO-{x['number']}", x["title"]) for x in p["change_orders"]]
    for div, amt, source, note in rows:
        db.execute(text("""
            INSERT INTO project_cost_budgets (id, project_id, division, amount, source, note, created_by_id)
            VALUES (:i, :p, :d, :a, :s, :n, :u)
        """), {"i": uuid4(), "p": pid, "d": div, "a": amt, "s": source, "n": note, "u": uid})
    _audit(db, pid, "project_cost_budgets", None, "budget-set", uid, {
        "estimate_version_id": str(estimate_version_id), "mapping": mapping, "co_divisions": co_divisions,
        "before": old, "total": p["total"]})
    return summary(db, pid)
