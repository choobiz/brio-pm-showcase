"""What the office chose to feed the scope, and what it chose to measure.

The gate this replaces was `category == 'drawing'`, applied in two places that
never agreed with each other and were invisible from the UI. On 105-107
Maple that gate saw 4 of the 25 sheets the consultants had stamped `_DWG_`;
the other 21 were filed as `permit` or `survey`, which is where a building
permit package belongs and says nothing about whether the file is a drawing.

Two independent decisions, because the two consumers want different files:

  SCOPE       what the model READS to write the narrative. PDFs and images
              today; docx/xlsx once the converters land. A DWG is useless here
              — it carries geometry, not sentences.
  QUANTITIES  what the CAD extractor MEASURES. DWG and DXF only. A photo is
              useless here — there is nothing to measure.

A permit-set PDF is usefully both. Most files are neither, which is why nothing
is selected until someone selects it.

`recommend()` is a STARTING POINT, not a decision. It ticks what is plausibly
relevant so a 193-document project does not have to be ticked by hand, and the
office then adjusts. Every recommendation carries the reason it was made.
"""

from __future__ import annotations

import os
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.services.estimate_readiness import CAD_EXTS, _discipline

#: Extensions the scope analysis can turn into content blocks. PDFs and images
#: go to the model as-is; the rest are extracted to text first (see
#: services/doc_text.py), which is why a spec or a room schedule now counts.
SCOPE_READABLE_EXTS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
    ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv",
)
#: Office formats, modern and legacy. The legacy binary ones need a full office
#: suite to read and are NOT in SCOPE_READABLE_EXTS — they are listed here only
#: so the row can say what to do instead of failing silently.
SCOPE_CONVERTIBLE_EXTS = (
    ".docx", ".xlsx", ".xlsm", ".doc", ".xls", ".txt", ".md", ".csv",
)

#: Categories whose contents describe the WORK, whatever the file looks like.
#: A hazmat or geotech report drives real scope even though it is prose.
ALWAYS_RECOMMEND_CATEGORIES = frozenset({
    "drawing", "scope", "photo", "client_note", "report",
})
#: Categories that are a MIXTURE — a permit package holds the drawing set and
#: also the insurance certificates, schedules and application forms filed with
#: it. Only the drawings are recommended; see `looks_like_drawing`.
MIXED_CATEGORIES = frozenset({"permit", "survey"})
#: Categories that are about MONEY or ADMIN, not about the work. A contract and
#: an invoice describe what was agreed and what was billed; neither tells the
#: model what to build, and both dilute the prompt.
NEVER_RECOMMEND_CATEGORIES = frozenset({
    "invoice", "invoice_legacy", "invoice_pdf", "financial", "correspondence",
    "quote", "contract", "video", "estimate_pdf", "scope_pdf", "change_order_pdf",
})


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


#: Consultants stamp the KIND into the filename alongside the discipline:
#: "..._A_DWG_Architectural BP.pdf" is a drawing, "..._A_DOC_Schedule B.pdf" is
#: not. Where the issuer has said which it is, that beats anything we infer.
_KIND_DWG = re.compile(r"[_ ](dwg|drawing)[_ ]", re.I)
_KIND_DOC = re.compile(r"[_ ]doc[_ ]", re.I)
_DRAWING_WORDS = re.compile(
    r"\b(drawing|plan|plans|elevation|elevations|section|sections|detail|details"
    r"|floor\s*plan|site\s*plan|layout|landscape|topograph\w*|grading|schematic)\b",
    re.I,
)


#: Filename tokens that mean "this is not scope", whatever drawer it is filed
#: in. Maple's `report` category holds a hazmat report and an energy
#: compliance study, which genuinely drive scope, alongside a construction
#: budget, a duplex pro-forma and someone's resume, which genuinely do not.
#: Category cannot separate those; the filename can.
_NEVER_SCOPE_WORDS = re.compile(
    r"\b(resume|curriculum\s*vitae|pro.?forma|budget|insurance|invoice|receipt"
    r"|quotation|remittance|payroll|timesheet)\b"
    r"|\bschedule\s*[abc]\b",
    re.I,
)


def looks_like_drawing(filename: str, discipline: str | None) -> bool:
    """Is this file a drawing sheet, as opposed to a form filed beside one?

    A permit package is a MIXTURE. On 105-107 Maple it holds 17 drawing
    sheets and also every insurance certificate, letter of assurance and
    application form. Recommending the whole category swept 84 files into the
    scope, 85 of which resolved to no discipline at all. Recommending none of
    it loses the architectural and structural sets. The issuer's own stamp
    settles it.
    """
    fn = filename or ""
    if _KIND_DWG.search(fn):
        return True
    if _KIND_DOC.search(fn):
        return False
    if discipline and discipline != "general":
        return True
    return bool(_DRAWING_WORDS.search(re.sub(r"[_\-]+", " ", fn)))


