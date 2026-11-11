"""New-project setup guide — tells the admin the right first steps and which
documents are needed (by project type) for a reliable scope + estimate.

Pure read: computes current state (docs / scope / estimate) + the next step,
and returns a type-tailored "minimum documents" checklist. The document
knowledge lives here so it's source-of-truth and reusable by intake/AI later.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _doc(label: str, category: str, why: str, required: bool) -> dict[str, Any]:
    return {"label": label, "category": category, "why": why, "required": required}


# ── Document recommendations per bucket ──────────────────────────────────────
_NEW_CONSTRUCTION = [
    _doc("Architectural drawings — permit set (plans, elevations, sections)", "drawing",
         "drives areas & quantities for the whole estimate", True),
    _doc("Structural drawings (foundation, framing)", "drawing",
         "structural scope is a major cost driver on new builds", True),
    _doc("Survey / site plan (legal + topographic)", "drawing",
         "grades, setbacks & site area for site work and excavation", True),
    _doc("Civil / servicing drawings (stormwater, utilities, driveway)", "drawing",
         "underground servicing is easily under-scoped without these", False),
    _doc("Geotechnical report", "report",
         "soil conditions set the foundation design and excavation cost", False),
    _doc("Arborist report / tree management plan", "report",
         "tree retention & protection can add cost and constrain the site", False),
    _doc("Mechanical / electrical / plumbing drawings", "drawing",
         "MEP scope where available; otherwise estimated by allowance", False),
    _doc("Energy / Step Code report", "report",
         "envelope & mechanical requirements affect cost", False),
    _doc("Specifications / finish schedule", "scope",
         "material grade drives cost — vague specs mean unreliable numbers", False),
    _doc("Development / building permit documents", "permit",
         "confirms approved scope and any conditions", False),
]

_RENOVATION = [
    _doc("Photos of the existing space", "photo",
         "condition drives demo & repair scope", True),
    _doc("Existing floor plan or measured dimensions", "drawing",
         "quantities can't be reliable without dimensions", True),
    _doc("Scope / wish list (what's included)", "client_note",
         "defines what to price — the biggest source of estimate error", True),
    _doc("Design drawings or inspiration", "drawing",
         "clarifies the target finish level", False),
    _doc("Material selections / finishes", "scope",
         "grade drives cost (tile, cabinets, fixtures)", False),
    _doc("Strata bylaws / rules", "report",
         "condo/townhouse work has hour & method restrictions that affect cost", False),
    _doc("Hazardous-materials report (pre-1990 buildings)", "report",
         "asbestos/lead abatement is a real, often-missed cost", False),
]

_GENERAL = [
    _doc("Drawings / plans", "drawing", "the basis for quantities", True),
    _doc("Site photos", "photo", "current conditions", True),
    _doc("Project brief / scope notes", "client_note", "defines what to price", True),
    _doc("Consultant reports (if any)", "report", "surface hidden-condition cost", False),
    _doc("Budget / target", "client_note", "calibrates the level of finish", False),
]

_NEW_KEYWORDS = ("multiplex", "quadplex", "fourplex", "triplex", "duplex", "laneway",
                 "townhouse", "new build", "new home", "new construction", "addition",
                 "development", "new garage", "adu", "carriage")
_RENO_KEYWORDS = ("renovation", "reno", "bathroom", "kitchen", "basement", "interior",
                  "remodel", "refinish", "refresh", "suite conversion")


def _bucket(project: dict) -> tuple[str, list[dict]]:
    hay = " ".join(str(project.get(k) or "") for k in ("type", "name", "building_type")).lower()
    if any(k in hay for k in _NEW_KEYWORDS):
        return "new_construction", _NEW_CONSTRUCTION
    if any(k in hay for k in _RENO_KEYWORDS):
        return "renovation", _RENOVATION
    return "general", _GENERAL


def compute_getting_started(db: Session, project_id: str) -> dict[str, Any]:
    proj = db.execute(
        text("select name, type, building_type from projects where id=:p"),
        {"p": project_id},
    ).mappings().one()

    doc_count = int(db.execute(
        text("select count(*) from documents where project_id=:p and archived=false"),
        {"p": project_id},
    ).scalar() or 0)
    has_scope = bool(db.execute(
        text("select 1 from scopes where project_id=:p limit 1"), {"p": project_id},
    ).first())
    has_estimate = bool(db.execute(
        text("select 1 from estimate_versions where project_id=:p limit 1"), {"p": project_id},
    ).first())

    has_documents = doc_count > 0
    if not has_documents:
        next_step = "add_documents"
    elif not has_scope:
        next_step = "generate_scope"
    elif not has_estimate:
        next_step = "generate_estimate"
    else:
        next_step = "done"

    bucket, docs = _bucket(dict(proj))
    return {
        "project_type": proj["type"],
        "bucket": bucket,
        "state": {
            "has_documents": has_documents,
            "document_count": doc_count,
            "has_scope": has_scope,
            "has_estimate": has_estimate,
        },
        "next_step": next_step,
        "steps": [
            {"key": "documents", "label": "Add documents", "done": has_documents},
            {"key": "scope", "label": "Generate scope", "done": has_scope},
            {"key": "estimate", "label": "Generate estimate", "done": has_estimate},
        ],
        "documents": docs,
    }
