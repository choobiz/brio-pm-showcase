"""Owner/office notification emails (best-effort — never break the request).

Reuses the SES SMTP sender. Automatic on change-order submit/decision; the
office can also send a curated "new update" (photos / milestone) to the client.
"""

from __future__ import annotations
from app.company import WEBSITE

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.email import send_email

log = logging.getLogger(__name__)

PORTAL_URL = "https://brio-pm.example.com/portal"


def _enabled() -> bool:
    return bool(settings.aws_ses_from_email and settings.aws_ses_access_key_id)


def _owner_emails(db: Session, project_id) -> list[str]:
    return [r for r in db.execute(text(
        "SELECT u.email FROM users u JOIN project_members pm ON pm.user_id = u.id "
        "WHERE pm.project_id = :p AND u.role = 'owner' AND u.active = true AND u.email IS NOT NULL"
    ), {"p": project_id}).scalars().all()]


def _office_emails(db: Session, project_id, created_by) -> list[str]:
    """Office recipients for a project notification: managers assigned to the
    project + all superadmins. Excludes owner-role (client) accounts and the
    person who triggered the event (don't notify someone of their own action)."""
    return [r for r in db.execute(text(
        "SELECT DISTINCT email FROM users WHERE active = true AND email IS NOT NULL "
        "AND role IN ('manager', 'superadmin') "
        "AND (role = 'superadmin' "
        "     OR id IN (SELECT user_id FROM project_members WHERE project_id = :p)) "
        "AND (CAST(:c AS uuid) IS NULL OR id <> CAST(:c AS uuid))"
    ), {"c": created_by, "p": project_id}).scalars().all()]


def _project_name(db: Session, project_id) -> str:
    return db.execute(text("SELECT name FROM projects WHERE id = :p"), {"p": project_id}).scalar() or "your project"


def _send(to: list[str], subject: str, text_body: str) -> None:
    if not _enabled() or not to:
        return
    html = (
        f"<div style='font-family:Helvetica,Arial,sans-serif;color:#26303a;line-height:1.55'>"
        f"<p>{text_body.replace(chr(10), '<br>')}</p>"
        f"<p style='margin-top:18px'><a href='{PORTAL_URL}' "
        f"style='background:#0068A0;color:#fff;padding:10px 18px;text-decoration:none;border-radius:4px'>"
        f"Open your portal</a></p>"
        f"<p style='color:#8a94a0;font-size:12px;margin-top:20px'>Built By Brio &middot; {WEBSITE}</p></div>"
    )
    for addr in to:
        try:
            send_email(addr, subject, text_body + f"\n\nOpen your portal: {PORTAL_URL}", html)
        except Exception:  # noqa: BLE001 — notifications are best-effort
            log.exception("notify send failed to %s", addr)


def notify_owner_change_order(db: Session, co) -> None:
    name = _project_name(db, co.project_id)
    amt = f"${round(float(co.cost_impact) * 1.05):,.0f}"
    body = (f"There's a change order to review on {name}.\n\n"
            f"{('CO-%d' % co.number)}: {co.title}\nCost impact: {amt} (incl. GST)"
            + (f"\nSchedule impact: {co.schedule_impact_days:+d} days" if co.schedule_impact_days else "")
            + "\n\nPlease review and approve or decline it in your portal (Payments).")
    _send(_owner_emails(db, co.project_id), f"Change order to review — {name}", body)


def notify_office_co_decision(db: Session, co) -> None:
    name = _project_name(db, co.project_id)
    verb = "approved" if co.status == "approved" else "declined"
    body = (f"The client {verb} CO-{co.number} ({co.title}) on {name}."
            + (f"\nTheir note: {co.owner_note}" if co.owner_note else ""))
    _send(_office_emails(db, co.project_id, co.decided_by_id), f"Change order {verb} — {name}", body)


def notify_owner_selection(db: Session, project_id, *, title: str, due_date=None) -> None:
    """Office → owner: a new material selection / approval needs a decision."""
    name = _project_name(db, project_id)
    body = (f"We need a decision from you on {name}.\n\n{title}"
            + (f"\nNeeded by: {due_date}" if due_date else "")
            + "\n\nPlease make your selection in your portal (Selections).")
    _send(_owner_emails(db, project_id), f"Selection needed — {name}", body)


def notify_office_selection(db: Session, project_id, *, title: str, choice: str, decided_by=None) -> None:
    """Owner → office: the client made a selection / approval decision."""
    name = _project_name(db, project_id)
    body = f"The client responded on {name}.\n\n{title}\nTheir choice: {choice}"
    _send(_office_emails(db, project_id, decided_by), f"Selection made — {name}", body)


def notify_owner_message(db: Session, project_id, *, preview: str) -> None:
    """Office → owner: a new message in the project thread."""
    name = _project_name(db, project_id)
    body = f"You have a new message from the BRIO team on {name}:\n\n“{preview}”\n\nReply in your portal."
    _send(_owner_emails(db, project_id), f"New message — {name}", body)


def notify_office_message(db: Session, project_id, *, preview: str, sender_id=None) -> None:
    """Owner → office: a new message in the project thread."""
    name = _project_name(db, project_id)
    body = f"The client sent a message on {name}:\n\n“{preview}”"
    _send(_office_emails(db, project_id, sender_id), f"New client message — {name}", body)


def notify_owner_update(db: Session, project_id, *, kind: str, message: str | None = None) -> None:
    name = _project_name(db, project_id)
    if kind == "photos":
        body = f"New photos of {name} have been added. You can view them in your portal."
        subject = f"New photos — {name}"
    elif kind == "milestone":
        phase = db.execute(text(
            "SELECT a.phase FROM activities a JOIN schedules s ON s.id = a.schedule_id "
            "WHERE s.project_id = :p AND a.start_date <= now()::date AND a.finish_date >= now()::date "
            "ORDER BY a.early_start_wd LIMIT 1"
        ), {"p": project_id}).scalar()
        body = (f"Progress update on {name}."
                + (f" We've reached the {phase} stage." if phase else "")
                + " See the latest in your portal.")
        subject = f"Project update — {name}"
    else:
        body = message or f"There's a new update on {name}. See your portal for details."
        subject = f"Update — {name}"
    _send(_owner_emails(db, project_id), subject, body)
