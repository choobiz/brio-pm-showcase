"""Prepared documents: the request lifecycle, shared by both engines.

  request ─► plan (engine) ─► questions? ─► answers ─► draft (engine) ─► review
                                                       ▲                    │
                                                       └── revise (engine) ◄┘ correction
  review ─► finalize ─► branded PDFs in the project's Documents (office-only)

ENGINES. A queued stage is the Mac worker's to claim first (Claude Code on
Amir's subscription, no API cost). If the Mac has not been seen for
`MAC_FRESH`, or does not claim within `MAC_GRACE`, the API engine may take it,
under the monthly cap in `prep_spend`. Both engines get the same context
(`job_context`) and return the same JSON (`apply_result`), so the review page
cannot tell them apart except by the `engine` label and the cost.

Nothing here sends anything to anyone.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from decimal import Decimal

from app.models.prep import (
    CompanyPractice,
    DrawingFact,
    PreparedDocument,
    PrepRequest,
    ProjectGeometry,
    Supplier,
    WorkerHeartbeat,
)

MAC = "mac"
#: The Mac counts as available if it checked in this recently.
MAC_FRESH = timedelta(minutes=2)
#: How long a queued stage waits for the Mac before the API may take it.
MAC_GRACE = timedelta(seconds=90)
#: A stage running longer than this is assumed dead and re-queued for the API.
STUCK_AFTER = timedelta(minutes=30)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ── Engine output contract ───────────────────────────────────────────────

class Question(BaseModel):
    id: str
    question: str
    why: str = ""
    suggested: str = ""


class PlannedDoc(BaseModel):
    kind: str = "other"
    title: str
    supplier: str | None = None


class PlanResult(BaseModel):
    summary: str = ""
    documents: list[PlannedDoc] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    #: A proposed foundation geometry (forming_calc.Geometry shape), when the
    #: job needs one and the project has none confirmed.
    geometry: dict[str, Any] | None = None


class Row(BaseModel):
    item: str = ""
    spec: str = ""
    qty: str | float | int | None = None
    unit: str = ""
    basis: str = ""
    source: str = ""


class Section(BaseModel):
    heading: str
    #: "table" (rows of item/spec/qty/unit), "pairs" (label/value), or "text".
    kind: str = "table"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    text: str = ""


class DraftDoc(BaseModel):
    kind: str = "other"
    title: str
    supplier: str | None = None
    header: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    notes: str = ""


class DraftResult(BaseModel):
    documents: list[DraftDoc]
    conflicts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    drawing_facts: list[dict[str, Any]] = Field(default_factory=list)


# ── Dispatch ─────────────────────────────────────────────────────────────

def mac_seen(db: Session) -> datetime | None:
    hb = db.get(WorkerHeartbeat, MAC)
    return hb.last_seen if hb else None


def mac_available(db: Session, at: datetime | None = None) -> bool:
    seen = mac_seen(db)
    return seen is not None and (at or now()) - seen <= MAC_FRESH


def queue(db: Session, req: PrepRequest, stage: str, *, approve_usd: Decimal | float | None = None) -> None:
    """Queue a stage. `approve_usd` is what the person approved for THIS step
    on the server; the request's limit becomes what is spent so far plus it."""
    t = now()
    if approve_usd is not None:
        req.approved_usd = Decimal(str(req.cost_usd or 0)) + Decimal(str(approve_usd))
    req.stage = stage
    req.status = "queued"
    req.error = None
    req.claimed_at = None
    req.api_after = t + MAC_GRACE if mac_available(db, t) else t
    _progress(req, f"Queued: {stage}" + (" (waiting for the Mac)" if req.api_after > t else ""))
    db.flush()


def _progress(req: PrepRequest, msg: str) -> None:
    req.progress = [*(req.progress or []), {"at": now().isoformat(), "msg": msg}][-40:]


def heartbeat(db: Session, info: dict[str, Any] | None = None) -> None:
    hb = db.get(WorkerHeartbeat, MAC)
    if hb is None:
        db.add(WorkerHeartbeat(name=MAC, last_seen=now(), info=info or {}))
    else:
        hb.last_seen = now()
        hb.info = info or hb.info
    db.flush()


