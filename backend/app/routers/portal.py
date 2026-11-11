"""Owner (client) portal — read-only, curated, per-project.

Access: role == 'owner' AND a member of the project, OR superadmin (preview).
Owners see only their project and only content marked client_visible. No cost
build-up, margins, or internal docs.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, real_user_id
from app.db import get_db
from app.models.client import Client
from app.models.invoice import Invoice
from app.models.project import Project
from app.services.invoice_pdf import render_invoice_pdf
from app.services.r2 import presign_get, put_bytes

# Invoice statuses that are issued to the client (drafts/void are office-only).
_CLIENT_INVOICE_STATUSES = ("sent", "partial", "paid", "overdue")


router = APIRouter(prefix="/portal", tags=["portal"])


def _access(db: Session, user: CurrentUser, project_id: UUID) -> None:
    """Owner-portal gate: owner-member or superadmin. Office/field roles are
    NOT admitted here (they use the office app)."""
    if user.role not in ("owner", "superadmin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner portal is client-only")
    assert_project_access(db, user, project_id)  # superadmin global / owner must be a member


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _owner_project_ids(db: Session, user: CurrentUser) -> list[str]:
    if user.role == "superadmin":
        return [str(r) for r in db.execute(text("SELECT id FROM projects WHERE status <> 'archived'")).scalars().all()]
    return [str(r) for r in db.execute(
        text("SELECT project_id FROM project_members WHERE user_id = :u"), {"u": user.id},
    ).scalars().all()]


@router.get("/projects")
def my_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    if user.role not in ("owner", "superadmin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner portal is client-only")
    ids = _owner_project_ids(db, user)
    if not ids:
        return []
    rows = db.execute(
        text("SELECT id, name, address, status, type FROM projects WHERE id = ANY(:ids)"),
        {"ids": ids},
    ).mappings().all()
    return [{"id": str(r["id"]), "name": r["name"], "address": r["address"],
             "status": r["status"], "type": r["type"]} for r in rows]


def _latest_schedule(db: Session, project_id: UUID):
    return db.execute(
        text("SELECT id, project_start, p50_finish_date, p80_finish_date FROM schedules "
             "WHERE project_id = :p AND client_visible ORDER BY created_at DESC LIMIT 1"),
        {"p": project_id},
    ).mappings().first()


def _activities(db: Session, schedule_id) -> list[dict]:
    return [dict(r) for r in db.execute(
        text("SELECT activity_code, name, phase, start_date, finish_date, is_milestone "
             "FROM activities WHERE schedule_id = :s ORDER BY early_start_wd NULLS FIRST, start_date"),
        {"s": schedule_id},
    ).mappings().all()]


def _hero_photo(db: Session, project_id: UUID) -> dict | None:
    r = db.execute(
        text("SELECT r2_key, filename FROM documents WHERE project_id = :p AND archived = false "
             "AND client_visible = true AND (mime LIKE 'image/%' OR category = 'photo') "
             "ORDER BY created_at DESC LIMIT 1"),
        {"p": project_id},
    ).mappings().first()
    return {"url": presign_get(r["r2_key"]), "caption": r["filename"]} if r else None


@router.get("/projects/{project_id}/overview")
def overview(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    _access(db, user, project_id)
    proj = db.execute(
        text("SELECT name, address, status, type FROM projects WHERE id = :p"), {"p": project_id},
    ).mappings().one()

    today = _today()
    current_phase: str | None = None
    next_milestone: dict | None = None
    target_finish: str | None = None
    srow = _latest_schedule(db, project_id)
    if srow:
        target_finish = str(srow["p50_finish_date"]) if srow["p50_finish_date"] else None
        acts = _activities(db, srow["id"])
        work = [a for a in acts if a["start_date"] and a["finish_date"]]
        # current phase = the activity spanning today; else nearest by time
        for a in work:
            if a["start_date"] <= today <= a["finish_date"]:
                current_phase = a["phase"]
                break
        if current_phase is None and work:
            if today < work[0]["start_date"]:
                current_phase = "Preparing to start"
            else:
                current_phase = work[-1]["phase"]
        # next milestone = first milestone finishing on/after today (else last)
        miles = [a for a in acts if a["is_milestone"] and a["start_date"]]
        upcoming = [a for a in miles if a["start_date"] >= today]
        pick = upcoming[0] if upcoming else (miles[-1] if miles else None)
        if pick:
            next_milestone = {"label": pick["name"], "date": str(pick["start_date"])}

    last_report = db.execute(
        text("SELECT max(report_date) FROM daily_site_reports WHERE project_id = :p"), {"p": project_id},
    ).scalar()

    return {
        "id": str(project_id),
        "name": proj["name"], "address": proj["address"], "status": proj["status"], "type": proj["type"],
        "current_phase": current_phase,
        "next_milestone": next_milestone,
        "target_finish": target_finish,
        "last_update": str(last_report) if last_report else None,
        "hero_photo": _hero_photo(db, project_id),
    }


@router.get("/projects/{project_id}/milestones")
def milestones(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    _access(db, user, project_id)
    srow = _latest_schedule(db, project_id)
    if not srow:
        return {"target_finish": None, "items": []}
    today = _today()
    items = []
    for a in _activities(db, srow["id"]):
        if not a["is_milestone"] or not a["start_date"]:
            continue
        d = a["start_date"]
        state = "done" if d < today else ("current" if d == today else "upcoming")
        items.append({"label": a["name"], "date": str(d), "state": state})
    return {"target_finish": str(srow["p50_finish_date"]) if srow["p50_finish_date"] else None, "items": items}


@router.get("/projects/{project_id}/photos")
def photos(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    _access(db, user, project_id)
    rows = db.execute(
        text("SELECT r2_key, filename, created_at FROM documents WHERE project_id = :p "
             "AND archived = false AND client_visible = true "
             "AND (mime LIKE 'image/%' OR category = 'photo') ORDER BY created_at DESC"),
        {"p": project_id},
    ).mappings().all()
    return [{"url": presign_get(r["r2_key"]), "caption": r["filename"],
             "date": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]


@router.get("/projects/{project_id}/documents")
def documents(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    _access(db, user, project_id)
    rows = db.execute(
        text("SELECT r2_key, filename, category, created_at FROM documents WHERE project_id = :p "
             "AND archived = false AND client_visible = true "
             "AND NOT (mime LIKE 'image/%' OR category = 'photo') ORDER BY created_at DESC"),
        {"p": project_id},
    ).mappings().all()
    return [{"name": r["filename"], "category": r["category"], "url": presign_get(r["r2_key"]),
             "date": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]


@router.get("/projects/{project_id}/payments")
def payments(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Owner-facing payment view: contract total, paid-to-date, and the draw
    schedule with per-draw status. Read-only (no cost build-up shown)."""
    _access(db, user, project_id)
    from app.services.package_pdf import _DRAW_WEIGHTS

    erow = db.execute(
        text("SELECT payload FROM estimate_versions WHERE project_id = :p AND is_active = true"),
        {"p": project_id},
    ).mappings().first()
    contract_total = 0
    draws: list[dict] = []
    if erow:
        est = erow["payload"] if isinstance(erow["payload"], dict) else json.loads(erow["payload"])
        oh = float(est.get("overhead") or 0)
        pr = float(est.get("profit") or 0)
        base = sum(float(c.get("total") or 0) for c in est.get("categories", []) if not c.get("optional"))
        pretax = round(base * (1 + oh / 100) * (1 + pr / 100))
        contract_total = round(pretax * 1.05)
        acc = 0
        for i, (m, pct, _detail) in enumerate(_DRAW_WEIGHTS):
            amt = contract_total - acc if i == len(_DRAW_WEIGHTS) - 1 else round(contract_total * pct / 100)
            acc += amt
            draws.append({"milestone": m, "pct": pct, "amount": amt})

    paid = float(db.execute(
        text("SELECT COALESCE(SUM(paid_amount), 0) FROM invoices WHERE project_id = :p"),
        {"p": project_id},
    ).scalar() or 0)

    # Approved change orders adjust the revised total (pre-tax → +GST).
    co_pretax = float(db.execute(
        text("SELECT COALESCE(SUM(cost_impact), 0) FROM change_orders WHERE project_id = :p AND status = 'approved'"),
        {"p": project_id},
    ).scalar() or 0)
    change_orders_total = round(co_pretax * 1.05)
    revised_total = contract_total + change_orders_total

    # Mark each draw against cumulative paid-to-date.
    cum = 0.0
    for d in draws:
        start = cum
        cum += d["amount"]
        d["status"] = "paid" if paid >= cum else ("partial" if paid > start else "due")

    return {
        "contract_total": contract_total,
        "change_orders_total": change_orders_total,
        "revised_total": revised_total,
        "paid_to_date": round(paid),
        "remaining": max(0, round(revised_total - paid)),
        "draws": draws,
    }


