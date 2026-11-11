"""Libraries landing page (round 2 board R2Libraries): what needs attention
in the libraries every estimate prices from, and one card per library.
Read-only."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.rate_library import MaterialRate
from app.services.price_drift import IndexTable


def overview(db: Session, today: date | None = None) -> dict[str, Any]:
    q = lambda sql, **k: db.execute(text(sql), k)  # noqa: E731
    status = dict(q("SELECT status, count(*) FROM rate_assemblies GROUP BY status").all())
    unpriced = [dict(r) for r in q("""SELECT code, name, status FROM rate_assemblies
                                       WHERE status IN ('draft', 'held') ORDER BY status, name""").mappings()]
    idx = IndexTable.load(db)
    mats = db.scalars(select(MaterialRate)).all()
    drifting = []
    for m in mats:
        d = idx.drift(m.as_of_date, m.trade_key, today)
        if d and d.get("stale"):
            drifting.append({"name": m.name, "reason": d.get("reason")})
    no_sku = sum(1 for m in mats if not (m.sku or "").strip())
    needs = []
    if unpriced:
        needs.append({"kind": "unpriced", "count": len(unpriced),
                      "text": f"{len(unpriced)} assemblies don't price yet: "
                              f"{status.get('draft', 0)} drafts, {status.get('held', 0)} held",
                      "items": [u["name"] for u in unpriced], "href": "/rates"})
    if drifting:
        needs.append({"kind": "drift", "count": len(drifting),
                      "text": f"{len(drifting)} material prices drifting: 5%+ off the cost index or older than 18 months",
                      "items": [f"{d['name']} ({d['reason']})" for d in drifting[:12]], "href": "/rates"})
    if mats and no_sku:
        needs.append({"kind": "sku", "count": no_sku,
                      "text": ("No material has a supplier SKU" if no_sku == len(mats)
                               else f"{no_sku} materials have no supplier SKU")
                              + ": a price-sheet import can't update them until they do",
                      "items": [], "href": "/rates"})
    cat = q("SELECT supplier, count(*) AS n FROM catalogue_items GROUP BY supplier ORDER BY n DESC").mappings().all()
    idx_row = q("""SELECT count(*) AS n, count(DISTINCT division) AS divs, min(ref_period) AS since,
                          max(ref_period) AS latest FROM cost_index_points""").mappings().first()
    sets = [r[0] for r in q("SELECT name FROM division_sets WHERE is_active ORDER BY name").all()]
    bills = q("""SELECT count(*), count(DISTINCT project_id) FROM project_costs
                 WHERE COALESCE(payload->>'source', '') = 'entered'""").first()
    return {
        "needs": needs,
        "cards": {
            "rates": {"assemblies": status.get("active", 0), "materials": len(mats),
                      "labour": q("SELECT count(*) FROM labour_rates").scalar()},
            "catalogue": [{"supplier": r["supplier"], "lines": r["n"]} for r in cat],
            "trades": q("SELECT count(*) FROM trades").scalar(),
            "divisions": sets,
            "cost_trends": {"points": idx_row["n"], "divisions": idx_row["divs"],
                            "since": str(idx_row["since"]) if idx_row["since"] else None,
                            "latest": str(idx_row["latest"]) if idx_row["latest"] else None},
            "actual_costs": {"bills": bills[0], "projects": bills[1]},
        },
    }
