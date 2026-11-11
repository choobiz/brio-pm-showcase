"""Ask — the assistant panel's question answering (UI round 1, phase 9).

Release 1 answers six kinds of question, all READ-ONLY (Amir, 2026-10-13):
what needs my attention, project status, this week's work, what blocks the
next phase, change orders waiting, invoices needing attention.

Three rules, from Amir's brief, and they shape every tool here:

  1. Every answer names the project it is about.
  2. Every answer says when the information was last updated, and links to
     the records behind it. Tools return `sources` (label, href, updated_at);
     the router hands them to the panel whatever the model writes.
  3. ⚠️ MISSING IS NOT "ON TRACK". Prod on 2026-10-13 had 0 daily reports,
     0 inspections, 0 cost entries, and schedule activities with no crew, no
     owner and no % complete. A tool never omits what it cannot know: it lists
     it under `not_recorded`, so the model has to say so instead of reading
     silence as good news.

Tools wrap the same services the pages use, scoped by the same access checks,
so the assistant can only see what the person asking can see.

⚠️ Exclusions live in the SYSTEM PROMPT, not in tool descriptions: a tool
description that mentions a question it does NOT answer attracts that question
(see reference_llm_tool_routing_traps).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, accessible_project_ids, assert_project_access

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
MAX_TURNS = 6
# 1500 cut the "what needs my attention" answer off mid-line in the rollout
# test (2026-10-14); a cut-off answer is also marked as such, never shown as whole.
MAX_TOKENS = 3000
CUT_OFF_NOTE = ("(This answer was cut off at the length limit. Ask about one project, or one topic, "
                "for the rest.)")
#: Prior turns kept for follow-ups ("and the week after?").
MAX_HISTORY = 6


def _today() -> date:
    from app.services.home import today

    return today()


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _src(label: str, href: str, updated_at: Any = None) -> dict[str, Any]:
    return {"label": label, "href": href, "updated_at": _iso(updated_at)}


def _href(item: dict[str, Any]) -> str | None:
    """A register item's link is its first action's."""
    return next((a.get("href") for a in item.get("actions") or [] if a.get("href")), None)


def _project_row(db: Session, pid: str) -> dict[str, Any]:
    return dict(db.execute(_T(
        "SELECT id, name, status, type, address, updated_at FROM projects WHERE id = :p"),
        {"p": pid}).mappings().one())


def _scope(db: Session, user: CurrentUser, project_id: str | None) -> list[str]:
    """One checked project, or every project the user may see."""
    if project_id:
        assert_project_access(db, user, UUID(project_id))
        return [project_id]
    return [str(p) for p in accessible_project_ids(db, user)]


def _schedule(db: Session, pid: str) -> tuple[Any, Any]:
    r = db.execute(_T(
        "SELECT id, created_at FROM schedules WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"),
        {"p": pid}).first()
    return (r[0], r[1]) if r else (None, None)


def _activities(db: Session, sid: Any) -> list[dict[str, Any]]:
    if not sid:
        return []
    return [dict(r) for r in db.execute(_T("""
        SELECT id, activity_code, name, phase, start_date, finish_date, is_critical,
               is_milestone, predecessor_codes
          FROM activities WHERE schedule_id = :s
      ORDER BY start_date NULLS LAST, activity_code"""), {"s": sid}).mappings().all()]


def _act(a: dict[str, Any]) -> dict[str, Any]:
    return {"code": a["activity_code"], "name": a["name"], "phase": a["phase"],
            "start": _iso(a["start_date"]), "finish": _iso(a["finish_date"]),
            "critical": bool(a["is_critical"]), "milestone": bool(a["is_milestone"])}


def _last_report(db: Session, pid: str) -> dict[str, Any] | None:
    r = db.execute(_T(
        "SELECT id, report_date, status, updated_at FROM daily_site_reports "
        "WHERE project_id = :p ORDER BY report_date DESC LIMIT 1"), {"p": pid}).mappings().first()
    return dict(r) if r else None


# ── Tools ────────────────────────────────────────────────────────────────

def find_project(db: Session, user: CurrentUser, query: str) -> dict[str, Any]:
    pids = accessible_project_ids(db, user)
    if not pids:
        return {"matches": []}
    words = [w for w in query.lower().split() if w]
    rows = db.execute(_T(
        "SELECT id, name, status, type, address FROM projects WHERE id = ANY(:p) ORDER BY name"),
        {"p": [str(p) for p in pids]}).mappings().all()
    hits = [r for r in rows
            if all(w in f"{r['name']} {r['address'] or ''}".lower() for w in words)]
    return {"matches": [{"project_id": str(r["id"]), "name": r["name"], "status": r["status"],
                         "type": r["type"], "address": r["address"]} for r in hits[:10]]}


def _project_names(db: Session, pids: list[str], limit: int = 40) -> list[str]:
    if not pids:
        return []
    names = db.execute(_T("SELECT name FROM projects WHERE id::text = ANY(:p) AND status <> 'archived' "
                          "ORDER BY name LIMIT :n"), {"p": [str(x) for x in pids], "n": limit}).scalars().all()
    return list(names)


def needs_attention(db: Session, user: CurrentUser, project_id: str | None = None) -> dict[str, Any]:
    from app.routers.manager import action_queue
    from app.services.register import build_register

    pids = _scope(db, user, project_id)
    today = _today()
    sources: list[dict[str, Any]] = []
    register = build_register(db, [UUID(p) for p in pids],
                              project_id=UUID(project_id) if project_id else None,
                              company_wide=user.role == "superadmin")
    items = [{"severity": i["severity"], "project": i.get("project_name"), "title": i["title"],
              "summary": i.get("summary"), "count": i.get("count"), "href": _href(i)}
             for i in register]
    sources += [_src(f"{i.get('project_name') or 'Projects'} — {i['title']}", _href(i))
                for i in register if _href(i)]
    unread: list[str] = []
    try:
        with db.begin_nested():
            asks = [a for a in action_queue(db=db, user=user)["items"]
                    if not project_id or a["project_id"] == project_id]
    except Exception:  # noqa: BLE001 — say it could not be read rather than fail the answer
        log.exception("assistant: client requests unreadable")
        asks, unread = [], ["client requests (could not be read just now)"]
    for a in asks:
        items.append({"severity": "client", "project": a["project_name"], "title": a["label"],
                      "summary": a["detail"], "since": a.get("at"), "href": a["href"]})

    money = invoices_attention(db, user, project_id)
    cos = change_orders_waiting(db, user, project_id)

    # A weekday with work scheduled and no report, in the last 7 days.
    missing_reports = []
    for pid in pids:
        p = _project_row(db, pid)
        if p["status"] == "archived":
            continue
        sid, _ = _schedule(db, pid)
        acts = _activities(db, sid)
        if not acts:
            continue
        filed = {r[0] for r in db.execute(_T(
            "SELECT report_date FROM daily_site_reports WHERE project_id = :p AND report_date >= :d"),
            {"p": pid, "d": today - timedelta(days=7)}).all()}
        days = [today - timedelta(days=i) for i in range(1, 8)]
        gaps = [d for d in days if d.weekday() < 5 and d not in filed and any(
            a["start_date"] and a["finish_date"] and a["start_date"] <= d <= a["finish_date"]
            for a in acts)]
        if gaps:
            missing_reports.append({"project": p["name"], "days": [d.isoformat() for d in sorted(gaps)],
                                    "href": f"/projects/{pid}/site"})
            sources.append(_src(f"{p['name']} — Site", f"/projects/{pid}/site"))

    upcoming = []
    from app.services.home import this_week

    for e in this_week(db, pids, today):
        upcoming.append(e)

    sources += money["sources"] + cos["sources"]
    sources.append(_src("Home — needs you", "/dashboard", datetime.now(timezone.utc)))
    return {
        "as_of": today.isoformat(),
        "projects_in_scope": len(pids),
        # Which ones, so a quiet answer can still say what it covered. Without
        # it the model wrote "covering 1 project ... the tool didn't return the
        # project's name" (rollout test as a manager, 2026-10-15).
        "project_names": _project_names(db, pids),
        "items": items,
        "change_orders_waiting": cos["waiting"],
        "invoices": money["projects"],
        "daily_reports_missing": missing_reports,
        "next_7_days": upcoming,
        "not_recorded": ["RFIs", "material orders and deliveries", "task owners and due dates",
                         "supplier bills", *unread],
        "sources": sources,
    }


def project_status(db: Session, user: CurrentUser, project_id: str) -> dict[str, Any]:
    from app.services import money as M
    from app.services.home import last_activity, next_milestone, road
    from app.services.register import build_register

    assert_project_access(db, user, UUID(project_id))
    p = _project_row(db, project_id)
    today = _today()
    base = f"/projects/{project_id}"
    sid, sched_at = _schedule(db, project_id)
    acts = _activities(db, sid)
    now = [_act(a) for a in acts if a["start_date"] and a["finish_date"]
           and a["start_date"] <= today <= a["finish_date"]]
    rd = road(db, UUID(project_id))
    issues = [{"severity": i["severity"], "title": i["title"], "summary": i.get("summary")}
              for i in build_register(db, [UUID(project_id)], project_id=UUID(project_id))]
    m = M.summary(db, UUID(project_id))
    rep = _last_report(db, project_id)
    last = last_activity(db, [project_id]).get(project_id)

    not_recorded = ["% complete on activities (progress is inferred from schedule dates only)"]
    if not rep:
        not_recorded.append("daily reports (none filed on this project)")
    if not acts:
        not_recorded.append("schedule")
    sources = [_src(f"{p['name']} — Overview", f"{base}/overview", last)]
    if sid:
        sources.append(_src("Schedule", f"{base}/workbench/schedule", sched_at))
    sources.append(_src("Money", f"{base}/money"))
    if rep:
        sources.append(_src(f"Daily report {rep['report_date']}", f"{base}/daily-reports/{rep['id']}",
                            rep["updated_at"]))
    return {
        "project": {"name": p["name"], "status": p["status"], "type": p["type"], "address": p["address"]},
        "as_of": today.isoformat(),
        "last_activity_at": _iso(last),
        "schedule_updated_at": _iso(sched_at),
        "phase_now": sorted({a["phase"] for a in now}) or None,
        "activities_today": now[:12],
        "next_milestone": next_milestone(db, project_id, today),
        "estimate_road": {"current": rd.get("current"),
                          "steps": [{"label": s["label"], "state": s["state"], "detail": s["detail"]}
                                    for s in rd.get("steps", [])]},
        "issues": issues,
        "money": {"contract_before_gst": (m["contract"] or {}).get("amount"),
                  "approved_change_orders_before_gst": m["change_orders"]["approved_before_gst"],
                  "invoiced": m["invoiced"], "paid": m["paid"], "owing": m["owing"],
                  "next_milestone_to_invoice": m["next_milestone"]},
        "last_daily_report": ({"date": _iso(rep["report_date"]), "status": rep["status"]} if rep else None),
        "not_recorded": not_recorded,
        "sources": sources,
    }


def week_plan(db: Session, user: CurrentUser, project_id: str,
              week_start: str | None = None) -> dict[str, Any]:
    assert_project_access(db, user, UUID(project_id))
    p = _project_row(db, project_id)
    start = date.fromisoformat(week_start) if week_start else _today()
    end = start + timedelta(days=6)
    sid, sched_at = _schedule(db, project_id)
    acts = [a for a in _activities(db, sid) if a["start_date"] and a["finish_date"]
            and a["start_date"] <= end and a["finish_date"] >= start]
    insp = []
    if sid and acts:
        insp = [dict(r) for r in db.execute(_T("""
            SELECT i.inspection_type, i.authority, i.notes, a.name AS activity, a.start_date
              FROM inspections i LEFT JOIN activities a ON a.id = i.activity_id
             WHERE i.schedule_id = :s AND a.start_date BETWEEN :a AND :b"""),
            {"s": sid, "a": start, "b": end}).mappings().all()]
    base = f"/projects/{project_id}"
    return {
        "project": p["name"],
        "week": {"from": start.isoformat(), "to": end.isoformat()},
        "schedule_updated_at": _iso(sched_at),
        "activities": [_act(a) for a in acts],
        "inspections": [{"type": i["inspection_type"], "authority": i["authority"],
                         "activity": i["activity"], "date": _iso(i["start_date"]), "notes": i["notes"]}
                        for i in insp],
        "not_recorded": ["crew assignments", "subcontractors booked",
                         *([] if insp else ["inspections (none recorded on this schedule)"]),
                         *([] if sid else ["schedule (this project has none)"])],
        "sources": [_src("Schedule", f"{base}/workbench/schedule", sched_at),
                    _src(f"{p['name']} — Site", f"{base}/site")],
    }


def blockers(db: Session, user: CurrentUser, project_id: str) -> dict[str, Any]:
    from app.services.interview import build_interview
    from app.services.register import build_register

    assert_project_access(db, user, UUID(project_id))
    p = _project_row(db, project_id)
    today = _today()
    base = f"/projects/{project_id}"
    sid, sched_at = _schedule(db, project_id)
    acts = _activities(db, sid)
    upcoming = [a for a in acts if a["start_date"] and a["start_date"] > today]
    next_phase = upcoming[0]["phase"] if upcoming else None
    by_code = {a["activity_code"]: a for a in acts}
    # The next phase's work that starts soon — not every activity in the phase,
    # which on 1234 dragged November slab prerequisites into a question about
    # footings starting tomorrow.
    window = today + timedelta(days=14)
    soon = [a for a in upcoming if a["phase"] == next_phase and a["start_date"] <= window]
    soon_codes = {a["activity_code"] for a in soon}
    prereq = []
    for a in soon:
        for code in a["predecessor_codes"] or []:
            pr = by_code.get(code)
            if pr and code not in soon_codes and pr["finish_date"] and pr["finish_date"] >= today:
                prereq.append({"needed_for": a["name"], "needed_from": _iso(a["start_date"]),
                               "prerequisite": _act(pr)})
    try:
        iv = build_interview(db, UUID(project_id))
        questions = [{"question": q["question"], "why": q["why"]} for q in iv["blocking"]]
    except ValueError:
        questions = []
    gates = [{"title": i["title"], "summary": i.get("summary")}
             for i in build_register(db, [UUID(project_id)], project_id=UUID(project_id))
             if i["severity"] == "blocking"]
    cos = [c for c in change_orders_waiting(db, user, project_id)["waiting"] if c.get("schedule_days")]
    return {
        "project": p["name"],
        "as_of": today.isoformat(),
        "next_phase": next_phase,
        "next_phase_starts": _iso(upcoming[0]["start_date"]) if upcoming else None,
        "next_phase_work_in_next_14_days": [_act(a) for a in soon][:12],
        "prerequisites_still_scheduled_to_finish": prereq[:15],
        "blocking_items": gates,
        "open_questions_before_next_estimate": questions,
        "change_orders_pending_with_schedule_impact": cos,
        "not_recorded": ["RFIs", "material orders and deliveries",
                         "actual progress (prerequisites are judged by their scheduled finish date)"],
        "sources": [_src("Schedule", f"{base}/workbench/schedule", sched_at),
                    _src(f"{p['name']} — Overview", f"{base}/overview")],
    }


def change_orders_waiting(db: Session, user: CurrentUser, project_id: str | None = None) -> dict[str, Any]:
    pids = _scope(db, user, project_id)
    today = _today()
    from app.services.money import _local

    rows = db.execute(_T("""
        SELECT c.id, c.project_id, p.name AS project, c.number, c.title, c.cost_impact,
               c.schedule_impact_days, c.status, c.submitted_at, c.created_at
          FROM change_orders c JOIN projects p ON p.id = c.project_id
         WHERE c.project_id = ANY(:p) AND c.status IN ('pending', 'draft')
      ORDER BY c.status DESC, c.submitted_at NULLS LAST, c.number"""), {"p": pids}).mappings().all()
    out, sources = [], []
    for r in rows:
        pid = str(r["project_id"])
        waiting = (today - _local(r["submitted_at"])).days if r["submitted_at"] else None
        out.append({"project": r["project"], "number": f"CO-{r['number']}", "title": r["title"],
                    "amount_before_gst": float(r["cost_impact"] or 0),
                    "schedule_days": r["schedule_impact_days"],
                    "state": "waiting on the client" if r["status"] == "pending" else "draft, not sent",
                    "approver": "the client" if r["status"] == "pending" else "office must send it first",
                    "waiting_days": waiting, "submitted_at": _iso(r["submitted_at"])})
        sources.append(_src(f"{r['project']} CO-{r['number']}", f"/projects/{pid}/change-orders",
                            r["submitted_at"] or r["created_at"]))
    return {"as_of": today.isoformat(), "projects_in_scope": len(pids), "waiting": out,
            "sources": sources}


def invoices_attention(db: Session, user: CurrentUser, project_id: str | None = None) -> dict[str, Any]:
    from app.services import money as M

    pids = _scope(db, user, project_id)
    out, sources = [], []
    for pid in pids:
        p = _project_row(db, pid)
        if p["status"] == "archived" and not project_id:
            continue
        s = M.summary(db, UUID(pid))
        invs = s["invoices"]
        overdue = [i for i in invs if i["status"] == "overdue"]
        unpaid = [i for i in invs if i["status"] in ("sent", "partial")]
        drafts = [i for i in invs if i["status"] == "draft"]
        if not (overdue or unpaid or drafts or s["next_milestone"]):
            continue
        brief = lambda i: {"number": i["number"], "for": i["for"], "total": i["total"],  # noqa: E731
                           "paid": i["paid"], "due": i["due"], "sent_at": i["sent_at"]}
        out.append({"project": p["name"], "owing": s["owing"],
                    "overdue": [brief(i) for i in overdue],
                    "sent_unpaid": [brief(i) for i in unpaid],
                    "drafts_not_sent": [brief(i) for i in drafts],
                    "next_milestone_to_invoice": s["next_milestone"]})
        sources.append(_src(f"{p['name']} — Money", f"/projects/{pid}/money",
                            max((i["sent_at"] for i in invs if i["sent_at"]), default=None)))
    return {"as_of": _today().isoformat(), "projects": out,
            "not_recorded": ["supplier bills awaiting approval", "payments owed to suppliers"],
            "sources": sources}


def budget_vs_actual(db: Session, user: CurrentUser, project_id: str) -> dict[str, Any]:
    """Money › Costs (2026-10-30): the budget per division against the bills
    entered. The budget is a share of the contract PRICE (overhead and profit
    included), so "left" is not margin."""
    from app.services import costs as C

    assert_project_access(db, user, UUID(project_id))
    p = _project_row(db, project_id)
    s = C.summary(db, project_id)
    last_bill = max((b["cost_date"] for b in s["bills"] if b["cost_date"]), default=None)
    not_recorded = ["labour (hours are not costed yet)", "bills not yet entered on Money › Costs"]
    if s["budget_total"] is None:
        not_recorded.insert(0, "the budget (not set on Money › Costs)")
    if not s["bills"]:
        not_recorded.insert(0, "any bills (none entered)")
    budget_total = s["budget_total"]
    return {
        "project": p["name"],
        "as_of": _today().isoformat(),
        "last_bill_date": last_bill,
        "contract": s["contract"],
        "budget_total_before_gst": budget_total,
        "spent_before_gst": s["spent_before_gst"],
        "left_before_gst": round(budget_total - s["spent_before_gst"], 2) if budget_total is not None else None,
        "gst_paid": s["gst"],
        "bills_entered": len(s["bills"]),
        "spent_with_no_division": s["unassigned_spent"],
        "by_division": [d for d in s["by_division"]],
        "over_budget": [d["division"] for d in s["by_division"] if d["over"]],
        "note": ("The budget is the contract price spread by division, so it includes overhead and profit: "
                 "a division under budget may still be eating the margin."),
        "not_recorded": not_recorded,
        "sources": [_src(f"{p['name']} — Costs", f"/projects/{project_id}/money?view=costs", last_bill)],
    }


# ── Tool definitions for the model ───────────────────────────────────────

_PID = {"type": "string", "description": "Project id (uuid)."}
_OPT_PID = {"type": "string", "description": "Project id (uuid). Omit for every project the user can see."}

TOOLS: list[dict[str, Any]] = [
    {"name": "find_project",
     "description": "Find projects by name or address words. Returns project ids.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]}},
    {"name": "needs_attention",
     "description": ("What needs the user's attention: blocking items, client requests, change "
                     "orders waiting, invoices, missing daily reports, and the next 7 days of the schedule."),
     "input_schema": {"type": "object", "properties": {"project_id": _OPT_PID}}},
    {"name": "project_status",
     "description": ("A project's current phase, activities under way, next milestone, open issues, "
                     "money position and last daily report."),
     "input_schema": {"type": "object", "properties": {"project_id": _PID}, "required": ["project_id"]}},
    {"name": "week_plan",
     "description": "Schedule activities and inspections for one project over a 7-day window.",
     "input_schema": {"type": "object", "properties": {
         "project_id": _PID,
         "week_start": {"type": "string", "description": "YYYY-MM-DD; defaults to today."}},
         "required": ["project_id"]}},
    {"name": "blockers",
     "description": ("What stands between a project and its next phase: prerequisite activities, "
                     "blocking items, open questions, change orders affecting the schedule."),
     "input_schema": {"type": "object", "properties": {"project_id": _PID}, "required": ["project_id"]}},
    {"name": "change_orders_waiting",
     "description": "Change orders waiting for approval or not yet sent, with amount, days and wait time.",
     "input_schema": {"type": "object", "properties": {"project_id": _OPT_PID}}},
    {"name": "invoices_attention",
     "description": "Client invoices overdue, sent and unpaid, drafts not sent, and the next milestone to bill.",
     "input_schema": {"type": "object", "properties": {"project_id": _OPT_PID}}},    {"name": "budget_vs_actual",
     "description": ("One project's budget per division against the supplier and subtrade bills entered, "
                     "with what's left and which divisions are over."),
     "input_schema": {"type": "object", "properties": {"project_id": _PID}, "required": ["project_id"]}},
]

_IMPL: dict[str, Callable[..., dict[str, Any]]] = {
    "find_project": find_project,
    "needs_attention": needs_attention,
    "project_status": project_status,
    "week_plan": week_plan,
    "blockers": blockers,
    "change_orders_waiting": change_orders_waiting,
    "invoices_attention": invoices_attention,
    "budget_vs_actual": budget_vs_actual,
}


def system_prompt(today: date, project: dict[str, Any] | None) -> str:
    here = (f"The user is looking at project \"{project['name']}\" (id {project['id']}). "
            "\"This project\" or an unnamed project means this one."
            if project else "The user is not on a project page.")
    return f"""You answer questions for BRIO, a construction company, about its projects. Today is {today.isoformat()} (Vancouver).
{here}

