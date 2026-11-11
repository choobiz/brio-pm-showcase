"""Milestone bookings and letters (plan approved by Amir 2026-10-16).

  GET    /projects/{pid}/milestones                  the list, dated from the schedule, + team
  POST   /projects/{pid}/milestones/build            add library items the project lacks
  POST   /projects/{pid}/milestones                  a custom item
  PATCH  /projects/{pid}/milestones/{mid}            status, booked date, docs, notes, confirm
  PUT    /projects/{pid}/team/{role}                 who a role is on this job
  POST   /projects/{pid}/milestones/{mid}/draft      queue the assistant's draft (never sends)
  GET    /projects/{pid}/milestone-emails/{eid}      a draft or a sent email
  PUT    /projects/{pid}/milestone-emails/{eid}      the PM's edits
  POST   /projects/{pid}/milestone-emails/{eid}/send send it, only with confirm=true

Office only: these write to professionals in BRIO's name.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.milestone import MILESTONE_STATUSES, TEAM_ROLES, MilestoneEmail, ProjectMilestone, ProjectTeamMember
from app.services import milestones as svc

router = APIRouter(prefix="/projects/{project_id}", tags=["milestones"], dependencies=[Depends(require_office)])


def _item(db: Session, project_id: UUID, mid: UUID) -> ProjectMilestone:
    m = db.get(ProjectMilestone, mid)
    if m is None or m.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "milestone not found")
    return m


@router.get("/milestones")
def list_milestones(project_id: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    out = svc.listing(db, project_id)
    out["facts"] = svc.project_facts(db, project_id)
    out["roles"] = list(TEAM_ROLES)
    return out


class BuildBody(BaseModel):
    municipality: str | None = None


@router.post("/milestones/build")
def build_milestones(project_id: UUID, body: BuildBody | None = None, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    r = svc.build(db, project_id, user.email, (body or BuildBody()).municipality)
    db.commit()
    return {**r, **svc.listing(db, project_id)}


class ItemCreate(BaseModel):
    title: str = Field(min_length=2)
    stage: str = "Other"
    party_role: str = "other"
    email_kind: str = Field(default="booking", pattern="^(booking|document_request|submittal|notice)$")
    lead_days: int | None = None
    activity_code: str | None = None
    required_docs: list[str] = Field(default_factory=list)
    why: str | None = None


@router.post("/milestones", status_code=status.HTTP_201_CREATED)
def add_milestone(project_id: UUID, body: ItemCreate, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    n = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id).count()
    m = ProjectMilestone(project_id=project_id, ref=f"M{n + 1}", stage=body.stage, stage_order=999,
                         title=body.title, party_role=body.party_role, email_kind=body.email_kind,
                         lead_days=body.lead_days, activity_code=body.activity_code,
                         required_docs=[{"name": d, "status": "missing"} for d in body.required_docs],
                         why=body.why, source=f"added by {user.email}", confirmed=True, created_by=user.email)
    db.add(m)
    db.commit()
    return {"id": str(m.id), "ref": m.ref}


class ItemPatch(BaseModel):
    status: str | None = None
    booked_for: date | None = None
    notes: str | None = None
    confirmed: bool | None = None
    activity_code: str | None = None
    lead_days: int | None = None
    party_role: str | None = None
    #: [{name, status, document_id?, note?}] — the whole list, as edited.
    required_docs: list[dict[str, Any]] | None = None


@router.patch("/milestones/{mid}")
def patch_milestone(project_id: UUID, mid: UUID, body: ItemPatch, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    m = _item(db, project_id, mid)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in MILESTONE_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"status must be one of {', '.join(MILESTONE_STATUSES)}")
    if "party_role" in data and data["party_role"] not in TEAM_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown role")
    if "required_docs" in data:
        for d in data["required_docs"]:
            if not d.get("name") or d.get("status") not in ("missing", "received", "not_needed"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "each document needs a name and a status")
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    return svc.listing(db, project_id)


class TeamPut(BaseModel):
    name: str | None = None
    company: str | None = None
    email: str | None = Field(default=None, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = None
    notes: str | None = None


@router.put("/team/{role}")
def put_team(project_id: UUID, role: str, body: TeamPut, db: Session = Depends(get_db),
             user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    if role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown role")
    t = db.query(ProjectTeamMember).filter(ProjectTeamMember.project_id == project_id,
                                           ProjectTeamMember.role == role).first()
    if t is None:
        t = ProjectTeamMember(project_id=project_id, role=role)
        db.add(t)
    for k, v in body.model_dump().items():
        setattr(t, k, v or None)
    db.commit()
    return svc.listing(db, project_id)


class DraftBody(BaseModel):
    attachment_ids: list[UUID] = Field(default_factory=list)
    #: Anything the PM wants said ("ask about the wall forms too").
    instructions: str | None = Field(default=None, max_length=2000)


def _sender(db: Session, user: CurrentUser) -> dict[str, str]:
    from sqlalchemy import text as _T

    from app.company import COMPANY

    name = db.execute(_T("SELECT name FROM users WHERE id = :i"), {"i": user.id}).scalar() if user.id else None
    return {"name": name or user.email, "email": user.email,
            "company": COMPANY.get("legal_name") or "BRIO", "phone": COMPANY.get("phone", "")}


@router.post("/milestones/{mid}/draft", status_code=status.HTTP_202_ACCEPTED)
def draft_email(project_id: UUID, mid: UUID, body: DraftBody, db: Session = Depends(get_db),
                user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Queue the assistant's draft. It lands as a draft; nothing is sent."""
    from sqlalchemy import text as _T

    from app.models.ai_job import AIJob

    assert_project_access(db, user, project_id)
    m = _item(db, project_id, mid)
    ctx = svc.email_context(db, m, _sender(db, user))
    names = [f for (f,) in db.execute(_T("SELECT filename FROM documents WHERE project_id = :p AND id = ANY(:ids)"),
                                      {"p": str(project_id), "ids": [str(i) for i in body.attachment_ids]})]
    party = ctx["item"].get("party") or {}
    job = AIJob(project_id=project_id, kind="email_draft", status="queued",
                input={"context": ctx, "attachments": names, "instructions": body.instructions,
                       "milestone_id": str(m.id)})
    db.add(job)
    db.flush()
    e = MilestoneEmail(milestone_id=m.id, project_id=project_id, to_email=party.get("email"),
                       to_name=party.get("name"), attachment_ids=[str(i) for i in body.attachment_ids],
                       status="drafting", ai_job_id=job.id)
    db.add(e)
    db.commit()
    return {"email_id": str(e.id), "job_id": str(job.id)}


