"""The link-node holdout: ramp nodes the merge must not absorb."""

from __future__ import annotations

from typing import Any

import numpy as np

from flagstones.buffers import _buffers_overlap
from flagstones.graph import _EdgeTable


def _link_holdouts(
    table: _EdgeTable,
    junction_mask: np.ndarray,
    dead_end_mask: np.ndarray,
    tolerance_m: float,
) -> list:
    """Return the held-back link nodes, ordered as the table lists them.

    A candidate touches an edge whose `highway` ends in `_link`, and the
    masks read it as neither a junction nor a street end. It rejoins the
    merge when a link edge finds a junction within merging reach (the
    `tolerance_m` buffers overlap), or when all of its plain edges do
    the same.
    """
    nodes = table.nodes
    count = len(nodes)
    touches_link = np.zeros(count, dtype=bool)
    touches_link[table.u[table.link]] = True
    touches_link[table.v[table.link]] = True
    candidate_mask = touches_link & ~junction_mask & ~dead_end_mask
    link_nodes: list[Any] = []
    if candidate_mask.any():
        touching = candidate_mask[table.u] | candidate_mask[table.v]
        neighbors: dict[int, list[tuple[int, bool]]] = {}
        for u, v, is_link in zip(
            table.u[touching], table.v[touching], table.link[touching], strict=True
        ):
            if u == v:
                continue
            if candidate_mask[u]:
                neighbors.setdefault(int(u), []).append((int(v), bool(is_link)))
            if candidate_mask[v]:
                neighbors.setdefault(int(v), []).append((int(u), bool(is_link)))

        def _within_merge_of_a_junction(node: int, other: int) -> bool:
            return bool(junction_mask[other]) and _buffers_overlap(
                table.x[node],
                table.y[node],
                table.x[other],
                table.y[other],
                tolerance_m,
            )

        for node in np.flatnonzero(candidate_mask):
            node = int(node)
            edges = neighbors.get(node, [])
            connects = any(
                is_link and _within_merge_of_a_junction(node, other)
                for other, is_link in edges
            )
            if not connects:
                plain = {other for other, is_link in edges if not is_link}
                connects = bool(plain) and all(
                    _within_merge_of_a_junction(node, other) for other in plain
                )
            if not connects:
                link_nodes.append(nodes[node])
    return link_nodes
