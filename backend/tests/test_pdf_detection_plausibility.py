"""The measure-on-PDF engine must not report things that are not lengths.

2026-10-26, 456 W Sample corridor set: the measure page listed "13,486 ft"
of line-work for a 687 sf corridor. Three causes, each pinned here:

  1. The file was printed through PostScript, so every letter and hairline is a
     small FILLED shape. Their perimeters were summed as line length.
  2. Lines were grouped by weight across the whole sheet: notes, title block and
     both plans became one "candidate".
  3. Sheet ID-0's plan is a pasted picture. Nothing in it can be detected, and
     what was found on that page was title block and notes. The page now says
     the plan is a picture instead.

And the other side: real wall runs drawn as strokes are still found.
"""
from __future__ import annotations

import io

import pymupdf
import pytest

from app.services.drawing import path_classifier as P
from app.services.drawing.pymupdf_extractor import DrawingExtract, PageGeometry, VectorPath, extract

W, H = 2592.0, 1728.0  # a 36x24 in sheet


def _page(n=1, raster=False):
    return PageGeometry(page=n, width=W, height=H, rotation=0, text_run_count=0, vector_path_count=0,
                        dimension_count=0, image_count=1 if raster else 0,
                        raster_regions=[(100, 100, 1600, 2200)] if raster else [], raster_plan=raster)


def _line(x0, y0, x1, y1, page=1, width=2.0, op="stroke"):
    return VectorPath(page=page, bbox=(x0, y0, x1, y1), op_type=op,
                      length=((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, closed=False,
                      stroke_width=width, item_count=1)


def _wall_run():
    """An L-shaped wall drawn as 3 touching strokes: 400 + 300 + 200 units."""
    return [_line(100, 100, 500, 100), _line(500, 100, 500, 400), _line(500, 400, 700, 400)]


def _classify(paths, pages=None):
    return P.classify(DrawingExtract(document_id="t", page_count=1, is_vector=True,
                                     pages=pages or [_page()], paths=paths))


def test_a_real_wall_run_is_still_found():
    cands = _classify(_wall_run())
    assert len(cands) == 1 and cands[0].total_length_pdf_units == 900
    assert cands[0].classification == "wall_or_partition"


def test_filled_outlines_are_not_lengths():
    """Letters printed as fills: 2,000 small closed shapes in a text block."""
    glyphs = []
    for i in range(2000):
        x, y = 1800 + (i % 100) * 6, 200 + (i // 100) * 12
        glyphs.append(VectorPath(page=1, bbox=(x, y, x + 4, y + 8), op_type="fill", length=24.0,
                                 closed=True, stroke_width=0.0, item_count=6))
    cands = _classify(glyphs + _wall_run())
    assert [c.total_length_pdf_units for c in cands] == [900], "only the wall run is a length"


def test_a_group_spread_over_the_sheet_is_dropped():
    """Thin lines of one weight scattered across the whole sheet are notes,
    title block and two plans at once, not one element."""
    scattered = [_line(x, y, x + 300, y + 7, width=0.5) for x in range(50, 2300, 450) for y in range(50, 1700, 300)]
    cands = _classify(scattered + _wall_run())
    assert all(c.total_length_pdf_units < 1000 for c in cands)
    assert any(c.total_length_pdf_units == 900 for c in cands)


def test_a_group_denser_than_any_wall_run_is_dropped():
    """Hatching: 300 short connected strokes packed into a small square."""
    hatch = [_line(1000 + i % 20, 1000, 1000 + i % 20, 1020, width=0.2) for i in range(300)]
    for h in hatch:
        h.bbox = (h.bbox[0], h.bbox[1], h.bbox[0], h.bbox[3])
    cand = P._make_candidate(1, "very_thin", True, hatch)
    assert cand is not None and not P._plausible(cand, _page())


def test_nothing_is_reported_on_a_sheet_whose_plan_is_a_picture():
    title_block = [_line(2400, 50, 2400, 1700, width=2.0), _line(2400, 50, 2550, 50, width=2.0)]
    assert _classify(title_block, pages=[_page(raster=True)]) == []


def _pdf(with_picture: bool, strokes: int = 10) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    if with_picture:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
        pix.clear_with(200)
        page.insert_image(pymupdf.Rect(100, 100, 1700, 1100), pixmap=pix)
    for i in range(strokes):
        page.draw_line((150 + i * 5, 150), (150 + i * 5, 900), width=1.0)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("picture,strokes,expect", [
    (True, 10, True),     # a pasted plan with a few marks: the plan is the picture
    (True, 400, False),   # vector line-work drawn over a background picture
    (False, 10, False),   # a normal vector sheet
])
def test_the_extractor_flags_a_picture_plan(picture, strokes, expect):
    page = extract(_pdf(picture, strokes)).pages[0]
    assert page.raster_plan is expect
    assert bool(page.raster_regions) is picture
