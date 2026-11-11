# CAD Quantity-Takeoff Engine — design and current state

**Status 2026-08-28.** Counts, sizes, gross envelope, perimeter and **per-room interior areas** are
all validated against a real architect DXF — interior rooms to within 0.9% of the architect's own
figures. See §6 for the method and its caveats.

Everything here is deterministic. **No LLM is involved in the CAD path**, by design: block references
are named entities, so counting doors is counting block references rather than classifying strokes.
Nothing can hallucinate a quantity.

---

## 1. Pipeline

```
DWG ──dwg2dxf──► DXF ──ezdxf──► inventory ──► scope to new work ──► extract ──► reconcile ──► estimate
```

| Stage | Module | Notes |
|---|---|---|
| Convert | `cad_extractor.load_doc` | `dwg2dxf` (LibreDWG 0.13.3, built into the API image). DXF native. |
| Inventory | `_inventory` | `$INSUNITS`, entity/layer census, extents |
| Scope | `_detect_scope` | largest cluster gap ≥ 10 ft; new work = side holding the area labels |
| Classify | `_classify` | layer first, block name second |
| Measure | `run_takeoff` | counts, sizes, wall LF, areas |
| Footprint | `floor_area.building_footprint` | snap → components → faces |
| Rooms | `room_areas.room_areas` | rasterise → close → label → centreline |
| Reconcile | `reconcile.py` | macro cross-check against the PDF permit set |

## 2. Units — every measurement converts through `unit_scale()`

`$INSUNITS` maps to inches: `inches 1 · feet 12 · mm 1/25.4 · cm 1/2.54 · m 39.37`. Unitless assumes
inches **and warns**.

This was a live defect until 2026-08-27: `/144` and `/12` were unconditional, and the scope-gap
threshold was a raw `120` "inches". A millimetre drawing therefore split on any 4-inch gap and
**silently dropped whole categories** — the metric fixture returned 0 doors, 0.0 wall LF and no floor
area, with no warning. Tolerances are now expressed in inches and divided by the unit scale, so they
stay physical.

## 3. Classification

Layer is authoritative, block name is the fallback — a block named `DOOR-36x80` on layer `0` is still
a door.

`DEFAULT_LAYER_MAP` follows AIA conventions (`A-DOOR*`, `A-GLAZ*`, `P-SANR-FIXT`, `M-EQPM`,
`I-FURN`), with `*` meaning prefix match. **It is overridable per drawing set** —
`run_takeoff(layer_map={...})` — because layer standards are a convention, not a guarantee. An office
using `OPENING-SWING` would otherwise classify to `other` and vanish silently.

Furniture and markers are excluded. Furniture used to leak into `counts`; `takeoff_summary` filtered
it out of the totals, which is precisely why it went unnoticed while `counts_detail` still carried it.

## 4. Sizes — the engine generates its own schedule

Openings are sized from **block ATTRIBUTES**, which is where CAD actually stores dimensions. Several
tag spellings are accepted (`WIDTH`/`W`/`WD`/`NOMINAL_WIDTH`/`ROUGH_WIDTH`, same for height), with a
fallback that parses the block name (`DR-36x80`).

Output is a generated schedule, not a count:

```
counts  {"door": 5, "window": 7, "plumbing_fixture": 2}
sizes   {"door": {"32x84": 1, "34x90": 2, "30x84": 1}, "window": {"30x84": 7}}
```

**This is the requirement for the marine-prospect engagement** — takeoff with no schedule or table of
contents, generating its own. A count of "5 doors" cannot be priced; `3 x 36x80 + 2 x 30x80` can.

*Coverage on the real file: windows 7/7, doors 4/5. One block carries neither attributes nor a
parseable name.*

## 5. Footprint — snap, split, faces

Real drawings do not contain the polygon you want. Measured on the garage: **zero closed polylines in
the entire file**, A-WALL has 14 open ends on the new-work side (walls are broken at every opening),
and the A-WALL bounding box gives 1001 sf against a real ~668.

1. **SNAP** — union-find over a spatial hash. CAD "touching" walls sit fractions of an inch apart; a
   round-to-grid would split points across cell borders, so the neighbourhood is checked 3x3.
2. **COMPONENTS** — split into disconnected networks *before* measuring. This is what gives a
   quantity provenance. On the garage the new-work side is two shapes, not one:

   | Component | Segments | Layers | Bbox | Meaning |
   |---|---|---|---|---|
   | 1 | 65 | A-WALL 34 + A-FLOR 31 | 26.0 x 38.6 ft | the wall network |
   | 2 | 20 | A-FLOR only | 28.1 x 40.1 ft | **the building outline, already closed** |

   Both span the same y-range, so this is **one view** — an earlier reading of "two stacked plan
   views merged" was wrong.
