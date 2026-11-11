"""The drawing shown beside the numbers.

A reviewer confirming "7 windows" from the digit alone is assenting, not
reviewing. These check that what gets drawn actually corresponds to what was
counted, and that the frame is the building rather than the sheet."""
import re

import pytest

from app.services.drawing.plan_svg import _polyline, render_view_svg


def test_svg_carries_no_script_or_external_reference():
    """It is injected into the page as HTML, so it must be inert."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           + _polyline([(0, 0), (10, 10)], "ln") + "</svg>")
    assert "<script" not in svg and "http://" not in svg.replace(
        "http://www.w3.org/2000/svg", "")


def test_a_drawing_that_will_not_read_answers_instead_of_raising(tmp_path):
    """A reviewer whose drawing will not render still has the numbers; the pane
    must say so rather than take the page down."""
    bad = tmp_path / "not-a-drawing.dxf"
    bad.write_text("this is not a DXF")
    with pytest.raises(Exception):
        render_view_svg(str(bad))


def test_polyline_rounds_and_stays_numeric():
    out = _polyline([(1.234, 5.678), (9.0, -2.5)], "hl")
    assert 'class="hl"' in out
    assert re.search(r'points="1\.2,5\.7 9\.0,-2\.5"', out)


def test_a_drawing_is_read_once_however_many_quantities_are_viewed(monkeypatch):
    """1234, 2026-10-20: each quantity clicked re-downloaded the DWG, converted
    it and re-ran the takeoff, about 15 s a click, and the pane read as broken.
    The read must happen once per stored file; a new file is a new read."""
    from app.services.drawing import plan_svg

    reads, fetches = [], []
    monkeypatch.setattr(plan_svg, "_read", lambda path: reads.append(path) or ("doc", "t"))
    monkeypatch.setattr(plan_svg, "_render", lambda doc, t, v, h, m: {"highlight": h})
    plan_svg._READ_CACHE.clear()

    def fetch():
        fetches.append(1)
        return "/tmp/x.dwg"

    for h in ("door_count", "window_count", "floor_area_sf", "door_count"):
        assert plan_svg.render_cached("doc-1:key-a", fetch, highlight=h) == {"highlight": h}
    assert len(reads) == 1 and len(fetches) == 1

    plan_svg.render_cached("doc-1:key-b", fetch, highlight="door_count")
    assert len(reads) == 2, "a re-upload (new storage key) must be read afresh"


def test_the_read_cache_is_bounded(monkeypatch):
    from app.services.drawing import plan_svg

    monkeypatch.setattr(plan_svg, "_read", lambda path: ("doc", "t"))
    monkeypatch.setattr(plan_svg, "_render", lambda *a: {})
    plan_svg._READ_CACHE.clear()
    for i in range(plan_svg._READ_CACHE_MAX + 3):
        plan_svg.render_cached(f"k{i}", lambda: "/tmp/x.dwg")
    assert len(plan_svg._READ_CACHE) == plan_svg._READ_CACHE_MAX
