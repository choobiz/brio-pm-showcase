"""Walking an area against a list of checks, and recording what was found.

⚠️ THESE ARE BRIO'S OWN QUALITY CHECKS. THEY ARE NOT A CODE COMPLIANCE
INSTRUMENT, and nothing built on them may imply otherwise.

A completed checklist says: our supervisor walked this area, looked at these
things, and recorded what they saw. It does not say the work meets the BC
Building Code, it does not substitute for an authority inspection, and it does
not discharge anyone's professional responsibility. Those determinations belong
to the authority having jurisdiction and to the registered professionals on the
job. `DISCLAIMER` below is attached to every checklist and every export, because
a document that looks like an inspection report and is not one is the kind of
thing that gets read years later in a dispute.

The separate `inspections` table models the authority coming to look —
authority, inspection_type, lead_time_days, gates, tied to a schedule activity.
Verified 2026-10-05: it has no column for a result, a deficiency or a photo, and
holds no rows. The two are different records and must stay that way.

⚠️ A null RESULT MEANS NOT YET LOOKED AT, AND NEVER PASS. A checklist where
unanswered reads as satisfactory produces a clean record of an inspection nobody
did — which is worse than having no checklist, because it is evidence of the
wrong thing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

DISCLAIMER = (
    "Brio's own quality check. This records what our supervisor observed. It is "
    "not a code-compliance determination and does not replace an inspection by "
    "the authority having jurisdiction or review by the project's registered "
    "professionals."
)

#: Checklists for the stages where finding a defect late is expensive because
#: the work gets covered. Deliberately short — a list nobody finishes is a list
#: that records nothing, and these are written to be walked in ten minutes.
#:
#: Phrased as observations a supervisor can make on site, not as code clauses.
#: "Fire blocking installed at the locations shown on the drawings" is something
#: a person can stand in front of and answer; "complies with BCBC 9.10.16" is
#: not, and inviting that answer from a supervisor is the overreach this module
#: exists to avoid.
TEMPLATES: dict[str, dict[str, Any]] = {
    "pre_drywall": {
        "title": "Pre-drywall walkthrough",
        "when": "Before any board goes up — everything here gets covered.",
        "items": [
            "Framing matches the current structural drawings, including any revisions",
            "Blocking in place for cabinets, grab bars, handrails, TV mounts and shelving",
            "Fire blocking and draft stopping installed where the drawings show it",
            "Hangers, straps and hold-downs installed and fully nailed",
            "Plumbing rough-in complete and pressure-tested",
            "Electrical rough-in complete, boxes set to finished wall depth",
            "Mechanical rough-in complete, ducts supported and sealed",
            "Window and door openings correct size, square, and properly flashed",
            "Insulation complete with no gaps at rim joists, corners or service penetrations",
            "Vapour barrier continuous, sealed at penetrations, laps and boxes",
            "No visible water damage, mould or standing water in the assembly",
        ],
    },
    "pre_pour": {
        "title": "Pre-pour check",
        "when": "Before concrete — the one stage that cannot be opened up again.",
        "items": [
            "Formwork dimensions and layout match the drawings",
            "Forms braced, clean, and free of debris, snow or standing water",
            "Reinforcing size, spacing, laps and cover as shown on the structural drawings",
            "Anchor bolts and hold-downs correct size, correct spacing, correctly located",
            "Sleeves, conduits and under-slab services placed and protected",
            "Vapour barrier and sub-base in place where shown",
            "Required authority inspection booked or completed",
        ],
    },
    "envelope": {
        "title": "Building envelope check",
        "when": "Before cladding — water gets in at the details, not the field.",
        "items": [
            "Water-resistive barrier continuous and lapped shingle-fashion",
            "Window and door perimeters flashed and sealed to the envelope drawings",
            "Through-wall flashings installed with end dams where shown",
            "Penetrations sealed — vents, hose bibs, electrical, mechanical",
            "Rainscreen cavity clear and drainage path unobstructed",
            "Roof-to-wall and deck-to-wall transitions detailed as drawn",
            "No reverse laps or unsealed terminations visible",
        ],
    },
    "deficiency_walk": {
        "title": "Deficiency walk",
        "when": "Before handover — what the owner is going to find.",
        "items": [
            "Paint and finishes complete, no visible defects at normal viewing distance",
            "Doors and windows operate, latch, and are adjusted",
            "Hardware installed, aligned and secure",
            "Flooring complete, transitions set, no lifting or gaps",
            "Millwork and cabinetry aligned, doors and drawers adjusted",
            "Fixtures operate — plumbing runs and drains, lights switch, outlets live",
            "Mechanical operates and registers are installed",
            "Site clean, protection removed, no construction debris left",
            "Owner's manuals, warranties and keys assembled for handover",
        ],
    },
}


class ChecklistError(ValueError):
    """Message is written for a person."""


def list_templates() -> list[dict[str, Any]]:
    return [
        {"key": k, "title": v["title"], "when": v["when"], "item_count": len(v["items"])}
        for k, v in TEMPLATES.items()
    ]


def _blank_items(template_key: str) -> list[dict[str, Any]]:
    tpl = TEMPLATES[template_key]
    return [
        {
            "id": f"{template_key}-{i:02d}",
            "text": text,
            # ⚠️ None, not "pass". Not yet looked at.
            "result": None,
            "note": None,
            "photos": [],
        }
        for i, text in enumerate(tpl["items"], start=1)
    ]


def create(
    db: Session, *, project_id: UUID, template_key: str,
    location: str | None = None, performed_by_id: UUID | None = None,
) -> dict[str, Any]:
    if template_key not in TEMPLATES:
        raise ChecklistError(f"There is no {template_key!r} checklist.")
    tpl = TEMPLATES[template_key]
    cid = uuid4()
    db.execute(_T("""
        INSERT INTO qa_checklists
            (id, project_id, template_key, title, location, status, items, performed_by_id)
        VALUES (:i, :p, :k, :t, :l, 'open', CAST(:items AS jsonb), :u)"""),
        {"i": str(cid), "p": str(project_id), "k": template_key,
         "t": tpl["title"], "l": location,
         "items": json.dumps(_blank_items(template_key)),
         "u": str(performed_by_id) if performed_by_id else None})
    return get(db, cid)


def get(db: Session, checklist_id: UUID) -> dict[str, Any]:
    row = db.execute(_T("SELECT * FROM qa_checklists WHERE id = :i"),
                     {"i": str(checklist_id)}).mappings().first()
    if row is None:
        raise ChecklistError("That checklist is no longer here.")
    out = dict(row)
    out["id"] = str(out["id"])
    out["project_id"] = str(out["project_id"])
    out["performed_by_id"] = str(out["performed_by_id"]) if out["performed_by_id"] else None
    out["disclaimer"] = DISCLAIMER
    out["progress"] = progress(out["items"])
    return out


def progress(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Where a walkthrough has got to.

    `not_looked_at` is reported first and as its own number, because it is the
    one a reader must not confuse with a pass.
    """
    rows = list(items or [])
    counts = {"pass": 0, "fail": 0, "na": 0, "not_looked_at": 0}
    for it in rows:
        r = it.get("result")
        counts["not_looked_at" if r is None else r] = \
            counts.get("not_looked_at" if r is None else r, 0) + 1
    return {
        "total": len(rows),
        **counts,
        "failures": [it["text"] for it in rows if it.get("result") == "fail"],
        "complete": counts["not_looked_at"] == 0 and bool(rows),
    }


