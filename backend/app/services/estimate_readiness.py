"""Estimate readiness — Track 1 / Phase 1 of the workflow redesign.

Answers, for a project's uploaded documents: can we produce a MEASURED estimate
(CAD quantity takeoff), or must we fall back to reading PDFs + rates? Classifies
drawings by format (CAD vs PDF) and discipline, and reports what each path unlocks
so the intake guide can tell the user exactly what to upload for a complete estimate.

This is the provenance foundation the rest of Track 1 builds on:
  - takeoff_mode "cad"  → measured takeoff available (highest confidence)
  - takeoff_mode "pdf"  → PDF-read + rate-based (needs human verification)
  - takeoff_mode "none" → no drawings; rate/scope-only (lowest confidence)
"""
from __future__ import annotations

import os
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

CAD_EXTS = (".dwg", ".dxf")

#: SQL predicate for "this document is CAD", by FILENAME only.
#:
#: Deliberately NOT gated on `category`. A DWG is a DWG whatever drawer it was
#: filed in, and gating on the filing label is the defect that hid most of a
#: stamped sheet set on Maple. `_fmt` below and document_selection already
#: classify by extension; two SQL queries did not, so a CAD file filed as
#: anything other than "drawing" was invisible to the takeoff builder AND to
#: the gate that requires a signed analysis — readiness would report the
#: project as CAD while the takeoff silently skipped the file.
IS_CAD_SQL = "(lower(filename) LIKE '%.dwg' OR lower(filename) LIKE '%.dxf')"

# Discipline inference from the drawing filename (mirrors documents._CATEGORY_HEURISTICS).
# Discipline from the drawing filename. ORDER MATTERS: the specific consultants
# are tested first, because "Structural Plan" and "Mechanical Plan" both contain
# "plan" and would otherwise be swallowed by the architectural catch-all.
_DISC_PATTERNS: list[tuple[str, str]] = [
    ("structural", r"\b(struct|structural)\b|\bs\d+[-\.]?\d*\b|\bfootings?|\bframing\s*plans?"),
    ("mechanical", r"\b(mech|mechanical|hvac)\b|\bm\d+[-\.]?\d*\b"),
    ("electrical", r"\b(elec|electrical)\b|\be\d+[-\.]?\d*\b"),
    ("plumbing", r"\b(plumb|plumbing)\b|\bp\d+[-\.]?\d*\b"),
    ("civil", r"\b(civil|storm|stormwater|grading|site\s*servicing|drainage|topo)\b"
              r"|\bc\d+[-\.]?\d*\b|\bsw?mp\b|catch\s*basin|streetlight|sanitary"
              r"|lot\s*survey|servicing"),
    # A bare "Plan", "Section", "Detail" or a raw CAD file with no discipline
    # marker is architectural — that is what an architect's file is called.
    # Without this, "1234 Example - Plan.dxf" resolved to "general" and was
    # dropped from the accuracy grade entirely, so the project the takeoff engine
    # was built against reported as having no drawings at all.
    ("architectural", r"\b(arch|architectural)\b|\ba\d+[-\.]?\d*\b|floor\s*plans?"
                      r"|elevations?|\bplans?\b|\bsections?\b|\bdetails?\b|\.(dwg|dxf)$"
                      # A window/door schedule, a coordination set and an
                      # in-progress drawing are all the architect's output.
                      r"|(window|door)\s*(and\s*door\s*)?schedule|coordination"
                      r"|in.progress\s*drawing|\bifc\b|permit\s*set"),
]

# What a complete estimate wants, and what each unlocks.
_RECOMMENDED = [
    {"id": "cad", "label": "CAD drawings (DWG / DXF)",
     "unlocks": "Accurate, measured quantity takeoff — counts and areas straight from the model.",
     "critical": False},
    {"id": "pdf_drawings", "label": "PDF drawing set (permit drawings)",
     "unlocks": "Fallback takeoff by reading the PDFs, plus visual context for the scope.",
     "critical": True},
    {"id": "scope", "label": "Scope of work",
     "unlocks": "The priced work list — required before an estimate can be generated.",
     "critical": True},
    {"id": "photos", "label": "Site photos",
     "unlocks": "Site conditions the drawings don't show; feeds allowances and risk.",
     "critical": False},
]


