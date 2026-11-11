"""Prepared documents: the office-side routes, the budget, suppliers and
practices, and the Mac worker's routes (shared-secret auth).

See app.services.prep for the lifecycle."""
from __future__ import annotations

import hmac
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.config import settings
from app.db import get_db
from app.models.prep import CompanyPractice, PreparedDocument, PrepRequest, Supplier
from app.services import prep as P
from app.services import prep_spend as S
from app.services import prep_tools as T

router = APIRouter(tags=["prep"])


def _req(db: Session, user: CurrentUser, project_id: UUID, rid: UUID) -> PrepRequest:
    assert_project_access(db, user, project_id)
    req = db.get(PrepRequest, rid)
    if req is None or req.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="request not found")
    return req


# ── Office ───────────────────────────────────────────────────────────────

#: What the person approved for the step on the server (the Mac is free).
#: Required on every action that starts a step (Amir 2026-10-13).
Approve = Field(ge=0, le=50, description="USD approved for this step if the server runs it; 0 = Mac only")


class CreateBody(BaseModel):
    request: str = Field(min_length=3, max_length=4000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=40)
    approve_usd: float = Approve


@router.get("/projects/{project_id}/prep/estimate")
def estimate_new(project_id: UUID, document_ids: str = Query(""), db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    from app.services import cost_estimate

    assert_project_access(db, user, project_id)
    ids = [d for d in document_ids.split(",") if d.strip()]
    return cost_estimate.prep(db, project_id, "plan", ids)


@router.get("/projects/{project_id}/prep/{rid}/estimate")
def estimate_step(project_id: UUID, rid: UUID, stage: str = Query(pattern="^(plan|draft|revise)$"),
                  db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    from app.services import cost_estimate

    req = _req(db, user, project_id, rid)
    return cost_estimate.prep(db, project_id, stage, [str(d) for d in req.document_ids or []])


@router.get("/projects/{project_id}/geometry")
def read_geometry(project_id: UUID, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_office)) -> dict[str, Any] | None:
    assert_project_access(db, user, project_id)
    return P.geometry_dict(db, project_id)


class GeometryBody(BaseModel):
    payload: dict[str, Any]


@router.put("/projects/{project_id}/geometry")
def confirm_geometry(project_id: UUID, body: GeometryBody, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_office)) -> dict[str, Any] | None:
    """Confirm (a new version of) the foundation geometry. Every forming
    quantity is counted from the latest confirmed version."""
    assert_project_access(db, user, project_id)
    P.confirm_geometry(db, project_id, body.payload, user.id)
    db.commit()
    return P.geometry_dict(db, project_id)


