"""Inspect a seed graph: its edge table, per-node counts, and the seed-rule
check. Whether a node reads as a junction or a street end derives from
those counts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

from flagstones.errors import NonDrivableGraphError

__all__ = [
    "SEED_EXCLUDE_ACCESS",
    "SEED_EXCLUDE_HIGHWAY",
    "dead_end_nodes",
    "require_drivable_graph",
]

# Highway values the seed rule rejects. Besides the drivable network's
# exclusions, the list also turns away `service` and `track`.
SEED_EXCLUDE_HIGHWAY: tuple[str, ...] = (
    "footway",
    "path",
    "pedestrian",
    "steps",
    "cycleway",
    "bridleway",
    "corridor",
    "proposed",
    "construction",
    "abandoned",
    "platform",
    "raceway",
    "service",
    "track",
)
SEED_EXCLUDE_ACCESS: tuple[str, ...] = ("no", "private")


def _tag_values(value: Any) -> list[Any]:
    """Collapse a tag's stored content down to the values worth using.

    Plain tags store a scalar, while osmnx keeps a list per merged edge.
    A row in a table-built graph that lacks the tag leaves None or NaN
    behind, and both get dropped here.
    """
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [
        v
        for v in values
        if v is not None and not (isinstance(v, float) and math.isnan(v))
    ]


def require_drivable_graph(graph: Any, *, exclude_highway: Sequence[str]) -> None:
    """Refuse a graph whose `highway` tags hit a value in `exclude_highway`.

    A graph keeps no record of the filter that built it, so the edges
    themselves are the proof. A lone `footway` or `cycleway` among them
    shows the seed rule did not filter this graph. The check reads
    `highway` as scalar or as merged-edge list, passes over edges carrying
    no `highway` at all (common in hand-built test graphs), and hands the
    failing count plus a named edge to `NonDrivableGraphError`.
    """
    excluded = set(exclude_highway)
    offending: list[tuple[Any, Any, str]] = []
    for u, v, value in graph.edges(data="highway"):
        # `set` iteration order varies per process, so the check sorts
        # matches and quotes the smallest tag instead of one picked by luck.
        matches = sorted(tag for tag in _tag_values(value) if tag in excluded)
        if matches:
            offending.append((u, v, matches[0]))
    if offending:
        u, v, tag = min(
            offending, key=lambda hit: (str(hit[0]), str(hit[1]), str(hit[2]))
        )
        raise NonDrivableGraphError(
            f"{len(offending)} edge(s) match highway values "
            "the seed rule excludes (sample: edge "
            f"{u}->{v} with highway={tag!r}). Tiles "
            "begin at road junctions, so rebuild the network via "
            "flagstones.osm.SEED_CUSTOM_FILTER or a filter dropping the "
            "same values"
        )


def _is_link(highway: Any) -> bool:
    """True if the edge's `highway` tag list holds any value ending `_link`.

    Once simplification merges ways, their tags arrive as a list, and an
    edge whose stretches cover ramp and roadway alike counts as link at
    either terminus. The `primary_link` that leaves NE 40th Street for
    Eastlake Avenue NE contributes no leg on the Eastlake side.
    """
    return any(str(v).endswith("_link") for v in _tag_values(highway))


def _road_names(data: Any) -> set[str]:
    """Collect the edge's `name` and `ref` values as strings, list or scalar."""
    names: set[str] = set()
    for key in ("name", "ref"):
        names.update(str(v) for v in _tag_values(data.get(key)))
    return names


def _one_way(data: Any) -> bool:
    """Whether `oneway` makes an edge one-way, with a list value meaning yes.

    Folded ways disagreeing about direction push the tag into a list, and
    the viz export classifies oneway identically.
    """
    value = data.get("oneway", False)
    return isinstance(value, (list, tuple)) or bool(value)


def _bearing_away(
    graph: Any, node: Any, other: Any, data: Any, *, reverse: bool
) -> float:
    """Compute the heading leaving `node` along its edge toward `other`.

    The answer runs in degrees counter-clockwise from east, aimed at the
    first geometry vertex at least 5 m out. That spacing spares the reading
    from any stub of a first segment. When the stored geometry advances
    toward `node` instead of away, `reverse=True` fixes the vertex order.
    """
    x0, y0 = graph.nodes[node]["x"], graph.nodes[node]["y"]
    geometry = data.get("geometry")
    if geometry is None:
        coords = [(x0, y0), (graph.nodes[other]["x"], graph.nodes[other]["y"])]
    else:
        coords = list(geometry.coords)
        if reverse:
            coords.reverse()
    target = coords[-1]
    for x, y in coords[1:]:
        if math.hypot(x - x0, y - y0) >= 5.0:
            target = (x, y)
            break
    return math.degrees(math.atan2(target[1] - y0, target[0] - x0)) % 360.0


