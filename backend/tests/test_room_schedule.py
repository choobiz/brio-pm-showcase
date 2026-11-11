"""Room-schedule extraction — the architect's own tags, read as data."""
import ezdxf
import pytest

from app.services.drawing.room_schedule import read_schedule, schedule_summary


def _doc():
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 1
    return doc


def _tag(msp, x, y, name, area, layer="A-AREA-IDEN"):
    msp.add_mtext(f"{area} SF", dxfattribs={"layer": layer}).set_location((x, y))
    if name is not None:
        msp.add_mtext(name, dxfattribs={"layer": layer}).set_location((x - 5, y + 6))


def test_reads_name_and_area_per_room():
    doc = _doc(); msp = doc.modelspace()
    _tag(msp, 0, 0, "LIVING ROOM", "198.58")
    _tag(msp, 300, 0, "BEDROOM", "98.46")
    _tag(msp, 600, 0, "BATHROOM", "44.58")
    rooms = read_schedule(msp)
    assert len(rooms) == 3
    assert {r.name for r in rooms} == {"LIVING ROOM", "BEDROOM", "BATHROOM"}
    assert sum(r.area_sf for r in rooms) == pytest.approx(341.62)


def test_summary_rolls_up_by_name():
    """Two bedrooms are two bedrooms — a takeoff prices the roll-up, not the list."""
    doc = _doc(); msp = doc.modelspace()
    _tag(msp, 0, 0, "BEDROOM", "100")
    _tag(msp, 300, 0, "BEDROOM", "120")
    _tag(msp, 600, 0, "KITCHEN", "80")
    s = schedule_summary(read_schedule(msp))
    assert s["rooms"] == 3 and s["named"] == 3
    assert s["total_sf"] == pytest.approx(300.0)
    assert s["by_name"]["BEDROOM"] == {"count": 2, "area_sf": 220.0}


def test_zoning_notes_are_not_rooms():
    """'120 + 549 = 669 sq ft' and '400 allowed' state areas but describe no room.
    Counting them inflates the floor area of a 400 sf garage to 1069."""
    doc = _doc(); msp = doc.modelspace()
    _tag(msp, 0, 0, "GARAGE", "399.6")
    for junk in ("120 + 549 = 669 SF", "400 SF allowed", "35% lot coverage",
                 "TOTAL 669 SF"):
        msp.add_mtext(junk, dxfattribs={"layer": "A-AREA-IDEN"}).set_location((900, 0))
    rooms = read_schedule(msp)
    assert len(rooms) == 1 and rooms[0].name == "GARAGE"


def test_a_bare_area_off_the_tag_layer_is_ignored():
    doc = _doc(); msp = doc.modelspace()
    msp.add_mtext("250 SF", dxfattribs={"layer": "G-ANNO-TEXT"}).set_location((0, 0))
    assert read_schedule(msp) == []


def test_a_named_area_off_the_tag_layer_is_still_read():
    """Layer standards are a convention. An office with its own scheme still has
    rooms, and the pattern — an area figure with a name beside it — identifies them."""
    doc = _doc(); msp = doc.modelspace()
    msp.add_mtext("250 SF", dxfattribs={"layer": "MY-ROOMS"}).set_location((0, 0))
    msp.add_mtext("STUDIO", dxfattribs={"layer": "MY-ROOMS"}).set_location((-5, 6))
    rooms = read_schedule(msp)
    assert len(rooms) == 1 and rooms[0].name == "STUDIO"


def test_view_clipping_keeps_each_plan_separate():
    """A sheet showing a basement beside a level 1 must not pool their schedules."""
    doc = _doc(); msp = doc.modelspace()
    _tag(msp, 0, 0, "REC ROOM", "200")
    _tag(msp, 5000, 0, "LIVING ROOM", "300")
    left = read_schedule(msp, inside=lambda p: p[0] < 2500)
    right = read_schedule(msp, inside=lambda p: p[0] >= 2500)
    assert [r.name for r in left] == ["REC ROOM"]
    assert [r.name for r in right] == ["LIVING ROOM"]


def test_metric_drawing_pairs_names_at_the_same_physical_distance():
    doc = _doc(); msp = doc.modelspace()
    MM = 25.4
    msp.add_mtext("198.58 SF", dxfattribs={"layer": "A-AREA-IDEN"}).set_location((0, 0))
    msp.add_mtext("LIVING ROOM", dxfattribs={"layer": "A-AREA-IDEN"}).set_location(
        (-5 * MM, 6 * MM))
    rooms = read_schedule(msp, unit_scale=1.0 / MM)
    assert len(rooms) == 1 and rooms[0].name == "LIVING ROOM"


def test_no_tags_yields_no_schedule():
    """The marine-prospect case: a drawing with no schedule of any kind. The caller must
    fall back to measured geometry rather than receive an empty answer as a total."""
    doc = _doc(); msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "A-WALL"})
    assert read_schedule(msp) == []


def test_a_datum_is_not_a_room_name():
    """'FF 214.00' is the finished-floor elevation printed beside the garage tag.
    Named by the nearest-text rule, a 400 sf garage becomes a room called FF 214.00."""
    doc = _doc(); msp = doc.modelspace()
    msp.add_mtext("399.6 SF", dxfattribs={"layer": "A-AREA-IDEN"}).set_location((0, 0))
    msp.add_mtext("FF 214.00", dxfattribs={"layer": "A-AREA-IDEN"}).set_location((-5, 6))
    rooms = read_schedule(msp)
    assert len(rooms) == 1
    assert rooms[0].name is None, rooms[0].name


def test_a_storey_total_is_not_a_room():
    """A total printed in room-tag style doubles the floor area if counted."""
    doc = _doc(); msp = doc.modelspace()
    for i, (nm, a) in enumerate([("LIVING ROOM", 300), ("BEDROOM", 200),
                                 ("KITCHEN", 100)]):
        _tag(msp, i * 400, 0, nm, str(a))
    _tag(msp, 2000, 0, "LEVEL 1", "600")          # == 300 + 200 + 100
    s = schedule_summary(read_schedule(msp))
    assert s["total_sf"] == pytest.approx(600.0), s
    assert s["rooms"] == 3
