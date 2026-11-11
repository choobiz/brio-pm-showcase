#!/usr/bin/env python3
"""Measure a building footprint off a drawing sheet, at the sheet's own scale.

Why this exists: the CAD takeoff's raster envelope produces an AREA that can be
close while its PERIMETER is badly wrong, because a trace that wanders around a
jog gains length far faster than area. On 1234 Example the engine traced
156.9 lf around a building whose outline measures 120 lf. There was no way to
settle it from the CAD alone — that DWG export's wall line-work does not close
at any morphological radius — so the answer had to come off the sheet.

Method:
  1. Render the sheet at a known scale. An architectural sheet states it
     ("3/16\" = 1'-0\""), which fixes points-per-foot exactly: 72 * 3/16 = 13.5.
  2. Isolate the shaded proposed-building region by tint, excluding the green
     site wash. Architects hatch rather than flood-fill, so the mask is closed
     before use.
  3. Fill each row between its extents. A hatch leaves gaps; a building does not.
  4. Rectify the left and right wall profiles. Raster jitter of one pixel per row
     inflates a perimeter without touching the area, which is the whole failure
     this tool exists to avoid.
  5. Report area beside the sheet's OWN stated area. That closure is the check
     that the scale and the region are right — without it the perimeter is just
     a number.

Usage:
  python measure_footprint_from_pdf.py drawing.pdf --page 1 --scale 3/16 \
      --clip 300,250,1000,1050 --stated-area 668
"""
from __future__ import annotations

import argparse
from collections import deque
from fractions import Fraction

import numpy as np


def _dilate(a: np.ndarray, r: int) -> np.ndarray:
    out = a.copy()
    for _ in range(r):
        n = out.copy()
        n[1:, :] |= out[:-1, :]; n[:-1, :] |= out[1:, :]
        n[:, 1:] |= out[:, :-1]; n[:, :-1] |= out[:, 1:]
        out = n
    return out


def _erode(a: np.ndarray, r: int) -> np.ndarray:
    return ~_dilate(~a, r)


def _largest_component(mask: np.ndarray) -> np.ndarray:
    lab = np.zeros(mask.shape, int)
    H, W = mask.shape
    cur, best = 0, (0, 0)
    for y0 in range(H):
        for x0 in np.where(mask[y0])[0]:
            if lab[y0, x0]:
                continue
            cur += 1
            n = 0
            q = deque([(y0, x0)])
            lab[y0, x0] = cur
            while q:
                y, x = q.popleft()
                n += 1
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        q.append((ny, nx))
            if n > best[0]:
                best = (n, cur)
    return lab == best[1]


def _steps(profile: np.ndarray, win: int, tol: float) -> np.ndarray:
    """Collapse a wall profile to the flat runs it actually has.

    A wall is straight; the raster of it is not. Without this the perimeter picks
    up a foot of spurious length for every pixel of jitter.
    """
    med = np.array([np.median(profile[max(0, i - win // 2):i + win // 2 + 1])
                    for i in range(len(profile))])
    out, cur = [], med[0]
    for v in med:
        if abs(v - cur) > tol:
            cur = v
        out.append(cur)
    return np.array(out)


def measure(pdf: str, page: int, scale: str, clip: tuple[float, float, float, float],
            render: int = 4, close_px: int = 16) -> dict:
    import fitz

    ppf = 72.0 * float(Fraction(scale)) * render      # pixels per foot
    doc = fitz.open(pdf)
    pg = doc[page - 1]
    pm = pg.get_pixmap(matrix=fitz.Matrix(render, render), clip=fitz.Rect(*clip))
    img = (np.frombuffer(pm.samples, dtype=np.uint8)
           .reshape(pm.height, pm.width, pm.n)[:, :, :3].astype(int))
    R, G, B = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    sat = img.max(axis=2) - img.min(axis=2)
    green = (G > R + 8) & (G > B + 8)                 # the site wash, not the building
    tint = (sat > 10) & (img.max(axis=2) > 80) & ~green

    solid = _erode(_dilate(tint, close_px), close_px)
    sel = _largest_component(solid)

    ys, xs = np.where(sel)
    sub = sel[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    clean = np.zeros_like(sub)
    for y in range(sub.shape[0]):
        idx = np.where(sub[y])[0]
        if len(idx):
            clean[y, idx.min():idx.max() + 1] = True
    clean = clean[clean.sum(axis=1) > 0.15 * clean.shape[1]]   # drop hatch overshoot

    left = np.array([np.where(r)[0].min() for r in clean], float) / ppf
    right = np.array([np.where(r)[0].max() for r in clean], float) / ppf
    ls = _steps(left, int(ppf * 1.5), 0.35)
    rs = _steps(right, int(ppf * 1.5), 0.35)

    def tv(a):
        return float(np.abs(np.diff(a)).sum())

    height = len(clean) / ppf
    area = clean.sum() / ppf ** 2
    perimeter = (2 * height + tv(ls) + tv(rs)
                 + (rs[0] - ls[0]) + (rs[-1] - ls[-1]))
    return {
        "pixels_per_foot": round(ppf, 2),
        "area_sf": round(area, 1),
        "perimeter_lf": round(perimeter, 1),
        "height_ft": round(height, 2),
        "top_width_ft": round(rs[0] - ls[0], 2),
        "bottom_width_ft": round(rs[-1] - ls[-1], 2),
        "jog_length_ft": round(tv(ls) + tv(rs), 2),
        "compactness": round(perimeter / (4 * np.sqrt(area)), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--scale", default="3/16",
                    help="sheet scale as inches per foot, e.g. 3/16 or 1/4")
    ap.add_argument("--clip", required=True,
                    help="x0,y0,x1,y1 in PDF points, generous around the building")
    ap.add_argument("--stated-area", type=float,
                    help="the sheet's own floor area, to check scale and region")
    ap.add_argument("--render", type=int, default=4)
    a = ap.parse_args()

    out = measure(a.pdf, a.page, a.scale,
                  tuple(float(v) for v in a.clip.split(",")), a.render)
    for k, v in out.items():
        print(f"  {k:<18} {v}")
    if a.stated_area:
        drift = 100 * (out["area_sf"] - a.stated_area) / a.stated_area
        print(f"\n  stated area        {a.stated_area} sf")
        print(f"  CLOSURE            {drift:+.1f}%")
        if abs(drift) > 5:
            print("\n  Closure is poor — the scale, the clip or the tint filter is "
                  "wrong. Do NOT use the perimeter until the area closes.")
        else:
            print("\n  Area closes, so scale and region are right; the perimeter "
                  "comes off the same rectified outline.")


if __name__ == "__main__":
    main()