def claim(db: Session, engine: str) -> PrepRequest | None:
    """The oldest queued stage this engine may take. The Mac may take anything
    queued; the API only what is past its `api_after`."""
    # The current time is passed in: Postgres now() is the TRANSACTION's start.
    sql = ("SELECT id FROM prep_requests WHERE status = 'queued'"
           + ("" if engine == MAC else " AND api_after <= :t")
           + " ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED")
    rid = db.execute(_T(sql), {"t": now()} if engine != MAC else {}).scalar()
    if rid is None:
        return None
    req = db.get(PrepRequest, rid)
    req.status = "running"
    req.engine = engine
    req.claimed_at = now()
    _progress(req, f"{'Your Mac' if engine == MAC else 'The server'} started the {req.stage}")
    db.flush()
    return req


def reap(db: Session) -> int:
    """Running too long: the engine died. Re-queue straight for the API."""
    n = 0
    for req in db.query(PrepRequest).filter(PrepRequest.status == "running",
                                            PrepRequest.claimed_at < now() - STUCK_AFTER).all():
        _progress(req, f"No result from the {req.engine} after {int(STUCK_AFTER.total_seconds() // 60)} min; retrying")
        req.status, req.api_after, req.claimed_at = "queued", now(), None
        n += 1
    db.flush()
    return n


# ── Context both engines read ────────────────────────────────────────────

def job_context(db: Session, req: PrepRequest) -> dict[str, Any]:
    from app.company import COMPANY
    from app.services.home import today

    pid = str(req.project_id)
    p = db.execute(_T("SELECT name, address, type, status FROM projects WHERE id = :p"),
                   {"p": pid}).mappings().one()
    selected = {str(d) for d in req.document_ids or []}
    docs = [dict(r) for r in db.execute(_T("""
        SELECT d.id::text AS id, d.filename, d.category, d.mime, d.created_at::date AS added,
               (SELECT count(*) FROM document_text_pages t WHERE t.document_id = d.id) AS text_pages
          FROM documents d WHERE d.project_id = :p AND NOT d.archived
      ORDER BY d.created_at DESC LIMIT 400"""), {"p": pid}).mappings().all()]
    for d in docs:
        d["selected"] = d["id"] in selected
        d["added"] = str(d["added"])
    sid = db.execute(_T("SELECT id FROM schedules WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"),
                     {"p": pid}).scalar()
    schedule = [] if not sid else [
        {"code": r["activity_code"], "name": r["name"], "phase": r["phase"],
         "start": str(r["start_date"]) if r["start_date"] else None,
         "finish": str(r["finish_date"]) if r["finish_date"] else None}
        for r in db.execute(_T("SELECT activity_code, name, phase, start_date, finish_date FROM activities "
                               "WHERE schedule_id = :s ORDER BY start_date NULLS LAST"), {"s": sid}).mappings()]
    quantities = [dict(r) for r in db.execute(_T("""
        SELECT discipline, quantity_type, value::float AS value, unit, status, notes
          FROM quantity_overrides WHERE project_id = :p AND status IN ('confirmed', 'corrected')
      ORDER BY discipline, quantity_type"""), {"p": pid}).mappings().all()]
    # Opening sizes from the SIGNED-OFF takeoff, per sheet. Until 2026-10-15 only
    # the counts reached Prepare, so a door and window price request on ZZ asked
    # for sizes the takeoff already held (36x80 x2, 30x80, 48x36 x2).
    pack = db.execute(_T("SELECT payload FROM drawing_analysis_packs WHERE project_id = :p "
                         "AND status = 'approved' ORDER BY version_number DESC LIMIT 1"), {"p": pid}).scalar()
    takeoff_sizes = [{"sheet": sh.get("filename"), "sizes": sh["sizes"]}
                     for sh in ((pack or {}).get("sheets") or []) if isinstance(sh, dict) and sh.get("sizes")]
    facts = [{"document_id": str(f.document_id), "page_no": f.page_no, "facts": f.facts_md}
             for f in db.query(DrawingFact).filter(DrawingFact.document_id.in_(
                 [UUID(d["id"]) for d in docs])).all()] if docs else []
    from app.services import forming_calc

    confirmed = confirmed_geometry(db, req.project_id)
    calculated = forming_calc.for_project(db, req.project_id) if confirmed else None
    plan = req.plan or {}
    current = [{"title": d.title, "kind": d.kind, "payload": d.payload}
               for d in db.query(PreparedDocument).filter(PreparedDocument.prep_request_id == req.id)
               .order_by(PreparedDocument.position).all()]
    return {
        "stage": req.stage,
        "today": str(today()),
        "request": req.request,
        "answers": req.answers or {},
        "correction": req.correction if req.stage == "revise" else None,
        "plan": {k: plan.get(k) for k in ("summary", "documents", "questions")} if plan else None,
        "current_draft": current if req.stage == "revise" else None,
        "company": {"legal_name": COMPANY["legal_name"], "address": COMPANY["address"],
                    "phone": COMPANY["phone"], "email": COMPANY["email"],
                    "site_contact": f"Amir Omidvar, {COMPANY['name']} · {COMPANY['phone']}"},
        "project": {"id": pid, "name": p["name"], "address": p["address"], "type": p["type"], "status": p["status"]},
        "documents": docs,
        "checked_quantities": quantities,
        "signed_takeoff_sizes": ({"note": "Opening sizes (width x height, inches) counted from the CAD drawings "
                                          "in the signed-off takeoff. Use them; don't ask for them.",
                                  "sheets": takeoff_sizes} if takeoff_sizes else None),
        "schedule": schedule,
        "drawing_facts": facts,
        "confirmed_geometry": ({"version": confirmed.version, **confirmed.payload} if confirmed else None),
        "calculated": ({"note": "Quantities from BRIO's calculator and the confirmed geometry. Use them exactly, "
                                "with their `calc` key; never recount them.",
                        "lumber": calculated["lumber"], "wall_forms": calculated["wall_forms"],
                        "concrete": calculated["concrete"]} if calculated else None),
        "practices": [{"topic": c.topic, "rule": c.rule} for c in db.query(CompanyPractice)
                      .filter(CompanyPractice.active).order_by(CompanyPractice.topic).all()],
        "suppliers": [{"name": s.name, "trade": s.trade, "contact": s.contact_name, "email": s.email,
                       "phone": s.phone} for s in db.query(Supplier).filter(Supplier.active).order_by(Supplier.name)],
    }


