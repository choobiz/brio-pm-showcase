"""Build synthetic DXF fixtures with KNOWN ground truth for the CAD takeoff.

There is no real DXF in the repo and no test for `cad_extractor`, so every claim
about what the extractor does has until now been read off the source rather than
measured. These fixtures make the extractor falsifiable: each file has an exact
expected answer, written next to it.

Two variants, geometrically identical, differing only in $INSUNITS:
  imperial.dxf  — $INSUNITS = 1 (inches)
  metric.dxf    — $INSUNITS = 4 (millimetres)

Ground truth (both files describe the same real building):
  doors                3   (2 x "DR-36x80", 1 x "DR-30x80")
  windows              2   (both "WIN-48x36")
  plumbing fixtures    1   (toilet)
  appliances           1   (range)
  furniture            1   -> must be EXCLUDED from the takeoff
  markers              1   -> must be EXCLUDED
  wall centreline      10.0 ft   (two parallel lines 6 in apart, 120 in long)
  floor area           100.0 sf  (a 120 in x 120 in A-AREA polyline)

Door/window sizes live in block ATTRIBUTES (width/height), which is where CAD
actually stores them — this is what the extractor must learn to read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import ezdxf

OUT = Path(__file__).parent

# Ground truth, asserted by the tests. Lengths in FEET, areas in SQUARE FEET.
GROUND_TRUTH = {
    "doors": 3,
    "windows": 2,
    "plumbing_fixture": 1,
    "appliance": 1,
    "wall_lf": 10.0,
    "floor_area_sf": 100.0,
    "door_sizes": {"36x80": 2, "30x80": 1},
    "window_sizes": {"48x36": 2},
    "excluded": {"furniture", "marker"},
}

# One inch expressed in each unit system. Every coordinate below is authored in
# inches and multiplied by this, so the two files are the SAME building.
SCALE = {"imperial": 1.0, "metric": 25.4}
INSUNITS = {"imperial": 1, "metric": 4}


def _def_block(doc, name: str, w: str, h: str) -> None:
    """A block carrying WIDTH/HEIGHT attribute definitions, as real CAD does."""
    blk = doc.blocks.new(name=name)
    blk.add_line((0, 0), (10, 0))
    blk.add_attdef("WIDTH", (0, 0), dxfattribs={"height": 1, "invisible": 1})
    blk.add_attdef("HEIGHT", (0, 2), dxfattribs={"height": 1, "invisible": 1})


def _insert(msp, name, at, layer, w, h, s: float):
    ref = msp.add_blockref(name, (at[0] * s, at[1] * s), dxfattribs={"layer": layer})
    ref.add_auto_attribs({"WIDTH": w, "HEIGHT": h})
    return ref


def build(variant: str) -> Path:
    s = SCALE[variant]
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = INSUNITS[variant]
    msp = doc.modelspace()

    for layer in ("A-DOOR", "A-GLAZ", "P-SANR-FIXT", "M-EQPM", "I-FURN",
                  "A-WALL", "A-AREA", "A-ANNO"):
        doc.layers.add(layer)

    for nm, w, h in (("DR-36x80", "36", "80"), ("DR-30x80", "30", "80"),
                     ("WIN-48x36", "48", "36"), ("TOILET", "", ""),
                     ("RANGE", "", ""), ("CHAIR", "", ""), ("NORTH-ARROW", "", "")):
        _def_block(doc, nm, w, h)

    # Openings — all inside the same cluster so scope detection keeps them.
    _insert(msp, "DR-36x80", (10, 10), "A-DOOR", "36", "80", s)
    _insert(msp, "DR-36x80", (10, 30), "A-DOOR", "36", "80", s)
    _insert(msp, "DR-30x80", (10, 50), "A-DOOR", "30", "80", s)
    _insert(msp, "WIN-48x36", (30, 10), "A-GLAZ", "48", "36", s)
    _insert(msp, "WIN-48x36", (30, 30), "A-GLAZ", "48", "36", s)
    _insert(msp, "TOILET", (50, 10), "P-SANR-FIXT", "", "", s)
    _insert(msp, "RANGE", (50, 30), "M-EQPM", "", "", s)
    # Must be excluded from the takeoff:
    _insert(msp, "CHAIR", (70, 10), "I-FURN", "", "", s)
    _insert(msp, "NORTH-ARROW", (70, 30), "A-ANNO", "", "", s)

    # Wall: two parallel lines 6 in apart, 120 in long -> centreline 120 in = 10 ft
    msp.add_line((0 * s, 0 * s), (120 * s, 0 * s), dxfattribs={"layer": "A-WALL"})
    msp.add_line((0 * s, 6 * s), (120 * s, 6 * s), dxfattribs={"layer": "A-WALL"})

    # Area: 120 in x 120 in = 14400 in^2 = 100 sf
    msp.add_lwpolyline(
        [(0 * s, 0 * s), (120 * s, 0 * s), (120 * s, 120 * s), (0 * s, 120 * s)],
        close=True, dxfattribs={"layer": "A-AREA"})
    msp.add_text("100 sq ft", dxfattribs={"layer": "A-AREA", "height": 2 * s}
                 ).set_placement((20 * s, 60 * s))

    path = OUT / f"{variant}.dxf"
    doc.saveas(path)
    return path


if __name__ == "__main__":
    for v in ("imperial", "metric"):
        p = build(v)
        print(f"wrote {p}")
    print("\nground truth:", GROUND_TRUTH)
    sys.exit(0)