def classify(filename: str, mime: str | None) -> dict[str, Any]:
    """What this file is, and what it is capable of contributing."""
    ext = _ext(filename)
    m = (mime or "").lower()

    if ext in CAD_EXTS:
        fmt = "cad"
    elif ext == ".pdf" or m == "application/pdf":
        fmt = "pdf"
    elif m.startswith("image/") or ext in (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"
    ):
        fmt = "image"
    elif ext in SCOPE_CONVERTIBLE_EXTS:
        fmt = "text"  # read by extraction, not sent as a rendered document
    else:
        fmt = "other"

    can_scope = ext in SCOPE_READABLE_EXTS or (
        fmt in ("pdf", "image") and ext not in CAD_EXTS
    )
    scope_block: str | None = None
    if not can_scope:
        if fmt == "cad":
            scope_block = "CAD geometry — no text for the scope. Use it for quantities."
        elif fmt == "text":
            scope_block = (
                f"{ext} is the legacy binary format — re-save it as "
                f"{'.docx' if ext == '.doc' else '.xlsx'}, or export to PDF."
            )
        else:
            scope_block = "This file type cannot be read."

    return {
        "format": fmt,                       # cad | pdf | image | text | other
        "discipline": _discipline(filename) if fmt in ("cad", "pdf") else None,
        "can_scope": can_scope,
        "can_quantities": fmt == "cad",
        "scope_blocked_reason": scope_block,
    }


def recommend(*, filename: str, mime: str | None, category: str) -> dict[str, Any]:
    """The one-click starting point for a single document."""
    info = classify(filename, mime)
    cat = (category or "").lower()

    quantities = info["can_quantities"]
    # Deliberately NOT gated on category: a DWG is a DWG whatever drawer it was
    # filed in, and that gate is the defect this whole change exists to remove.
    q_reason = "CAD file — measurable" if quantities else None

    scope = False
    s_reason: str | None = None
    disc = info["discipline"]
    normalised = re.sub(r"[_\-]+", " ", filename or "")
    if info["can_scope"]:
        if _NEVER_SCOPE_WORDS.search(normalised):
            s_reason = "the name says this is commercial or personal, not scope"
        elif cat in NEVER_RECOMMEND_CATEGORIES:
            s_reason = f"filed as {cat} — commercial or administrative, not scope"
        elif cat in ALWAYS_RECOMMEND_CATEGORIES:
            scope = True
            s_reason = (
                f"{disc} drawing" if disc and disc != "general" else f"filed as {cat}"
            )
        elif cat in MIXED_CATEGORIES:
            if looks_like_drawing(filename, disc):
                scope = True
                s_reason = (
                    f"{disc} drawing in the {cat} package" if disc and disc != "general"
                    else f"drawing in the {cat} package"
                )
            else:
                s_reason = f"a form filed with the {cat}, not a drawing"
        else:
            s_reason = f"filed as {cat or 'uncategorised'} — include it if it describes the work"

    return {
        **info,
        "recommend_scope": scope,
        "recommend_quantities": quantities,
        "scope_reason": s_reason,
        "quantities_reason": q_reason,
    }


def signed_takeoff_ids(db: Session, project_id: UUID | str) -> set[str]:
    """CAD files read by the project's SIGNED-OFF takeoff.

    Signing off a takeoff is a person deciding to price from those files, so
    they count as chosen for quantities unless someone explicitly unticked them.
    Until 2026-10-15 they did not: the Takeoff page read every CAD file, while
    the estimate counted only ticked ones, so on ZZ Rollout Test a signed-off
    takeoff was silently skipped (the estimate priced "scope + rates") and the
    grade said "no CAD, upload the DXF" with the DXF uploaded and signed.
    """
    ids = db.execute(_T(
        "SELECT source_document_ids FROM drawing_analysis_packs "
        "WHERE project_id = :p AND status = 'approved' "
        "ORDER BY version_number DESC LIMIT 1"), {"p": str(project_id)}).scalar()
    return {str(i) for i in (ids or [])}