# ── Applying an engine's result ──────────────────────────────────────────

def _supplier_id(db: Session, name: str | None) -> UUID | None:
    if not name:
        return None
    s = db.query(Supplier).filter(Supplier.name.ilike(name.strip())).first()
    return s.id if s else None


def apply_result(db: Session, req: PrepRequest, result: dict[str, Any], *, engine: str) -> None:
    """Validate and store an engine's output for the request's current stage.
    A malformed result fails the stage with the validation message rather than
    storing something half-shaped."""
    if req.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Request is {req.status}, not running.")
    try:
        if req.stage == "plan":
            plan = PlanResult.model_validate(result)
            needs_geometry = bool(plan.geometry) and confirmed_geometry(db, req.project_id) is None
            # Build the whole dict before assigning: a JSONB dict changed in
            # place after a flush is never saved (the flag was lost in prod).
            req.plan = {**plan.model_dump(), "geometry_to_confirm": needs_geometry}
            if needs_geometry:
                propose_geometry(db, req, plan.geometry)
            if plan.questions or needs_geometry:
                req.status = "waiting_user"
                _progress(req, f"{len(plan.questions)} question(s) before drafting"
                          + ("; the foundation geometry needs your confirmation" if needs_geometry else ""))
            else:
                _progress(req, "No questions; drafting")
                queue(db, req, "draft")
        else:
            draft = DraftResult.model_validate(result)
            enforce_calculated(db, req, draft)
            old = {d.position: d for d in db.query(PreparedDocument)
                   .filter(PreparedDocument.prep_request_id == req.id).all()}
            for d in old.values():
                db.delete(d)
            db.flush()
            for i, d in enumerate(draft.documents):
                prev = old.get(i)
                db.add(PreparedDocument(
                    prep_request_id=req.id, project_id=req.project_id, position=i, kind=d.kind,
                    title=d.title, supplier_id=_supplier_id(db, d.supplier),
                    payload={"supplier": d.supplier, "header": d.header,
                             "sections": [s.model_dump() for s in d.sections], "notes": d.notes},
                    version=(prev.version + 1) if prev else 1))
            req.plan = {**(req.plan or {}), "conflicts": draft.conflicts, "assumptions": draft.assumptions}
            for f in draft.drawing_facts:
                # A draft's own summary must never replace a full read.
                save_fact(db, f, engine=engine, overwrite=False)
            req.status = "waiting_user"
            req.correction = None
            _progress(req, f"Draft ready: {len(draft.documents)} document(s)")
    except ValidationError as e:
        req.status = "failed"
        req.error = f"The {engine} returned a result in the wrong shape: {e.errors()[0]['msg']}"
        _progress(req, req.error)
    db.flush()


