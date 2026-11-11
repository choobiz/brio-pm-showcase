"""Measure-on-PDF, steps 2 and 3 (2026-10-26, 456 W Sample corridor set).

Step 2, the scale check: ID-0's title block said 1/4" = 1'-0" while the plan was
pasted in enlarged (~0.48"/ft), so every length came out 1.9x long and nothing
said so. A sheet's printed dimensions now give the scale it is really drawn at,
next to the scale its label claims.

Step 3, walls drawn as solid fills: on ID-1/ID-2 every wall is a grey fill cut
into triangles by a PostScript print. The engine measured lines only, so no wall
was ever listed. Fills are now measured along their centre line.

Also: straight diagonal lines chain by their real end points instead of being
lumped into one group per page.
"""
from __future__ import annotations

import io

import pymupdf
import pytest

from app.services.drawing import analyze_drawing
from app.services.drawing import path_classifier as P
from app.services.drawing.pymupdf_extractor import DrawingExtract, PageGeometry, VectorPath
from app.services.drawing.scale_check import DimensionReading, consensus, parse_feet

W, H = 2592.0, 1728.0  # 36x24 in


# ── step 2: the scale check ─────────────────────────────────────────────


@pytest.mark.parametrize("text,feet", [
    ("15'-6\"", 15.5), ("15'6\"", 15.5), ("15' 6\"", 15.5), ("58'", 58.0),
    ("12'-6 1/2\"", 12.5 + 0.5 / 12), ("3’-0”", 3.0),
    ("A.F.F. 3'4\"", None), ("2'4\"x6'6\"", None), ("C.H.=9'0\"", None), ("1/4\"=1'-0\"", None),
])
def test_only_plain_dimension_strings_are_read(text, feet):
    got = parse_feet(text)
    assert (got is None) if feet is None else got == pytest.approx(feet)


def test_agreeing_readings_win_and_scattered_ones_do_not():
    r = lambda ft, pu: DimensionReading(text="x", feet=ft, pdf_units=pu)  # noqa: E731
    agree = [r(10, 180), r(15.5, 279), r(4, 72.1), r(22, 395.5)]
    noise = [r(10, 40), r(3, 300), r(8, 9)]
    assert consensus(agree + noise).pdf_units_per_real_foot == pytest.approx(18, rel=0.01)
    assert consensus(noise).pdf_units_per_real_foot is None
    assert consensus([r(10, 180), r(12, 216)]).pdf_units_per_real_foot is None, "two readings are not enough"


def _dim(page, x0, x1, y, text):
    """A horizontal dimension: line, 45-degree ticks at both ends, string above."""
    page.draw_line((x0 - 6, y), (x1 + 6, y), width=0.5)
    for x in (x0, x1):
        page.draw_line((x - 3, y + 3), (x + 3, y - 3), width=0.5)
        page.draw_line((x, y - 12), (x, y + 12), width=0.5)  # extension line
    page.insert_text(((x0 + x1) / 2 - 12, y - 4), text, fontsize=8)


def _sheet(units_per_foot: float, label: str, rotate: int = 0) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    y = 200
    for feet, text in ((10, "10'-0\""), (15.5, "15'-6\""), (22, "22'-0\""), (8.25, "8'-3\""), (31, "31'")):
        _dim(page, 200, 200 + feet * units_per_foot, y, text)
        y += 60
    page.insert_text((2300, 1650), label, fontsize=9)
    if rotate:
        page.set_rotation(rotate)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("rotate", [0, 90, 270])
def test_printed_dimensions_give_the_scale_the_sheet_is_drawn_at(rotate):
    """The label says 1/4" but the plan was enlarged to 34.6 units/ft (ID-0)."""
    s = analyze_drawing(_sheet(34.6, "SCALE: 1/4\" = 1'-0\"", rotate)).scales[0]
    assert s.pdf_units_per_real_foot == pytest.approx(18.0)       # what the label claims
    assert s.measured_ppf == pytest.approx(34.6, rel=0.01)        # what the drawing measures
    assert s.measured_agreeing == 5


