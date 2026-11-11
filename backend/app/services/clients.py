"""Client / CRM service layer.

Ports the JSON registry helpers from
``internal-ops/brio-admin/lib/registry.mjs`` into Postgres / SQLAlchemy.

Pure normalization helpers (``normalize_name`` / ``_email`` / ``_phone`` /
``_address``) match the JS reference exactly so that the brio-admin
``_index.json`` seed data can be imported without re-deduping.

Find-or-create match priority is **email > phone > name** — first hit wins.
On a hit, any new email / phone / address from the payload is appended to the
matched client's arrays (deduped by the normalized form). On a miss, a fresh
client row is inserted and returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.document import Document
from app.models.estimate import Estimate
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.project import Project


# ── Normalization ─────────────────────────────────────────────────────────
# These four functions MUST stay byte-identical with the JS reference at
# brio-admin/lib/registry.mjs. Don't add quirks; if you find a divergence,
# fix the JS side first then mirror it here.


def normalize_name(s: Optional[str]) -> str:
    """Lowercase, trim, collapse internal whitespace."""

    if not s:
        return ""
    return re.sub(r"\s+", " ", s.lower().strip())


def normalize_email(s: Optional[str]) -> str:
    """Lowercase + trim. Mirrors ``normalizeEmail`` in registry.mjs."""

    if not s:
        return ""
    return s.lower().strip()


_NON_DIGIT = re.compile(r"\D")


def normalize_phone(s: Optional[str]) -> str:
    """Strip everything that is not a digit."""

    if not s:
        return ""
    return _NON_DIGIT.sub("", s)


_ADDR_PUNCT = re.compile(r"[#.,]")
_ADDR_UNIT_WORD = re.compile(r"\bunit\b")
_ADDR_WHITESPACE = re.compile(r"\s+")


def normalize_address(s: Optional[str]) -> str:
    """Lowercase, drop ``# . ,``, drop the literal word ``unit``, collapse ws."""

    if not s:
        return ""
    out = s.lower()
    out = _ADDR_PUNCT.sub(" ", out)
    out = _ADDR_UNIT_WORD.sub("", out)
    out = _ADDR_WHITESPACE.sub(" ", out)
    return out.strip()


# ── Find-or-create ────────────────────────────────────────────────────────


@dataclass
class FindOrCreateResult:
    client: Client
    created: bool


def _sync_singular_mirrors(client: Client) -> None:
    """Keep the legacy singular columns aligned with arrays[0]."""

    client.email = client.emails[0] if client.emails else None
    client.phone = client.phones[0] if client.phones else None
    client.address = client.addresses[0] if client.addresses else None


def _append_unique(
    raw_value: Optional[str],
    normalized: str,
    raws: list[str],
    normalizeds: list[str],
) -> bool:
    """Append ``raw_value`` to ``raws`` iff ``normalized`` is new.

    Returns True if the lists were mutated. The arrays stay in lockstep:
    ``raws[i]`` is the user-facing form of ``normalizeds[i]``.
    """

    if not normalized:
        return False
    if normalized in normalizeds:
        return False
    raws.append(raw_value or "")
    normalizeds.append(normalized)
    return True


def find_or_create_client(
    db: Session,
    *,
    name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    today: Optional[date] = None,
) -> FindOrCreateResult:
    """Match an existing client by email > phone > name, or insert a new one.

    On match, merges any new contact info into the matched record and bumps
    ``last_seen``. On miss, inserts a fresh row with ``first_seen = last_seen
    = today``.
    """

    today = today or datetime.utcnow().date()
    n_name = normalize_name(name)
    n_email = normalize_email(email)
    n_phone = normalize_phone(phone)
    n_addr = normalize_address(address)

    match: Optional[Client] = None

    if n_email:
        match = db.execute(
            select(Client).where(Client.normalized_emails.any(n_email)).limit(1)
        ).scalar_one_or_none()

    if match is None and n_phone:
        match = db.execute(
            select(Client).where(Client.normalized_phones.any(n_phone)).limit(1)
        ).scalar_one_or_none()

    if match is None and n_name:
        match = db.execute(
            select(Client).where(Client.normalized_name == n_name).limit(1)
        ).scalar_one_or_none()

    if match is not None:
        mutated = False
        # Copy the arrays so SQLAlchemy detects the change (mutating in place
        # on a JSON/ARRAY column is not auto-flagged dirty).
        emails = list(match.emails or [])
        n_emails = list(match.normalized_emails or [])
        phones = list(match.phones or [])
        n_phones = list(match.normalized_phones or [])
        addresses = list(match.addresses or [])
        n_addresses = list(match.normalized_addresses or [])

        if _append_unique(email, n_email, emails, n_emails):
            mutated = True
        if _append_unique(phone, n_phone, phones, n_phones):
            mutated = True
        if _append_unique(address, n_addr, addresses, n_addresses):
            mutated = True

        if mutated:
            match.emails = emails
            match.normalized_emails = n_emails
            match.phones = phones
            match.normalized_phones = n_phones
            match.addresses = addresses
            match.normalized_addresses = n_addresses
            _sync_singular_mirrors(match)

        match.last_seen = today
        db.flush()
        return FindOrCreateResult(client=match, created=False)

    # Miss → insert.
    client = Client(
        name=name or "",
        normalized_name=n_name,
        emails=[email] if n_email else [],
        normalized_emails=[n_email] if n_email else [],
        phones=[phone] if n_phone else [],
        normalized_phones=[n_phone] if n_phone else [],
        addresses=[address] if n_addr else [],
        normalized_addresses=[n_addr] if n_addr else [],
        tags=[],
        first_seen=today,
        last_seen=today,
    )
    _sync_singular_mirrors(client)
    db.add(client)
    db.flush()
    return FindOrCreateResult(client=client, created=True)


