"""Cross-sheet reconciliation and room-to-trade mapping."""
import pytest

from app.services.drawing.project_takeoff import (
    building_key, classify_sheet, classify_storey, reconcile,
)
from app.services.drawing.room_trades import (
    estimated_perimeter_ft, room_type, schedule_to_trades,
)


class _T:
    def __init__(self, views):
        self.views = views


def _view(i, title, sched_sf, rooms, env, counts=None, by_name=None):
    return {
        "index": i, "title": title, "included": True,
        "room_schedule_summary": {"rooms": rooms, "named": rooms,
                                  "total_sf": sched_sf,
                                  "by_name": by_name or {}},
        "envelope_sf": env, "wall_lf": {"A-WALL": 100.0},
        "counts": counts or {"door": {"d": 5}}, "sizes": {},
    }


def test_two_storeys_on_separate_sheets_become_two_storeys():
    p = reconcile([
        ("107 Maple - A6 - BASEMENT FLOOR PLAN.dwg",
         _T([_view(0, "BASEMENT FLOOR PLAN", 1415.1, 18, 1293.2)])),
        ("107 Maple - A7 - LEVEL 1 FLOOR PLAN.dwg",
         _T([_view(0, "LEVEL 1 FLOOR PLAN", 1353.0, 11, 1847.4)])),
    ])
    assert len(p.storeys) == 2
    assert {s.storey for s in p.storeys} == {"basement", "level_1"}
    assert p.totals["schedule_sf"] == pytest.approx(2768.1)


