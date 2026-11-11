"""Reading tools both drafting engines use: a document's text, a text search,
and a drawing page cut into tiles.

⚠️ TILES, NEVER THE WHOLE SHEET. Probe on 2026-10-13 (1234 structural sheet,
7200x4800 px at 200 dpi): sent as one image it was downscaled and the model
read four values confidently WRONG (footing 18" deep, which is the frost depth;
1/2" anchor bolts; a 20" lap; concrete strength unreadable). The same page as
3x2 overlapping tiles got every value right. A page is therefore only ever
handed over as tiles.

Every function takes the request's project and refuses documents outside it.
"""
from __future__ import annotations

import io
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

MAX_TEXT_CHARS = 40_000
TILE_DPI = 200
#: Longest side the API keeps without downscaling.
TILE_MAX_PX = 1568


def _doc(db: Session, project_id: UUID, document_id: str) -> dict[str, Any]:
    try:
        did = UUID(str(document_id))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found") from None
    row = db.execute(_T("SELECT id, filename, r2_key, mime FROM documents WHERE id = :d AND project_id = :p"),
                     {"d": str(did), "p": str(project_id)}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="document not found in this project")
    return dict(row)


def document_text(db: Session, project_id: UUID, document_id: str, pages: list[int] | None = None) -> dict[str, Any]:
    d = _doc(db, project_id, document_id)
    q = "SELECT page_no, text FROM document_text_pages WHERE document_id = :d"
    params: dict[str, Any] = {"d": str(d["id"])}
    if pages:
        q += " AND page_no = ANY(:pg)"
        params["pg"] = pages
    rows = db.execute(_T(q + " ORDER BY page_no"), params).all()
    out, used = [], 0
    for page_no, text in rows:
        if used >= MAX_TEXT_CHARS:
            break
        chunk = text[: MAX_TEXT_CHARS - used]
        used += len(chunk)
        out.append({"page": page_no, "text": chunk})
    return {"filename": d["filename"], "pages": out,
            "note": None if rows else "No text layer: a scan or photo. Read it as tiles instead.",
            "truncated": used >= MAX_TEXT_CHARS}


def search(db: Session, project_id: UUID, query: str) -> list[dict[str, Any]]:
    from app.services.document_search import search_document_pages

    return search_document_pages(db, query=query, project_ids=[project_id], limit=15)


def page_count(pdf: bytes) -> int:
    import fitz

    return fitz.open(stream=pdf, filetype="pdf").page_count


def tiles(db: Session, project_id: UUID, document_id: str, page_no: int = 1) -> list[bytes]:
    """PNG tiles of one page (or the image itself, tiled when large)."""
    import fitz
    from PIL import Image

    from app.services.r2 import get_bytes

    d = _doc(db, project_id, document_id)
    data = get_bytes(d["r2_key"])
    if (d["mime"] or "").startswith("image/") or not data[:4] == b"%PDF":
        img = Image.open(io.BytesIO(data)).convert("RGB")
    else:
        doc = fitz.open(stream=data, filetype="pdf")
        if not 1 <= page_no <= doc.page_count:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"page {page_no} is outside 1-{doc.page_count}")
        pix = doc[page_no - 1].get_pixmap(dpi=TILE_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    return [_png(t) for t in split(img)]


def split(img: Any, overlap: float = 0.08) -> list[Any]:
    """Enough overlapping tiles that none is downscaled much: a letter page is
    one tile, a 36x24 sheet at 200 dpi is 3x2."""
    w, h = img.size
    cols = max(1, round(w / (TILE_MAX_PX * 1.5)))
    rows = max(1, round(h / (TILE_MAX_PX * 1.5)))
    if cols == rows == 1:
        return [img]
    tw, th = w / cols, h / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = max(0, int(c * tw - overlap * tw)), max(0, int(r * th - overlap * th))
            x1, y1 = min(w, int((c + 1) * tw + overlap * tw)), min(h, int((r + 1) * th + overlap * th))
            out.append(img.crop((x0, y0, x1, y1)))
    return out


def _png(img: Any) -> bytes:
    img = img.copy()
    img.thumbnail((TILE_MAX_PX, TILE_MAX_PX))
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()
