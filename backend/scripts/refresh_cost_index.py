"""Fetch StatCan's building construction price index into cost_index_points.

Table 18-10-0289-01, Vancouver, single-detached house, every division plus the
composite. Quarterly data; run from the VM's cron a week after each release.
Inserts index points only. It never reads or changes a price: drift is worked
out when a price is shown (services/price_drift.py).

Idempotent on (series, division, quarter): a re-run adds only new quarters, and
updates a value StatCan has revised.

    PYTHONPATH=/app python scripts/refresh_cost_index.py            # dry run
    PYTHONPATH=/app python scripts/refresh_cost_index.py --apply
    PYTHONPATH=/app python scripts/refresh_cost_index.py --apply --periods 20   # backfill 5 years
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date
from decimal import Decimal

WDS = "https://www150.statcan.gc.ca/t1/wds/rest"
PID = 18100289
GEOGRAPHY = "Vancouver, British Columbia"
BUILDING = "Single-detached house"


def _post(path: str, body: list[dict]) -> list[dict]:
    req = urllib.request.Request(f"{WDS}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _member(dim: dict, name: str) -> int:
    hits = [m["memberId"] for m in dim["member"] if m["memberNameEn"] == name]
    if len(hits) != 1:
        raise SystemExit(f"StatCan metadata changed: {dim['dimensionNameEn']} has {len(hits)} '{name}'")
    return hits[0]


def fetch(periods: int) -> list[tuple[str, date, Decimal, int]]:
    """[(division, quarter, value, vector id)] for every division StatCan publishes."""
    from app.services.library_vocab import STATCAN_COMPOSITE, STATCAN_DIVISIONS

    meta = _post("getCubeMetadata", [{"productId": PID}])[0]["object"]
    dims = {d["dimensionNameEn"]: d for d in meta["dimension"]}
    geo = _member(dims["Geography"], GEOGRAPHY)
    bld = _member(dims["Type of building"], BUILDING)
    wanted = {STATCAN_COMPOSITE, *STATCAN_DIVISIONS.values()}
    divs = {m["memberNameEn"]: m["memberId"] for m in dims["Division"]["member"]
            if m["memberNameEn"] in wanted}
    missing = wanted - set(divs)
    if missing:
        raise SystemExit(f"StatCan metadata changed: divisions not found {sorted(missing)}")
    names = {v: k for k, v in divs.items()}
    body = [{"productId": PID, "coordinate": f"{geo}.{bld}.{d}.0.0.0.0.0.0.0", "latestN": periods}
            for d in divs.values()]
    out = []
    for r in _post("getDataFromCubePidCoordAndLatestNPeriods", body):
        if r.get("status") != "SUCCESS":
            continue  # a division StatCan doesn't publish for houses
        o = r["object"]
        div = names[int(o["coordinate"].split(".")[2])]
        for p in o["vectorDataPoint"]:
            if p.get("value") is None:
                continue
            out.append((div, date.fromisoformat(p["refPer"]), Decimal(str(p["value"])), o["vectorId"]))
    return out


def apply(db, points) -> tuple[int, int]:
    from app.models.catalogue import CostIndexPoint
    from app.services.price_drift import SERIES

    have = {(p.division, p.ref_period): p
            for p in db.query(CostIndexPoint).filter(CostIndexPoint.series == SERIES)}
    added = revised = 0
    for div, q, v, vec in points:
        p = have.get((div, q))
        if p is None:
            db.add(CostIndexPoint(series=SERIES, division=div, ref_period=q, value=v,
                                  payload={"vector": vec, "table": "18-10-0289-01"}))
            added += 1
        elif p.value != v:
            p.payload = {**(p.payload or {}), "revised_from": float(p.value)}
            p.value = v
            revised += 1
    return added, revised


def main() -> None:
    import app.main  # noqa: F401
    from app.db import SessionLocal

    periods = int(sys.argv[sys.argv.index("--periods") + 1]) if "--periods" in sys.argv else 4
    points = fetch(periods)
    divs = sorted({p[0] for p in points})
    latest = max((p[1] for p in points), default=None)
    print(f"{len(points)} points, {len(divs)} divisions, latest quarter {latest}")
    with SessionLocal() as db:
        added, revised = apply(db, points)
        print(f"would add {added}, revise {revised}")
        if "--apply" in sys.argv:
            db.commit()
            print("APPLIED.")
        else:
            db.rollback()
            print("Dry run: nothing written.")


if __name__ == "__main__":
    main()
