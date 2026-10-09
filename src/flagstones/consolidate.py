"""Intersection consolidation: fold junction nodes that lie within the
merge distance of one another into seed clusters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
from shapely import Point
from shapely.ops import unary_union

from flagstones.buffers import _components, _overlapping_pairs
from flagstones.crs import crs_of, require_metric_crs
from flagstones.errors import PreConsolidatedGraphError
from flagstones.graph import _edge_table, _EdgeTable, _group_legs
from flagstones.holdouts import _link_holdouts
from flagstones.medians import _median_ties
from flagstones.params import TileParams
from flagstones.transitions import _classify_transitions

_DEFAULTS = TileParams()

logger = logging.getLogger(__name__)

__all__ = ["Cluster", "clusters_from_graph"]


@dataclass(frozen=True)
class Cluster:
    """An intersection as the merge sees it, before any snapping.

    `x` and `y` sit in the graph's projected CRS. `degree` counts
    distinct road legs at the junction without direction, so a two-way
    street contributes one leg where the MultiDiGraph degree would give
    it two.
    """

    osmids: tuple[int, ...]
    x: float
    y: float
    degree: int


def clusters_from_graph(
    graph: Any,
    *,
    tolerance_m: float,
    min_degree: int = _DEFAULTS.degree_filter_min,
    junction_degree: int | None = None,
    contract_transitions: bool = _DEFAULTS.contract_transitions,
    transition_require_same_road: bool = _DEFAULTS.transition_require_same_road,
    transition_spread_max_deg: float = _DEFAULTS.transition_spread_max_deg,
    transition_opposite_min_deg: float = _DEFAULTS.transition_opposite_min_deg,
    _table: _EdgeTable | None = None,
) -> list[Cluster]:
    """Fold the graph's intersections into seed clusters and return them.

    The merge follows osmnx's `consolidate_intersections(tolerance,
    rebuild_graph=True, dead_ends=True)` with an edge table plus an
    STRtree, since osmnx's own graph rebuild takes hours on a statewide
    extract. Each node gets a 64-gon buffer of `tolerance_m`. Buffers
    merge with the ones they overlap, and a merged buffer then splits
    back into the weakly connected components of graph edges between its
    nodes, so a grade separation whose junctions share no edge stays
    split. A whole merged buffer donates its union's centroid to the
    cluster. After a split the cluster position becomes the mean of the
    node points, matching how osmnx places clusters.

    `tolerance_m` matches the per-node buffer radius osmnx takes, which
    means nodes close enough to merge can be up to double it apart.
    Boeing's published merge distance of 10 m corresponds to 5 here.
    `min_degree` sets the leg count a cluster has to reach to come back.
    `junction_degree` decides when a node joins the merge as a junction
    and starts at `min_degree`. That lets a caller who wants every
    cluster (`min_degree=1`) pass 3 and still get the published merge.

    A dead end stays out of the merge however close it lies, and the
    degree filter sees it as a singleton cluster with one leg (a
    ramp-only or isolated end gets 0). The default filter drops them; a
    request for every cluster with `min_degree=1` keeps the degree-1
    road ends. The parent junction still claims the cul-de-sac as one of
    its legs, so a three-way with a cul-de-sac attached reports degree
    3. This departs from osmnx on purpose: `dead_ends=False` deletes the
    leg along with the end, while `dead_ends=True` admits a nearby
    street end into the junction's cluster to bend its centroid.

    Two side streets that each meet a carriageway of a divided road,
    facing one another with nothing through the median, would stay two
    junctions at any closeness. A median tie (`_median_ties`) joins
    such a pair, for the component split only.

    When `contract_transitions` is on, `classify_transitions` names its
    nodes and they skip both the merge and the seeds at any degree,
    while the junction past the transition counts the leg to each of
    them. Flip it off and a join or fork between carriageways with
    three legs looks like a junction, so it seeds a tile at the
    transition. The `transition_*` arguments pass straight through to
    `classify_transitions`.

    The return sorts clusters by `(x, y)`. A graph without nodes returns
    an empty list. `ValueError` comes from `crs_of` refusing the CRS or
    from axes not measured in meters. An `osmid_original` node signals
    an upstream merge, and this function answers it with
    `PreConsolidatedGraphError`. `_table` lets a caller hand over an
    edge table it already built.
    """
    require_metric_crs(crs_of(graph))
    table = _edge_table(graph) if _table is None else _table
    nodes = table.nodes
    count = len(nodes)
    if count == 0:
        return []
    transition_nodes = (
        _classify_transitions(
            graph,
            table,
            require_same_road=transition_require_same_road,
            spread_max_deg=transition_spread_max_deg,
            opposite_min_deg=transition_opposite_min_deg,
        )
        if contract_transitions
        else set()
    )

    if any("osmid_original" in data for _, data in graph.nodes(data=True)):
        raise PreConsolidatedGraphError(
            "a node has osmid_original, so the graph was consolidated "
            "upstream and a second merge would fold the clusters again; "
            "pass the pre-consolidation graph"
        )

    streets, node_legs = table.counts
    dead_end_mask = streets <= 1
    dead_ends_held_out = [nodes[i] for i in np.flatnonzero(dead_end_mask)]
    # Ramp nodes under the `junction_degree` bar stay out of the merge
    # too, so they cannot drag a junction's centroid or widen its
    # osmids. Slip lane ends mid-block, roads bending into a single
    # ramp and nodes of nothing but ramps fall below it. Some of them
    # merge anyway. A link touching a junction within the merge
    # distance is the connector inside an intersection (Roosevelt Way
    # NE at NE 40th Alley, Eastlake Avenue NE at NE Campus Parkway).
    # And when a node's ordinary edges all point at junctions inside
    # that distance, it sits wedged between junctions (Southeast
    # Eastgate Way at 148th Avenue SE). That case insists on every
    # edge qualifying, because a through-road edge running on
    # elsewhere leaves the node wedged between nothing.
    junction_degree = min_degree if junction_degree is None else junction_degree
    junction_mask = node_legs >= junction_degree
    link_nodes = _link_holdouts(table, junction_mask, dead_end_mask, tolerance_m)
    # A node listed twice would mint its osmid as two singleton
    # clusters. The dead-end and link lists are disjoint, while a
    # transition node can read as a link node too.
    held_out = list(
        dict.fromkeys([*dead_ends_held_out, *link_nodes, *transition_nodes])
    )
    keep_mask = np.ones(count, dtype=bool)
    keep_mask[[table.index[node] for node in held_out]] = False
    keep = np.flatnonzero(keep_mask)
    position = np.full(count, -1, dtype=np.int64)
    position[keep] = np.arange(len(keep))

    buffer_label = _components(
        len(keep), *_overlapping_pairs(table.x[keep], table.y[keep], tolerance_m)
    )
    eu, ev = position[table.u], position[table.v]
    joined = (eu >= 0) & (ev >= 0) & (eu != ev)
    if joined.any():
        joined[joined] = buffer_label[eu[joined]] == buffer_label[ev[joined]]
    one_way_ends = np.zeros(count, dtype=bool)
    carriage = table.oneway & ~table.link & table.between
    one_way_ends[table.u[carriage]] = True
    one_way_ends[table.v[carriage]] = True
    ties = _median_ties(
        graph,
        [nodes[i] for i in np.flatnonzero(one_way_ends & keep_mask)],
        tolerance_m,
    )
    tie_i = np.array([position[table.index[a]] for a, _ in ties], dtype=np.int64)
    tie_j = np.array([position[table.index[b]] for _, b in ties], dtype=np.int64)
    tied = (
        buffer_label[tie_i] == buffer_label[tie_j] if len(ties) else np.zeros(0, bool)
    )
    component = _components(
        len(keep),
        np.concatenate([eu[joined], tie_i[tied]]),
        np.concatenate([ev[joined], tie_j[tied]]),
    )
    logger.debug(
        "consolidation branches",
        extra={
            "branch_counts": {
                "dead_end_holdouts": len(dead_ends_held_out),
                "link_holdouts": len(link_nodes),
                "transitions_contracted": len(transition_nodes),
                "median_ties": int(np.count_nonzero(tied)),
            }
        },
    )

    order = np.argsort(component, kind="stable")
    if len(order):
        starts = np.flatnonzero(
            np.r_[True, component[order][1:] != component[order][:-1]]
        )
        bounds = np.r_[starts, len(order)]
    else:
        starts = bounds = np.zeros(0, dtype=np.int64)
    parts_per_buffer = np.bincount(buffer_label[order[starts]], minlength=len(keep))
    memberships: list[tuple[tuple[int, ...], float, float]] = []
    cluster_index = np.empty(count, dtype=np.int64)
    for start, stop in zip(bounds[:-1], bounds[1:], strict=True):
        members = keep[order[start:stop]]
        cluster_index[members] = len(memberships)
        osmids = tuple(sorted(int(nodes[i]) for i in members))
        if len(members) == 1:
            memberships.append(
                (osmids, float(table.x[members[0]]), float(table.y[members[0]]))
            )
            continue
        xs, ys = table.x[members], table.y[members]
        if parts_per_buffer[buffer_label[order[start]]] == 1:
            centroid = unary_union(
                [Point(x, y).buffer(tolerance_m) for x, y in zip(xs, ys, strict=True)]
            ).centroid
        else:
            centroid = unary_union(shapely.points(xs, ys)).centroid
        memberships.append((osmids, float(centroid.x), float(centroid.y)))
    for node in held_out:
        i = table.index[node]
        cluster_index[i] = len(memberships)
        memberships.append(((int(node),), float(table.x[i]), float(table.y[i])))

    # Leg counts cross the entire edge table through the cluster map,
    # matching osmnx's street_count sweep on its rebuilt graph, so a leg
    # to a held-out dead end counts at the junction that survives.
    roads, links = _group_legs(table, cluster_index, len(memberships), by_kind=True)
    degrees = roads + np.where(roads == 1, links, 0)
    clusters = [
        Cluster(osmids=osmids, x=x, y=y, degree=int(degrees[index]))
        for index, (osmids, x, y) in enumerate(memberships)
    ]
    transition_ids = {int(node) for node in transition_nodes}
    clusters = [
        cluster
        for cluster in clusters
        if cluster.degree >= min_degree
        and not (len(cluster.osmids) == 1 and cluster.osmids[0] in transition_ids)
    ]
    clusters.sort(key=lambda c: (c.x, c.y))
    return clusters
