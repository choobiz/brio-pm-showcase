"""Check a sheet's scale against its own printed dimensions.

456 W Sample ID-0 (2026-10-26): the title block said 1/4" = 1'-0", but the
plan had been pasted in enlarged, to about 0.48" per foot. Every length measured
at the printed scale came out 1.9x too long, and nothing on the page said so.

A dimension string such as 15'-6" sits beside its dimension line, and the line
is cut at both ends by a tick or an extension line. The distance between those
two stops, divided by 15.5 ft, is the scale the sheet is actually drawn at. One
reading can be wrong (the nearest line belongs to another string, a stop is
missed); many readings that agree cannot all be wrong the same way. So the scale
is only reported when enough of them agree.

Only works where the dimension text is real text. A sheet printed through
PostScript has its lettering turned into shapes, and a pasted picture has no
lines; on those the page asks for a two-point calibration instead.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# 15'-6"   15' 6"   15'6"   15'-6 1/2"   58'
_DIM = re.compile(r"""^(\d{1,3})\s*'(?:\s*-?\s*(\d{1,2})(?:\s+(\d{1,2})/(\d{1,2}))?\s*")?$""")
#: A stroke within this many PDF units of horizontal/vertical is on an axis.
_AXIS_TOL = 0.3
#: A tick is a short slash across the dimension line.
_TICK_MIN, _TICK_MAX = 2.0, 15.0
#: How far (in text heights) the dimension line may sit from its string.
_MAX_OFFSET_TEXT_HEIGHTS = 4.0
#: A stop must sit this close to the dimension line (PDF units).
_STOP_TOL = 2.5
#: Readings within this fraction of each other agree.
AGREE_TOL = 0.015
#: Report a measured scale with at least this many agreeing readings making up
#: at least this share of all readings on the page...
MIN_AGREEING = 3
MIN_AGREEING_SHARE = 0.4
#: ...or with this many agreeing whatever the share: wrong readings scatter, and
#: five landing within 1.5% of each other by chance does not happen. PROJB A-006
#: had 14 of 44 at exactly 1/4" and the rest scattered between 1 and 48.
MIN_AGREEING_ALONE = 5


@dataclass
class DimensionReading:
    text: str
    feet: float
    pdf_units: float

    @property
    def units_per_foot(self) -> float:
        return self.pdf_units / self.feet


@dataclass
class MeasuredScale:
    pdf_units_per_real_foot: float | None
    agreeing: int
    readings: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_feet(text: str) -> float | None:
    """Feet in a dimension string, or None when it is not just a dimension."""
    t = text.strip().replace("”", '"').replace("’", "'").replace("''", '"')
    m = _DIM.match(t)
    if not m:
        return None
    v = int(m[1]) + (int(m[2]) / 12 if m[2] else 0.0)
    if m[3]:
        if int(m[4]) == 0:
            return None
        v += int(m[3]) / int(m[4]) / 12
    return v if 1.0 <= v <= 300.0 else None


def _segments(drawings: list[dict]):
    """Horizontal lines, vertical lines and tick centres from stroked paths."""
    horiz, vert, ticks = [], [], []
    for d in drawings:
        if d.get("type") == "f":
            continue
        for it in d.get("items") or ():
            if it[0] != "l":
                continue
            a, b = it[1], it[2]
            dx, dy = abs(a.x - b.x), abs(a.y - b.y)
            if dy < _AXIS_TOL and dx >= _AXIS_TOL:
                horiz.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
            elif dx < _AXIS_TOL and dy >= _AXIS_TOL:
                vert.append((min(a.y, b.y), max(a.y, b.y), (a.x + b.x) / 2))
            else:
                length = (dx * dx + dy * dy) ** 0.5
                if _TICK_MIN <= length <= _TICK_MAX and 0.5 < dx / dy < 2.0:
                    ticks.append(((a.x + b.x) / 2, (a.y + b.y) / 2))
    return horiz, vert, ticks


def read_dimensions(spans: list[tuple[str, tuple[float, float, float, float], bool]],
                    drawings: list[dict]) -> list[DimensionReading]:
    """Pair each dimension string with the stops of its dimension line.

    ``spans`` are (text, bbox, runs_left_right) in the same coordinates as
    ``drawings`` (PyMuPDF's unrotated page space for both). The direction comes
    from the text itself: a short string like 31' has a box taller than wide.
    """
    dims = [(parse_feet(t), t, b, lr) for t, b, lr in spans]
    dims = [d for d in dims if d[0] is not None]
    if not dims:
        return []
    horiz, vert, ticks = _segments(drawings)
    out: list[DimensionReading] = []
    for feet, text, (x0, y0, x1, y1), along_x in dims:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        height = (y1 - y0) if along_x else (x1 - x0)
        along, across = (cx, cy) if along_x else (cy, cx)
        lines = horiz if along_x else vert
        perpendicular = vert if along_x else horiz
        near = sorted(
            (abs(pos - across), lo, hi, pos) for lo, hi, pos in lines
            if lo <= along <= hi and abs(pos - across) <= _MAX_OFFSET_TEXT_HEIGHTS * height
        )
        for _, lo, hi, pos in near[:3]:
            stops = [(tx if along_x else ty) for tx, ty in ticks
                     if abs((ty if along_x else tx) - pos) < _STOP_TOL
                     and lo - 3 <= (tx if along_x else ty) <= hi + 3]
            stops += [p for plo, phi, p in perpendicular
                      if lo - 3 <= p <= hi + 3 and plo - 3 <= pos <= phi + 3]
            before = [s for s in stops if s < along - 1]
            after = [s for s in stops if s > along + 1]
            if before and after:
                out.append(DimensionReading(text=text, feet=round(feet, 4),
                                            pdf_units=round(min(after) - max(before), 2)))
                break
    return out


def consensus(readings: list[DimensionReading]) -> MeasuredScale:
    """The scale most readings agree on, or None when they do not agree enough."""
    values = sorted(r.units_per_foot for r in readings if r.feet > 0 and r.pdf_units > 0)
    best: list[float] = []
    j = 0
    for i in range(len(values)):          # widest window of values within AGREE_TOL
        while values[i] > values[j] * (1 + AGREE_TOL):
            j += 1
        if i - j + 1 > len(best):
            best = values[j:i + 1]
    ok = len(best) >= MIN_AGREEING_ALONE or (
        len(best) >= MIN_AGREEING and len(best) >= MIN_AGREEING_SHARE * len(values))
    mid = best[len(best) // 2] if best else None
    return MeasuredScale(
        pdf_units_per_real_foot=round(mid, 3) if ok and mid else None,
        agreeing=len(best),
        readings=len(values),
    )
