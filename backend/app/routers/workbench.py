"""Workbench router — read-only viewers for Phase 2 of brio-pm UI plan.

Exposes the workbench artifacts (estimate_versions, schedules, activities,
inspections, cash_flows, schedules_of_values, contracts, narratives, audit_logs)
behind a single /workbench prefix. All endpoints are GET — editing arrives in
Phase 3.

Uses raw SQL through SQLAlchemy text() because we don't want to commit to
full ORM models until the editing surface lands and the columns stabilize.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth import CurrentUser, get_current_user, require_office, require_owner, require_project_member
import logging

from app.db import get_db
from app.services.estimate_pdf import (
    render_client_estimate_pdf,
    render_estimate_pdf,
)
from app.services.estimate_scope import NEW_VERSION_SCOPE_SQL, scope_binds

logger = logging.getLogger(__name__)

# Every route here is one project's data: checked once, for all of them. None
# of the 45 routes checked project membership until 2026-10-12 — any signed-in
# user, a client included, could read or change any project's estimates.
# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/projects/{project_id}/workbench", tags=["workbench"],
                   dependencies=[Depends(require_office), Depends(require_project_member)])


# ── Shared helpers ───────────────────────────────────────────────────────
def _content_disposition(filename: str, disposition: str = "inline") -> str:
    """RFC 6266 Content-Disposition that is safe for latin-1 HTTP headers.

    Project/estimate names can contain non-latin-1 characters (e.g. the em-dash
    in "1234 Example Way — New Garage"), which crash Starlette's header
    encoding. Emit an ASCII-only `filename=` fallback plus a UTF-8 `filename*`.
    """
    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "download.pdf"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _rows_as_dicts(result) -> list[dict[str, Any]]:
    """Convert SQLAlchemy result rows into list[dict] for JSON serialization."""
    cols = result.keys()
    out = []
    for row in result:
        out.append({c: row[i] for i, c in enumerate(cols)})
    return out


def _row_as_dict(row, cols) -> dict[str, Any]:
    return {c: row[i] for i, c in enumerate(cols)}


# Phase 3a — downstream artifacts (SOV, cash flow, contract) are "stale" when
# their based_on_estimate_id points at any estimate other than the project's
# is_active=true row. NULL on either side ⇒ not stale (no relationship to judge).
_IS_STALE_SQL = (
    "(based_on_estimate_id IS NOT NULL "
    " AND (SELECT id FROM estimate_versions "
    "        WHERE project_id = :pid AND is_active = true) IS NOT NULL "
    " AND based_on_estimate_id <> (SELECT id FROM estimate_versions "
    "        WHERE project_id = :pid AND is_active = true)) AS is_stale"
)


# ── Workbench overview ───────────────────────────────────────────────────
@router.get("/overview")
def workbench_overview(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Return the workbench landing context — active estimate + per-tab counts.

    Active estimate is the row with is_active=true (at most one per project,
    enforced by the partial unique index uq_estimate_versions_active_per_project).
    """
    active = db.execute(
        text("""
            SELECT id, version_number, reference, label, overhead_pct, profit_pct
            FROM estimate_versions
            WHERE project_id = :pid AND is_active = true
            LIMIT 1
        """),
        {"pid": project_id},
    ).first()

    counts = db.execute(
        text("""
            SELECT
              (SELECT COUNT(*) FROM estimate_versions WHERE project_id = :pid)    AS estimates,
              (SELECT COUNT(*) FROM schedules         WHERE project_id = :pid)    AS schedules,
              (SELECT COUNT(*) FROM cash_flows        WHERE project_id = :pid)    AS cash_flows,
              (SELECT COUNT(*) FROM schedules_of_values WHERE project_id = :pid)  AS sovs,
              (SELECT COUNT(*) FROM contracts         WHERE project_id = :pid)    AS contracts,
              (SELECT COUNT(*) FROM narratives        WHERE project_id = :pid)    AS narratives
        """),
        {"pid": project_id},
    ).first()

    return {
        "active_estimate": (
            None if active is None else {
                "id": active[0],
                "version_number": active[1],
                "reference": active[2],
                "label": active[3],
                "overhead_pct": active[4],
                "profit_pct": active[5],
            }
        ),
        "counts": {
            "estimates": counts[0],
            "schedules": counts[1],
            "cash_flows": counts[2],
            "sovs": counts[3],
            "contracts": counts[4],
            "narratives": counts[5],
        },
    }


