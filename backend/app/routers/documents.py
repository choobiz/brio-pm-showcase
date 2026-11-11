"""Documents router — Track B.

Upload flow is two-step so large files never transit the API:
  1. POST /documents/presign  → create a Document row, return a presigned R2
     PUT URL. The browser uploads the bytes directly to R2.
  2. POST /documents/{id}/finalize → record the uploaded size; the document
     now appears in listings.

Downloads return a short-lived presigned R2 GET URL. Deletes are soft
(`archived=true`) — the R2 object and the audit trail are never destroyed.
Every upload / download / delete writes an `ActivityLog` row.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import mimetypes
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    assert_project_access,
    get_current_user,
    require_office,
)
from app.data.bc_doc_checklist import BC_DOC_CHECKLIST
from app.db import get_db
from app.models.activity_log import ActivityLog
from app.models.document import Document
from app.schemas.document import (
    CategorySuggestRequest,
    DocumentBulkResult,
    DocumentBulkUpdate,
    DocumentDownloadResponse,
    DocumentPreviewResponse,
    DocumentFinalizeRequest,
    DocumentPresignRequest,
    DocumentPresignResponse,
    DocumentRead,
    DocumentThumbnailResponse,
    DocumentUpdate,
)
from app.schemas.document_summary import DocumentIndexBadge, ProjectIndexRead
from app.services.r2 import presign_download, presign_inline, get_bytes, head_size, object_exists, presign_get, presign_put, put_bytes

# CAD / plot formats mimetypes doesn't know — extension is authoritative for these.
_EXT_MIME = {".dwg": "application/acad", ".dxf": "application/dxf", ".pcp": "application/octet-stream"}


def _mime_from_filename(name: str) -> str | None:
    """Derive the mime from the filename extension (more reliable than the
    client-supplied content_type — fixes e.g. a .dwg uploaded as application/pdf)."""
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in (name or "") else ""
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    guessed, _ = mimetypes.guess_type(name or "")
    return guessed

# Longest edge of a generated document thumbnail, in pixels.
_THUMB_MAX_PX = 480


def _make_thumbnail(data: bytes) -> bytes:
    """Resize image bytes to a small JPEG thumbnail (aspect preserved)."""
    from PIL import Image  # lazy — Pillow ships with weasyprint in the image

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    img.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _actor_uuid(user: CurrentUser) -> UUID | None:
    raw = getattr(user, "id", None)
    return UUID(raw) if raw else None


def _log(
    db: Session, *, project_id: UUID, user: CurrentUser, action: str, **payload
) -> None:
    db.add(
        ActivityLog(
            project_id=project_id,
            actor_id=_actor_uuid(user),
            action=action,
            payload=payload,
        )
    )


def _ext(name: str) -> str:
    """".pdf" from "plan.PDF" (lower-cased); "" when there is no extension."""
    m = re.search(r"(\.[A-Za-z0-9]{1,8})$", name or "")
    return m.group(1).lower() if m else ""


def _safe_filename(name: str) -> str:
    """Strip anything path-unsafe; keep a sane fallback."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()).strip("._")
    return cleaned[:160] or "file"


# Segment boundaries for the heuristics below.
#
# `_safe_filename()` rewrites spaces and commas to "_" before a name ever
# reaches these patterns (the inbound-email path in
# app/services/inbound_attachments.py categorises the *sanitised* name), and
# "_" is a word character — so r"\belec\b" does NOT fire on "..._Elec.pdf".
# That is exactly why consultant-named PDFs were all landing in `other`.
# These lookarounds treat "_", "-", "." and whitespace alike as separators.
# `_suggest_category` lowercases first, so [a-z0-9] is the full alphabet here.
_SEG_L = r"(?<![a-z0-9])"
_SEG_R = r"(?![a-z0-9])"

#: Document-type words that outrank a discipline mention. "Mechanical" in a
#: filename is weak evidence of a drawing — the corpus has
#: "Mechanical _ Ventilation Checklist.pdf" and "Garden Electrical Issue.docx",
#: neither of which is one. A trade name plus a document noun is a document
#: ABOUT that trade, not a drawing OF it.
#:
#: Deliberately NOT here: "schedule" (a door schedule and a letter of assurance
#: are both real members of a drawing set) and "set"/"package" (those argue FOR
#: a drawing). The bare "- Elec.pdf" and "MECH - ... Issued Set.pdf" forms stay
#: drawings, which is what this corpus shows they are.
_DOC_TYPE_NOUN = (
    r"(?:checklists?|manuals?|handbooks?|invoices?|receipts?|quotes?|quotations?"
    r"|proposals?|letters?|certificates?|warrant(?:y|ies)|minutes|logs?|issues?"
    r"|notices?|summar(?:y|ies)|inspections?|instructions?)"
)
#: A discipline word only means "drawing" when no document noun is present.
_NOT_A_DOC = rf"^(?!.*{_SEG_L}{_DOC_TYPE_NOUN}{_SEG_R})"


