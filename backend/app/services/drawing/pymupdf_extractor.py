"""PyMuPDF-based extractor for construction PDFs.

Reads vector PDFs and returns structured page geometry + text — the
foundation for measurement-grade quantity takeoff. Output is project-type-
agnostic: works for renovations, new builds, commercial TIs, residential —
nothing in this module is tuned for a specific occupancy class.

Output ``is_vector=False`` means the PDF is scanned/raster and downstream
needs to fall back to OCR (Tesseract layer, Phase 1.5).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import pymupdf  # PyMuPDF 1.26+

from app.services.drawing.poche import FilledWall, filled_walls
from app.services.drawing.scale_check import consensus, read_dimensions


# Construction dimension formats. Tightened — requires an explicit unit
# marker (foot tick, inch tick, mm, cm, or metric m) so phone numbers,
# drawing IDs, dates, and decimals don't match.
_DIM_PATTERNS = [
    # Imperial feet-inches: 12'-6", 12'-0", 12'-6 1/2", 12'-6, plain 12'
    re.compile(r"\b\d+'(?:-?\s*\d+(?:\s+\d+/\d+)?\s*\"?)?\b"),
    # Inches only with the inch mark: 6", 6 1/2"  (must end on ")
    re.compile(r"\b\d+(?:\s+\d+/\d+)?\""),
    # Metric mm with explicit unit: 3650mm, 1200 mm
    re.compile(r"\b\d{2,5}(?:\.\d+)?\s*mm\b", re.I),
    # Metric cm with explicit unit
    re.compile(r"\b\d+(?:\.\d+)?\s*cm\b", re.I),
    # Metric m with explicit unit, negative lookahead to avoid 'mm'
    re.compile(r"\b\d+(?:\.\d+)?\s*m\b(?!m)", re.I),
]


@dataclass
class TextRun:
    text: str
    bbox: tuple[float, float, float, float]
    page: int
    font_size: float
    is_dimension: bool


@dataclass
class VectorPath:
    page: int
    bbox: tuple[float, float, float, float]
    op_type: str            # "stroke" | "fill" | "stroke_fill"
    length: float           # path length in PDF units
    closed: bool
    stroke_width: float
    item_count: int
    #: Where the path starts and ends. A bbox cannot say which corners a
    #: diagonal joins, so chaining diagonals needs these.
    p0: tuple[float, float] | None = None
    p1: tuple[float, float] | None = None
    #: One straight segment: the only kind of non-orthogonal path that can be
    #: a run (a diagonal wall or pipe). Outlines and arcs are shapes.
    straight: bool = False


@dataclass
class PageGeometry:
    page: int
    width: float
    height: float
    rotation: int
    text_run_count: int
    vector_path_count: int
    dimension_count: int
    image_count: int
    #: Pasted pictures big enough to be a plan (>= RASTER_MIN_PAGE_FRACTION of
    #: the sheet), in the same unrotated coordinates as path bboxes.
    raster_regions: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: True when the sheet's drawing is a picture: line detection cannot see
    #: into it, so the page must be calibrated and measured by hand.
    raster_plan: bool = False
    #: The scale the sheet's own printed dimensions measure at (PDF units per
    #: real foot), when enough of them agree; see scale_check.py.
    measured_ppf: float | None = None
    measured_agreeing: int = 0
    measured_readings: int = 0


#: A picture covering at least this share of the sheet is treated as a plan.
#: 456 W Sample ID-0 (2026-10-26): the plan was a 1682x1107 px image filling
#: ~67% of the sheet, and every "length" found on it was title block and notes.
RASTER_MIN_PAGE_FRACTION = 0.15
#: ...and it is the sheet's drawing when stroked line-work inside it is this
#: sparse (vector markup over a picture would add strokes on top of it).
RASTER_MAX_STROKES_INSIDE = 200


def _inside(b: tuple[float, float, float, float], r: tuple[float, float, float, float]) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return r[0] <= cx <= r[2] and r[1] <= cy <= r[3]


@dataclass
class DrawingExtract:
    document_id: str
    page_count: int
    is_vector: bool                                  # False ⇒ raster ⇒ OCR needed
    pages: list[PageGeometry] = field(default_factory=list)
    text_runs: list[TextRun] = field(default_factory=list)
    paths: list[VectorPath] = field(default_factory=list)
    dimensions: list[TextRun] = field(default_factory=list)
    #: Walls drawn as solid fills, measured along their centre lines.
    filled_walls: list[FilledWall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Compact stats — for review pages, logging, and CI smoke tests."""
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "is_vector": self.is_vector,
            "text_runs": len(self.text_runs),
            "dimensions": len(self.dimensions),
            "paths": len(self.paths),
            "total_path_length": round(sum(p.length for p in self.paths), 1),
        }


def _is_dimension_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return any(pat.search(t) for pat in _DIM_PATTERNS)


def _path_length(items: list) -> float:
    """Approximate vector-path length, summing line + curve chords + rect perimeters."""
    total = 0.0
    for item in items:
        if not item:
            continue
        op = item[0]
        if op == "l":  # line: ('l', p0, p1)
            try:
                p0, p1 = item[1], item[2]
                total += ((p1.x - p0.x) ** 2 + (p1.y - p0.y) ** 2) ** 0.5
            except (AttributeError, IndexError):
                continue
        elif op == "c":  # bezier: ('c', p0, p1, p2, p3) — chord approximation
            try:
                p0, p3 = item[1], item[4]
                total += ((p3.x - p0.x) ** 2 + (p3.y - p0.y) ** 2) ** 0.5
            except (AttributeError, IndexError):
                continue
        elif op == "re":  # rectangle: ('re', Rect)
            try:
                rect = item[1]
                total += 2 * (rect.width + rect.height)
            except (AttributeError, IndexError):
                continue
    return total


