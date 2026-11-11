"""Actual progress per schedule activity: site reports first, typed-in facts on top.

Two sources, merged per field:
  1. Daily site reports (drafts included, flagged; Amir 2026-10-29). Derived on
     every read so they are never stale.
  2. activity_actuals rows (typed in on "Record what happened"). A field set
     there wins over the derived value for that field.

Reading a report's task:
  started  — status in_progress or completed, or percent > 0
  done     — status completed, or percent >= 100
  "delayed" / "not_worked" at 0% is NOT a start: the 1234 footings were saved
  "delayed, 0%, will be poured tomorrow" on 20 Oct.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.activity_actual import ACTUAL_STATUSES, ActivityActual
from app.models.daily_site_report import DailySiteReport

_FIELDS = ("status", "actual_start", "actual_finish", "percent_complete", "reason")
SITE_TZ = ZoneInfo("America/Vancouver")


def site_today() -> date:
    return datetime.now(SITE_TZ).date()


def derive_from_reports(reports: Iterable[tuple[date, bool, list[dict]]]) -> dict[str, dict[str, Any]]:
    """Per activity code, what the site reports say.

    `reports` is (report_date, is_draft, tasks), any order. Returns
    {code: {status, actual_start, actual_finish, percent_complete, reason,
    last_reported, from_draft}}."""
    out: dict[str, dict[str, Any]] = {}
    for day, is_draft, tasks in sorted(reports, key=lambda r: r[0]):
        for t in tasks or []:
            if not isinstance(t, dict) or t.get("removed"):
                continue
            code = t.get("activity_code")
            if not code:
                continue
            st = t.get("status")
            pct = int(t.get("percent_complete") or 0)
            done = st == "completed" or pct >= 100
            started = done or st == "in_progress" or pct > 0
            a = out.setdefault(code, {
                "status": "not_started", "actual_start": None, "actual_finish": None,
                "percent_complete": 0, "reason": None, "last_reported": None, "from_draft": False,
            })
            if started and a["actual_start"] is None:
                a["actual_start"] = day
                a["from_draft"] = a["from_draft"] or is_draft
            if done and a["actual_finish"] is None:
                a["actual_finish"] = day
                a["from_draft"] = a["from_draft"] or is_draft
            a["percent_complete"] = 100 if done else pct
            a["status"] = "done" if a["actual_finish"] else ("started" if a["actual_start"] else "not_started")
            note = (t.get("notes") or "").strip()
            a["reason"] = note if (st in ("delayed", "not_worked") and note) else (None if started else a["reason"])
            a["last_reported"] = day
    return out


def project_actuals(db: Session, project_id: UUID | str) -> dict[str, dict[str, Any]]:
    """Merged actuals for every activity code with any fact on record.

    Each entry carries `source`: "site_report", "typed" or "both", so the
    screen can say where a date came from."""
    reports = (
        db.query(DailySiteReport)
        .filter(DailySiteReport.project_id == project_id)
        .all()
    )
    derived = derive_from_reports(
        (r.report_date, r.status == "draft", (r.payload or {}).get("tasks") or []) for r in reports
    )
    typed = {
        a.activity_code: a
        for a in db.query(ActivityActual).filter(ActivityActual.project_id == project_id).all()
    }
    out: dict[str, dict[str, Any]] = {}
    for code in set(derived) | set(typed):
        d = derived.get(code)
        row = dict(d) if d else {
            "status": None, "actual_start": None, "actual_finish": None,
            "percent_complete": None, "reason": None, "last_reported": None, "from_draft": False,
        }
        t = typed.get(code)
        if t is not None:
            for f in _FIELDS:
                v = getattr(t, f)
                if v is not None:
                    row[f] = v
        row["source"] = "both" if (d and t) else ("typed" if t else "site_report")
        row["activity_code"] = code
        out[code] = row
    return out


def record(db: Session, project_id: UUID | str, code: str, fields: dict[str, Any],
           user_id: UUID | None) -> ActivityActual:
    """Upsert the typed-in facts for one activity. Only supplied keys change;
    an explicit None clears that field back to whatever the reports say."""
    if "status" in fields and fields["status"] is not None and fields["status"] not in ACTUAL_STATUSES:
        raise ValueError(f"status must be one of {ACTUAL_STATUSES}")
    today = site_today()
    for k in ("actual_start", "actual_finish"):
        if fields.get(k) and fields[k] > today:
            raise ValueError(f"{k.replace('_', ' ')} {fields[k]} is in the future")
    # Compare against what is already on record too: a finish typed today
    # must not land before a start a site report gave.
    current = project_actuals(db, project_id).get(code, {})
    s = fields["actual_start"] if "actual_start" in fields else current.get("actual_start")
    f = fields["actual_finish"] if "actual_finish" in fields else current.get("actual_finish")
    if s and f and f < s:
        raise ValueError(f"actual finish {f} is before actual start {s}")
    row = (
        db.query(ActivityActual)
        .filter(ActivityActual.project_id == project_id, ActivityActual.activity_code == code)
        .one_or_none()
    )
    if row is None:
        row = ActivityActual(project_id=project_id, activity_code=code)
        db.add(row)
    for k in _FIELDS:
        if k in fields:
            setattr(row, k, fields[k])
    row.recorded_by_id = user_id
    db.flush()
    return row