3. **BRIDGE** (only if a component will not close) — join loose ends within 60 in, but only when the
   connector runs roughly along one of the two walls, which distinguishes a doorway from two
   unrelated stubs facing each other.
4. **FACES** — planar traversal taking the clockwise-most turn. Interior faces come back positive,
   the outer boundary negative.

**Selection prefers a network that closes WITHOUT bridging**, because an already-closed outline
involves no guessing. The garage returns **783.0 sf / 156.9 lf with `bridged_gaps = 0`**.

`Footprint` carries `component_index`, `components`, `needed_bridging` and `interior_faces_sf` so any
number can be traced to the shape that produced it.

**Declining is a valid answer.** Open line-work returns `None` rather than a bounding box: a
confident wrong area lands in a price.

## 6. Interior room areas — SOLVED by rasterisation, validated to 0.9%

### 6.1 What the plan actually is

The new-work sheet is the **main floor**: a garage plus the habitable remainder. Established from
the drawing, not from the label text — the `OVERHEAD - 8 PANELS` door block sits inside the large
region, so that region is the garage.

The architect's own figures for this plan: **garage 399.6 sf + 268 sf = 667.6 sf.**
The `120 + 549 = 669` and `garage 399.6 / 400 allowed / 669 allowed / 35% lot coverage` texts are a
**zoning note block** at the bottom of the sheet, not room labels — which is why they fall outside
every detected region. `549` and `120` describe the *upper* floor and are merely placed on this
sheet; they land inside the garage region and must not be treated as its area.

Two finished-floor datums, `FF 214.00` in the garage and `FF 214.50` in the habitable side, are a
**6 in slab step**, not two drawings. Their regions are 8 in apart — adjacent rooms sharing a wall.

### 6.2 The vector approach cannot do this — established by measurement

| Attempt | Rooms | Sum |
|---|---|---|
| A-WALL + A-FLOR | 201.2, 48.9 | 250.1 |
| **+ I-WALL** (14 interior segments) | 183.3, 48.9 | 232.2, **worse** |
| + I-WALL with a 36 in interior gap | identical | 232.2 |
| Walls only (A-WALL + I-WALL) | — | **no envelope at all** |

Not a layer-selection or threshold problem. **Walls are drawn as double lines** (measured 6.5 in
apart, 11 parallel pairs, unanimous). A room is bounded by the *inner* faces; mixing inner and outer
lines into one planar graph produces slivers and false faces, and every opening is still a hole in
the boundary. That family of fixes is ruled out.

### 6.3 Rasterise → close → label → centreline-correct

1. **Rasterise** A-WALL + I-WALL + A-FLOR at 1 px/inch. A wall becomes the solid barrier it
   physically is, so double lines and slivers stop mattering.
2. **Morphological closing** with r = 8 in (dilate then erode, square SE). Seals every gap up to
   2r = 16 in, which covers the door and window breaks, without moving any wall face.
3. **Label** the enclosed empty regions (flood fill from the border inwards first, so anything
   reachable from outside is discarded). Two filters: below 10 sf, and below 30 in in either
   dimension. The second matters more — a leaking gap opens a long thin sliver between two wall
   faces that passes an area test easily (28 ft x 2 ft is 56 sf) but is not a room.
4. **Centreline correction** — add `perimeter x half the wall thickness`, where the thickness is
   **measured from the drawing's own double-line spacing** (`measure_wall_thickness`, wall layers
   only) and never assumed. Architects dimension rooms to the centreline; the raster measures to the
   inner face. When no consistent double-line pattern exists — single-line walls are a real
   convention — the correction is skipped and a warning says the areas will read low.

A half-pixel per unit of perimeter is also added back: the rasterised line sits *on* the inner face
and its pixel counts as wall, so without it every room reads low by `perimeter / 2` pixels.

Result on the real file, against the architect's own figures:

| Region | Net (inner face) | Perimeter | Centreline | Architect | Error |
|---|---|---|---|---|---|
| R1 garage | 383.4 sf | 78.2 lf | **404.6** | 399.6 | **+1.3%** |
| R2 + R3 habitable | 230.2 sf | 130.2 lf | **265.5** | 268.0 | **−0.9%** |
| Total | 613.6 sf | | **670.1** | 667.6 | **+0.4%** |

### 6.4 The plateau is the acceptance test