def set_result(
    db: Session, checklist_id: UUID, item_id: str, *,
    result: str | None, note: str | None = None, photos: list[str] | None = None,
) -> dict[str, Any]:
    """Record one observation. `result=None` puts an item back to unanswered."""
    if result is not None and result not in ("pass", "fail", "na"):
        raise ChecklistError(f"{result!r} is not a result.")
    cl = get(db, checklist_id)
    if cl["status"] == "complete":
        raise ChecklistError(
            "This walkthrough was completed. Start a new one rather than editing "
            "a record of what was observed."
        )
    items = cl["items"]
    for it in items:
        if it["id"] == item_id:
            it["result"] = result
            if note is not None:
                it["note"] = note.strip() or None
            if photos is not None:
                it["photos"] = photos
            break
    else:
        raise ChecklistError(f"There is no item {item_id!r} on this checklist.")

    db.execute(_T("UPDATE qa_checklists SET items = CAST(:v AS jsonb), "
                  "updated_at = now() WHERE id = :i"),
               {"v": json.dumps(items), "i": str(checklist_id)})
    return get(db, checklist_id)


def complete(
    db: Session, checklist_id: UUID, *, user_id: UUID | None = None,
    allow_unanswered: bool = False,
) -> dict[str, Any]:
    """Close the walkthrough.

    ⚠️ REFUSES BY DEFAULT WHILE ANYTHING IS UNANSWERED. A completed checklist is
    a record that somebody looked; closing one with items nobody reached makes
    it a record that they did. Closing anyway is allowed — a walk can be
    genuinely partial — but it has to be asked for, and the unanswered items stay
    unanswered rather than being quietly marked.
    """
    cl = get(db, checklist_id)
    p = cl["progress"]
    if p["not_looked_at"] and not allow_unanswered:
        raise ChecklistError(
            f"{p['not_looked_at']} of {p['total']} checks have not been looked at. "
            f"Walk them, or close it anyway and they stay recorded as not looked at."
        )
    db.execute(_T("UPDATE qa_checklists SET status = 'complete', completed_at = now(), "
                  "performed_by_id = COALESCE(:u, performed_by_id), updated_at = now() "
                  "WHERE id = :i"),
               {"u": str(user_id) if user_id else None, "i": str(checklist_id)})
    return get(db, checklist_id)


def list_for_project(db: Session, project_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(_T("""
        SELECT id, template_key, title, location, status, items, completed_at, created_at
          FROM qa_checklists WHERE project_id = :p
         ORDER BY created_at DESC"""), {"p": str(project_id)}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["progress"] = progress(d.pop("items"))
        out.append(d)
    return out