def save_fact(db: Session, f: dict[str, Any], *, engine: str, model: str | None = None,
              overwrite: bool = True) -> None:
    """Keep what a drawing page says. `overwrite=False` only fills a gap: on
    1234 (2026-10-13) a draft's short summary replaced a full tiled read, and
    the next job would have worked from the summary."""
    try:
        doc_id, page = UUID(str(f["document_id"])), int(f.get("page_no") or 1)
        text = str(f.get("facts") or f.get("facts_md") or "").strip()
    except (KeyError, ValueError, TypeError):
        return
    if not text:
        return
    row = db.query(DrawingFact).filter(DrawingFact.document_id == doc_id, DrawingFact.page_no == page).first()
    if row and not overwrite:
        return
    if row:
        row.facts_md, row.engine, row.model = text, engine, model
    else:
        db.add(DrawingFact(document_id=doc_id, page_no=page, facts_md=text, engine=engine, model=model))
    db.flush()


def fail(db: Session, req: PrepRequest, message: str) -> None:
    req.status = "failed"
    req.error = message
    _progress(req, message)
    db.flush()


# ── Person-side actions ──────────────────────────────────────────────────

def create(db: Session, project_id: UUID, user_id: Any, request: str, document_ids: list[UUID],
           *, approve_usd: Decimal | float | None = None) -> PrepRequest:
    if document_ids:
        found = {r[0] for r in db.execute(_T(
            "SELECT id FROM documents WHERE project_id = :p AND id = ANY(:ids)"),
            {"p": str(project_id), "ids": [str(d) for d in document_ids]}).all()}
        if len(found) != len(set(document_ids)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="A selected document is not in this project.")
    try:
        uid = UUID(str(user_id))
    except (TypeError, ValueError):
        uid = None
    req = PrepRequest(project_id=project_id, requested_by_id=uid, request=request.strip(),
                      document_ids=[str(d) for d in document_ids], progress=[])
    db.add(req)
    db.flush()
    queue(db, req, "plan", approve_usd=approve_usd)
    return req


def answer(db: Session, req: PrepRequest, answers: dict[str, str], *, approve_usd: Decimal | float | None = None) -> None:
    if req.status != "waiting_user" or req.stage != "plan":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="There are no questions waiting on this request.")
    if (req.plan or {}).get("geometry_to_confirm") and confirmed_geometry(db, req.project_id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Confirm the foundation geometry first: every quantity is counted from it.")
    req.answers = {k: str(v)[:2000] for k, v in answers.items()}
    queue(db, req, "draft", approve_usd=approve_usd)


def revise(db: Session, req: PrepRequest, correction: str, *, remember: bool, user_id: Any,
           approve_usd: Decimal | float | None = None) -> None:
    if req.status != "waiting_user" or req.stage == "plan":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="There is no draft to correct yet.")
    req.correction = correction.strip()[:4000]
    if remember:
        try:
            uid = UUID(str(user_id))
        except (TypeError, ValueError):
            uid = None
        db.add(CompanyPractice(topic="general", rule=req.correction, created_by_id=uid,
                               source=f"Correction on a prepared document, {now().date().isoformat()}"))
    queue(db, req, "revise", approve_usd=approve_usd)