The radius is not a tuned constant. Six radii are swept and the answer is the **plateau** — the run
of adjacent radii that agree within 2%. Here that is r = 6, 8, 10 in; the midpoint is used. r = 12
swallows the 46 sf region. A result stable at only one radius emits a warning, because it means the
answer depends on the opening size and should be checked by eye.

Plateau selection ranks by **room count first**, run length second. The two failure modes are not
symmetric: too small a radius leaks through a doorway and *merges* two rooms, while too large only
deletes a small one — and a leak can be stable across several radii, so longest-run alone would
happily settle on the merged answer.

**Caveats, stated plainly.** One drawing, three regions. The plateau width on other drawing sets is
unknown. Closets under 10 sf or narrower than 30 in are excluded by design and must be added by hand
where they matter.

### 6.5 Multi-room plans — the two gap scales

The 1234 Example method (one closing radius over the whole plan) does not survive
a multi-room architectural set. Measured on Maple:

| Symptom | Sheet |
|---|---|
| One 969 sf void at every radius up to 16 in | 109 view 0 |
| Nothing at all below r = 20 in, then 55 sf, then 777 sf at r = 30 | 109 view 1 |
| 4 rooms totalling 146 sf at r = 3, then 1165 sf at r = 4 | 107 A6 |

**Cause: a plan has two gap scales and one radius cannot serve both.** Exterior
openings run to 6 ft — patio doors, window walls, a garage door. Interior doorways
are 30–36 in. Sized for the exterior, the radius swallows every room narrower than
6 ft. Sized for the interior, the envelope leaks and the entire floor drains
outdoors as a single region.

**Fix: seal the envelope separately.** `building_mask` closes the *exterior* wall
raster at a wide radius and fills everything unreachable from the image border.
Nothing has to close for that to work — which matters, because vector face-tracing
cannot supply the outline here at all: it needs the exterior wall graph to close,
and exterior walls are broken at every window. On these sheets the tracer returned
a **46 sf duct chase** as the building. Rooms are then measured against the
*unclosed* interior walls **inside** that mask, so the envelope radius moves only
the outline and never a room.

**Choosing the envelope radius took three attempts**, each measured against the
architect's own figures across seven views:

| Rule | Why it fails |
|---|---|
| First radius agreeing with its successor | Cannot tell a sealed envelope from a *stable leak* — 109 view 1 sits flat at 125 sf for three radii before sealing at 1149 |
| Radius after the largest step | Picks the bulge when the envelope was already sealed at the smallest radius tried — 107 ran to 72 in and returned an outline **51% oversize** |
| **≥ half the largest area AND settled** | Correct on all seven |

**The sweep is capped by the widest opening the drawing itself declares**, read
from its door and window blocks. Sealing a gap wider than any opening the architect
drew is not sealing — it is inventing a wall. Without the cap, the two regression
tests that must *decline* to answer began returning confident areas for buildings
with a whole side missing.

### 6.6 What still does not work: the per-room split on Maple

Envelope areas now land close to the architect's label sums. The per-room split
does not converge — the interior partitions on these sheets are drawn with gaps
far wider than a doorway, and 109 view 0 carries **no door blocks at all**, so
there is nothing to seal against. Every affected sheet says so in a warning.

Two hypotheses were tested and refuted before settling on this:

- **Wall geometry nested inside block references.** Refuted: `virtual_entities()`
  shows the nested content is doors, glazing, fixtures and furniture — no A-WALL or
  I-WALL anywhere.
- **`Q-SPCQ` (Revit space boundaries) carries room polygons.** Refuted: 54 polys on
  1234 and **none with an area above 1 sf** — they are label boxes, not rooms.

## 6.7 The room schedule is IN the drawing

Revit tags every room on the AIA room-identification layer as two texts a few
inches apart — `'198.58 SF'` and `'LIVING ROOM'`. That is a complete room
schedule: name, area, position, per room. It is authoritative data in the file, in
the way block ATTRIBUTES are authoritative for opening sizes, and the engine was
discarding it as annotation.

`room_schedule.py` reads it. Maple 107 A6 yields **18 rooms / 1415.1 sf** —
MUD ROOM x2, BEDROOM x2, BATHROOM x2, MEC. ROOM x2, LIVING ROOM x2, KITCHEN x2,
GARAGE x2, STAIR x2, CLOSET, STORAGE. A duplex basement, correct down to the pairs.

Two defects the real files exposed:
- a **datum** printed beside a tag was read as the room name — a garage came back
  called `FF 214.00`;
- a **storey total** printed in room-tag style was counted as a room, 1084.4 sf
  among eleven on 107 A7. An entry equal to the sum of its siblings is a total.

