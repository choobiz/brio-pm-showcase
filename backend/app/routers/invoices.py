"""Invoices router — Track E.

Invoice numbers come from the Postgres sequence `invoice_number_seq` so
`INV-{YYYY}-{NNNN}` is collision-free under concurrent creates. Totals are
computed server-side from the type-specific payload. Payments are recorded as
`Payment` rows; the invoice's `paid_amount` + `status` are recomputed on each
insert. PDFs go to R2; "send" emails the client the PDF via SES. Every
create / payment / send / status change writes an `ActivityLog` row.
"""

from __future__ import annotations
from app.company import COMPANY

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import Response
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.activity_log import ActivityLog
from app.models.client import Client
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.project import Project
from app.schemas.invoice import (
    ChangeOrderInvoiceSeed,
    InvoiceCreateRequest,
    InvoiceSeed,
    InvoicePatch,
    InvoicePdfResult,
    InvoiceRead,
    InvoiceSendResult,
)
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services.client_text import redact_internal_pricing
from app.services.email import send_email
from app.services.invoice_pdf import render_invoice_pdf
from app.services.r2 import presign_get, put_bytes
from app.services.share_token import sign_invoice_token

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/invoices", tags=["invoices"], dependencies=[Depends(require_office)])


# --- helpers ---------------------------------------------------------------


def _invoice_for_user(db: Session, invoice_id: UUID, user: CurrentUser) -> Invoice:
    """Load an invoice and authorise the caller for its project.

    Every invoice endpoint keyed on invoice_id must go through here. Before
    2026-09-10 not one endpoint in this router called assert_project_access, so
    any authenticated user could read, edit, PDF or SEND invoices belonging to
    any project. documents.py had guarded correctly all along; invoices did not.
    """
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    assert_project_access(db, user, invoice.project_id)
    return invoice



def _actor_uuid(user: CurrentUser) -> UUID | None:
    raw = getattr(user, "id", None)
    return UUID(raw) if raw else None


def _log(db: Session, *, project_id: UUID, user: CurrentUser, action: str, **payload) -> None:
    db.add(
        ActivityLog(
            project_id=project_id,
            actor_id=_actor_uuid(user),
            action=action,
            payload=payload,
        )
    )


def _next_invoice_number(db: Session) -> str:
    """`INV-{YYYY}-{NNNN}` — the numeric part from an atomic sequence."""
    n = db.execute(text("SELECT nextval('invoice_number_seq')")).scalar_one()
    return f"INV-{date.today().year}-{int(n):04d}"


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class PercentageWithoutBasis(ValueError):
    """A line bills a percentage of the contract and there is nothing to take it of.

    Raised rather than falling through to zero. The progress form offers a
    "% of contract" column and the client PDF renders it, so a percentage with
    no resolvable contract value is a real instruction we cannot carry out - not
    a line worth nothing.
    """


def _apply_percentages(items: list, contract_basis: float | None) -> None:
    """Turn "% of contract" lines into amounts, in place.

    ⚠️ The basis is the estimate SUBTOTAL, pre-GST. INV-1234-001 billed its 10%
    deposit as 18,041.90 against a 180,419 subtotal and then added GST on top,
    so percentages have always applied before tax. Taking them off a
    GST-inclusive total would overbill by 5%.

    An explicit amount wins over a percentage: someone who typed a number meant
    that number. The percentage is kept either way because the PDF prints it.
    """
    for it in items:
        if not isinstance(it, dict):
            continue
        pct = _f(it.get("percentage"))
        if not pct or _f(it.get("amount")):
            continue
        if not contract_basis:
            raise PercentageWithoutBasis(
                f'line "{it.get("description") or "(no description)"}" bills '
                f"{pct}% of the contract, but this project has no active estimate "
                f"to take a percentage of - enter an amount, or set an active "
                f"estimate version first"
            )
        # Written back so the stored invoice says what it billed, rather than
        # leaving the number to be re-derived every time it is read.
        it["amount"] = round(contract_basis * pct / 100, 2)


