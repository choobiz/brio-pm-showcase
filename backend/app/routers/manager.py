"""Manager dashboard — portfolio health, schedule-risk alerts, and a daily-report
feed, scoped to the caller's assigned projects (superadmin = all).

All from existing data; the schedule-risk uses the deterministic (no-AI) path so
the dashboard is cheap to load.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office
from app.db import get_db


router = APIRouter(prefix="/manager", tags=["manager"])


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _scoped_ids(db: Session, user: CurrentUser) -> list[str]:
    if user.role == "superadmin":
        return [str(r) for r in db.execute(
            text("SELECT id FROM projects WHERE status <> 'archived' ORDER BY created_at DESC")
        ).scalars().all()]
    return [str(r) for r in db.execute(
        text("SELECT project_id FROM project_members WHERE user_id = :u"), {"u": user.id},
    ).scalars().all()]


def _contract_and_paid(db: Session, pid: str) -> tuple[int, int]:
    erow = db.execute(
        text("SELECT payload FROM estimate_versions WHERE project_id = :p AND is_active = true"),
        {"p": pid},
    ).mappings().first()
    total = 0
    if erow:
        est = erow["payload"] if isinstance(erow["payload"], dict) else json.loads(erow["payload"])
        oh = float(est.get("overhead") or 0)
        pr = float(est.get("profit") or 0)
        base = sum(float(c.get("total") or 0) for c in est.get("categories", []) if not c.get("optional"))
        total = round(round(base * (1 + oh / 100) * (1 + pr / 100)) * 1.05)
    paid = float(db.execute(
        text("SELECT COALESCE(SUM(paid_amount), 0) FROM invoices WHERE project_id = :p"), {"p": pid},
    ).scalar() or 0)
    return total, round(paid)


def _hours(db: Session, pid: str) -> dict[str, Any]:
    row = db.execute(text(
        "SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (punch_out_at - punch_in_at)) / 3600) "
        "  FILTER (WHERE status = 'closed'), 0) AS hrs, "
        "COUNT(*) FILTER (WHERE status = 'open') AS open_cnt FROM time_entries WHERE project_id = :p"
    ), {"p": pid}).mappings().one()
    hrs = float(row["hrs"] or 0)
    offsite = 0
    proj = db.execute(text("SELECT latitude, longitude FROM projects WHERE id = :p"), {"p": pid}).mappings().one()
    if proj["latitude"] is not None and proj["longitude"] is not None:
        from app.services.geocode import haversine_m
        slat, slng = float(proj["latitude"]), float(proj["longitude"])
        pts = db.execute(text(
            "SELECT punch_in_lat AS la, punch_in_lng AS lo FROM time_entries "
            "  WHERE project_id = :p AND punch_in_lat IS NOT NULL "
            "UNION ALL SELECT punch_out_lat, punch_out_lng FROM time_entries "
            "  WHERE project_id = :p AND punch_out_lat IS NOT NULL"
        ), {"p": pid}).mappings().all()
        offsite = sum(1 for pt in pts if haversine_m(slat, slng, float(pt["la"]), float(pt["lo"])) > 500)
    total_min = round(hrs * 60)
    return {"total_hm": f"{total_min // 60}:{total_min % 60:02d}", "open_punches": int(row["open_cnt"]),
            "offsite_count": offsite}


def _schedule(db: Session, pid: str, today: date) -> dict[str, Any] | None:
    base = db.execute(text(
        "SELECT p50_finish_date FROM schedules WHERE project_id = :p ORDER BY version_number ASC LIMIT 1"
    ), {"p": pid}).scalar()
    latest = db.execute(text(
        "SELECT p50_finish_date FROM schedules WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"
    ), {"p": pid}).scalar()
    if not latest:
        return None
    delta = (latest - base).days if (latest and base) else 0
    light = "green" if delta <= 3 else ("amber" if delta <= 14 else "red")
    return {"target_finish": str(latest), "baseline_finish": str(base) if base else None,
            "days_delta": delta, "light": light}


def _reports(db: Session, pid: str, today: date) -> dict[str, Any]:
    last = db.execute(text("SELECT MAX(report_date) FROM daily_site_reports WHERE project_id = :p"), {"p": pid}).scalar()
    return {"last_report_date": str(last) if last else None, "missing": (last is None) or (last < today)}


@router.get("/portfolio")
def portfolio(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    from app.services.daily_site_report import analyze_schedule_impact
    from app.services.package_fingerprint import compute_package_fingerprint, diff_fingerprints
    today = _today()
    out = []
    for pid in _scoped_ids(db, user):
        proj = db.execute(text("SELECT name, address, status FROM projects WHERE id = :p"), {"p": pid}).mappings().one()
        total, paid = _contract_and_paid(db, pid)
        # package staleness
        prow = db.execute(text("SELECT package_baseline FROM projects WHERE id = :p"), {"p": pid}).mappings().first()
        baseline = prow["package_baseline"] if prow else None
        if baseline:
            cur = compute_package_fingerprint(db, pid)
            pkg_stale = len(diff_fingerprints(baseline.get("sections") or {}, cur["sections"])) > 0
        else:
            pkg_stale = False
        risk = analyze_schedule_impact(db, pid, use_ai=False)
        pending_cos = int(db.execute(
            text("SELECT COUNT(*) FROM change_orders WHERE project_id = :p AND status = 'pending'"),
            {"p": pid},
        ).scalar() or 0)
        out.append({
            "project_id": pid, "name": proj["name"], "address": proj["address"], "status": proj["status"],
            "schedule": _schedule(db, pid, today),
            "hours": _hours(db, pid),
            "reports": _reports(db, pid, today),
            "package_stale": pkg_stale, "package_sent": baseline is not None,
            "contract_total": total, "paid_to_date": paid,
            "risk_count": risk.get("count", 0),
            "pending_change_orders": pending_cos,
        })
    return {"projects": out, "generated_for": str(today)}


@router.get("/alerts")
def alerts(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    from app.services.daily_site_report import analyze_schedule_impact
    out = []
    for pid in _scoped_ids(db, user):
        name = db.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": pid}).scalar()
        r = analyze_schedule_impact(db, pid, use_ai=False)
        for w in r.get("warnings", []):
            out.append({**w, "project_id": pid, "project_name": name})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda w: order.get(w.get("severity"), 3))
    return {"alerts": out, "count": len(out)}


@router.get("/action-queue")
def action_queue(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Cross-project queue of items awaiting office/manager action: client
    selection responses (confirm), unread client messages (reply), and change
    orders the client has decided or that are still unsent. Newest first."""
    items: list[dict] = []
    for pid in _scoped_ids(db, user):
        name = db.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": pid}).scalar()

        # Client selections the client has responded to → office confirms.
        for s in db.execute(text(
            "SELECT id, title, decision_type, options, selected_option_id, decided_at "
            "FROM client_selections WHERE project_id = :p AND status = 'submitted'"
        ), {"p": pid}).mappings().all():
            if s["decision_type"] == "approve":
                choice = "Approved" if s["selected_option_id"] == "approved" else "Requested changes"
            else:
                opt = next((o for o in (s["options"] or []) if o.get("id") == s["selected_option_id"]), None)
                choice = (opt or {}).get("label", "—")
            items.append({
                "type": "selection", "project_id": pid, "project_name": name,
                "label": s["title"], "detail": f"Client chose: {choice} — confirm",
                "at": str(s["decided_at"]) if s["decided_at"] else None,
                "href": f"/projects/{pid}/selections",
            })

        # Unread client messages → office replies.
        m = db.execute(text(
            "SELECT COUNT(*) AS c, MAX(created_at) AS last FROM project_messages "
            "WHERE project_id = :p AND sender_role = 'owner' AND read_at IS NULL"
        ), {"p": pid}).mappings().one()
        if int(m["c"] or 0) > 0:
            items.append({
                "type": "message", "project_id": pid, "project_name": name,
                "label": f"{m['c']} unread message{'s' if m['c'] > 1 else ''}",
                "detail": "Reply to the client",
                "at": str(m["last"]) if m["last"] else None,
                "href": f"/projects/{pid}/messages",
            })

        # Change orders the client decided (apply/revise) or that are still unsent.
        for co in db.execute(text(
            "SELECT id, number, title, status, decided_at, created_at FROM change_orders "
            "WHERE project_id = :p AND status IN ('draft', 'approved', 'rejected')"
        ), {"p": pid}).mappings().all():
            detail = {"draft": "Send to client", "approved": "Client approved — apply",
                      "rejected": "Client declined — revise"}[co["status"]]
            items.append({
                "type": "change_order", "project_id": pid, "project_name": name,
                "label": f"CO-{co['number']}: {co['title']}", "detail": detail,
                "at": str(co["decided_at"] or co["created_at"]) if (co["decided_at"] or co["created_at"]) else None,
                "href": f"/projects/{pid}/change-orders",
            })

    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return {"items": items, "count": len(items)}


