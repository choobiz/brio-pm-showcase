"""Phase B — activation regeneration governance.

When a different estimate version is activated, its downstream documents are
regenerated so everything stays consistent with the estimate (the source of
truth). AI documents (scope, schedule, presentation) cost tokens and go through
a single management approval; deterministic ones (cash flow, SOV, contract) are
recomputed for free. A ledger records who approved and the estimated cost.
"""
from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

# Rough per-document token cost (observed Opus ~$0.45–0.55/doc).
_AI_COST = {"scope_from_estimate": 0.50, "schedule_from_estimate": 0.50, "presentation": 0.55}


def _exists(db: Session, table: str, pid: str) -> bool:
    return db.execute(text(f"SELECT 1 FROM {table} WHERE project_id = :p LIMIT 1"), {"p": pid}).first() is not None


def regeneration_plan(db: Session, project_id) -> dict:
    """What will be regenerated when this project's active estimate changes, with
    a per-artifact token-cost breakdown."""
    pid = str(project_id)
    has = {
        "schedule": _exists(db, "schedules", pid),
        "presentation": _exists(db, "presentations", pid),
        "cash_flow": _exists(db, "cash_flows", pid),
        "sov": _exists(db, "schedules_of_values", pid),
        "contract": _exists(db, "contracts", pid),
    }
    artifacts: list[dict] = [
        {"name": "Scope of Work", "kind": "ai", "cost": _AI_COST["scope_from_estimate"]},
    ]
    if has["schedule"]:
        artifacts.append({"name": "Schedule", "kind": "ai", "cost": _AI_COST["schedule_from_estimate"]})
    if has["presentation"]:
        artifacts.append({"name": "Presentation", "kind": "ai", "cost": _AI_COST["presentation"]})
    if has["cash_flow"]:
        artifacts.append({"name": "Cash Flow", "kind": "deterministic", "cost": 0.0})
    if has["sov"]:
        artifacts.append({"name": "Schedule of Values", "kind": "deterministic", "cost": 0.0})
    if has["contract"]:
        artifacts.append({"name": "Contract", "kind": "deterministic", "cost": 0.0})
    total = round(sum(a["cost"] for a in artifacts), 2)
    return {"artifacts": artifacts, "total_cost_usd": total, "has": has}


def execute_regeneration(db: Session, project_id, estimate_version_id, user) -> dict:
    """Activate the version + orchestrate the regeneration. AI docs are enqueued
    as jobs (run in FIFO order by the worker); deterministic docs are recomputed
    inline via the existing regenerate-downstream logic. Writes a ledger row."""
    from app.models.ai_job import AIJob

    pid = str(project_id)
    eid = str(estimate_version_id)
    plan = regeneration_plan(db, project_id)
    has = plan["has"]

    # 1) Activate the target estimate version.
    db.execute(text("UPDATE estimate_versions SET is_active = false "
                    "WHERE project_id = :p AND is_active = true AND id <> :e"), {"p": pid, "e": eid})
    db.execute(text("UPDATE estimate_versions SET is_active = true WHERE id = :e AND project_id = :p"), {"e": eid, "p": pid})

    # Carry the start date from the latest schedule so dates stay stable.
    ps = db.execute(text("SELECT project_start FROM schedules WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"),
                    {"p": pid}).scalar()
    ps = ps.isoformat() if ps else None

    # 2) Enqueue AI regeneration jobs IN ORDER (single worker = FIFO: scope → schedule → presentation).
    enq: list[tuple[str, dict]] = [("scope_from_estimate", {"estimate_version_id": eid})]
    if has["schedule"]:
        enq.append(("schedule_from_estimate", {"estimate_version_id": eid, "project_start": ps}))
    if has["presentation"]:
        enq.append(("presentation", {"estimate_version_id": eid, "audience": "client", "project_start": ps}))
    job_ids: list[dict] = []
    for kind, inp in enq:
        j = AIJob(project_id=project_id, kind=kind, status="queued", input=inp)
        db.add(j)
        db.flush()
        job_ids.append({"kind": kind, "job_id": str(j.id)})
    db.commit()  # make the activation + queued jobs durable before the deterministic pass

    # 3) Deterministic recompute (SOV rescale, contract recompute, cash-flow re-point).
    det: dict | None = None
    try:
        from app.routers.workbench import regenerate_downstream
        det = regenerate_downstream(project_id, db, user)
    except Exception as e:  # noqa: BLE001 — never let the deterministic step abort the flow
        det = {"error": str(e)}

    # 4) Ledger.
    approval_id = uuid4()
    who = getattr(user, "name", None) or getattr(user, "email", None) or "owner"
    db.execute(text(
        "INSERT INTO regeneration_approvals "
        "(id, project_id, estimate_version_id, approved_by_id, approved_by_name, estimated_cost_usd, artifacts) "
        "VALUES (:id, :p, :e, :uid, :un, :cost, CAST(:arts AS jsonb))"),
        {"id": str(approval_id), "p": pid, "e": eid, "uid": getattr(user, "id", None), "un": who,
         "cost": plan["total_cost_usd"], "arts": json.dumps({"plan": plan["artifacts"], "jobs": job_ids})})
    db.commit()

    return {"approval_id": str(approval_id), "activated_estimate_id": eid,
            "jobs": job_ids, "deterministic": det, "estimated_cost_usd": plan["total_cost_usd"]}
