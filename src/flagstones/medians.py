"""Median ties: node pairs facing each other across a divided road."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from flagstones.buffers import _buffers_overlap
from flagstones.transitions import _angle_between, _carriageways

_OPPOSITE_MIN_DEG = 150.0


def _median_ties(
    graph: Any, candidates: Sequence[Any], tolerance_m: float
) -> list[tuple[Any, Any]]:
    """Return the `candidates` pairs split by a median, carriageway to
    carriageway.

    A node pair qualifies when each node takes a one-way non-link edge,
    those edges carry the same name or ref and head apart by at least
    `_OPPOSITE_MIN_DEG`, the nodes share no edge, and their buffers overlap.
    Such a pair is one intersection, yet osmnx cuts a merged buffer
    into connected components and nothing crosses the median to bridge
    them. Dexter Avenue N at Thomas Street is one such pair. The caller
    folds returned pairs into the split only. `candidates` arrives as
    the merge's nodes on one-way non-link edges.
    """
    carriageways = {
        node: found for node in candidates if (found := _carriageways(graph, node))
    }
    cell = 2.0 * tolerance_m
    buckets: dict[tuple[int, int], list[Any]] = {}
    for node in carriageways:
        x, y = graph.nodes[node]["x"], graph.nodes[node]["y"]
        buckets.setdefault((math.floor(x / cell), math.floor(y / cell)), []).append(
            node
        )

    def _opposite(
        mine: list[tuple[str, float]], theirs: list[tuple[str, float]]
    ) -> bool:
        return any(
            name == other
            and _angle_between(bearing, other_bearing) >= _OPPOSITE_MIN_DEG
            for name, bearing in mine
            for other, other_bearing in theirs
        )

    ties: list[tuple[Any, Any]] = []
    for a, mine in carriageways.items():
        ax, ay = graph.nodes[a]["x"], graph.nodes[a]["y"]
        cx, cy = math.floor(ax / cell), math.floor(ay / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for b in buckets.get((cx + dx, cy + dy), ()):
                    if b <= a or graph.has_edge(a, b) or graph.has_edge(b, a):
                        continue
                    bx, by = graph.nodes[b]["x"], graph.nodes[b]["y"]
                    if not _buffers_overlap(ax, ay, bx, by, tolerance_m):
                        continue
                    if _opposite(mine, carriageways[b]):
                        ties.append((a, b))
    return sorted(ties)
