"""How reviewed takeoff quantities reach the estimate.

Both rules here were wrong in a way that produced a plausible number rather
than an error, which is the failure mode that actually reaches a client."""
import pytest
from types import SimpleNamespace
from uuid import uuid4

from app.services.rate_reconcile import _takeoff_quantities


class _DB:
    """Answers both the override query and the document/building lookup."""

    def __init__(self, rows, docs=None):
        self._rows = rows
        self._docs = docs or {}

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._rows

    def execute(self, stmt, params=None):
        docs = [{"id": k, "filename": v} for k, v in self._docs.items()]

        class R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return docs

        return R()


def _row(qtype, value, doc=None, status="confirmed", unit="sf"):
    return SimpleNamespace(quantity_type=qtype, value=value, unit=unit,
                           source="cad_extract", status=status,
                           source_document_id=doc)


def test_sheets_sum_rather_than_overwrite():
    """Basement 800 sf + level 1 900 sf is a 1,700 sf house. Last-write-wins
    priced it at 900 and nothing in the output said a sheet had been dropped."""
    a, b = uuid4(), uuid4()
    q = _takeoff_quantities(_DB([_row("floor_area_sf", 800, a),
                                 _row("floor_area_sf", 900, b)]), uuid4())
    assert q["floor_area_sf"]["value"] == 1700.0


def test_a_later_row_for_the_same_sheet_supersedes_the_earlier_one():
    """Re-extracting one sheet must correct it, not double it."""
    a = uuid4()
    q = _takeoff_quantities(_DB([_row("floor_area_sf", 800, a),
                                 _row("floor_area_sf", 815, a)]), uuid4())
    assert q["floor_area_sf"]["value"] == 815.0


def test_an_excluded_quantity_does_not_price_the_estimate():
    """'Excluded' counts as resolved for the gate, so without a status filter an
    estimate passed the gate and then priced the number a reviewer struck out."""
    a, b = uuid4(), uuid4()
    q = _takeoff_quantities(_DB([_row("floor_area_sf", 800, a),
                                 _row("floor_area_sf", 900, b, status="excluded")]),
                            uuid4())
    assert q["floor_area_sf"]["value"] == 800.0


def test_a_hand_entered_total_replaces_the_sheet_sum():
    """A row with no source document is a person stating the project total."""
    a, b = uuid4(), uuid4()
    q = _takeoff_quantities(_DB([_row("floor_area_sf", 800, a),
                                 _row("floor_area_sf", 900, b),
                                 _row("floor_area_sf", 1650, None)]), uuid4())
    assert q["floor_area_sf"]["value"] == 1650.0


def test_the_same_drawing_in_two_formats_is_read_once():
    """Plan.dwg and Plan.dxf are one sheet. Since sheets now sum, reading both
    would report twice the building's floor area."""
    from app.services.drawing_analysis import _dedupe_formats

    docs = [{"id": 1, "filename": "Plan.dwg"}, {"id": 2, "filename": "Plan.dxf"},
            {"id": 3, "filename": "Site.dxf"}]
    keep, dupes = _dedupe_formats(docs)
    assert sorted(d["filename"] for d in keep) == ["Plan.dxf", "Site.dxf"]
    assert [d["filename"] for d in dupes] == ["Plan.dwg"]
    assert dupes[0]["duplicate_of"] == "Plan.dxf"


def test_distinct_sheets_are_not_treated_as_duplicates():
    from app.services.drawing_analysis import _dedupe_formats

    keep, dupes = _dedupe_formats([{"id": 1, "filename": "A1 Basement.dxf"},
                                   {"id": 2, "filename": "A2 Level 1.dxf"}])
    assert len(keep) == 2 and dupes == []


def test_rebuild_retires_quantities_from_a_now_skipped_duplicate():
    """An earlier pack read both formats and wrote a row for each. Sheets sum,
    so those stale rows would report twice the building. They are excluded, not
    deleted — the row keeps its history and nothing reviewed vanishes."""
    from app.services.drawing_analysis import _supersede

    seen = {}

    class _DB:
        def execute(self, stmt, params=None):
            seen["sql"] = " ".join(str(stmt).split())
            seen["params"] = params
            return type("R", (), {"rowcount": 7})()

    n = _supersede(_DB(), uuid4(), {"id": uuid4(), "duplicate_of": "Plan.dxf"})
    assert n == 7
    assert "status = 'excluded'" in seen["sql"]
    assert "source = 'cad_extract'" in seen["sql"]        # never touches manual rows
    assert "Plan.dxf" in seen["params"]["note"]


