"""Region-based stated-quantity extractor for construction PDFs.

Replaces the line-weight stroke-classification approach. Premise: the most
accurate takeoff *reads* the quantities the drawing already states (schedules,
area/quantity tables, assembly specs / general notes) rather than measuring
geometry it can't identify.

Pipeline (two-pass — validated on real drawings 2026-06-27):
  1. LOCATE  — render the page at overview resolution, ask the vision model to
     return normalized bounding boxes of every table / schedule / notes block.
  2. EXTRACT — for each region, crop the PDF to its bbox, render that crop at
     HIGH dpi (full-sheet text is illegible at vision scale; the crop is not),
     and ask the model to transcribe it as structured rows or spec statements.

Why two passes: a 36x24" sheet downscales to ~55 dpi at vision scale and fine
table text is lost; the same region cropped + rendered at ~250 dpi is exact.

The model is Anthropic vision via the shared client in ``app.services.ai``
(bills boka's key). Transcription only — the prompts forbid inference/math, so
the human review step gets verbatim values to confirm.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import pymupdf

from app.services.ai import MODEL_OPUS, call_anthropic


# Overview pass: cap the long edge here. Vision downscales to ~1568px anyway,
# so rendering much larger wastes tokens without helping region location.
_OVERVIEW_LONG_PX = 1600
# Extract pass: target this long edge for the cropped region. High enough that
# schedule/table text is crisp; capped by _EXTRACT_MAX_DPI so a tiny crop
# doesn't render absurdly large.
_EXTRACT_TARGET_LONG_PX = 2200
_EXTRACT_MAX_DPI = 300
# Pad each region bbox by this fraction so a slightly-tight model box doesn't
# clip the table's edge rows/columns.
_BBOX_PAD = 0.02


@dataclass
class Region:
    kind: str            # schedule | quantity_table | spec_notes | title_block | other
    title: str
    bbox: list[float]    # [x0, y0, x1, y1] normalized 0..1, top-left origin
    content: dict[str, Any] | None = None  # filled by extract pass

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageExtraction:
    document_id: str
    page: int                       # 1-based
    page_width_pt: float
    page_height_pt: float
    regions: list[Region] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page": self.page,
            "page_width_pt": self.page_width_pt,
            "page_height_pt": self.page_height_pt,
            "regions": [r.to_dict() for r in self.regions],
            "stats": self.stats,
        }


# ── rendering ────────────────────────────────────────────────────────────

def _render_png(
    page: pymupdf.Page,
    *,
    clip: pymupdf.Rect | None = None,
    target_long_px: int,
    max_dpi: int = _EXTRACT_MAX_DPI,
) -> bytes:
    """Render a page (or a clip rect) to PNG bytes, sizing dpi so the long
    edge lands near ``target_long_px`` without exceeding ``max_dpi``."""
    region = clip if clip is not None else page.rect
    long_pt = max(region.width, region.height)
    if long_pt <= 0:
        long_pt = 1.0
    dpi = min(max_dpi, max(36, int(target_long_px / long_pt * 72)))
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    return pix.tobytes("png")


def _image_block(png: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png).decode("ascii"),
        },
    }


def _parse_json(text: str) -> Any:
    """Pull the first JSON value out of a model response (tolerates ```json
    fences and surrounding prose)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    # Try the whole thing first — critical so an object that *contains* an
    # array (e.g. {"rows":[...]}) isn't mis-sliced down to the inner array.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Fall back to the EARLIEST top-level bracket (object or array), so we keep
    # the outermost structure rather than always preferring '['.
    candidates = [(t.find(o), o, c) for o, c in (("{", "}"), ("[", "]"))]
    for i, open_ch, close_ch in sorted(c for c in candidates if c[0] != -1):
        j = t.rfind(close_ch)
        if j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(t)  # let it raise with the original text


# ── pass 1: locate regions ─────────────────────────────────────────────────

_LOCATE_SYSTEM = (
    "You are a construction-drawing analyst. You identify TEXT blocks that "
    "state quantities or specifications — never the drawing graphics."
)

_LOCATE_PROMPT = """\
This image is one sheet of a construction drawing set.

Find every distinct TABLE / SCHEDULE / NOTES block that states data, such as:
- schedules (door, window, fixture, finish, footing, beam, column)
- quantity or area calculation tables, volumetric tables
- general notes and assembly specifications (e.g. "exterior walls 2x6 @ 24\\" o/c")
- the title block (sheet number, project, scale)

IGNORE the drawing graphics themselves (floor plans, sections, elevations,
details, 3D views) — only the text/tabular blocks.

Return a JSON array; one object per block:
[{"kind":"schedule|quantity_table|spec_notes|title_block|other",
  "title":"<short label of what it is>",
  "bbox":[x0,y0,x1,y1]}]
bbox is normalized 0..1 with the top-left of the image as origin
(x = fraction of width, y = fraction of height). Be generous with the box so
no rows/columns are cut off. Return ONLY the JSON array."""


def locate_regions(page: pymupdf.Page, *, model: str = MODEL_OPUS) -> tuple[list[Region], dict]:
    png = _render_png(page, target_long_px=_OVERVIEW_LONG_PX, max_dpi=200)
    res = call_anthropic(
        model=model,
        system=_LOCATE_SYSTEM,
        messages=[{"role": "user", "content": [{"type": "text", "text": _LOCATE_PROMPT}, _image_block(png)]}],
        max_tokens=2000,
    )
    raw = _parse_json(res.text)
    regions: list[Region] = []
    for r in raw if isinstance(raw, list) else []:
        bbox = r.get("bbox") or []
        if len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox)
        regions.append(Region(
            kind=str(r.get("kind") or "other"),
            title=str(r.get("title") or ""),
            bbox=[min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
        ))
    return regions, {"locate_cost_usd": float(res.cost_usd), "locate_model": model}


# ── pass 2: extract one region ──────────────────────────────────────────────

_EXTRACT_SYSTEM = (
    "You transcribe construction-drawing tables and notes VERBATIM. You never "
    "infer, compute, round, or fill in missing values. Illegible -> null."
)

_EXTRACT_PROMPT = """\
This is a cropped region from a construction drawing sheet. Transcribe it.

If it is a TABLE or SCHEDULE, return:
{"format":"table","title":"<title>",
 "columns":["<header>", ...],
 "rows":[["<cell>", ...], ...]}

If it is NOTES / SPECIFICATIONS (prose or numbered statements), return:
{"format":"specs","title":"<title>",
 "statements":["<each note / spec verbatim>", ...]}

Rules:
- Transcribe EXACTLY what is printed — every cell, every number, every unit.
- Do NOT calculate, total, convert units, or infer anything not printed.
- If a cell/value is unreadable, use null (tables) or note "[illegible]" (specs).
- Return ONLY the JSON object."""


def _pad_clip(page: pymupdf.Page, bbox: list[float]) -> pymupdf.Rect:
    W, H = page.rect.width, page.rect.height
    x0 = max(0.0, bbox[0] - _BBOX_PAD)
    y0 = max(0.0, bbox[1] - _BBOX_PAD)
    x1 = min(1.0, bbox[2] + _BBOX_PAD)
    y1 = min(1.0, bbox[3] + _BBOX_PAD)
    return pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H)


def extract_region(page: pymupdf.Page, region: Region, *, model: str = MODEL_OPUS) -> float:
    """Run the high-res extract pass for one region; mutates region.content.
    Returns the call cost (USD)."""
    clip = _pad_clip(page, region.bbox)
    png = _render_png(page, clip=clip, target_long_px=_EXTRACT_TARGET_LONG_PX)
    res = call_anthropic(
        model=model,
        system=_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": [{"type": "text", "text": _EXTRACT_PROMPT}, _image_block(png)]}],
        max_tokens=4000,
    )
    try:
        region.content = _parse_json(res.text)
    except (json.JSONDecodeError, ValueError):
        region.content = {"format": "error", "raw": res.text[:4000]}
    return float(res.cost_usd)


# ── orchestration ───────────────────────────────────────────────────────────

def extract_page(
    pdf_bytes: bytes,
    page_index: int,
    *,
    document_id: str = "",
    model: str = MODEL_OPUS,
    skip_kinds: tuple[str, ...] = ("title_block",),
) -> PageExtraction:
    """Full two-pass extraction for one page (0-based ``page_index``)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_index]
    out = PageExtraction(
        document_id=document_id,
        page=page_index + 1,
        page_width_pt=float(page.rect.width),
        page_height_pt=float(page.rect.height),
    )

    regions, stats = locate_regions(page, model=model)
    total_cost = stats.get("locate_cost_usd", 0.0)
    for region in regions:
        if region.kind in skip_kinds:
            out.regions.append(region)  # keep it listed, don't pay to extract
            continue
        total_cost += extract_region(page, region, model=model)

    out.regions = regions
    out.stats = {
        **stats,
        "region_count": len(regions),
        "extracted_count": sum(1 for r in regions if r.content is not None),
        "total_cost_usd": round(total_cost, 4),
        "schema_version": 1,
    }
    doc.close()
    return out
