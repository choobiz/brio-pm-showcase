"""Content-hash-keyed cache around the drawing analysis pipeline.

Re-parsing a 13 MB / 20-page PDF takes ~8 seconds. Same call from the review
UI should be sub-100ms after the first run — that's what this layer does.

Cache key: SHA-256 of the R2 object bytes. If the underlying drawing has
been replaced (new permit set), the hash changes and we re-parse
transparently.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.document import Document
from app.models.drawing_analysis import DrawingAnalysis
from app.services.drawing import AnalyzedDrawing, analyze_drawing
from app.services.r2 import get_bytes

# 4 (2026-10-26): fills and sheet-wide groups no longer counted as lengths;
# pages carry raster_plan. Older rows are re-parsed by the background worker.
# 5 (2026-10-26): walls drawn as solid fills ("wall_filled"), diagonals chained
# by their real end points, scales carry what printed dimensions measure at.
SCHEMA_VERSION = 5  # bump when the payload shape changes; older rows are re-parsed


# Cap per-candidate member paths in the serialized cache. A candidate may
# contain hundreds of strokes; the review UI only needs enough to visually
# explain "this is what was counted". Top-N by length keeps the biggest
# contributors (which is also what the user will scan).
_MAX_PATHS_PER_CANDIDATE = 60


def _serialize(result: AnalyzedDrawing) -> dict:
    """Compact JSON payload for storage. Keeps top-N member paths per
    candidate so the review UI can highlight each individual stroke instead
    of a single union bbox that spans most of the page."""
    candidates_out = []
    for c in result.candidates:
        d = c.to_dict()
        # Cap member paths to the top-N longest, keep raw count separately so
        # the UI can show "showing top 60 of 247 strokes".
        all_paths = d.get("paths") or []
        top = sorted(all_paths, key=lambda p: -(p.get("length") or 0))[
            :_MAX_PATHS_PER_CANDIDATE
        ]
        d["paths"] = [
            {"bbox": list(p["bbox"]), "length": p["length"]} for p in top
        ]
        candidates_out.append(d)

    return {
        "pages": [
            {
                "page": p.page,
                "width": p.width,
                "height": p.height,
                "rotation": p.rotation,
                "text_run_count": p.text_run_count,
                "vector_path_count": p.vector_path_count,
                "dimension_count": p.dimension_count,
                "image_count": p.image_count,
                "raster_regions": [list(r) for r in getattr(p, "raster_regions", [])],
                "raster_plan": bool(getattr(p, "raster_plan", False)),
            }
            for p in result.extract.pages
        ],
        "candidates": candidates_out,
        "scales": [s.to_dict() for s in result.scales],
        "dimensions": [
            {
                "text": d.text,
                "bbox": list(d.bbox),
                "page": d.page,
                "font_size": d.font_size,
            }
            for d in result.extract.dimensions
        ],
        "linear_summary": result.linear_summary(),
        "stats": {
            "text_runs": len(result.extract.text_runs),
            "raw_paths": len(result.extract.paths),
            "dimensions": len(result.extract.dimensions),
            "candidates": len(result.candidates),
            "scales_with_label": sum(1 for s in result.scales if s.scale_label),
            "schema_version": SCHEMA_VERSION,
        },
    }


def cached_analysis(db: Session, document_id: UUID) -> DrawingAnalysis | None:
    """The stored analysis when it is current for the file, else None. Never
    parses: safe to call inside a web request (parsing is the worker's job,
    see services/drawing/analysis_queue.py)."""
    existing = db.query(DrawingAnalysis).filter_by(document_id=document_id).first()
    if existing is None:
        return None
    if (existing.payload or {}).get("stats", {}).get("schema_version", 0) < SCHEMA_VERSION:
        return None
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    if hashlib.sha256(get_bytes(doc.r2_key)).hexdigest() != existing.content_hash:
        return None
    return existing


def get_or_analyze(
    db: Session, document_id: UUID, *, force: bool = False
) -> DrawingAnalysis | None:
    """Return a fresh or cached DrawingAnalysis for ``document_id``.

    ``force=True`` re-parses regardless of the content hash — useful after a
    code change to the parsing logic.
    """
    doc = db.get(Document, document_id)
    if doc is None:
        return None

    data = get_bytes(doc.r2_key)
    content_hash = hashlib.sha256(data).hexdigest()

    existing = (
        db.query(DrawingAnalysis).filter_by(document_id=document_id).first()
    )
    cached_schema = (
        (existing.payload or {}).get("stats", {}).get("schema_version", 0)
        if existing is not None
        else 0
    )
    if (
        existing is not None
        and existing.content_hash == content_hash
        and cached_schema >= SCHEMA_VERSION
        and not force
    ):
        return existing

    result = analyze_drawing(data, document_id=str(document_id))
    payload = _serialize(result)

    if existing is None:
        row = DrawingAnalysis(
            document_id=document_id,
            content_hash=content_hash,
            is_vector=result.extract.is_vector,
            page_count=result.extract.page_count,
            payload=payload,
        )
        db.add(row)
    else:
        existing.content_hash = content_hash
        existing.is_vector = result.extract.is_vector
        existing.page_count = result.extract.page_count
        existing.payload = payload
        flag_modified(existing, "payload")
        row = existing

    db.commit()
    db.refresh(row)
    return row
