"""Libraries landing page (round 2 board R2Libraries): what needs attention,
and a card per library. Read-only, office only."""
from __future__ import annotations

import inspect
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import require_office  # noqa: E402
from app.models.rate_library import MaterialRate, RateAssembly  # noqa: E402
from app.routers import libraries as router  # noqa: E402
from app.services import libraries as svc  # noqa: E402


def test_needs_you_names_unpriced_assemblies_and_missing_skus(db_session):
    db = db_session
    tag = uuid4().hex[:6]
    for st, n in (("active", 2), ("draft", 2), ("held", 1)):
        for i in range(n):
            db.add(RateAssembly(code=f"T-{tag}-{st}-{i}", name=f"{st} assembly {i}", trade="framing", unit="sqft",
                                status=st))
    db.add(MaterialRate(name=f"2x4 {tag}", unit="ea", unit_cost=Decimal("5.10"), source="manual",
                        as_of_date=date(2026, 1, 1)))
    db.flush()
    out = svc.overview(db, today=date(2026, 11, 2))
    needs = {n["kind"]: n for n in out["needs"]}
    assert needs["unpriced"]["count"] >= 3 and f"draft assembly 0" in needs["unpriced"]["items"]
    assert "drafts" in needs["unpriced"]["text"] and "held" in needs["unpriced"]["text"]
    assert needs["sku"]["count"] >= 1
    assert out["cards"]["rates"]["assemblies"] >= 2 and out["cards"]["rates"]["materials"] >= 1


def test_the_overview_is_office_only():
    for route in router.router.routes:
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert require_office in deps, route.path
