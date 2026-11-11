"""Milestone bookings and letters: building a project's list, dating it from the
schedule, and the email around each item (plan approved by Amir 2026-10-16).

Nothing here sends anything unless `send_email` is called by the send endpoint,
which requires the PM's explicit confirmation of the exact message.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.models.milestone import MilestoneEmail, ProjectMilestone, ProjectTeamMember
from app.services.milestone_library import Item, library_for

#: The tag every outgoing subject carries, so a reply files back to its item.
REF_TAG = re.compile(r"\[KS-([MS]\d+)\]", re.I)


# ── Project facts read from its own documents ───────────────────────────────


def _doc_text(db: Session, project_id: UUID | str, filename_like: str | None = None, limit_chars: int = 400_000) -> str:
    rows = db.execute(_T("""
        SELECT d.filename, p.text FROM documents d
          JOIN document_text_pages p ON p.document_id = d.id
         WHERE d.project_id = :p AND NOT d.archived
           AND (CAST(:f AS text) IS NULL OR d.filename ILIKE :f)"""),
        {"p": str(project_id), "f": filename_like}).all()
    return re.sub(r"\s+", " ", " ".join((t or "") for _f, t in rows))[:limit_chars]


def project_facts(db: Session, project_id: UUID | str) -> dict[str, Any]:
    """What the project's own documents say that changes its list: the
    engineer's notice period, whether there is a stormwater plan, the permit
    number. Each fact carries where it came from."""
    facts: dict[str, Any] = {}
    names = [f for (f,) in db.execute(_T(
        "SELECT filename FROM documents WHERE project_id = :p AND NOT archived"), {"p": str(project_id)})]
    for n in names:
        m = re.search(r"\b(BLD\d{4}-\d{4,6})\b", n or "", re.I)
        if m:
            facts["permit_number"] = {"value": m.group(1).upper(), "source": n}
            break
    if any(re.search(r"\bSMP\b|stormwater", n or "", re.I) for n in names):
        facts["stormwater"] = {"value": True, "source": next(n for n in names if re.search(r"\bSMP\b|stormwater", n or "", re.I))}
    text = _doc_text(db, project_id)
    # "NOTIFY GEO ENGINEERS MINIMUM 3 DAYS IN ADVANCE FOR INSPECTION"
    m = re.search(r"NOTIFY\s+(.{2,40}?)\s+(?:A\s+)?MINIMUM\s+(?:OF\s+)?(\d{1,2})\s+(?:WORKING\s+|BUSINESS\s+)?DAYS",
                  text, re.I)
    if m:
        facts["engineer_notice"] = {"value": int(m.group(2)), "firm": m.group(1).strip().title(),
                                    "quote": m.group(0), "source": "structural drawing notes"}
        tail = text[m.end():m.end() + 260]
        facts["engineer_notice"]["for"] = tail.split(" 1. ")[0].strip(" :-")
    if not facts.get("permit_number"):
        m = re.search(r"\b(BLD\d{4}-\d{4,6})\b", text)
        if m:
            facts["permit_number"] = {"value": m.group(1), "source": "project documents"}
    return facts


# ── Schedule ────────────────────────────────────────────────────────────────


def schedule_activities(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    """The latest schedule's activities (schedules are versioned; the newest wins)."""
    return [dict(r) for r in db.execute(_T("""
        SELECT a.activity_code AS code, a.name, a.start_date, a.finish_date
          FROM activities a
         WHERE a.schedule_id = (SELECT id FROM schedules WHERE project_id = :p
                                 ORDER BY version_number DESC, created_at DESC LIMIT 1)
         ORDER BY a.start_date NULLS LAST, a.activity_code"""), {"p": str(project_id)}).mappings()]