def _compute_totals(inv_type: str, payload: dict, contract_basis: float | None = None) -> dict:
    """Compute subtotal / GST / total from the type-specific payload and write
    them back in. progress/change_order sum lineItem amounts; t&m sums
    labor + (materials × markup); retainer uses lineItems or a flat amount.

    `contract_basis` resolves "% of contract" lines. Omitted, a percentage line
    with no amount raises rather than silently contributing nothing.
    """
    if inv_type == "tm":
        labor = sum(
            _f(l.get("hours")) * _f(l.get("rate"))
            for l in (payload.get("labor") or [])
            if isinstance(l, dict)
        )
        materials = sum(
            _f(m.get("qty") or 1) * _f(m.get("unitCost"))
            for m in (payload.get("materials") or [])
            if isinstance(m, dict)
        )
        markup = _f(payload.get("materialMarkup")) / 100
        subtotal = labor + materials * (1 + markup)
    else:
        items = payload.get("lineItems") or []
        _apply_percentages(items, contract_basis)
        subtotal = sum(_f(it.get("amount")) for it in items if isinstance(it, dict))
        if inv_type == "retainer" and not items:
            subtotal = _f(payload.get("amount") or payload.get("subtotal"))

    subtotal = round(subtotal, 2)
    gst = round(subtotal * 0.05, 2)

    # Adjustments sit AFTER GST and are not taxed. They are not work: they are
    # corrections to the account - GST missed on an earlier invoice, a credit, a
    # holdback release. Two things follow, and both are the point of the feature:
    # the subtotal stays the value of the work billed on THIS invoice, and no GST
    # is charged on an adjustment, which matters most for the case that prompted
    # it, because charging 5% on a GST catch-up would be charging tax on tax.
    # Negative amounts are allowed deliberately - a credit or a holdback
    # deduction is the same mechanism with the sign flipped.
    adjustments = [a for a in (payload.get("adjustments") or []) if isinstance(a, dict)]
    adj_total = round(sum(_f(a.get("amount")) for a in adjustments), 2)

    payload["subtotal"] = subtotal
    payload["gst"] = gst
    payload["adjustmentsTotal"] = adj_total
    payload["total"] = round(subtotal + gst + adj_total, 2)
    return payload


def _recompute_payment_state(db: Session, invoice: Invoice) -> None:
    """Recompute `paid_amount` + `status` from the invoice's Payment rows."""
    total = Decimal(str(invoice.payload.get("total") or 0))
    rows = db.execute(
        select(Payment.amount, Payment.paid_at).where(Payment.invoice_id == invoice.id)
    ).all()
    paid_sum = sum((r.amount for r in rows), Decimal("0"))
    invoice.paid_amount = paid_sum
    if total > 0 and paid_sum >= total:
        invoice.status = "paid"
        # The date of the latest payment, not the moment it was entered: until
        # 2026-10-15 a payment recorded days later dated the invoice paid today.
        invoice.paid_at = max((r.paid_at for r in rows if r.paid_at), default=datetime.now(timezone.utc))
    elif paid_sum > 0:
        invoice.status = "partial"
        invoice.paid_at = None
    else:
        invoice.paid_at = None


def _project_and_client(db: Session, project_id: UUID) -> tuple[dict, dict]:
    project = db.get(Project, project_id)
    client = (
        db.get(Client, project.client_id)
        if project is not None and project.client_id is not None
        else None
    )
    return (
        {
            "name": getattr(project, "name", None),
            "address": getattr(project, "address", None),
            "type": getattr(project, "type", None),
        },
        {
            "name": getattr(client, "name", None),
            "email": getattr(client, "email", None),
            "phone": getattr(client, "phone", None),
            "address": getattr(client, "address", None),
        },
    )