# ── ZIP auto-categorisation heuristics (Path A from nav redesign). ──
# Filename-based pattern match — the review modal lets the user fix any
# misclassification before the rows commit. Order matters: first match wins.
_CATEGORY_HEURISTICS: list[tuple[str, str]] = [
    # BCBC letters of assurance (Schedule A / B / C-A / C-B / C-1 / C-2). These
    # MUST stay first: the filenames almost always also name a discipline
    # ("Schedule B - MECH and ELEC", "Structural Engineer Schedule CB"), and they
    # belong to the building-permit package, not to the drawing set.
    #
    # The C-form covers "C-A"/"C-B"/"CA"/"CB"/"C-1"/"C-2" — "Schedule CB" (with no
    # separator) is the dominant spelling in this corpus, and without it those
    # files fall through to whatever discipline word appears next in the name
    # (e.g. "Structural Engineer Schedule CB.pdf" was landing as `drawing`).
    #
    # The leading guard keeps CONTRACT schedules out: BRIO's own contracts carry
    # Schedules A-G (Article A-2), so "Contract Schedule B - Pricing.pdf" is a
    # pricing annex, not a letter of assurance. It must fall through to the
    # contract rule. Note the guard cannot be done by simply ordering the contract
    # rule first — that rule also matches bare "signed", which would swallow the
    # real "Schedule B (Signed).pdf" assurance letter.
    # The guard uses the segment boundaries, not `\b`: bare "contract" also matches
    # "Contractor" (so "Schedule B - Contractor Statement.pdf" would wrongly fall
    # through to the contract rule), while `\b` fails on the sanitised
    # "Contract_Schedule_B" because `_` is a word character.
    (
        rf"^(?!.*{_SEG_L}(?:contract|ccdc){_SEG_R}).*{_SEG_L}schedule[\s_-]+"
        rf"(?:c[\s_-]*[ab12]?|[ab])(?![a-z0-9])",
        "permit",
    ),
    (r"letters?[\s_-]*of[\s_-]*assurance", "permit"),
    # Drawings — discipline tags. The bare sheet-code alternates that used to live
    # on these five lines (`\ba\d+[-\.]?\d*\b` etc.) were REMOVED 2026-08-11: a
    # bare letter+digit token matches company and vendor names, not just sheet
    # refs. Measured over the 1,691 uncategorised documents, that form produced 51
    # false `drawing` hits — "Sister Co Resume.pdf" / "Sister Co Bonus.pdf" (Sister Co
    # is one of our own companies), "A1 Doors (Exterior Doors Only).pdf" (A1 Doors
    # is a door supplier), "M3415-15 Signed.pdf" (a reference number). Because
    # these rules sit above the money rules, they also STOLE correct categories:
    # "A1 Doors Statement of Account.pdf" and "SWMP Payment Receipt (Sister Co).pdf"
    # were becoming `drawing` instead of `invoice_legacy`.
    # NOTE: the discipline tags used to be matched HERE as five separate rules,
    # duplicating the consultant-tag rescue rule at the bottom of this list. The
    # duplication was not harmless: sitting above the scope rule, it turned
    # "Electrical Scope 4100 Spruce St.xlsx" and "Electrical Specs.pdf" into
    # drawings, because a discipline word was tested before anyone asked what
    # kind of document it was. The single rescue rule at the end does the same
    # job in the only position where it is safe — after every stronger signal.
    # Sheet refs keep working in the two forms that are actually unambiguous:
    # TERMINAL (the whole name is the sheet — "A1.pdf", "A-101.pdf", "A1.1.pdf"),
    # handled here; and HYPHENATED anywhere in the name ("… - A-101 …"), handled by
    # the appended `[asmep]-\d{1,3}` rule further down. A bare token mid-name is
    # deliberately no longer enough.
    # The extension is excluded from the image types on purpose: this rule sits
    # above the photo-by-extension entry, and "A-101.jpg" is a photograph (often of
    # a drawing) — it must still classify as `photo`, not `drawing`.
    (
        r"(?<![a-z0-9])[asmep][-\.]?\d{1,3}(?:\.\d{1,2})?\s*"
        r"(?=\.(?!jpe?g|png|heic|tiff?|gif|webp|bmp)[a-z0-9]{2,4}$)",
        "drawing",
    ),
    (r"drawing|\bdwg\b|\bplan\b|elevation|section|site\s*plan|floor\s*plan", "drawing"),
    # Reports
    (r"\bsmp\b|stormwater|geotech|hazmat|asbestos", "report"),
    (r"\breport\b|\baudit\b|\bsurvey\b|inspection\s*report", "report"),
    # Contract
    # Bounded: bare "contract" also matches "contractor" and "subcontractor", so
    # "01-contractor-identification.pdf" and a "Subcontractor Spec & Pricing
    # Request" were both filing as contracts. Neither is one.
    (rf"{_SEG_L}(contracts?|agreements?|ccdc){_SEG_R}", "contract"),
    # A bare "Signed" is weak on its own: "Home Maintenance Manual
    # Signed.pdf" is a manual that happens to be signed, not a contract.
    (rf"{_NOT_A_DOC}.*\bsigned\b", "contract"),
    # Scope
    (r"\bscope\b|specifications?|\bspecs?\b|\bsow\b|requirements", "scope"),
    # Permit
    (r"permit|approval|licen[cs]e", "permit"),
    # Photo by extension (after content-type check below)
    # ORDER: below scope and permit — "Electrical Scope.xlsx" is a scope and
    # asking "which discipline" before "which kind of document" got that
    # wrong. Above the photo-by-extension rule, because "MECH riser.png"
    # is a drawing that happens to be a PNG.
    # A DOCUMENT noun outranks the discipline tag: "Mechanical _ Ventilation
    # Checklist.pdf" and "Garden Electrical Issue.docx" are documents about a
    # trade, not drawings of it, and `category='drawing'` feeds the estimate
    # accuracy grade — a checklist counted as a drawing makes a project look
    # more measured than it is.
    (
        rf"{_NOT_A_DOC}.*{_SEG_L}(architectural|arch|structural|struct"
        rf"|mechanical|mech|electrical|elec|plumbing|plumb|civil){_SEG_R}",
        "drawing",
    ),
    (r"\.(jpe?g|png|heic|tiff?|gif|webp|bmp)$", "photo"),
    # ── Appended 2026-08 — consultant / accounting filenames. Deliberately
    # placed AFTER every rule above so no filename that classifies correctly
    # today changes category; these only rescue names that fell through to
    # `other`. The underscore-safe boundaries are what make them fire on the
    # sanitised inbound-email names.
    #
    # Consultant discipline tags — usually a trailing " - MECH.pdf" segment,
    # but matched anywhere in the name.
    (rf"{_SEG_L}ti{_SEG_R}|tenant[\s_-]*improvements?", "drawing"),
    # Sheet codes as a standalone token: A-1, A-101, S-2, M-3, E-1, P-2.
    # 1-3 digits only, so "E-2024" (a year) and "PO-124225" never match.
    # "C" is excluded on purpose — it would collide with Schedule C-1 / C-2.
    (rf"{_SEG_L}[asmep]-\d{{1,3}}{_SEG_R}", "drawing"),
    # Quotes / estimates / bids from trades and consultants.
    (
        rf"{_SEG_L}(quotations?|quotes?|estimates?|proposals?|tenders?"
        rf"|re[\s_-]?bids?|bids?){_SEG_R}",
        "report",
    ),
    # Invoices and money statements. "inv" needs a following number so
    # "Investigation" / "Inventory" / "Invitation to tender" never match.
    (rf"{_SEG_L}(invoices?|receipts?){_SEG_R}|{_SEG_L}inv[\s_-]*\d", "invoice_legacy"),
    # "statement" is money-ish (bank / owner statement) EXCEPT for the two
    # construction senses: a "method statement" (safety procedure) and a
    # "statement of work" (a scope). Both are excluded and keep falling
    # through rather than being mislabelled as billing.
    (
        rf"{_SEG_L}(?<!method[\s_-])statements?{_SEG_R}(?![\s_-]*of[\s_-]*work)",
        "invoice_legacy",
    ),
]


