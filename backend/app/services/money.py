"""The Money tab's summary: contract, change orders, invoiced, paid, owing,
and the next payment-schedule milestone that has no invoice yet.

Read-only. Every figure is derived from rows that already exist; the totals
use the same rules as Home (contract before GST, GST at 5%) and the invoice
router (an invoice's own `payload.total`, which includes GST).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

GST = 0.05

#: Dates are Vancouver dates: an invoice sent at 6pm on the 1st was sent on the
#: 1st, not on the 2nd as the UTC date would have it.
SITE_TZ = "America/Vancouver"


def _local(dt: datetime | None) -> date | None:
    from zoneinfo import ZoneInfo

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(SITE_TZ)).date()

#: Invoice statuses that have gone to the client.
ISSUED = ("sent", "partial", "paid", "overdue")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _json(v: Any) -> dict:
    if isinstance(v, str):
        try:
            return json.loads(v or "{}")
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _norm(s: str | None) -> str:
    """'Deposit — on signing (10%)' and 'Deposit — on signing' are one milestone."""
    s = re.sub(r"\([^)]*\)", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _same_milestone(a: str | None, b: str | None) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and (x == y or x.startswith(y) or y.startswith(x))


def _due(payload: dict, sent_at: datetime | None) -> date | None:
    """The due date, when the invoice says one: an explicit `dueDate`, or
    "Net N" terms counted from when it was sent. "Payable upon receipt" and
    unknown terms give None — not overdue by guesswork."""
    d = payload.get("dueDate")
    if d:
        try:
            return date.fromisoformat(str(d)[:10])
        except ValueError:
            return None
    m = re.search(r"net\s*(\d+)", str(payload.get("terms") or ""), re.I)
    if m and sent_at:
        return _local(sent_at) + timedelta(days=int(m.group(1)))
    return None


def schedule(db: Session, pid: str) -> list[dict[str, Any]]:
    """The payment schedule: the latest contract's, else the active estimate's.
    Normalised to {milestone, pct, amount (before GST) | None}."""
    row = db.execute(_T(
        "SELECT payload FROM contracts WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"),
        {"p": pid}).scalar()
    sched = _json(row).get("paymentSchedule") if row else None
    if not sched:
        est = db.execute(_T(
            "SELECT payload FROM estimate_versions WHERE project_id = :p AND is_active LIMIT 1"),
            {"p": pid}).scalar()
        sched = _json(est).get("paymentSchedule") if est else None
    out = []
    for e in sched or []:
        if not isinstance(e, dict) or not e.get("milestone"):
            continue
        pct = e.get("pct", e.get("percentage"))
        out.append({"milestone": str(e["milestone"]), "pct": _f(pct) if pct is not None else None,
                    "amount": _f(e["amount"]) if e.get("amount") is not None else None})
    return out


def summary(db: Session, project_id: UUID, *, today: date | None = None) -> dict[str, Any]:
    from app.services.home import active_estimate, contract_value

    pid = str(project_id)
    today = today or _local(datetime.now(timezone.utc))
    contract = contract_value(db, pid, active_estimate(db, pid))

    cos = []
    approved = 0.0
    for r in db.execute(_T("""
        SELECT id, number, title, cost_impact, schedule_impact_days, status, submitted_at, decided_at
          FROM change_orders WHERE project_id = :p AND status <> 'void' ORDER BY number"""),
            {"p": pid}).mappings().all():
        if r["status"] == "approved":
            approved += _f(r["cost_impact"])
        waiting = (today - _local(r["submitted_at"])).days if r["status"] == "pending" and r["submitted_at"] else None
        cos.append({"id": str(r["id"]), "number": r["number"], "title": r["title"],
                    "amount": _f(r["cost_impact"]), "days": r["schedule_impact_days"],
                    "status": r["status"], "waiting_days": waiting,
                    "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None})

    invs = []
    invoiced = paid = 0.0
    for r in db.execute(_T("""
        SELECT id, number, type, status, paid_amount, payload, sent_at, created_at
          FROM invoices WHERE project_id = :p AND status <> 'void' ORDER BY created_at"""),
            {"p": pid}).mappings().all():
        pl = _json(r["payload"])
        total = _f(pl.get("total"))
        due = _due(pl, r["sent_at"])
        status = r["status"]
        if status in ("sent", "partial") and due and due < today:
            status = "overdue"
        if r["status"] in ISSUED:
            invoiced += total
            paid += _f(r["paid_amount"])
        invs.append({"id": str(r["id"]), "number": r["number"], "type": r["type"],
                     "for": pl.get("milestone") or pl.get("description") or r["type"],
                     "milestone": pl.get("milestone"), "schedule_index": pl.get("scheduleIndex"),
                     "total": total, "paid": _f(r["paid_amount"]), "status": status,
                     "stored_status": r["status"], "due": due.isoformat() if due else None,
                     "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None})

    sched = schedule(db, pid)
    base = (contract or {}).get("amount") or 0
    nxt = None
    for i, e in enumerate(sched):
        billed = any(
            inv["schedule_index"] == i or _same_milestone(inv["milestone"], e["milestone"])
            for inv in invs)
        if billed:
            continue
        amount = e["amount"] if e["amount"] is not None else (
            round((base + approved) * e["pct"] / 100, 2) if e["pct"] is not None else None)
        nxt = {"index": i, "milestone": e["milestone"], "pct": e["pct"],
               "amount_before_gst": amount,
               "amount_with_gst": round(amount * (1 + GST), 2) if amount is not None else None}
        break

    contract_with_gst = round(base * (1 + GST), 2) if contract else None
    return {
        "contract": contract and {**contract, "with_gst": contract_with_gst},
        "change_orders": {"approved_before_gst": round(approved, 2),
                          "approved_with_gst": round(approved * (1 + GST), 2), "items": cos},
        "revised_contract_with_gst": round((base + approved) * (1 + GST), 2) if contract else None,
        "invoiced": round(invoiced, 2),
        "paid": round(paid, 2),
        "owing": round(invoiced - paid, 2),
        "invoices": invs,
        "schedule_count": len(sched),
        "next_milestone": nxt,
    }