def _ends(items: list) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """First and last point of a path made of lines and curves."""
    try:
        first, last = items[0], items[-1]
        if first[0] not in ("l", "c") or last[0] not in ("l", "c"):
            return None, None
        a, b = first[1], last[-1]
        return (float(a.x), float(a.y)), (float(b.x), float(b.y))
    except (AttributeError, IndexError, TypeError):
        return None, None


def extract(pdf_bytes: bytes, document_id: str = "") -> DrawingExtract:
    """Parse a PDF, returning text runs, vector paths, dimensions, page geometry."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = DrawingExtract(document_id=document_id, page_count=doc.page_count, is_vector=True)

    for page_idx, page in enumerate(doc, start=1):
        page_text_runs: list[TextRun] = []
        page_dims: list[TextRun] = []
        spans: list[tuple[str, tuple[float, float, float, float], bool]] = []
        image_count = 0

        text_dict = page.get_text("dict") or {}
        for block in text_dict.get("blocks", []):
            btype = block.get("type")
            if btype == 1:
                image_count += 1
                continue
            for line in block.get("lines", []):
                ldir = line.get("dir") or (1.0, 0.0)
                left_right = abs(ldir[0]) >= abs(ldir[1])
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    is_dim = _is_dimension_text(text)
                    tr = TextRun(
                        text=text,
                        bbox=tuple(span.get("bbox", (0, 0, 0, 0))),
                        page=page_idx,
                        font_size=float(span.get("size", 0.0)),
                        is_dimension=is_dim,
                    )
                    page_text_runs.append(tr)
                    spans.append((text, tr.bbox, left_right))
                    if is_dim:
                        page_dims.append(tr)

        page_paths: list[VectorPath] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        for d in drawings:
            items = d.get("items") or []
            if not items:
                continue
            try:
                length = _path_length(items)
            except Exception:
                length = 0.0
            rect = d.get("rect")
            bbox = (
                (rect.x0, rect.y0, rect.x1, rect.y1)
                if rect is not None else (0.0, 0.0, 0.0, 0.0)
            )
            op_type = {"s": "stroke", "f": "fill", "fs": "stroke_fill"}.get(
                d.get("type", ""), d.get("type", "") or ""
            )
            # Fills are never chained (see path_classifier), so skip their ends:
            # a PostScript sheet has ~25k of them.
            p0, p1 = _ends(items) if op_type != "fill" else (None, None)
            page_paths.append(VectorPath(
                page=page_idx, bbox=bbox, op_type=op_type, length=length,
                closed=bool(d.get("closePath", False)),
                stroke_width=float(d.get("width") or 0.0),
                item_count=len(items), p0=p0, p1=p1,
                straight=len(items) == 1 and items[0][0] == "l",
            ))

        # Pictures, in the same unrotated space as get_drawings() bboxes.
        raster_regions: list[tuple[float, float, float, float]] = []
        try:
            area = float(page.mediabox.width * page.mediabox.height) or 1.0
            for info in page.get_image_info():
                x0, y0, x1, y1 = info.get("bbox") or (0, 0, 0, 0)
                if (x1 - x0) * (y1 - y0) >= RASTER_MIN_PAGE_FRACTION * area:
                    raster_regions.append((float(x0), float(y0), float(x1), float(y1)))
        except Exception:  # noqa: BLE001 — a picture we cannot place is not a plan
            raster_regions = []
        strokes_inside = sum(
            1 for vp in page_paths
            if vp.op_type != "fill" and any(_inside(vp.bbox, r) for r in raster_regions)
        )
        raster_plan = bool(raster_regions) and strokes_inside <= RASTER_MAX_STROKES_INSIDE

        try:
            measured = consensus(read_dimensions(spans, drawings))
        except Exception:  # noqa: BLE001 — a check that fails says nothing
            measured = consensus([])
        if not raster_plan:
            try:
                out.filled_walls.extend(filled_walls(page_idx, drawings))
            except Exception:  # noqa: BLE001 — never lose the line takeoff over it
                pass

        out.pages.append(PageGeometry(
            page=page_idx,
            width=float(page.rect.width),
            height=float(page.rect.height),
            rotation=int(page.rotation),
            text_run_count=len(page_text_runs),
            vector_path_count=len(page_paths),
            dimension_count=len(page_dims),
            image_count=image_count,
            raster_regions=raster_regions,
            raster_plan=raster_plan,
            measured_ppf=measured.pdf_units_per_real_foot,
            measured_agreeing=measured.agreeing,
            measured_readings=measured.readings,
        ))
        out.text_runs.extend(page_text_runs)
        out.paths.extend(page_paths)
        out.dimensions.extend(page_dims)

    # Vector-vs-raster heuristic: a true CAD-export PDF will have many vector
    # paths per page; a scanned/raster PDF will have ~zero paths and mostly
    # one large image per page.
    total_paths = len(out.paths)
    total_images = sum(p.image_count for p in out.pages)
    out.is_vector = total_paths >= max(20, 5 * doc.page_count) or total_images == 0

    doc.close()
    return out
