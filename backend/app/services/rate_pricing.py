"""Deterministic rate-library pricing engine — Track 2 Phase 6.

Given an assembly + quantity + project type, resolve the best active
:class:`RateEntry` and return a priced amount plus full provenance. "Best" is
strictly ordered so pricing is deterministic and auditable:

  1. project-type match — an entry whose ``project_type`` equals the request
     beats an ``any`` entry (a ``commercial`` request never uses a
     ``residential`` rate, and vice-versa);
  2. source trust — ``subcontract_derived`` > ``published`` > ``composed`` >
     ``market`` (what BRIO actually paid beats a published book rate beats a
     built-up rate beats a market fallback);
  3. recency — most recent ``as_of_date`` then ``created_at``.

The engine only *reads* rate_entries; composing a new installed rate from
material + labour tables is an authoring step (``compose_installed_rate``),
whose result is written back as a ``composed`` entry.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rate_library import (
    LabourRate,
    MaterialRate,
    RateAssembly,
    RateEntry,
)


# Lower = more trusted. Unknown sources sort last.
SOURCE_PRIORITY: dict[str, int] = {
    # "own" = a rate BRIO set while reviewing an estimate (phase 3 write-back,
    # 2026-10-16); ties with what a subcontract actually cost, newest wins.
    "own": 0,
    "subcontract_derived": 0,
    "published": 1,
    "composed": 2,
    "market": 3,
}
MATERIAL_PRIORITY: dict[str, int] = {"own": 0, "supplier": 0, "market": 1}
LABOUR_PRIORITY: dict[str, int] = {"own": 0, "association": 1, "workbc": 2, "market": 3}

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def _eligible(entry_ptype: str, requested: str) -> bool:
    """An entry is usable if the request is unconstrained, matches exactly, or
    the entry is project-type-agnostic ('any')."""
    return requested == "any" or entry_ptype == requested or entry_ptype == "any"


def _recency(as_of: date | None, created: datetime | None) -> tuple[float, float]:
    """Higher = newer. Missing as_of sorts oldest within its source tier."""
    as_of_ord = as_of.toordinal() if as_of else 0
    created_ts = (created or _MIN_DT).timestamp()
    return (as_of_ord, created_ts)


def _ptype_rank(entry_ptype: str, requested: str) -> int:
    """0 if the entry is specific to the requested type, 1 if it's 'any'.
    Only a tiebreaker — a project-type-agnostic ('any') rate is an explicit
    'applies to both' assertion, NOT a mismatch, so it must not override a
    higher-trust source. Opposite types are already excluded by _eligible."""
    return 0 if (requested != "any" and entry_ptype == requested) else 1


def _entry_rank(entry: RateEntry, requested: str) -> tuple:
    """Sort key — smallest is best. Source trust leads (the library's thesis:
    real paid data > published > composed > market); project-type match and
    recency are tiebreakers. Recency inverted so newer wins."""
    src_rank = SOURCE_PRIORITY.get(entry.source_type, 99)
    ptype_rank = _ptype_rank(entry.project_type, requested)
    as_of_ord, created_ts = _recency(entry.as_of_date, entry.created_at)
    return (src_rank, ptype_rank, -as_of_ord, -created_ts)


def resolve_best_entry(
    db: Session, *, assembly_code: str, project_type: str = "any"
) -> RateEntry | None:
    """Return the single best active rate entry for an assembly, or None."""
    requested = project_type or "any"
    entries = list(
        db.scalars(
            select(RateEntry)
            .join(RateAssembly, RateAssembly.id == RateEntry.assembly_id)
            .where(RateAssembly.code == assembly_code, RateEntry.is_active.is_(True),
                   # A draft or held assembly is under review: it must never
                   # price anything (starter library, 2026-10-16).
                   RateAssembly.status == "active")
        )
    )
    eligible = [e for e in entries if _eligible(e.project_type, requested)]
    if not eligible:
        return None
    eligible.sort(key=lambda e: _entry_rank(e, requested))
    return eligible[0]


def _provenance(entry: RateEntry) -> dict[str, Any]:
    return {
        "source_type": entry.source_type,
        "confidence": entry.confidence,
        "entry_id": str(entry.id),
        "project_type": entry.project_type,
        "source_ref": entry.source_ref,
        "as_of_date": entry.as_of_date.isoformat() if entry.as_of_date else None,
    }


def price(
    db: Session,
    *,
    assembly_code: str,
    quantity: float | Decimal,
    project_type: str = "any",
) -> dict[str, Any] | None:
    """Price an assembly at the given quantity. Returns None (not an error)
    when no rate exists — the caller falls back to LLM/benchmark pricing."""
    entry = resolve_best_entry(db, assembly_code=assembly_code, project_type=project_type)
    if entry is None:
        return None
    qty = Decimal(str(quantity))
    amount = entry.unit_rate * qty
    return {
        "assembly_code": assembly_code,
        "unit": entry.unit,
        "unit_rate": float(entry.unit_rate),
        "quantity": float(qty),
        "amount": round(float(amount), 2),
        "provenance": _provenance(entry),
    }


def list_best_rates(
    db: Session, *, project_type: str = "any", status: str = "active"
) -> list[dict[str, Any]]:
    """The current best rate for every assembly in `status` (active by default)
    — powers the Cost Intelligence UI. Assemblies with no eligible entry are
    still listed (rate = None) so gaps are visible. For draft and held
    assemblies the rate shown is their newest entry, for review only: they
    never price (resolve_best_entry refuses them)."""
    assemblies = list(
        db.scalars(
            select(RateAssembly)
            .where(RateAssembly.status == status)
            .order_by(RateAssembly.trade, RateAssembly.code)
        )
    )
    out: list[dict[str, Any]] = []
    for a in assemblies:
        if status == "active":
            entry = resolve_best_entry(db, assembly_code=a.code, project_type=project_type)
        else:
            entry = _newest_entry(db, a, project_type)
        out.append(
            {
                "code": a.code,
                "name": a.name,
                "trade": a.trade,
                "trade_key": a.trade_key,
                "trade_label": _trade_label(a.trade_key),
                "division": _division(a.trade_key),
                "category": a.category,
                "unit": a.unit,
                "project_type": a.project_type,
                "status": a.status,
                "unit_rate": float(entry.unit_rate) if entry else None,
                "provenance": _provenance(entry) if entry else None,
            }
        )
    return out


def _newest_entry(db: Session, a: RateAssembly, project_type: str) -> RateEntry | None:
    rows = [e for e in db.scalars(select(RateEntry).where(RateEntry.assembly_id == a.id,
                                                          RateEntry.is_active.is_(True)))
            if _eligible(e.project_type, project_type or "any")]
    rows.sort(key=lambda e: _entry_rank(e, project_type or "any"))
    return rows[0] if rows else None


def _trade_label(trade_key: str | None) -> str | None:
    from app.services.library_vocab import TRADE_KEYS, trade

    return trade(trade_key).label if trade_key in TRADE_KEYS else None


def _division(trade_key: str | None) -> str | None:
    from app.services.library_vocab import division_for

    return division_for(trade_key)


# ── Material / labour resolution (feeds the compose path + the UI) ──────


def best_material_rate(
    db: Session, *, name: str | None = None, trade: str | None = None,
    project_type: str = "any",
) -> MaterialRate | None:
    """Best active material rate: supplier (our) beats market fallback, then
    newest. Match by exact name if given, else by trade."""
    stmt = select(MaterialRate).where(MaterialRate.is_active.is_(True))
    if name:
        stmt = stmt.where(MaterialRate.name == name)
    if trade:
        stmt = stmt.where(MaterialRate.trade == trade)
    rows = list(db.scalars(stmt))
    req = project_type or "any"
    rows = [m for m in rows if _eligible(m.project_type, req)]
    if not rows:
        return None
    rows.sort(
        key=lambda m: (
            MATERIAL_PRIORITY.get(m.source, 99),
            _ptype_rank(m.project_type, req),
            -_recency(m.as_of_date, m.created_at)[0],
            -_recency(m.as_of_date, m.created_at)[1],
        )
    )
    return rows[0]


def best_labour_rate(
    db: Session, *, trade: str, project_type: str = "any"
) -> LabourRate | None:
    """Best active labour rate for a trade: own beats association beats WorkBC
    beats market, then newest."""
    rows = list(
        db.scalars(
            select(LabourRate).where(
                LabourRate.trade == trade, LabourRate.is_active.is_(True)
            )
        )
    )
    req = project_type or "any"
    rows = [r for r in rows if _eligible(r.project_type, req)]
    if not rows:
        return None
    rows.sort(
        key=lambda r: (
            LABOUR_PRIORITY.get(r.source, 99),
            _ptype_rank(r.project_type, req),
            -_recency(r.as_of_date, r.created_at)[0],
            -_recency(r.as_of_date, r.created_at)[1],
        )
    )
    return rows[0]


def compose_installed_rate(
    *, material_cost: float | Decimal, labour_rate: float | Decimal,
    productivity_hr_per_unit: float | Decimal,
) -> dict[str, Any]:
    """Compose an installed unit rate = material + labour_rate × productivity.
    Pure function used by the authoring flow to build a ``composed`` entry."""
    material = Decimal(str(material_cost))
    rate = Decimal(str(labour_rate))
    hours = Decimal(str(productivity_hr_per_unit))
    labour = rate * hours
    installed = material + labour
    return {
        "installed_rate": round(float(installed), 4),
        "material_cost": float(material),
        "labour_cost": round(float(labour), 4),
        "labour_rate": float(rate),
        "productivity_hr_per_unit": float(hours),
    }