def _suggest_category(filename: str, mime: str | None) -> str:
    # Underscores are WORD characters, so `\bscope\b` does not match
    # "Project_Scope_Summary.docx" and `\b(mech|mechanical)\b` does not match
    # "Mechanical_Plans.pdf". Every filename restored from R2 is
    # underscore-separated, which is how 158 documents ended up in "other".
    # Only underscores: a HYPHEN is not a word character, so `\b` already works
    # across it — and normalising hyphens away breaks the sheet-code rule, which
    # reads "A-01" and "M-100" literally.
    fn = re.sub(r"_+", " ", filename.lower())
    if (mime or "").startswith("image/"):
        return "photo"
    for pattern, cat in _CATEGORY_HEURISTICS:
        if re.search(pattern, fn):
            return cat
    return "other"


@router.get("/checklist")
def get_checklist(_: CurrentUser = Depends(get_current_user)) -> dict:
    return BC_DOC_CHECKLIST


# ── Indexing (P1) ──────────────────────────────────────────────────────────
# All indexing-adjacent endpoints share the documents router so the
# Document/ DocumentSummary surface stays in one place.

@router.get("/index-status", response_model=list[DocumentIndexBadge])
def list_index_status(
    project_id: UUID = Query(..., description="filter to documents for this project"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[DocumentIndexBadge]:
    """Per-document index status for the Documents grid badges."""
    from app.models.document_summary import DocumentSummary

    assert_project_access(db, user, project_id)

    docs = db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .where(Document.archived.is_(False))
        .where(Document.size_bytes.is_not(None))
    ).scalars().all()
    summaries = db.execute(
        select(DocumentSummary).where(DocumentSummary.project_id == project_id)
    ).scalars().all()
    by_doc = {s.document_id: s for s in summaries}

    out: list[DocumentIndexBadge] = []
    for d in docs:
        s = by_doc.get(d.id)
        if s is None:
            out.append(
                DocumentIndexBadge(
                    document_id=d.id, status="not_indexed", summary_short=None
                )
            )
            continue
        short = None
        if s.summary_text:
            short = (s.summary_text.split(".", 1)[0]).strip()[:180]
        out.append(
            DocumentIndexBadge(
                document_id=d.id,
                status=s.status,
                summary_short=short,
                indexed_at=s.indexed_at,
                summary_text=s.summary_text,
                error_message=s.error_message,
            )
        )
    return out


@router.post("/{document_id}/index", response_model=DocumentIndexBadge)
def queue_document_index(
    document_id: UUID,
    force: bool = Query(False, description="re-queue even if a summary already exists"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> DocumentIndexBadge:
    """Manually (re-)queue a document_summary job for one document."""
    from app.services.document_summary_queue import queue_document_summary

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    # Resolved from the DOCUMENT: the caller supplies only a document id, so the
    # project has to be looked up before access can be checked.
    assert_project_access(db, user, doc.project_id)
    job = queue_document_summary(db, doc, force=force)
    db.commit()

    from app.models.document_summary import DocumentSummary

    summary = db.execute(
        select(DocumentSummary).where(DocumentSummary.document_id == doc.id)
    ).scalar_one_or_none()
    if summary is None:
        # Skipped (oversized / wrong category / etc.) — surface that.
        return DocumentIndexBadge(document_id=doc.id, status="skipped")
    return DocumentIndexBadge(
        document_id=doc.id,
        status=summary.status,
        summary_short=(summary.summary_text or "")[:180] or None,
        indexed_at=summary.indexed_at,
    )


@router.post("/index/reindex-project")
def reindex_project(
    project_id: UUID = Query(...),
    force: bool = Query(True),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Queue document_summary jobs for every live document in the project.
    Used by the "Reindex project" button + the cross-project backfill CLI."""
    assert_project_access(db, user, project_id)
    from app.services.document_summary_queue import queue_document_summary

    docs = db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .where(Document.archived.is_(False))
        .where(Document.size_bytes.is_not(None))
    ).scalars().all()

    queued = 0
    skipped = 0
    for d in docs:
        job = queue_document_summary(db, d, force=force)
        if job is None:
            skipped += 1
        else:
            queued += 1
    db.commit()
    return {"queued": queued, "skipped": skipped, "total": len(docs)}


@router.get("/index", response_model=ProjectIndexRead)
def get_project_index(
    project_id: UUID = Query(...),
    rebuild: bool = Query(
        False,
        description="force re-materialise the cached R2 artifact even if it's fresh",
    ),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> ProjectIndexRead:
    """Return the rendered project-index Markdown + freshness metadata.
    Always reads from the source rows (DocumentSummary) so a stale cache
    can't return stale content. The R2 artifact is updated when `rebuild`
    is true OR opportunistically when content has materially changed."""
    from datetime import datetime, timezone

    from app.services.project_index import (
        materialize_project_index,
        render_project_index_markdown,
    )

    assert_project_access(db, user, project_id)

    md, meta = render_project_index_markdown(db, project_id)
    if rebuild:
        materialize_project_index(db, project_id, user_id=_actor_uuid(user))

    return ProjectIndexRead(
        project_id=project_id,
        project_name=meta["project_name"],
        document_count=meta["document_count"],
        summarized_count=meta["summarized_count"],
        pending_count=meta["pending_count"],
        failed_count=meta["failed_count"],
        last_indexed_at=meta["last_indexed_at"],
        generated_at=datetime.now(timezone.utc),
        markdown=md,
    )


@router.post("/index/materialize")
def materialize_index(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Force-write the current MD index to R2 as a `project_index` Document
    row. Used by the "Save / share" button on the Project Index view."""
    assert_project_access(db, user, project_id)
    from app.services.project_index import materialize_project_index

    key, url = materialize_project_index(db, project_id, user_id=_actor_uuid(user))
    return {"r2_key": key, "download_url": url}


@router.get("/drawing-register")
def drawing_register(
    project_id: UUID = Query(..., description="the project whose drawings to group"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """Group a project's drawings into sheets, each with its revisions.

    Groupings someone has confirmed are returned as confirmed; the rest are a
    PROPOSAL and nothing about them is written. A filename is evidence about a
    drawing, not a fact about one: "R2" is what somebody typed. Every group
    reports why it was grouped so a reviewer can disagree.

    Declared before "/{document_id}" for the same reason as /search.
    """
    from app.services.drawing_register_store import register_for_project

    assert_project_access(db, user, project_id)
    return register_for_project(db, project_id)


@router.post("/drawing-register/confirm")
def confirm_drawing_register(
    project_id: UUID = Query(...),
    fingerprint: str = Query(..., description="the grouping the reviewer was shown"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """Record the proposed grouping as confirmed.

    A WRITE, and the first one this workspace makes. Office role, because which
    drawing is current decides which one an estimate is priced from.

    The fingerprint is re-checked against a freshly computed proposal and a
    mismatch is REFUSED. Someone can upload a drawing between the page rendering
    and the button being pressed; confirming a grouping nobody looked at is
    precisely what the confirmation exists to prevent. Same reasoning as the
    `before` check when applying an AI edit.
    """
    from app.services.drawing_register_store import RegisterChanged, confirm_register

    assert_project_access(db, user, project_id)
    try:
        return confirm_register(db, project_id, fingerprint, _actor_uuid(user))
    except RegisterChanged as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "error": "register_changed", "message": str(e)})


@router.get("/search")
def search_documents_endpoint(
    q: str = Query(..., min_length=1, description="words to look for in the name or summary"),
    project_id: UUID | None = Query(None, description="restrict to one project; omit to search every project you can see"),
    category: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """Find a document by name, or by what its summary says.

    Declared BEFORE the `/{document_id}` routes: FastAPI matches in declaration
    order, so "search" would otherwise be parsed as a document id and 422.

    Scope is resolved from the caller's own access, never from the request. With
    a project_id it is the usual single-project check; without one it is every
    project they are a member of. There is no code path that searches a project
    the caller cannot already open.
    """
    from app.auth import accessible_project_ids
    from app.services.document_search import search_documents

    if project_id is not None:
        assert_project_access(db, user, project_id)
        pids = [project_id]
    else:
        pids = accessible_project_ids(db, user)

    from app.services.document_search import search_document_pages
    from app.services.document_text_index import coverage

    results = search_documents(
        db, query=q, project_ids=pids, category=category, limit=limit,
    )
    # Inside-the-file matches rank below every name and summary match, and a
    # file already found by name is not listed twice.
    seen = {r["id"] for r in results}
    if len(results) < limit:
        for hit in search_document_pages(db, query=q, project_ids=pids,
                                         category=category, limit=limit):
            if hit["id"] not in seen and len(results) < limit:
                results.append(hit)
                seen.add(hit["id"])
    return {
        "query": q,
        "scope": "project" if project_id else "all-accessible",
        "projects_searched": len(pids),
        "count": len(results),
        "results": results,
        # What the inside-the-file search covered, so the page can say "searched
        # inside 412 of 1,750 files" rather than imply it searched them all.
        "text_coverage": coverage(db, pids),
        # Said explicitly so callers do not present upload order as revision
        # order. There is no revision chain on documents yet.
        "ordering_note": (
            "Ranked by how well the name matches, then newest upload first. "
            "Documents have no revision history yet, so 'latest' means most "
            "recently uploaded, not the current revision."
        ),
    }


@router.get("/used-in")
def documents_used_in(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, list[str]]:
    """Where each document is used: Takeoff, Scope, Estimate, Costs, Notes.

    Only documents used somewhere appear. Read from the rows that point at a
    document, so a drawing nobody has analysed is used in nothing.
    """
    from app.services.document_usage import used_in

    assert_project_access(db, user, project_id)
    return used_in(db, project_id)


@router.get("/text-status")
def documents_text_status(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[dict[str, Any]]:
    """Whether each readable file's text has been read for searching.

    One row per PDF, Word, Excel or text file that is queued or has been read:
    waiting, searchable, or why not (a scan with no text layer, too large,
    failed). Photos and video never appear — they are not read.
    """
    from app.services.document_text_index import project_text_status

    assert_project_access(db, user, project_id)
    return project_text_status(db, project_id)


@router.post("/suggest-categories")
def suggest_categories(
    body: CategorySuggestRequest,
    _: CurrentUser = Depends(require_office),
) -> dict[str, str]:
    """The category this server would guess for each filename.

    The upload review asks before anything is stored, so the guess shown is the
    one the import paths use rather than a second copy of the rules in the
    browser, which had drifted (it missed "_Elec.pdf").
    """
    return {
        name: _suggest_category(name, _mime_from_filename(name))
        for name in body.filenames
    }


@router.post("/bulk", response_model=DocumentBulkResult)
def bulk_update_documents(
    body: DocumentBulkUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> DocumentBulkResult:
    """Apply one change to several documents: category, sharing, or archive.

    Every id must belong to the named project; one that does not refuses the
    whole request, so a stale selection cannot reach into another project.
    Each document that changes gets its own activity row, as a single edit would.
    """
    assert_project_access(db, user, body.project_id)
    if body.category is None and body.client_visible is None and body.archived is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="nothing to change")
    ids = list(dict.fromkeys(body.document_ids))
    docs = db.execute(select(Document).where(Document.id.in_(ids))).scalars().all()
    if len(docs) != len(ids) or any(d.project_id != body.project_id for d in docs):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="one or more documents are not in this project")
    now = datetime.now(timezone.utc)
    actor = _actor_uuid(user)
    updated = 0
    for doc in docs:
        changed: dict[str, str] = {}
        if body.category is not None:
            if body.category != doc.category:
                changed["category"] = body.category
                doc.category = body.category
            if doc.category_confirmed_at is None:
                changed["category_confirmed"] = doc.category
                doc.category_confirmed_at = now
                doc.category_confirmed_by_id = actor
        if body.client_visible is not None and body.client_visible != doc.client_visible:
            changed["client_visible"] = str(body.client_visible)
            doc.client_visible = body.client_visible
        if body.archived is not None and body.archived != doc.archived:
            changed["archived"] = str(body.archived)
            doc.archived = body.archived
        if changed:
            updated += 1
            _log(db, project_id=doc.project_id, user=user, action="document.bulk_update",
                 document_id=str(doc.id), filename=doc.filename, extra=changed)
    db.commit()
    return DocumentBulkResult(updated=updated, unchanged=len(docs) - updated)


@router.get("", response_model=list[DocumentRead])
def list_documents(
    project_id: UUID = Query(..., description="filter to documents for this project"),
    archived: bool = Query(False, description="true lists the ARCHIVED documents instead"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[Document]:
    """Live documents for a project — newest first. Excludes archived rows and
    presigned-but-never-uploaded rows (size_bytes still NULL). With
    `archived=true`, the archived ones instead, so they can be restored."""
    assert_project_access(db, user, project_id)
    stmt = (
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.archived.is_(archived),
            Document.size_bytes.is_not(None),
        )
        .order_by(Document.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post("/presign", response_model=DocumentPresignResponse)
def presign_document(
    body: DocumentPresignRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DocumentPresignResponse:
    assert_project_access(db, user, body.project_id)
    doc_id = uuid.uuid4()
    filename = _safe_filename(body.filename)
    r2_key = f"projects/{body.project_id}/documents/{doc_id}/{filename}"

    doc = Document(
        id=doc_id,
        project_id=body.project_id,
        category=body.category,
        phase=body.phase,
        filename=body.filename.strip() or filename,
        r2_key=r2_key,
        mime=body.content_type,
        size_bytes=None,  # set on finalize
        uploaded_by_id=_actor_uuid(user),
    )
    if body.category_confirmed:
        doc.category_confirmed_at = datetime.now(timezone.utc)
        doc.category_confirmed_by_id = _actor_uuid(user)
    db.add(doc)
    db.commit()

    return DocumentPresignResponse(
        document_id=doc_id,
        r2_key=r2_key,
        upload_url=presign_put(r2_key, body.content_type),
    )


@router.post("/{document_id}/finalize", response_model=DocumentRead)
def finalize_document(
    document_id: UUID,
    body: DocumentFinalizeRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Document:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    # Verify the browser PUT actually landed — don't mark a row "live" (visible +
    # consumable) when the object is missing; it would 404 inside scope/takeoff.
    if not object_exists(doc.r2_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="upload not found in storage — the file was not uploaded",
        )
    # Size + mime from the authoritative sources (R2 + filename), not client input —
    # fixes trusted-size rows and DWG-mislabeled-as-PDF that break downstream ingest.
    doc.size_bytes = head_size(doc.r2_key) or body.size_bytes
    reconciled = _mime_from_filename(doc.filename)
    if reconciled and reconciled != (doc.mime or ""):
        doc.mime = reconciled
    _log(
        db,
        project_id=doc.project_id,
        user=user,
        action="document.upload",
        document_id=str(doc.id),
        filename=doc.filename,
        category=doc.category,
        size_bytes=body.size_bytes,
    )
    # Searchable text: queue the file to be read. Local, no AI cost, and like
    # the summary below it must never block the upload acknowledgement.
    try:
        from app.services.document_text_index import queue_document_text

        with db.begin_nested():
            queue_document_text(db, doc)
    except Exception:  # noqa: BLE001
        logger.exception("document_text: could not queue %s", doc.id)
    # Drawing PDFs: parse for the measure page now, in the background worker,
    # so it is usually ready before anyone opens the page. Best-effort.
    try:
        from app.services.drawing.analysis_queue import is_drawing_pdf, queue_analysis

        if is_drawing_pdf(doc):
            with db.begin_nested():
                queue_analysis(db, doc)
    except Exception:  # noqa: BLE001
        logger.exception("drawing_analysis: could not queue %s", doc.id)
    # P1 indexing: auto-queue a document_summary job. Best-effort —
    # an indexing failure must never block the upload acknowledgement.
    try:
        from app.services.document_summary_queue import queue_document_summary

        queue_document_summary(db, doc)
    except Exception as exc:  # noqa: BLE001
        _log(
            db,
            project_id=doc.project_id,
            user=user,
            action="document.index_queue_failed",
            document_id=str(doc.id),
            error=str(exc),
        )
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/{document_id}/download", response_model=DocumentDownloadResponse)
def download_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> DocumentDownloadResponse:
    doc = db.get(Document, document_id)
    if doc is None or doc.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    _log(
        db,
        project_id=doc.project_id,
        user=user,
        action="document.download",
        document_id=str(doc.id),
        filename=doc.filename,
    )
    db.commit()
    if not object_exists(doc.r2_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="file is missing from storage",
        )
    return DocumentDownloadResponse(
        filename=doc.filename,
        download_url=presign_download(doc.r2_key, doc.filename),
    )


#: What a browser can render in place. Anything else has to be downloaded, and
#: the UI should say so rather than opening a blank pane.
PREVIEWABLE = {
    "application/pdf": "application/pdf",
    "image/png": "image/png", "image/jpeg": "image/jpeg", "image/gif": "image/gif",
    "image/webp": "image/webp", "image/svg+xml": "image/svg+xml",
    "text/plain": "text/plain; charset=utf-8",
    "text/markdown": "text/plain; charset=utf-8",
    "text/csv": "text/plain; charset=utf-8",
}
_EXT_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
             ".svg": "image/svg+xml", ".txt": "text/plain", ".md": "text/markdown",
             ".csv": "text/csv"}


@router.get("/{document_id}/preview", response_model=DocumentPreviewResponse)
def preview_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> DocumentPreviewResponse:
    """A URL the browser renders in place, for looking at a drawing without
    downloading it. Says plainly when a file cannot be previewed."""
    import os

    doc = db.get(Document, document_id)
    if doc is None or doc.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    mime = (doc.mime or "").split(";")[0].strip().lower()
    if mime not in PREVIEWABLE:
        # Stored mime is often absent or wrong on migrated documents; the
        # extension is the more reliable signal for what the file actually is.
        mime = _EXT_MIME.get(os.path.splitext(doc.filename or "")[1].lower(), mime)
    if mime not in PREVIEWABLE:
        return DocumentPreviewResponse(
            filename=doc.filename, previewable=False,
            reason=(f"{os.path.splitext(doc.filename or '')[1] or 'This file type'} "
                    f"cannot be shown in the browser — download it to open."))
    if not object_exists(doc.r2_key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="file is missing from storage")
    return DocumentPreviewResponse(
        filename=doc.filename, previewable=True, content_type=PREVIEWABLE[mime],
        preview_url=presign_inline(doc.r2_key, PREVIEWABLE[mime], doc.filename))


@router.get("/{document_id}/thumbnail", response_model=DocumentThumbnailResponse)
def document_thumbnail(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DocumentThumbnailResponse:
    """A small JPEG thumbnail for an image document — generated lazily on first
    request and cached in R2 alongside the original."""
    doc = db.get(Document, document_id)
    if doc is None or doc.archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    # Crew see thumbnails of site photos (their daily report), nothing else: a
    # scanned invoice or contract is an image too (rollout part 3, 2026-10-15).
    if user.role not in ("manager", "superadmin") and doc.category != "photo":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="office documents are not available here")
    if not (doc.mime or "").startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="thumbnails are only available for image documents",
        )
    thumb_key = f"{doc.r2_key}.thumb.jpg"
    if not object_exists(thumb_key):
        try:
            thumb = _make_thumbnail(get_bytes(doc.r2_key))
        except Exception as exc:  # noqa: BLE001 — bad/corrupt image, etc.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"could not generate thumbnail: {exc}",
            ) from exc
        put_bytes(thumb_key, thumb, "image/jpeg")
    return DocumentThumbnailResponse(thumbnail_url=presign_get(thumb_key))


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID,
    body: DocumentUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Document:
    """Partial update — only fields supplied in the body get touched. Used by
    the ZIP-import review modal to commit corrected categories."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    changed: dict[str, str] = {}
    if body.category is not None and body.category != doc.category:
        changed["category"] = body.category
        doc.category = body.category
    # Choosing a category is a person confirming it, even when it is the one
    # already there; so is pressing "Confirm" on it.
    if (body.category is not None or body.category_confirmed) and doc.category_confirmed_at is None:
        changed["category_confirmed"] = doc.category
        doc.category_confirmed_at = datetime.now(timezone.utc)
        doc.category_confirmed_by_id = _actor_uuid(user)
    if body.filename is not None:
        name = body.filename.strip().replace("/", "_").replace("\\", "_")
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="a name is required")
        # Keep the file type: a name typed without its extension would leave
        # the file unopenable once downloaded.
        old_ext = _ext(doc.filename)
        if old_ext and not name.lower().endswith(old_ext):
            name += old_ext
        if name != doc.filename:
            changed["filename"] = name
            changed["previous_filename"] = doc.filename
            doc.filename = name
    if body.client_visible is not None and body.client_visible != doc.client_visible:
        changed["client_visible"] = str(body.client_visible)
        doc.client_visible = body.client_visible
    if changed:
        _log(
            db,
            project_id=doc.project_id,
            user=user,
            action="document.update",
            document_id=str(doc.id),
            filename=doc.filename,
            extra=changed,
        )
        db.commit()
        db.refresh(doc)
    return doc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Response:
    """Soft delete — archive the row; the R2 object is left intact."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    if not doc.archived:
        doc.archived = True
        _log(
            db,
            project_id=doc.project_id,
            user=user,
            action="document.delete",
            document_id=str(doc.id),
            filename=doc.filename,
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/restore", response_model=DocumentRead)
def restore_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Document:
    """Undo an archive: the document returns to the list as it was."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    if doc.archived:
        doc.archived = False
        _log(db, project_id=doc.project_id, user=user, action="document.restore",
             document_id=str(doc.id), filename=doc.filename)
        db.commit()
        db.refresh(doc)
    return doc


@router.post("/{document_id}/read-text")
def read_document_text_again(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """Queue a file to have its text read again for searching. No AI cost."""
    from app.services.document_text_index import queue_document_text

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)
    if not queue_document_text(db, doc, force=True):
        raise HTTPException(status_code=422, detail="This kind of file has no text to read.")
    db.commit()
    return {"document_id": str(doc.id), "status": "pending"}


# ── Smart ZIP upload (Path A) ──
# CLI / API fallback for whole-archive imports. The web UI does NOT call
# this — Vercel's serverless function body cap (4.5 MB) made the proxy
# path unusable for real Drive archives, so the browser extracts the ZIP
# with JSZip and uploads each member through the normal presign → R2
# flow instead. This endpoint is kept for direct API/CLI callers (e.g.
# server-side batch imports) where the body cap doesn't apply.
_JUNK_PATHS = ("__macosx/", ".ds_store")


@router.post("/process-zip", response_model=list[DocumentRead])
def process_zip(
    project_id: UUID = Query(..., description="project the extracted files belong to"),
    file: UploadFile = File(..., description="ZIP archive of one or more files"),
    phase: str = Query("preconstruction"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[Document]:
    """Extract a ZIP server-side, push each file into R2, create Document rows
    with auto-suggested categories. Returns the created rows so the frontend
    can render a review/edit modal."""
    assert_project_access(db, user, project_id)
    raw = file.file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"file is not a valid ZIP archive ({exc})",
        ) from exc

    created: list[Document] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        lower = name.lower()
        # Skip macOS sidecar junk
        if any(j in lower for j in _JUNK_PATHS):
            continue
        if name.startswith("._") or name.endswith("/"):
            continue
        if info.file_size == 0:
            continue

        # Take only the basename — folder hierarchies inside the zip get flattened.
        basename = name.rsplit("/", 1)[-1]
        safe = _safe_filename(basename)
        if not safe:
            continue

        try:
            blob = zf.read(info)
        except Exception as exc:  # noqa: BLE001
            # Skip individual unreadable entries; carry on with the rest.
            continue
        if not blob:
            continue

        mime = mimetypes.guess_type(basename)[0] or "application/octet-stream"
        category = _suggest_category(basename, mime)

        # Drop into R2 under a unique key per file
        r2_key = f"projects/{project_id}/documents/{uuid.uuid4()}/{safe}"
        put_bytes(r2_key, blob, mime)

        doc = Document(
            project_id=project_id,
            category=category,
            phase=phase,
            filename=basename,
            r2_key=r2_key,
            mime=mime,
            size_bytes=len(blob),
            uploaded_by_id=_actor_uuid(user),
        )
        db.add(doc)
        db.flush()
        _log(
            db,
            project_id=project_id,
            user=user,
            action="document.zip_extract",
            document_id=str(doc.id),
            filename=basename,
            extra={"suggested_category": category, "zip_source": file.filename},
        )
        try:
            from app.services.document_text_index import queue_document_text

            with db.begin_nested():
                queue_document_text(db, doc)
        except Exception:  # noqa: BLE001 — never block the ZIP path
            pass
        # P1 indexing — auto-queue per file. Best-effort.
        try:
            from app.services.document_summary_queue import queue_document_summary

            queue_document_summary(db, doc)
        except Exception:  # noqa: BLE001 — never block the ZIP path
            pass
        created.append(doc)

    db.commit()
    for doc in created:
        db.refresh(doc)
    return created