def match_activity(item: Item, acts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for pattern in item.match:
        for a in acts:
            if re.search(pattern, a["name"] or "", re.I):
                return a
    return None


def minus_business_days(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


# ── Building the list ───────────────────────────────────────────────────────


def build(db: Session, project_id: UUID | str, user_email: str, municipality: str | None = None) -> dict[str, Any]:
    """Add every library item the project doesn't have yet. Idempotent: an item
    already on the list (by template key) is left exactly as the PM set it."""
    facts = project_facts(db, project_id)
    acts = schedule_activities(db, project_id)
    have = {m.template_key for m in db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id)}
    n_refs = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id).count()
    notice = facts.get("engineer_notice")
    added = 0
    for it in library_for(municipality):
        if it.key in have:
            continue
        if it.needs and not facts.get(it.needs):
            continue
        lead, lead_note, source = it.lead_days, it.lead_note, it.source
        if notice and it.role in ("structural_engineer", "geotechnical_engineer") and it.kind == "booking":
            lead = notice["value"]
            lead_note = f"{notice['firm']}: “{notice['quote']}” ({notice['source']})"
        act = match_activity(it, acts)
        n_refs += 1
        db.add(ProjectMilestone(
            project_id=project_id, ref=f"M{n_refs}", template_key=it.key, stage=it.stage, stage_order=it.order,
            title=it.title, party_role=it.role, email_kind=it.kind, status="to_book", lead_days=lead,
            lead_note=lead_note, activity_code=act["code"] if act else None,
            required_docs=[{"name": d, "status": "missing"} for d in it.docs],
            why=it.why + (" Confirm whether it applies." if it.optional else ""), source=source,
            confirmed=False, created_by=user_email))
        added += 1
    db.flush()
    return {"added": added, "facts": facts}


def serialize(db: Session, m: ProjectMilestone, acts: dict[str, dict[str, Any]] | None = None,
              team: dict[str, ProjectTeamMember] | None = None, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    a = (acts or {}).get(m.activity_code or "")
    need_by = a["start_date"] if a else None
    book_by = minus_business_days(need_by, m.lead_days) if need_by and m.lead_days else need_by
    who = (team or {}).get(m.party_role)
    docs = m.required_docs or []
    return {
        "id": str(m.id), "ref": m.ref, "template_key": m.template_key, "stage": m.stage,
        "stage_order": m.stage_order, "title": m.title, "party_role": m.party_role, "email_kind": m.email_kind,
        "status": m.status, "lead_days": m.lead_days, "lead_note": m.lead_note, "activity_code": m.activity_code,
        "activity_name": a["name"] if a else None,
        "need_by": need_by.isoformat() if need_by else None, "book_by": book_by.isoformat() if book_by else None,
        "late": bool(book_by and book_by < today and m.status == "to_book"),
        "required_docs": docs, "docs_ready": sum(1 for d in docs if d.get("status") in ("received", "not_needed")),
        "why": m.why, "source": m.source, "booked_for": m.booked_for.isoformat() if m.booked_for else None,
        "notes": m.notes, "confirmed": m.confirmed, "thread": m.thread or [],
        # Drafts and sent emails, newest first, so the panel reopens a draft
        # instead of starting a new one.
        "emails": [{"id": str(e.id), "status": e.status, "subject": e.subject,
                    "updated_at": e.updated_at.isoformat() if e.updated_at else None}
                   for e in db.query(MilestoneEmail).filter(MilestoneEmail.milestone_id == m.id)
                   .order_by(MilestoneEmail.created_at.desc())],
        "party": ({"name": who.name, "company": who.company, "email": who.email, "phone": who.phone}
                  if who else None),
    }


def listing(db: Session, project_id: UUID | str) -> dict[str, Any]:
    acts = {a["code"]: a for a in schedule_activities(db, project_id)}
    team = {t.role: t for t in db.query(ProjectTeamMember).filter(ProjectTeamMember.project_id == project_id)}
    items = (db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id)
             .order_by(ProjectMilestone.stage_order, ProjectMilestone.ref).all())
    return {"items": [serialize(db, m, acts, team) for m in items],
            "team": [{"id": str(t.id), "role": t.role, "name": t.name, "company": t.company, "email": t.email,
                      "phone": t.phone, "notes": t.notes} for t in team.values()]}


# ── Email ───────────────────────────────────────────────────────────────────


def tag_subject(subject: str, ref: str) -> str:
    """Every outgoing subject ends with the item's tag; never twice."""
    subject = REF_TAG.sub("", subject or "").strip()
    return f"{subject} [KS-{ref}]"


