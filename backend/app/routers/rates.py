"""Rate-library API — Track 2 Phase 6 (Cost Intelligence surface).

Read + deterministic pricing:
  - GET  /rates/assemblies                 best current rate per assembly
  - GET  /rates/assemblies/{code}          assembly + full entry history
  - POST /rates/price                      price {assembly_code, quantity, project_type}
  - GET  /rates/materials                  material rates (supplier + market)
  - GET  /rates/labour                     labour rates (own + fallback)
  - GET  /rates/catalogue                  supplier catalogue search (read-only)

Authoring (feeds the library as Amir submits rates / jobs close):
  - POST /rates/assemblies                 add an assembly to the catalog
  - POST /rates/entries                    add a rate entry (supersedes prior active)
  - POST /rates/materials                  add a material rate
  - POST /rates/labour                     add a labour rate (effective auto-computed)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_office, CurrentUser, get_current_user
from app.db import get_db
from app.models.rate_library import (
    LabourRate,
    MaterialRate,
    RateAssembly,
    RateEntry,
)
from app.services import rate_pricing


# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/rates", tags=["rates"], dependencies=[Depends(require_office)])


# ── Read / price ───────────────────────────────────────────────────


class PriceRequest(BaseModel):
    assembly_code: str
    quantity: float
    project_type: str = "any"


@router.get("/assemblies")
def list_assemblies(
    project_type: str = Query("any"),
    status_: str = Query("active", alias="status", pattern="^(active|draft|held|retired)$"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Best current rate for every assembly in a status (active by default;
    draft/held = the starter library awaiting review), resolved for the
    requested project type, with how far its price has likely drifted."""
    from app.services.price_drift import IndexTable

    idx = IndexTable.load(db)
    rows = rate_pricing.list_best_rates(db, project_type=project_type, status=status_)
    for r in rows:
        as_of = (r.get("provenance") or {}).get("as_of_date")
        r["drift"] = idx.drift(date.fromisoformat(as_of) if as_of else None, r.get("trade_key"))
    return rows


