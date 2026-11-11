"""Employee time tracking — punch in / punch out with GPS.

  GET  /me/active-punch                       the caller's open entry (or null)
  POST /projects/{id}/punch-in                start a shift (one open at a time)
  POST /me/punch-out                          close the caller's open shift
  GET  /projects/{id}/time-entries            office view — all entries + hours
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.models.time_entry import TimeEntry
from app.services.geocode import geocode, haversine_m


router = APIRouter(tags=["time-tracking"])

# A punch recorded farther than this from the geocoded job site is flagged for
# office review. Generous to absorb ordinary GPS drift + geocode imprecision.
OFFSITE_RADIUS_M = 500.0


class PunchBody(BaseModel):
    lat: float | None = None
    lng: float | None = None
    accuracy: float | None = None
    notes: str | None = None


def _hours(e: TimeEntry) -> float | None:
    if e.punch_out_at is None:
        return None
    return round((e.punch_out_at - e.punch_in_at).total_seconds() / 3600, 4)


def _hm(hours: float | None) -> str | None:
    if hours is None:
        return None
    total_min = round(hours * 60)
    return f"{total_min // 60}:{total_min % 60:02d}"


def _dist_m(site: tuple[float, float] | None, lat, lng) -> float | None:
    if site is None or lat is None or lng is None:
        return None
    return round(haversine_m(site[0], site[1], float(lat), float(lng)), 0)


def _read(
    e: TimeEntry,
    *,
    project_name: str | None = None,
    user_label: str | None = None,
    site: tuple[float, float] | None = None,
) -> dict[str, Any]:
    h = _hours(e)
    d_in = _dist_m(site, e.punch_in_lat, e.punch_in_lng)
    d_out = _dist_m(site, e.punch_out_lat, e.punch_out_lng)
    # Flag only on a distance we could actually compute (need site coords + a
    # GPS fix). Missing GPS is "unknown", not "off-site".
    flagged = any(d is not None and d > OFFSITE_RADIUS_M for d in (d_in, d_out))
    return {
        "id": str(e.id),
        "user_id": str(e.user_id),
        "user": user_label,
        "project_id": str(e.project_id),
        "project_name": project_name,
        "status": e.status,
        "punch_in_at": e.punch_in_at.isoformat() if e.punch_in_at else None,
        "punch_in_loc": _loc(e.punch_in_lat, e.punch_in_lng, e.punch_in_accuracy),
        "punch_out_at": e.punch_out_at.isoformat() if e.punch_out_at else None,
        "punch_out_loc": _loc(e.punch_out_lat, e.punch_out_lng, e.punch_out_accuracy),
        "distance_in_m": d_in,
        "distance_out_m": d_out,
        "location_flag": flagged,
        "hours": h,
        "hours_hm": _hm(h),
        "notes": e.notes,
        "source": e.source,
    }


def _loc(lat, lng, acc) -> dict[str, Any] | None:
    if lat is None or lng is None:
        return None
    return {"lat": float(lat), "lng": float(lng), "accuracy": float(acc) if acc is not None else None}


def _actor(user: CurrentUser) -> UUID:
    return UUID(user.id)


def _open_entry(db: Session, user_id: UUID) -> TimeEntry | None:
    return db.scalar(
        select(TimeEntry).where(TimeEntry.user_id == user_id, TimeEntry.status == "open")
    )


def _project_name(db: Session, project_id) -> str | None:
    return db.execute(text("SELECT name FROM projects WHERE id=:p"), {"p": project_id}).scalar()


def ensure_project_location(db: Session, project_id) -> tuple[float, float] | None:
    """Return the project's (lat, lng), geocoding from its address on first use.

    Cached on the row. `geocoded_at` marks that we've tried, so an address that
    won't resolve doesn't hit Nominatim on every Hours load.
    """
    row = db.execute(
        text("SELECT address, latitude, longitude, geocoded_at FROM projects WHERE id=:p"),
        {"p": project_id},
    ).mappings().first()
    if row is None:
        return None
    if row["latitude"] is not None and row["longitude"] is not None:
        return float(row["latitude"]), float(row["longitude"])
    if row["geocoded_at"] is not None:
        return None  # already attempted, unresolved
    coords = geocode(row["address"]) if row["address"] else None
    lat, lng = (coords if coords else (None, None))
    db.execute(
        text(
            "UPDATE projects SET latitude=:lat, longitude=:lng, geocoded_at=now() WHERE id=:p"
        ),
        {"lat": lat, "lng": lng, "p": project_id},
    )
    db.commit()
    return (lat, lng) if coords else None


@router.get("/me/active-punch")
def active_punch(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    e = _open_entry(db, _actor(user))
    if e is None:
        return {"active": None}
    return {"active": _read(e, project_name=_project_name(db, e.project_id))}


@router.post("/projects/{project_id}/punch-in")
def punch_in(
    project_id: UUID,
    body: PunchBody,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    # A worker clocks in to a project they are on. Until 2026-10-13 any
    # signed-in user could clock in to any project.
    assert_project_access(db, user, project_id)
    uid = _actor(user)
    open_e = _open_entry(db, uid)
    if open_e is not None:
        # Already clocked in — one shift at a time. Surface the active one.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "already_punched_in",
                "active": _read(open_e, project_name=_project_name(db, open_e.project_id)),
            },
        )
    e = TimeEntry(
        user_id=uid, project_id=project_id, punch_in_at=datetime.now(timezone.utc),
        punch_in_lat=body.lat, punch_in_lng=body.lng, punch_in_accuracy=body.accuracy,
        notes=body.notes, status="open", source="field",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    site = ensure_project_location(db, e.project_id)
    return _read(e, project_name=_project_name(db, e.project_id), site=site)


@router.post("/me/punch-out")
def punch_out(
    body: PunchBody,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    e = _open_entry(db, _actor(user))
    if e is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not punched in")
    e.punch_out_at = datetime.now(timezone.utc)
    e.punch_out_lat = body.lat
    e.punch_out_lng = body.lng
    e.punch_out_accuracy = body.accuracy
    if body.notes:
        e.notes = ((e.notes + "\n") if e.notes else "") + body.notes
    e.status = "closed"
    db.commit()
    db.refresh(e)
    site = ensure_project_location(db, e.project_id)
    return _read(e, project_name=_project_name(db, e.project_id), site=site)


@router.get("/projects/{project_id}/time-entries")
def list_time_entries(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    site = ensure_project_location(db, project_id)
    rows = db.execute(
        text(
            """SELECT te.*, COALESCE(u.name, u.email) AS user_label
                 FROM time_entries te JOIN users u ON u.id = te.user_id
                WHERE te.project_id = :p
                ORDER BY te.punch_in_at DESC"""
        ),
        {"p": project_id},
    ).mappings().all()
    entries = []
    total = 0.0
    for r in rows:
        e = db.get(TimeEntry, r["id"])
        item = _read(e, user_label=r["user_label"], site=site)
        if item["hours"]:
            total += item["hours"]
        entries.append(item)
    return {
        "entries": entries,
        "total_hours": round(total, 2),
        "total_hm": _hm(total),
        "site": {"lat": site[0], "lng": site[1]} if site else None,
        "offsite_radius_m": OFFSITE_RADIUS_M,
    }
