"""Billing plan: the signed contract's stages, when each is billed, and the
schedule of values + cash flow built from them.

Sources, none of which is changed here:
  * stages and amounts: the contract's payload.paymentSchedule (as signed);
  * when a stage is billed: an invoice already issued for it (matched by stage
    name), else the finish of the schedule activity picked for it, from the
    LATEST schedule (a recorded actual finish wins);
  * the holdback stage: billed at substantial completion (recorded, else the
    latest schedule's P50 finish), paid 55 days later;
  * the schedule of values: the Money › Costs budget, each line spread over the
    working days of its division's schedule activities;
  * money out: bills entered, plus cost_share_pct of what's left of each
    division's budget, spread over that division's remaining working days.

Everything in the cash flow is before GST: GST collected is remitted and GST
paid is claimed back, so neither is BRIO's cash.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import costs as costs_svc
from app.services.actuals import project_actuals, site_today
from app.services.replan import Calendar

SITE_TZ = ZoneInfo("America/Vancouver")
HOLDBACK_RELEASE_DAYS = 55  # BC Builders Lien Act, and Art. 4.2 of the 1234 contract
BASIS = "billing-plan"  # marks SOV rows / cash flows built here, so nothing rescales them


class PlanError(ValueError):
    pass


# ── small helpers ────────────────────────────────────────────────────────
def _f(v: Any) -> float:
    return float(v or 0)


def _date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        # Timestamps are stored in UTC; the day that counts is the site's.
        return v.astimezone(SITE_TZ).date() if v.tzinfo else v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _key(s: str | None) -> str:
    """Stage / invoice milestone text compared without '(10%)', dashes, case."""
    s = re.sub(r"\(\s*\d+(\.\d+)?\s*%\s*\)", " ", s or "")
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _workdays(start: date, finish: date, cal: Calendar) -> list[date]:
    if finish < start:
        start, finish = finish, start
    out, d = [], start
    while d <= finish:
        if cal.working(d):
            out.append(d)
        d += timedelta(days=1)
    return out or [start]


def _month(d: date) -> str:
    return d.strftime("%b %Y")


# ── loading ──────────────────────────────────────────────────────────────
def _contract(db: Session, pid: str):
    return db.execute(text("""
        SELECT id, reference, status, version_number, signed_at, payload,
               project_start_date, substantial_completion_date, substantial_completion_actual
          FROM contracts WHERE project_id = :p
         ORDER BY (status IN ('executed','signed')) DESC, version_number DESC LIMIT 1"""),
        {"p": pid}).mappings().first()


def _schedule(db: Session, pid: str):
    s = db.execute(text("""
        SELECT id, version_number, project_start, holidays, p50_finish_date, deterministic_finish_date
          FROM schedules WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"""),
        {"p": pid}).mappings().first()
    if not s:
        return None, []
    acts = db.execute(text("""
        SELECT activity_code, name, phase, start_date, finish_date, is_milestone
          FROM activities WHERE schedule_id = :s ORDER BY start_date, activity_code"""),
        {"s": s["id"]}).mappings().all()
    return s, [dict(a) for a in acts]


def _plan_row(db: Session, pid: str):
    return db.execute(text("""
        SELECT stage_activities, division_phases, cost_share_pct, owner_net_days, updated_at
          FROM billing_plans WHERE project_id = :p"""), {"p": pid}).mappings().first()


def _invoices(db: Session, pid: str) -> list[dict]:
    rows = db.execute(text("""
        SELECT number, status::text AS status, payload, paid_amount, paid_at, created_at
          FROM invoices WHERE project_id = :p AND status::text <> 'void'
         ORDER BY created_at"""), {"p": pid}).mappings().all()
    return [dict(r) for r in rows]


def _phase_division(phase: str, labels: list[str]) -> str | None:
    """A schedule phase's division: same title first ('Site Work' is 17, not
    02 '... Site Clearing'), else the most shared words."""
    for lab in labels:
        if costs_svc._title(lab).lower() == (phase or "").strip().lower():
            return lab
    return costs_svc.guess_division(phase or "", labels)


# ── the plan as the screen shows it ──────────────────────────────────────
def view(db: Session, project_id: UUID | str, override: dict | None = None) -> dict[str, Any]:
    """The billing plan, with `override` (an unsaved body) applied when given."""
    pid = str(project_id)
    c = _contract(db, pid)
    if not c:
        raise PlanError("This project has no contract yet.")
    payload = c["payload"] if isinstance(c["payload"], dict) else json.loads(c["payload"] or "{}")
    raw_stages = payload.get("paymentSchedule") or []
    if not raw_stages:
        raise PlanError("The contract has no payment stages. Set them under Payment terms first.")
    sched, acts = _schedule(db, pid)
    if not sched:
        raise PlanError("This project has no schedule yet.")
    by_code = {a["activity_code"]: a for a in acts}
    actual = project_actuals(db, pid)

    saved = _plan_row(db, pid)
    o = override or {}
    stage_acts: dict[str, str | None] = dict((saved["stage_activities"] if saved else {}) or {})
    stage_acts.update({str(k): v for k, v in (o.get("stage_activities") or {}).items()})
    div_phases: dict[str, str | None] = dict((saved["division_phases"] if saved else {}) or {})
    div_phases.update(o.get("division_phases") or {})
    share = float(o["cost_share_pct"]) if o.get("cost_share_pct") is not None else (
        _f(saved["cost_share_pct"]) if saved else 85.0)
    net_days = int(o["owner_net_days"]) if o.get("owner_net_days") is not None else (
        int(saved["owner_net_days"]) if saved else 0)
    sc_actual = _date(o["substantial_completion_actual"]) if "substantial_completion_actual" in o else \
        _date(c["substantial_completion_actual"])

    def finish_of(code: str | None) -> date | None:
        if not code or code not in by_code:
            return None
        a = actual.get(code) or {}
        return _date(a.get("actual_finish")) or _date(by_code[code]["finish_date"])

    expected_sc = _date(sched["p50_finish_date"] or sched["deterministic_finish_date"])
    sc = sc_actual or expected_sc
    invoices = _invoices(db, pid)
    used: set[str] = set()
    stages, problems = [], []
    for i, s in enumerate(raw_stages, 1):
        k = _key(s.get("milestone"))
        inv = next((v for v in invoices if v["number"] not in used and k and (
            _key((v["payload"] or {}).get("milestone")).startswith(k)
            or k.startswith(_key((v["payload"] or {}).get("milestone")) or "\x00"))), None)
        holdback = "holdback" in k
        code = stage_acts.get(str(i)) or None
        st: dict[str, Any] = {
            "no": i, "milestone": s.get("milestone"), "pct": _f(s.get("pct")),
            "amount": round(_f(s.get("amount")), 2), "detail": s.get("detail") or "",
            "holdback": holdback, "activity_code": code,
            "activity_name": by_code[code]["name"] if code in by_code else None,
            "invoice": None, "bill_date": None, "paid_date": None, "needs_pick": False,
        }
        if code and code not in by_code:
            problems.append(f"Stage {i}: {code} isn't in schedule v{sched['version_number']}.")
        if inv:
            used.add(inv["number"])
            p = inv["payload"] or {}
            st["invoice"] = {"number": inv["number"], "status": inv["status"],
                             "date": p.get("date"), "subtotal": _f(p.get("subtotal")) or st["amount"],
                             "paid_at": str(_date(inv["paid_at"])) if inv["paid_at"] else None}
            st["bill_date"] = _date(p.get("date")) or _date(inv["created_at"])
            st["paid_date"] = _date(inv["paid_at"]) or (st["bill_date"] + timedelta(days=net_days))
        elif holdback:
            st["bill_date"] = sc
            st["paid_date"] = sc + timedelta(days=HOLDBACK_RELEASE_DAYS) if sc else None
        else:
            f = finish_of(code)
            if f is None:
                st["needs_pick"] = True
                problems.append(f"Pick when stage {i} ({s.get('milestone')}) is billed.")
            else:
                st["bill_date"] = f
                st["paid_date"] = f + timedelta(days=net_days)
        stages.append(st)

    # Divisions in the budget, and the schedule work each is spread over.
    labels = costs_svc.division_labels(db, pid)
    budget = db.execute(text("""
        SELECT division, amount, source, note FROM project_cost_budgets
         WHERE project_id = :p ORDER BY created_at, division"""), {"p": pid}).mappings().all()
    phases = sorted({a["phase"] for a in acts if a["phase"]},
                    key=lambda ph: min(str(a["start_date"]) for a in acts if a["phase"] == ph))
    auto: dict[str, list[str]] = {}
    for ph in phases:
        d = _phase_division(ph, labels)
        if d:
            auto.setdefault(d, []).append(ph)
    divisions = []
    for d in sorted({b["division"] for b in budget}):
        general = d.startswith("01 ")
        picked = div_phases.get(d)
        ph = [picked] if picked else auto.get(d, [])
        need = not general and not ph
        if picked and picked not in phases:
            problems.append(f"{d}: '{picked}' isn't a phase in schedule v{sched['version_number']}.")
        if need:
            problems.append(f"Pick the schedule work for {d}.")
        divisions.append({"division": d, "budget": round(sum(_f(b["amount"]) for b in budget
                                                            if b["division"] == d), 2),
                          "phases": ph, "picked": picked, "whole_job": general, "needs_pick": need})
    if not budget:
        problems.append("Set the budget on Money › Costs first.")
    if sc is None:
        problems.append("The schedule has no finish date, so the holdback can't be dated.")
    if sc_actual and sc_actual > site_today():
        problems.append("Substantial completion can't be recorded in the future.")
    if not 1 <= share <= 100:
        problems.append("Cost share must be between 1% and 100% of the budget.")
    if not 0 <= net_days <= 120:
        problems.append("Client payment days must be between 0 and 120.")

    contract_vals = costs_svc.contract(db, pid) or {}
    stage_total = round(sum(s["amount"] for s in stages), 2)
    revised = contract_vals.get("revised")
    latest = lambda t: db.execute(text(  # noqa: E731
        f"SELECT id, version_number, created_at FROM {t} WHERE project_id = :p "
        "ORDER BY version_number DESC LIMIT 1"), {"p": pid}).mappings().first()
    sov, cf = latest("schedules_of_values"), latest("cash_flows")
    client_v = db.execute(text("""SELECT max(version_number) FROM schedules
                                   WHERE project_id = :p AND client_visible"""), {"p": pid}).scalar()
    return {
        "contract": {"id": str(c["id"]), "reference": c["reference"], "status": c["status"],
                     "signed_at": c["signed_at"].isoformat() if c["signed_at"] else None,
                     "substantial_completion_date": str(c["substantial_completion_date"])
                     if c["substantial_completion_date"] else None,
                     "substantial_completion_actual": str(sc_actual) if sc_actual else None,
                     "revised": revised},
        "schedule": {"id": str(sched["id"]), "version_number": sched["version_number"],
                     "client_version": client_v,
                     "project_start": str(sched["project_start"]) if sched["project_start"] else None,
                     "expected_substantial_completion": str(expected_sc) if expected_sc else None},
        "stages": [{**s, "bill_date": str(s["bill_date"]) if s["bill_date"] else None,
                    "paid_date": str(s["paid_date"]) if s["paid_date"] else None} for s in stages],
        "stage_total": stage_total,
        "stages_off_by": round(stage_total - revised, 2) if revised is not None else None,
        "activities": [{"code": a["activity_code"], "name": a["name"], "phase": a["phase"],
                        "finish_date": str(finish_of(a["activity_code"]) or ""),
                        "is_milestone": bool(a["is_milestone"])} for a in acts],
        "phases": phases,
        "divisions": divisions,
        "cost_share_pct": share,
        "owner_net_days": net_days,
        "holdback_release_days": HOLDBACK_RELEASE_DAYS,
        "complete": not problems,
        "problems": problems,
        "saved_at": saved["updated_at"].isoformat() if saved and saved["updated_at"] else None,
        "latest_sov": {"id": str(sov["id"]), "version_number": sov["version_number"]} if sov else None,
        "latest_cash_flow": {"id": str(cf["id"]), "version_number": cf["version_number"]} if cf else None,
        # internal, stripped by the router
        "_sched": sched, "_acts": acts, "_budget": [dict(b) for b in budget], "_sc": sc,
        "_expected_sc": expected_sc, "_stage_dates": stages,
    }


def public(v: dict) -> dict:
    return {k: val for k, val in v.items() if not k.startswith("_")}


# ── schedule of values ───────────────────────────────────────────────────
def _division_days(v: dict, division: str) -> list[date]:
    sched, acts = v["_sched"], v["_acts"]
    hol = Calendar.from_json(sched["holidays"])
    start = _date(sched["project_start"]) or min(_date(a["start_date"]) for a in acts)
    d = next(x for x in v["divisions"] if x["division"] == division)
    if d["whole_job"]:
        return _workdays(start, v["_expected_sc"] or start, hol)
    days: list[date] = []
    for a in acts:
        if a["phase"] in d["phases"] and a["start_date"] and a["finish_date"]:
            days += _workdays(_date(a["start_date"]), _date(a["finish_date"]), hol)
    return days


def build_sov(v: dict) -> dict:
    rows, months_set = [], set()
    for b in v["_budget"]:
        days = _division_days(v, b["division"])
        per_month: dict[date, int] = {}
        for d in days:
            m = d.replace(day=1)
            per_month[m] = per_month.get(m, 0) + 1
        keys = sorted(per_month)
        parts = costs_svc._split(Decimal(str(b["amount"])), [per_month[k] for k in keys])
        monthly = {_month(k): float(p) for k, p in zip(keys, parts)}
        months_set |= set(keys)
        co = (b["source"] or "").startswith("CO-")
        rows.append({
            "tradeNumber": (b["source"] or "").split()[0] if co else b["division"][:2],
            "description": f"{b['division'][3:]} — {b['note']}" if co and b["note"] else b["division"][3:],
            "division": b["division"], "optional": False, "basis": "budget",
            "scheduledValue": round(_f(b["amount"]), 2), "monthly": monthly,
        })
    months = [_month(m) for m in sorted(months_set)]
    totals = {m: round(sum(r["monthly"].get(m, 0) for r in rows), 2) for m in months}
    run, cum = 0.0, {}
    for r in rows:
        acc = 0.0
        r["cumulativePct"] = {}
        for m in months:
            acc += r["monthly"].get(m, 0)
            r["monthly"].setdefault(m, 0.0)
            r["cumulativePct"][m] = round(100 * acc / r["scheduledValue"], 1) if r["scheduledValue"] else 0
    for m in months:
        run += totals[m]
        cum[m] = round(run, 2)
    lien = {m: round(totals[m] * 0.10, 2) for m in months}
    return {"months": months, "rows": rows, "monthly_totals": totals, "cumulative_totals": cum,
            "lien_holdback_10pct": lien, "owner_net_monthly": {m: round(totals[m] - lien[m], 2) for m in months},
            "total_pretax": round(sum(r["scheduledValue"] for r in rows), 2)}


# ── cash flow ────────────────────────────────────────────────────────────
def build_cash_flow(db: Session, project_id: UUID | str, v: dict) -> dict:
    pid = str(project_id)
    today = site_today()
    share = v["cost_share_pct"] / 100
    ins: list[tuple[date, str, float]] = []
    for s in v["_stage_dates"]:
        amt = s["invoice"]["subtotal"] if s["invoice"] else s["amount"]
        ins.append((s["paid_date"], s["milestone"], round(amt, 2)))

    outs: list[tuple[date, str, float]] = []
    spent: dict[str | None, float] = {}
    for r in db.execute(text("""SELECT category, amount, cost_date FROM project_costs
                                 WHERE project_id = :p"""), {"p": pid}).mappings().all():
        outs.append((_date(r["cost_date"]) or today, r["category"] or "No division", _f(r["amount"])))
        spent[r["category"]] = spent.get(r["category"], 0) + _f(r["amount"])
    budget_by_div: dict[str, float] = {}
    for b in v["_budget"]:
        budget_by_div[b["division"]] = budget_by_div.get(b["division"], 0) + _f(b["amount"])
    phase_costs: dict[str, float] = {d: round(x, 2) for d, x in spent.items() if d}
    for div, bud in budget_by_div.items():
        left = round(bud * share - spent.get(div, 0), 2)
        if left <= 0:
            continue
        days = [d for d in _division_days(v, div) if d >= today] or [today]
        for d, part in zip(days, costs_svc._split(Decimal(str(left)), [1] * len(days))):
            outs.append((d, div, float(part)))
        phase_costs[div] = round(phase_costs.get(div, 0) + left, 2)

    start = _date(v["_sched"]["project_start"]) or min(d for d, *_ in ins + outs)
    first = _monday(min([start] + [d for d, *_ in ins + outs]))
    last = _monday(max(d for d, *_ in ins + outs))
    weeks, pos, n, w = [], 0.0, 1, first
    lo, lo_week = 0.0, None
    while w <= last:
        end = w + timedelta(days=6)
        draws = [{"milestone": m, "amount": a} for d, m, a in ins if w <= d <= end]
        dt = round(sum(x["amount"] for x in draws), 2)
        ot = round(sum(a for d, _, a in outs if w <= d <= end), 2)
        pos = round(pos + dt - ot, 2)
        weeks.append({"week_num": n, "week_start": str(w), "week_end": str(end), "draws": draws,
                      "draws_total": dt, "outflow_total": ot, "net_this_week": round(dt - ot, 2),
                      "cumulative_position": pos})
        if pos < lo:
            lo, lo_week = pos, end
        w, n = w + timedelta(days=7), n + 1
    sc = v["_sc"]
    return {
        "total_pretax": round(sum(a for _, _, a in ins), 2),
        "weekly_periods": weeks,
        "final_cash_position": pos,
        "min_cash_position": lo,
        "min_position_week_end": str(lo_week) if lo_week else None,
        "bridge_financing_required": round(-lo, 2) if lo < 0 else 0.0,
        "phase_costs": phase_costs,
        "milestone_dates": {s["milestone"]: str(s["paid_date"]) for s in v["_stage_dates"]},
        "key_dates": {
            "basis": BASIS, "cost_share_pct": v["cost_share_pct"],
            "schedule_version": v["schedule"]["version_number"],
            "project_start": str(start),
            "substantial_completion_contract": v["contract"]["substantial_completion_date"],
            "substantial_completion_expected": v["schedule"]["expected_substantial_completion"],
            "substantial_completion_actual": v["contract"]["substantial_completion_actual"],
            "holdback_release": str(sc + timedelta(days=HOLDBACK_RELEASE_DAYS)) if sc else None,
        },
    }


# ── save ─────────────────────────────────────────────────────────────────
def save(db: Session, project_id: UUID | str, body: dict, uid: UUID | None) -> dict[str, Any]:
    pid = str(project_id)
    v = view(db, pid, body)
    if v["problems"]:
        raise PlanError(" ".join(v["problems"]))
    stage_acts = {str(s["no"]): s["activity_code"] for s in v["stages"] if s["activity_code"]}
    div_phases = {d["division"]: d["picked"] for d in v["divisions"] if d["picked"]}
    db.execute(text("""
        INSERT INTO billing_plans (project_id, contract_id, stage_activities, division_phases,
                                   cost_share_pct, owner_net_days, created_by_id, updated_by_id)
        VALUES (:p, :c, CAST(:sa AS jsonb), CAST(:dp AS jsonb), :cs, :nd, :u, :u)
        ON CONFLICT (project_id) DO UPDATE SET
            contract_id = EXCLUDED.contract_id, stage_activities = EXCLUDED.stage_activities,
            division_phases = EXCLUDED.division_phases, cost_share_pct = EXCLUDED.cost_share_pct,
            owner_net_days = EXCLUDED.owner_net_days, updated_by_id = EXCLUDED.updated_by_id,
            updated_at = now()"""),
        {"p": pid, "c": v["contract"]["id"], "sa": json.dumps(stage_acts), "dp": json.dumps(div_phases),
         "cs": v["cost_share_pct"], "nd": v["owner_net_days"], "u": uid})
    db.execute(text("UPDATE contracts SET substantial_completion_actual = :d WHERE id = :c"),
               {"d": v["contract"]["substantial_completion_actual"], "c": v["contract"]["id"]})

    sched_id = v["schedule"]["id"]
    sov = build_sov(v)
    prev = db.execute(text("""SELECT id, version_number FROM schedules_of_values
                               WHERE project_id = :p AND scenario = 'base'
                               ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).first()
    sov_ver = (prev.version_number if prev else 0) + 1
    sov_id = db.execute(text("""
        INSERT INTO schedules_of_values (id, project_id, version_number, scenario, based_on_schedule_id,
               total_pretax, months, rows, monthly_totals, cumulative_totals, lien_holdback_10pct,
               owner_net_monthly, notes, parent_version_id, created_by_id)
        VALUES (gen_random_uuid(), :p, :v, 'base', :s, :t, CAST(:mo AS jsonb), CAST(:r AS jsonb),
               CAST(:mt AS jsonb), CAST(:ct AS jsonb), CAST(:lh AS jsonb), CAST(:on AS jsonb), :n, :pv, :u)
        RETURNING id"""),
        {"p": pid, "v": sov_ver, "s": sched_id, "t": sov["total_pretax"], "mo": json.dumps(sov["months"]),
         "r": json.dumps(sov["rows"]), "mt": json.dumps(sov["monthly_totals"]),
         "ct": json.dumps(sov["cumulative_totals"]), "lh": json.dumps(sov["lien_holdback_10pct"]),
         "on": json.dumps(sov["owner_net_monthly"]),
         "n": f"Built from the billing plan: the Money › Costs budget spread over schedule "
              f"v{v['schedule']['version_number']}'s dates.",
         "pv": prev.id if prev else None, "u": uid}).scalar()

    cf = build_cash_flow(db, pid, v)
    prev = db.execute(text("""SELECT id, version_number FROM cash_flows
                               WHERE project_id = :p AND scenario = 'base'
                               ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).first()
    cf_ver = (prev.version_number if prev else 0) + 1
    cf_id = db.execute(text("""
        INSERT INTO cash_flows (id, project_id, version_number, scenario, based_on_schedule_id,
               owner_net_days, lien_holdback_pct, lien_release_days_post_substantial,
               deficiency_days_post_occupancy, total_pretax, final_cash_position, min_cash_position,
               min_position_week_end, bridge_financing_required, weekly_periods, phase_costs,
               milestone_dates, key_dates, notes, parent_version_id, created_by_id)
        VALUES (gen_random_uuid(), :p, :v, 'base', :s, :nd, 0, :hr, 0, :t, :fp, :mp, :mw, :bf,
               CAST(:wp AS jsonb), CAST(:pc AS jsonb), CAST(:md AS jsonb), CAST(:kd AS jsonb), :n, :pv, :u)
        RETURNING id"""),
        {"p": pid, "v": cf_ver, "s": sched_id, "nd": v["owner_net_days"], "hr": HOLDBACK_RELEASE_DAYS,
         "t": cf["total_pretax"], "fp": cf["final_cash_position"], "mp": cf["min_cash_position"],
         "mw": cf["min_position_week_end"], "bf": cf["bridge_financing_required"],
         "wp": json.dumps(cf["weekly_periods"]), "pc": json.dumps(cf["phase_costs"]),
         "md": json.dumps(cf["milestone_dates"]), "kd": json.dumps(cf["key_dates"]),
         "n": (f"Built from the billing plan. Before GST. Money out = bills entered + "
               f"{v['cost_share_pct']:g}% of what's left of each division's budget. The holdback is its "
               f"own contract stage, paid {HOLDBACK_RELEASE_DAYS} days after substantial completion."),
         "pv": prev.id if prev else None, "u": uid}).scalar()

    costs_svc._audit(db, pid, "billing_plans", None, "billing-plan-saved", uid, {
        "stage_activities": stage_acts, "division_phases": div_phases,
        "cost_share_pct": v["cost_share_pct"], "owner_net_days": v["owner_net_days"],
        "substantial_completion_actual": v["contract"]["substantial_completion_actual"],
        "sov": {"id": str(sov_id), "version": sov_ver}, "cash_flow": {"id": str(cf_id), "version": cf_ver}})
    db.commit()
    out = public(view(db, pid))
    out["saved"] = {"sov": {"id": str(sov_id), "version_number": sov_ver},
                    "cash_flow": {"id": str(cf_id), "version_number": cf_ver,
                                  "min_cash_position": cf["min_cash_position"],
                                  "min_position_week_end": cf["min_position_week_end"],
                                  "bridge_financing_required": cf["bridge_financing_required"]}}
    return out
