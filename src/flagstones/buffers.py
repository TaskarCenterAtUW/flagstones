"""Node buffers that match osmnx."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely import STRtree

# osmnx calculates node buffers through geopandas at quad_segs=16, so each one
# has 64 sides. shapely.buffer defaults to quad_segs=8, so callers who need the
# osmnx shape can pass _BUFFER_QUAD_SEGMENTS:
#
_BUFFER_SEGMENTS = 64
_BUFFER_QUAD_SEGMENTS = _BUFFER_SEGMENTS // 4


def _buffers_overlap(ax: float, ay: float, bx: float, by: float, radius: float) -> bool:
    """Overlap test for two node buffers of `radius`, matching osmnx.

    The 64-gon measures `radius` * cos(pi / 64) from its center to an edge
    midpoint, and the full `radius` to a vertex, so the test weighs the center
    distance against twice that reach along the direction joining their
    centers.
    """
    dx, dy = bx - ax, by - ay
    distance = math.hypot(dx, dy)

    if distance == 0.0:
        return True

    segment = 2.0 * math.pi / _BUFFER_SEGMENTS
    psi = (math.atan2(dy, dx) % segment) - segment / 2.0

    return distance < 2.0 * radius * math.cos(segment / 2.0) / math.cos(psi)


def _components(count: int, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Group the items that the pair arrays `(i, j)` connect.

    Returns a label per item, the smallest member index of its group.
    """
    label = np.arange(count)
    if len(i) == 0:
        return label

    while True:
        ri, rj = label[i], label[j]

        if np.array_equal(ri, rj):
            return label

        # Re-hooking each label to its smaller end, then jumping pointers,
        # resolves even a long overlap chain within a few rounds:
        #
        smaller = np.minimum(ri, rj)
        np.minimum.at(label, ri, smaller)
        np.minimum.at(label, rj, smaller)

        while True:
            jumped = label[label]

            if np.array_equal(jumped, label):
                break

            label = jumped


def _overlapping_pairs(
    x: np.ndarray, y: np.ndarray, radius: float
) -> tuple[np.ndarray, np.ndarray]:
    """Return the pairs `i < j` whose `radius` buffers overlap.

    Finds every pair within 2x `radius`, and the same 64-sided check of
    `_buffers_overlap`, run vectorized, drops the pairs whose polygons never
    meet. The osmnx shape spans its full radius toward vertices alone, so plain
    proximity to twice the radius over-reports.
    """
    if len(x) == 0:
        empty = np.zeros(0, dtype=np.int64)
        return empty, empty

    points = shapely.points(x, y)
    tree = STRtree(points)
    qi, ti = tree.query(points, predicate="dwithin", distance=2.0 * radius)
    ordered = qi < ti
    qi, ti = qi[ordered], ti[ordered]
    dx, dy = x[ti] - x[qi], y[ti] - y[qi]
    distance = np.hypot(dx, dy)
    segment = 2.0 * math.pi / _BUFFER_SEGMENTS
    psi = np.mod(np.arctan2(dy, dx), segment) - segment / 2.0
    reach = 2.0 * radius * math.cos(segment / 2.0) / np.cos(psi)
    overlap = (distance == 0.0) | (distance < reach)

    return qi[overlap], ti[overlap]
