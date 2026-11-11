"""Split a floor plan into rooms at its doorways, by watershed on the distance map.

Morphological closing is the wrong instrument for this. It has one radius, and a
plan has two scales that radius must serve at once — a 6 ft patio door and a 30 in
closet — so any value either welds rooms together or eats them.

A doorway is not a gap to be filled. It is a NECK: the free space pinches to the
door width and opens out again. The distance-to-nearest-wall map makes that
explicit — it peaks in the middle of each room and collapses to half a door width
in the opening between them. Flooding outward from the peaks in order of
descending distance therefore meets exactly at the necks, whatever their width, and
a 30 in closet keeps its area because nothing was ever dilated into it.

Seeds come from the architect's own room labels when the drawing carries them, so
each measured region is tied to the label that names it and per-room validation is
possible. Without labels — the case that matters for a client who wants takeoff
from CAD with no schedule at all — the peaks of the distance map serve instead.
"""

from __future__ import annotations

import heapq
from typing import Optional, Sequence

import numpy as np

Point = tuple[float, float]

#: Free space narrower than this is a doorway, a chase or a wall gap, never a room.
#: Half of it is the distance-map value at the neck.
MIN_ROOM_WIDTH_IN = 30.0

#: Two regions meeting through free space WIDER than this were never divided —
#: the watershed line between them is an artefact of seeding, not a wall. Sized
#: above the widest opening a plan normally carries (a 6 ft patio door) and below
#: the narrowest dimension of a real room.
MAX_NECK_IN = 84.0


def distance_transform(free: np.ndarray) -> np.ndarray:
    """Exact Euclidean distance from each free cell to the nearest blocked cell.

    Felzenszwalb & Huttenlocher's separable transform: a 1-D lower envelope of
    parabolas per column, then per row. O(n) and exact, where the chamfer masks
    normally used in its place are neither.
    """
    INF = 1e12
    f = np.where(free, INF, 0.0)
    out = np.empty_like(f)
    for axis in (0, 1):
        src = f if axis == 0 else out
        n = src.shape[axis]
        moved = np.moveaxis(src, axis, 0)
        res = np.empty_like(moved)
        m = moved.shape[1]
        v = np.zeros((n, m), dtype=np.int64)
        z = np.empty((n + 1, m))
        z[0] = -INF
        z[1] = INF
        k = np.zeros(m, dtype=np.int64)
        cols = np.arange(m)
        for q in range(1, n):
            fq = moved[q]
            while True:
                vk = v[k, cols]
                s = ((fq + q * q) - (moved[vk, cols] + vk * vk)) / (2.0 * q - 2.0 * vk)
                bad = (s <= z[k, cols]) & (k > 0)
                if not bad.any():
                    break
                k = np.where(bad, k - 1, k)
            vk = v[k, cols]
            s = ((fq + q * q) - (moved[vk, cols] + vk * vk)) / (2.0 * q - 2.0 * vk)
            k = k + 1
            v[k, cols] = q
            z[k, cols] = s
            z[k + 1, cols] = INF
        k = np.zeros(m, dtype=np.int64)
        for q in range(n):
            while True:
                nxt = z[k + 1, cols]
                bad = nxt < q
                if not bad.any():
                    break
                k = np.where(bad, k + 1, k)
            vk = v[k, cols]
            res[q] = (q - vk) ** 2 + moved[vk, cols]
        placed = np.moveaxis(res, 0, axis)
        out = placed
        f = placed
    return np.sqrt(np.maximum(out, 0.0))


def _local_seeds(dist: np.ndarray, free: np.ndarray, min_radius: float,
                 min_separation: float) -> list[tuple[int, int]]:
    """Peaks of the distance map, taken strongest first and kept apart.

    A room's deepest point is its centre; requiring seeds to be at least one
    room-radius apart stops one room being seeded twice and split down the middle.
    """
    cand = np.argwhere(free & (dist >= min_radius))
    if cand.size == 0:
        return []
    order = np.argsort(-dist[cand[:, 0], cand[:, 1]])
    seeds: list[tuple[int, int]] = []
    for idx in order:
        y, x = int(cand[idx, 0]), int(cand[idx, 1])
        d = dist[y, x]
        sep = max(min_separation, d)
        if all((y - sy) ** 2 + (x - sx) ** 2 >= sep * sep for sy, sx in seeds):
            seeds.append((y, x))
    return seeds


