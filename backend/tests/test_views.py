"""View segmentation — the failure mode that silently halved a building.

The old scope rule was single-axis: find the largest gap between element clusters
and keep one side. These fixtures encode both ways that was wrong.
"""
import ezdxf
import pytest

from app.services.drawing.views import detect_views


def _doc(units=1):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = units
    return doc


def _rect(msp, x, y, w, h, layer="A-WALL"):
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    for i in range(4):
        msp.add_line(pts[i], pts[i + 1], dxfattribs={"layer": layer})


def _plan(msp, x, y, w=300.0, h=200.0, s=1.0):
    """A plan with enough line-work to clear MIN_VIEW_SEGMENTS (20 segments)."""
    _rect(msp, x, y, w, h)
    for i in range(1, 5):
        px = x + i * w / 5
        msp.add_line((px, y), (px, y + h), dxfattribs={"layer": "I-WALL"})
        msp.add_line((px + 6 * s, y), (px + 6 * s, y + h), dxfattribs={"layer": "I-WALL"})
    for i in range(1, 5):
        py = y + i * h / 5
        msp.add_line((x, py), (x + w, py), dxfattribs={"layer": "I-WALL"})
        msp.add_line((x, py + 6 * s), (x + w, py + 6 * s), dxfattribs={"layer": "I-WALL"})


def test_two_plans_on_one_sheet_are_two_views_not_one_kept_and_one_discarded():
    """Basement and Level 1 side by side. The old rule kept one and dropped the
    other — 39 of 57 glazing blocks discarded on the real file."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    _plan(msp, 900, 0)          # 50 ft away
    views = detect_views(doc)
    assert len(views) == 2, [(v.bbox, v.source) for v in views]
    assert all(v.source == "cluster" for v in views), "must not fall through to 'whole'"
    lo = min(views, key=lambda v: v.bbox[0])
    hi = max(views, key=lambda v: v.bbox[0])
    assert lo.bbox[2] < hi.bbox[0], "views must not overlap"


def test_a_courtyard_inside_one_plan_does_not_split_the_building():
    """Two wings joined top and bottom around a 25 ft void. To a single-axis gap
    rule this is indistinguishable from two separate plans, and it split the real
    Maple 107 A7 building in half."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)                          # west wing
    _plan(msp, 600, 0)                        # east wing, 25 ft of void between
    for y in (0, 200):                        # the walls that make it ONE building
        msp.add_line((300, y), (600, y), dxfattribs={"layer": "A-WALL"})
        msp.add_line((300, y + 6), (600, y + 6), dxfattribs={"layer": "A-WALL"})
    views = detect_views(doc)
    assert len(views) == 1, f"split a single building into {len(views)}: " \
                            f"{[v.bbox for v in views]}"


def test_two_detached_buildings_stay_separate_even_when_close():
    """1234 Example: a house and a detached garage sit closer together than the
    wings of a courtyard plan. Distance cannot tell them apart — connectivity can."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)                          # house
    _plan(msp, 400, 0)                        # garage, 8 ft away, nothing joining them
    views = detect_views(doc)
    assert len(views) == 2, f"merged two buildings: {[v.bbox for v in views]}"


def test_element_blocks_are_not_cropped_out_of_their_own_view():
    """A door insert sits ON its wall; a view sized from line-work alone can crop
    it by a hair and lose the count."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    doc.blocks.new(name="DR").add_line((0, 0), (1, 0))
    msp.add_blockref("DR", (0, 100), dxfattribs={"layer": "A-DOOR"})     # on the wall
    msp.add_blockref("DR", (300, 100), dxfattribs={"layer": "A-DOOR"})   # far wall
    views = detect_views(doc)
    assert len(views) == 1
    v = views[0]
    assert v.contains((0, 100)) and v.contains((300, 100))


def test_a_drawing_with_no_aia_layers_still_yields_a_view():
    """View detection must not depend on layer names — an office with its own
    scheme would otherwise get no takeoff at all rather than a rougher one."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    for e in msp:
        e.dxf.layer = "MY-WALLS"
    views = detect_views(doc)
    assert len(views) == 1 and views[0].segment_count > 0


def test_annotation_layers_do_not_define_a_view():
    """Wall poche and wall tags are not wall. Feeding them in inflates the view."""
    doc = _doc()
    msp = doc.modelspace()
    _plan(msp, 0, 0)
    for i in range(40):                      # a title block far away, on annotation
        msp.add_line((5000 + i, 5000), (5000 + i, 5100), dxfattribs={"layer": "A-ANNO-TEXT"})
    views = detect_views(doc)
    assert all(v.bbox[2] < 4000 for v in views), [v.bbox for v in views]


def test_empty_drawing_yields_no_views():
    assert detect_views(_doc()) == []


def test_metric_drawing_uses_the_same_physical_thresholds():
    """The cluster gap is 30 ft. In millimetres that is 9144 units, not 360 — a
    threshold left in raw units splits a metric drawing on any 1.2 ft gap."""
    MM = 25.4
    doc = _doc(units=4)
    msp = doc.modelspace()
    _plan(msp, 0, 0, 300 * MM, 200 * MM, s=MM)
    _plan(msp, 900 * MM, 0, 300 * MM, 200 * MM, s=MM)
    views = detect_views(doc, unit_scale=1.0 / MM)
    assert len(views) == 2, [(v.bbox, v.source) for v in views]
