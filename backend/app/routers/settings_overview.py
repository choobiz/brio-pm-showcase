"""Settings page (round 2 board R2Settings): who has access, what each role
can do (read-only: it describes what the app enforces), the company details
the documents carry, and the email addresses. Office only; the people list
is superadmin only, like Users & Roles."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office
from app.config import settings
from app.db import get_db

router = APIRouter(prefix="/settings", tags=["settings"])

# What the app enforces, per role (kept beside the code that enforces it:
# require_office on money/estimate/AI routers, superadmin on clients/CRM/
# reviews/skills/users, portal routes owner-only).
ROLE_MATRIX = [
    {"what": "Money, estimates, rates", "superadmin": "yes", "manager": "their projects", "worker": "no",
     "owner": "own payments and invoices"},
    {"what": "AI writing", "superadmin": "yes", "manager": "under the approval limit", "worker": "no", "owner": "no"},
    {"what": "Keep / activate a version", "superadmin": "yes", "manager": "no", "worker": "no", "owner": "no"},
    {"what": "Daily reports, clock in", "superadmin": "yes", "manager": "yes", "worker": "their projects", "owner": "no"},
    {"what": "Clients, Sales, Skills, Reviews, Users", "superadmin": "yes", "manager": "no", "worker": "no", "owner": "no"},
    {"what": "Portal: progress, selections, proposal, messages", "superadmin": "preview", "manager": "no",
     "worker": "no", "owner": "their project"},
]


@router.get("/overview")
def settings_overview(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    people = None
    if user.role == "superadmin":
        people = [dict(r) for r in db.execute(text("""
            SELECT u.email, u.name, u.role, u.active,
                   (SELECT count(*) FROM project_members pm WHERE pm.user_id = u.id) AS projects
              FROM users u ORDER BY u.active DESC, u.role, u.email""")).mappings()]
    addresses = [{"project": r[0], "address": f"p-{r[1]}@{settings.inbound_email_domain}"}
                 for r in db.execute(text("""SELECT name, inbound_token FROM projects
                                              WHERE inbound_token IS NOT NULL AND status <> 'archived'
                                              ORDER BY name""")).all()]
    return {
        "people": people,
        "roles": ROLE_MATRIX,
        "company": {"name": "BRIO Construction Inc.", "editable": False},
        "email": {"sender": settings.aws_ses_from_email or None, "inbound_domain": settings.inbound_email_domain,
                  "project_addresses": addresses},
    }
