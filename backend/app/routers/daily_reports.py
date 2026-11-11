"""Daily site reports API — schedule-driven on-site logs + impact warnings.

  GET   /projects/{id}/daily-reports              list (date, status, drift)
  POST  /projects/{id}/daily-reports/sync         generate/reconcile for a date
  GET   /projects/{id}/schedule-impact            management warnings
  GET   /daily-reports/{rid}                       one report (+ drift flag)
  PATCH /daily-reports/{rid}                        save payload / status
  POST  /daily-reports/{rid}/submit                 mark submitted
  GET   /daily-reports/{rid}/pdf                     branded PDF
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.models.daily_site_report import DailySiteReport
from app.services import daily_site_report as svc

log = logging.getLogger(__name__)


router = APIRouter(tags=["daily-reports"])


def _read(report: DailySiteReport, db: Session, *, drift: bool | None = None) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "project_id": str(report.project_id),
        "schedule_id": str(report.schedule_id) if report.schedule_id else None,
        "report_date": report.report_date.isoformat(),
        "status": report.status,
        "payload": report.payload or {},
        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
        "schedule_drifted": svc.schedule_drifted(db, report) if drift is None else drift,
    }


class SyncRequest(BaseModel):
    date: date


class ReportUpdate(BaseModel):
    payload: dict[str, Any] | None = None
    status: str | None = None  # draft | submitted | finalized


@router.get("/projects/{project_id}/daily-reports")
def list_daily_reports(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    # None of this router checked the project until 2026-10-13: any signed-in
    # user, a client included, could read, edit or submit any project's reports.
    assert_project_access(db, user, project_id)
    rows = (
        db.query(DailySiteReport)
        .filter(DailySiteReport.project_id == project_id)
        .order_by(DailySiteReport.report_date.desc())
        .all()
    )
    current_sched = svc.active_schedule_id(db, project_id)
    out = []
    for r in rows:
        tasks = (r.payload or {}).get("tasks", [])
        out.append({
            **_read(r, db, drift=(current_sched is not None and current_sched != r.schedule_id)),
            "task_count": len(tasks),
            "completed_count": sum(1 for t in tasks if t.get("status") == "completed"),
        })
    return out


@router.post("/projects/{project_id}/daily-reports/sync")
def sync_daily_report(
    project_id: UUID,
    body: SyncRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Create the day's report pre-filled from the active schedule, or reconcile
    an existing one against the current schedule (never losing entered data)."""
    assert_project_access(db, user, project_id)
    actor = None
    try:
        actor = UUID(user.id)
    except (ValueError, TypeError, AttributeError):
        actor = None
    report = svc.sync_report(db, project_id, body.date, actor_id=actor)
    return _read(report, db, drift=False)