# ── Estimate versions ────────────────────────────────────────────────────
@router.get("/estimate-versions")
def list_estimate_versions(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, version_number, reference, label, cost_basis,
                   overhead_pct, profit_pct, parent_version_id, notes,
                   is_active, created_at, created_by_id, ai_job_id,
                   payload -> 'provenance' AS provenance,
                   payload -> '_revision' AS revision,
                   jsonb_build_object(
                       'categories', payload -> 'categories',
                       'overhead', payload -> 'overhead',
                       'profit', payload -> 'profit',
                       'detailLevel', payload -> 'detailLevel') AS _money
            FROM estimate_versions
            WHERE project_id = :pid
            ORDER BY version_number
        """),
        {"pid": project_id},
    )
    from app.services.estimate_totals import version_totals

    out = _rows_as_dicts(rows)
    for r in out:
        money = r.pop("_money", None)
        if isinstance(money, str):
            import json as _json
            money = _json.loads(money)
        # Same arithmetic as Home and the owner portal, so the list, the
        # version page and Home cannot disagree about a version's total.
        r["totals"] = version_totals(money or {})
    return out


@router.get("/estimate-versions/{version_id}")
def get_estimate_version(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text("""
            SELECT id, project_id, version_number, reference, label, cost_basis,
                   overhead_pct, profit_pct, notes, payload, parent_version_id,
                   is_active, created_at, created_by_id
            FROM estimate_versions
            WHERE project_id = :pid AND id = :vid
        """),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    cols = ['id', 'project_id', 'version_number', 'reference', 'label', 'cost_basis',
            'overhead_pct', 'profit_pct', 'notes', 'payload', 'parent_version_id',
            'is_active', 'created_at', 'created_by_id']
    from app.services.estimate_totals import version_totals

    out = _row_as_dict(row, cols)
    payload = out.get("payload")
    if isinstance(payload, str):
        import json as _json
        payload = _json.loads(payload)
    out["totals"] = version_totals(payload if isinstance(payload, dict) else {})
    return out


@router.post("/estimate-versions/{version_id}/activate", status_code=status.HTTP_200_OK)
def activate_estimate_version(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Mark this estimate as the project's active anchor.

    Atomic: clears any prior is_active=true row for the project, then sets
    the target row's is_active=true. The partial unique index
    uq_estimate_versions_active_per_project prevents two active rows from
    coexisting; the explicit clear avoids tripping it inside the same TX.
    """
    target = db.execute(
        text("""
            SELECT id, version_number, reference, is_active
            FROM estimate_versions
            WHERE project_id = :pid AND id = :vid
        """),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")

    # Serialize activation per project (auto-releases at commit) so two concurrent
    # activations can't both set is_active=true and violate the partial unique index.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(project_id)})
    # Clear any prior active (no-op if already none active for this project).
    db.execute(
        text("""
            UPDATE estimate_versions
               SET is_active = false
             WHERE project_id = :pid AND is_active = true AND id <> :vid
        """),
        {"pid": project_id, "vid": version_id},
    )
    # Set the new active.
    db.execute(
        text("""
            UPDATE estimate_versions
               SET is_active = true
             WHERE project_id = :pid AND id = :vid
        """),
        {"pid": project_id, "vid": version_id},
    )
    # Audit-log the change.
    db.execute(
        text("""
            INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id)
            VALUES (:pid, 'estimate_versions', :vid, 'activate', :uid)
        """),
        {"pid": project_id, "vid": version_id, "uid": user.id},
    )
    db.commit()
    return {
        "id": target[0],
        "version_number": target[1],
        "reference": target[2],
        "is_active": True,
    }


# Phase 6 — drawing audit links: line item ↔ drawing sheet references.
@router.get("/estimate-versions/{version_id}/drawing-links")
def list_drawing_links(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text(
            """SELECT dll.id, dll.trade_name, dll.line_item_index, dll.document_id,
                      dll.sheet_number, dll.tag_reference, dll.page_number,
                      dll.verified, dll.notes, dll.created_at,
                      d.filename, d.category
                 FROM drawing_line_item_links dll
                 JOIN documents d ON d.id = dll.document_id
                WHERE dll.estimate_version_id = :vid AND d.project_id = :pid
             ORDER BY dll.trade_name, dll.line_item_index, dll.created_at"""
        ),
        {"vid": version_id, "pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/drawings")
def list_project_drawings(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text(
            """SELECT id, filename, category, phase, mime, size_bytes, created_at
                 FROM documents
                WHERE project_id = :pid
                  AND category = 'drawing'
                  AND archived = false
             ORDER BY filename"""
        ),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.post("/estimate-versions/{version_id}/drawing-links", status_code=status.HTTP_201_CREATED)
def create_drawing_link(
    project_id: UUID,
    version_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    document_id = body.get("document_id")
    trade_name = body.get("trade_name")
    line_item_index = body.get("line_item_index")
    if not document_id or not trade_name or line_item_index is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="document_id, trade_name, line_item_index required",
        )
    new_id = uuid4()
    db.execute(
        text(
            """INSERT INTO drawing_line_item_links (
                    id, estimate_version_id, trade_name, line_item_index, document_id,
                    sheet_number, tag_reference, page_number, verified, notes, created_by_id
               ) VALUES (
                    :id, :vid, :tn, :idx, :did,
                    :sheet, :tag, :page, :ver, :notes, :uid
               )"""
        ),
        {
            "id": new_id, "vid": version_id, "tn": trade_name,
            "idx": int(line_item_index), "did": document_id,
            "sheet": body.get("sheet_number"),
            "tag": body.get("tag_reference"),
            "page": body.get("page_number"),
            "ver": bool(body.get("verified", False)),
            "notes": body.get("notes"),
            "uid": user.id,
        },
    )
    db.commit()
    return {"id": str(new_id)}


@router.delete("/drawing-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_drawing_link(
    project_id: UUID,
    link_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    # Ownership check via project_id → estimate_versions → drawing_line_item_links
    row = db.execute(
        text(
            """SELECT dll.id FROM drawing_line_item_links dll
                 JOIN estimate_versions ev ON ev.id = dll.estimate_version_id
                WHERE dll.id = :lid AND ev.project_id = :pid"""
        ),
        {"lid": link_id, "pid": project_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="link not found")
    db.execute(
        text("DELETE FROM drawing_line_item_links WHERE id = :lid"),
        {"lid": link_id},
    )
    db.commit()


# Phase 5 — generate a branded estimate PDF directly from a workbench
# estimate_version. Reuses the existing render_estimate_pdf service that
# powers the legacy estimates router; we just shape the workbench
# version's payload + project + client to fit the renderer's contract
# and stream the bytes back as application/pdf.
@router.get("/estimate-versions/{version_id}/pdf")
def estimate_version_pdf(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    row = db.execute(
        text(
            """SELECT ev.payload, ev.reference, ev.version_number,
                      p.name AS project_name, p.address AS project_address, p.type AS project_type,
                      p.sqft, c.name AS client_name, c.email AS client_email, c.phone AS client_phone,
                      c.address AS client_address
                 FROM estimate_versions ev
                 JOIN projects p ON p.id = ev.project_id
                 LEFT JOIN clients c ON c.id = p.client_id
                WHERE ev.project_id = :pid AND ev.id = :vid"""
        ),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")

    payload, reference, version_number = row[0], row[1], row[2]
    payload = payload if isinstance(payload, dict) else __import__("json").loads(payload)

    project = {
        "name": row[3] or "Project",
        "address": row[4] or "",
        "type": row[5] or "",
        "sqft": row[6],
    }
    client = {
        "name": row[7] or "Client",
        "email": row[8] or "",
        "phone": row[9] or "",
        "address": row[10] or "",
    }

    # Patch payload with the version reference so the PDF header reads correctly
    payload = {**payload, "reference": payload.get("reference") or reference}

    pdf_bytes = render_estimate_pdf(payload=payload, project=project, client=client)
    safe_name = (project["name"] or "Estimate").replace("/", "-").replace(" ", "_")
    filename = f"Estimate_{safe_name}_v{version_number:03d}_{reference}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/estimate-versions/{version_id}/pdf-scope")
def estimate_version_pdf_scope(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """The scope the "scope + selections + lump sum" PDF will use for this
    estimate, and how many the project has, so the page can name it or ask.
    Same resolution as the PDF itself (services/estimate_scope.scope_for_pdf)."""
    from app.services.estimate_scope import scope_for_pdf
    sc, n = scope_for_pdf(db, project_id, version_id)
    if sc is None:
        return {"scope": None, "scope_count": n}
    p = sc[2] if isinstance(sc[2], dict) else {}
    return {"scope": {"id": str(sc[0]), "version": sc[1], "reference": p.get("reference") or p.get("title"),
                      "summary": (p.get("summary") or "")[:200]},
            "scope_count": n}


# Client estimate — 5th estimate type. mode=detailed (every line item, single
# client price) or mode=selection (Labor + Material-Selection: cosmetic/fixture
# items tagged `selectable` show a material allowance the client can upgrade,
# labor fixed). Contingency is pulled out as one separate line in both modes.
@router.get("/estimate-versions/{version_id}/selection-pdf")
def estimate_version_selection_pdf(
    project_id: UUID,
    version_id: UUID,
    mode: str = "detailed",
    scope_id: UUID | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    from app.services.estimate_selection_pdf import render_client_selection_pdf
    row = db.execute(
        text(
            """SELECT ev.payload, ev.reference, p.name, p.address, p.type, p.sqft,
                      c.name, c.email, c.phone, c.address
                 FROM estimate_versions ev
                 JOIN projects p ON p.id = ev.project_id
                 LEFT JOIN clients c ON c.id = p.client_id
                WHERE ev.project_id = :pid AND ev.id = :vid"""
        ),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    payload = row[0] if isinstance(row[0], dict) else __import__("json").loads(row[0])
    payload = {**payload, "reference": payload.get("reference") or row[1]}
    project = {"name": row[2] or "Project", "address": row[3] or "", "type": row[4] or "", "sqft": row[5]}
    client = {"name": row[6] or "", "email": row[7] or "", "phone": row[8] or "", "address": row[9] or ""}
    m = mode if mode in ("detailed", "selection", "lumpsum", "scopepack", "divisions", "divisions_lines") else "detailed"
    if m in ("divisions", "divisions_lines"):
        # Priced by CSI division with Div 09 by scope (456 W Sample PM, 2026-10-27);
        # divisions_lines adds each division's work and the assumptions (2026-11-06).
        from app.services.division_breakdown import render_pdf as render_divisions
        pdf_bytes = render_divisions(payload, project, client, with_lines=(m == "divisions_lines"))
    else:
        pdf_bytes = render_client_selection_pdf(payload, project, client, m)
    if m == "scopepack":
        # Scope + selections (no prices) + lump sum (Amir, 2026-10-27): the
        # project's scope document goes in front of the estimate part. Which
        # scope is the caller's choice; a project can carry one per job.
        from app.services.package_pdf import merge_pdfs
        from app.services.scope_pdf import render_full_scope_pdf
        from app.services.estimate_scope import scope_for_pdf
        if scope_id is not None:
            sc = db.execute(text("SELECT id, version, payload, created_at FROM scopes "
                                 "WHERE id = :s AND project_id = :pid"), {"s": scope_id, "pid": project_id}).first()
            if sc is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That scope is not on this project.")
            n_scopes = 1
        else:
            # The estimate's own scope (estimate_versions.scope_id), else the
            # project's only scope; never the latest of several.
            sc, n_scopes = scope_for_pdf(db, project_id, version_id)
        if sc is None and n_scopes == 0:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="This project has no scope to include. Create one on the Scope tab first.")
        if sc is None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="This estimate is not linked to a scope and the project has more than one; "
                                       "choose which one to include.")
        scope_pdf = render_full_scope_pdf(
            scopes=[{"payload": sc[2] or {}, "version": sc[1], "generated_at": sc[3]}],
            project=project, client=client)
        pdf_bytes = merge_pdfs([scope_pdf, pdf_bytes])
    label = {"selection": "Selections", "lumpsum": "Lump_Sum", "scopepack": "Scope_and_Price",
             "divisions": "By_Division", "divisions_lines": "By_Division_Detailed"}.get(m, "Detailed")
    safe = (project["name"] or "Estimate").replace("/", "-").replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(f"Estimate_{label}_{safe}_{row[1]}.pdf")},
    )


# 1234 Example client package — standalone docs (cover/summary/estimate/schedule/
# cashflow/contract) merged into one bundle. doc=merged | cover | summary | estimate |
# schedule | cashflow | contract.
@router.get("/estimate-readiness")
def estimate_readiness(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Document-intake guide: can this project produce a MEASURED estimate (CAD
    takeoff) or must it fall back to PDF-read + rates? Plus what's still missing."""
    from app.services.estimate_disciplines import recommend_disciplines
    from app.services.estimate_readiness import compute_readiness

    out = compute_readiness(db, project_id)

    # Which trades this job actually needs. estimate_full runs one AI call per
    # discipline and there are six, so this is what decides whether a run takes
    # three minutes or eight. Recommendation only: the caller sends the final
    # list. See estimate_disciplines for why it is not automatic.
    scope_payload = db.execute(
        text(
            "SELECT payload FROM scopes WHERE project_id = :p "
            "ORDER BY version DESC LIMIT 1"
        ),
        {"p": str(project_id)},
    ).scalar()
    out["discipline_plan"] = recommend_disciplines(scope_payload)
    return out


@router.get("/client-package-pdf")
def client_package_pdf(
    project_id: UUID,
    doc: str = "merged",
    estimate_ref: str = "active",
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    from app.services.package_pdf import PackageIncomplete, build_client_package
    try:
        pdf_bytes, filename = build_client_package(db, str(project_id), estimate_ref, doc)
    except PackageIncomplete as e:
        raise HTTPException(status_code=409, detail=str(e))
    # First time the full merged package is downloaded, capture a baseline so the
    # stale-package warning has something to compare against. Explicit re-baselining
    # happens via POST /package-mark-sent after a resend.
    if doc == "merged":
        _row = db.execute(
            text("select package_baseline from projects where id=:p"), {"p": project_id}
        ).mappings().first()
        if _row is not None and not _row["package_baseline"]:
            from datetime import datetime, timezone
            from app.services.package_fingerprint import compute_package_fingerprint
            _fp = compute_package_fingerprint(db, str(project_id))
            _baseline = {"sections": _fp["sections"],
                         "marked_at": datetime.now(timezone.utc).isoformat(),
                         "marked_by": "auto:first-download"}
            db.execute(text("update projects set package_baseline=CAST(:b AS jsonb) where id=:p"),
                       {"b": __import__("json").dumps(_baseline), "p": project_id})
            db.commit()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/package-status")
def package_status(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Is the client package stale vs. what was last sent? Names what changed."""
    from app.services.package_fingerprint import (
        compute_package_fingerprint, diff_fingerprints, SECTION_LABELS,
    )
    current = compute_package_fingerprint(db, str(project_id))
    row = db.execute(
        text("select package_baseline from projects where id=:p"), {"p": project_id}
    ).mappings().first()
    baseline = row["package_baseline"] if row else None
    if not baseline:
        return {"has_baseline": False, "stale": False, "changed_sections": [],
                "changed_labels": [], "marked_at": None, "details": current["details"]}
    changed = diff_fingerprints(baseline.get("sections") or {}, current["sections"])
    return {
        "has_baseline": True,
        "stale": len(changed) > 0,
        "changed_sections": changed,
        "changed_labels": [SECTION_LABELS.get(c, c) for c in changed],
        "marked_at": baseline.get("marked_at"),
        "marked_by": baseline.get("marked_by"),
        "details": current["details"],
    }


@router.post("/package-mark-sent")
def package_mark_sent(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Snapshot the current package fingerprint as 'what was last sent'."""
    from datetime import datetime, timezone
    from app.services.package_fingerprint import compute_package_fingerprint
    fp = compute_package_fingerprint(db, str(project_id))
    baseline = {"sections": fp["sections"],
                "marked_at": datetime.now(timezone.utc).isoformat(),
                "marked_by": user.email}
    db.execute(text("update projects set package_baseline=CAST(:b AS jsonb) where id=:p"),
               {"b": __import__("json").dumps(baseline), "p": project_id})
    db.commit()
    return {"ok": True, "marked_at": baseline["marked_at"]}


# Phase 7 — client-presentation edition of the estimate version.
# Same source payload as `estimate_version_pdf`, different document: cover
# page with the total investment, project info page, category cards (no
# line items / labor / materials), investment summary, payment schedule,
# assumptions, exclusions, BRIO 5-Star Guarantee, signature page.
@router.get("/estimate-versions/{version_id}/client-pdf")
def estimate_version_client_pdf(
    project_id: UUID,
    version_id: UUID,
    summary: bool = False,
    residential: bool = False,
    internal_fee: bool = False,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    row = db.execute(
        text(
            """SELECT ev.payload, ev.reference, ev.version_number,
                      p.name AS project_name, p.address AS project_address, p.type AS project_type,
                      p.sqft, c.name AS client_name, c.email AS client_email, c.phone AS client_phone,
                      c.address AS client_address
                 FROM estimate_versions ev
                 JOIN projects p ON p.id = ev.project_id
                 LEFT JOIN clients c ON c.id = p.client_id
                WHERE ev.project_id = :pid AND ev.id = :vid"""
        ),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")

    payload, reference, version_number = row[0], row[1], row[2]
    payload = payload if isinstance(payload, dict) else __import__("json").loads(payload)

    project = {
        "name": row[3] or "Project",
        "address": row[4] or "",
        "type": row[5] or "",
        "sqft": row[6],
    }
    client = {
        "name": row[7] or "Client",
        "email": row[8] or "",
        "phone": row[9] or "",
        "address": row[10] or "",
    }
    # Patch reference into payload so the cover/header stays consistent,
    # even though the client edition doesn't headline the reference code.
    payload = {**payload, "reference": payload.get("reference") or reference}

    pdf_bytes = render_client_estimate_pdf(
        payload=payload, project=project, client=client,
        summary=summary, residential=residential, internal_fee=internal_fee,
    )
    safe_name = (project["name"] or "Estimate").replace("/", "-").replace(" ", "_")
    variant = (
        "INTERNAL_Fee_Breakdown" if internal_fee
        else "Residential" if residential
        else "Summary" if summary
        else "Detailed"
    )
    filename = f"Project_Estimate_{variant}_{safe_name}_v{version_number:03d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# Phase 4 Step 2 — save an edited schedule as a new immutable version.
# Caller (in-browser CPM editor) does the recompute and sends fully-baked
# activity rows (with ES/EF/LS/LF/float/critical + start/finish dates
# already populated); this endpoint persists them under version_number = max+1.
@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule_version(
    project_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    parent_id = body.get("parent_schedule_id")
    project_start = body.get("project_start")
    working_hours = body.get("working_hours") or "Mon-Fri 7am-5pm (50 hr/week)"
    holidays = body.get("holidays") or []
    deterministic_finish = body.get("deterministic_finish_date")
    p50 = body.get("p50_finish_date")
    p80 = body.get("p80_finish_date")
    expected_wd = body.get("project_expected_wd")
    std_dev = body.get("project_std_dev_wd")
    critical_codes = body.get("critical_path_activity_codes") or []
    notes = body.get("notes") or ""
    activities = body.get("activities") or []
    inspections = body.get("inspections") or []

    if not project_start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="project_start required")
    if not activities:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="activities[] required")

    next_ver_row = db.execute(
        text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM schedules WHERE project_id = :pid"),
        {"pid": project_id},
    ).first()
    next_ver = int(next_ver_row[0])

    sched_id = uuid4()
    db.execute(
        text("""
            INSERT INTO schedules (
                id, project_id, version_number, project_start, working_hours,
                holidays, deterministic_finish_date, p50_finish_date, p80_finish_date,
                project_expected_wd, project_std_dev_wd,
                critical_path_activity_codes, notes, parent_version_id, created_by_id
            ) VALUES (
                :id, :pid, :ver, :ps, :wh,
                CAST(:hols AS jsonb), :df, :p50, :p80,
                :ewd, :stddev, CAST(:cpc AS jsonb), :notes, :parent, :uid
            )
        """),
        {
            "id": sched_id, "pid": project_id, "ver": next_ver,
            "ps": project_start, "wh": working_hours,
            "hols": __import__("json").dumps(holidays),
            "df": deterministic_finish, "p50": p50, "p80": p80,
            "ewd": expected_wd, "stddev": std_dev,
            "cpc": __import__("json").dumps(critical_codes),
            "notes": notes, "parent": parent_id, "uid": user.id,
        },
    )

    # Insert activities
    activity_id_by_code: dict[str, str] = {}
    for a in activities:
        act_id = uuid4()
        activity_id_by_code[a["activity_code"]] = str(act_id)
        db.execute(
            text("""
                INSERT INTO activities (
                    id, schedule_id, activity_code, name, phase,
                    predecessor_codes, lag_wd,
                    optimistic_wd, most_likely_wd, pessimistic_wd,
                    expected_wd, variance,
                    early_start_wd, early_finish_wd, late_start_wd, late_finish_wd,
                    total_float_wd, free_float_wd,
                    start_date, finish_date, is_critical, is_milestone
                ) VALUES (
                    :id, :sid, :code, :name, :phase,
                    CAST(:preds AS jsonb), :lag,
                    :o, :m, :p, :ewd, :var,
                    :es, :ef, :ls, :lf, :tf, :ff,
                    :sd, :fd, :crit, :ms
                )
            """),
            {
                "id": act_id, "sid": sched_id,
                "code": a["activity_code"], "name": a["name"], "phase": a["phase"],
                "preds": __import__("json").dumps(a.get("predecessor_codes") or []),
                "lag": int(a.get("lag_wd") or 0),
                "o": a.get("optimistic_wd"), "m": a.get("most_likely_wd"),
                "p": a.get("pessimistic_wd"),
                "ewd": a.get("expected_wd"), "var": a.get("variance"),
                "es": a.get("early_start_wd"), "ef": a.get("early_finish_wd"),
                "ls": a.get("late_start_wd"), "lf": a.get("late_finish_wd"),
                "tf": a.get("total_float_wd"), "ff": a.get("free_float_wd"),
                "sd": a.get("start_date"), "fd": a.get("finish_date"),
                "crit": bool(a.get("is_critical")), "ms": bool(a.get("is_milestone")),
            },
        )

    # Insert inspections (re-link to new activity IDs by code if present)
    for i in inspections:
        host_code = i.get("activity_code")
        host_id = activity_id_by_code.get(host_code) if host_code else None
        db.execute(
            text("""
                INSERT INTO inspections (
                    id, schedule_id, activity_id, authority, inspection_type,
                    lead_time_days, gates, notes
                ) VALUES (
                    :id, :sid, :aid, :auth, :it, :lead, :gates, :n
                )
            """),
            {
                "id": uuid4(), "sid": sched_id, "aid": host_id,
                "auth": i.get("authority"), "it": i.get("inspection_type"),
                "lead": i.get("lead_time_days"), "gates": i.get("gates"),
                "n": i.get("notes"),
            },
        )

    db.execute(
        text("""
            INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
            VALUES (:pid, 'schedules', :rid, 'edit-save-new-version', :uid, CAST(:diff AS jsonb))
        """),
        {
            "pid": project_id, "rid": sched_id, "uid": user.id,
            "diff": __import__("json").dumps({
                "version_number": next_ver,
                "parent_schedule_id": str(parent_id) if parent_id else None,
                "n_activities": len(activities),
                "n_inspections": len(inspections),
            }),
        },
    )
    db.commit()
    return {
        "id": str(sched_id), "version_number": next_ver,
        "critical_path_activity_codes": critical_codes,
    }


# Phase 3a Step 5 — regenerate downstream artifacts (SOV / Cash Flow /
# Contract) against the project's currently-active estimate. Three modes:
#
#   - Contract: real math — base + option contract values recomputed from
#     the active estimate's category totals (non-optional vs optional).
#   - SOV: rescale-only — for each row in the existing SOV, scale its
#     monthly allocations by (new_trade_total / old_trade_total). Preserves
#     the month-weighting that was carefully laid out originally; just
#     refreshes amounts. NEW trades in the active estimate that aren't in
#     the existing SOV are NOT auto-added (would need schedule context to
#     allocate properly).
#   - Cash Flow: re-point only. The weekly math is too schedule-dependent
#     to refresh blindly; we just update based_on_estimate_id so the stale
#     banner clears, and surface a "re-save the billing plan for accurate cash
#     flow" note in the response.
import re

@router.post("/regenerate-downstream")
def regenerate_downstream(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    # 1. Resolve the active estimate
    active = db.execute(
        text("""
            SELECT id, payload FROM estimate_versions
             WHERE project_id = :pid AND is_active = true LIMIT 1
        """),
        {"pid": project_id},
    ).first()
    if not active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No active estimate set; mark one Active first.",
        )
    active_id, active_payload_raw = active[0], active[1]
    payload = active_payload_raw if isinstance(active_payload_raw, dict) else __import__("json").loads(active_payload_raw)

    cats = payload.get("categories", []) or []
    overhead = float(payload.get("overhead") or 0)
    profit = float(payload.get("profit") or 0)

    def cat_total(c: dict) -> float:
        return float(c.get("total") or 0)

    # 2. New trade totals indexed by leading trade number (e.g., "01")
    new_trade_totals: dict[str, float] = {}
    for c in cats:
        m = re.match(r"^\s*(\d+)\b", c.get("name") or "")
        if not m:
            continue
        new_trade_totals[m.group(1)] = cat_total(c)

    base_direct_cont = sum(cat_total(c) for c in cats if not c.get("optional"))
    opt_direct_cont = sum(cat_total(c) for c in cats if c.get("optional"))
    base_pretax = base_direct_cont * (1 + overhead / 100) * (1 + profit / 100)
    opt_pretax = opt_direct_cont * (1 + overhead / 100) * (1 + profit / 100)

    summary = {"contract": None, "sovs": [], "cash_flows": [], "active_estimate_id": str(active_id)}

    # ── Contract: real recompute ────────────────────────────────────────
    contracts = db.execute(
        text("""
            SELECT id, base_contract_value, option_contract_value, status
              FROM contracts WHERE project_id = :pid
        """),
        {"pid": project_id},
    ).fetchall()
    for cid, _old_base, _old_opt, c_status in contracts:
        # A signed or executed contract's value is fixed: it changes only by
        # change order. Recomputing it from the estimate here (also run when
        # an estimate is activated) would rewrite a signed document — on 1234
        # it would have stored the CO-inclusive estimate as the base, counting
        # CO-3 twice (found 2026-11-02).
        if c_status in ("signed", "executed"):
            summary["contract"] = {"id": str(cid), "status": f"kept ({c_status})"}
            continue
        db.execute(
            text("""
                UPDATE contracts
                   SET base_contract_value = :b,
                       option_contract_value = :o,
                       based_on_estimate_id = :eid
                 WHERE id = :cid AND project_id = :pid
            """),
            {
                "b": round(base_pretax, 2),
                "o": round(opt_pretax, 2),
                "eid": active_id,
                "cid": cid,
                "pid": project_id,
            },
        )
        summary["contract"] = {
            "id": str(cid),
            "base_contract_value": round(base_pretax, 2),
            "option_contract_value": round(opt_pretax, 2),
        }

    # ── SOV: rescale rows by new trade total ────────────────────────────
    sovs = db.execute(
        text("""
            SELECT id, scenario, rows, monthly_totals, months,
                   total_pretax, based_on_estimate_id
              FROM schedules_of_values WHERE project_id = :pid
        """),
        {"pid": project_id},
    ).fetchall()
    for sov_id, scenario, rows_json, _monthly_totals, months_json, _old_pretax, _old_eid in sovs:
        rows = rows_json if isinstance(rows_json, list) else __import__("json").loads(rows_json)
        months = months_json if isinstance(months_json, list) else __import__("json").loads(months_json)
        # Built from the billing plan: rows are the Costs budget (contract
        # price), not estimate trades, so rescaling by estimate direct cost
        # would be wrong. Re-saving the billing plan rebuilds them.
        if any((r or {}).get("basis") == "budget" for r in rows):
            summary["sovs"].append({"id": str(sov_id), "scenario": scenario,
                                    "status": "kept (built from the billing plan; re-save the plan)"})
            continue

        new_rows = []
        new_monthly: dict[str, float] = {m: 0.0 for m in months}
        for r in rows:
            tn = r.get("tradeNumber") or r.get("trade_number")
            old_scheduled = float(r.get("scheduledValue") or 0)
            new_scheduled = new_trade_totals.get(str(tn), old_scheduled)
            # Skip trades flagged optional in scenario != "combined"
            if scenario == "base" and r.get("optional"):
                new_scheduled = 0.0
            scale = (new_scheduled / old_scheduled) if old_scheduled > 0 else 0.0
            new_monthly_for_row = {m: round(float(v) * scale, 2) for m, v in (r.get("monthly") or {}).items()}
            for m, v in new_monthly_for_row.items():
                new_monthly[m] = round(new_monthly[m] + v, 2)
            new_row = {**r, "scheduledValue": round(new_scheduled, 2), "monthly": new_monthly_for_row}
            new_rows.append(new_row)

        new_total = sum(new_monthly.values())
        # Recompute cumulative & holdback math
        running = 0.0
        cumulative: dict[str, float] = {}
        for m in months:
            running += new_monthly[m]
            cumulative[m] = round(running, 2)
        lien = {m: round(v * 0.10, 2) for m, v in new_monthly.items()}
        owner_net = {m: round(new_monthly[m] - lien[m], 2) for m in months}

        db.execute(
            text("""
                UPDATE schedules_of_values
                   SET rows = CAST(:r AS jsonb),
                       monthly_totals = CAST(:mt AS jsonb),
                       cumulative_totals = CAST(:ct AS jsonb),
                       lien_holdback_10pct = CAST(:lh AS jsonb),
                       owner_net_monthly = CAST(:on AS jsonb),
                       total_pretax = :tp,
                       based_on_estimate_id = :eid
                 WHERE id = :sid AND project_id = :pid
            """),
            {
                "r": __import__("json").dumps(new_rows),
                "mt": __import__("json").dumps(new_monthly),
                "ct": __import__("json").dumps(cumulative),
                "lh": __import__("json").dumps(lien),
                "on": __import__("json").dumps(owner_net),
                "tp": round(new_total, 2),
                "eid": active_id,
                "sid": sov_id,
                "pid": project_id,
            },
        )
        summary["sovs"].append({"id": str(sov_id), "scenario": scenario, "total_pretax": round(new_total, 2)})

    # ── Cash Flow: re-point only (weekly math too schedule-dependent for here) ──
    cfs = db.execute(
        text("""
            SELECT id, scenario FROM cash_flows WHERE project_id = :pid
               AND COALESCE(key_dates->>'basis', '') <> 'billing-plan'
        """),
        {"pid": project_id},
    ).fetchall()
    for cf_id, scenario in cfs:
        db.execute(
            text("""
                UPDATE cash_flows
                   SET based_on_estimate_id = :eid,
                       notes = COALESCE(notes, '') || E'\n\n[Re-pointed at active estimate :ref. Weekly math not re-derived; save the billing plan on Contract to rebuild it.]'
                 WHERE id = :cid AND project_id = :pid
            """),
            {"eid": active_id, "ref": str(active_id), "cid": cf_id, "pid": project_id},
        )
        summary["cash_flows"].append({"id": str(cf_id), "scenario": scenario, "status": "re-pointed (math not recomputed)"})

    # Audit log
    db.execute(
        text("""
            INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
            VALUES (:pid, 'projects', :pid, 'regenerate-downstream', :uid, CAST(:diff AS jsonb))
        """),
        {
            "pid": project_id,
            "uid": user.id,
            "diff": __import__("json").dumps(summary),
        },
    )

    db.commit()
    return summary


# Phase 3b — inline editing: update an existing estimate version in place.
# Counterpart to the POST (create new version) below — caller picks the
# mode per save based on whether mid-iteration history matters.
@router.put("/estimate-versions/{version_id}")
def update_estimate_version(
    project_id: UUID,
    version_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    target = db.execute(
        text(
            "SELECT id, reference, version_number FROM estimate_versions "
            "WHERE project_id = :pid AND id = :vid"
        ),
        {"pid": project_id, "vid": version_id},
    ).first()
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")

    payload = body.get("payload") or {}
    notes = body.get("notes") or payload.get("notes") or ""
    overhead = body.get("overhead_pct")
    profit = body.get("profit_pct")
    if overhead is None:
        overhead = payload.get("overhead", 0)
    if profit is None:
        profit = payload.get("profit", 0)
    reference = body.get("reference") or payload.get("reference") or target[1]
    rates = _settle_rates(db, payload, body, user, f"{reference} v{int(target[2])}")

    # History before the overwrite. This is the live estimate editor and it was
    # not covered when revisions landed: it writes through the workbench router
    # rather than through estimates.update_estimate, so the snapshot has to be
    # taken here too or a hand edit to the active version has nothing behind it.
    from app.services.revisions import actor_uuid, snapshot

    snapshot(
        db, table="estimate_versions", row_id=version_id, project_id=project_id,
        action="edit", actor_id=actor_uuid(user),
    )

    db.execute(
        text(
            """UPDATE estimate_versions
                  SET reference = :ref,
                      overhead_pct = :oh,
                      profit_pct = :pr,
                      notes = :notes,
                      payload = CAST(:payload AS jsonb)
                WHERE project_id = :pid AND id = :vid"""
        ),
        {
            "pid": project_id,
            "vid": version_id,
            "ref": reference,
            "oh": float(overhead or 0),
            "pr": float(profit or 0),
            "notes": notes,
            "payload": __import__("json").dumps(payload),
        },
    )
    db.execute(
        text(
            """INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
               VALUES (:pid, 'estimate_versions', :rid, 'edit-update-in-place', :uid, CAST(:diff AS jsonb))"""
        ),
        {
            "pid": project_id,
            "rid": version_id,
            "uid": user.id,
            "diff": __import__("json").dumps(
                {"reference": reference, "version_number": int(target[2])}
            ),
        },
    )
    db.commit()
    return {"id": str(version_id), "version_number": int(target[2]), "reference": reference,
            "rate_library": rates}


def _settle_rates(db: Session, payload: dict, body: dict, user: CurrentUser, source: str) -> dict | None:
    """The save step's rate-library decisions (phase 3 write-back), written in
    the same transaction as the estimate. Mutates `payload`'s rate links."""
    from app.services.rate_writeback import DecisionError, settle

    if not isinstance(payload, dict):
        return None
    try:
        return settle(db, payload, body.get("rate_updates"), source=f"estimate {source}",
                      user_email=user.email)
    except DecisionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


# Phase 3b — inline editing: create a new immutable estimate version from
# an edited payload. The caller (the in-browser editor) does the rollup
# arithmetic and sends a fully-recomputed payload; this endpoint persists
# it as v(max+1) and marks it active. We keep the old version intact for
# audit history.
@router.post("/estimate-versions", status_code=status.HTTP_201_CREATED)
def create_estimate_version(
    project_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    parent_version_id = body.get("parent_version_id")
    payload = body.get("payload") or {}
    notes = body.get("notes") or payload.get("notes") or ""
    overhead = body.get("overhead_pct")
    profit = body.get("profit_pct")
    if overhead is None:
        overhead = payload.get("overhead", 0)
    if profit is None:
        profit = payload.get("profit", 0)

    # Reference + label — default to a `-edit-{n}` suffix on the parent if
    # caller didn't provide one. Keeps the audit chain readable.
    reference = body.get("reference") or payload.get("reference")
    label = body.get("label")

    if not reference:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="reference is required on payload (e.g., EST-10059)",
        )

    rates = _settle_rates(db, payload, body, user, f"{reference} (new version)")

    # Serialize numbering + activation per project (auto-releases at commit) so a
    # concurrent save/generate can't pick the same version_number.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(project_id)})
    # Pick the next version_number for this project
    next_ver_row = db.execute(
        text("""
            SELECT COALESCE(MAX(version_number), 0) + 1
            FROM estimate_versions WHERE project_id = :pid
        """),
        {"pid": project_id},
    ).first()
    next_ver = int(next_ver_row[0])

    if not label:
        label = f"v{next_ver:03d} — {reference} (edited)"

    new_id = uuid4()

    # Clear any prior active (partial unique index enforces ≤1 active per project)
    db.execute(
        text("""
            UPDATE estimate_versions SET is_active = false
             WHERE project_id = :pid AND is_active = true
        """),
        {"pid": project_id},
    )

    # Insert the new version, marked active
    db.execute(
        text("""
            INSERT INTO estimate_versions (
                id, project_id, version_number, reference, label, cost_basis,
                overhead_pct, profit_pct, notes, payload,
                parent_version_id, is_active, created_by_id, scope_id
            ) VALUES (
                :id, :pid, :ver, :ref, :label, :cost_basis,
                :oh, :pr, :notes, CAST(:payload AS jsonb),
                :parent, true, :uid, """ + NEW_VERSION_SCOPE_SQL + """
            )
        """),
        {
            **scope_binds(project_id, parent_version_id),
            "id": new_id,
            "pid": project_id,
            "ver": next_ver,
            "ref": reference,
            "label": label,
            "cost_basis": payload.get("costBasis") or payload.get("cost_basis") or "LOW",
            "oh": float(overhead or 0),
            "pr": float(profit or 0),
            "notes": notes,
            "payload": __import__("json").dumps(payload),
            "parent": parent_version_id,
            "uid": user.id,
        },
    )

    # Audit log
    db.execute(
        text("""
            INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id, diff)
            VALUES (:pid, 'estimate_versions', :rid, 'edit-save-new-version', :uid, CAST(:diff AS jsonb))
        """),
        {
            "pid": project_id,
            "rid": new_id,
            "uid": user.id,
            "diff": __import__("json").dumps({
                "reference": reference,
                "version_number": next_ver,
                "parent_version_id": str(parent_version_id) if parent_version_id else None,
            }),
        },
    )
    db.commit()

    return {
        "id": str(new_id),
        "version_number": next_ver,
        "reference": reference,
        "is_active": True,
        "rate_library": rates,
    }


# ── Schedules + activities + inspections ─────────────────────────────────
@router.get("/schedules")
def list_schedules(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, version_number, project_start, working_hours, holidays,
                   deterministic_finish_date, p50_finish_date, p80_finish_date,
                   project_expected_wd, project_std_dev_wd,
                   critical_path_activity_codes, notes, created_at, is_baseline, client_visible
            FROM schedules
            WHERE project_id = :pid
            ORDER BY version_number
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/schedules/{schedule_id}")
def get_schedule(
    project_id: UUID,
    schedule_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    sched_row = db.execute(
        text("""
            SELECT id, version_number, project_start, working_hours, holidays,
                   deterministic_finish_date, p50_finish_date, p80_finish_date,
                   project_expected_wd, project_std_dev_wd,
                   critical_path_activity_codes, notes, created_at, is_baseline, client_visible
            FROM schedules
            WHERE project_id = :pid AND id = :sid
        """),
        {"pid": project_id, "sid": schedule_id},
    ).first()
    if not sched_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="schedule not found")

    activities = db.execute(
        text("""
            SELECT id, activity_code, name, phase, predecessor_codes, lag_wd,
                   optimistic_wd, most_likely_wd, pessimistic_wd, expected_wd,
                   variance, early_start_wd, early_finish_wd,
                   late_start_wd, late_finish_wd, total_float_wd, free_float_wd,
                   start_date, finish_date, is_critical, is_milestone
            FROM activities
            WHERE schedule_id = :sid
            ORDER BY COALESCE(early_start_wd, 0), activity_code
        """),
        {"sid": schedule_id},
    )

    inspections = db.execute(
        text("""
            SELECT id, activity_id, authority, inspection_type, lead_time_days, gates, notes
            FROM inspections
            WHERE schedule_id = :sid
            ORDER BY authority
        """),
        {"sid": schedule_id},
    )

    sched_cols = ['id', 'version_number', 'project_start', 'working_hours', 'holidays',
                  'deterministic_finish_date', 'p50_finish_date', 'p80_finish_date',
                  'project_expected_wd', 'project_std_dev_wd',
                  'critical_path_activity_codes', 'notes', 'created_at']
    return {
        **_row_as_dict(sched_row, sched_cols),
        "activities": _rows_as_dicts(activities),
        "inspections": _rows_as_dicts(inspections),
    }


# ── Cash flows ───────────────────────────────────────────────────────────
@router.get("/cash-flows")
def list_cash_flows(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text(f"""
            SELECT id, version_number, scenario, total_pretax,
                   final_cash_position, min_cash_position,
                   min_position_week_end, bridge_financing_required,
                   owner_net_days, lien_holdback_pct,
                   lien_release_days_post_substantial,
                   deficiency_days_post_occupancy,
                   based_on_estimate_id,
                   {_IS_STALE_SQL},
                   created_at
            FROM cash_flows
            WHERE project_id = :pid
            ORDER BY scenario, version_number
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/cash-flows/{cf_id}")
def get_cash_flow(
    project_id: UUID,
    cf_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text(f"""
            SELECT id, version_number, scenario, total_pretax,
                   final_cash_position, min_cash_position,
                   min_position_week_end, bridge_financing_required,
                   owner_net_days, lien_holdback_pct,
                   lien_release_days_post_substantial,
                   deficiency_days_post_occupancy,
                   weekly_periods, phase_costs, milestone_dates, key_dates,
                   based_on_estimate_id, based_on_schedule_id,
                   {_IS_STALE_SQL},
                   notes, created_at
            FROM cash_flows
            WHERE project_id = :pid AND id = :cid
        """),
        {"pid": project_id, "cid": cf_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="cash flow not found")
    cols = ['id', 'version_number', 'scenario', 'total_pretax',
            'final_cash_position', 'min_cash_position',
            'min_position_week_end', 'bridge_financing_required',
            'owner_net_days', 'lien_holdback_pct',
            'lien_release_days_post_substantial', 'deficiency_days_post_occupancy',
            'weekly_periods', 'phase_costs', 'milestone_dates', 'key_dates',
            'based_on_estimate_id', 'based_on_schedule_id', 'is_stale',
            'notes', 'created_at']
    return _row_as_dict(row, cols)


# ── Schedules of values ──────────────────────────────────────────────────
@router.get("/sovs")
def list_sovs(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text(f"""
            SELECT id, version_number, scenario, total_pretax,
                   based_on_estimate_id,
                   {_IS_STALE_SQL},
                   notes, created_at
            FROM schedules_of_values
            WHERE project_id = :pid
            ORDER BY scenario, version_number
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/sovs/{sov_id}")
def get_sov(
    project_id: UUID,
    sov_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text(f"""
            SELECT id, version_number, scenario, total_pretax,
                   months, rows, monthly_totals, cumulative_totals,
                   lien_holdback_10pct, owner_net_monthly,
                   based_on_estimate_id, based_on_schedule_id,
                   {_IS_STALE_SQL},
                   notes, created_at
            FROM schedules_of_values
            WHERE project_id = :pid AND id = :sid
        """),
        {"pid": project_id, "sid": sov_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="sov not found")
    cols = ['id', 'version_number', 'scenario', 'total_pretax',
            'months', 'rows', 'monthly_totals', 'cumulative_totals',
            'lien_holdback_10pct', 'owner_net_monthly',
            'based_on_estimate_id', 'based_on_schedule_id', 'is_stale',
            'notes', 'created_at']
    return _row_as_dict(row, cols)


@router.get("/sov-csv")
def sov_csv(
    project_id: UUID,
    scenario: str = "base",
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Download the Schedule of Values as an AIA G703-style CSV for draw
    submissions to owners/lenders. Reads the latest SOV for the scenario."""
    scenario = "combined" if scenario == "combined" else "base"
    row = db.execute(
        text("""
            SELECT total_pretax, months, rows, monthly_totals, cumulative_totals,
                   lien_holdback_10pct, owner_net_monthly
              FROM schedules_of_values
             WHERE project_id = :p AND scenario = :s
             ORDER BY version_number DESC LIMIT 1
        """),
        {"p": project_id, "s": scenario},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no SOV for this scenario")
    total, months, rows, mt, ct, lien, net = row
    months = months or []
    proj = db.execute(text("SELECT name, slug FROM projects WHERE id = :p"), {"p": project_id}).first()

    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Schedule of Values — {proj.name if proj else ''}"])
    w.writerow([f"Scenario: {scenario}"])
    w.writerow([])
    w.writerow(["Trade #", "Description", "Scheduled Value", *months])
    for r in rows or []:
        monthly = r.get("monthly") or {}
        desc = (r.get("description") or "") + (" (OPTIONAL)" if r.get("optional") else "")
        w.writerow([r.get("tradeNumber", ""), desc, r.get("scheduledValue", 0),
                    *[monthly.get(m, 0) for m in months]])
    w.writerow([])
    w.writerow(["", "Monthly draw (gross)", "", *[(mt or {}).get(m, 0) for m in months]])
    w.writerow(["", "Cumulative", "", *[(ct or {}).get(m, 0) for m in months]])
    w.writerow(["", "Lien holdback 10%", "", *[(lien or {}).get(m, 0) for m in months]])
    w.writerow(["", "Owner net to BRIO (90%)", "", *[(net or {}).get(m, 0) for m in months]])
    w.writerow(["", "Total scheduled value (pre-tax)", total])

    slug = (proj.slug if proj else "project")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": _content_disposition(f"SOV-{slug}-{scenario}.csv", "attachment")},
    )


# ── Contracts ────────────────────────────────────────────────────────────
@router.get("/contracts")
def list_contracts(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text(f"""
            SELECT id, version_number, reference, template_type, status,
                   owner_name, owner_address,
                   base_contract_value, option_contract_value,
                   based_on_estimate_id,
                   {_IS_STALE_SQL},
                   project_start_date, substantial_completion_date, final_completion_date,
                   signed_at, signed_by_owner, signed_by_contractor,
                   created_at
            FROM contracts
            WHERE project_id = :pid
            ORDER BY version_number
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/contracts/{contract_id}")
def get_contract(
    project_id: UUID,
    contract_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text(f"""
            SELECT id, version_number, reference, template_type, status,
                   owner_name, owner_address,
                   base_contract_value, option_contract_value,
                   based_on_estimate_id, based_on_schedule_id, based_on_sov_id,
                   {_IS_STALE_SQL},
                   project_start_date, substantial_completion_date, final_completion_date,
                   payload, signed_at, signed_by_owner, signed_by_contractor,
                   notes, created_at
            FROM contracts
            WHERE project_id = :pid AND id = :cid
        """),
        {"pid": project_id, "cid": contract_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="contract not found")
    cols = ['id', 'version_number', 'reference', 'template_type', 'status',
            'owner_name', 'owner_address',
            'base_contract_value', 'option_contract_value',
            'based_on_estimate_id', 'based_on_schedule_id', 'based_on_sov_id',
            'is_stale',
            'project_start_date', 'substantial_completion_date', 'final_completion_date',
            'payload', 'signed_at', 'signed_by_owner', 'signed_by_contractor',
            'notes', 'created_at']
    d = _row_as_dict(row, cols)
    # Value-based staleness: the id-based `is_stale` misses in-place estimate
    # edits (same version id, changed content). Compare the stored contract value
    # against what the ACTIVE estimate now produces so a drift is always caught.
    exp = _active_estimate_contract_values(db, project_id)
    if exp is not None:
        eb, eo = exp
        d["active_base_contract_value"] = eb
        d["active_option_contract_value"] = eo
        # Signed/executed: never "out of date" — the estimate may carry approved
        # change orders the contract holds separately, and a signed value
        # changes only by change order.
        d["value_stale"] = d.get("status") not in ("signed", "executed") and (
            abs(_num(d.get("base_contract_value")) - eb) > 1.0
            or abs(_num(d.get("option_contract_value")) - eo) > 1.0)
    else:
        d["value_stale"] = False
        d["active_base_contract_value"] = None
        d["active_option_contract_value"] = None
    # Approved change orders fold into the revised contract value (+GST, no markup),
    # matching the standalone contract PDF and the client portal.
    d["approved_co_pretax"] = round(_num(db.execute(
        text("SELECT COALESCE(SUM(cost_impact), 0) FROM change_orders "
             "WHERE project_id = :p AND status = 'approved'"),
        {"p": project_id},
    ).scalar()), 2)
    return d


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _active_estimate_contract_values(db: Session, project_id) -> tuple[float, float] | None:
    """Base + option contract values (marked-up, pre-tax) the ACTIVE estimate
    would currently produce, or None if there's no active estimate."""
    import json as _json
    row = db.execute(
        text("SELECT payload FROM estimate_versions WHERE project_id = :p AND is_active = true"),
        {"p": project_id},
    ).first()
    if not row:
        return None
    est = row[0] if isinstance(row[0], dict) else _json.loads(row[0])
    cats = est.get("categories", []) or []
    mk = (1 + _num(est.get("overhead")) / 100) * (1 + _num(est.get("profit")) / 100)
    base = sum(_num(c.get("total")) for c in cats if not c.get("optional"))
    opt = sum(_num(c.get("total")) for c in cats if c.get("optional"))
    return round(base * mk, 2), round(opt * mk, 2)


@router.post("/contracts", status_code=status.HTTP_201_CREATED)
def create_contract(
    project_id: UUID,
    body: dict = Body(default={}),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Create a contract record from the project's active estimate + latest
    schedule. Computes base/option contract values (with the estimate's
    management fee), key dates, and a payment-schedule payload. Each call
    creates a new version (draft). Office-only; audit-logged.
    """
    est = db.execute(
        text("""SELECT id, reference, payload FROM estimate_versions
                WHERE project_id = :p AND is_active = true"""),
        {"p": project_id},
    ).first()
    if not est:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No active estimate. Set one active before generating a contract.",
        )
    est_id, est_ref, payload = est
    cats = payload.get("categories", []) if isinstance(payload, dict) else []
    oh, pr = _num(payload.get("overhead")), _num(payload.get("profit"))
    mk = (1 + oh / 100) * (1 + pr / 100)
    base_direct = sum(_num(c.get("total")) for c in cats if not c.get("optional"))
    opt_direct = sum(_num(c.get("total")) for c in cats if c.get("optional"))
    base_pretax = round(base_direct * mk, 2)
    opt_pretax = round(opt_direct * mk, 2)

    sched = db.execute(
        text("""SELECT id, project_start, deterministic_finish_date,
                       p50_finish_date, p80_finish_date
                  FROM schedules WHERE project_id = :p
                 ORDER BY created_at DESC LIMIT 1"""),
        {"p": project_id},
    ).first()
    sched_id = sched.id if sched else None
    start_d = sched.project_start if sched else None
    subst_d = (sched.p50_finish_date or sched.deterministic_finish_date) if sched else None
    final_d = (sched.p80_finish_date or sched.deterministic_finish_date) if sched else None

    proj = db.execute(
        text("SELECT slug, name, client_id FROM projects WHERE id = :p"), {"p": project_id}
    ).first()
    owner_name = owner_address = None
    if proj and proj.client_id:
        cl = db.execute(
            text("SELECT name, address FROM clients WHERE id = :c"), {"c": proj.client_id}
        ).first()
        if cl:
            owner_name, owner_address = cl.name, cl.address

    import re as _re
    slug_token = _re.sub(r"[^A-Z0-9]+", "-", str((proj.slug if proj else "") or "PROJECT").upper()).strip("-") or "PROJECT"
    next_ver = (db.execute(
        text("SELECT COALESCE(MAX(version_number), 0) FROM contracts WHERE project_id = :p"),
        {"p": project_id},
    ).scalar() or 0) + 1
    reference = f"CONTRACT-{slug_token}-{next_ver:03d}"

    # 30 / 30 / 30 / 10 draw schedule on the base contract total (incl. GST).
    total_incl = round(base_pretax * 1.05, 2)
    weights = [("Deposit — on signing", 30), ("At building lock-up", 30),
               ("At interior finishes", 30), ("Completion — holdback", 10)]
    draws, acc = [], 0.0
    for i, (label, pct) in enumerate(weights):
        amt = round(total_incl - acc, 2) if i == len(weights) - 1 else round(total_incl * pct / 100, 2)
        acc += amt
        draws.append({"milestone": label, "pct": pct, "amount": amt})

    import json as _json
    contract_payload = _json.dumps({
        "ownerName": owner_name,
        "ownerAddress": owner_address,
        "paymentSchedule": draws,
        "generated_from_estimate": est_ref,
    })

    row = db.execute(
        text("""
            INSERT INTO contracts
                (project_id, version_number, reference, template_type, status,
                 owner_name, owner_address, base_contract_value, option_contract_value,
                 based_on_estimate_id, based_on_schedule_id,
                 project_start_date, substantial_completion_date, final_completion_date,
                 payload, created_by_id)
            VALUES
                (:pid, :ver, :ref, 'CCDC2', 'draft',
                 :on, :oa, :bcv, :ocv,
                 :eid, :sid,
                 :sd, :subd, :fd,
                 CAST(:pl AS jsonb), :uid)
            RETURNING id
        """),
        {"pid": project_id, "ver": next_ver, "ref": reference,
         "on": owner_name, "oa": owner_address, "bcv": base_pretax, "ocv": opt_pretax,
         "eid": est_id, "sid": sched_id,
         "sd": start_d, "subd": subst_d, "fd": final_d,
         "pl": contract_payload, "uid": user.id},
    ).first()
    db.execute(
        text("""INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id)
                VALUES (:pid, 'contracts', :cid, 'create', :uid)"""),
        {"pid": project_id, "cid": row.id, "uid": user.id},
    )
    db.commit()
    return {"id": str(row.id), "reference": reference, "version_number": next_ver, "status": "draft"}


_CONTRACT_STATUSES = ("draft", "issued", "signed", "executed")


@router.post("/contracts/{contract_id}/status", status_code=status.HTTP_200_OK)
def set_contract_status(
    project_id: UUID,
    contract_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Advance the contract lifecycle: draft → issued → signed → executed.

    Signing stamps signed_at + the owner's name; executing records the
    contractor signatory. Reverting to draft/issued clears the signature
    fields so stale signing metadata can't linger. Office-only; audit-logged.
    """
    new_status = str(body.get("status") or "").strip().lower()
    if new_status not in _CONTRACT_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {_CONTRACT_STATUSES}",
        )
    row = db.execute(
        text("SELECT id, status FROM contracts WHERE project_id = :p AND id = :c"),
        {"p": project_id, "c": contract_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="contract not found")

    signed_by_owner = (str(body.get("signed_by_owner") or "")).strip() or None
    signed_by_contractor = (str(body.get("signed_by_contractor") or "")).strip() or None
    signed_date = (str(body.get("signed_date") or "")).strip() or None  # ISO 'YYYY-MM-DD'

    sets = ["status = :st"]
    params: dict = {"st": new_status, "p": project_id, "c": contract_id}

    if new_status in ("signed", "executed"):
        if signed_date:
            sets.append("signed_at = CAST(:sat AS timestamptz)")
            params["sat"] = signed_date
        else:
            sets.append("signed_at = COALESCE(signed_at, now())")
        if signed_by_owner:
            sets.append("signed_by_owner = :sbo")
            params["sbo"] = signed_by_owner
        if new_status == "executed":
            sets.append(
                "signed_by_contractor = COALESCE(:sbc, signed_by_contractor, "
                "'Amir Omidvar, BRIO Construction Inc.')"
            )
            params["sbc"] = signed_by_contractor
    else:
        # Reverting the lifecycle — drop any captured signature metadata.
        sets += ["signed_at = NULL", "signed_by_owner = NULL", "signed_by_contractor = NULL"]

    db.execute(
        text(f"UPDATE contracts SET {', '.join(sets)} WHERE project_id = :p AND id = :c"),
        params,
    )
    db.execute(
        text("""
            INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id)
            VALUES (:pid, 'contracts', :cid, :act, :uid)
        """),
        {"pid": project_id, "cid": contract_id, "act": f"status:{new_status}", "uid": user.id},
    )
    db.commit()
    return {"id": str(contract_id), "status": new_status}


@router.put("/contracts/{contract_id}/signatories", status_code=status.HTTP_200_OK)
def set_contract_signatories(
    project_id: UUID,
    contract_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Replace the contract's signatory list (stored in payload.signatories) —
    a list of {name, title} rendered as signature blocks in the PDF. Empty rows
    are dropped. Office-only; audit-logged.
    """
    sigs = body.get("signatories")
    if not isinstance(sigs, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="signatories must be a list")
    clean: list[dict] = []
    for s in sigs:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        title = str(s.get("title") or "").strip()
        if name or title:
            clean.append({"name": name, "title": title})

    row = db.execute(
        text("SELECT payload FROM contracts WHERE project_id = :p AND id = :c"),
        {"p": project_id, "c": contract_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="contract not found")
    payload = dict(row[0] or {})
    payload["signatories"] = clean

    import json as _json
    db.execute(
        text("UPDATE contracts SET payload = CAST(:pl AS jsonb) WHERE project_id = :p AND id = :c"),
        {"pl": _json.dumps(payload), "p": project_id, "c": contract_id},
    )
    db.execute(
        text("""INSERT INTO audit_logs (project_id, table_name, row_id, action, user_id)
                VALUES (:pid, 'contracts', :cid, 'signatories', :uid)"""),
        {"pid": project_id, "cid": contract_id, "uid": user.id},
    )
    db.commit()
    return {"id": str(contract_id), "signatories": clean}


# ── Narratives ───────────────────────────────────────────────────────────
@router.get("/narratives")
def list_narratives(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    # A row is superseded exactly when another row chains back to it. Without
    # this the index lists every version of every note side by side with nothing
    # saying which one is current, and the reader opens a stale one — the precise
    # failure the append-only history exists to prevent.
    rows = db.execute(
        text("""
            SELECT n.id, n.parent_table, n.parent_id, n.title, n.version_number,
                   n.created_at, octet_length(n.content_md) AS content_size,
                   EXISTS (SELECT 1 FROM narratives c
                           WHERE c.parent_version_id = n.id) AS is_superseded
            FROM narratives n
            WHERE n.project_id = :pid
            ORDER BY n.created_at DESC
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/narratives/{nid}")
def get_narrative(
    project_id: UUID,
    nid: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text("""
            SELECT id, parent_table, parent_id, title, content_md,
                   version_number, created_at
            FROM narratives
            WHERE project_id = :pid AND id = :nid
        """),
        {"pid": project_id, "nid": nid},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="narrative not found")
    cols = ['id', 'parent_table', 'parent_id', 'title', 'content_md',
            'version_number', 'created_at']
    out = _row_as_dict(row, cols)
    out["images"] = _resolve_narrative_images(db, project_id, out.get("content_md") or "")
    # Where this version was superseded, say so and say by what — a reader who
    # arrived on an old link needs the way forward, not just a version number.
    nxt = db.execute(
        text("""SELECT id, version_number FROM narratives
                WHERE parent_version_id = :nid
                ORDER BY version_number LIMIT 1"""),
        {"nid": nid},
    ).mappings().first()
    out["superseded_by"] = (
        {"id": str(nxt["id"]), "version_number": nxt["version_number"]} if nxt else None
    )
    return out


#: Images are referenced in `content_md` as `![alt](doc:<document-uuid>)` rather
#: than by URL. R2 presigns expire (900s), so a URL stored in the markdown would
#: be dead long before anyone read it; and a `data:` URI is silently dropped by
#: react-markdown's `defaultUrlTransform`, whose safe-protocol list is
#: http/https/irc/mailto/xmpp only. Storing the document id and minting a fresh
#: presign on every read is the same pattern the owner portal already uses.
_DOC_REF = re.compile(r"doc:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def _resolve_narrative_images(db: Session, project_id: UUID, content_md: str) -> dict[str, str]:
    """Map each `doc:<uuid>` referenced in the markdown to a fresh presigned URL.

    Only documents belonging to THIS project resolve — a reference to another
    project's document yields no entry, so the image simply does not render.
    """
    ids = {m.group(1).lower() for m in _DOC_REF.finditer(content_md or "")}
    if not ids:
        return {}
    from app.services.r2 import presign_get

    rows = db.execute(
        text("""SELECT id, r2_key FROM documents
                WHERE project_id = :pid AND NOT archived AND id = ANY(:ids)"""),
        {"pid": project_id, "ids": [UUID(i) for i in ids]},
    ).mappings().all()
    # 3600s, not the 900s default: a brief is read slowly, and a re-request
    # after an expired presign renders as a broken image with no explanation.
    return {str(r["id"]): presign_get(r["r2_key"], expires=3600) for r in rows}


@router.post("/narratives", status_code=status.HTTP_201_CREATED)
def create_narrative(
    project_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Write a project note. Markdown body, optionally hung off another record.

    `parent_table` says what the note is about ('projects' for project-wide
    context, 'schedules', 'cash_flows', 'drawing_analyses', ...). It is NOT a
    foreign key — a note can outlive the thing it describes.
    """
    title = (body.get("title") or "").strip()
    content_md = body.get("content_md") or ""
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="title required")
    if not content_md.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="content_md required")

    parent_table = (body.get("parent_table") or "projects").strip()
    parent_id = body.get("parent_id") or (str(project_id) if parent_table == "projects" else None)

    nid = uuid4()
    db.execute(
        text("""INSERT INTO narratives
                  (id, project_id, parent_table, parent_id, title, content_md,
                   version_number, created_by_id)
                VALUES (:id, :pid, :pt, :parent, :title, :md, 1, :uid)"""),
        {"id": nid, "pid": project_id, "pt": parent_table, "parent": parent_id,
         "title": title, "md": content_md, "uid": user.id},
    )
    db.commit()
    return {"id": str(nid), "title": title, "version_number": 1,
            "parent_table": parent_table, "parent_id": parent_id}


@router.put("/narratives/{nid}")
def revise_narrative(
    project_id: UUID,
    nid: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Supersede a note with a new version.

    History is append-only: the prior row is left exactly as it was and a new
    row is written pointing back at it through `parent_version_id`. Editing in
    place would destroy the record of what the site was told last week, which
    is the whole reason these are versioned.
    """
    prev = db.execute(
        text("""SELECT id, parent_table, parent_id, title, version_number
                FROM narratives WHERE project_id = :pid AND id = :nid"""),
        {"pid": project_id, "nid": nid},
    ).mappings().first()
    if not prev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="narrative not found")

    # Revising a version that has already been revised FORKS the history: two
    # rows claim the same version number, both look current, and the edit is
    # applied to a body that is already out of date. Refuse, and say where the
    # live version is — the caller almost always holds a stale id, not bad intent.
    child = db.execute(
        text("""SELECT id, version_number FROM narratives
                WHERE parent_version_id = :nid ORDER BY version_number LIMIT 1"""),
        {"nid": nid},
    ).mappings().first()
    if child:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(f"v{prev['version_number']} was already superseded by "
                    f"v{child['version_number']} ({child['id']}). Revise that one instead — "
                    f"revising this would fork the history."),
        )

    content_md = body.get("content_md") or ""
    if not content_md.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="content_md required")
    title = (body.get("title") or prev["title"]).strip()

    new_id = uuid4()
    db.execute(
        text("""INSERT INTO narratives
                  (id, project_id, parent_table, parent_id, title, content_md,
                   version_number, parent_version_id, created_by_id)
                VALUES (:id, :pid, :pt, :parent, :title, :md, :ver, :prev, :uid)"""),
        {"id": new_id, "pid": project_id, "pt": prev["parent_table"],
         "parent": prev["parent_id"], "title": title, "md": content_md,
         "ver": prev["version_number"] + 1, "prev": prev["id"], "uid": user.id},
    )
    db.commit()
    return {"id": str(new_id), "title": title,
            "version_number": prev["version_number"] + 1,
            "supersedes": str(prev["id"])}


@router.get("/narratives/{nid}/pdf")
def narrative_pdf(
    project_id: UUID,
    nid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """The note as a BRIO-branded PDF, so it can go to a supplier who has no login.

    Streamed rather than stored: a takeoff sent out for pricing is a snapshot of
    a note that is still being revised, and keeping every snapshot as a Document
    would bury the drawings under near-duplicates. The note itself is the record;
    this is a copy of it.
    """
    from fastapi.responses import Response

    from app.services.narrative_pdf import render_narrative_pdf
    from app.services.r2 import get_bytes

    row = db.execute(
        text("""SELECT id, title, content_md, version_number, created_at
                FROM narratives WHERE project_id = :pid AND id = :nid"""),
        {"pid": project_id, "nid": nid},
    ).mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="narrative not found")

    # Image bytes are INLINED, not linked. A presigned URL would render today and
    # break silently once it expires, and a PDF outlives its presign by years.
    images: dict[str, bytes] = {}
    ids = {m.group(1).lower() for m in _DOC_REF.finditer(row["content_md"] or "")}
    if ids:
        docs = db.execute(
            text("""SELECT id, r2_key FROM documents
                    WHERE project_id = :pid AND NOT archived AND id = ANY(:ids)"""),
            {"pid": project_id, "ids": [UUID(i) for i in ids]},
        ).mappings().all()
        for d in docs:
            try:
                images[str(d["id"])] = get_bytes(d["r2_key"])
            except Exception:  # noqa: BLE001 - a missing asset must not lose the PDF
                logger.warning("narrative pdf: image %s unreadable", d["id"])

    project = db.execute(
        text("SELECT name FROM projects WHERE id = :p"), {"p": project_id},
    ).mappings().first()

    pdf = render_narrative_pdf(dict(row), dict(project or {}), images)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", row["title"]).strip("-")[:80]
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'},
    )


# ── Audit logs ───────────────────────────────────────────────────────────
@router.get("/audit-logs")
def list_audit_logs(
    project_id: UUID,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, table_name, row_id, action, diff, user_id, created_at
            FROM audit_logs
            WHERE project_id = :pid
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"pid": project_id, "lim": limit},
    )
    return _rows_as_dicts(rows)


@router.get("/presentations")
def list_presentations(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, audience, version_number, label, status,
                   based_on_estimate_id, share_token, ai_job_id,
                   created_at, created_by_id
            FROM presentations
            WHERE project_id = :pid
            ORDER BY created_at DESC
        """),
        {"pid": project_id},
    )
    return _rows_as_dicts(rows)


@router.get("/presentations/{presentation_id}")
def get_presentation(
    project_id: UUID,
    presentation_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    row = db.execute(
        text("""
            SELECT id, project_id, audience, version_number, label, status, payload,
                   based_on_estimate_id, based_on_schedule_id, based_on_cash_flow_id,
                   share_token, pdf_r2_key, ai_job_id, created_at, created_by_id
            FROM presentations
            WHERE project_id = :pid AND id = :prid
        """),
        {"pid": project_id, "prid": presentation_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="presentation not found")
    cols = ['id', 'project_id', 'audience', 'version_number', 'label', 'status', 'payload',
            'based_on_estimate_id', 'based_on_schedule_id', 'based_on_cash_flow_id',
            'share_token', 'pdf_r2_key', 'ai_job_id', 'created_at', 'created_by_id']
    return _row_as_dict(row, cols)


@router.get("/presentations/{presentation_id}/pdf")
def presentation_pdf(
    project_id: UUID,
    presentation_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> Response:
    import json as _json

    from app.services.presentation_pdf import render_presentation_pdf

    row = db.execute(
        text("SELECT payload, label FROM presentations WHERE project_id = :pid AND id = :prid"),
        {"pid": project_id, "prid": presentation_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="presentation not found")
    payload = row["payload"]
    if isinstance(payload, str):
        payload = _json.loads(payload)

    project = db.execute(text("SELECT * FROM projects WHERE id = :pid"), {"pid": project_id}).mappings().first()
    project = dict(project) if project else {}
    client = None
    if project.get("client_id"):
        c = db.execute(text("SELECT * FROM clients WHERE id = :cid"), {"cid": project["client_id"]}).mappings().first()
        client = dict(c) if c else None

    pdf_bytes = render_presentation_pdf(payload, project, client)
    filename = f"{(row['label'] or 'presentation').strip()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/estimate-versions/{version_id}/regeneration-plan")
def get_regeneration_plan(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Preview what activating this estimate version will regenerate downstream,
    with a per-artifact token-cost breakdown (for the approval notice)."""
    from app.services.regeneration import regeneration_plan
    return regeneration_plan(db, project_id)


@router.post("/estimate-versions/{version_id}/activate-with-regeneration")
def activate_with_regeneration(
    project_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_owner),
) -> dict:
    """Owner-approved: activate this estimate version AND regenerate the
    downstream documents (scope/schedule/presentation via AI jobs; cash flow /
    SOV / contract deterministically). Records an approval-ledger entry."""
    exists = db.execute(
        text("SELECT 1 FROM estimate_versions WHERE id = :e AND project_id = :p"),
        {"e": version_id, "p": project_id},
    ).first()
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    from app.services.regeneration import execute_regeneration
    return execute_regeneration(db, project_id, version_id, user)


@router.get("/prior-client-estimate")
def get_prior_client_estimate(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """The prior estimate figure/date/context the client already saw — used by
    the presentation's 'why higher' section (persists across regenerations)."""
    row = db.execute(
        text("SELECT prior_client_estimate FROM projects WHERE id = :p"), {"p": project_id},
    ).scalar()
    return {"prior_client_estimate": row}


@router.put("/prior-client-estimate")
def set_prior_client_estimate(
    project_id: UUID,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Set (or clear, with {}/null) the project's prior client estimate.
    Expected shape: {total: number, date: string, note: string}."""
    import json as _json
    value = body.get("prior_client_estimate", body) if isinstance(body, dict) else None
    if not value or not value.get("total"):
        db.execute(text("UPDATE projects SET prior_client_estimate = NULL WHERE id = :p"), {"p": project_id})
        db.commit()
        return {"prior_client_estimate": None}
    clean = {"total": value.get("total"), "date": value.get("date"), "note": value.get("note")}
    db.execute(
        text("UPDATE projects SET prior_client_estimate = CAST(:v AS jsonb) WHERE id = :p"),
        {"v": _json.dumps(clean), "p": project_id},
    )
    db.commit()
    return {"prior_client_estimate": clean}


@router.post("/presentations/{presentation_id}/share")
def share_presentation(
    project_id: UUID,
    presentation_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Generate (or return) a public, tokenized client link to the presentation
    proposal PDF — for presenting later / leaving with the client."""
    from app.services.share_token import sign_presentation_token
    row = db.execute(
        text("SELECT id FROM presentations WHERE id = :i AND project_id = :p"),
        {"i": presentation_id, "p": project_id},
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="presentation not found")
    token = sign_presentation_token(str(presentation_id))
    db.execute(text("UPDATE presentations SET share_token = :t WHERE id = :i"),
               {"t": token, "i": presentation_id})
    db.commit()
    base = "https://104-198-45-146.nip.io/brio-pm"
    return {"share_token": token, "url": f"{base}/public/presentation/{token}"}


# ── Email sharing estimate packages (Amir, 2026-10-27) ────────────────────────
# The assistant drafts; Amir edits and sends from his own mail. Nothing is sent
# from here. See services/estimate_email.py.

def _share_versions(db: Session, project_id: UUID, version_ids: list[UUID] | None = None) -> list[dict]:
    from app.services.estimate_scope import scope_for_pdf
    from app.services.estimate_selection_pdf import client_totals

    rows = db.execute(text(
        "SELECT id, version_number, reference, is_active, payload, created_at FROM estimate_versions "
        "WHERE project_id = :p ORDER BY version_number DESC"), {"p": project_id}).all()
    out = []
    for r in rows:
        if version_ids is not None and r[0] not in version_ids:
            continue
        payload = r[4] if isinstance(r[4], dict) else __import__("json").loads(r[4] or "{}")
        sc, _n = scope_for_pdf(db, project_id, r[0])
        sp = (sc[2] if sc is not None and isinstance(sc[2], dict) else {}) if sc is not None else {}
        t = client_totals(payload)
        summary = (sp.get("summary") or "").strip()
        title = (sp.get("title") or (summary.split(".")[0][:90] if summary else "") or r[2])
        out.append({"id": str(r[0]), "version_number": r[1], "reference": r[2], "is_active": r[3],
                    "created_at": r[5].isoformat() if r[5] else None, "payload": payload,
                    "scope": sp if sc is not None else None, "scope_reference": sp.get("reference"),
                    "title": title, "subtotal": round(t["subtotal"], 2), "total": round(t["total"], 2)})
    return out


@router.get("/estimate-email/options")
def estimate_email_options(project_id: UUID, db: Session = Depends(get_db),
                           _: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Estimates that can go in a client email, with the lump-sum figures their
    packages print and the scope each belongs to."""
    return [{k: v for k, v in o.items() if k not in ("payload", "scope")} for o in _share_versions(db, project_id)]


class EstimateEmailBody(BaseModel):
    version_ids: list[UUID] = Field(min_length=1, max_length=6)
    instructions: str = Field(default="", max_length=2000)


@router.post("/estimate-email/drafts", status_code=status.HTTP_202_ACCEPTED)
def draft_estimate_email(project_id: UUID, body: EstimateEmailBody, db: Session = Depends(get_db),
                         user: CurrentUser = Depends(get_current_user)) -> dict:
    """Queue the assistant's draft of the email sharing these packages."""
    from app.models.ai_job import AIJob
    from app.routers.milestones import _sender
    from app.services.estimate_email import PURPOSE, package_context

    chosen = _share_versions(db, project_id, list(body.version_ids))
    if len(chosen) != len(set(body.version_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="An estimate is not on this project.")
    proj = db.execute(text("SELECT p.name, p.address, c.name, c.email, c.phone FROM projects p "
                           "LEFT JOIN clients c ON c.id = p.client_id WHERE p.id = :p"), {"p": project_id}).first()
    packages, attachments = [], []
    for v in chosen:
        pkg = package_context(v, v["scope"])
        packages.append(pkg)
        attachments.append(f"{pkg['name']}: scope, material selections and lump-sum price (PDF)")
    ctx = {"project": {"name": proj[0], "address": proj[1]} if proj else {},
           "client": {"organisation": proj[2], "contact_name": None, "email": proj[3]} if proj else {},
           "sender": _sender(db, user), "packages": packages, "attachments": attachments}
    job = AIJob(id=uuid4(), project_id=project_id, kind="email_draft", status="queued",
                input={"purpose": PURPOSE, "context": ctx, "instructions": body.instructions,
                       "version_ids": [str(v) for v in body.version_ids]})
    db.add(job)
    db.commit()
    return {"job_id": str(job.id)}


@router.get("/estimate-email/drafts")
def list_estimate_email_drafts(project_id: UUID, db: Session = Depends(get_db),
                               _: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """This project's earlier drafts, newest first, so a closed panel loses
    nothing (Amir, 2026-10-27). The assistant's text as drafted; edits made in
    the panel are not saved."""
    from app.models.ai_job import AIJob

    jobs = (db.query(AIJob)
            .filter(AIJob.project_id == project_id, AIJob.kind == "email_draft",
                    AIJob.input["purpose"].astext == "estimate_share")
            .order_by(AIJob.created_at.desc()).limit(20).all())
    out = []
    for j in jobs:
        o = j.output or {}
        out.append({"job_id": str(j.id), "status": j.status,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                    "subject": (o.get("draft") or {}).get("subject"),
                    "version_ids": (j.input or {}).get("version_ids") or []})
    return out


@router.get("/estimate-email/drafts/{job_id}")
def get_estimate_email_draft(project_id: UUID, job_id: UUID, db: Session = Depends(get_db),
                             _: CurrentUser = Depends(get_current_user)) -> dict:
    from app.models.ai_job import AIJob

    job = db.get(AIJob, job_id)
    if job is None or job.project_id != project_id or (job.input or {}).get("purpose") != "estimate_share":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
    out = job.output or {}
    return {"status": job.status, "error": job.error, "draft": out.get("draft"),
            "warnings": out.get("warnings") or [], "version_ids": (job.input or {}).get("version_ids") or [],
            "created_at": job.created_at.isoformat() if job.created_at else None}



# ── Div 09 scope review for the by-division PDF (2026-10-27) ──────────────────

def _version_payload(db: Session, project_id: UUID, version_id: UUID) -> dict:
    row = db.execute(text("SELECT payload FROM estimate_versions WHERE project_id = :p AND id = :v"),
                     {"p": project_id, "v": version_id}).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    return row[0] if isinstance(row[0], dict) else __import__("json").loads(row[0] or "{}")


@router.get("/estimate-versions/{version_id}/subscopes")
def get_subscopes(project_id: UUID, version_id: UUID, division: str = "09", db: Session = Depends(get_db),
                  _: CurrentUser = Depends(get_current_user)) -> dict:
    """A division's lines with their suggested and saved scope, and the prices
    the PDF will print for each scope."""
    from app.services import division_breakdown as DB

    payload = _version_payload(db, project_id, version_id)
    div = next((d for d in DB.breakdown(payload)["divisions"] if d["code"] == division), None)
    return {"division": division, "labels": DB.scope_labels(division),
            "lines": DB.review_lines(payload, division),
            "scopes": [{"label": s["label"], "amount": s["amount"]} for s in (div or {}).get("scopes") or []]}


class SubscopeChoice(BaseModel):
    cat_index: int
    item_index: int
    scope: str


class SubscopeBody(BaseModel):
    division: str = "09"
    choices: list[SubscopeChoice] = Field(max_length=500)


@router.put("/estimate-versions/{version_id}/subscopes")
def save_subscopes(project_id: UUID, version_id: UUID, body: SubscopeBody, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """Store the scope chosen for each line on the estimate itself. Snapshots the
    version first, like every in-place estimate edit. Prices do not change."""
    from app.services import division_breakdown as DB
    from app.services.revisions import actor_uuid, snapshot

    payload = _version_payload(db, project_id, version_id)
    try:
        changed = DB.apply_choices(payload, body.division, [c.model_dump() for c in body.choices])
    except (ValueError, IndexError, KeyError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    if changed:
        snapshot(db, table="estimate_versions", row_id=version_id, project_id=project_id,
                 action="edit", actor_id=actor_uuid(user), reason="Div 09 scope grouping for the by-division PDF")
        db.execute(text("UPDATE estimate_versions SET payload = CAST(:pl AS jsonb) WHERE project_id = :p AND id = :v"),
                   {"pl": __import__("json").dumps(payload), "p": project_id, "v": version_id})
        db.commit()
    return {"changed": changed, **get_subscopes(project_id, version_id, body.division, db=db, _=user)}