@router.get("/projects/{project_id}/prep")
def list_requests(project_id: UUID, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    rows = db.query(PrepRequest).filter(PrepRequest.project_id == project_id) \
        .order_by(PrepRequest.created_at.desc()).limit(50).all()
    return [{"id": str(r.id), "request": r.request, "status": r.status, "stage": r.stage,
             "engine": r.engine, "cost_usd": float(r.cost_usd or 0),
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


@router.post("/projects/{project_id}/prep", status_code=201)
def create_request(project_id: UUID, body: CreateBody, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    req = P.create(db, project_id, user.id, body.request, body.document_ids, approve_usd=body.approve_usd)
    db.commit()
    return P.to_dict(db, req)


@router.get("/projects/{project_id}/prep/{rid}")
def read_request(project_id: UUID, rid: UUID, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    return P.to_dict(db, _req(db, user, project_id, rid))


class AnswersBody(BaseModel):
    answers: dict[str, str]
    approve_usd: float = Approve


@router.post("/projects/{project_id}/prep/{rid}/answers")
def answer(project_id: UUID, rid: UUID, body: AnswersBody, db: Session = Depends(get_db),
           user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    req = _req(db, user, project_id, rid)
    P.answer(db, req, body.answers, approve_usd=body.approve_usd)
    db.commit()
    return P.to_dict(db, req)


class ReviseBody(BaseModel):
    correction: str = Field(min_length=2, max_length=4000)
    remember: bool = False
    approve_usd: float = Approve


@router.post("/projects/{project_id}/prep/{rid}/revise")
def revise(project_id: UUID, rid: UUID, body: ReviseBody, db: Session = Depends(get_db),
           user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    req = _req(db, user, project_id, rid)
    P.revise(db, req, body.correction, remember=body.remember, user_id=user.id, approve_usd=body.approve_usd)
    db.commit()
    return P.to_dict(db, req)


class EditBody(BaseModel):
    title: str | None = None
    header: list[dict[str, Any]] | None = None
    sections: list[dict[str, Any]] | None = None
    notes: str | None = None
    supplier: str | None = None


@router.patch("/projects/{project_id}/prep/{rid}/documents/{did}")
def edit_document(project_id: UUID, rid: UUID, did: UUID, body: EditBody, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    req = _req(db, user, project_id, rid)
    doc = db.get(PreparedDocument, did)
    if doc is None or doc.prep_request_id != req.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    P.edit_document(db, doc, body.model_dump(exclude_none=True, exclude={"title"}), body.title)
    db.commit()
    return P.to_dict(db, req)


@router.get("/projects/{project_id}/prep/{rid}/documents/{did}/preview")
def preview(project_id: UUID, rid: UUID, did: UUID, db: Session = Depends(get_db),
            user: CurrentUser = Depends(require_office)) -> Response:
    from app.services.prep_pdf import render

    req = _req(db, user, project_id, rid)
    doc = db.get(PreparedDocument, did)
    if doc is None or doc.prep_request_id != req.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found")
    return Response(render(doc.title, doc.payload), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc.title[:80]}.pdf"'})


@router.post("/projects/{project_id}/prep/{rid}/finalize")
def finalize(project_id: UUID, rid: UUID, db: Session = Depends(get_db),
             user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    req = _req(db, user, project_id, rid)
    P.finalize(db, req, user.id)
    db.commit()
    return P.to_dict(db, req)


class RetryBody(BaseModel):
    approve_usd: float = Approve


@router.post("/projects/{project_id}/prep/{rid}/retry")
def retry(project_id: UUID, rid: UUID, body: RetryBody, db: Session = Depends(get_db),
          user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Run a failed stage again (Mac first, as always), with a fresh approval."""
    req = _req(db, user, project_id, rid)
    if req.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only a failed request can be retried.")
    P.queue(db, req, req.stage, approve_usd=body.approve_usd)
    db.commit()
    return P.to_dict(db, req)


@router.post("/projects/{project_id}/prep/{rid}/cancel")
def cancel(project_id: UUID, rid: UUID, db: Session = Depends(get_db),
           user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    req = _req(db, user, project_id, rid)
    if req.status == "finalized":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already finalised.")
    req.status = "cancelled"
    db.commit()
    return P.to_dict(db, req)


# ── Budget ───────────────────────────────────────────────────────────────

@router.get("/prep/status")
def prep_status(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    seen = P.mac_seen(db)
    return {**S.summary(db, user), "mac_available": P.mac_available(db),
            "mac_last_seen": seen.isoformat() if seen else None}


class CapBody(BaseModel):
    usd: Decimal


@router.put("/prep/cap")
def set_cap(body: CapBody, db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    S.set_cap(db, user, body.usd)
    db.commit()
    return S.summary(db, user)


@router.get("/prep/forming-params")
def get_forming_params(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    from app.services.forming_calc import params

    return params(db)


@router.put("/prep/forming-params")
def put_forming_params(body: dict[str, Any], db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """BRIO's forming numbers (stake spacing, spare %, stock lengths, nails)."""
    from datetime import datetime, timezone

    from app.models.prep import AppSetting
    from app.services.forming_calc import DEFAULT_PARAMS, params

    unknown = set(body) - set(DEFAULT_PARAMS)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown setting(s): {sorted(unknown)}")
    for k, v in body.items():
        if k != "nails" and (not isinstance(v, (int, float)) or v <= 0 or v > 1000):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{k} must be a positive number")
    row = db.get(AppSetting, "forming_params") or AppSetting(key="forming_params", value={})
    row.value = {**params(db), **body}
    row.updated_at = datetime.now(timezone.utc)
    db.merge(row)
    db.commit()
    return params(db)


# ── Suppliers and practices ──────────────────────────────────────────────

class SupplierBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trade: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    active: bool = True


def _supplier(s: Supplier) -> dict[str, Any]:
    return {"id": str(s.id), "name": s.name, "trade": s.trade, "contact_name": s.contact_name,
            "email": s.email, "phone": s.phone, "address": s.address, "notes": s.notes, "active": s.active}


@router.get("/suppliers")
def list_suppliers(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    return [_supplier(s) for s in db.query(Supplier).order_by(Supplier.active.desc(), Supplier.name).all()]


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierBody, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    s = Supplier(**body.model_dump())
    db.add(s)
    db.commit()
    return _supplier(s)


@router.put("/suppliers/{sid}")
def update_supplier(sid: UUID, body: SupplierBody, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    s = db.get(Supplier, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="supplier not found")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return _supplier(s)


class PracticeBody(BaseModel):
    topic: str = Field(min_length=1, max_length=80)
    rule: str = Field(min_length=3, max_length=4000)
    active: bool = True


def _practice(c: CompanyPractice) -> dict[str, Any]:
    return {"id": str(c.id), "topic": c.topic, "rule": c.rule, "source": c.source, "active": c.active,
            "created_at": c.created_at.isoformat() if c.created_at else None}


@router.get("/practices")
def list_practices(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    return [_practice(c) for c in db.query(CompanyPractice)
            .order_by(CompanyPractice.active.desc(), CompanyPractice.topic, CompanyPractice.created_at).all()]


@router.post("/practices", status_code=201)
def create_practice(body: PracticeBody, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    try:
        uid = UUID(str(user.id))
    except (TypeError, ValueError):
        uid = None
    c = CompanyPractice(**body.model_dump(), created_by_id=uid, source="Added by hand")
    db.add(c)
    db.commit()
    return _practice(c)


@router.put("/practices/{cid}")
def update_practice(cid: UUID, body: PracticeBody, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    c = db.get(CompanyPractice, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="practice not found")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.commit()
    return _practice(c)


# ── Mac worker (shared secret) ───────────────────────────────────────────

def require_worker(x_prep_worker_token: str | None = Header(default=None)) -> None:
    expected = settings.prep_worker_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="worker access is not configured")
    if not x_prep_worker_token or not hmac.compare_digest(x_prep_worker_token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="bad worker token")


worker = APIRouter(prefix="/prep/worker", tags=["prep-worker"], dependencies=[Depends(require_worker)])


def _running(db: Session, rid: UUID) -> PrepRequest:
    req = db.get(PrepRequest, rid)
    if req is None or req.status != "running" or req.engine != P.MAC:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not a request the Mac is running")
    return req


class ClaimBody(BaseModel):
    info: dict[str, Any] = Field(default_factory=dict)


@worker.post("/claim")
def worker_claim(body: ClaimBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Heartbeat, then the next queued stage if there is one."""
    P.heartbeat(db, body.info)
    req = P.claim(db, P.MAC)
    from app.services.prep_prompt import instructions

    out = ({"request_id": str(req.id), "stage": req.stage, "instructions": instructions(req.stage),
            "context": P.job_context(db, req)} if req else {})
    db.commit()
    return out


@worker.post("/heartbeat")
def worker_heartbeat(body: ClaimBody, db: Session = Depends(get_db)) -> dict[str, bool]:
    """While the Mac is busy on a stage it keeps checking in, so new requests
    still wait for it rather than going to the API."""
    P.heartbeat(db, body.info)
    db.commit()
    return {"ok": True}


@worker.get("/{rid}/documents/{document_id}/text")
def worker_text(rid: UUID, document_id: str, pages: str | None = Query(None), db: Session = Depends(get_db)) -> dict[str, Any]:
    req = _running(db, rid)
    pg = [int(p) for p in pages.split(",") if p.strip().isdigit()] if pages else None
    return T.document_text(db, req.project_id, document_id, pg)


@worker.get("/{rid}/documents/{document_id}/download")
def worker_download(rid: UUID, document_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.r2 import presign_get

    req = _running(db, rid)
    d = T._doc(db, req.project_id, document_id)
    return {"filename": d["filename"], "mime": d["mime"], "url": presign_get(d["r2_key"], expires=600)}


@worker.get("/{rid}/search")
def worker_search(rid: UUID, q: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    req = _running(db, rid)
    return T.search(db, req.project_id, q)


class ProgressBody(BaseModel):
    msg: str = Field(max_length=300)


@worker.post("/{rid}/progress")
def worker_progress(rid: UUID, body: ProgressBody, db: Session = Depends(get_db)) -> dict[str, bool]:
    req = _running(db, rid)
    P._progress(req, body.msg)
    P.heartbeat(db)
    db.commit()
    return {"ok": True}


class FactBody(BaseModel):
    document_id: UUID
    page_no: int = 1
    facts: str = Field(min_length=1, max_length=20000)


@worker.post("/{rid}/facts")
def worker_fact(rid: UUID, body: FactBody, db: Session = Depends(get_db)) -> dict[str, bool]:
    req = _running(db, rid)
    T._doc(db, req.project_id, str(body.document_id))
    P.save_fact(db, body.model_dump(), engine=P.MAC, model="claude-code")
    db.commit()
    return {"ok": True}


class ResultBody(BaseModel):
    result: dict[str, Any]


@worker.post("/{rid}/result")
def worker_result(rid: UUID, body: ResultBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    req = _running(db, rid)
    P.apply_result(db, req, body.result, engine=P.MAC)
    db.commit()
    return {"status": req.status, "error": req.error}


class FailBody(BaseModel):
    error: str = Field(max_length=2000)
    retry_on_api: bool = True


@worker.post("/{rid}/fail")
def worker_fail(rid: UUID, body: FailBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The Mac could not finish. By default the stage goes to the API engine
    (under the cap) rather than failing outright."""
    req = _running(db, rid)
    if body.retry_on_api:
        P._progress(req, f"The Mac couldn't finish ({body.error[:160]}); handing to the server")
        req.status, req.api_after, req.claimed_at = "queued", P.now(), None
    else:
        P.fail(db, req, body.error)
    db.commit()
    return {"status": req.status}