def project_address(db: Session, project_id: UUID | str) -> str:
    """The project's forwarding address, minted on first use (as /inbound-address does)."""
    import secrets

    from app.config import settings
    from app.models.project import Project

    p = db.get(Project, project_id)
    if not p.inbound_token:
        p.inbound_token = secrets.token_hex(6)
        db.flush()
    return f"p-{p.inbound_token}@{settings.inbound_email_domain}"


def email_context(db: Session, m: ProjectMilestone, sender: dict[str, str]) -> dict[str, Any]:
    """Everything the drafter is allowed to say, and nothing else."""
    from app.models.project import Project

    p = db.get(Project, m.project_id)
    facts = project_facts(db, m.project_id)
    acts = schedule_activities(db, m.project_id)
    by_code = {a["code"]: a for a in acts}
    team = {t.role: t for t in db.query(ProjectTeamMember).filter(ProjectTeamMember.project_id == m.project_id)}
    item = serialize(db, m, by_code, team)
    # The neighbouring activities give the drafter real dates (the pour after
    # a review, the next pour that may need a second visit).
    near = []
    if item["need_by"]:
        d = date.fromisoformat(item["need_by"])
        near = [{"code": a["code"], "name": a["name"], "start": a["start_date"].isoformat()}
                for a in acts if a["start_date"] and abs((a["start_date"] - d).days) <= 14]
    related = [serialize(db, x, by_code, team) for x in db.query(ProjectMilestone).filter(
        ProjectMilestone.project_id == m.project_id, ProjectMilestone.party_role == m.party_role,
        ProjectMilestone.id != m.id)]
    docs = [{"id": str(i), "filename": f} for i, f in db.execute(_T(
        "SELECT id, filename FROM documents WHERE project_id = :p AND NOT archived "
        "AND category IN ('drawing','survey','permit','report') ORDER BY created_at DESC LIMIT 40"),
        {"p": str(m.project_id)})]
    muni = team.get("municipality")
    return {
        "municipality": (muni.company or muni.name) if muni else "the municipality having jurisdiction",
        "project": {"name": p.name, "address": p.address, "permit_number": (facts.get("permit_number") or {}).get("value")},
        "item": item, "facts": facts, "nearby_schedule": near,
        "same_party_items": [{"ref": r["ref"], "title": r["title"], "status": r["status"],
                              "need_by": r["need_by"]} for r in related],
        "available_documents": docs, "sender": sender,
    }


def sender_for(user_email: str, user_name: str | None) -> str:
    """The From line. Amir, 2026-10-17: send "from amir@builtbybrio.com" so the
    record is kept without remembering a BCC. SES only signs for its verified
    domains, so any other address goes out as "Name via Keystone"."""
    from email.utils import formataddr

    from app.config import settings

    domains = {d.strip().lower() for d in settings.ses_sender_domains.split(",") if d.strip()}
    if user_email and user_email.rsplit("@", 1)[-1].lower() in domains:
        return formataddr((user_name or "", user_email)) if user_name else user_email
    return formataddr((f"{user_name or user_email} via Keystone", settings.aws_ses_from_email))