def snap_seeds(seeds: Sequence[tuple[int, int]], free: np.ndarray, dist: np.ndarray,
               search_px: float) -> list[tuple[int, int]]:
    """Move each seed to the deepest free cell near it.

    A room label's coordinate is the TEXT anchor, not the room centre — for MTEXT
    it is a corner of the text box. Left where it lands, a seed sits on a wall (and
    floods nothing) or just across a partition (and floods the wrong room). Both
    happened: on Maple 107 nine of eighteen labels produced no region at all.
    """
    h, w = free.shape
    r = int(round(search_px))
    out: list[tuple[int, int]] = []
    for y, x in seeds:
        y0, y1 = max(0, y - r), min(h, y + r + 1)
        x0, x1 = max(0, x - r), min(w, x + r + 1)
        if y0 >= y1 or x0 >= x1:
            continue
        win_free = free[y0:y1, x0:x1]
        if not win_free.any():
            continue
        win = np.where(win_free, dist[y0:y1, x0:x1], -1.0)
        iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
        out.append((y0 + int(iy), x0 + int(ix)))
    return out


def merge_by_neck(labels: np.ndarray, dist: np.ndarray,
                  max_neck_px: float) -> np.ndarray:
    """Merge neighbouring regions that are not separated by a doorway-sized neck.

    Seeding on every peak of the distance map over-segments — a long living space
    has several peaks and gets split down the middle by a watershed line drawn
    through open floor. The test for whether a division is real is the same one
    that defines a doorway: how wide is the free space where the two regions meet?
    At a door it is the door. Mid-room it is metres. Anything wider than the widest
    opening the drawing declares was never a division at all.
    """
    h, w = labels.shape
    neck: dict[tuple[int, int], float] = {}
    for dy, dx in ((0, 1), (1, 0)):
        a = labels[:h - dy, :w - dx]
        b = labels[dy:, dx:]
        d = np.minimum(dist[:h - dy, :w - dx], dist[dy:, dx:])
        m = (a != b) & (a > 0) & (b > 0)
        if not m.any():
            continue
        av, bv, dv = a[m], b[m], d[m]
        lo = np.minimum(av, bv)
        hi = np.maximum(av, bv)
        for k_lo, k_hi, val in zip(lo.tolist(), hi.tolist(), dv.tolist()):
            key = (k_lo, k_hi)
            if val > neck.get(key, -1.0):
                neck[key] = val

    parent = list(range(int(labels.max()) + 1))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for (a, b), d in sorted(neck.items(), key=lambda kv: -kv[1]):
        if 2.0 * d <= max_neck_px:
            continue                       # a real doorway — keep them apart
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    remap = np.zeros(len(parent), dtype=np.int32)
    nxt = 0
    for i in range(1, len(parent)):
        r = find(i)
        if remap[r] == 0:
            nxt += 1
            remap[r] = nxt
        remap[i] = remap[r]
    return remap[labels]


def segment_rooms(walls: np.ndarray,
                  interior: Optional[np.ndarray] = None,
                  seeds: Optional[Sequence[tuple[int, int]]] = None,
                  px_per_in: float = 1.0,
                  min_room_width_in: float = MIN_ROOM_WIDTH_IN,
                  max_neck_in: Optional[float] = MAX_NECK_IN,
                  ) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """Label rooms by flooding outward from seeds, deepest free space first.

    Returns ``(labels, distance, seeds)``. Label 0 is wall or outdoors.

    The flood is a priority queue keyed on NEGATIVE distance, so every cell is
    claimed by the seed whose room reaches it through the widest corridor. Two
    rooms joined by a door meet inside the door, because that is the narrowest
    point on any path between them — no radius, no threshold, no door block needed.
    """
    free = ~walls
    if interior is not None:
        free = free & interior
    if not free.any():
        return np.zeros(walls.shape, dtype=np.int32), np.zeros(walls.shape), []

    dist = distance_transform(free)
    min_radius = (min_room_width_in / 2.0) * px_per_in

    if seeds is None:
        seeds = _local_seeds(dist, free, min_radius, min_radius * 2.0)
    else:
        seeds = snap_seeds(seeds, free, dist, min_radius * 2.0)
    seeds = [(y, x) for y, x in seeds
             if 0 <= y < free.shape[0] and 0 <= x < free.shape[1] and free[y, x]]
    if not seeds:
        return np.zeros(walls.shape, dtype=np.int32), dist, []

    labels = np.zeros(walls.shape, dtype=np.int32)
    heap: list[tuple[float, int, int, int]] = []
    for i, (y, x) in enumerate(seeds, start=1):
        labels[y, x] = i
        heapq.heappush(heap, (-float(dist[y, x]), y, x, i))
    h, w = free.shape
    while heap:
        _d, y, x, lab = heapq.heappop(heap)
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and free[ny, nx] and labels[ny, nx] == 0:
                labels[ny, nx] = lab
                heapq.heappush(heap, (-float(dist[ny, nx]), ny, nx, lab))
    if max_neck_in:
        labels = merge_by_neck(labels, dist, max_neck_in * px_per_in)
    return labels, dist, list(seeds)
