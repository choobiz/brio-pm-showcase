"""Inbound-email webhook — a per-project BCC/forward address feeds Conversations
(Track 2 P2).

Two entry points, both machine-facing (NOT user-authenticated), both guarded by
the shared ``INBOUND_WEBHOOK_SECRET`` (fail-closed):
  * ``POST /inbound/postmark`` — Postmark's inbound-parse JSON (production).
  * ``POST /inbound/email``   — generic JSON (tests / alternate providers).

The target project is resolved from the recipient token (``p-<token>@<domain>``),
so the URL carries no project id. Postmark can't send a custom header, so the
secret may be supplied via the ``?secret=`` query param OR the ``X-Inbound-Secret``
header.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.project import Project
from app.services.communication_ingest import ingest_communication


router = APIRouter(prefix="/inbound", tags=["inbound"])

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"p-([a-f0-9]{6,})@", re.IGNORECASE)


def _log_attachments(provider: str, attachments: list[dict] | None) -> None:
    """Record what the provider actually delivered.

    This path is machine-facing with no UI, which is exactly why silently dropped
    attachments went unnoticed. The absent-vs-empty distinction matters when
    diagnosing: a missing key means the provider never sends attachments (stream
    misconfigured), an empty list means it sent none for this message (the
    forward carried none), and names present here but no Document rows means the
    failure is ours.
    """
    if attachments is None:
        log.info("inbound[%s]: no Attachments key in payload", provider)
        return
    if not attachments:
        log.info("inbound[%s]: Attachments key present but empty", provider)
        return
    desc = ", ".join(
        f"{a.get('Name') or a.get('name') or '?'}"
        f"({a.get('ContentLength') or len(str(a.get('Content') or a.get('content') or ''))}b"
        f"{', inline' if (a.get('ContentID') or a.get('content_id')) else ''})"
        for a in attachments if isinstance(a, dict)
    )
    log.info("inbound[%s]: %d attachment(s): %s", provider, len(attachments), desc)


def _check_secret(provided: str | None) -> None:
    if not settings.inbound_webhook_secret or provided != settings.inbound_webhook_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bad or missing inbound secret")


def _token_from_text(*parts: str | None) -> str | None:
    for p in parts:
        if not p:
            continue
        m = _TOKEN_RE.search(p)
        if m:
            return m.group(1).lower()
    return None


def _html_to_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _resolve_and_ingest(
    db: Session, *, token: str | None, subject: str | None, body: str,
    from_email: str | None, from_name: str | None, to_raw: str | None,
    attachments: list[dict] | None = None,
) -> dict:
    # Always ACK with 200 (even when we can't process) so the provider's webhook
    # "check" passes and a misaddressed email doesn't trigger a retry-storm.
    if not token:
        return {"ok": False, "skipped": "no project token in recipient"}
    project = db.execute(select(Project).where(Project.inbound_token == token)).scalars().first()
    if project is None:
        return {"ok": False, "skipped": f"unknown project token: {token}"}
    # A body-less email carrying attachments is still worth keeping — forwarding
    # a drawing set with no covering note is normal. Synthesise a body so the
    # communication persists and the files aren't dropped with it.
    if not body.strip():
        if not attachments:
            return {"ok": False, "skipped": "empty body"}
        n = len(attachments)
        body = f"(no message body — {n} attachment{'s' if n != 1 else ''})"
    # Mail from a BRIO address is either a BCC of what we sent or a forward of
    # what we received; record who really wrote it (services/inbound_direction).
    from app.services.inbound_direction import resolve
    office = {e.lower() for e in db.execute(text(
        "SELECT email FROM users WHERE role IN ('manager', 'superadmin') AND email IS NOT NULL")).scalars()}
    who = resolve(from_email=from_email, from_name=from_name, to_raw=to_raw, subject=subject, body=body,
                  project_address_token=token, office_emails=office)
    c = ingest_communication(
        db, project_id=project.id, body=body, subject=who.subject,
        from_name=who.from_name, from_email=who.from_email, to_emails=who.to_emails,
        channel="email", direction=who.direction, source="forward",
        classify=True, created_by_id=None,
    )
    out = {"ok": True, "communication_id": str(c.id), "project_id": str(project.id)}
    # Attachments become project Documents. Runs after the communication is
    # committed and never raises, so an R2 failure costs the files, not the log.
    if attachments:
        from app.services.inbound_attachments import save_inbound_attachments
        out["attachments"] = save_inbound_attachments(
            db, project_id=project.id, attachments=attachments,
        )
    # A reply to a milestone email files against its item by the [KS-Mn] tag
    # (milestone emails BCC this address and put it in Reply-To). Never raises:
    # filing the communication matters more than linking it.
    try:
        from app.services.milestones import link_inbound

        doc_ids = [a["document_id"] for a in ((out.get("attachments") or {}).get("saved") or [])
                   if isinstance(a, dict) and a.get("document_id")]
        linked = link_inbound(db, project.id, subject=subject, from_email=from_email,
                              communication_id=str(c.id), document_ids=doc_ids)
        if linked:
            if linked.get("own_copy"):
                c.direction = "outbound"
            db.commit()
            out["milestone"] = linked
    except Exception:  # noqa: BLE001
        db.rollback()
    return out


# ---------------------------------------------------------------------------
# Generic JSON (tests / alternate providers)
# ---------------------------------------------------------------------------

class InboundEmail(BaseModel):
    to: str | None = None
    token: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    cc: str | None = None
    # Same shape as Postmark's: {"Name", "Content" (base64), "ContentType", "ContentID"}
    attachments: list[dict] | None = None


@router.post("/email")
def inbound_email(
    payload: InboundEmail,
    secret: str | None = None,
    x_inbound_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    _check_secret(x_inbound_secret or secret)
    _log_attachments("generic", payload.attachments)
    token = (payload.token.strip().lower() if payload.token else None) \
        or _token_from_text(payload.to, payload.cc)
    body = (payload.text or "").strip() or (_html_to_text(payload.html) if payload.html else "")
    return _resolve_and_ingest(
        db, token=token, subject=payload.subject, body=body,
        from_email=payload.from_email, from_name=payload.from_name, to_raw=payload.to,
        attachments=payload.attachments,
    )


# ---------------------------------------------------------------------------
# Postmark inbound-parse (production)
#   https://postmarkapp.com/developer/webhooks/inbound-webhook
# ---------------------------------------------------------------------------

class PostmarkInbound(BaseModel):
    model_config = ConfigDict(extra="ignore")
    OriginalRecipient: str | None = None   # the exact address this hit (best for token)
    To: str | None = None
    ToFull: list[dict] | None = None
    Cc: str | None = None
    From: str | None = None
    FromName: str | None = None
    FromFull: dict | None = None
    Subject: str | None = None
    TextBody: str | None = None
    HtmlBody: str | None = None
    # [{"Name", "Content" (base64), "ContentType", "ContentLength", "ContentID"}]
    # Requires "Include raw email content"/attachments enabled on the Postmark
    # inbound stream — without it Postmark sends an empty list.
    Attachments: list[dict] | None = None


@router.post("/postmark")
def inbound_postmark(
    payload: PostmarkInbound,
    secret: str | None = None,
    x_inbound_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    _check_secret(x_inbound_secret or secret)

    to_full = ",".join(x.get("Email", "") for x in (payload.ToFull or []) if isinstance(x, dict))
    to_raw = payload.OriginalRecipient or payload.To or to_full or None
    token = _token_from_text(payload.OriginalRecipient, payload.To, to_full, payload.Cc)

    ff = payload.FromFull or {}
    from_email = ff.get("Email") or payload.From
    from_name = ff.get("Name") or payload.FromName

    _log_attachments("postmark", payload.Attachments)
    body = (payload.TextBody or "").strip() or (_html_to_text(payload.HtmlBody) if payload.HtmlBody else "")
    return _resolve_and_ingest(
        db, token=token, subject=payload.Subject, body=body,
        from_email=from_email, from_name=from_name, to_raw=to_raw,
        attachments=payload.Attachments,
    )
