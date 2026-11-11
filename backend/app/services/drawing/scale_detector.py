"""Detect drawing scale per page and compute PDF→real-world conversion.

PyMuPDF reports geometry in PDF units (1 unit = 1/72 inch).  Real-world
quantities require knowing the drawing's scale notation.

Supported notations:
- Imperial:  ``1/4" = 1'-0"``, ``1/8" = 1'-0"``, ``3/8" = 1'-0"`` etc.
- Metric:    ``1:50``, ``1:100``, ``1:200``

Returns one PageScale per page. Confidence flags how the scale was found —
"high" when text was explicitly tagged ``SCALE:``, "medium" when the format
was found anywhere in page text, "measured" when no label was found but the
sheet's own printed dimensions agree on one (scale_check.py), "low" when
nothing was found (the page might be a notes/details sheet with no
measurable plan).

Each PageScale also carries what the printed dimensions measure at
(``measured_ppf``), so the review page can warn when the scale in use
disagrees with the drawing itself.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.services.drawing.pymupdf_extractor import DrawingExtract


# Imperial scale: 1/4" = 1'-0", 1" = 1'-0", 3/8" = 1'-0", etc.
_SCALE_IMPERIAL = re.compile(
    r"(\d+(?:/\d+)?)\s*[\"”]?\s*=\s*1\s*'\s*-?\s*0?\s*[\"”]?",
)
# Metric ratio:  1:50, 1 : 100
_SCALE_METRIC = re.compile(r"1\s*:\s*(\d{2,4})\b")

# PDF point = 1/72 inch = 25.4/72 mm ≈ 0.3528 mm
_MM_PER_POINT = 25.4 / 72.0
_MM_PER_FOOT = 304.8


@dataclass
class PageScale:
    page: int
    scale_label: str | None              # e.g. "1/4\" = 1'-0\"" or "1:50"
    pdf_units_per_real_foot: float | None
    confidence: str                      # "high" | "medium" | "measured" | "low"
    #: What the sheet's printed dimensions measure at, when enough agree.
    measured_ppf: float | None = None
    measured_agreeing: int = 0
    measured_readings: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_imperial_fraction(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _imperial_to_pdf_units_per_foot(fraction_inches: float) -> float:
    """1/4" plan = 1'-0" real ⇒ 1 real foot draws as 0.25 inch ⇒ 18 PDF points."""
    return fraction_inches * 72.0


def _metric_to_pdf_units_per_foot(ratio: int) -> float:
    """1:50 metric ⇒ 1 PDF mm = 50 real mm ⇒ 1 real foot = 304.8 mm = 304.8/50 PDF mm
    ⇒ in points: (304.8 / ratio) / mm_per_point."""
    if ratio == 0:
        return 0.0
    return (_MM_PER_FOOT / ratio) / _MM_PER_POINT


def _try_match(text: str) -> tuple[str, float] | None:
    """Return (label, pdf_units_per_real_foot) if text matches a scale format."""
    t = text.replace("”", '"').replace("’", "'")
    m_imp = _SCALE_IMPERIAL.search(t)
    if m_imp:
        frac = _parse_imperial_fraction(m_imp.group(1))
        if 0 < frac < 12:  # sanity guard
            return (m_imp.group(0), _imperial_to_pdf_units_per_foot(frac))
    m_met = _SCALE_METRIC.search(t)
    if m_met:
        ratio = int(m_met.group(1))
        if 5 <= ratio <= 5000:  # sanity guard on plausible architectural ratios
            return (m_met.group(0), _metric_to_pdf_units_per_foot(ratio))
    return None


def units_per_foot_from_label(label: str) -> float | None:
    """Public helper: convert a scale label (``1/4" = 1'-0"`` or ``1:50``) into
    PDF-units-per-real-foot. Returns None when the label matches no known
    notation. Used by the manual scale-override endpoint so the imperial /
    metric conversion math lives in exactly one place."""
    hit = _try_match(label)
    return round(hit[1], 4) if hit else None


#: Common drawing scales, to name a measured scale when it is one of them.
#: Labels match the review page's scale list so its dropdown can show them.
_NAMED = [(f'{f}" = 1\'-0"', _imperial_to_pdf_units_per_foot(_parse_imperial_fraction(f)))
          for f in ("1/16", "3/32", "1/8", "3/16", "1/4", "3/8", "1/2", "3/4", "1", "3")] + \
         [(f"1:{r}", _metric_to_pdf_units_per_foot(r)) for r in (10, 20, 25, 50, 75, 100, 200, 500)]


def named_scale_for(ppf: float, metric: bool = False, tol: float = 0.01) -> str | None:
    """The common scale ``ppf`` matches within ``tol``, else None. Only names
    from one family: 34.6 units/ft is 1:25, which means nothing on a sheet
    dimensioned in feet and inches (and measured scales come from those)."""
    for label, v in _NAMED:
        if (":" in label) == metric and v and abs(ppf - v) <= tol * v:
            return label
    return None


def detect(extract: DrawingExtract) -> list[PageScale]:
    """Find a scale label per page; rank by 'SCALE:' tag presence."""
    by_page: dict[int, list[str]] = {}
    for t in extract.text_runs:
        by_page.setdefault(t.page, []).append(t.text)

    results: list[PageScale] = []
    for page in extract.pages:
        texts = by_page.get(page.page, [])
        label: str | None = None
        ppf: float | None = None
        confidence = "low"

        # First pass: explicit "SCALE:" / "SCALE =" tags
        for t in texts:
            stripped = t.strip()
            if stripped.upper().startswith(("SCALE", "SC:", "SC ")):
                hit = _try_match(stripped)
                if hit:
                    label, ppf = hit
                    confidence = "high"
                    break

        # Second pass: same format found anywhere in page text
        if label is None:
            for t in texts:
                hit = _try_match(t)
                if hit:
                    label, ppf = hit
                    confidence = "medium"
                    break

        measured = getattr(page, "measured_ppf", None)
        if label is None and measured:
            # No printed scale, but the dimensions agree on one: use it.
            label, ppf, confidence = named_scale_for(measured), measured, "measured"

        results.append(PageScale(
            page=page.page,
            scale_label=label,
            pdf_units_per_real_foot=round(ppf, 3) if ppf else None,
            confidence=confidence,
            measured_ppf=measured,
            measured_agreeing=getattr(page, "measured_agreeing", 0),
            measured_readings=getattr(page, "measured_readings", 0),
        ))

    return results
