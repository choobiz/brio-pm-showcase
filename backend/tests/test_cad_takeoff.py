"""Falsifiable tests for the CAD quantity takeoff.

Run:  python -m pytest tests/test_cad_takeoff.py -v
or:   python tests/test_cad_takeoff.py        (prints a report, no pytest needed)

Every assertion is against a fixture whose correct answer is known exactly
(see tests/fixtures/make_takeoff_dxf.py).
"""
from __future__ import annotations

from sqlalchemy import text as _T

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.drawing.cad_extractor import run_takeoff  # noqa: E402
from tests.fixtures.make_takeoff_dxf import GROUND_TRUTH  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
GT = GROUND_TRUTH


def _counts(t, category: str) -> int:
    return sum((t.counts.get(category) or {}).values())


def _report(variant: str) -> dict:
    t = run_takeoff(FIX / f"{variant}.dxf")
    return {
        "units": t.units,
        "doors": _counts(t, "door"),
        "windows": _counts(t, "window"),
        "plumbing_fixture": _counts(t, "plumbing_fixture"),
        "appliance": _counts(t, "appliance"),
        "furniture_leaked": _counts(t, "furniture"),
        "wall_lf": round(sum(t.wall_lf.values()), 1),
        "floor_area_sf": t.derived.get("floor_area_sf"),
        "areas_sf": t.areas_sf,
        "sizes": getattr(t, "sizes", None),
        "warnings": t.warnings,
    }


# ── counts: these should already pass ────────────────────────────────────────

def test_imperial_counts():
    r = _report("imperial")
    assert r["doors"] == GT["doors"], f"doors {r['doors']} != {GT['doors']}"
    assert r["windows"] == GT["windows"], f"windows {r['windows']} != {GT['windows']}"
    assert r["plumbing_fixture"] == GT["plumbing_fixture"]
    assert r["appliance"] == GT["appliance"]


def test_furniture_and_markers_excluded():
    r = _report("imperial")
    assert r["furniture_leaked"] == 0, "furniture must not enter the takeoff"


# ── measurement: imperial ────────────────────────────────────────────────────

def test_imperial_wall_length():
    r = _report("imperial")
    assert abs(r["wall_lf"] - GT["wall_lf"]) < 0.2, \
        f"wall {r['wall_lf']} lf != {GT['wall_lf']} lf"


def test_imperial_floor_area():
    r = _report("imperial")
    assert r["floor_area_sf"] is not None, "no floor area derived"
    assert abs(r["floor_area_sf"] - GT["floor_area_sf"]) < 1.0, \
        f"floor area {r['floor_area_sf']} sf != {GT['floor_area_sf']} sf"


# ── measurement: metric — the same building, so the same answers ─────────────

def test_metric_wall_length_matches_imperial():
    r = _report("metric")
    assert abs(r["wall_lf"] - GT["wall_lf"]) < 0.2, \
        f"metric wall {r['wall_lf']} lf != {GT['wall_lf']} lf — units not applied"


def test_metric_floor_area_matches_imperial():
    r = _report("metric")
    assert r["floor_area_sf"] is not None
    assert abs(r["floor_area_sf"] - GT["floor_area_sf"]) < 1.0, \
        f"metric floor area {r['floor_area_sf']} sf != {GT['floor_area_sf']} sf — units not applied"


# ── sizes from block attributes ──────────────────────────────────────────────

def test_door_sizes_from_attributes():
    r = _report("imperial")
    assert r["sizes"], "no sizes extracted — block ATTRIBUTES not read"
    got = {k: v for k, v in (r["sizes"].get("door") or {}).items()}
    assert got == GT["door_sizes"], f"door sizes {got} != {GT['door_sizes']}"


def test_window_sizes_from_attributes():
    r = _report("imperial")
    assert r["sizes"], "no sizes extracted — block ATTRIBUTES not read"
    got = {k: v for k, v in (r["sizes"].get("window") or {}).items()}
    assert got == GT["window_sizes"], f"window sizes {got} != {GT['window_sizes']}"


if __name__ == "__main__":
    for v in ("imperial", "metric"):
        print(f"\n=== {v} ===")
        for k, val in _report(v).items():
            print(f"  {k:20} {val}")
    print("\nground truth:", GT)


