"""What has been drafted and not issued — and how to take one back.

§11's action policy puts drafting at Phase 3: "agent creates a draft; nothing
issued", with the recovery listed as "delete the draft". Both halves were
missing. Drafts already existed — measured 2026-10-02 there were 20 inactive
estimate versions, 7 draft invoices and 3 draft change orders — but they were
scattered across three surfaces with no view that said "this is what is sitting
unissued", and nothing offered to remove one.

⚠️ "DRAFT" IS A DIFFERENT COLUMN ON EVERY TABLE, and that is why this module
exists rather than a single query. An estimate version is unissued when
`is_active` is false; a change order or invoice when `status = 'draft'`. Hiding
that behind one word is the point — a reviewer should not have to know which
column means unissued where.

⚠️ NULL PROVENANCE IS "NOT RECORDED", NEVER "WRITTEN BY A PERSON". The ai_job_id
columns were added 2026-10-02 and every pre-existing row is NULL because the
link was never captured. Rendering that as human-authored would invent a fact.

⚠️ DELETING IS GUARDED THREE WAYS. A draft may only be removed if it is still a
draft, nothing references it, and a snapshot is taken first so the deletion is
itself reversible. "Delete the draft" is the recovery for a bad draft; it must
not become a way to lose a good one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.services.proposals import impact_of
from app.services.revisions import snapshot


class DraftError(ValueError):
    """This draft cannot be removed. Message is written for a person."""


#: How each record type says "not issued", and what it is called on screen.
#:
#: `issued_note` is what the UI says when a record is NOT a draft, so refusing
#: to delete one can explain itself rather than just greying out.
DRAFTABLE: dict[str, dict[str, Any]] = {
    "estimate_version": {
        "table": "estimate_versions",
        "label": "Estimate version",
        "where": "is_active = false",
        "issued_note": "This is the active version — invoices bill from it.",
        "title_sql": "coalesce(reference, 'version ' || version_number::text)",
    },
    "change_order": {
        "table": "change_orders",
        "label": "Change order",
        "where": "status = 'draft'",
        "issued_note": "This change order has been decided and is part of the record.",
        "title_sql": "coalesce(title, 'untitled change order')",
    },
    "invoice": {
        "table": "invoices",
        "label": "Invoice",
        "where": "status = 'draft'",
        "issued_note": "This invoice has been sent. Issued documents are evidence.",
        "title_sql": "coalesce(number, 'unnumbered invoice')",
    },
}


def _rows(db: Session, kind: str, project_id: UUID) -> list[dict[str, Any]]:
    spec = DRAFTABLE[kind]
    sql = f"""
        SELECT id::text AS id,
               {spec['title_sql']} AS title,
               ai_job_id::text AS ai_job_id,
               created_at
          FROM {spec['table']}
         WHERE project_id = :p AND {spec['where']}
         ORDER BY created_at DESC NULLS LAST
    """
    out = []
    for r in db.execute(_T(sql), {"p": str(project_id)}).mappings().all():
        out.append({
            "kind": kind,
            "label": spec["label"],
            "id": r["id"],
            "title": r["title"],
            # Three states, not two. A missing link is unknown, and saying so is
            # the whole discipline here.
            "drafted_by": ("ai" if r["ai_job_id"] else "not recorded"),
            "ai_job_id": r["ai_job_id"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


def list_drafts(db: Session, project_id: UUID) -> dict[str, Any]:
    """Everything unissued on a project, across all three record types."""
    items: list[dict[str, Any]] = []
    for kind in DRAFTABLE:
        items.extend(_rows(db, kind, project_id))
    items.sort(key=lambda d: d["created_at"] or "", reverse=True)
    return {
        "items": items,
        "total": len(items),
        "traced": sum(1 for i in items if i["ai_job_id"]),
        # Said explicitly so the caller cannot mistake the backlog for
        # human-authored work.
        "untraced_note": (
            "Records created before 2026-10-02 carry no link to the run that "
            "produced them. That is not a statement that a person wrote them."
        ),
    }


def delete_draft(
    db: Session, kind: str, draft_id: UUID, *, user_id: UUID | None, reason: str | None = None,
) -> dict[str, Any]:
    """Remove a draft, after proving it is safe to remove.

    Three guards, each closing a different way this goes wrong:
      1. It must still be a draft. An issued invoice is evidence of what was
         billed; deleting one rewrites history.
      2. Nothing may reference it. A schedule built from an estimate version
         outlives the version, and removing the version leaves the schedule
         pointing at nothing.
      3. The state is snapshotted first, so "delete the draft" is itself
         reversible. The recovery for a bad draft must not be a way to lose a
         good one.
    """
    if kind not in DRAFTABLE:
        raise DraftError(f"{kind!r} is not a kind of draft.")
    spec = DRAFTABLE[kind]

    row = db.execute(
        _T(f"SELECT id, project_id FROM {spec['table']} "
           f"WHERE id = :i AND {spec['where']}"),
        {"i": str(draft_id)},
    ).mappings().first()
    if row is None:
        # Distinguish "gone" from "not a draft" — they call for different
        # actions and look identical from an empty result.
        exists = db.execute(
            _T(f"SELECT 1 FROM {spec['table']} WHERE id = :i"), {"i": str(draft_id)},
        ).first()
        if exists:
            raise DraftError(spec["issued_note"])
        raise DraftError("That record is no longer here.")

    blocking = impact_of(db, spec["table"], draft_id)
    if blocking:
        what = ", ".join(f"{b['count']} × {b['what']}" for b in blocking)
        raise DraftError(
            f"Something is built from this: {what}. Remove or re-point those "
            f"first — deleting it would leave them referring to nothing."
        )

    # Reversible. snapshot() runs inside this transaction, so a rolled-back
    # delete leaves no history claiming it happened.
    rev = snapshot(
        db, table=spec["table"], row_id=str(draft_id),
        project_id=row["project_id"], action="delete_draft",
        actor_id=user_id, reason=(reason or "draft discarded")[:500],
    )

    db.execute(_T(f"DELETE FROM {spec['table']} WHERE id = :i"), {"i": str(draft_id)})
    return {"deleted": True, "kind": kind, "revision_number": rev}
