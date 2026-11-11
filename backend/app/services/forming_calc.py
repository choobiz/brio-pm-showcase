"""Forming calculator: footing-form lumber, wall-form geometry for the rental
company, concrete and rebar, counted from a CONFIRMED foundation geometry.

WHY CODE AND NOT THE MODEL (Amir, 2026-10-13: "there should be consistent
results"). Three drafts of the same 1234 order chose a different perimeter,
inner form line, corner count (22, 24, 20), joint count (9, 16, 8), spare
(10%, 8%) and spreader length every time, because the model measured and did
arithmetic freehand. Here the method is fixed and the BRIO numbers are
settings, so the same geometry always gives the same quantities.

Checked against Amir's approved 1234 order: with its geometry (130.4 lf, 12
outside / 8 inside corners, ~17 ft interior wall) this gives 289.5 lf of form
line, 20 boards, 147 stakes (approved 140), 11 spreader stock (10), 5 splice
stock (4) and the same nails: 5 of 6 lines within 10%.

Geometry is the outer face of the exterior wall, a closed rectilinear outline:
outside corners - inside corners = 4, so offsetting it by d changes its
perimeter by 2 d (E - I) = 8 d.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

DEFAULT_PARAMS: dict[str, Any] = {
    "stake_spacing_ft": 4, "board_length_ft": 16, "spare_pct": 10, "stakes_per_corner": 2,
    "spreader_spacing_ft": 4, "spreader_cut_in": 30, "spreader_stock_ft": 10,
    "cleat_length_in": 24, "cleat_stock_ft": 8, "concrete_spare_pct": 5,
    "nails": [{"item": "Duplex (double-head) nails", "spec": "3-1/2 in (16d)", "qty": 10, "unit": "lb"},
              {"item": "Duplex (double-head) nails", "spec": "2-1/2 in (8d)", "qty": 5, "unit": "lb"}],
}


class Face(BaseModel):
    name: str = ""
    length_ft: float = Field(gt=0)


class InteriorWall(BaseModel):
    name: str = "Interior wall"
    length_ft: float = Field(gt=0)
    footing: bool = True


class Geometry(BaseModel):
    faces: list[Face] = Field(min_length=4)
    outside_corners: int
    inside_corners: int
    wall_thickness_in: float = 8
    footing_width_in: float = 24
    footing_depth_in: float = 8
    wall_height_ft: float = 3
    interior_walls: list[InteriorWall] = Field(default_factory=list)
    t_junctions: int = 0
    footing_bars: int = 2
    bar_size: str = "15M"
    lap_in: float = 26
    bar_stock_ft: float = 20
    notes: str = ""

    @model_validator(mode="after")
    def closed_outline(self) -> "Geometry":
        if self.outside_corners - self.inside_corners != 4:
            raise ValueError(f"A closed square-cornered outline has 4 more outside than inside corners; "
                             f"got {self.outside_corners} outside and {self.inside_corners} inside.")
        if self.outside_corners + self.inside_corners != len(self.faces):
            raise ValueError(f"{len(self.faces)} faces need {len(self.faces)} corners; "
                             f"got {self.outside_corners + self.inside_corners}.")
        if self.footing_width_in < self.wall_thickness_in:
            raise ValueError("The footing is narrower than the wall.")
        return self


def params(db: Session | None) -> dict[str, Any]:
    if db is None:
        return dict(DEFAULT_PARAMS)
    from app.models.prep import AppSetting

    row = db.get(AppSetting, "forming_params")
    return {**DEFAULT_PARAMS, **(row.value if row else {})}


def _r(x: float, n: int = 1) -> float:
    return round(x, n)


def calculate(g: Geometry, p: dict[str, Any]) -> dict[str, Any]:
    spare = 1 + p["spare_pct"] / 100
    t, w = g.wall_thickness_in / 12, g.footing_width_in / 12
    proj = (w - t) / 2
    k = g.outside_corners - g.inside_corners  # always 4
    P = sum(f.length_ft for f in g.faces)
    interiors = [iw.length_ft for iw in g.interior_walls if iw.footing]

    outer = P + 2 * proj * k
    inner = P - 2 * (t + proj) * k
    lines = [("outer footing form", outer, True), ("inner footing form", inner, True)]
    lines += [(f"interior footing form ({iw.name})", iw.length_ft, False)
              for iw in g.interior_walls if iw.footing for _ in (0, 1)]
    form_lf = sum(l for _, l, _ in lines)
    board = p["board_length_ft"]

    boards = math.ceil(form_lf * spare / board)
    joints = sum(math.ceil(l / board) if closed else max(0, math.ceil(l / board) - 1) for _, l, closed in lines)
    line_stakes = sum(math.ceil(l / p["stake_spacing_ft"]) for _, l, _ in lines)
    corners = g.outside_corners + g.inside_corners
    stakes = math.ceil((line_stakes + p["stakes_per_corner"] * corners + joints) * spare)

    centre = P - 2 * (t / 2) * k + sum(interiors)
    spreaders = math.ceil(math.ceil(centre / p["spreader_spacing_ft"]) * spare)
    per_stock = int(p["spreader_stock_ft"] * 12 // p["spreader_cut_in"])
    spreader_stock = math.ceil(spreaders / per_stock)
    cleat_stock = math.ceil(joints * p["cleat_length_in"] / 12 / p["cleat_stock_ft"])

    src = "calculator"
    lumber = [
        {"calc": "footing_boards", "item": "2x10 SPF #2", "spec": f"{board:g} ft", "qty": boards, "unit": "pcs",
         "basis": (f"form lines {' + '.join(f'{_r(l)}' for _, l, _ in lines)} = {_r(form_lf)} lf; "
                   f"+{p['spare_pct']}% = {_r(form_lf * spare)} lf / {board:g} ft = {boards}"), "source": src},
        {"calc": "footing_stakes", "item": "Wood form stakes, pointed, ready-made", "spec": "24 in", "qty": stakes,
         "unit": "pcs",
         "basis": (f"{line_stakes} at {p['stake_spacing_ft']:g} ft o/c along the form lines + "
                   f"{p['stakes_per_corner']} x {corners} corners + {joints} board joints = "
                   f"{line_stakes + p['stakes_per_corner'] * corners + joints}; +{p['spare_pct']}% = {stakes}"),
         "source": src},
        {"calc": "spreaders", "item": f"2x4 SPF #2 (spreaders, cut on site to {p['spreader_cut_in']:g} in)",
         "spec": f"{p['spreader_stock_ft']:g} ft", "qty": spreader_stock, "unit": "pcs",
         "basis": (f"footing centreline {_r(centre)} lf / {p['spreader_spacing_ft']:g} ft = "
                   f"{math.ceil(centre / p['spreader_spacing_ft'])}; +{p['spare_pct']}% = {spreaders} spreaders; "
                   f"{per_stock} per {p['spreader_stock_ft']:g} ft piece = {spreader_stock}"), "source": src},
        {"calc": "splices", "item": "2x4 SPF #2 (splice boards at board joints)", "spec": f"{p['cleat_stock_ft']:g} ft",
         "qty": cleat_stock, "unit": "pcs",
         "basis": (f"{joints} board joints x {p['cleat_length_in']:g} in = {_r(joints * p['cleat_length_in'] / 12)} lf "
                   f"/ {p['cleat_stock_ft']:g} ft = {cleat_stock}"), "source": src},
    ]
    for i, n in enumerate(p.get("nails") or []):
        lumber.append({"calc": f"nails_{i}", **n, "basis": "BRIO standard allowance for a footing forming order",
                       "source": "BRIO setting"})

    panel_h = 4 if g.wall_height_ft <= 3 else 2 * math.ceil((g.wall_height_ft + 0.5) / 2)
    inside_face = P - 2 * t * k
    wall_forms = [
        {"calc": "wall_outside_face", "label": "Exterior wall, outside face", "value": f"{_r(P)} lin ft",
         "basis": " + ".join(f"{f.length_ft:g}" for f in g.faces)},
        {"calc": "wall_inside_face", "label": "Exterior wall, inside face", "value": f"{_r(inside_face)} lin ft",
         "basis": f"{_r(P)} - 2 x {_r(t, 3)} ft wall x {k}"},
        *[{"calc": f"interior_wall_{i}", "label": f"{iw.name}, both faces", "value": f"{_r(2 * iw.length_ft)} lin ft",
           "basis": f"2 x {iw.length_ft:g} ft"} for i, iw in enumerate(g.interior_walls)],
        {"calc": "wall_corners", "label": "Corners",
         "value": f"{g.outside_corners} outside, {g.inside_corners} inside", "basis": "confirmed geometry"},
        {"calc": "wall_junctions", "label": "T-junctions", "value": str(g.t_junctions), "basis": "confirmed geometry"},
        {"calc": "wall_size", "label": "Wall", "value": f"{g.wall_thickness_in:g} in thick, up to {g.wall_height_ft:g} ft "
                                                         f"above the footing; {panel_h} ft forms",
         "basis": "confirmed geometry"},
    ]

    cspare = 1 + p.get("concrete_spare_pct", 5) / 100
    foot_cy = centre * w * (g.footing_depth_in / 12) / 27
    wall_cy = (P - 2 * (t / 2) * k + sum(iw.length_ft for iw in g.interior_walls)) * t * g.wall_height_ft / 27
    run = centre
    pieces = math.ceil(run / (g.bar_stock_ft - g.lap_in / 12))
    bar_lf = g.footing_bars * (run + max(0, pieces - 1) * g.lap_in / 12)
    concrete = [
        {"calc": "footing_concrete", "item": "Concrete, footings", "spec": f"{g.footing_width_in:g} x {g.footing_depth_in:g} in",
         "qty": _r(foot_cy * cspare), "unit": "cu yd",
         "basis": f"{_r(centre)} lf x {_r(w, 2)} ft x {_r(g.footing_depth_in / 12, 2)} ft / 27 = {_r(foot_cy, 2)} cu yd; "
                  f"+{p.get('concrete_spare_pct', 5)}%", "source": src},
        {"calc": "wall_concrete", "item": "Concrete, walls", "spec": f"{g.wall_thickness_in:g} in x {g.wall_height_ft:g} ft",
         "qty": _r(wall_cy * cspare), "unit": "cu yd",
         "basis": f"wall centreline x {_r(t, 3)} ft x {g.wall_height_ft:g} ft / 27 = {_r(wall_cy, 2)} cu yd; "
                  f"+{p.get('concrete_spare_pct', 5)}%", "source": src},
        {"calc": "footing_rebar", "item": f"Rebar {g.bar_size}, footings", "spec": f"{g.footing_bars} continuous, "
                                                                                  f"{g.lap_in:g} in laps",
         "qty": _r(bar_lf), "unit": "lin ft",
         "basis": f"{g.footing_bars} x ({_r(run)} lf + {max(0, pieces - 1)} laps x {g.lap_in:g} in)", "source": src},
    ]
    return {"perimeter_ft": _r(P), "form_line_lf": _r(form_lf), "joints": joints,
            "lumber": lumber, "wall_forms": wall_forms, "concrete": concrete,
            "rows": {r["calc"]: r for r in [*lumber, *wall_forms, *concrete]}}


def for_project(db: Session, project_id: Any) -> dict[str, Any] | None:
    """The calculation from the project's latest CONFIRMED geometry, or None."""
    from app.models.prep import ProjectGeometry

    g = (db.query(ProjectGeometry)
         .filter(ProjectGeometry.project_id == project_id, ProjectGeometry.status == "confirmed")
         .order_by(ProjectGeometry.version.desc()).first())
    if g is None:
        return None
    out = calculate(Geometry.model_validate(g.payload), params(db))
    out["geometry_version"] = g.version
    for r in out["rows"].values():
        if r.get("source") == "calculator":
            r["source"] = f"calculator, confirmed geometry v{g.version}"
    return out
