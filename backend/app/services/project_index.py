"""Render the per-project document index as Markdown + cache it as a
`project_index` Document row in R2.

Layout (by-category, matching the Documents UI order):

  # Project Index — {Project Name}

  - Address, type, sqft
  - Indexed N of M documents · last indexed YYYY-MM-DD HH:MM
  - Document categories present

  ## Drawings
  ### A1.04 — Floor Plan, Level 2
  - one paragraph summary
  - structured facts as a short bullet list
  - file pointer (id + size + upload date)
  ...

  ## Contracts
  ...

The MD is regenerated whenever any DocumentSummary in the project gets
a later updated_at than the cached project_index Document. Stale rebuild
is automatic; there's no explicit invalidation step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.document import Document
from app.models.document_summary import DocumentSummary
from app.models.project import Project
from app.services.r2 import object_exists, presign_get, put_bytes


# Display order mirrors the Documents page sidebar — drawings first
# because they're the largest signal for downstream AI tools.
_CATEGORY_DISPLAY_ORDER: list[tuple[str, str]] = [
    ("drawing", "Drawings"),
    ("contract", "Contracts"),
    ("scope", "Scope Documents"),
    ("report", "Reports (SMP / Geotech / Hazmat / Inspections)"),
    ("permit", "Permits"),
    ("photo", "Photos"),
    ("client_note", "Client Notes"),
    ("invoice_legacy", "Legacy Invoices"),
    ("project_index", "Index (self)"),
    ("scope_pdf", "Generated Scope PDFs"),
    ("other", "Other"),
]


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _ul(items: list[Any], indent: int = 0) -> str:
    pad = "  " * indent
    out: list[str] = []
    for it in items:
        text = str(it).strip()
        if not text:
            continue
        out.append(f"{pad}- {text}")
    return "\n".join(out)


def _render_structured_block(
    category: str, fields: dict[str, Any]
) -> str:
    """Compact key-value facts, formatted per category."""
    if not isinstance(fields, dict):
        return ""

    def kv(label: str, key: str) -> str | None:
        val = fields.get(key)
        if val in (None, "", [], {}):
            return None
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val if str(v).strip())
            if not val:
                return None
        return f"- **{label}:** {val}"

    rows: list[str] = []

    if category == "drawing":
        for label, key in [
            ("Discipline", "discipline"),
            ("Sheet", "sheet_number"),
            ("Title", "sheet_title"),
            ("Revision", "revision"),
            ("Scale", "scale"),
            ("Date", "date"),
            ("Drawn by", "drawn_by"),
            ("Approved by", "approved_by"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        feats = fields.get("key_features") or []
        if feats:
            rows.append("- **Key features:** " + "; ".join(str(f) for f in feats))
        rooms = fields.get("rooms") or []
        if rooms:
            rows.append("- **Rooms:** " + ", ".join(str(r) for r in rooms))

    elif category == "contract":
        ct = fields.get("contract_type")
        if ct:
            rows.append(f"- **Type:** {ct}")
        parties = fields.get("parties") or {}
        if isinstance(parties, dict):
            party_bits = []
            if parties.get("contractor"):
                party_bits.append(f"Contractor: {parties['contractor']}")
            if parties.get("owner"):
                party_bits.append(f"Owner: {parties['owner']}")
            for other in parties.get("other") or []:
                party_bits.append(str(other))
            if party_bits:
                rows.append("- **Parties:** " + "; ".join(party_bits))
        val = fields.get("contract_value")
        cur = fields.get("currency") or "CAD"
        if val is not None:
            try:
                rows.append(f"- **Value:** {cur} ${float(val):,.2f}")
            except (TypeError, ValueError):
                rows.append(f"- **Value:** {val}")
        for label, key in [
            ("Start", "start_date"),
            ("Substantial completion", "completion_date"),
            ("Payment terms", "payment_terms"),
            ("Liquidated damages", "liquidated_damages"),
            ("Holdback", "holdback_pct"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        clauses = fields.get("key_clauses") or []
        if clauses:
            rows.append("- **Key clauses:** " + "; ".join(str(c) for c in clauses))

    elif category == "scope":
        for label, key in [
            ("Trade", "trade"),
            ("Version", "version"),
            ("Reference", "reference"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        for label, key in [
            ("Work categories", "work_categories"),
            ("Notable items", "notable_items"),
            ("Key assumptions", "key_assumptions"),
            ("Key exclusions", "key_exclusions"),
        ]:
            vals = fields.get(key) or []
            if vals:
                rows.append(f"- **{label}:** " + "; ".join(str(v) for v in vals))

    elif category == "report":
        for label, key in [
            ("Type", "report_type"),
            ("Issued", "issue_date"),
            ("Author", "author"),
            ("Subject", "subject"),
            ("Risk level", "risk_level"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        for label, key in [
            ("Key findings", "key_findings"),
            ("Recommendations", "recommendations"),
        ]:
            vals = fields.get(key) or []
            if vals:
                rows.append(f"- **{label}:** " + "; ".join(str(v) for v in vals))

    elif category == "permit":
        for label, key in [
            ("Type", "permit_type"),
            ("Number", "permit_number"),
            ("AHJ", "ahj"),
            ("Issued", "issue_date"),
            ("Expires", "expiry"),
            ("Status", "status"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        conditions = fields.get("conditions") or []
        if conditions:
            rows.append("- **Conditions:** " + "; ".join(str(c) for c in conditions))

    elif category == "photo":
        for label, key in [
            ("Location", "location"),
            ("Stage", "before_after"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        for label, key in [
            ("Visible", "visible_elements"),
            ("Notable", "notable_conditions"),
            ("Relevance", "discipline_relevance"),
        ]:
            vals = fields.get(key) or []
            if vals:
                rows.append(f"- **{label}:** " + "; ".join(str(v) for v in vals))

    elif category == "client_note":
        for label, key in [
            ("Topic", "topic"),
            ("Author", "author"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        for label, key in [
            ("Action items", "action_items"),
            ("Decisions", "decisions"),
        ]:
            vals = fields.get(key) or []
            if vals:
                rows.append(f"- **{label}:** " + "; ".join(str(v) for v in vals))

    elif category == "invoice_legacy":
        for label, key in [
            ("Invoice #", "invoice_number"),
            ("Vendor", "vendor"),
            ("Date", "date"),
        ]:
            line = kv(label, key)
            if line:
                rows.append(line)
        total = fields.get("total")
        cur = fields.get("currency") or "CAD"
        if total is not None:
            try:
                rows.append(f"- **Total:** {cur} ${float(total):,.2f}")
            except (TypeError, ValueError):
                rows.append(f"- **Total:** {total}")
        items = fields.get("line_items") or []
        if items:
            rows.append("- **Line items:** " + "; ".join(str(i) for i in items))

    else:  # other / fallback
        for label, key in [("Category guess", "category_guess")]:
            line = kv(label, key)
            if line:
                rows.append(line)
        facts = fields.get("key_facts") or []
        if facts:
            rows.append("- **Key facts:** " + "; ".join(str(f) for f in facts))

    return "\n".join(rows)


def _render_entry(
    doc: Document, summary: DocumentSummary | None
) -> str:
    title = (
        (summary.structured_fields or {}).get("sheet_title")
        if summary and summary.structured_fields
        else None
    )
    sheet_no = (
        (summary.structured_fields or {}).get("sheet_number")
        if summary and summary.structured_fields
        else None
    )
    if sheet_no and title:
        heading = f"### {sheet_no} — {title}"
    elif title:
        heading = f"### {title}"
    else:
        heading = f"### {doc.filename}"

    parts = [heading]

    if summary and summary.summary_text:
        parts.append(summary.summary_text.strip())
    elif summary and summary.status == "queued":
        parts.append("_Queued for indexing — content not yet summarised._")
    elif summary and summary.status == "running":
        parts.append("_Indexing in progress._")
    elif summary and summary.status == "failed":
        parts.append(
            f"_Indexing failed: {summary.error_message or 'unknown error'}_"
        )
    else:
        parts.append("_Not yet indexed._")

    if summary and summary.structured_fields:
        block = _render_structured_block(doc.category, summary.structured_fields)
        if block:
            parts.append(block)

    meta_bits = [f"File: `{doc.filename}`"]
    if doc.size_bytes:
        meta_bits.append(_fmt_bytes(doc.size_bytes))
    meta_bits.append(f"uploaded {_fmt_dt(doc.created_at)}")
    meta_bits.append(f"id `{doc.id}`")
    parts.append("- " + " · ".join(meta_bits))

    return "\n\n".join(parts)


def render_project_index_markdown(db: Session, project_id: UUID) -> tuple[str, dict[str, Any]]:
    """Returns (markdown, metadata). Metadata carries counts + last
    indexed timestamp so callers can surface freshness in the UI."""

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("project not found")

    client = (
        db.get(Client, project.client_id) if project.client_id is not None else None
    )

    docs = list(
        db.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .where(Document.archived == False)  # noqa: E712
            .order_by(Document.category, Document.created_at)
        )
        .scalars()
        .all()
    )

    # Index of summaries by document_id.
    summaries = list(
        db.execute(
            select(DocumentSummary).where(
                DocumentSummary.project_id == project_id
            )
        )
        .scalars()
        .all()
    )
    sum_by_doc = {s.document_id: s for s in summaries}

    # Bucket by category in the canonical display order; spill anything
    # uncategorised into "Other".
    known_cats = {k for k, _ in _CATEGORY_DISPLAY_ORDER}
    buckets: dict[str, list[Document]] = {k: [] for k, _ in _CATEGORY_DISPLAY_ORDER}
    for d in docs:
        if d.category == "project_index":
            continue  # never recurse the index into itself
        if d.category in known_cats:
            buckets[d.category].append(d)
        else:
            buckets["other"].append(d)

    # Freshness metadata
    summarized = sum(1 for s in summaries if s.status == "succeeded")
    pending = sum(1 for s in summaries if s.status in ("queued", "running"))
    failed = sum(1 for s in summaries if s.status == "failed")
    indexed_dts = [s.indexed_at for s in summaries if s.indexed_at]
    last_indexed = max(indexed_dts) if indexed_dts else None

    # ── Markdown assembly ─────────────────────────────────────────────
    project_type = project.type or "—"
    header = [f"# Project Index — {project.name}"]
    header.append("")
    info_lines = [
        f"- **Address:** {project.address or '—'}",
        f"- **Project type:** {project_type}",
        f"- **Approx. area:** {project.sqft:,} sf" if project.sqft else None,
        f"- **Client:** {client.name if client else '—'}",
    ]
    header.append("\n".join(line for line in info_lines if line))
    header.append("")
    header.append(
        f"- **Documents:** {len(docs)} total · {summarized} indexed · "
        f"{pending} pending · {failed} failed"
    )
    header.append(f"- **Last indexed:** {_fmt_dt(last_indexed)}")
    header.append(f"- **Generated:** {_fmt_dt(datetime.now(timezone.utc))}")
    header.append("")

    body_sections: list[str] = []
    for cat, label in _CATEGORY_DISPLAY_ORDER:
        items = buckets.get(cat) or []
        if not items:
            continue
        if cat in ("project_index", "scope_pdf"):
            continue  # generated outputs, not source content
        body_sections.append(f"## {label} ({len(items)})\n")
        for doc in items:
            body_sections.append(_render_entry(doc, sum_by_doc.get(doc.id)))
            body_sections.append("")  # spacer

    if not body_sections:
        body_sections.append(
            "_No source documents yet. Upload drawings, contracts, scope "
            "documents, reports, or permits on the Documents tab and the "
            "index will populate automatically._"
        )

    md = "\n".join(header) + "\n" + "\n".join(body_sections)
    md = md.rstrip() + "\n"

    return md, {
        "project_id": str(project_id),
        "project_name": project.name,
        "document_count": len(docs),
        "summarized_count": summarized,
        "pending_count": pending,
        "failed_count": failed,
        "last_indexed_at": last_indexed,
    }


def materialize_project_index(
    db: Session, project_id: UUID, *, user_id: UUID | None = None
) -> tuple[str, str]:
    """Re-render the MD and write it to R2 under a stable per-project key.
    Returns (r2_key, presigned_get_url) for the caller to surface.

    The Document row tracking this artifact is upserted: we keep at most
    one `project_index` row per project so the Documents tab shows a
    single "latest" entry."""
    md, meta = render_project_index_markdown(db, project_id)
    body = md.encode("utf-8")
    key = f"projects/{project_id}/index/PROJECT_INDEX.md"
    put_bytes(key, body, "text/markdown; charset=utf-8")

    # Upsert the Document row — one project_index per project.
    existing = list(
        db.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .where(Document.category == "project_index")
            .where(Document.archived == False)  # noqa: E712
        )
        .scalars()
        .all()
    )
    for stale in existing[1:]:
        stale.archived = True
    if existing:
        keep = existing[0]
        keep.r2_key = key
        keep.size_bytes = len(body)
        keep.mime = "text/markdown; charset=utf-8"
        keep.filename = "PROJECT_INDEX.md"
    else:
        db.add(
            Document(
                project_id=project_id,
                category="project_index",
                phase="preconstruction",
                filename="PROJECT_INDEX.md",
                r2_key=key,
                mime="text/markdown; charset=utf-8",
                size_bytes=len(body),
                uploaded_by_id=user_id,
            )
        )
    db.commit()

    # Quick existence check helps the caller decide whether to retry the
    # presign; we don't fail the request if it's missing for some reason.
    if not object_exists(key):
        # put_bytes succeeded but the check raced; presign anyway.
        pass
    return key, presign_get(key)