# ── portability: a client whose office does NOT use AIA layering ─────────────

def _nonstandard_dxf(tmp: Path) -> Path:
    """Same building, an office-specific layer scheme, and block names that give
    the name-fallback nothing to work with.

    `tmp` must be a TEMPORARY directory. Both callers used to pass the fixtures
    directory, so every test run rewrote a version-controlled file — the diff was
    only DXF header noise ($TDCREATE, $FINGERPRINTGUID), which is exactly why it
    survived: it looked like something a person had done rather than a test
    writing into the repo.
    """
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for lay in ("OPENING-SWING", "OPENING-FIXED", "SANITARY"):
        doc.layers.add(lay)
    for nm in ("TYPE-A", "TYPE-B", "TYPE-C"):
        blk = doc.blocks.new(name=nm)
        blk.add_line((0, 0), (10, 0))
    msp.add_blockref("TYPE-A", (10, 10), dxfattribs={"layer": "OPENING-SWING"})
    msp.add_blockref("TYPE-A", (10, 30), dxfattribs={"layer": "OPENING-SWING"})
    msp.add_blockref("TYPE-B", (30, 10), dxfattribs={"layer": "OPENING-FIXED"})
    msp.add_blockref("TYPE-C", (50, 10), dxfattribs={"layer": "SANITARY"})
    out = tmp / "nonstandard.dxf"
    doc.saveas(out)
    return out


def test_nonstandard_layers_are_missed_without_a_map(tmp_path):
    """Falsifying check: prove the default map genuinely fails here, so the
    custom-map test below is meaningful rather than vacuous."""
    path = _nonstandard_dxf(tmp_path)
    t = run_takeoff(path)
    assert _counts(t, "door") == 0, "expected the AIA default to miss these layers"
    assert _counts(t, "window") == 0


def test_nonstandard_layers_with_custom_map(tmp_path):
    path = _nonstandard_dxf(tmp_path)
    t = run_takeoff(path, layer_map={
        "OPENING-SWING": "door",
        "OPENING-FIXED": "window",
        "SANITARY": "plumbing_fixture",
    })
    assert _counts(t, "door") == 2, f"doors {_counts(t, 'door')} != 2"
    assert _counts(t, "window") == 1
    assert _counts(t, "plumbing_fixture") == 1


def test_derived_provenance_strings_do_not_break_override_writing():
    """`derived` carries provenance strings next to the numbers —
    floor_area_source, geom_area_source. takeoff_to_overrides compared every
    value to 0, which raises on a string; the error was swallowed per sheet, so
    a project reported '0 sheets read' with no quantities and no visible cause."""
    from app.services.drawing.cad_extractor import Takeoff, takeoff_to_overrides

    t = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
    t.counts = {"door": {"D1": 3}}
    t.derived = {"floor_area_sf": 670.1, "floor_area_source": "measured_rooms",
                 "geom_area_source": "raster_envelope", "views": 2}

    written = []

    class _DB:
        def query(self, *a, **k):
            return self
        def filter_by(self, **k):
            return self
        def one_or_none(self):
            return None
        def add(self, row):
            written.append(row)
        def flush(self):
            pass

    n = takeoff_to_overrides(_DB(), "p1", t)
    assert n >= 2                                  # the door count and the area
    kinds = {r.quantity_type for r in written}
    assert "floor_area_sf" in kinds
    assert "floor_area_source" not in kinds        # a string is not a quantity