def _render_and_store(db: Session, invoice: Invoice, user: CurrentUser) -> tuple[bytes, Document]:
    """Render the invoice PDF, store it in R2, and record a Document."""
    project_dict, client_dict = _project_and_client(db, invoice.project_id)
    pdf = render_invoice_pdf(
        number=invoice.number,
        inv_type=invoice.type,
        status=invoice.status,
        paid_amount=invoice.paid_amount,
        payload=invoice.payload or {},
        project=project_dict,
        client=client_dict,
    )
    key = f"invoices/{invoice.id}/{invoice.number}.pdf"
    put_bytes(key, pdf, "application/pdf")

    # The R2 key is deterministic, so every render overwrites the same object.
    # Inserting a Document row unconditionally therefore produced one row per
    # "Generate PDF" click, all pointing at the same file: INV-2026-10056 had
    # five. They looked like five versions in the Documents list, with differing
    # byte sizes, while four of them no longer existed. Reuse the row instead.
    doc = (
        db.query(Document)
        .filter(Document.r2_key == key)
        .order_by(Document.created_at.desc())
        .first()
    )
    if doc is None:
        doc = Document(
            project_id=invoice.project_id,
            category="invoice_pdf",
            phase="in_progress",
            filename=f"{invoice.number}.pdf",
            r2_key=key,
            mime="application/pdf",
            size_bytes=len(pdf),
            uploaded_by_id=_actor_uuid(user),
        )
        db.add(doc)
    else:
        doc.size_bytes = len(pdf)
        doc.uploaded_by_id = _actor_uuid(user)
    return pdf, doc


# --- routes ----------------------------------------------------------------


