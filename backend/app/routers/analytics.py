"""Analytics router — portfolio cost intelligence (leverage feature L1 v1).

Aggregates the `project_cost` corpus (subtrade/supplier actuals migrated from
the Google Drive archive) into benchmarks: cost by standard category, by
project, by year, and by kind. This is the data foundation for calibrating
estimates to BRIO's real numbers instead of generic regional rates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Numeric, func, select
from sqlalchemy.orm import Session

from app.auth import require_office, CurrentUser, get_current_user
from app.db import get_db
from app.models.estimate import Estimate
from app.models.project import Project
from app.models.project_cost import ProjectCost

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_office)])


def _f(v: object) -> float:
    return float(v) if v is not None else 0.0


@router.get("/costs")
def cost_analytics(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Portfolio-wide rollups of project costs."""
    amount = ProjectCost.amount

    total_amount, cost_count = db.execute(
        select(func.coalesce(func.sum(amount), 0), func.count(ProjectCost.id))
    ).one()
    projects_with_costs = db.execute(
        select(func.count(func.distinct(ProjectCost.project_id)))
    ).scalar_one()

    by_category = [
        {"category": cat or "Uncategorized", "total": _f(tot),
         "count": cnt, "avg": _f(avg)}
        for cat, tot, cnt, avg in db.execute(
            select(ProjectCost.category, func.sum(amount),
                   func.count(ProjectCost.id), func.avg(amount))
            .group_by(ProjectCost.category)
            .order_by(func.sum(amount).desc().nullslast())
        ).all()
    ]

    by_project = [
        {"project_id": str(pid), "slug": slug, "name": name,
         "total": _f(tot), "count": cnt}
        for pid, slug, name, tot, cnt in db.execute(
            select(Project.id, Project.slug, Project.name,
                   func.sum(amount), func.count(ProjectCost.id))
            .join(ProjectCost, ProjectCost.project_id == Project.id)
            .group_by(Project.id, Project.slug, Project.name)
            .order_by(func.sum(amount).desc().nullslast())
        ).all()
    ]

    year = func.extract("year", ProjectCost.cost_date)
    by_year = [
        {"year": int(y) if y is not None else None,
         "total": _f(tot), "count": cnt}
        for y, tot, cnt in db.execute(
            select(year, func.sum(amount), func.count(ProjectCost.id))
            .group_by(year).order_by(year)
        ).all()
    ]

    by_kind = [
        {"kind": kind, "total": _f(tot), "count": cnt}
        for kind, tot, cnt in db.execute(
            select(ProjectCost.kind, func.sum(amount), func.count(ProjectCost.id))
            .group_by(ProjectCost.kind)
            .order_by(func.sum(amount).desc().nullslast())
        ).all()
    ]

    return {
        "summary": {
            "total_amount": _f(total_amount),
            "cost_count": cost_count,
            "projects_with_costs": projects_with_costs,
            "category_count": sum(
                1 for c in by_category if c["category"] != "Uncategorized"
            ),
        },
        "by_category": by_category,
        "by_project": by_project,
        "by_year": by_year,
        "by_kind": by_kind,
    }


@router.get("/variance")
def variance_analytics(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict:
    """Estimate-vs-actual at the project level: latest estimate's base-cost
    subtotal compared to the sum of project_costs for the same project."""
    subtotal = Estimate.payload["subtotal"].astext.cast(Numeric)
    latest_rn = func.row_number().over(
        partition_by=Estimate.project_id, order_by=Estimate.version.desc()
    ).label("rn")
    ranked = (
        select(
            Estimate.project_id.label("pid"),
            Estimate.ref.label("ref"),
            Estimate.version.label("version"),
            subtotal.label("subtotal"),
            latest_rn,
        ).subquery()
    )
    latest_est = select(ranked).where(ranked.c.rn == 1).subquery()

    costs = (
        select(
            ProjectCost.project_id.label("pid"),
            func.sum(ProjectCost.amount).label("total"),
            func.count(ProjectCost.id).label("count"),
        )
        .group_by(ProjectCost.project_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Project.id, Project.slug, Project.name,
            latest_est.c.ref, latest_est.c.subtotal, latest_est.c.version,
            costs.c.total, costs.c.count,
        )
        .join(latest_est, latest_est.c.pid == Project.id)
        .join(costs, costs.c.pid == Project.id)
        .order_by(Project.name)
    ).all()

    projects = []
    for pid, slug, name, ref, est_sub, version, actual, count in rows:
        est_f = _f(est_sub)
        act_f = _f(actual)
        delta = act_f - est_f
        pct = (delta / est_f * 100.0) if est_f else None
        projects.append({
            "project_id": str(pid),
            "slug": slug,
            "name": name,
            "estimate": {"ref": ref, "subtotal": est_f, "version": version},
            "actual": {"total": act_f, "count": count},
            "delta": delta,
            "pct": pct,
        })
    return {"projects": projects}