def _email(db: Session, project_id: UUID, eid: UUID) -> MilestoneEmail:
    e = db.get(MilestoneEmail, eid)
    if e is None or e.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "email not found")
    return e


def _from_line(db: Session, user: CurrentUser) -> str:
    """The From line this user's send would carry, shown in the confirmation.
    (Named apart from `_sender`, the drafter's signature: sharing that name
    replaced it, and drafts lost the PM's phone and company, 2026-10-17.)"""
    name = db.execute(_T("SELECT name FROM users WHERE id = :i"), {"i": user.id}).scalar() if user.id else None
    return svc.sender_for(user.email, name)


def _email_out(db: Session, e: MilestoneEmail, user: CurrentUser | None = None) -> dict[str, Any]:
    m = db.get(ProjectMilestone, e.milestone_id)
    return {"id": str(e.id), "milestone_id": str(e.milestone_id), "ref": m.ref if m else None,
            "to_email": e.to_email, "to_name": e.to_name, "cc": e.cc, "subject": e.subject, "body": e.body,
            "subject_as_sent": svc.tag_subject(e.subject, m.ref) if m and e.subject else None,
            "project_address": svc.project_address(db, e.project_id),
            "attachment_ids": e.attachment_ids, "status": e.status, "error": e.error,
            "sent_at": e.sent_at.isoformat() if e.sent_at else None, "sent_by": e.sent_by,
            "sent_envelope": e.sent_envelope,
            "from_as_sent": (e.sent_envelope or {}).get("from") or (_from_line(db, user) if user else None)}


@router.get("/milestone-emails/{eid}")
def get_email(project_id: UUID, eid: UUID, db: Session = Depends(get_db),
              user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    out = _email_out(db, _email(db, project_id, eid), user)
    db.commit()  # project_address may have minted the token
    return out


class EmailPut(BaseModel):
    to_email: str | None = Field(default=None, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    to_name: str | None = None
    cc: str | None = None
    subject: str | None = None
    body: str | None = None
    attachment_ids: list[UUID] | None = None


@router.put("/milestone-emails/{eid}")
def edit_email(project_id: UUID, eid: UUID, body: EmailPut, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    e = _email(db, project_id, eid)
    if e.status == "sent":
        raise HTTPException(status.HTTP_409_CONFLICT, "this email has been sent; draft a new one")
    data = body.model_dump(exclude_unset=True)
    if "attachment_ids" in data:
        data["attachment_ids"] = [str(i) for i in data["attachment_ids"] or []]
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return _email_out(db, e, user)


class SendBody(BaseModel):
    #: The PM has read the exact email (recipient, subject, body, attachments)
    #: and pressed Send in the confirmation. Anything else is refused.
    confirm: bool = False


@router.post("/milestone-emails/{eid}/send")
def send_email(project_id: UUID, eid: UUID, body: SendBody, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    if not body.confirm:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED,
                            "confirm the email before it goes out (confirm=true after review)")
    e = _email(db, project_id, eid)
    if e.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, f"this email is {e.status}")
    if not e.to_email or not e.subject.strip() or not e.body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a recipient, subject and body are required")
    if "[confirm" in e.body.lower() or "[confirm" in e.subject.lower():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "the draft still has a [confirm ...] placeholder; fill it in first")
    m = db.get(ProjectMilestone, e.milestone_id)
    from sqlalchemy import text as _T

    name = db.execute(_T("SELECT name FROM users WHERE id = :i"), {"i": user.id}).scalar() if user.id else None
    try:
        envelope = svc.send(db, e, m, user_email=user.email, user_name=name)
    except Exception as exc:  # noqa: BLE001  (SES refusal is shown to the PM, nothing half-sent)
        # svc.send records nothing until SES has accepted the message, so there
        # is nothing to undo: keep it a draft and say why.
        e.status, e.error = "draft", f"Not sent: {exc}"
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Not sent: {exc}")
    db.commit()
    return {**_email_out(db, e), "envelope": envelope}