@router.get("/comms-log")
def comms_log(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Per-project client-communication overview: last contact, who spoke last,
    unread client messages, and when the office last reached out. Surfaces
    clients who've gone quiet or are awaiting a reply. Attention-first."""
    out: list[dict] = []
    for pid in _scoped_ids(db, user):
        name = db.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": pid}).scalar()
        agg = db.execute(text(
            "SELECT COUNT(*) AS total, "
            "  MAX(created_at) AS last_at, "
            "  COUNT(*) FILTER (WHERE sender_role = 'owner' AND read_at IS NULL) AS unread, "
            "  MAX(created_at) FILTER (WHERE sender_role = 'office') AS last_office_at "
            "FROM project_messages WHERE project_id = :p"
        ), {"p": pid}).mappings().one()
        last_sender = None
        if agg["last_at"]:
            last_sender = db.execute(text(
                "SELECT sender_role FROM project_messages WHERE project_id = :p "
                "ORDER BY created_at DESC LIMIT 1"
            ), {"p": pid}).scalar()
        unread = int(agg["unread"] or 0)
        awaiting_reply = last_sender == "owner" and unread > 0
        out.append({
            "project_id": pid, "project_name": name,
            "total_messages": int(agg["total"] or 0),
            "last_message_at": str(agg["last_at"]) if agg["last_at"] else None,
            "last_sender": last_sender,
            "last_office_at": str(agg["last_office_at"]) if agg["last_office_at"] else None,
            "unread_from_client": unread,
            "awaiting_reply": awaiting_reply,
        })
    # Attention order: awaiting reply → never contacted → quietest (oldest last contact).
    def _key(r: dict):
        return (0 if r["awaiting_reply"] else 1,
                0 if r["last_message_at"] is None else 1,
                r["last_message_at"] or "")
    out.sort(key=_key)
    return {"projects": out}


@router.get("/report-feed")
def report_feed(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    ids = _scoped_ids(db, user)
    if not ids:
        return {"reports": []}
    rows = db.execute(text(
        "SELECT dsr.id, dsr.project_id, p.name AS project_name, dsr.report_date, dsr.status, dsr.submitted_at "
        "FROM daily_site_reports dsr JOIN projects p ON p.id = dsr.project_id "
        "WHERE dsr.project_id = ANY(:ids) ORDER BY dsr.report_date DESC, dsr.created_at DESC LIMIT :lim"
    ), {"ids": ids, "lim": limit}).mappings().all()
    return {"reports": [{
        "id": str(r["id"]), "project_id": str(r["project_id"]), "project_name": r["project_name"],
        "report_date": str(r["report_date"]), "status": r["status"],
        "submitted": r["submitted_at"] is not None,
    } for r in rows]}
