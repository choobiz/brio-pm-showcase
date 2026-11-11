"""Resolve which cost divisions an estimate should be organised into.

One hard-coded category list can never be right for BRIO, because the breakdown
depends on the project type: residential work uses BRIO's own 18 numbered
divisions in construction sequence, commercial work uses CSI MasterFormat. The
generic 19-category list that estimate generation used to hard-code came from a
retired reference file and matched neither, so every residential estimate had to
be recategorised by hand after the model produced it.

⚠️ This only shapes what the NEXT generation is told to produce. Stored estimate
payloads carry their own category names and are never rewritten from here —
existing records stay exactly as they are.

An unclassified project (``type IS NULL``) resolves to nothing and the caller
falls back to the generic list. That fallback is reported, not silent: a project
quietly estimated against the wrong breakdown is the failure mode this module
exists to remove.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.division import Division, DivisionSet, ProjectTypeDivisionSet


def division_set_for_type(db: Session, project_type: str | None) -> DivisionSet | None:
    """The active set mapped to this project type, or None when unmapped."""
    if not project_type:
        return None
    row = db.execute(
        select(DivisionSet)
        .join(ProjectTypeDivisionSet, ProjectTypeDivisionSet.set_id == DivisionSet.id)
        .where(
            ProjectTypeDivisionSet.project_type == project_type,
            DivisionSet.is_active.is_(True),
        )
    ).scalars().first()
    return row


def divisions_for_type(
    db: Session, project_type: str | None, *, include_optional: bool = False
) -> list[Division]:
    """Ordered divisions for a project type.

    Reserved numbers are never returned — they have no title and no meaning.
    ``active_by_default`` filters CSI's 35 active divisions down to the ~21 BRIO
    plausibly prices; pass ``include_optional`` to get the rest as well (e.g. a
    project genuinely needing Conveying Equipment for an elevator).
    """
    s = division_set_for_type(db, project_type)
    if s is None:
        return []
    stmt = (
        select(Division)
        .where(Division.set_id == s.id, Division.is_reserved.is_(False))
        .order_by(Division.sequence)
    )
    if not include_optional:
        stmt = stmt.where(Division.active_by_default.is_(True))
    return list(db.scalars(stmt))


def division_context(db: Session, project_type: str | None) -> dict[str, Any] | None:
    """The block attached to an AI payload so the prompt can name real divisions.

    Returns None when the type is unmapped, which the caller must treat as a
    reportable gap rather than a default.
    """
    s = division_set_for_type(db, project_type)
    if s is None:
        return None
    rows = divisions_for_type(db, project_type)
    if not rows:
        return None
    return {
        "set_code": s.code,
        "set_name": s.name,
        "standard": s.standard,
        "version": s.version,
        # Exactly the strings a category should carry — number and title are one
        # name, so the model is given the whole label, not a bare title.
        "labels": [d.label for d in rows],
        # Seeds each category's contingencyPct: 0 on general conditions, 5 on
        # trades. Set per division, never globally.
        "contingency_pct": {d.label: float(d.contingency_pct) for d in rows},
    }
