"""Schedule of values progress (round 2 board R2Billing): per line, its value,
% done, earned, billed and left; and the cash flow by month.

  * value: the latest schedule of values row (built from the billing plan);
  * % done: from Schedule › Record what happened, each of the division's
    activities weighted by its planned working days (done = 100%, started =
    its recorded %, otherwise 0). General conditions run the whole job, so
    their % is the share of the job's working days elapsed;
  * billed: what has been invoiced for contract stages (before GST), spread
    over the lines by value — BRIO bills by stage, not by trade, so a line's
    billed figure is its share, and the total is exact;
  * left = value − billed.
Read-only: nothing here writes.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import billing_plan as bp
from app.services import costs as costs_svc
from app.services.actuals import project_actuals, site_today
from app.services.replan import Calendar

BILLED_STATUSES = ("sent", "partial", "paid", "overdue")


def _f(v: Any) -> float:
    return float(v or 0)


def _pct_by_division(db: Session, pid: str, v: dict) -> dict[str, float]:
    today = site_today()
    actual = project_actuals(db, pid)
    sched, acts = v["_sched"], v["_acts"]
    cal = Calendar.from_json(sched["holidays"])
    out: dict[str, float] = {}
    for d in v["divisions"]:
        if d["whole_job"]:
            days = bp._division_days(v, d["division"])
            out[d["division"]] = round(100 * sum(1 for x in days if x <= today) / len(days), 1) if days else 0.0
            continue
        num = den = 0.0
        for a in acts:
            if a["phase"] not in d["phases"] or not a["start_date"] or not a["finish_date"]:
                continue
            w = len(bp._workdays(bp._date(a["start_date"]), bp._date(a["finish_date"]), cal))
            r = actual.get(a["activity_code"]) or {}
            if r.get("status") == "done" or r.get("actual_finish"):
                p = 100.0
            elif r.get("status") == "started" or r.get("actual_start"):
                p = float(r.get("percent_complete") or 0)
            else:
                p = 0.0
            num += w * p
            den += w
        out[d["division"]] = round(num / den, 1) if den else 0.0
    return out


def progress(db: Session, project_id: UUID | str) -> dict[str, Any] | None:
    pid = str(project_id)
    sov = db.execute(text("""SELECT id, version_number, rows, total_pretax, created_at FROM schedules_of_values
                              WHERE project_id = :p AND scenario = 'base'
                              ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).mappings().first()
    if not sov:
        return None
    rows = sov["rows"] if isinstance(sov["rows"], list) else json.loads(sov["rows"] or "[]")
    try:
        v = bp.view(db, pid)
        pct = _pct_by_division(db, pid, v)
        pct_note = None
    except bp.PlanError as e:
        pct, pct_note = {}, str(e)

    billed_total = Decimal("0")
    for r in db.execute(text("""SELECT payload FROM invoices WHERE project_id = :p AND type::text = 'progress'
                                 AND status::text = ANY(:st)"""), {"p": pid, "st": list(BILLED_STATUSES)}).scalars():
        p = r if isinstance(r, dict) else json.loads(r or "{}")
        billed_total += Decimal(str(p.get("subtotal") or 0))
    values = [Decimal(str(r.get("scheduledValue") or 0)) for r in rows]
    shares = costs_svc._split(billed_total, [float(x) for x in values]) if sum(values) and billed_total else \
        [Decimal("0")] * len(rows)

    out_rows = []
    for r, value, billed in zip(rows, values, shares):
        div = r.get("division")
        p = pct.get(div) if div else None
        earned = (value * Decimal(str(p)) / 100).quantize(Decimal("0.01")) if p is not None else None
        out_rows.append({
            "line": r.get("tradeNumber"), "description": r.get("description"), "division": div,
            "value": float(value), "pct_done": p, "earned": float(earned) if earned is not None else None,
            "billed": float(billed), "left": float(value - billed),
        })
    total = sum(values)
    earned_total = sum(Decimal(str(x["earned"])) for x in out_rows if x["earned"] is not None)

    months: dict[str, dict[str, float]] = {}
    cf = db.execute(text("""SELECT weekly_periods FROM cash_flows WHERE project_id = :p AND scenario = 'base'
                             ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).scalar()
    for w in (cf if isinstance(cf, list) else json.loads(cf or "[]")):
        m = date.fromisoformat(w["week_end"]).strftime("%Y-%m")
        e = months.setdefault(m, {"in": 0.0, "out": 0.0})
        e["in"] += _f(w.get("draws_total"))
        e["out"] += _f(w.get("outflow_total"))
    return {
        "as_of": str(site_today()),
        "sov": {"id": str(sov["id"]), "version_number": sov["version_number"]},
        "rows": out_rows,
        "total": {"value": float(total), "earned": float(earned_total),
                  "pct_done": round(float(earned_total / total * 100), 1) if total else 0.0,
                  "billed": float(billed_total), "left": float(total - billed_total),
                  "billed_ahead": float(billed_total - earned_total)},
        "pct_note": pct_note,
        "months": [{"month": k, "in": round(x["in"], 2), "out": round(x["out"], 2)} for k, x in sorted(months.items())],
    }


def render_pdf(db: Session, project_id: UUID | str) -> bytes:
    from weasyprint import HTML  # lazy — heavy native deps

    p = progress(db, project_id)
    if p is None:
        raise bp.PlanError("No schedule of values yet. Save the billing plan on Contract first.")
    name = db.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": str(project_id)}).scalar() or ""
    esc = lambda s: (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    m = lambda n: "—" if n is None else f"${n:,.2f}"  # noqa: E731
    pc = lambda n: "—" if n is None else f"{n:.0f}%"  # noqa: E731
    body = "".join(
        f"<tr><td>{esc(r['line'])}</td><td>{esc(r['description'])}</td><td class=n>{m(r['value'])}</td>"
        f"<td class=n>{pc(r['pct_done'])}</td><td class=n>{m(r['earned'])}</td><td class=n>{m(r['billed'])}</td>"
        f"<td class=n>{m(r['left'])}</td></tr>" for r in p["rows"])
    t = p["total"]
    html = f"""<html><head><meta charset="utf-8"><style>
@page {{ size: letter landscape; margin: 14mm; }}
body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 9pt; color: #1c1c1a; }}
h1 {{ font-size: 15pt; color: #0068A0; margin: 0 0 2px; }} .sub {{ color: #555; margin: 0 0 10px; }}
table {{ width: 100%; border-collapse: collapse; }} th, td {{ padding: 4px 6px; border-bottom: 1px solid #ddd; }}
th {{ text-align: left; font-size: 8pt; text-transform: uppercase; color: #555; border-bottom: 1.5px solid #0068A0; }}
.n {{ text-align: right; }} tfoot td {{ font-weight: bold; border-top: 1.5px solid #0068A0; }}
.note {{ margin-top: 10px; color: #555; font-size: 8pt; }}
</style></head><body>
<h1>Schedule of values</h1>
<p class="sub">{esc(name)} · as of {esc(p['as_of'])} · version {p['sov']['version_number']} · amounts before GST</p>
<table><thead><tr><th>Line</th><th>Description</th><th class=n>Value</th><th class=n>% done</th><th class=n>Earned</th>
<th class=n>Billed</th><th class=n>Left</th></tr></thead><tbody>{body}</tbody>
<tfoot><tr><td></td><td>Total</td><td class=n>{m(t['value'])}</td><td class=n>{pc(t['pct_done'])}</td>
<td class=n>{m(t['earned'])}</td><td class=n>{m(t['billed'])}</td><td class=n>{m(t['left'])}</td></tr></tfoot></table>
<p class="note">% done comes from recorded progress on site. Billed is what has been invoiced for contract stages,
shown against each line by its share of the contract.</p>
</body></html>"""
    return HTML(string=html).write_pdf()