@router.get("", response_model=list[InvoiceRead])
def list_invoices(
    project_id: UUID = Query(..., description="filter to invoices for this project"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Invoice]:
    assert_project_access(db, user, project_id)
    stmt = (
        select(Invoice)
        .where(Invoice.project_id == project_id)
        .order_by(Invoice.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


@router.get("/seed-from-estimate", response_model=InvoiceSeed)
def seed_invoice_from_estimate(
    project_id: UUID = Query(...),
    type: str = Query("progress"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Propose an invoice payload from the project's ACTIVE estimate version.

    This proposes; it does not create. The numbers are money, so a person reads
    them in the form and presses create. Nothing is written here.
    """
    assert_project_access(db, user, project_id)
    if type == "change_order":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "a change order bills work beyond the estimate — seed it from the "
                "change order itself via /invoices/seed-from-change-order"
            ),
        )
    version = _active_estimate_version(db, project_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this project has no active estimate version to invoice from",
        )
    ep = version["payload"] or {}
    line_items = _line_items_from_estimate(ep)
    if not line_items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the active estimate carries no billable amount",
        )
    payload = {
        "date": date.today().isoformat(),
        "description": f"Work as per estimate {version['reference'] or ''}".strip(),
        "estimateRef": version["reference"],
        "notes": _scope_notes(ep, version["reference"]),
        "lineItems": line_items,
        "sourceEstimate": {
            "versionId": str(version["id"]),
            "versionNumber": version["version_number"],
            "reference": version["reference"],
        },
    }
    _compute_totals(type, payload, _f(ep.get("subtotal")))
    try:
        est_subtotal = round(float(ep.get("subtotal") or 0), 2)
    except (TypeError, ValueError):
        est_subtotal = 0.0
    return {
        "payload": payload,
        "estimate_reference": version["reference"],
        "estimate_version_number": version["version_number"],
        "estimate_subtotal": est_subtotal,
        "matches_estimate_subtotal": abs(payload["subtotal"] - est_subtotal) < 0.01,
    }


@router.get("/seed-from-change-order", response_model=ChangeOrderInvoiceSeed)
def seed_invoice_from_change_order(
    change_order_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Propose a change-order invoice payload from a ChangeOrder row.

    This proposes; it does not create. Same contract as seed-from-estimate —
    the numbers are money, so a person reads them in the form and presses
    create. Nothing is written here.
    """
    from app.models.change_order import ChangeOrder

    co = db.get(ChangeOrder, change_order_id)
    if co is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="change order not found"
        )
    assert_project_access(db, user, co.project_id)
    if co.status == "void":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this change order is void and cannot be billed",
        )

    detail = co.detail or {}
    raw_items = [i for i in (detail.get("line_items") or []) if isinstance(i, dict)]
    work = _change_order_work(raw_items)

    # One lump-sum line, matching how the change order itself is priced to the
    # client. Derived from the breakdown when there is one so the bill and the
    # signed change order cannot disagree; the stored cost_impact otherwise.
    subtotal = (
        round(sum(_f(i.get("amount")) for i in raw_items), 2)
        if raw_items
        else round(float(co.cost_impact or 0), 2)
    )
    if subtotal == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this change order carries no billable amount",
        )
    reference = f"CO-{co.number}"
    line_items = [
        {"description": f"{reference} — {co.title} (lump sum)", "amount": subtotal}
    ]
    payload = {
        "date": date.today().isoformat(),
        "description": co.description or co.title,
        "changeOrderNumber": reference,
        "reason": co.title,
        "notes": _change_order_notes(detail, work),
        "lineItems": line_items,
        "sourceChangeOrder": {
            "changeOrderId": str(co.id),
            "number": co.number,
            "reference": reference,
        },
    }
    _compute_totals("change_order", payload)

    co_subtotal = round(float(co.cost_impact or 0), 2)
    warnings: list[str] = []
    if co.status != "approved":
        warnings.append(
            f"{reference} is {co.status}, not approved. Billing unapproved extra work is "
            "a commercial decision — check before you send this."
        )
    if abs(payload["subtotal"] - co_subtotal) >= 0.01:
        warnings.append(
            f"The billed subtotal does not match the change order's cost impact "
            f"({_fmt_money(payload['subtotal'])} vs {_fmt_money(co_subtotal)})."
        )
    return {
        "payload": payload,
        "change_order_reference": reference,
        "change_order_status": co.status,
        "change_order_subtotal": co_subtotal,
        "matches_change_order": abs(payload["subtotal"] - co_subtotal) < 0.01,
        "warnings": warnings,
    }


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_invoice(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Invoice:
    return _invoice_for_user(db, invoice_id, user)


@router.get("/{invoice_id}/share-link")
def invoice_share_link(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """A signed token for the public client invoice-view link."""
    invoice = _invoice_for_user(db, invoice_id, user)
    return {"token": sign_invoice_token(str(invoice.id))}



_CONTINUATION = re.compile(r"^[a-z(,\"']")
_DANGLING = re.compile(r"(\s(if|and|or|with|at|to|for|the|a|per|from|in|on)\s*$)|([/,(-]\s*$)")


def _tidy_items(raw: list[str]) -> list[str]:
    """Repair estimate item text before it goes in front of a client.

    The 2026-08-31 recovery split long descriptions at a fixed width, so a line
    can be the tail of the one above it, and a few lost their tail entirely.
    Continuations are rejoined; a line whose tail is gone is trimmed back to its
    last complete clause. Nothing is invented.
    """
    joined: list[str] = []
    for item in raw:
        item = (item or "").strip()
        if not item:
            continue
        if joined and _CONTINUATION.match(item):
            joined[-1] = joined[-1].rstrip() + " " + item
        else:
            joined.append(item)

    out: list[str] = []
    for item in joined:
        if item.count("(") > item.count(")"):
            item = item[: item.rfind("(")].rstrip(" ,;-")
        elif _DANGLING.search(item):
            for sep in (";", ",", " ("):
                cut = item.rfind(sep)
                if cut > 40:
                    item = item[:cut].rstrip(" ,;(-")
                    break
            else:
                item = item.rstrip(" ,;(/-")
        out.append(item)
    return redact_internal_pricing(out)


def _scope_notes(payload: dict, reference: str | None) -> str:
    """Build the scope-of-work text that goes in the invoice Notes field.

    Line items carry the money; Notes carries what was actually done. On
    estimates recovered from the data loss the per-line costs are gone but the
    described work survived, so this is the only place the detail can appear.
    """
    blocks: list[str] = []
    for cat in payload.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        name = (cat.get("name") or "").strip()
        if not name or name.lower().startswith("lump-sum price as issued"):
            continue
        items = _tidy_items(
            [(i.get("description") or i.get("name") or "") if isinstance(i, dict) else str(i)
             for i in (cat.get("items") or [])]
        )
        # a category that only says "no work in scope" belongs on an estimate,
        # not on an invoice for work performed
        items = [i for i in items if not re.match(r"^\s*(no|nil)\b", i, re.I)]
        if not items:
            continue
        blocks.append(name + "\n" + "\n".join(f"  - {i}" for i in items))

    if not blocks:
        return ""
    head = f"Scope of work as per estimate {reference}." if reference else "Scope of work."
    return head + "\n\n" + "\n\n".join(blocks)


# --- seeding an invoice from the ACTIVE estimate -----------------------------

# Only these invoice types derive from the estimate. A change order bills work
# *beyond* the estimated scope by definition, so it is created independently and
# is deliberately exempt from the active-estimate rule below.
ESTIMATE_DERIVED_TYPES = {"progress", "tm", "retainer"}


def _active_estimate_version(db: Session, project_id: UUID) -> dict | None:
    """The project's single active estimate version, or None.

    `uq_estimate_versions_active_per_project` is a partial unique index, so at
    most one row per project can be active. That guarantee is what makes
    "invoice from the active estimate" well defined.
    """
    row = db.execute(
        text(
            "SELECT id, version_number, reference, label, payload "
            "FROM estimate_versions WHERE project_id = :p AND is_active = true"
        ),
        {"p": str(project_id)},
    ).mappings().first()
    return dict(row) if row else None


def _contract_basis(db: Session, project_id: UUID, payload: dict) -> float | None:
    """What a "% of contract" line bills against: the estimate SUBTOTAL, pre-GST.

    Prefer the version the invoice was seeded from, so an invoice keeps billing
    against the contract it was written for even after a later version is made
    active. Fall back to the active version for an invoice typed from scratch.
    Returns None when there is nothing to take a percentage of; the caller turns
    that into a refusal rather than a zero.
    """
    src = payload.get("sourceEstimate")
    if isinstance(src, dict) and src.get("versionId"):
        row = db.execute(
            text("SELECT payload FROM estimate_versions WHERE id = :v"),
            {"v": str(src["versionId"])},
        ).mappings().first()
        if row:
            sub = _f((row["payload"] or {}).get("subtotal"))
            if sub:
                return sub

    version = _active_estimate_version(db, project_id)
    if version:
        sub = _f((version["payload"] or {}).get("subtotal"))
        if sub:
            return sub
    return None


def _refuse_zero_total(payload: dict) -> None:
    """An invoice with line items and a zero total is always a mistake.

    This is the backstop, not the fix. INV-2026-10059 was created with one line
    item reading "20% of contract" and a total of 0.00, took a number out of the
    sequence, and looked like a real invoice. Whatever else silently evaluates
    to nothing in future, it stops here instead of reaching a client.
    """
    items = payload.get("lineItems") or []
    # Checks the SUBTOTAL, not the total. An adjustment is added after GST, so a
    # non-zero adjustment on top of unpriced line items would otherwise produce
    # a non-zero total and walk straight past this guard. It also leaves an
    # adjustment-only invoice legal, which it should be: an invoice that exists
    # purely to collect GST missed earlier carries no line items at all.
    if items and not _f(payload.get("subtotal")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"this invoice has {len(items)} line item(s) but totals 0.00 - "
                f"every line is blank or unpriced. Enter an amount, or a "
                f"percentage of a contract that has an active estimate."
            ),
        )


def _fmt_money(n: float) -> str:
    return f"${n:,.2f}"


def _change_order_work(raw: list[dict]) -> list[str]:
    """The work descriptions behind a change order, cleaned for a client.

    Descriptions only — the per-line money stays internal. A change order is
    priced to the client as a LUMP SUM (see change_order_pdf), and an invoice
    that re-itemises it would publish on the bill exactly the breakdown the
    change order deliberately withheld. The quantities survive inside the
    descriptions ("100 LF x 8' high"), so the scope is still checkable.
    """
    from app.services.client_text import PRICING_CLAUSE

    out: list[str] = []
    for it in raw:
        desc = PRICING_CLAUSE.sub("", str(it.get("description") or "")).rstrip(" ,;:-\u2014\u2013")
        if desc:
            out.append(desc)
    return out


def _change_order_notes(detail: dict, work: list[str]) -> str:
    """What the lump sum covers, and what it does not, for the Notes field.

    Same role as _scope_notes on an estimate-seeded invoice: the line carries
    the money, Notes carries the substance. With lump-sum pricing this is the
    only place the work appears, so it leads.
    """
    blocks: list[str] = []
    if work:
        blocks.append("Work included" + "\n" + "\n".join(f"  - {i}" for i in work))
    for title, key in (("Assumptions", "assumptions"), ("Not included", "exclusions")):
        items = redact_internal_pricing(
            [str(i) for i in (detail.get(key) or []) if str(i).strip()]
        )
        if items:
            blocks.append(title + "\n" + "\n".join(f"  - {i}" for i in items))
    return "\n\n".join(blocks)


def _line_items_from_estimate(payload: dict) -> list[dict]:
    """Client-facing line items from an estimate payload.

    Overhead and profit are never emitted as lines. BRIO estimates show the
    client subtotal, GST and total only, so the lines must sum to the estimate
    subtotal with the margin already inside the numbers.
    """
    items: list[dict] = []
    for cat in payload.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        total = cat.get("total")
        if total in (None, ""):
            continue
        try:
            amount = round(float(total), 2)
        except (TypeError, ValueError):
            continue
        if amount == 0:
            continue
        items.append({"description": cat.get("name") or "Work as estimated", "amount": amount})

    # A lump-sum estimate carries no per-category totals. Bill it as one line
    # rather than inventing a breakdown the estimate never had.
    if not items:
        try:
            subtotal = round(float(payload.get("subtotal") or 0), 2)
        except (TypeError, ValueError):
            subtotal = 0.0
        if subtotal:
            items = [{"description": "Work as per estimate (lump sum)", "amount": subtotal}]
    return items


def _replay(db: Session, key: str, project_id: UUID) -> Invoice | None:
    """The invoice a previous POST with this key already created, if any.

    Refuses across projects rather than returning someone else's invoice. The
    key is a client-generated UUID, so a cross-project hit is a client bug, and
    a loud 409 beats quietly attaching this project's retry to another one.
    """
    existing = db.execute(
        select(Invoice).where(Invoice.idempotency_key == key)
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this idempotency key already belongs to a different project",
        )
    return existing


# Writes are office-only (2026-10-13). They checked project membership but not
# role, so a client on the project could create, edit, send, pay or delete.
@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create_invoice(
    body: InvoiceCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Invoice:
    assert_project_access(db, user, body.project_id)

    # Replay before doing any work. An invoice number is drawn from a sequence
    # and a retry that creates a second one leaves a hole in the billing record
    # with nothing in it to explain the gap.
    if body.idempotency_key:
        prior = _replay(db, body.idempotency_key, body.project_id)
        if prior is not None:
            # 200, not 201: nothing was created this time, and the caller that
            # retried because it never saw the first response deserves to be
            # told which of the two happened.
            response.status_code = status.HTTP_200_OK
            return prior

    payload = dict(body.payload or {})
    payload.setdefault("date", date.today().isoformat())

    # If this invoice was seeded from an estimate, that estimate must still be
    # the active one. Estimates get superseded; an invoice built from a version
    # that has since been replaced bills a price the client never agreed to.
    src = payload.get("sourceEstimate")
    if body.type in ESTIMATE_DERIVED_TYPES and isinstance(src, dict) and src.get("versionId"):
        active = _active_estimate_version(db, body.project_id)
        if active is None or str(active["id"]) != str(src["versionId"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "the estimate this invoice was built from is no longer the active "
                    "version — reseed from the current estimate before invoicing"
                ),
            )

    try:
        _compute_totals(body.type, payload, _contract_basis(db, body.project_id, payload))
    except PercentageWithoutBasis as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    _refuse_zero_total(payload)

    invoice = Invoice(
        project_id=body.project_id,
        number=_next_invoice_number(db),
        type=body.type,
        payload=payload,
        status="draft",
        idempotency_key=body.idempotency_key,
    )
    db.add(invoice)
    _log(
        db,
        project_id=body.project_id,
        user=user,
        action="invoice.create",
        number=invoice.number,
        type=body.type,
        total=payload.get("total"),
    )
    try:
        db.commit()
    except IntegrityError:
        # Two requests carrying the same key raced past the replay check above
        # - a double submit, or a retry arriving while the first is still in
        # flight. The unique index is what actually makes this safe; the lookup
        # is only the fast path. Return the one that won.
        db.rollback()
        if not body.idempotency_key:
            raise
        prior = _replay(db, body.idempotency_key, body.project_id)
        if prior is None:
            raise
        response.status_code = status.HTTP_200_OK
        return prior
    db.refresh(invoice)
    return invoice


@router.patch("/{invoice_id}", response_model=InvoiceRead)
def update_invoice(
    invoice_id: UUID,
    body: InvoicePatch,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Invoice:
    invoice = _invoice_for_user(db, invoice_id, user)
    # Guard, not style: `dict(body.payload or {})` on a status-only patch would
    # assign an empty payload and erase the invoice's line items and totals.
    if body.payload is not None:
        payload = dict(body.payload)
        try:
            _compute_totals(
                invoice.type, payload, _contract_basis(db, invoice.project_id, payload)
            )
        except PercentageWithoutBasis as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        _refuse_zero_total(payload)
        invoice.payload = payload
    if body.status is not None and body.status != invoice.status:
        _log(
            db,
            project_id=invoice.project_id,
            user=user,
            action="invoice.status",
            number=invoice.number,
            **{"from": invoice.status, "to": body.status},
        )
        invoice.status = body.status
        if body.status in ("sent", "overdue", "partial", "paid") and invoice.sent_at is None:
            invoice.sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(invoice)
    return invoice



# Returns 200 with a small body rather than 204. FastAPI asserts that a 204 route
# must not be able to return a body, and that assertion fires on the inferred
# response model regardless of response_class. A 200 matches the rest of this
# codebase and avoids the trap entirely.
@router.delete("/{invoice_id}")
def delete_invoice(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Delete a DRAFT invoice. Issued invoices are never deletable.

    Amir's rule, 2026-09-10: once an invoice is issued it cannot be deleted.
    An issued invoice has been seen by a client and owns a number in a sequence;
    removing it leaves a hole with no record of why. Drafts have been seen by
    nobody, so they are safe to remove.

    Also refused when payments exist, which should be impossible on a draft but
    is checked rather than assumed.
    """
    invoice = _invoice_for_user(db, invoice_id, user)

    if invoice.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"only draft invoices can be deleted; this one is {invoice.status}",
        )
    paid = db.execute(
        select(func.count()).select_from(Payment).where(Payment.invoice_id == invoice.id)
    ).scalar_one()
    if paid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"invoice has {paid} recorded payment(s) and cannot be deleted",
        )

    _log(db, project_id=invoice.project_id, user=user,
         action="invoice.delete", number=invoice.number, status=invoice.status)
    number = invoice.number
    db.delete(invoice)
    db.commit()
    return {"ok": True, "deleted": number}

@router.get("/{invoice_id}/payments", response_model=list[PaymentRead])
def list_payments(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Payment]:
    _invoice_for_user(db, invoice_id, user)
    stmt = (
        select(Payment)
        .where(Payment.invoice_id == invoice_id)
        .order_by(Payment.paid_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post("/{invoice_id}/payments", response_model=InvoiceRead)
def record_payment(
    invoice_id: UUID,
    body: PaymentCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Invoice:
    invoice = _invoice_for_user(db, invoice_id, user)
    payment = Payment(
        invoice_id=invoice.id,
        amount=body.amount,
        method=body.method,
        notes=body.notes,
    )
    if body.paid_at is not None:
        payment.paid_at = body.paid_at
    db.add(payment)
    db.flush()
    _recompute_payment_state(db, invoice)
    _log(
        db,
        project_id=invoice.project_id,
        user=user,
        action="invoice.payment",
        number=invoice.number,
        amount=str(body.amount),
        method=body.method,
        new_status=invoice.status,
    )
    db.commit()
    db.refresh(invoice)
    return invoice


@router.post("/{invoice_id}/pdf", response_model=InvoicePdfResult)
def generate_invoice_pdf(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> InvoicePdfResult:
    invoice = _invoice_for_user(db, invoice_id, user)
    pdf, doc = _render_and_store(db, invoice, user)
    db.commit()
    db.refresh(doc)
    return InvoicePdfResult(
        document_id=doc.id,
        filename=doc.filename,
        size_bytes=len(pdf),
        download_url=presign_get(doc.r2_key),
    )


@router.post("/{invoice_id}/send", response_model=InvoiceSendResult)
def send_invoice(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> InvoiceSendResult:
    """Render the invoice PDF and email it to the client via SES."""
    invoice = _invoice_for_user(db, invoice_id, user)

    project_dict, client_dict = _project_and_client(db, invoice.project_id)
    to_email = client_dict.get("email")
    if not to_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client has no email address on file",
        )

    # Status first, THEN render. The PDF stamps its badge from invoice.status,
    # so rendering before the flip emailed the client a document marked DRAFT
    # while the dashboard showed the invoice as sent. A client who receives a
    # draft can reasonably treat it as not yet payable.
    if invoice.status == "draft":
        invoice.status = "sent"
    if invoice.sent_at is None:
        invoice.sent_at = datetime.now(timezone.utc)
        db.flush()

    pdf, doc = _render_and_store(db, invoice, user)

    total = invoice.payload.get("total")
    body_text = (
        f"Hello {client_dict.get('name') or 'there'},\n\n"
        f"Please find attached invoice {invoice.number} for "
        f"{project_dict.get('name') or 'your project'}"
        + (f", total ${total}." if total else ".")
        + "\n\n"
        + (invoice.payload.get("notes") or "")
        + f"\n\nThank you,\nBuilt By Brio\n{COMPANY['email']} · {COMPANY['phone']}"
    )
    try:
        send_email(
            to=to_email,
            subject=f"Invoice {invoice.number} — Built By Brio",
            body_text=body_text,
            attachment=(f"{invoice.number}.pdf", pdf, "application/pdf"),
        )
    except Exception as exc:
        # Nothing is committed yet, so this un-sends the status change and the
        # Document row together. An invoice marked sent that never left the
        # building is worse than a failed send you can retry.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"invoice not sent: {exc}",
        ) from exc

    _log(
        db,
        project_id=invoice.project_id,
        user=user,
        action="invoice.send",
        number=invoice.number,
        sent_to=to_email,
    )
    db.commit()
    db.refresh(doc)
    return InvoiceSendResult(
        sent_to=to_email, document_id=doc.id, status=invoice.status
    )
