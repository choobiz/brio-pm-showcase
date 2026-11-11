"""How far a stored price has likely moved since it was entered.

Reads StatCan's building construction price index for Vancouver single-detached
houses (table 18-10-0289-01, quarterly, by division) from ``cost_index_points``
and compares the quarter a price was entered in with the latest published
quarter. It REPORTS drift; it never changes a price (plan phase 2: "Use it to
age stored prices, not to set them").

The index measures a whole house's cost in a division, not one product, so the
figure is a prompt to re-check a price, not a replacement for it.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalogue import CostIndexPoint
from app.services.library_vocab import STATCAN_COMPOSITE, statcan_division

SERIES = "statcan-18100289:Vancouver:Single-detached house"
#: A price is flagged for a re-check past either line. Defaults, not rules.
STALE_PCT = 5.0
STALE_DAYS = 548  # 18 months


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


class IndexTable:
    """The series loaded once per request: division → sorted [(quarter, value)]."""

    def __init__(self, points: dict[str, list[tuple[date, float]]]):
        self.points = {k: sorted(v) for k, v in points.items()}

    @classmethod
    def load(cls, db: Session) -> "IndexTable":
        pts: dict[str, list[tuple[date, float]]] = {}
        for p in db.scalars(select(CostIndexPoint).where(CostIndexPoint.series == SERIES)):
            pts.setdefault(p.division, []).append((p.ref_period, float(p.value)))
        return cls(pts)

    def _series(self, trade_key: str | None) -> tuple[str, list[tuple[date, float]]]:
        div = statcan_division(trade_key)
        if self.points.get(div):
            return div, self.points[div]
        return STATCAN_COMPOSITE, self.points.get(STATCAN_COMPOSITE, [])

    def drift(self, as_of: date | None, trade_key: str | None, today: date | None = None) -> dict | None:
        """{pct, since, latest, division, stale, reason} or None with no index loaded.

        pct is None when the price is too new to measure (entered in or after the
        latest published quarter) or has no date at all; a dateless price is
        flagged stale, because its age is unknown."""
        div, series = self._series(trade_key)
        if not series:
            return None
        today = today or date.today()
        latest_q, latest_v = series[-1]
        out = {"division": div, "latest": latest_q.isoformat(), "since": None, "pct": None,
               "stale": False, "reason": None}
        if as_of is None:
            out.update(stale=True, reason="no price date")
            return out
        q = quarter_start(as_of)
        out["since"] = q.isoformat()
        if q >= latest_q:
            out["reason"] = "newer than the latest index quarter"
        else:
            i = bisect_right([d for d, _ in series], q) - 1
            if i < 0:
                out["reason"] = "older than the index series"
            else:
                out["pct"] = round((latest_v / series[i][1] - 1) * 100, 1)
        age = (today - as_of).days
        if out["pct"] is not None and abs(out["pct"]) >= STALE_PCT:
            out.update(stale=True, reason=f"index moved {out['pct']:+.1f}% since entry")
        elif age > STALE_DAYS:
            out.update(stale=True, reason=f"entered {age // 30} months ago")
        return out