def test_repeat_extraction_of_the_same_document_updates_rather_than_duplicates(db_session):
    """The session runs autoflush=False, so a pending row is invisible to the
    next lookup. Re-running the extraction in one session used to re-add every
    candidate and die on ix_quantity_overrides_doc_candidate at commit, losing
    the entire pack. Idempotency is keyed on (document, candidate) — the same
    pair the unique index uses."""
    from uuid import uuid4
    from app.models.quantity_override import QuantityOverride
    from app.services.drawing.cad_extractor import Takeoff, takeoff_to_overrides

    db = db_session
    pid, doc_a, doc_b = uuid4(), uuid4(), uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, 'Takeoff dedupe', :s, 'preconstruction')"),
               {"i": str(pid), "s": f"takeoff-dedupe-{str(pid)[:8]}"})
    for d, fn in ((doc_a, "Plan.dxf"), (doc_b, "Plan.dwg")):
        db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, phase) "
                      "values (:i, :p, :f, :k, 'drawing', 'design')"),
                   {"i": str(d), "p": str(pid), "f": fn, "k": f"t/{fn}"})

    def _t(n):
        t = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
        t.counts = {"window": {"W1": n}}
        return t

    takeoff_to_overrides(db, pid, _t(7), document_id=doc_a)
    takeoff_to_overrides(db, pid, _t(7), document_id=doc_a)   # same session, again
    takeoff_to_overrides(db, pid, _t(4), document_id=doc_b)   # a second sheet
    db.commit()

    rows = db.query(QuantityOverride).filter_by(
        project_id=pid, source_candidate_key="cad:window_count").all()
    by_doc = {r.source_document_id: float(r.value) for r in rows}
    assert len(rows) == 2                  # one per sheet, not three
    assert by_doc[doc_a] == 7.0            # sheet B did not overwrite sheet A
    assert by_doc[doc_b] == 4.0


# ── size parsing: the BIM-name failure modes ────────────────────────────────
# These lock in a defect found 2026-10-01 by re-running the engine against the
# 1234 Example production file. Counts were right; sizes were confidently wrong.

from app.services.drawing.cad_extractor import _size_from_name, _fmt_in  # noqa: E402


def _sz(name):
    p = _size_from_name(name)
    return f"{_fmt_in(p[0])}x{_fmt_in(p[1])}" if p else None


def test_revit_type_beats_family_name():
    """`Family - Type` where the FAMILY is itself named after a size.

    On 1234 Example the family is literally called `30x84`, so taking the first
    dimension pair in the string reported all seven windows as 30x84 and collapsed
    three real sizes into one wrong one. The rightmost segment is the type, which
    is the thing actually placed.
    """
    assert _sz("30x84 - Fixed - 36x24-7393081-L1") == "36x24"
    assert _sz("30x84 - Fixed - 8_x8_-7399745-L1") == "96x96"
    assert _sz("30x84 - Fixed - 8_x4_-7404529-L1") == "96x48"


def test_feet_notation_with_mangled_foot_mark():
    """`18_ x 8_` is 18'x8' — an export mangled the apostrophes to underscores.

    This previously matched nothing, so the overhead door carried NO size at all
    and the omission was silent.
    """
    assert _sz("Overhead - 8 Panels - 18_ x 8_-7393378-L1") == "216x96"


def test_ambiguous_underscore_resolves_by_plausibility():
    """`7_x80_` uses the same mark for feet AND inches.

    Read naively that is 7ft x 80ft. 80 ft is not an opening, so the second mark is
    inches: a 7'-0" x 80" closet slider.
    """
    assert _sz("Single Closet Slider - Deco - 7_x80_-6981726-L1") == "84x80"


def test_explicit_marks_are_never_second_guessed():
    assert _sz("Casement - 3'-6\"x5'-0\"") == "42x60"
    assert _sz("Awning - 2' x 4'") == "24x48"


def test_bare_numbers_stay_inches_and_no_dimensions_returns_none():
    assert _sz("DR-36x80") == "36x80"
    assert _sz("Fixed - 1200x900") == "1200x900"
    assert _sz("No Dimensions Here") is None


def test_every_counted_opening_is_sized_or_warned():
    """A count without a size cannot be priced, and absence is invisible.

    Door sizes totalled 4 against a door count of 5 and nothing noticed, because
    the only way to see it was to subtract one from the other.
    """
    from app.services.drawing.cad_extractor import Takeoff, _check_size_coverage

    t = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
    t.counts = {"door": {"a": 3, "b": 2}}          # 5 counted
    t.sizes = {"door": {"36x80": 3, "30x80": 1}}   # 4 sized
    _check_size_coverage(t)
    assert any("1 of 5 doors carry NO size" in w for w in t.warnings)

    t2 = Takeoff(units="inches", scope_boundary=None, scope_axis="x")
    t2.counts = {"door": {"a": 2}}
    t2.sizes = {"door": {"36x80": 2}}
    _check_size_coverage(t2)
    assert not t2.warnings