@router.get("/projects/{project_id}/invoices")
def portal_invoices(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Issued invoices for the client's project (drafts/void hidden)."""
    _access(db, user, project_id)
    rows = db.execute(
        select(Invoice)
        .where(
            Invoice.project_id == project_id,
            Invoice.status.in_(_CLIENT_INVOICE_STATUSES),
        )
        .order_by(Invoice.created_at.desc())
    ).scalars().all()
    out: list[dict] = []
    for inv in rows:
        p = inv.payload or {}
        total = float(p.get("total") or 0)
        paid = float(inv.paid_amount or 0)
        out.append({
            "id": str(inv.id),
            "number": inv.number,
            "type": inv.type,
            "date": p.get("date"),
            "description": p.get("description") or p.get("milestone"),
            "total": total,
            "paid": paid,
            "balance": round(total - paid, 2),
            "status": inv.status,
        })
    return out


@router.get("/projects/{project_id}/invoices/{invoice_id}/pdf")
def portal_invoice_pdf(
    project_id: UUID,
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Render the issued invoice's PDF and return a short-lived download URL."""
    _access(db, user, project_id)
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.project_id != project_id or inv.status not in _CLIENT_INVOICE_STATUSES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invoice not found")
    project = db.get(Project, inv.project_id)
    client = (
        db.get(Client, project.client_id)
        if project is not None and project.client_id is not None
        else None
    )
    pdf = render_invoice_pdf(
        number=inv.number,
        inv_type=inv.type,
        status=inv.status,
        paid_amount=inv.paid_amount,
        payload=inv.payload or {},
        project={
            "name": getattr(project, "name", None),
            "address": getattr(project, "address", None),
            "type": getattr(project, "type", None),
        },
        client={
            "name": getattr(client, "name", None),
            "email": getattr(client, "email", None),
            "phone": getattr(client, "phone", None),
            "address": getattr(client, "address", None),
        },
    )
    key = f"invoices/{inv.id}/{inv.number}.pdf"
    put_bytes(key, pdf, "application/pdf")
    return {"download_url": presign_get(key)}


@router.get("/projects/{project_id}/change-orders")
def portal_change_orders(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Change orders the client can see — pending (awaiting their decision),
    approved, or rejected. Drafts stay office-only."""
    _access(db, user, project_id)
    from app.models.change_order import ChangeOrder
    from app.routers.change_orders import co_read
    rows = db.execute(
        select(ChangeOrder).where(
            ChangeOrder.project_id == project_id,
            ChangeOrder.status.in_(["pending", "approved", "rejected"]),
        ).order_by(ChangeOrder.number)
    ).scalars().all()
    return [co_read(c) for c in rows]


class CODecision(BaseModel):
    decision: str  # "approve" | "reject"
    note: str | None = None


@router.post("/change-orders/{co_id}/decision")
def portal_co_decision(
    co_id: UUID,
    body: CODecision,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Owner approves/rejects a pending change order for their own project."""
    from app.models.change_order import ChangeOrder
    from app.routers.change_orders import co_read
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="change order not found")
    _access(db, user, co.project_id)  # owner-member or superadmin
    if co.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="this change order isn't awaiting a decision")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="decision must be approve or reject")
    co.status = "approved" if body.decision == "approve" else "rejected"
    co.decided_by_id = real_user_id(user)
    co.decided_at = datetime.now(timezone.utc)
    co.owner_note = body.note
    db.commit()
    db.refresh(co)
    from app.services.notify import notify_office_co_decision
    notify_office_co_decision(db, co)  # best-effort
    return co_read(co)