@router.get("/projects/{project_id}/schedule-impact")
def schedule_impact(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    return svc.analyze_schedule_impact(db, project_id)


@router.get("/daily-reports/{report_id}")
def get_daily_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    r = db.get(DailySiteReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    assert_project_access(db, user, r.project_id)
    return _read(r, db)


def complete_means_done(payload: dict[str, Any]) -> dict[str, Any]:
    """A task saved as completed with no percent is 100% done (rollout test
    2026-10-14 saved "Complete" at 0%). Builds a new dict: JSONB rows are not
    saved when mutated in place."""
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return payload
    fixed = [{**t, "percent_complete": 100}
             if isinstance(t, dict) and t.get("status") == "completed" and not t.get("percent_complete") else t
             for t in tasks]
    return {**payload, "tasks": fixed}


def _photo_data_urls(db: Session, r: DailySiteReport, limit: int = 12) -> list[str]:
    """The report's photos as small JPEG data URLs for the PDF. A photo that
    can't be fetched is left out rather than failing the report."""
    import base64

    from app.models.document import Document
    from app.routers.documents import _make_thumbnail
    from app.services import r2

    out = []
    for pid in ((r.payload or {}).get("photos") or [])[:limit]:
        try:
            doc = db.get(Document, UUID(str(pid)))
            if doc is None or doc.project_id != r.project_id:
                continue
            out.append("data:image/jpeg;base64," + base64.b64encode(_make_thumbnail(r2.get_bytes(doc.r2_key))).decode())
        except Exception:  # noqa: BLE001
            log.warning("daily report %s: photo %s left out of the PDF", r.id, pid)
    return out


@router.patch("/daily-reports/{report_id}")
def update_daily_report(
    report_id: UUID,
    body: ReportUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    r = db.get(DailySiteReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    assert_project_access(db, user, r.project_id)
    if r.status == "finalized":
        raise HTTPException(status.HTTP_409_CONFLICT, "report is finalized")
    if body.payload is not None:
        r.payload = complete_means_done(body.payload)
    if body.status is not None:
        if body.status not in ("draft", "submitted", "finalized"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad status")
        r.status = body.status
        if body.status in ("submitted", "finalized") and r.submitted_at is None:
            r.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)
    return _read(r, db)


@router.post("/daily-reports/{report_id}/submit")
def submit_daily_report(
    report_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    r = db.get(DailySiteReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    assert_project_access(db, user, r.project_id)
    r.status = "submitted"
    r.submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)
    return _read(r, db)


@router.get("/daily-reports/{report_id}/pdf")
def daily_report_pdf(
    report_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    r = db.get(DailySiteReport, report_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    assert_project_access(db, user, r.project_id)
    proj = db.execute(
        __import__("sqlalchemy").text("SELECT name, address FROM projects WHERE id=:p"),
        {"p": r.project_id},
    ).mappings().first()
    from app.services.daily_report_pdf import render_daily_report_pdf

    pdf = render_daily_report_pdf(
        report={"report_date": r.report_date.isoformat(), "status": r.status, "payload": r.payload or {}},
        project={"name": (proj or {}).get("name") or "Project", "address": (proj or {}).get("address") or ""},
        photos=_photo_data_urls(db, r),
    )
    fname = f"Daily-Site-Report-{r.report_date.isoformat()}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}"'},
    )


# ── Site tab: one day, read-only ──────────────────────────────────────────

#: BRIO works in Vancouver. A report filed at 6pm belongs to that day; the
#: server's UTC date would file it as tomorrow from 5pm.
SITE_TZ = "America/Vancouver"


def _site_today() -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(SITE_TZ)).date()


@router.get("/projects/{project_id}/site-day")
def site_day(
    project_id: UUID,
    day: date | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Everything the Site tab shows for one day, without writing anything.

    ⚠️ READ-ONLY on purpose. The existing `/daily-reports/sync` CREATES the
    day's report; calling it just to draw a week strip would leave a draft on
    every day anyone looked at. The report row is created only when someone
    saves one.

    Week strip status per day: `filed` (submitted or finalized), `draft`,
    `missing` (no report although work was scheduled on a weekday, or someone
    clocked in), `pending` (today, not filed yet — the day is not over),
    `quiet` (nothing expected and no report), `future`.

    A schedule activity spanning a weekend does not mean anyone worked Saturday:
    a weekend day counts as missing only if someone actually clocked in.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from sqlalchemy import text as _T

    from app.services.home import next_milestone

    assert_project_access(db, user, project_id)
    today = _site_today()
    on = day or today
    tz = ZoneInfo(SITE_TZ)

    sid = svc.active_schedule_id(db, project_id)
    acts = svc._all_activities(db, sid) if sid else []

    week = [on - timedelta(days=i) for i in range(6, -1, -1)]
    reports = {
        r.report_date: r for r in db.query(DailySiteReport).filter(
            DailySiteReport.project_id == project_id,
            DailySiteReport.report_date >= week[0],
            DailySiteReport.report_date <= week[-1]).all()
    }
    start_utc = datetime.combine(week[0], datetime.min.time(), tz)
    end_utc = datetime.combine(week[-1] + timedelta(days=1), datetime.min.time(), tz)
    punch_days = {
        r[0].astimezone(tz).date() for r in db.execute(_T(
            "SELECT punch_in_at FROM time_entries WHERE project_id = :p "
            "AND punch_in_at >= :a AND punch_in_at < :b"),
            {"p": str(project_id), "a": start_utc, "b": end_utc}).all()
    }
    strip = []
    for d in week:
        r = reports.get(d)
        if d > today:
            st = "future"
        elif r is not None and r.status in ("submitted", "finalized"):
            st = "filed"
        elif r is not None:
            st = "draft"
        elif d == today:
            st = "pending"
        elif d in punch_days or (d.weekday() < 5 and svc._overlapping(acts, d)):
            st = "missing"
        else:
            st = "quiet"
        strip.append({"date": d.isoformat(), "status": st})

    scheduled = [
        {"activity_code": a.get("activity_code"), "name": a.get("name"),
         "is_critical": bool(a.get("is_critical")), "is_milestone": bool(a.get("is_milestone")),
         "start_date": str(a["start_date"]), "finish_date": str(a["finish_date"])}
        for a in svc._overlapping(acts, on)
    ]

    day_start = datetime.combine(on, datetime.min.time(), tz)
    day_end = day_start + timedelta(days=1)
    from app.models.time_entry import TimeEntry
    from app.routers.time_tracking import _read as _read_entry

    # The stored site coordinates only. `ensure_project_location` would geocode
    # and WRITE them, and this endpoint does not write.
    loc = db.execute(_T("SELECT latitude, longitude FROM projects WHERE id = :p"),
                     {"p": str(project_id)}).first()
    site = (float(loc[0]), float(loc[1])) if loc and loc[0] is not None and loc[1] is not None else None
    hours = []
    for e in db.query(TimeEntry).filter(
            TimeEntry.project_id == project_id,
            TimeEntry.punch_in_at >= day_start, TimeEntry.punch_in_at < day_end
    ).order_by(TimeEntry.punch_in_at).all():
        name = db.execute(_T("SELECT coalesce(name, email) FROM users WHERE id = :u"),
                          {"u": str(e.user_id)}).scalar()
        hours.append(_read_entry(e, user_label=name, site=site))

    photos = [
        {"id": str(r["id"]), "filename": r["filename"]}
        for r in db.execute(_T("""
            SELECT id, filename FROM documents
             WHERE project_id = :p AND category = 'photo' AND archived = false
               AND created_at >= :a AND created_at < :b
             ORDER BY created_at"""),
            {"p": str(project_id), "a": day_start, "b": day_end}).mappings().all()
    ]

    report = reports.get(on)
    return {
        "date": on.isoformat(),
        "today": today.isoformat(),
        "week": strip,
        "has_schedule": sid is not None,
        "scheduled": scheduled,
        "report": _read(report, db) if report else None,
        "next_milestone": next_milestone(db, str(project_id), on),
        "hours": hours,
        "offsite_count": sum(1 for h in hours if h.get("location_flag")),
        "photos": photos,
    }