def mark_as_client(db: Session, client_id: Optional[UUID]) -> None:
    """A prospect who is picked for a project, or entered as a client, becomes
    one. Same row, so their CRM history and opportunities come with them."""
    if client_id is None:
        return
    c = db.get(Client, client_id)
    if c is not None and c.kind != "client":
        c.kind = "client"


# ── Search ────────────────────────────────────────────────────────────────


def search_clients(
    db: Session, q: Optional[str], limit: int = 100, kind: Optional[str] = "client",
) -> list[Client]:
    """Substring search across name + normalized arrays.

    The search is intentionally permissive — anything the user types is
    normalized through every form (name / email / phone / address) and we
    match if any normalized array contains a row that contains the query.
    Postgres ``unnest`` + ``ILIKE`` keeps this expressible without a custom
    GIN trigram index for the V1 dataset (~tens of thousands of rows).
    """

    stmt = select(Client).order_by(Client.name)
    # Clients and CRM prospects share this table (one identity per firm), but
    # the Clients page and the project picker must only ever see real clients:
    # 4,857 permit-data prospects were burying the two that exist.
    if kind:
        stmt = stmt.where(Client.kind == kind)
    if q:
        like = f"%{q.strip().lower()}%"
        n_phone = normalize_phone(q)

        # Build the OR clause: substring match on normalized_name OR
        # any element of any normalized array contains the query.
        conds = [Client.normalized_name.ilike(like)]
        # ARRAY substring matching: rely on array_to_string for portable
        # substring search across the elements.
        conds.append(func.array_to_string(Client.normalized_emails, " ").ilike(like))
        conds.append(func.array_to_string(Client.normalized_addresses, " ").ilike(like))
        if n_phone:
            conds.append(
                func.array_to_string(Client.normalized_phones, " ").ilike(f"%{n_phone}%")
            )
        else:
            conds.append(func.array_to_string(Client.normalized_phones, " ").ilike(like))
        stmt = stmt.where(or_(*conds))

    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars().all())


# ── Roll-ups ──────────────────────────────────────────────────────────────


@dataclass
class ClientRollup:
    projects: list[Project]
    documents: list[Document]
    invoices: list[Invoice]
    payments: list[Payment]
    total_billed: Decimal
    total_paid: Decimal
    total_outstanding: Decimal
    last_activity: Optional[date]
    project_contract_values: dict[UUID, Decimal]


def _invoice_total(inv: Invoice) -> Decimal:
    """Pull the pre-tax + GST total out of the invoice payload, falling back
    to ``subtotal`` if ``total`` is missing."""

    payload = inv.payload or {}
    for key in ("total", "subtotal"):
        value = payload.get(key)
        if value is not None:
            try:
                return Decimal(str(value))
            except Exception:
                continue
    return Decimal("0")


def _estimate_total(est: Estimate) -> Decimal:
    payload = est.payload or {}
    for key in ("total", "subtotal"):
        value = payload.get(key)
        if value is not None:
            try:
                return Decimal(str(value))
            except Exception:
                continue
    return Decimal("0")


def rollup_for_client(db: Session, client_id: UUID) -> ClientRollup:
    """Walks all projects + descendants for one client, totals up money,
    finds the last-activity date. Used by ``GET /clients/{id}``."""

    projects = list(
        db.execute(
            select(Project).where(Project.client_id == client_id).order_by(Project.created_at.desc())
        ).scalars().all()
    )

    project_ids = [p.id for p in projects]
    documents: list[Document] = []
    invoices: list[Invoice] = []
    estimates: list[Estimate] = []
    payments: list[Payment] = []

    if project_ids:
        documents = list(
            db.execute(
                select(Document).where(Document.project_id.in_(project_ids)).order_by(Document.created_at.desc())
            ).scalars().all()
        )
        invoices = list(
            db.execute(
                select(Invoice).where(Invoice.project_id.in_(project_ids)).order_by(Invoice.created_at.desc())
            ).scalars().all()
        )
        estimates = list(
            db.execute(
                select(Estimate).where(Estimate.project_id.in_(project_ids)).order_by(Estimate.created_at.desc())
            ).scalars().all()
        )
        invoice_ids = [inv.id for inv in invoices]
        if invoice_ids:
            payments = list(
                db.execute(
                    select(Payment).where(Payment.invoice_id.in_(invoice_ids)).order_by(Payment.paid_at.desc())
                ).scalars().all()
            )

    # Contract value per project = total of the most recent estimate.
    contract_values: dict[UUID, Decimal] = {}
    for est in estimates:
        # estimates are ordered desc — first one we see for a project wins.
        contract_values.setdefault(est.project_id, _estimate_total(est))

    total_billed = sum((_invoice_total(inv) for inv in invoices), Decimal("0"))
    total_paid = sum((p.amount for p in payments), Decimal("0"))
    total_outstanding = total_billed - total_paid

    candidates: list[date] = []
    for inv in invoices:
        if inv.created_at:
            candidates.append(inv.created_at.date())
    for doc in documents:
        if doc.created_at:
            candidates.append(doc.created_at.date())
    for proj in projects:
        if proj.created_at:
            candidates.append(proj.created_at.date())
    last_activity = max(candidates) if candidates else None

    return ClientRollup(
        projects=projects,
        documents=documents,
        invoices=invoices,
        payments=payments,
        total_billed=total_billed,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
        last_activity=last_activity,
        project_contract_values=contract_values,
    )