def _fmt(filename: str, mime: str | None) -> str | None:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in CAD_EXTS:
        return "cad"
    if ext == ".pdf" or (mime or "") == "application/pdf":
        return "pdf"
    return None


#: Consultants stamp the discipline into the filename as a single letter between
#: separators — "105-107 Maple Cres_A_DWG_Architectural BP.pdf". This is the
#: most reliable signal in the whole document store when it is present, because
#: it is written by the issuer rather than inferred by us.
_DISC_CODES = {"a": "architectural", "s": "structural", "c": "civil",
               "m": "mechanical", "e": "electrical", "p": "plumbing",
               "g": "civil", "l": "architectural"}   # g geotechnical, l landscape
_CODE_RE = re.compile(r"[_ ]([ascmepgl])[_ ](dwg|drawing)[_ ]", re.I)


def _discipline(filename: str) -> str:
    # Underscores are word characters, so `\bmechanical\b` does NOT match
    # "Mechanical_Plans.pdf" — and every filename that came back from R2 is
    # underscore-separated. Normalise first.
    raw = (filename or "").lower()
    code = _CODE_RE.search(raw)
    if code:
        return _DISC_CODES[code.group(1).lower()]
    fn = re.sub(r"[_\-]+", " ", raw)
    for disc, pat in _DISC_PATTERNS:
        if re.search(pat, fn):
            return disc
    return "general"


#: grade -> (confidence, message). The grade is discipline-aware; this is the
#: one place the readiness banner's wording is decided.
_CONFIDENCE_BY_GRADE: dict[str, tuple[str, str]] = {
    "measured_complete": (
        "measured",
        "Every budget-affecting discipline is CAD. Quantities are measured, not assumed.",
    ),
    "measured_partial": (
        "partially_measured",
        "The architectural set is CAD, so areas and openings are measured — but at least one "
        "budget-affecting discipline has no drawings and falls back to an allowance.",
    ),
    "read_from_pdf": (
        "estimated",
        "The architectural set is not CAD, so the quantities that carry the budget are READ "
        "rather than measured and need a human to confirm every figure. Upload the "
        "architectural DWG/DXF and the same drawings produce a measured takeoff.",
    ),
    "scope_only": (
        "insufficient",
        "No drawings — the estimate can only price a written scope against the rate library. "
        "Treat the total as an order of magnitude.",
    ),
}