# From 100 m up, a loop way that leaves a node and returns brings that node
# two legs. Anything shorter is a turning circle at some cul-de-sac's
# end and counts nothing. Among Seattle's loop-ended streets, measured
# lengths skip the whole gap between 98 m and 177 m.
_LOOP_ROAD_MIN_M = 100.0


def _is_loop_road(data: Any) -> bool:
    return float(data.get("length") or 0.0) >= _LOOP_ROAD_MIN_M


@dataclass(frozen=True)
class _EdgeTable:
    """The edge list flattened into parallel arrays.

    Groupbys over these arrays do the heavy work, covering the
    carriageway prefilter, the link holdout, the transition shape, street
    totals, and leg totals. Walking nodes one at a time in Python instead
    needs hours on a statewide graph. In the arrays, rows point back into
    `nodes` through `u` and `v`, and `u == v` flags a self-loop.
    """

    nodes: list[Any]
    index: dict[Any, int]
    x: np.ndarray
    y: np.ndarray
    u: np.ndarray
    v: np.ndarray
    link: np.ndarray
    loop_road: np.ndarray
    oneway: np.ndarray

    @property
    def between(self) -> np.ndarray:
        """Rows for edges whose endpoints are different nodes."""
        return self.u != self.v

    @cached_property
    def counts(self) -> tuple[np.ndarray, np.ndarray]:
        """Per-node street totals next to leg totals, produced by
        `_node_counts`.

        Computed once per table and shared by the merge and the clip.
        """
        return _node_counts(self)


def _edge_table(graph: Any) -> _EdgeTable:
    nodes = list(graph.nodes)
    index = {node: i for i, node in enumerate(nodes)}
    count = len(nodes)
    x = np.fromiter((graph.nodes[n]["x"] for n in nodes), dtype=float, count=count)
    y = np.fromiter((graph.nodes[n]["y"] for n in nodes), dtype=float, count=count)
    us: list[int] = []
    vs: list[int] = []
    links: list[bool] = []
    loops: list[bool] = []
    oneways: list[bool] = []
    for u, v, data in graph.edges(data=True):
        us.append(index[u])
        vs.append(index[v])
        links.append(_is_link(data.get("highway")))
        loops.append(u == v and _is_loop_road(data))
        oneways.append(_one_way(data))
    return _EdgeTable(
        nodes=nodes,
        index=index,
        x=x,
        y=y,
        u=np.array(us, dtype=np.int64),
        v=np.array(vs, dtype=np.int64),
        link=np.array(links, dtype=bool),
        loop_road=np.array(loops, dtype=bool),
        oneway=np.array(oneways, dtype=bool),
    )