**This reframes what geometry is for.** The drawing states the rooms; geometry
independently *checks* them by measuring the envelope. On Maple 109 the
envelope is 1.03x the schedule. Where the ratio leaves the plausible band the
takeoff says so. A drawing with no tags falls through to measured rooms and their
convergence warning — which is the case that matters for takeoff from CAD with no
schedule of any kind.

## 6.8 Engines built for the no-tag case

Both are kept, and both were measured against the architect's own per-room figures
before the schedule was found. Each returned a median per-room error above 25% on
Maple, so neither is used where tags exist.

- **`centerlines.py`** — pair double-line faces, merge collinear runs across
  doorways, extend endpoints onto the walls they stop short of. Its envelope on
  1234 measured **666.6 sf against the architect's 667.6 (−0.15%)**, net area
  straight from geometry, which is the best single number the geometry has
  produced. Its room split did not separate anything.
- **`watershed.py`** — exact Felzenszwalb distance transform, flood from the peaks
  in descending order so regions meet at the necks (which is what a doorway is, at
  any width, with no radius), then merge any pair whose neck is wider than a door.
  Totals landed within 1-9%; per-room did not.

## 6.9 Cross-sheet reconciliation and trade quantities

`project_takeoff.py` folds per-sheet takeoffs into one building takeoff, keyed on
the storey each view shows. Summing sheets is wrong twice over: it double-counts a
storey drawn on two sheets, and it hides disagreements that should be surfaced.

The failure this caught on its own first run: `109 Maple - A6 - BASEMENT &
LEVEL 1 FLOOR PLANS` names **two** storeys, so filename fallback filed the level 1
view under "basement", one overwrote the other, and the building lost a floor.
Ambiguous text now returns no storey, the views are kept apart by position, and a
finding names them. Before: 4 storeys / 4990 sf. After: **6 storeys / 6919.3 sf**.

`room_trades.py` turns the schedule into quantities in BRIO's 18 divisions, and
**every line carries a basis**: `measured` (the drawing said it), `derived`
(arithmetic under a stated assumption), `assumed` (a rule of thumb about how rooms
are built). Nothing reaches a price without a human seeing which is which. Where a
rule and a measurement describe the same thing the measurement wins and the gap is
reported — on Maple, **59 doors drawn against 64 implied by the room schedule**,
and 18 plumbing fixtures drawn against 44 implied, which says the fixture count is
the one to check.

## 7. Gross vs net

- **Gross (geometry, 783 sf)** — to the outer face. Drives foundation, framing, roof, cladding.
- **Net (garage 399.6 + habitable 268 = 667.6)** — interior. Drives flooring and finishes.

Wall thickness explains ~85 sf of the 114 sf difference (156.9 lf x 6.5 in); ~29 sf is unexplained
and not worth chasing on a single drawing. The takeoff reports both and **warns above 5% drift**.

Note the label trap: `669` is the *allowable* zoning figure while the PDF states **668 proposed**.
`120 + 549 = 669` is the **upper** floor; this sheet is the main floor, `399.6 + 268 = 667.6`. See
§6.1 — reading a room's area off the nearest text is how you get 549 sf for a 400 sf garage.

## 8. Tests

`tests/test_cad_takeoff.py` (10) + `tests/test_floor_area.py` (8) + `tests/test_room_areas.py` (12),
over fixtures with exact known answers (`tests/fixtures/make_takeoff_dxf.py`, and double-line
rectangles whose true area is arithmetic rather than a second implementation of the thing under
test).

Deliberately included: an **L-plan** where a bounding box would say 100 sf and truth is 75; a **36 in
doorway** that must bridge; **metric equivalence** with imperial; a **non-AIA layer fixture** plus a
test asserting the default map genuinely FAILS on it, so the custom-map test cannot pass vacuously;
and two cases that **must return `None`** (an 80 in gap, and open line-work). A method that always
returns an answer is not a method.

For rooms specifically: a 32 in doorway that must *not* merge two rooms, a 36 in exterior door that
must not leak a room outdoors, a 10 ft opening that must return `None` rather than invent a wall,
single-line walls that must report net only and warn, and metric/imperial equivalence.

## 9. Real-file validation

`1234 Example - Plan.dxf` (a real production drawing, not included in this repo).
Counts match the documented ground truth exactly: **5 doors, 7 windows, 2 plumbing fixtures, 0
appliances**, existing house excluded (9 / 8 / 6 / 5 dropped).

Not yet validated against a second project. A second multi-building set is held privately for that.