def test_the_same_storey_on_two_sheets_is_not_counted_twice():
    """The failure this exists to prevent: a revised sheet doubling the building."""
    p = reconcile([
        ("107 A6 BASEMENT FLOOR PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1415.1, 18, 1293.2)])),
        ("107 A6 rev2 BASEMENT FLOOR PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1415.1, 18, 1293.2)])),
    ])
    assert len(p.storeys) == 1
    assert p.totals["schedule_sf"] == pytest.approx(1415.1)
    assert len(p.storeys[0].sources) == 2


def test_a_disagreement_between_sheets_is_reported_not_averaged():
    p = reconcile([
        ("107 A6 BASEMENT PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1415.1, 18, 1293.2)])),
        ("107 A6 rev2 BASEMENT PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1180.0, 15, 1293.2)])),
    ])
    assert len(p.storeys) == 1
    assert any("room schedule" in f and "apart" in f for f in p.findings), p.findings
    assert p.storeys[0].schedule_sf == pytest.approx(1415.1)   # never the average


def test_separate_buildings_are_kept_apart():
    """107 and 109 are two houses. Merging their basements by storey halves the job."""
    p = reconcile([
        ("107 Maple A6 BASEMENT PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1415.1, 18, 1293.2)])),
        ("109 Maple A6 BASEMENT PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1064.5, 11, 1091.4)])),
    ])
    assert len(p.storeys) == 2
    assert p.totals["buildings"] == 2
    assert p.totals["schedule_sf"] == pytest.approx(2479.6)


def test_sections_contribute_no_area():
    """A section's line-work is a building elevation, not a floor. Counting it adds
    a storey the project does not have."""
    p = reconcile([
        ("107 A6 BASEMENT PLAN.dwg", _T([_view(0, "BASEMENT PLAN", 1415.1, 18, 1293.2)])),
        ("107 A12 BUILDING SECTIONS.dwg", _T([_view(0, None, 900.0, 8, 900.0)])),
    ])
    assert p.totals["schedule_sf"] == pytest.approx(1415.1)
    assert any("section" in u for u in p.unplaced)


def test_a_view_that_names_no_storey_is_flagged():
    p = reconcile([("107 A6 plan.dwg", _T([_view(0, None, 500.0, 5, 520.0)]))])
    assert any("names no storey" in f for f in p.findings)


def test_sheet_and_storey_classification():
    assert classify_sheet("107 Maple A2 SITE PLAN.dwg") == "site_plan"
    assert classify_sheet("107 A14 BUILDING SECTIONS.dwg") == "section"
    assert classify_sheet("107 A6 BASEMENT FLOOR PLAN.dwg") == "floor_plan"
    assert classify_storey("BASEMENT FLOOR PLAN") == "basement"
    assert classify_storey(None, "109 A6 LEVEL 1 FLOOR PLANS.dwg") == "level_1"
    assert building_key("109 Maple--Sheet - A6.dwg") == "109"


# ── trade mapping ────────────────────────────────────────────────────────────

def test_a_bathroom_gets_wet_room_trades_and_a_bedroom_does_not():
    bath = {ln["item"] for d in schedule_to_trades(
        [{"name": "BATHROOM", "area_sf": 44.6}])["divisions"].values()
        for ln in d["lines"]}
    bed = {ln["item"] for d in schedule_to_trades(
        [{"name": "BEDROOM", "area_sf": 98.5}])["divisions"].values()
        for ln in d["lines"]}
    assert any("Toilet" in i for i in bath) and any("Waterproof" in i for i in bath)
    assert not any("Toilet" in i for i in bed) and not any("Waterproof" in i for i in bed)


def test_an_unfinished_room_gets_no_drywall_or_paint():
    """A garage is not painted. Finishing it inflates two divisions at once."""
    t = schedule_to_trades([{"name": "GARAGE", "area_sf": 193.8}])
    assert "06" not in t["divisions"] and "07" not in t["divisions"]
    assert any("Sealed concrete" in ln["item"] for ln in t["divisions"]["08"]["lines"])


def test_every_line_carries_a_basis_and_assumptions_are_not_measured():
    t = schedule_to_trades([{"name": "BATHROOM", "area_sf": 44.6}])
    for d in t["divisions"].values():
        for ln in d["lines"]:
            assert ln["basis"] in ("measured", "derived", "assumed")
    floor = [ln for ln in t["divisions"]["08"]["lines"] if "Tile —" in ln["item"]][0]
    assert floor["basis"] == "measured"
    toilet = [ln for ln in t["divisions"]["13"]["lines"]][0]
    assert toilet["basis"] == "assumed"
    assert any("ASSUMED" in a for a in t["assumptions"])


def test_measured_counts_beat_the_rule_of_thumb_and_the_gap_is_reported():
    t = schedule_to_trades(
        [{"name": "BEDROOM", "area_sf": 100}, {"name": "BATHROOM", "area_sf": 40},
         {"name": "KITCHEN", "area_sf": 90}],
        measured_counts={"door": 22, "window": 2, "plumbing_fixture": 10})
    assert any("22 drawn vs 3 implied" in c for c in t["cross_checks"]), t["cross_checks"]
    assert any(ln["basis"] == "measured" and "counted from the drawing" in ln["item"]
               for ln in t["divisions"]["05"]["lines"])


def test_an_unrecognised_room_is_named_not_silently_defaulted():
    t = schedule_to_trades([{"name": "SAUNA", "area_sf": 60}])
    assert t["unrecognised_rooms"] == ["SAUNA"]
    assert all("not recognised" in (ln["note"] or "")
               for d in t["divisions"].values() for ln in d["lines"])


def test_perimeter_estimate_is_sane():
    """A 100 sf room is about 41 lf around, not 20 and not 80."""
    assert 38 <= estimated_perimeter_ft(100.0) <= 45


def test_numbered_rooms_match_their_type():
    assert room_type("BEDROOM 02")[1] and room_type("BEDROOM 02")[0].floor == "Engineered floor"
    assert room_type("POWDER ROOM")[0].fixtures == ("Toilet", "Basin")


def test_a_sheet_naming_two_storeys_does_not_collapse_them_into_one():
    """'BASEMENT & LEVEL 1 FLOOR PLANS' names both. Matching the first pattern files
    the level 1 view under 'basement', one overwrites the other, and the building
    silently loses a floor — which is exactly what happened on Maple 109."""
    p = reconcile([
        ("109 Maple A6 - BASEMENT & LEVEL 1 FLOOR PLANS.dwg",
         _T([_view(0, None, 1064.5, 11, 1091.4), _view(1, None, 881.3, 11, 1149.5)])),
    ])
    assert len(p.storeys) == 2, [(s.storey, s.schedule_sf) for s in p.storeys]
    assert p.totals["schedule_sf"] == pytest.approx(1945.8)
    assert any("names 2 storeys" in f for f in p.findings), p.findings


def test_a_titled_view_still_wins_over_an_ambiguous_filename():
    p = reconcile([
        ("109 A6 - BASEMENT & LEVEL 1 FLOOR PLANS.dwg",
         _T([_view(0, "BASEMENT FLOOR PLAN", 1064.5, 11, 1091.4),
             _view(1, "LEVEL 1 FLOOR PLAN", 881.3, 11, 1149.5)])),
    ])
    assert {s.storey for s in p.storeys} == {"basement", "level_1"}


def _sched_view(i, title, rooms, env):
    """rooms: list of (name, area, x)."""
    return {
        "index": i, "title": title, "included": True,
        "room_schedule": [{"name": n, "area_sf": a, "at": [x, 0]} for n, a, x in rooms],
        "room_schedule_summary": {
            "rooms": len(rooms), "named": len(rooms),
            "total_sf": round(sum(a for _n, a, _x in rooms), 1), "by_name": {}},
        "envelope_sf": env, "wall_lf": {}, "counts": {}, "sizes": {},
    }


def test_a_side_by_side_duplex_is_recognised_as_two_dwellings():
    """107's plans show every room twice, mirrored about the party wall. Reporting
    that as one dwelling understates the unit count the whole estimate scales on."""
    mirrored = [("KITCHEN", 100, 0), ("LIVING ROOM", 200, 50), ("BATHROOM", 40, 100),
                ("BATHROOM", 40, 300), ("LIVING ROOM", 200, 350), ("KITCHEN", 100, 400)]
    p = reconcile([("107 A6 BASEMENT PLAN.dwg", _T([_sched_view(0, "BASEMENT PLAN",
                                                                mirrored, 800.0)]))])
    st = p.storeys[0]
    assert st.dwelling_units == 2
    assert st.mirrored_pct is not None and st.mirrored_pct >= 0.9
    assert p.totals["dwelling_units"] == 2


def test_a_single_dwelling_is_not_reported_as_a_duplex():
    single = [("KITCHEN", 128, 0), ("LIVING ROOM", 228, 60), ("GARAGE", 184, 120),
              ("DINING ROOM", 112, 180), ("OFFICE", 85, 240), ("STAIR", 85, 300)]
    p = reconcile([("109 A6 BASEMENT PLAN.dwg", _T([_sched_view(0, "BASEMENT PLAN",
                                                                single, 1091.4)]))])
    st = p.storeys[0]
    assert st.dwelling_units == 1
    assert st.mirrored_pct is not None and st.mirrored_pct < 0.5