def _group_legs(
    table: _EdgeTable, mapping: np.ndarray, groups: int, *, by_kind: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Tally road legs and link legs for each group of nodes.

    Counting follows what osmnx's `street_count` measures on a rebuilt
    graph. For a facing pair of groups, the pair's larger directed count
    wins. That yields one leg where a street is two-way, two where
    separately mapped ways join the same two junctions, and one where a
    divided road appears as matched one-way siblings. A member's loop road
    contributes two legs (floor per `_LOOP_ROAD_MIN_M`), and an edge with
    both endpoints inside the same group adds nothing. `mapping` places
    each node into a group, and turning `by_kind` on splits road edges
    from `*_link` edges into separate tallies, leaving callers free to apply
    the link rule. Flip it off and links drop into the road tally, which
    reproduces the street count that finds street ends.
    """
    roads = np.zeros(groups, dtype=np.int64)
    links = np.zeros(groups, dtype=np.int64)
    if len(table.u) == 0:
        return roads, links
    a = mapping[table.u]
    b = mapping[table.v]
    apart = a != b
    kind = (
        table.link[apart].astype(np.int8)
        if by_kind
        else np.zeros(int(apart.sum()), np.int8)
    )
    frame = pd.DataFrame({"a": a[apart], "b": b[apart], "k": kind})
    if len(frame):
        out = frame.value_counts()
        inn = frame[["b", "a", "k"]].set_axis(["a", "b", "k"], axis=1).value_counts()
        legs = pd.concat([out, inn], axis=1).fillna(0).max(axis=1)
        per_group = legs.groupby(level=["a", "k"]).sum()
        for k, target in ((0, roads), (1, links)):
            if k in per_group.index.get_level_values("k"):
                found = per_group.xs(k, level="k")
                target[found.index.to_numpy()] = found.to_numpy().astype(np.int64)
    loops = table.loop_road & ~table.between
    if loops.any():
        loop_groups = mapping[table.u[loops]]
        loop_kind = table.link[loops] if by_kind else np.zeros(int(loops.sum()), bool)
        # One physical loop earns a node two legs no matter how many ways
        # draw it, the same number `count_streets_per_node` reaches over
        # `set(nx.selfloop_edges(Gu, keys=False))`. Per-way arithmetic
        # would chop single loops into slivers wherever `simplify_graph`
        # stamped their directions with different `osmid`s. Two
        # genuine loops at a node still earn only two legs, matching
        # osmnx.
        roads[np.unique(loop_groups[~loop_kind])] += 2
        links[np.unique(loop_groups[loop_kind])] += 2
    return roads, links


def _node_counts(table: _EdgeTable) -> tuple[np.ndarray, np.ndarray]:
    """Count streets and legs for every node of the table.

    Streets settle street ends. Re-measuring osmnx's `street_count` over
    this graph, rather than over one of its buffered fetches, moves some
    readings at the boundary: a junction just inside it can report 3
    there beside 1 here, because its other legs lie outside, while a
    loop shorter than `_LOOP_ROAD_MIN_M` contributes 0 to this count
    where osmnx would credit two. Legs settle junctions. Links add to
    the total only for a node standing on exactly one road leg. A road
    dying into ramps still registers as a junction, yet a ramp meeting a
    through street adds nothing.
    """
    identity = np.arange(len(table.nodes))
    streets, _ = _group_legs(table, identity, len(table.nodes), by_kind=False)
    roads, links = _group_legs(table, identity, len(table.nodes), by_kind=True)
    return streets, roads + np.where(roads == 1, links, 0)


def dead_end_nodes(graph: Any) -> list[Any]:
    """List the graph's street ends, nodes holding at most one street.

    Counting reads only this graph's own edges and emits nodes in the
    order the graph holds them. Consolidation leaves street ends
    untouched, the default degree filter drops them, and the junction
    each one hangs off keeps the leg to it. `node_union_clip` keeps
    the discs of these same nodes out of the clip.
    """
    table = _edge_table(graph)
    streets, _ = table.counts
    return [table.nodes[i] for i in np.flatnonzero(streets <= 1)]


@dataclass(frozen=True)
class _LegInfo:
    """What connects a node to a neighbor.

    Edge direction shows up in `out` (an edge departs toward the neighbor)
    and `in_` (one arrives from it). From either direction, road names merge
    into `names`, and the edge checked first, outgoing before incoming,
    contributes `bearing`. Hand-written test edges skip geometry, so only
    real graphs take the `reverse` path inside `_bearing_away`.
    """

    out: bool
    in_: bool
    names: frozenset[str]
    bearing: float


def _leg_info(graph: Any, node: Any, neighbor: Any) -> _LegInfo:
    out_edges = graph.get_edge_data(node, neighbor) or {}
    in_edges = graph.get_edge_data(neighbor, node) or {}
    names: set[str] = set()
    for data in out_edges.values():
        names |= _road_names(data)
    for data in in_edges.values():
        names |= _road_names(data)
    if out_edges:
        data = next(iter(out_edges.values()))
        bearing = _bearing_away(graph, node, neighbor, data, reverse=False)
    else:
        data = next(iter(in_edges.values()))
        bearing = _bearing_away(graph, node, neighbor, data, reverse=True)
    return _LegInfo(
        out=bool(out_edges), in_=bool(in_edges), names=frozenset(names), bearing=bearing
    )


def _undirected_neighbors(graph: Any, node: Any) -> list[Any]:
    return list(
        dict.fromkeys(
            n for n in (*graph.successors(node), *graph.predecessors(node)) if n != node
        )
    )