# ── Tags ──────────────────────────────────────────────────────────────────


def add_tag(db: Session, client: Client, tag: str) -> Client:
    tag = tag.strip()
    if not tag:
        return client
    tags = list(client.tags or [])
    if tag in tags:
        return client
    tags.append(tag)
    client.tags = tags
    db.flush()
    return client


def remove_tag(db: Session, client: Client, tag: str) -> Client:
    tags = [t for t in (client.tags or []) if t != tag]
    client.tags = tags
    db.flush()
    return client


# ── Merge ─────────────────────────────────────────────────────────────────


def merge_clients(db: Session, *, source_id: UUID, target_id: UUID) -> Client:
    """Move all of ``source``'s state into ``target`` and delete ``source``.

    - Project / Document / Invoice FK rows that point at the source are
      re-pointed at the target.
    - Source's emails / phones / addresses / tags are appended to target's
      arrays, deduped by the normalized form.
    - ``first_seen`` becomes the older of the two; ``last_seen`` becomes the
      newer.
    - ``notes`` are concatenated.
    """

    if source_id == target_id:
        raise ValueError("cannot merge a client into itself")

    source = db.get(Client, source_id)
    target = db.get(Client, target_id)
    if source is None:
        raise LookupError(f"source client {source_id} not found")
    if target is None:
        raise LookupError(f"target client {target_id} not found")

    # Move all projects.
    db.execute(
        Project.__table__.update()
        .where(Project.client_id == source.id)
        .values(client_id=target.id)
    )

    # Merge arrays.
    emails = list(target.emails or [])
    n_emails = list(target.normalized_emails or [])
    for raw, n in zip(source.emails or [], source.normalized_emails or []):
        _append_unique(raw, n, emails, n_emails)

    phones = list(target.phones or [])
    n_phones = list(target.normalized_phones or [])
    for raw, n in zip(source.phones or [], source.normalized_phones or []):
        _append_unique(raw, n, phones, n_phones)

    addresses = list(target.addresses or [])
    n_addresses = list(target.normalized_addresses or [])
    for raw, n in zip(source.addresses or [], source.normalized_addresses or []):
        _append_unique(raw, n, addresses, n_addresses)

    tags = list(target.tags or [])
    for tag in source.tags or []:
        if tag and tag not in tags:
            tags.append(tag)

    target.emails = emails
    target.normalized_emails = n_emails
    target.phones = phones
    target.normalized_phones = n_phones
    target.addresses = addresses
    target.normalized_addresses = n_addresses
    target.tags = tags

    # Dates.
    if source.first_seen and (target.first_seen is None or source.first_seen < target.first_seen):
        target.first_seen = source.first_seen
    if source.last_seen and (target.last_seen is None or source.last_seen > target.last_seen):
        target.last_seen = source.last_seen

    # Notes.
    if source.notes:
        target.notes = (
            f"{target.notes}\n\n[merged from {source.id}]\n{source.notes}"
            if target.notes
            else source.notes
        )

    _sync_singular_mirrors(target)

    db.delete(source)
    db.flush()
    return target


# ── Listing helpers ───────────────────────────────────────────────────────


def primary_email(client: Client) -> Optional[str]:
    return client.emails[0] if client.emails else client.email


def primary_phone(client: Client) -> Optional[str]:
    return client.phones[0] if client.phones else client.phone


def project_count(db: Session, client_id: UUID) -> int:
    return int(
        db.execute(
            select(func.count(Project.id)).where(Project.client_id == client_id)
        ).scalar_one()
    )


def last_activity_for(db: Session, client_id: UUID) -> Optional[date]:
    """Cheap last-activity lookup for the list view (one query, no rollup)."""

    rows: Iterable[Optional[datetime]] = db.execute(
        select(func.max(Project.updated_at)).where(Project.client_id == client_id)
    ).scalars().all()
    for ts in rows:
        if ts is not None:
            return ts.date()
    return None
