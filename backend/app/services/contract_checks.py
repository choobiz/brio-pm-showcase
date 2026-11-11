"""Contract › checks, time extension, signed copy, amend (round 2 board
R2Contract). Nothing here changes a signed contract's terms:
  * the time-extension button drafts a CHANGE ORDER (no cost, the days the
    schedule has moved past the contract date) for the office to review/send;
  * the countersigned copy is a document the contract points to;
  * Amend makes a new DRAFT version copied from the signed one; the signed
    version stays as it is.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.change_order import ChangeOrder
from app.services import costs as costs_svc
from app.services.actuals import site_today


class ContractError(ValueError):
    pass


def _contract(db: Session, pid: str):
    return db.execute(text("""
        SELECT * FROM contracts WHERE project_id = :p
         ORDER BY (status IN ('executed','signed')) DESC, version_number DESC LIMIT 1"""), {"p": pid}).mappings().first()


def _expected_finish(db: Session, pid: str) -> date | None:
    r = db.execute(text("""SELECT p50_finish_date, deterministic_finish_date FROM schedules
                            WHERE project_id = :p ORDER BY version_number DESC LIMIT 1"""), {"p": pid}).first()
    return (r[0] or r[1]) if r else None


def checks(db: Session, project_id: UUID | str) -> dict[str, Any]:
    pid = str(project_id)
    c = _contract(db, pid)
    if not c:
        return {"contract": None, "checks": [], "late": None}
    payload = c["payload"] if isinstance(c["payload"], dict) else json.loads(c["payload"] or "{}")
    out: list[dict[str, str]] = []
    vals = costs_svc.contract(db, pid) or {}
    revised = Decimal(str(vals.get("revised") or 0))
    stages = payload.get("paymentSchedule") or []
    stage_sum = sum(Decimal(str(s.get("amount") or 0)) for s in stages)
    if stages and abs(stage_sum - revised) >= Decimal("0.01"):
        diff = stage_sum - revised
        last = Decimal(str(stages[-1].get("amount") or 0)) - diff
        out.append({"kind": "stages", "text": (
            f"The signed stages add up to ${stage_sum:,.2f}, ${abs(diff):,.2f} "
            f"{'more' if diff > 0 else 'less'} than the revised value (${revised:,.2f}). "
            f"Bill the last stage at ${last:,.2f} to close it.")})
    gst_stated = payload.get("gstStated")
    if gst_stated is None:
        # Transcribed contracts carry what the signed page says in their notes.
        for w in payload.get("warnings") or []:
            if "GST" in w and "states" in w:
                out.append({"kind": "gst", "text": w + " Invoices use 5% of the revised value."})
                break
    if c["status"] in ("signed", "executed") and not c.get("signed_copy_document_id"):
        out.append({"kind": "signed_copy", "text": "No countersigned owner copy on file."})
    sc = c["substantial_completion_date"]
    expected = _expected_finish(db, pid)
    late = None
    if sc and expected and expected > sc:
        late = {"contract_date": str(sc), "expected": str(expected), "days": (expected - sc).days,
                "passed": sc < site_today(),
                "extension_co": db.execute(text("""SELECT number FROM change_orders WHERE project_id = :p
                                                   AND detail->>'source' = 'time-extension' ORDER BY number DESC
                                                   LIMIT 1"""), {"p": pid}).scalar()}
    return {"contract": {"id": str(c["id"]), "status": c["status"], "reference": c["reference"],
                         "signed_copy_document_id": str(c["signed_copy_document_id"])
                         if c.get("signed_copy_document_id") else None},
            "checks": out, "late": late}


def draft_time_extension(db: Session, project_id: UUID | str, uid: UUID | None) -> ChangeOrder:
    pid = str(project_id)
    info = checks(db, pid)
    late = info["late"]
    if not late:
        raise ContractError("The schedule finishes on or before the contract date; no extension needed.")
    nxt = (db.execute(select(ChangeOrder.number).where(ChangeOrder.project_id == UUID(pid))
                      .order_by(ChangeOrder.number.desc()).limit(1)).scalar() or 0) + 1
    sc, exp = date.fromisoformat(late["contract_date"]), date.fromisoformat(late["expected"])
    co = ChangeOrder(
        project_id=UUID(pid), number=nxt,
        title=f"Time extension: substantial completion {sc:%b %d, %Y} → {exp:%b %d, %Y}",
        description=(f"Extends substantial completion by {late['days']} days, from {sc:%b %d, %Y} (contract) "
                     f"to {exp:%b %d, %Y} (current schedule). No change to the contract price. "
                     "Add the reasons (permit, weather, owner changes) before sending."),
        cost_impact=Decimal("0"), schedule_impact_days=late["days"], status="draft", created_by_id=uid,
        detail={"source": "time-extension", "contract_date": str(sc), "new_date": str(exp)},
    )
    db.add(co)
    db.flush()
    return co


def set_signed_copy(db: Session, project_id: UUID | str, document_id: UUID | str) -> None:
    pid = str(project_id)
    ok = db.execute(text("SELECT 1 FROM documents WHERE id = :d AND project_id = :p"),
                    {"d": str(document_id), "p": pid}).first()
    if not ok:
        raise ContractError("That document isn't on this project.")
    c = _contract(db, pid)
    if not c:
        raise ContractError("This project has no contract.")
    db.execute(text("UPDATE contracts SET signed_copy_document_id = :d WHERE id = :c"),
               {"d": str(document_id), "c": str(c["id"])})


def amend(db: Session, project_id: UUID | str, uid: UUID | None) -> dict[str, Any]:
    """A new draft version copied from the signed one. The signed one stays."""
    pid = str(project_id)
    c = _contract(db, pid)
    if not c or c["status"] not in ("signed", "executed"):
        raise ContractError("Only a signed contract is amended; edit a draft directly.")
    if db.execute(text("""SELECT 1 FROM contracts WHERE project_id = :p AND status = 'draft'
                           AND parent_version_id = :c"""), {"p": pid, "c": str(c["id"])}).first():
        raise ContractError("An amendment draft already exists.")
    ver = (db.execute(text("SELECT COALESCE(MAX(version_number), 0) FROM contracts WHERE project_id = :p"),
                      {"p": pid}).scalar() or 0) + 1
    ref = f"{c['reference'] or 'CONTRACT'}-A{ver}"
    row = db.execute(text("""
        INSERT INTO contracts (id, project_id, version_number, reference, template_type, status, owner_name,
               owner_address, base_contract_value, option_contract_value, based_on_estimate_id,
               based_on_schedule_id, project_start_date, substantial_completion_date, final_completion_date,
               payload, notes, parent_version_id, created_by_id)
        SELECT gen_random_uuid(), project_id, :v, :r, template_type, 'draft', owner_name, owner_address,
               base_contract_value, option_contract_value, based_on_estimate_id, based_on_schedule_id,
               project_start_date, substantial_completion_date, final_completion_date, payload,
               COALESCE(notes, '') || :n, id, :u
          FROM contracts WHERE id = :c RETURNING id"""),
        {"v": ver, "r": ref, "n": f"\nAmendment of {c['reference']} (v{c['version_number']}).", "u": uid,
         "c": str(c["id"])}).scalar()
    return {"id": str(row), "version_number": ver, "reference": ref}
