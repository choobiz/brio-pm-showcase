"""Drawing-takeoff service.

Reads a PDF and returns measurement-ready quantity candidates. The pipeline:
  1. PyMuPDF extraction       — text + positions + raw vector paths
  2. Path classification      — group raw paths into linear candidates
  3. Scale detection          — find drawing scale per page, convert to real units

Project-type-agnostic: identical pipeline for residential renos, new builds,
commercial TIs. The review UI consumes these candidates and lets a human
confirm / adjust before the estimate is generated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.drawing.path_classifier import LinearCandidate, classify
from app.services.drawing.pymupdf_extractor import DrawingExtract, extract
from app.services.drawing.scale_detector import PageScale, detect as detect_scales


@dataclass
class AnalyzedDrawing:
    document_id: str
    extract: DrawingExtract
    candidates: list[LinearCandidate]
    scales: list[PageScale]

    def linear_summary(self) -> list[dict[str, Any]]:
        """One row per (page, classification) — converted to real feet where
        the scale is known."""
        scale_by_page = {s.page: s for s in self.scales}
        out: list[dict[str, Any]] = []
        for c in self.candidates:
            sc = scale_by_page.get(c.page)
            real_ft = None
            if sc and sc.pdf_units_per_real_foot:
                real_ft = round(c.total_length_pdf_units / sc.pdf_units_per_real_foot, 1)
            out.append({
                "page": c.page,
                "classification": c.classification,
                "width_bucket": c.width_bucket,
                "orthogonal": c.orthogonal,
                "path_count": c.path_count,
                "stroke_width": c.stroke_width,
                "pdf_units": c.total_length_pdf_units,
                "real_feet": real_ft,
                "scale_confidence": sc.confidence if sc else "low",
            })
        return out


def analyze_drawing(pdf_bytes: bytes, document_id: str = "") -> AnalyzedDrawing:
    """Full pipeline: extract → classify → detect scale."""
    ext = extract(pdf_bytes, document_id=document_id)
    candidates = classify(ext)
    scales = detect_scales(ext)
    return AnalyzedDrawing(
        document_id=document_id,
        extract=ext,
        candidates=candidates,
        scales=scales,
    )


__all__ = [
    "AnalyzedDrawing",
    "DrawingExtract",
    "LinearCandidate",
    "PageScale",
    "analyze_drawing",
    "classify",
    "detect_scales",
    "extract",
]
