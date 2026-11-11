"""Daily site report generation, reconciliation, and schedule-impact analysis.

Pre-fills a day's report from the project's active schedule (activities whose
date range overlaps the day) and reconciles an existing report against a shifted
schedule WITHOUT losing entered data. Also analyses recent reports for risks to
the upcoming schedule (deterministic slip detection + AI note-risk) and returns
management warnings.
"""

from __future__ import annotations

import json
from datetime import date as _date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.daily_site_report import DailySiteReport


# ── schedule helpers ────────────────────────────────────────────────


def active_schedule_id(db: Session, project_id: UUID | str) -> UUID | None:
    row = db.execute(
        text("SELECT id FROM schedules WHERE project_id=:p ORDER BY created_at DESC LIMIT 1"),
        {"p": project_id},
    ).first()
    return row[0] if row else None


def _all_activities(db: Session, schedule_id) -> list[dict]:
    rows = db.execute(
        text(
            """SELECT activity_code, name, phase, start_date, finish_date,
                      is_critical, is_milestone, predecessor_codes
                 FROM activities WHERE schedule_id=:s"""
        ),
        {"s": schedule_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _overlapping(activities: list[dict], day: _date) -> list[dict]:
    out = [
        a for a in activities
        if a.get("start_date") and a.get("finish_date")
        and a["start_date"] <= day <= a["finish_date"]
    ]
    out.sort(key=lambda a: (a.get("start_date"), a.get("activity_code") or ""))
    return out


def _pred_codes(activity: dict) -> list[str]:
    out = []
    for pc in (activity.get("predecessor_codes") or []):
        if isinstance(pc, str):
            out.append(pc)
        elif isinstance(pc, dict):
            c = pc.get("code") or pc.get("activity_code")
            if c:
                out.append(str(c))
    return out


# ── report shape ────────────────────────────────────────────────────


def _empty_payload() -> dict:
    return {
        "conditions": {}, "manpower": [], "tasks": [], "deliveries": [],
        "visitors": [], "safety": {}, "delays": [], "notes": "",
    }


def _task_from_activity(a: dict) -> dict:
    return {
        "activity_code": a.get("activity_code"),
        "name": a.get("name"),
        "phase": a.get("phase"),
        "scheduled_start": str(a["start_date"]) if a.get("start_date") else None,
        "scheduled_finish": str(a["finish_date"]) if a.get("finish_date") else None,
        "is_critical": bool(a.get("is_critical")),
        "is_milestone": bool(a.get("is_milestone")),
        "from_schedule": True, "ad_hoc": False, "removed": False,
        "status": "not_started", "percent_complete": 0, "crew": None, "notes": "",
    }


def _has_entry(t: dict) -> bool:
    return (
        (t.get("status") not in (None, "not_started"))
        or bool(t.get("notes"))
        or bool(t.get("percent_complete"))
        or bool(t.get("crew"))
    )


# ── generate / reconcile ────────────────────────────────────────────


def sync_report(
    db: Session, project_id: UUID | str, day: _date, actor_id: UUID | None = None
) -> DailySiteReport:
    """Create the day's report pre-filled from the active schedule, or reconcile
    an existing (non-finalized) one against the current schedule — preserving all
    supervisor-entered data. Finalized reports are returned untouched."""
    sched_id = active_schedule_id(db, project_id)
    scheduled = _overlapping(_all_activities(db, sched_id), day) if sched_id else []

    report = (
        db.query(DailySiteReport)
        .filter_by(project_id=project_id, report_date=day)
        .one_or_none()
    )
    if report is None:
        payload = _empty_payload()
        payload["tasks"] = [_task_from_activity(a) for a in scheduled]
        report = DailySiteReport(
            project_id=project_id, schedule_id=sched_id, report_date=day,
            status="draft", payload=payload, created_by_id=actor_id,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return report

    if report.status == "finalized":
        return report

    payload = dict(report.payload or {})
    for k, v in _empty_payload().items():
        payload.setdefault(k, v)
    existing = payload.get("tasks") or []
    existing_sched = {
        t.get("activity_code"): t for t in existing
        if t.get("activity_code") and t.get("from_schedule") and not t.get("ad_hoc")
    }
    ad_hoc = [t for t in existing if t.get("ad_hoc")]

    merged: list[dict] = []
    seen: set = set()
    for a in scheduled:
        code = a.get("activity_code")
        seen.add(code)
        if code in existing_sched:
            t = dict(existing_sched[code])
            t.update({
                "name": a.get("name"), "phase": a.get("phase"),
                "scheduled_start": str(a["start_date"]) if a.get("start_date") else None,
                "scheduled_finish": str(a["finish_date"]) if a.get("finish_date") else None,
                "is_critical": bool(a.get("is_critical")),
                "is_milestone": bool(a.get("is_milestone")),
                "from_schedule": True, "removed": False,
            })
            merged.append(t)
        else:
            merged.append(_task_from_activity(a))
    # scheduled tasks that dropped off this day: keep+flag if the supervisor
    # entered anything, otherwise drop.
    for code, t in existing_sched.items():
        if code in seen:
            continue
        if _has_entry(t):
            t = dict(t)
            t["removed"] = True
            merged.append(t)
    merged.extend(ad_hoc)

    payload["tasks"] = merged
    report.payload = payload
    report.schedule_id = sched_id
    db.commit()
    db.refresh(report)
    return report


def schedule_drifted(db: Session, report: DailySiteReport) -> bool:
    """True if the project's active schedule is newer than the one this report
    was last synced against — the UI offers a re-sync."""
    current = active_schedule_id(db, report.project_id)
    return current is not None and current != report.schedule_id


# ── schedule-impact analysis (deterministic + AI) ───────────────────


def analyze_schedule_impact(
    db: Session, project_id: UUID | str, *, today: _date | None = None, use_ai: bool = True
) -> dict[str, Any]:
    """Warnings for management: scheduled tasks slipping (deterministic, with
    critical-path propagation) plus AI-extracted risks from recent site notes."""
    today = today or _date.today()
    sched_id = active_schedule_id(db, project_id)
    if not sched_id:
        return {"warnings": [], "generated_for": str(today), "count": 0, "has_schedule": False}

    acts = _all_activities(db, sched_id)
    by_code = {a["activity_code"]: a for a in acts}
    successors: dict[str, list[str]] = {}
    for a in acts:
        for pc in _pred_codes(a):
            successors.setdefault(pc, []).append(a["activity_code"])

    reports = (
        db.query(DailySiteReport)
        .filter(DailySiteReport.project_id == project_id, DailySiteReport.report_date <= today)
        .order_by(DailySiteReport.report_date)
        .all()
    )
    latest: dict[str, dict] = {}
    for r in reports:
        for t in (r.payload or {}).get("tasks", []):
            code = t.get("activity_code")
            if code:
                latest[code] = {
                    "status": t.get("status"), "percent": t.get("percent_complete") or 0,
                    "date": str(r.report_date),
                }

    warnings: list[dict] = []
    for a in acts:
        fd = a.get("finish_date")
        if not fd or a.get("is_milestone") or fd >= today:
            continue
        st = latest.get(a["activity_code"])
        done = bool(st) and (st["status"] == "completed" or (st["percent"] or 0) >= 100)
        if done:
            continue
        impacted = [
            by_code[c]["name"] for c in successors.get(a["activity_code"], [])
            if c in by_code and by_code[c].get("start_date") and by_code[c]["start_date"] >= today
        ]
        reported = (
            f" — last reported {st['status']} ({st['percent']}%) on {st['date']}"
            if st else " — no site report on record"
        )
        warnings.append({
            "source": "slip",
            "severity": "high" if a.get("is_critical") else "medium",
            "critical_path": bool(a.get("is_critical")),
            "activity": a.get("name"),
            "activity_code": a.get("activity_code"),
            "issue": f"Was scheduled to finish {fd} but is not reported complete{reported}.",
            "impacted_upcoming": impacted,
        })

    if use_ai:
        try:
            warnings.extend(_ai_note_risks(db, acts, reports, today))
        except Exception:  # noqa: BLE001 — AI is best-effort; deterministic still returns
            pass

    order = {"high": 0, "medium": 1, "low": 2}
    warnings.sort(key=lambda w: order.get(w.get("severity"), 3))
    return {
        "warnings": warnings, "generated_for": str(today),
        "count": len(warnings), "has_schedule": True,
    }


def _ai_note_risks(db: Session, acts: list[dict], reports: list, today: _date) -> list[dict]:
    recent = [r for r in reports if r.report_date >= today - timedelta(days=10)]
    blob: list[str] = []
    for r in recent:
        p = r.payload or {}
        chunks: list[str] = []
        if p.get("notes"):
            chunks.append("notes: " + str(p["notes"]))
        for d in (p.get("delays") or []):
            vals = "; ".join(str(v) for v in d.values() if v)
            if vals:
                chunks.append("delay: " + vals)
        if (p.get("safety") or {}).get("notes"):
            chunks.append("safety: " + str(p["safety"]["notes"]))
        for t in (p.get("tasks") or []):
            if t.get("notes"):
                chunks.append(f"task '{t.get('name')}': {t['notes']}")
        if chunks:
            blob.append(f"[{r.report_date}] " + " | ".join(chunks))
    if not blob:
        return []

    upcoming = [
        f"{a['name']} ({a.get('start_date')}–{a.get('finish_date')})"
        for a in acts
        if a.get("start_date") and today <= a["start_date"] <= today + timedelta(days=14)
    ]
    from app.services.ai import MODEL_SONNET, call_anthropic

    system = (
        "You are a construction scheduler reviewing on-site daily notes. From the notes below, "
        "identify risks that impact the UPCOMING schedule. Use ONLY facts stated in the notes — "
        "do NOT invent delays, durations, causes, or task names. For each real risk, give the issue, "
        "its likely schedule impact, which upcoming tasks it affects (from the provided list), and a "
        'severity (high | medium | low). Reply with ONLY a JSON array: '
        '[{"issue","impact","affected_tasks","severity","source_date"}]. Empty array if the notes '
        "show no schedule risk."
    )
    content = (
        "RECENT ON-SITE NOTES:\n" + "\n".join(blob)
        + "\n\nUPCOMING TASKS (next 14 days):\n" + ("\n".join(upcoming) or "(none listed)")
    )
    res = call_anthropic(MODEL_SONNET, system, [{"role": "user", "content": content}], max_tokens=1200)
    raw = (res.text or "").strip()
    s, e = raw.find("["), raw.rfind("]")
    out: list[dict] = []
    if s != -1 and e != -1 and e > s:
        try:
            for w in json.loads(raw[s : e + 1]):
                if isinstance(w, dict) and w.get("issue"):
                    sev = str(w.get("severity") or "medium").lower()
                    out.append({
                        "source": "note",
                        "severity": sev if sev in ("high", "medium", "low") else "medium",
                        "critical_path": False,
                        "issue": str(w.get("issue")),
                        "impact": str(w.get("impact") or ""),
                        "impacted_upcoming": w.get("affected_tasks") or [],
                        "source_date": str(w.get("source_date") or ""),
                    })
        except (ValueError, TypeError):
            pass
    return out