def compute_readiness(db: Session, project_id: UUID | str) -> dict[str, Any]:
    rows = db.execute(
        _T("select id, category, filename, mime from documents "
           "where project_id = :p and archived = false"),
        {"p": str(project_id)},
    ).mappings().all()

    # WHICH documents count as the drawing set.
    #
    # This used to be `category == 'drawing'` and nothing else, which is a
    # filing label rather than a decision about what to price from. On
    # 105-107 Maple that filter saw 4 of the 25 sheets the consultants had
    # stamped _DWG_; 17 were filed as permit and 4 as survey, which is where a
    # building-permit package belongs. The project reported as barely drawn.
    #
    # An explicit selection now wins. `None` means nobody has used the
    # document-selection screen on this project, and the old filter still
    # applies — existing projects must not change behaviour underneath a run
    # that is already working.
    from app.services.document_selection import selected_ids

    chosen = selected_ids(db, project_id, purpose="quantities")
    chosen_scope = selected_ids(db, project_id, purpose="scope")
    if chosen is not None:
        picked = set(chosen) | set(chosen_scope or [])
        drawings = [r for r in rows if str(r["id"]) in picked]
        selection_source = "explicit"
    else:
        drawings = [r for r in rows if (r["category"] or "") == "drawing"]
        selection_source = "category"
    has_scope = any((r["category"] or "") == "scope" for r in rows)
    has_photos = any((r["category"] or "") == "photo" for r in rows)

    # Per-discipline coverage: which formats are present.
    disciplines: dict[str, dict[str, bool]] = {}
    cad_docs, pdf_docs = [], []
    for d in drawings:
        fmt = _fmt(d["filename"], d["mime"])
        if fmt is None:
            continue
        disc = _discipline(d["filename"])
        cov = disciplines.setdefault(disc, {"cad": False, "pdf": False})
        cov[fmt] = True
        (cad_docs if fmt == "cad" else pdf_docs).append(d["filename"])

    has_cad = bool(cad_docs)
    has_pdf = bool(pdf_docs)

    # `takeoff_mode` is a ROUTING decision, and "is there any CAD at all" is the
    # right question for it: if a structural DWG exists we should measure it,
    # even when the architectural set is PDF only. Narrowing this would stop
    # measuring a trade we can actually measure, which is strictly worse.
    mode = "cad" if has_cad else ("pdf" if has_pdf else "none")

    present = {
        "cad": has_cad,
        "pdf_drawings": has_pdf,
        "scope": has_scope,
        "photos": has_photos,
    }
    checklist = [
        {**item, "present": present.get(item["id"], False)}
        for item in _RECOMMENDED
    ]
    missing_critical = [c["label"] for c in checklist if c["critical"] and not c["present"]]

    # Discipline-aware grade. `takeoff_mode` above answers "is there CAD"; this
    # answers "which trades are actually measured", which is the question a person
    # about to price the job is asking. A set with architectural CAD and no
    # structural drawings measures its partitions and guesses its footings.
    from app.services.estimate_accuracy import grade_accuracy

    # Drawings whose discipline cannot be read must be COUNTED, not dropped. A
    # file that matches no pattern contributes nothing to the grade, so silently
    # ignoring it makes the estimate look less supported than it is.
    unattributed = sum(1 for d in drawings
                       if _fmt(d["filename"], d["mime"])
                       and _discipline(d["filename"]) == "general")
    accuracy = grade_accuracy(disciplines, unattributed=unattributed).as_dict()

    # `confidence` is a CLAIM about how good the number will be, and it is
    # derived from the discipline-aware grade rather than from CAD presence.
    #
    # It used to read "measured" whenever any CAD file existed anywhere, which
    # put a green "Measured takeoff available" banner directly above discipline
    # chips reading "architectural PDF" — a card contradicting itself. The
    # architectural set governs ~45% of a residential budget (room areas,
    # opening counts, partition lengths, envelope), so a project whose
    # architectural drawings are PDF is not measured in any sense a person
    # pricing the job would accept, however much structural CAD sits beside it.
    #
    # grade_accuracy already encodes this and was tightened once for the same
    # reason. Deriving from it means there is ONE verdict instead of two that
    # can disagree.
    confidence, message = _CONFIDENCE_BY_GRADE[accuracy["grade"]]
    if mode == "cad" and confidence != "measured":
        message += (
            " The CAD you do have is still measured — the takeoff runs on it; "
            "it just does not cover the trades that carry the budget."
        )

    return {
        # "explicit" when the office has chosen the documents on the selection
        # screen, "category" when it is still falling back to the filing label.
        "selection_source": selection_source,
        "takeoff_mode": mode,          # cad | pdf | none
        # measured | partially_measured | estimated | insufficient — DERIVED from
        # accuracy.grade, never from CAD presence. See _CONFIDENCE_BY_GRADE.
        "confidence": confidence,
        "message": message,
        "accuracy": accuracy,
        "present": present,
        "checklist": checklist,
        "disciplines": disciplines,    # {arch: {cad,pdf}, ...}
        "counts": {"cad": len(cad_docs), "pdf_drawings": len(pdf_docs)},
        "missing_critical": missing_critical,
    }