def edit_document(db: Session, doc: PreparedDocument, payload: dict[str, Any], title: str | None) -> None:
    if doc.status == "final":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This document is final. Start a new request to change it.")
    DraftDoc.model_validate({"title": title or doc.title, **payload})
    doc.payload = {**doc.payload, **{k: payload[k] for k in ("header", "sections", "notes", "supplier") if k in payload}}
    if title:
        doc.title = title
    doc.version += 1
    db.flush()


def prepared_filename(project_name: str, title: str) -> str:
    """'<project> - <title>.pdf', without the project twice when the title
    already names it ("1234 Example Way - Forming Lumber Order - 1234
    Example Way Footings.pdf", rollout test 2026-10-14)."""
    short = (project_name or "").split(" — ")[0][:60].strip()
    if not short or short.lower() in title.lower():
        return f"{title}.pdf"
    return f"{short} - {title}.pdf"


def finalize(db: Session, req: PrepRequest, user_id: Any) -> list[PreparedDocument]:
    """Render every draft document to a BRIO PDF and file it in the project's
    Documents, office-only. Sends nothing."""
    from app.models.document import Document
    from app.services.prep_pdf import render
    from app.services.r2 import put_bytes

    if req.status != "waiting_user" or req.stage == "plan":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="There is no draft ready to finalise.")
    project = db.execute(_T("SELECT name, address FROM projects WHERE id = :p"),
                         {"p": str(req.project_id)}).mappings().one()
    try:
        uid = UUID(str(user_id))
    except (TypeError, ValueError):
        uid = None
    docs = db.query(PreparedDocument).filter(PreparedDocument.prep_request_id == req.id) \
        .order_by(PreparedDocument.position).all()
    if not docs:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This request has no documents.")
    for d in docs:
        pdf = render(d.title, d.payload)
        safe = "".join(ch for ch in d.title if ch.isalnum() or ch in " -_().'&").strip()[:120] or "Document"
        filename = prepared_filename(project["name"], safe)
        key = f"prepared/{req.id}/{d.id}-v{d.version}.pdf"
        put_bytes(key, pdf, "application/pdf")
        doc = Document(project_id=req.project_id, category="prepared", phase="in_progress",
                       filename=filename, r2_key=key, mime="application/pdf", size_bytes=len(pdf),
                       uploaded_by_id=uid, client_visible=False)
        db.add(doc)
        db.flush()
        d.document_id = doc.id
        d.status = "final"
    req.status = "finalized"
    _progress(req, f"Finalised: {len(docs)} PDF(s) filed in Documents")
    db.flush()
    return docs


def to_dict(db: Session, req: PrepRequest) -> dict[str, Any]:
    docs = db.query(PreparedDocument).filter(PreparedDocument.prep_request_id == req.id) \
        .order_by(PreparedDocument.position).all()
    return {
        "id": str(req.id), "project_id": str(req.project_id), "request": req.request,
        "document_ids": req.document_ids, "status": req.status, "stage": req.stage, "engine": req.engine,
        "plan": req.plan, "answers": req.answers, "progress": req.progress, "error": req.error,
        "cost_usd": float(req.cost_usd or 0), "approved_usd": float(req.approved_usd or 0),
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "geometry": geometry_dict(db, req.project_id),
        "mac_available": mac_available(db),
        "documents": [{"id": str(d.id), "kind": d.kind, "title": d.title, "payload": d.payload,
                       "version": d.version, "status": d.status,
                       "document_id": str(d.document_id) if d.document_id else None} for d in docs],
    }


# ── Geometry: proposed by the AI, confirmed by a person ──────────────────

def confirmed_geometry(db: Session, project_id: Any) -> ProjectGeometry | None:
    return (db.query(ProjectGeometry)
            .filter(ProjectGeometry.project_id == project_id, ProjectGeometry.status == "confirmed")
            .order_by(ProjectGeometry.version.desc()).first())