def list_for_project(db: Session, project_id: UUID | str) -> list[dict[str, Any]]:
    """Every live document, with its classification, recommendation and the
    current selection. One query, one pass — the table is rendered from this."""
    rows = db.execute(
        _T(
            "SELECT d.id, d.filename, d.mime, d.category, d.size_bytes, d.created_at, "
            "       s.use_for_scope, s.use_for_quantities "
            "FROM documents d "
            "LEFT JOIN document_selections s "
            "       ON s.document_id = d.id AND s.project_id = d.project_id "
            "WHERE d.project_id = :p AND d.archived = false "
            "ORDER BY d.category, d.filename"
        ),
        {"p": str(project_id)},
    ).mappings().all()

    # The same file uploaded more than once. Maple holds the hazardous
    # material report three times and several permit forms twice; sending each
    # copy to the model costs real money and tells it nothing new. The first
    # by filename+size is canonical, the rest are marked and are skipped by
    # "select recommended" — but still listed, because deciding which copy is
    # current is not ours to make silently.
    seen: dict[tuple[str, int | None], str] = {}

    signed = signed_takeoff_ids(db, project_id)
    out: list[dict[str, Any]] = []
    for r in rows:
        key = ((r["filename"] or "").strip().lower(), r["size_bytes"])
        first = seen.get(key)
        if first is None:
            seen[key] = str(r["id"])
        rec = recommend(filename=r["filename"], mime=r["mime"], category=r["category"])
        if first is not None:
            rec = {**rec, "recommend_scope": False, "recommend_quantities": False,
                   "scope_reason": "a duplicate of a file already in the list"}
        out.append({
            "document_id": str(r["id"]),
            "filename": r["filename"],
            "category": r["category"],
            "mime": r["mime"],
            "size_bytes": r["size_bytes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            # None means "never touched" — distinct from an explicit False, so
            # the UI can show which rows the office has actually ruled on.
            "use_for_scope": r["use_for_scope"],
            "use_for_quantities": r["use_for_quantities"],
            #: In the signed-off takeoff: counts as chosen unless explicitly False.
            "in_signed_takeoff": str(r["id"]) in signed,
            "duplicate_of": first,
            **rec,
        })
    return out


def selected_ids(
    db: Session, project_id: UUID | str, *, purpose: str
) -> list[str] | None:
    """Document ids chosen for `purpose` ("scope" or "quantities").

    Returns None when the project has NO selection rows at all. That is not the
    same as an empty selection: it means nobody has used this screen yet, and
    callers fall back to their previous behaviour rather than suddenly reading
    nothing. Deliberate — a silent behaviour change on 30 existing projects is
    how a working estimate turns into an empty one.
    """
    column = "use_for_scope" if purpose == "scope" else "use_for_quantities"
    any_rows = db.execute(
        _T("SELECT 1 FROM document_selections WHERE project_id = :p LIMIT 1"),
        {"p": str(project_id)},
    ).scalar()
    if not any_rows:
        return None
    rows = db.execute(
        _T(
            f"SELECT document_id FROM document_selections "
            f"WHERE project_id = :p AND {column} = true"
        ),
        {"p": str(project_id)},
    ).scalars().all()
    chosen = [str(r) for r in rows]
    if purpose != "scope":
        unticked = {str(r) for r in db.execute(_T(
            "SELECT document_id FROM document_selections "
            "WHERE project_id = :p AND use_for_quantities = false"), {"p": str(project_id)}).scalars()}
        chosen += sorted(signed_takeoff_ids(db, project_id) - set(chosen) - unticked)
    return chosen


def accuracy_for_selection(db: Session, project_id: UUID | str) -> dict[str, Any]:
    """Grade the estimate from WHAT WAS SELECTED, not from filing category.

    Same grader the readiness endpoint has always used; only the population it
    sees has changed.
    """
    from app.services.estimate_accuracy import grade_accuracy

    everything = list_for_project(db, project_id)
    docs = [d for d in everything if d["use_for_scope"] or d["use_for_quantities"]
            or (d["in_signed_takeoff"] and d["use_for_quantities"] is not False)]
    disciplines: dict[str, dict[str, bool]] = {}
    unattributed = 0
    counts = {"cad": 0, "pdf": 0, "image": 0, "text": 0, "other": 0}
    for d in docs:
        counts[d["format"]] = counts.get(d["format"], 0) + 1
        if d["format"] not in ("cad", "pdf"):
            continue
        disc = d["discipline"] or "general"
        if disc == "general":
            unattributed += 1
            continue
        cov = disciplines.setdefault(disc, {"cad": False, "pdf": False})
        cov[d["format"]] = True

    grade = grade_accuracy(disciplines, unattributed=unattributed).as_dict()
    return {
        "accuracy": grade,
        "disciplines": disciplines,
        "counts": counts,
        "selected_scope": sum(1 for d in docs if d["use_for_scope"]),
        "selected_quantities": sum(1 for d in docs if d["use_for_quantities"]),
        "total_documents": len(everything),
    }