@router.get("/assemblies/{code}")
def assembly_detail(
    code: str,
    project_type: str = Query("any"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Assembly + its full entry history (all sources, active + superseded),
    plus the resolved best entry for the requested project type."""
    assembly = db.scalar(select(RateAssembly).where(RateAssembly.code == code))
    if assembly is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No assembly '{code}'")
    entries = list(
        db.scalars(
            select(RateEntry)
            .where(RateEntry.assembly_id == assembly.id)
            .order_by(RateEntry.is_active.desc(), RateEntry.created_at.desc())
        )
    )
    best = rate_pricing.resolve_best_entry(db, assembly_code=code, project_type=project_type)
    from sqlalchemy import text as _t

    # Estimates whose lines name this rate (phase 3 links). A text match on the
    # stored JSON, then confirmed per line, so a code inside a name never counts.
    used_on: int | None = 0
    try:
        with db.begin_nested():
            found = db.execute(_t("SELECT payload FROM estimate_versions WHERE payload::text LIKE :p"),
                               {"p": f'%"{code}"%'}).all()
    except Exception:  # noqa: BLE001  (a count is not worth failing the page)
        found, used_on = [], None
    for (payload,) in found:
        if any(((it.get("rate_link") or {}).get("rate") or {}).get("code") == code
               for c in (payload or {}).get("categories") or [] for it in (c.get("items") or [])
               if isinstance(it, dict)):
            used_on += 1
    return {
        "used_on_estimates": used_on,
        "code": assembly.code,
        "name": assembly.name,
        "trade": assembly.trade,
        **_vocab_fields(assembly.trade_key),
        "category": assembly.category,
        "unit": assembly.unit,
        "project_type": assembly.project_type,
        "scope_notes": assembly.scope_notes,
        "status": assembly.status,
        "best_entry_id": str(best.id) if best else None,
        "entries": [
            {
                "id": str(e.id),
                "unit_rate": float(e.unit_rate),
                "unit": e.unit,
                "source_type": e.source_type,
                "confidence": e.confidence,
                "project_type": e.project_type,
                "source_ref": e.source_ref,
                "as_of_date": e.as_of_date.isoformat() if e.as_of_date else None,
                "is_active": e.is_active,
                "payload": e.payload,
                "created_by": e.created_by,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


@router.post("/price")
def price_assembly(
    body: PriceRequest,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Deterministically price one assembly. 200 with priced=false when no
    rate exists (so callers can branch without catching a 404)."""
    result = rate_pricing.price(
        db,
        assembly_code=body.assembly_code,
        quantity=body.quantity,
        project_type=body.project_type,
    )
    if result is None:
        return {"priced": False, "assembly_code": body.assembly_code}
    return {"priced": True, **result}


def _vocab_fields(trade_key: str | None) -> dict[str, Any]:
    """The shared trade and the residential division it prices under."""
    from app.services.library_vocab import division_for, trade

    return {"trade_key": trade_key,
            "trade_label": trade(trade_key).label if trade_key else None,
            "division": division_for(trade_key)}


def _per_measure(m: MaterialRate) -> dict[str, Any] | None:
    from app.services.library_vocab import UnknownUnit, cost_per_measure

    try:
        r = cost_per_measure(m.unit_cost, m.unit, measure_unit=m.measure_unit,
                             coverage=m.coverage, waste_pct=m.waste_pct)
    except UnknownUnit:
        return None
    return {"usd": float(round(r[0], 4)), "unit": r[1]} if r else None


@router.get("/materials")
def list_materials(
    trade: str | None = Query(None),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    stmt = select(MaterialRate).where(MaterialRate.is_active.is_(True))
    if trade:
        stmt = stmt.where((MaterialRate.trade == trade) | (MaterialRate.trade_key == trade))
    stmt = stmt.order_by(MaterialRate.trade, MaterialRate.name)
    from app.services.price_drift import IndexTable

    idx = IndexTable.load(db)
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "sku": m.sku,
            "trade": m.trade,
            **_vocab_fields(m.trade_key),
            "category": m.category,
            "unit": m.unit,
            "unit_cost": float(m.unit_cost),
            "measure_unit": m.measure_unit,
            "coverage": float(m.coverage) if m.coverage is not None else None,
            "cost_per_measure": _per_measure(m),
            "source": m.source,
            "supplier": m.supplier,
            "project_type": m.project_type,
            "waste_pct": float(m.waste_pct) if m.waste_pct is not None else None,
            "as_of_date": m.as_of_date.isoformat() if m.as_of_date else None,
            "source_ref": m.source_ref,
            # unit_cost is the MID band; admin needs the low/high to choose a
            # band before an estimate goes to a client, so it has to reach the UI.
            # Absent on the framing/drywall rows, which came from a yard survey
            # rather than a banded reference — hence the null-safe read.
            "band": (m.payload or {}).get("band"),
            "drift": idx.drift(m.as_of_date, m.trade_key),
            "payload": m.payload or {},
        }
        for m in db.scalars(stmt)
    ]


class WritebackPreview(BaseModel):
    #: The estimate document about to be saved (the editor's payload) ...
    document: dict[str, Any] | None = None
    #: ... or an AI edit about to be applied, with the changes the reviewer kept.
    ai_edit_job_id: UUID | None = None
    accept_paths: list[str] | None = None


@router.post("/writeback/preview")
def writeback_preview(
    body: WritebackPreview,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The library changes a save would make: each linked line whose price was
    fixed, and each priced allowance that could become a rate. Read-only."""
    from app.services.rate_writeback import proposals

    doc = body.document
    if body.ai_edit_job_id is not None:
        from app.routers.ai_edits import planned_document

        doc = planned_document(db, user, body.ai_edit_job_id, body.accept_paths)
    if not isinstance(doc, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "document or ai_edit_job_id is required")
    return {"proposals": proposals(doc)}


@router.get("/catalogue")
def search_catalogue(
    q: str | None = Query(None, max_length=200),
    trade: str | None = Query(None),
    supplier: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The supplier catalogue: read-only, searchable, never in an estimate
    prompt. Every word of `q` must appear in the description or SKU."""
    from sqlalchemy import func, or_

    from app.models.catalogue import CatalogueItem
    from app.services.price_drift import IndexTable

    stmt = select(CatalogueItem)
    for word in (q or "").split():
        like = f"%{word}%"
        stmt = stmt.where(or_(CatalogueItem.description.ilike(like), CatalogueItem.sku.ilike(like)))
    if trade:
        stmt = stmt.where(CatalogueItem.trade_key == trade)
    if supplier:
        stmt = stmt.where(CatalogueItem.supplier == supplier)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    idx = IndexTable.load(db)
    rows = db.scalars(stmt.order_by(CatalogueItem.description).limit(limit).offset(offset))
    return {"total": total, "items": [
        {
            "id": str(c.id), "supplier": c.supplier, "sku": c.sku, "description": c.description,
            "supplier_group": c.supplier_group, "supplier_category": c.supplier_category,
            "raw_unit": c.raw_unit, "raw_price": float(c.raw_price),
            "unit": c.unit, "unit_cost": float(c.unit_cost) if c.unit_cost is not None else None,
            **_vocab_fields(c.trade_key),
            "as_of_date": c.as_of_date.isoformat() if c.as_of_date else None,
            "drift": idx.drift(c.as_of_date, c.trade_key),
        }
        for c in rows
    ]}


# ── Supplier price-sheet import (phase 4) ───────────────────────────


def _import_out(imp, rows: bool = False) -> dict[str, Any]:
    out = {
        "id": str(imp.id), "supplier": imp.supplier, "filename": imp.filename, "status": imp.status,
        "column_map": imp.column_map, "summary": imp.summary, "created_by": imp.created_by,
        "created_at": imp.created_at.isoformat() if imp.created_at else None,
        "applied_by": imp.applied_by, "applied_at": imp.applied_at.isoformat() if imp.applied_at else None,
        "undone_by": imp.undone_by, "undone_at": imp.undone_at.isoformat() if imp.undone_at else None,
    }
    if rows:
        out["rows"] = imp.rows
    return out


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def stage_import(
    file: UploadFile = File(...),
    supplier: str = Form(..., min_length=1, max_length=120),
    column_map: str | None = Form(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Read a price sheet and check every row. Writes only the staged import:
    the catalogue and library change on apply. Without a column map, the
    supplier's last confirmed one is used, else a guess from the headers."""
    import json as _json

    from app.models.prep import AppSetting
    from app.services import catalogue_import as CI

    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "price sheets over 20 MB are refused")
    try:
        headers, rows = CI.read_sheet(file.filename or "", data)
    except Exception as e:  # noqa: BLE001  (a corrupt workbook is the person's to fix)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"could not read the sheet: {e}")
    if not headers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "the sheet has no header row")
    saved = db.get(AppSetting, f"import_map:{supplier.strip().lower()}")
    if column_map:
        cmap = {k: int(v) for k, v in _json.loads(column_map).items() if v is not None and v != ""}
    elif saved is not None and (saved.value or {}).get("headers") == headers:
        cmap = saved.value["map"]
    else:
        cmap = CI.guess_map(headers)
    try:
        imp = CI.stage(db, supplier=supplier.strip(), filename=file.filename or "", headers=headers,
                       rows=rows, column_map=cmap, user_email=user.email)
    except ValueError as e:
        db.rollback()
        return {"status": "needs_map", "headers": headers, "column_map": cmap,
                "sample": rows[:5], "detail": str(e)}
    db.commit()
    return {**_import_out(imp), "headers": headers, "sample": rows[:5]}


@router.get("/imports")
def list_imports(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> list[dict[str, Any]]:
    from app.models.catalogue import CatalogueImport

    q = (db.query(CatalogueImport).filter(CatalogueImport.status != "discarded")
         .order_by(CatalogueImport.created_at.desc()).limit(50))
    return [_import_out(i) for i in q]


@router.get("/imports/{import_id}")
def get_import(import_id: UUID, bucket: str | None = Query(None), limit: int = Query(200, le=2000),
               db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.models.catalogue import CatalogueImport

    imp = db.get(CatalogueImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import not found")
    out = _import_out(imp)
    rows = imp.rows or []
    if bucket == "big_move":
        rows = [r for r in rows if r.get("big_move")]
    elif bucket:
        rows = [r for r in rows if r.get("bucket") == bucket]
    out["rows"], out["rows_total"] = rows[:limit], len(rows)
    return out


class ImportApply(BaseModel):
    #: Library materials (by id) to update from this sheet; the ones left ticked.
    material_ids: list[str] = Field(default_factory=list)


@router.post("/imports/{import_id}/apply")
def apply_import(import_id: UUID, body: ImportApply, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.models.catalogue import CatalogueImport
    from app.models.prep import AppSetting
    from app.services import catalogue_import as CI

    imp = db.get(CatalogueImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import not found")
    try:
        n = CI.apply(db, imp, body.material_ids, user.email)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    # Remember this supplier's columns for next time (keyed to its headers).
    db.merge(AppSetting(key=f"import_map:{imp.supplier.lower()}",
                        value={"map": imp.column_map, "headers": (imp.summary or {}).get("headers")}))
    db.commit()
    return {**_import_out(imp), "applied": n}


@router.post("/imports/{import_id}/undo")
def undo_import(import_id: UUID, db: Session = Depends(get_db),
                user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    from app.models.catalogue import CatalogueImport
    from app.services import catalogue_import as CI

    imp = db.get(CatalogueImport, import_id)
    if imp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import not found")
    try:
        n = CI.undo(db, imp, user.email)
    except CI.UndoRefused as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    db.commit()
    return {**_import_out(imp), "undone": n}


class AssemblyStatus(BaseModel):
    status: str = Field(pattern="^(active|retired|draft|held)$")


@router.patch("/assemblies/{code}")
def set_assembly_status(code: str, body: AssemblyStatus, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Release (active), retire, or send back for review. Retiring keeps the
    assembly and its history; it just stops pricing."""
    a = db.scalar(select(RateAssembly).where(RateAssembly.code == code))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No assembly '{code}'")
    was = a.status
    a.status = body.status
    a.payload = {**(a.payload or {}), "status_log": [*((a.payload or {}).get("status_log") or []),
                 {"from": was, "to": body.status, "by": user.email, "at": date.today().isoformat()}]}
    db.commit()
    return {"code": a.code, "status": a.status, "was": was}


@router.get("/labour")
def list_labour(
    trade: str | None = Query(None),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    stmt = select(LabourRate).where(LabourRate.is_active.is_(True))
    if trade:
        stmt = stmt.where((LabourRate.trade == trade) | (LabourRate.trade_key == trade))
    stmt = stmt.order_by(LabourRate.trade, LabourRate.source)
    return [
        {
            "id": str(r.id),
            "trade": r.trade,
            **_vocab_fields(r.trade_key),
            "region": r.region,
            "project_type": r.project_type,
            "source": r.source,
            "base_rate": float(r.base_rate),
            "discount_pct": float(r.discount_pct),
            "effective_rate": float(r.effective_rate),
            "as_of_date": r.as_of_date.isoformat() if r.as_of_date else None,
            "source_ref": r.source_ref,
            # The billable low/mid/high band is what admin compares against when
            # choosing a band for a client-facing estimate. It lives in payload
            # because it is NOT a cost and must never be priced directly — but it
            # has to reach the UI, or the comparison cannot happen.
            "billable_band": (r.payload or {}).get("billable_band"),
            "payload": r.payload or {},
        }
        for r in db.scalars(stmt)
    ]


# ── Authoring ──────────────────────────────────────────────────────


class AssemblyCreate(BaseModel):
    code: str
    name: str
    trade: str
    unit: str  # a measure unit: the takeoff quantity it prices (sf, lf, ea …)
    category: str | None = None
    project_type: str = "any"
    description: str | None = None
    scope_notes: str | None = None


class EntryCreate(BaseModel):
    assembly_code: str
    unit_rate: float
    source_type: str  # subcontract_derived | published | composed | market
    confidence: str = "derived"
    project_type: str = "any"
    source_ref: str | None = None
    as_of_date: date | None = None
    source_project_id: UUID | None = None
    source_cost_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    # If true, deactivate any prior active entry for the same
    # (assembly, source_type, project_type) and point its superseded_by here.
    supersede: bool = True


class MaterialCreate(BaseModel):
    name: str
    unit: str
    unit_cost: float
    source: str = "supplier"  # supplier | market
    sku: str | None = None
    trade: str | None = None
    category: str | None = None
    supplier: str | None = None
    project_type: str = "any"
    waste_pct: float | None = None
    #: What the takeoff measures this in, and how much of it one `unit` covers
    #: (a 4x8 sheet: measure_unit "sf", coverage 32).
    measure_unit: str | None = None
    coverage: float | None = Field(default=None, gt=0)
    as_of_date: date | None = None
    source_ref: str | None = None


class LabourCreate(BaseModel):
    trade: str
    base_rate: float
    source: str = "own"  # own | association | workbc | market
    region: str = "metro-vancouver"
    project_type: str = "any"
    discount_pct: float | None = None  # defaults to 0; set explicitly for a real discount
    effective_rate: float | None = None  # override; else computed
    as_of_date: date | None = None
    source_ref: str | None = None


_SOURCE_TYPES = ("own", "subcontract_derived", "published", "composed", "market")


def _unit(raw: str, *, field: str = "unit", measure: bool = False) -> str:
    """The canonical unit, or a 422 that lists the allowed ones (plan phase 1,
    2026-10-15: units were stored as typed, eight spellings for five things)."""
    from app.services.library_vocab import MEASURE_UNITS, UnknownUnit, normalize_unit

    try:
        u = normalize_unit(raw)
    except UnknownUnit as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field}: {e}")
    if measure and u not in MEASURE_UNITS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{field}: {u} is not something a takeoff measures ({', '.join(sorted(MEASURE_UNITS))})")
    return u


def _trade_key(raw: str | None) -> str | None:
    from app.services.library_vocab import normalize_trade

    return normalize_trade(raw)


@router.post("/assemblies", status_code=status.HTTP_201_CREATED)
def create_assembly(
    body: AssemblyCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if db.scalar(select(RateAssembly).where(RateAssembly.code == body.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Assembly '{body.code}' exists")
    a = RateAssembly(
        code=body.code, name=body.name, trade=body.trade, trade_key=_trade_key(body.trade),
        unit=_unit(body.unit, measure=True),
        category=body.category, project_type=body.project_type,
        description=body.description, scope_notes=body.scope_notes,
        created_by=user.email,
    )
    db.add(a)
    db.commit()
    return {"id": str(a.id), "code": a.code}


@router.post("/entries", status_code=status.HTTP_201_CREATED)
def create_entry(
    body: EntryCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if body.source_type not in _SOURCE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"bad source_type {body.source_type}")
    assembly = db.scalar(select(RateAssembly).where(RateAssembly.code == body.assembly_code))
    if assembly is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No assembly '{body.assembly_code}'")

    entry = RateEntry(
        assembly_id=assembly.id, unit_rate=Decimal(str(body.unit_rate)),
        unit=assembly.unit, source_type=body.source_type, confidence=body.confidence,
        project_type=body.project_type, source_ref=body.source_ref,
        as_of_date=body.as_of_date, source_project_id=body.source_project_id,
        source_cost_id=body.source_cost_id, payload=body.payload, notes=body.notes,
        created_by=user.email,
    )
    db.add(entry)
    db.flush()  # get entry.id before wiring supersede pointers

    if body.supersede:
        prior = list(
            db.scalars(
                select(RateEntry).where(
                    RateEntry.assembly_id == assembly.id,
                    RateEntry.source_type == body.source_type,
                    RateEntry.project_type == body.project_type,
                    RateEntry.is_active.is_(True),
                    RateEntry.id != entry.id,
                )
            )
        )
        for p in prior:
            p.is_active = False
            p.superseded_by = entry.id

    db.commit()
    return {"id": str(entry.id), "assembly_code": body.assembly_code, "superseded": len(prior) if body.supersede else 0}


@router.post("/materials", status_code=status.HTTP_201_CREATED)
def create_material(
    body: MaterialCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    unit = _unit(body.unit)
    measure = _unit(body.measure_unit, field="measure_unit", measure=True) if body.measure_unit else None
    if measure and measure != unit and body.coverage is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"coverage: how many {measure} does one {unit} cover?")
    m = MaterialRate(
        name=body.name, unit=unit, unit_cost=Decimal(str(body.unit_cost)),
        measure_unit=measure, coverage=Decimal(str(body.coverage)) if body.coverage else None,
        source=body.source, sku=body.sku, trade=body.trade, trade_key=_trade_key(body.trade),
        category=body.category,
        supplier=body.supplier, project_type=body.project_type,
        waste_pct=Decimal(str(body.waste_pct)) if body.waste_pct is not None else None,
        as_of_date=body.as_of_date, source_ref=body.source_ref, created_by=user.email,
    )
    db.add(m)
    db.commit()
    return {"id": str(m.id), "name": m.name}


@router.post("/labour", status_code=status.HTTP_201_CREATED)
def create_labour(
    body: LabourCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    # No implicit discount on any source. Published wage data (Job Bank/WorkBC)
    # is stored AS POSTED; a discount is only applied when a real one exists
    # (e.g. a negotiated association rate), and then it must be passed explicitly.
    # Previously non-own sources were silently reduced 20%, which understated
    # every fallback rate and pushed apprentice rates below BC's statutory floor.
    discount = 0.0 if body.discount_pct is None else body.discount_pct
    if body.effective_rate is not None:
        effective = body.effective_rate
    else:
        effective = round(body.base_rate * (1 - discount / 100.0), 4)
    r = LabourRate(
        trade=body.trade, trade_key=_trade_key(body.trade), region=body.region, project_type=body.project_type,
        source=body.source, base_rate=Decimal(str(body.base_rate)),
        discount_pct=Decimal(str(discount)), effective_rate=Decimal(str(effective)),
        as_of_date=body.as_of_date, source_ref=body.source_ref, created_by=user.email,
    )
    db.add(r)
    db.commit()
    return {"id": str(r.id), "trade": r.trade, "effective_rate": float(r.effective_rate)}
