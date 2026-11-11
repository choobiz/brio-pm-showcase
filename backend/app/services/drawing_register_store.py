"""Read and write the confirmed drawing register.

`drawing_register` PROPOSES groupings from filenames and knows nothing about a
database. This module is where a person's decision is recorded, and where the
two are merged for reading: a drawing someone has confirmed is reported as
confirmed, and everything else is reported as a proposal.

⚠️ CURRENT IS DERIVED, NEVER STORED. Within a group, the member that is not a
duplicate and has the latest issue date — then the highest revision, then the
latest upload. An `is_current` column would be a second copy of a fact that can
drift from the first, and every new file in a group would mean rewriting the
whole group to keep it true.

⚠️ CONFIRMING RE-CHECKS WHAT THE REVIEWER SAW. The proposal carries a
fingerprint; accepting it sends that fingerprint back, and a mismatch is refused
rather than applied. Someone can upload a drawing between the page rendering and
the button being pressed, and silently confirming a grouping nobody looked at is
exactly what the confirmation exists to prevent. Same reasoning as the `before`
check in ai_edits.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.services.drawing_register import (
    build_drawing_register,
    extract_revision,
)


def _rev_sort(revision_label: str | None, issued_on: date | None,
              created_at: Any) -> tuple:
    """Newest first. Issue date leads — a drawing issued in November supersedes
    one issued in October whatever the labels say."""
    d = (issued_on.year, issued_on.month, issued_on.day) if issued_on else (0, 0, 0)
    rev = extract_revision(revision_label or "")
    return (d, rev["sort"] if rev else (1, 0), str(created_at or ""))


def _fingerprint(sheets: list[dict[str, Any]], duplicates: list[dict[str, Any]]) -> str:
    """A stable hash of the grouping a reviewer is being shown.

    Covers only what they are being asked to agree with — which files are grouped
    together and which are duplicates. Not the labels, which they may edit.
    """
    payload = {
        "sheets": sorted(sorted(r["id"] for r in s["revisions"]) for s in sheets),
        "duplicates": sorted([d["id"], d["duplicate_of"]] for d in duplicates),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _drawings(db: Session, project_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(_T("""
        SELECT id, filename, size_bytes, created_at, drawing_group_id, revision_label,
               issued_on, duplicate_of_id, register_reviewed_at, register_reviewed_by_id
          FROM documents
         WHERE project_id = :p AND archived = false AND size_bytes IS NOT NULL
           AND category = 'drawing'
      ORDER BY created_at"""), {"p": str(project_id)}).mappings().all()
    return [dict(r) for r in rows]


def register_for_project(db: Session, project_id: UUID) -> dict[str, Any]:
    """The register: confirmed groupings where they exist, proposals elsewhere."""
    docs = _drawings(db, project_id)
    confirmed = [d for d in docs if d["drawing_group_id"] is not None]
    unconfirmed = [d for d in docs if d["drawing_group_id"] is None]

    sheets: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    # ── confirmed groups, read back as recorded ──────────────────────────
    by_group: dict[Any, list[dict[str, Any]]] = {}
    for d in confirmed:
        by_group.setdefault(d["drawing_group_id"], []).append(d)

    by_id = {d["id"]: d for d in docs}
    for gid, members in by_group.items():
        dupes = [m for m in members if m["duplicate_of_id"] is not None]
        real = [m for m in members if m["duplicate_of_id"] is None]
        for m in dupes:
            other = by_id.get(m["duplicate_of_id"])
            duplicates.append({
                "id": str(m["id"]), "filename": m["filename"],
                "duplicate_of": str(m["duplicate_of_id"]),
                "duplicate_of_filename": other["filename"] if other else "(removed)",
                "reason": "confirmed as the same file",
            })
        if not real:
            continue
        real.sort(key=lambda m: _rev_sort(m["revision_label"], m["issued_on"],
                                          m["created_at"]), reverse=True)
        sheets.append({
            "key": f"group:{gid}",
            "status": "confirmed",
            "sheet_number": None,
            "label": real[0]["filename"],
            "revisions": [{
                "id": str(m["id"]), "filename": m["filename"],
                "size_bytes": m["size_bytes"],
                "revision": m["revision_label"],
                "issued": m["issued_on"].isoformat() if m["issued_on"] else None,
                "is_current": i == 0,
            } for i, m in enumerate(real)],
            "revision_count": len(real),
            "has_history": len(real) > 1,
            "grouped_because": "confirmed",
            "grouped_because_plain": "confirmed by a person",
            "reviewed_at": (real[0]["register_reviewed_at"].date().isoformat()
                            if real[0]["register_reviewed_at"] else None),
        })

    # ── everything else, still a proposal ────────────────────────────────
    proposal = build_drawing_register([
        {"id": str(d["id"]), "filename": d["filename"],
         "size_bytes": d["size_bytes"],
         "created_at": d["created_at"].isoformat() if d["created_at"] else None}
        for d in unconfirmed
    ])
    for s in proposal["sheets"]:
        s["status"] = "proposed"
        s["reviewed_at"] = None
    sheets.extend(proposal["sheets"])
    duplicates.extend(proposal["duplicates"])

    sheets.sort(key=lambda s: (s["status"] == "confirmed", not s["has_history"],
                               s["label"].lower()))
    with_history = [s for s in sheets if s["has_history"]]
    return {
        "sheets": sheets,
        "duplicates": duplicates,
        "summary": {
            "documents_in": len(docs),
            "duplicates_found": len(duplicates),
            "distinct_drawings": len(sheets),
            "drawings_with_more_than_one_revision": len(with_history),
            "superseded_files": sum(s["revision_count"] - 1 for s in with_history),
            "confirmed_drawings": sum(1 for s in sheets if s["status"] == "confirmed"),
            "proposed_drawings": sum(1 for s in sheets if s["status"] == "proposed"),
        },
        # Only the un-confirmed part can be accepted, so the fingerprint covers
        # only that. Confirming twice must not be possible by accident.
        "fingerprint": _fingerprint(proposal["sheets"], proposal["duplicates"]),
        "caveat": (
            "Groupings marked as proposed are worked out from filenames and are not "
            "saved. A drawing whose revision is not written on its filename cannot be "
            "grouped by one."
        ),
    }


class RegisterChanged(Exception):
    """The set moved between being shown and being confirmed."""


def confirm_register(db: Session, project_id: UUID, fingerprint: str,
                     user_id: Any) -> dict[str, Any]:
    """Record the proposal as confirmed, if it still matches what was shown.

    Writes the grouping a person accepted. Raises RegisterChanged when the
    recomputed fingerprint differs — a drawing uploaded in between would change
    the grouping, and confirming one nobody looked at is the failure this guard
    exists for.
    """
    current = register_for_project(db, project_id)
    if current["fingerprint"] != fingerprint:
        raise RegisterChanged(
            "The drawings changed since this was shown, so the grouping you "
            "reviewed is no longer the one that would be saved. Reload and "
            "check it again."
        )

    proposed = [s for s in current["sheets"] if s["status"] == "proposed"]
    dup_ids = {d["id"]: d["duplicate_of"]
               for d in current["duplicates"] if d["reason"] != "confirmed as the same file"}
    now = datetime.now(timezone.utc)
    written = 0

    for sheet in proposed:
        gid = uuid4()
        members = [r["id"] for r in sheet["revisions"]]
        # Duplicates belong to the group of the file they duplicate, so a group
        # holds every file that is that drawing — revisions and copies alike.
        members += [d for d, of in dup_ids.items() if of in members]
        for doc_id in members:
            rev = next((r for r in sheet["revisions"] if r["id"] == doc_id), None)
            db.execute(_T("""
                UPDATE documents
                   SET drawing_group_id = :g,
                       revision_label = :rev,
                       issued_on = :issued,
                       duplicate_of_id = :dup,
                       register_reviewed_at = :at,
                       register_reviewed_by_id = :by,
                       updated_at = now()
                 WHERE id = :id AND project_id = :p"""), {
                "g": str(gid),
                "rev": rev["revision"] if rev else None,
                "issued": rev["issued"] if rev else None,
                "dup": dup_ids.get(doc_id),
                "at": now, "by": str(user_id) if user_id else None,
                "id": doc_id, "p": str(project_id),
            })
            written += 1
    db.commit()
    return {"drawings_confirmed": len(proposed), "files_recorded": written}