@router.get("/projects/{project_id}/selections")
def portal_selections(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    """Material selections / approvals for the client — pending (awaiting them),
    submitted (their choice), or confirmed by the office."""
    _access(db, user, project_id)
    # Only what the office has sent (drafts stay office-side), in the order the
    # work needs them, with the decide-by date (R2Selections, 2026-11-02).
    from app.services.selection_plan import plan
    keep = ("id", "title", "category", "description", "decision_type", "purchaser", "options", "status",
            "selected_option_id", "selected_option", "client_comment", "decided_at", "decide_by", "allowance")
    return [{k: r.get(k) for k in keep} for r in plan(db, project_id)["selections"] if r["sent_at"]]


class SelectionDecision(BaseModel):
    option_id: str | None = None       # for decision_type == 'select'
    decision: str | None = None        # for decision_type == 'approve': 'approved' | 'changes_requested'
    comment: str | None = None


@router.post("/projects/{project_id}/selections/{sid}/decide")
def portal_selection_decide(
    project_id: UUID,
    sid: UUID,
    body: SelectionDecision,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Owner submits a selection choice / approval for their own project."""
    from app.models.client_selection import ClientSelection
    from app.routers.selections import sel_read
    s = db.get(ClientSelection, sid)
    if s is None or s.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="selection not found")
    _access(db, user, project_id)  # owner-member or superadmin
    if s.sent_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="selection not found")
    if s.status == "confirmed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="this selection is already finalized")

    choice_label: str
    if s.decision_type == "approve":
        if body.decision not in ("approved", "changes_requested"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="decision must be approved or changes_requested")
        s.selected_option_id = body.decision
        choice_label = "Approved" if body.decision == "approved" else "Requested changes"
    else:
        opt = next((o for o in (s.options or []) if o.get("id") == body.option_id), None)
        if opt is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="option_id is not one of the choices")
        s.selected_option_id = body.option_id
        choice_label = opt.get("label") or body.option_id

    s.status = "submitted"
    s.client_comment = body.comment
    s.decided_at = datetime.now(timezone.utc)
    s.decided_by_id = real_user_id(user)
    db.commit()
    db.refresh(s)
    from app.services.notify import notify_office_selection
    notify_office_selection(db, project_id, title=s.title, choice=choice_label, decided_by=s.decided_by_id)
    return sel_read(s)


@router.get("/projects/{project_id}/messages")
def portal_messages(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """The client's message thread with the office. Opening marks office
    messages read."""
    _access(db, user, project_id)
    from app.models.project_message import ProjectMessage
    from app.routers.messages import mark_read, msg_read
    mark_read(db, project_id, "owner")
    rows = db.execute(
        select(ProjectMessage).where(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at)
    ).scalars().all()
    return {"messages": [msg_read(m) for m in rows]}


class MessageBody(BaseModel):
    body: str


@router.post("/projects/{project_id}/messages", status_code=status.HTTP_201_CREATED)
def portal_send_message(
    project_id: UUID,
    body: MessageBody,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Owner sends a message to the office for their own project."""
    _access(db, user, project_id)
    from app.models.project_message import ProjectMessage
    from app.routers.messages import msg_read
    text_body = (body.body or "").strip()
    if not text_body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="message body is required")
    sender_id = real_user_id(user)
    m = ProjectMessage(project_id=project_id, sender_id=sender_id, sender_role="owner", body=text_body)
    db.add(m)
    db.commit()
    db.refresh(m)
    from app.services.notify import notify_office_message
    notify_office_message(db, project_id, preview=text_body[:140], sender_id=sender_id)  # best-effort
    return msg_read(m)


# ── Proposal (round 2, 2026-11-02): view the proposal sent to the portal, accept it.
class ProposalAccept(BaseModel):
    typed_name: str
    agree: bool = False
    #: Names of the optional items the client kept; omitted = all of them.
    options: list[str] | None = None


@router.get("/projects/{project_id}/proposal")
def portal_proposal(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    _access(db, user, project_id)
    from app.services.proposal_portal import mark_opened, portal_view
    out = portal_view(db, project_id)
    if out and user.role == "owner":  # the client opening it, not the office previewing
        mark_opened(db, project_id, out["id"])
        db.commit()
    return {"proposal": out}


@router.post("/projects/{project_id}/proposal/{presentation_id}/accept")
def portal_proposal_accept(
    project_id: UUID,
    presentation_id: UUID,
    body: ProposalAccept,
    request: Request,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """The client accepts by typing their name and ticking the box. Recorded
    with the time, IP, browser and the price shown. No contract changes."""
    _access(db, user, project_id)
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="only the client can accept a proposal")
    if not body.agree:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Tick the box to accept.")
    from app.services import proposal_portal as pp
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = fwd or (request.client.host if request.client else None)
    uid = real_user_id(user)
    try:
        out = pp.accept(db, project_id, presentation_id, uid, body.typed_name, ip,
                        request.headers.get("user-agent"), body.options)
    except pp.ProposalError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    db.commit()
    from app.services.notify import notify_office_message
    notify_office_message(db, project_id, preview=f"The client accepted the proposal ({body.typed_name}).",
                          sender_id=uid)  # best-effort
    return {"proposal": out}


@router.get("/projects/{project_id}/warranty")
def portal_warranty(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Warranty & care overview: the 1-year workmanship warranty (always), the
    2-5-10 home warranty where the project is a dwelling, and the projected
    warranty-start (substantial completion)."""
    _access(db, user, project_id)
    from app.services.package_pdf import HOME_WARRANTY_PROVIDER
    row = db.execute(
        text("SELECT home_warranty_required FROM projects WHERE id = :p"), {"p": project_id}
    ).first()
    home_warranty = bool(row and row[0])
    completion = db.execute(
        text("SELECT p50_finish_date FROM schedules WHERE project_id = :p AND client_visible "
             "ORDER BY created_at DESC LIMIT 1"), {"p": project_id}
    ).scalar()
    return {
        "workmanship_years": 1,
        "home_warranty_required": home_warranty,
        "home_warranty_provider": HOME_WARRANTY_PROVIDER if home_warranty else None,
        "projected_completion": str(completion) if completion else None,
    }


@router.get("/projects/{project_id}/package.pdf")
def package_pdf(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _access(db, user, project_id)
    from app.services.package_pdf import PackageIncomplete, build_client_package
    try:
        pdf, filename = build_client_package(db, str(project_id), "active", "merged")
    except PackageIncomplete as e:
        raise HTTPException(status_code=409, detail=str(e))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})
