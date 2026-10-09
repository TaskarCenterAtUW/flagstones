"""Carriageway transitions: joins and forks where a road's carriageways
split off or reunite."""

from __future__ import annotations

from typing import Any

import pandas as pd

from flagstones.graph import (
    _bearing_away,
    _edge_table,
    _EdgeTable,
    _is_link,
    _leg_info,
    _one_way,
    _road_names,
    _undirected_neighbors,
)
from flagstones.params import TileParams

_DEFAULTS = TileParams()

__all__ = ["classify_transitions"]


def _carriageways(graph: Any, node: Any) -> list[tuple[str, float]]:
    """List the one-way roads passing through `node`, each as (name, bearing).

    Every one-way, non-link leg at `node` contributes a pair per `name`
    or `ref` it carries. Bearings follow the direction traffic travels.
    """
    found: list[tuple[str, float]] = []
    for _, other, data in graph.out_edges(node, data=True):
        if other == node or not _one_way(data) or _is_link(data.get("highway")):
            continue
        bearing = _bearing_away(graph, node, other, data, reverse=False)
        found.extend((name, bearing) for name in _road_names(data))
    for other, _, data in graph.in_edges(node, data=True):
        if other == node or not _one_way(data) or _is_link(data.get("highway")):
            continue
        bearing = (
            _bearing_away(graph, node, other, data, reverse=True) + 180.0
        ) % 360.0
        found.extend((name, bearing) for name in _road_names(data))
    return found


def _angle_between(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def classify_transitions(
    graph: Any,
    *,
    require_same_road: bool = _DEFAULTS.transition_require_same_road,
    spread_max_deg: float = _DEFAULTS.transition_spread_max_deg,
    opposite_min_deg: float = _DEFAULTS.transition_opposite_min_deg,
) -> set[Any]:
    """Find the graph's join and fork points along carriageway roads.

    Classification happens to the raw graph as delivered, before any
    consolidation. A qualifying node sees exactly three distinct
    neighbors, wired as a join or a fork. A join pairs a two-way neighbor
    with one that only enters and one that only exits. A fork takes
    three one-way neighbors, two heading one way and the third against
    them. Pass `require_same_road` and the legs must hold a `name` or
    `ref` in common. Paired legs may differ in heading by at most
    `spread_max_deg`, while the continuation opens `opposite_min_deg`
    or more from each, which turns away the ordinary right-angle tee
    whose topology alone fits.

    The result is a set of node IDs. `clusters_from_graph` needs no more
    to exclude those nodes from the merge as well as from the seeds.
    """
    return _classify_transitions(
        graph,
        _edge_table(graph),
        require_same_road=require_same_road,
        spread_max_deg=spread_max_deg,
        opposite_min_deg=opposite_min_deg,
    )


def _transition_candidates(table: _EdgeTable) -> list[Any]:
    """Find the nodes whose three neighbors already point like a join or fork.

    Direction alone filters the graph in a single table pass. Names and
    bearings live per edge, so they are fetched only for the survivors.
    """
    if len(table.u) == 0:
        return []
    apart = table.between
    frame = pd.DataFrame({"a": table.u[apart], "b": table.v[apart]})
    out = frame.drop_duplicates().assign(out=True)
    inn = (
        frame[["b", "a"]]
        .set_axis(["a", "b"], axis=1)
        .drop_duplicates()
        .assign(inn=True)
    )
    pairs = out.merge(inn, on=["a", "b"], how="outer")
    pairs["out"] = pairs["out"].eq(True)
    pairs["inn"] = pairs["inn"].eq(True)
    pairs["two_way"] = pairs["out"] & pairs["inn"]
    pairs["out_only"] = pairs["out"] & ~pairs["inn"]
    pairs["in_only"] = pairs["inn"] & ~pairs["out"]
    shape = pairs.groupby("a")[["two_way", "out_only", "in_only"]].sum()
    shape["n"] = pairs.groupby("a").size()
    fits = (shape["n"] == 3) & (
        ((shape["two_way"] == 1) & (shape["out_only"] == 1) & (shape["in_only"] == 1))
        | ((shape["two_way"] == 0) & (shape["out_only"] == 2) & (shape["in_only"] == 1))
        | ((shape["two_way"] == 0) & (shape["out_only"] == 1) & (shape["in_only"] == 2))
    )
    return [table.nodes[i] for i in shape.index[fits.to_numpy()]]


def _classify_transitions(
    graph: Any,
    table: _EdgeTable,
    *,
    require_same_road: bool,
    spread_max_deg: float,
    opposite_min_deg: float,
) -> set[Any]:
    found: set[Any] = set()
    # `_transition_candidates` admits join and fork shapes only, so the
    # branches below partition whatever reaches this loop.
    for node in _transition_candidates(table):
        info = [_leg_info(graph, node, n) for n in _undirected_neighbors(graph, node)]
        two_way = [leg for leg in info if leg.out and leg.in_]
        outs = [leg for leg in info if leg.out and not leg.in_]
        ins = [leg for leg in info if leg.in_ and not leg.out]
        if two_way:
            pair, single = (ins[0], outs[0]), two_way[0]
        elif len(outs) == 2:
            pair, single = (outs[0], outs[1]), ins[0]
        else:
            pair, single = (ins[0], ins[1]), outs[0]
        if require_same_road:
            common = info[0].names
            for leg in info[1:]:
                common = common & leg.names
            if not common:
                continue
        spread = _angle_between(pair[0].bearing, pair[1].bearing)
        opposite = min(
            _angle_between(single.bearing, pair[0].bearing),
            _angle_between(single.bearing, pair[1].bearing),
        )
        if spread > spread_max_deg or opposite < opposite_min_deg:
            continue
        found.add(node)
    return found