def test_a_sheet_with_no_label_takes_the_measured_scale():
    s = analyze_drawing(_sheet(18.0, "")).scales[0]
    assert s.confidence == "measured"
    assert s.pdf_units_per_real_foot == pytest.approx(18.0, rel=0.01)
    assert s.scale_label == "1/4\" = 1'-0\""


# ── step 3: walls drawn as solid fills ───────────────────────────────────


def _triangles(page, x0, y0, x1, y1, colour):
    """A rectangle filled as two triangles, the way the PostScript print cut it."""
    for pts in (((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))):
        page.draw_polyline(pts, color=None, fill=colour, closePath=True)


def _fill_sheet() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    grey = (0.8, 0.8, 0.8)
    # An L-shaped 9-unit (6" at 1/4") wall: 400 along, then 300 down.
    _triangles(page, 300, 300, 700, 309, grey)
    _triangles(page, 691, 309, 700, 600, grey)
    # A separate 15-unit wall, 200 long.
    _triangles(page, 1200, 900, 1400, 915, grey)
    # Lettering printed as fills: thin black strokes.
    for i in range(300):
        x, y = 1800 + (i % 30) * 8, 200 + (i // 30) * 14
        page.draw_rect(pymupdf.Rect(x, y, x + 1.2, y + 7), color=None, fill=(0, 0, 0))
    # A hairline rule drawn as a fill.
    page.draw_rect(pymupdf.Rect(100, 1500, 1500, 1501), color=None, fill=grey)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_walls_drawn_as_fills_are_measured_on_their_centre_line():
    walls = sorted((c for c in analyze_drawing(_fill_sheet()).candidates if c.classification == "wall_filled"),
                   key=lambda c: -c.total_length_pdf_units)
    assert len(walls) == 2, [(c.total_length_pdf_units, c.stroke_width) for c in walls]
    l_wall, short = walls
    assert l_wall.stroke_width == pytest.approx(9, abs=0.5)
    assert l_wall.total_length_pdf_units == pytest.approx(400 + 291, rel=0.03)
    assert short.stroke_width == pytest.approx(15, abs=0.5)
    assert short.total_length_pdf_units == pytest.approx(200, rel=0.03)
    assert l_wall.paths, "the page needs the wall's pieces to highlight it"


def test_no_filled_walls_on_a_sheet_whose_plan_is_a_picture():
    wall = P.FilledWall(page=1, bbox=(300, 300, 700, 600), length=691.0, thickness=9.0, colour=(0.8,) * 3)
    page = PageGeometry(page=1, width=W, height=H, rotation=0, text_run_count=0, vector_path_count=0,
                        dimension_count=0, image_count=1, raster_regions=[(0, 0, W, H)], raster_plan=True)
    ex = DrawingExtract(document_id="t", page_count=1, is_vector=True, pages=[page], filled_walls=[wall])
    assert P.classify(ex) == []


# ── diagonals chain by their real end points ─────────────────────────────


def _diag(x0, y0, x1, y1):
    return VectorPath(page=1, bbox=(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), op_type="stroke",
                      length=((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, closed=False, stroke_width=1.0,
                      item_count=1, p0=(x0, y0), p1=(x1, y1), straight=True)


def test_straight_diagonals_chain_by_where_they_really_meet():
    """A V of two diagonals is one run; a diagonal elsewhere is another. They
    used to be one group, and a bbox corner cannot tell a / from a \\."""
    v = [_diag(100, 100, 300, 300), _diag(300, 300, 500, 100)]
    far = _diag(1500, 900, 1700, 1100)
    page = PageGeometry(page=1, width=W, height=H, rotation=0, text_run_count=0, vector_path_count=3,
                        dimension_count=0, image_count=0)
    cands = P.classify(DrawingExtract(document_id="t", page_count=1, is_vector=True, pages=[page],
                                      paths=v + [far]))
    lengths = sorted(c.total_length_pdf_units for c in cands)
    assert lengths == pytest.approx([282.8, 565.7], abs=0.2)