Answer ONLY from tool results. Never guess a figure, date or name.

Every answer must:
- Name the project(s) it is about.
- Say how current the information is (the as_of / updated dates in the tool results).
- Link the records it relies on with markdown links using the exact href paths the tools return, e.g. [Money](/projects/…/money).

Missing information: if a tool lists something under not_recorded, or a list is empty because nothing was recorded, SAY it is not recorded. Never treat missing updates as proof that work is on track, on time or complete. "No daily report filed" is a gap to point out, not good news.

You can answer: what needs attention, project status, this week's work, what blocks the next phase, change orders waiting, invoices and payments needing attention, and whether a project is on budget (budget per division against the bills entered; labour is not costed, so say so).
You cannot yet answer, and should say so plainly in one sentence: material orders and deliveries, RFIs, who is responsible for a task, finding documents or drawings, punch lists and handover. Do not call a tool for these.
You cannot change anything. If asked to do something, say which page to do it on and link it.

If the user names a project, use find_project to get its id. If several match, ask which one.
Be brief: lead with the answer, then a short list. For questions across projects, show at most about 10 items, most urgent first; summarise the rest as counts (e.g. "6 draft invoices not sent, on 5 projects") and put company-wide data housekeeping (missing year built, AI results to judge) in one closing line. Money in CAD with $ and commas; say whether a figure is before or with GST when the tool says."""


def _run_tool(db: Session, user: CurrentUser, name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = _IMPL.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        with db.begin_nested():
            return fn(db, user, **args)
    except Exception as e:  # noqa: BLE001 — a failed tool is reported to the model, not raised
        from fastapi import HTTPException

        if isinstance(e, HTTPException):
            return {"error": "not allowed or not found" if e.status_code in (403, 404) else str(e.detail)}
        log.exception("assistant tool %s failed", name)
        return {"error": f"{name} failed"}


def _cut_off(text: str) -> str:
    """A reply that hit the length limit: drop the half-written last line and say so."""
    lines = text.rstrip().split("\n")
    if len(lines) > 1:
        lines = lines[:-1]
    return "\n".join(lines).rstrip() + "\n\n" + CUT_OFF_NOTE


def ask(db: Session, user: CurrentUser, question: str, *, project_id: str | None = None,
        history: list[dict[str, str]] | None = None, client: Any = None,
        approved_usd: float | None = None) -> dict[str, Any]:
    """Answer one question. Returns the answer text, the sources every tool
    call produced (deduplicated, in order), and which tools ran."""
    from app.services.ai import anthropic_client

    project = None
    if project_id:
        assert_project_access(db, user, UUID(project_id))
        project = {"id": project_id, "name": _project_row(db, project_id)["name"]}

    c = client or anthropic_client()
    messages: list[dict[str, Any]] = []
    for turn in (history or [])[-MAX_HISTORY:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:4000]})
    messages.append({"role": "user", "content": question})

    sources: list[dict[str, Any]] = []
    tools_used: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    t0 = time.perf_counter()
    answer = ""
    for _ in range(MAX_TURNS):
        r = c.messages.create(model=MODEL, max_tokens=MAX_TOKENS, tools=TOOLS,
                              system=system_prompt(_today(), project), messages=messages)
        usage["input_tokens"] += r.usage.input_tokens
        usage["output_tokens"] += r.usage.output_tokens
        if approved_usd is not None:
            from app.services.prep_spend import cost as _cost

            if float(_cost(MODEL, usage)) > approved_usd and r.stop_reason == "tool_use":
                answer = (f"I stopped before finishing: this question needed more than the ${approved_usd:.2f} you "
                          "approved. Ask again with a narrower question, or approve a little more.")
                break
        content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b
                   for b in r.content]
        calls = [b for b in content if b.get("type") == "tool_use"]
        if r.stop_reason != "tool_use" or not calls:
            answer = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            if r.stop_reason == "max_tokens":
                answer = _cut_off(answer)
            break
        messages.append({"role": "assistant", "content": content})
        results = []
        for b in calls:
            out = _run_tool(db, user, b["name"], dict(b.get("input") or {}))
            tools_used.append(b["name"])
            for s in out.get("sources", []):
                if s["href"] not in {x["href"] for x in sources}:
                    sources.append(s)
            results.append({"type": "tool_result", "tool_use_id": b["id"],
                            "content": json.dumps(out, default=str)[:60000]})
        messages.append({"role": "user", "content": results})
    else:
        answer = "That took too many steps to work out. Try asking about one project at a time."

    try:
        from app.services import prep_spend

        with db.begin_nested():
            prep_spend.record(db, "ask", MODEL, usage)
        db.commit()
    except Exception:  # noqa: BLE001 — never lose an answer over bookkeeping
        log.exception("assistant: could not record spend")
    log.info("assistant ask user=%s tools=%s tokens=%s ms=%d", user.email, tools_used, usage,
             int((time.perf_counter() - t0) * 1000))
    return {"answer": answer or "I couldn't put an answer together. Try rephrasing.",
            "sources": sources, "tools": tools_used, "usage": usage}