def latest_geometry(db: Session, project_id: Any) -> ProjectGeometry | None:
    return (db.query(ProjectGeometry).filter(ProjectGeometry.project_id == project_id)
            .order_by(ProjectGeometry.version.desc()).first())


def _next_version(db: Session, project_id: Any) -> int:
    g = latest_geometry(db, project_id)
    return (g.version + 1) if g else 1


def propose_geometry(db: Session, req: PrepRequest, payload: dict[str, Any]) -> ProjectGeometry:
    """Store the AI's proposal as-is (even if it fails validation: the person
    fixes it on the confirm form, which validates)."""
    g = ProjectGeometry(project_id=req.project_id, version=_next_version(db, req.project_id), status="proposed",
                        payload=payload, source=f"Proposed from the documents for: {req.request[:200]}",
                        prep_request_id=req.id)
    db.add(g)
    db.flush()
    return g


def confirm_geometry(db: Session, project_id: Any, payload: dict[str, Any], user_id: Any) -> ProjectGeometry:
    from app.services.forming_calc import Geometry

    try:
        clean = Geometry.model_validate(payload).model_dump()
    except ValidationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.errors()[0]["msg"]) from None
    try:
        uid = UUID(str(user_id))
    except (TypeError, ValueError):
        uid = None
    g = ProjectGeometry(project_id=project_id, version=_next_version(db, project_id), status="confirmed",
                        payload=clean, source="Confirmed by a person", confirmed_by_id=uid, confirmed_at=now())
    db.add(g)
    db.flush()
    return g


def geometry_dict(db: Session, project_id: Any) -> dict[str, Any] | None:
    from app.services import forming_calc

    g = latest_geometry(db, project_id)
    if g is None:
        return None
    c = confirmed_geometry(db, project_id)
    calc = forming_calc.for_project(db, project_id) if c else None
    return {"version": g.version, "status": g.status, "payload": g.payload,
            "confirmed_version": c.version if c else None,
            "confirmed_at": c.confirmed_at.isoformat() if c and c.confirmed_at else None,
            "calculated": ({k: calc[k] for k in ("perimeter_ft", "form_line_lf", "lumber", "wall_forms", "concrete")}
                           if calc else None)}


def enforce_calculated(db: Session, req: PrepRequest, draft: DraftResult) -> None:
    """Calculated quantities are not the model's to change. A row carrying a
    `calc` key gets the calculator's numbers back; a calculated lumber item
    left out is reported; any other numeric row in an order or takeoff is
    marked as estimated, so the reader knows which numbers to check."""
    from app.services import forming_calc

    calc = forming_calc.for_project(db, req.project_id)
    rows = calc["rows"] if calc else {}
    used: set[str] = set()
    changed: list[str] = []
    for d in draft.documents:
        for s in d.sections:
            for r in s.rows:
                key = r.get("calc")
                if key and key in rows:
                    used.add(key)
                    want = rows[key]
                    for f in ("qty", "unit", "value"):
                        if f in want and str(r.get(f)) != str(want[f]):
                            changed.append(f"{key}: {r.get(f)} -> {want[f]}")
                            r[f] = want[f]
                    r["basis"], r["source"] = want.get("basis", ""), want.get("source", "calculator")
                elif (s.kind == "table" and d.kind in ("order", "takeoff", "price_request")
                      and r.get("qty") not in (None, "") and not str(r.get("basis", "")).startswith("Estimated")):
                    r["basis"] = "Estimated (not calculated): " + str(r.get("basis", ""))
    if changed:
        draft.conflicts.append("Calculated quantities were restored where the draft differed: " + "; ".join(changed))
    is_forming = any("form" in (d.title or "").lower() for d in draft.documents)
    missing = [rows[k]["item"] + " " + rows[k]["spec"] for k in rows
               if k not in used and rows[k].get("item") and calc and rows[k] in calc["lumber"]]
    if is_forming and missing:
        draft.conflicts.append("Calculated lumber not on any document: " + ", ".join(missing))