def send(db: Session, e: MilestoneEmail, m: ProjectMilestone, *, user_email: str, user_name: str | None) -> dict[str, Any]:
    """Send one confirmed email: from the PM's own address when it is on a verified
    domain (else "via Keystone"), Reply-To the PM AND the project
    address (a BCC'd address never receives replies), BCC the project address so
    the sent copy is filed too. Raises on any SES failure; the caller records it."""
    from email.message import EmailMessage
    from email.utils import formataddr

    from app.config import settings
    from app.services.email import deliver

    proj_addr = project_address(db, m.project_id)
    subject = tag_subject(e.subject, m.ref)
    msg = EmailMessage()
    msg["From"] = sender_for(user_email, user_name)
    msg["To"] = formataddr((e.to_name or "", e.to_email)) if e.to_name else e.to_email
    if e.cc:
        msg["Cc"] = e.cc
    msg["Reply-To"] = ", ".join(x for x in (user_email, proj_addr) if x)
    msg["Subject"] = subject
    msg.set_content(e.body)
    names = []
    for doc_id in e.attachment_ids or []:
        row = db.execute(_T("SELECT filename, r2_key, mime FROM documents WHERE id = :i AND project_id = :p"),
                         {"i": doc_id, "p": str(m.project_id)}).first()
        if row is None:
            continue
        from app.services.r2 import get_bytes

        maintype, _, subtype = (row.mime or "application/octet-stream").partition("/")
        msg.add_attachment(get_bytes(row.r2_key), maintype=maintype, subtype=subtype or "octet-stream",
                           filename=row.filename)
        names.append(row.filename)
    rcpts = [e.to_email, *[c.strip() for c in (e.cc or "").split(",") if c.strip()], proj_addr]
    deliver(msg, rcpts)
    now = datetime.now(timezone.utc)
    envelope = {"from": msg["From"], "to": msg["To"], "cc": e.cc, "reply_to": msg["Reply-To"], "bcc": proj_addr,
                "subject": subject, "attachments": names}
    e.status, e.sent_at, e.sent_by, e.sent_envelope, e.error = "sent", now, user_email, envelope, None
    m.thread = [*(m.thread or []), {"at": now.isoformat(), "direction": "out", "email_id": str(e.id),
                                    "to": e.to_email, "subject": subject, "document_ids": []}]
    if m.status == "to_book":
        m.status = "requested"
    return envelope


def link_inbound(db: Session, project_id: UUID, *, subject: str | None, from_email: str | None,
                 communication_id: str, document_ids: list[str]) -> dict[str, Any] | None:
    """File an email that arrived at the project address against its item, by
    the [KS-Mn] tag. Our own BCC copy is recognised and not counted as a reply."""
    from app.config import settings

    t = REF_TAG.search(subject or "")
    if not t:
        return None
    if t.group(1).upper().startswith("S"):
        return _link_submittal(db, project_id, t.group(1).upper(), subject=subject, from_email=from_email,
                               communication_id=communication_id, document_ids=document_ids)
    m = db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project_id,
                                          ProjectMilestone.ref == t.group(1).upper()).first()
    if m is None:
        return None
    # Our BCC copy comes from the default sender, or from the PM's own address with
    # the exact subject we sent; a reply ("RE: ...") from anyone is a reply.
    from email.utils import parseaddr

    sender = (from_email or "").lower()
    own = sender == (settings.aws_ses_from_email or "").lower() or any(
        (x.sent_envelope or {}).get("subject") == subject
        and parseaddr((x.sent_envelope or {}).get("from") or "")[1].lower() == sender
        for x in db.query(MilestoneEmail).filter(MilestoneEmail.milestone_id == m.id, MilestoneEmail.status == "sent")
    )
    if own:
        return {"milestone": m.ref, "own_copy": True}
    m.thread = [*(m.thread or []), {"at": datetime.now(timezone.utc).isoformat(), "direction": "in",
                                    "communication_id": communication_id, "from": from_email,
                                    "subject": subject, "document_ids": document_ids}]
    db.flush()
    return {"milestone": m.ref, "own_copy": False}


def _link_submittal(db: Session, project_id: UUID, ref: str, *, subject: str | None, from_email: str | None,
                    communication_id: str, document_ids: list[str]) -> dict[str, Any] | None:
    """A [KS-Sn] email: our own BCC copy of the questions (sent from Gmail, so
    from the PM: the subject has no "RE:") or a reply, filed on the newest
    revision of that submittal."""
    from app.models.submittal import Submittal

    s = db.query(Submittal).filter(Submittal.project_id == project_id, Submittal.ref == ref) \
        .order_by(Submittal.created_at.desc()).first()
    if s is None:
        return None
    if any(t.get("communication_id") == communication_id for t in s.thread or []):
        return {"submittal": ref, "own_copy": None, "duplicate": True}
    own = any(tag_subject(e.get("subject") or "", ref) == (subject or "").strip() for e in s.emails or [])
    s.thread = [*(s.thread or []), {"at": datetime.now(timezone.utc).isoformat(), "direction": "out" if own else "in",
                                    "communication_id": communication_id, "from": from_email,
                                    "subject": subject, "document_ids": document_ids}]
    db.flush()
    return {"submittal": ref, "own_copy": own}