def test_a_roof_does_not_multiply_by_the_number_of_sheets():
    """A building has one roof however many storeys are drawn. Summing gave the
    Maple set 4,990 sf of roof over a building whose largest storey is
    1,415 — the same "sheets sum" rule that is correct for floor area."""
    docs = [uuid4() for _ in range(4)]
    rows = [_row("roof_area_sf", v, d)
            for v, d in zip((1157.4, 1415.1, 1353.0, 1064.5), docs)]
    q = _takeoff_quantities(_DB(rows), uuid4())
    assert q["roof_area_sf"]["value"] == 1415.1
    assert q["roof_area_sf"]["aggregation"] == "envelope"
    assert q["roof_area_sf"]["sheets"] == 4


def test_floor_area_still_sums_across_storeys():
    """The two rules have to coexist: storeys accumulate, envelopes do not."""
    a, b = uuid4(), uuid4()
    q = _takeoff_quantities(
        _DB([_row("floor_area_sf", 1415.1, a), _row("floor_area_sf", 1353.0, b)]),
        uuid4())
    assert q["floor_area_sf"]["value"] == pytest.approx(2768.1)
    assert q["floor_area_sf"]["aggregation"] == "sum"


def test_perimeter_does_not_sum_across_the_storeys_of_one_building():
    a, b = uuid4(), uuid4()
    q = _takeoff_quantities(
        _DB([_row("perimeter_lf", 120.1, a, unit="lf"),
             _row("perimeter_lf", 118.4, b, unit="lf")]), uuid4())
    assert q["perimeter_lf"]["value"] == 120.1


def test_each_building_gets_its_own_roof():
    """Three duplexes on one site have three roofs. Taking one global maximum
    reported Maple as having 1,415 sf of roof across all three; summing
    every sheet reported 4,990. Largest WITHIN a building, summed ACROSS."""
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    docs = {
        a: "107 Maple-Coordination Set-Sheet - A6 - BASEMENT FLOOR PLAN.dwg",
        b: "107 Maple-Coordination Set-Sheet - A7 - LEVEL 1 FLOOR PLAN.dwg",
        c: "109 Maple--Sheet - A6 - BASEMENT _ LEVEL 1 FLOOR PLANS.dwg",
        d: "111 Maple-Sheet - A6 - BASEMENT _ LEVEL 1 FLOOR PLANS.dwg",
    }
    rows = [_row("roof_area_sf", 1415.1, a), _row("roof_area_sf", 1353.0, b),
            _row("roof_area_sf", 1064.5, c), _row("roof_area_sf", 1157.4, d)]
    q = _takeoff_quantities(_DB(rows, docs), uuid4())
    # 107 takes its larger storey; 109 and 111 contribute their own.
    assert q["roof_area_sf"]["value"] == pytest.approx(1415.1 + 1064.5 + 1157.4)
    assert q["roof_area_sf"]["buildings"] == 3
    assert q["roof_area_sf"]["sheets"] == 4


def test_two_storeys_of_ONE_building_still_give_one_roof():
    """The half of the rule that the global maximum already got right must not
    regress: a basement and a level 1 of the same house share a roof."""
    a, b = uuid4(), uuid4()
    docs = {a: "1234 Example - Basement.dxf", b: "1234 Example - Level 1.dxf"}
    q = _takeoff_quantities(
        _DB([_row("roof_area_sf", 670.1, a), _row("roof_area_sf", 640.0, b)], docs),
        uuid4())
    assert q["roof_area_sf"]["value"] == 670.1
    assert q["roof_area_sf"]["buildings"] == 1


def test_perimeters_add_across_buildings():
    """Three foundations are three perimeters of footing to dig and pour."""
    a, b = uuid4(), uuid4()
    docs = {a: "107 Maple - A6.dwg", b: "109 Maple - A6.dwg"}
    q = _takeoff_quantities(
        _DB([_row("perimeter_lf", 120.1, a, unit="lf"),
             _row("perimeter_lf", 118.4, b, unit="lf")], docs), uuid4())
    assert q["perimeter_lf"]["value"] == pytest.approx(238.5)


def test_a_sheet_with_no_civic_number_is_one_default_building():
    """Most sets are one building and name their sheets by number alone."""
    a, b = uuid4(), uuid4()
    docs = {a: "A6 - BASEMENT.dwg", b: "A7 - LEVEL 1.dwg"}
    q = _takeoff_quantities(
        _DB([_row("roof_area_sf", 900.0, a), _row("roof_area_sf", 850.0, b)], docs),
        uuid4())
    assert q["roof_area_sf"]["value"] == 900.0
    assert q["roof_area_sf"]["buildings"] == 1
