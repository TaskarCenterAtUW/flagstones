import networkx as nx
import pytest

from flagstones.consolidate import clusters_from_graph
from flagstones.errors import PreConsolidatedGraphError
from flagstones.graph import dead_end_nodes
from tests.graphs import (
    _add_edge,
    _already_consolidated_fixture,
    _carriageway_join_junction_fixture,
    _cul_de_sac_fixture,
    _divided_road_fixture,
    _grid_with_cul_de_sacs_fixture,
    _junction_with_slip_lane_end_fixture,
    _link_between_junctions_fixture,
    _median_pair_fixture,
    _merged_carriageway_pair_fixture,
    _parallel_way_fixture,
    _road_end_with_ramps_fixture,
    _self_loop_fixture,
    _t_junction_with_dead_end_fixture,
    _t_junction_with_link_fixture,
    _wedged_link_node_fixture,
    _wgs84_fixture,
)


def test_divided_road_junction_becomes_one_intersection():
    """Fold the four nodes where two divided roads cross into one
    intersection. On their own, the corners would each seed a tile."""
    graph = _divided_road_fixture()
    clusters = clusters_from_graph(graph, tolerance_m=15.0)
    assert len(clusters) == 1
    assert len(clusters[0].osmids) == 4


def test_the_divided_road_cluster_reports_its_true_centroid_and_degree():
    """Pin the cluster's centroid and degree, since the count check
    above passes even with wrong `.x`, `.y` or `.degree` values.

    The square centers on about (1010, 1000) and sprouts six outward
    arms, which is where degree 6 comes from. An edge between two
    cluster members adds no leg."""
    cluster = clusters_from_graph(_divided_road_fixture(), tolerance_m=15.0)[0]
    assert cluster.x == pytest.approx(1010.0, abs=1e-6)
    assert cluster.y == pytest.approx(1000.0, abs=1e-6)
    assert cluster.degree == 6
    assert cluster.osmids == (1, 2, 3, 4)


def test_dead_ends_are_not_intersections():
    graph = _cul_de_sac_fixture()
    assert clusters_from_graph(graph, tolerance_m=5.0, min_degree=3) == []


def test_a_junction_keeps_the_leg_that_dead_ends():
    """Keep the parent junction's leg to a dead-end street, even though
    the cul-de-sac's end node seeds nothing itself.

    osmnx's `dead_ends=False` deletes that leg along with the end,
    leaving node 2 at degree 2, and the filter would then discard the
    junction for good."""
    clusters = clusters_from_graph(
        _t_junction_with_dead_end_fixture(), tolerance_m=5.0, min_degree=3
    )
    assert [c.osmids for c in clusters] == [(2,)]
    assert clusters[0].degree == 3


def test_a_close_cul_de_sac_dead_end_is_not_consolidated():
    """Hold the cul-de-sac end out of the junction's cluster even at
    4 m, deep inside merge range, so it bends no centroid down the stub
    and contributes no osmid to the cluster. Node 2 still counts the
    cul-de-sac as one of its three legs, and the dead end seeds
    nothing."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        3: (200.0, 200.0),
        4: (0.0, 200.0),
        5: (200.0, -4.0),  # the dead end, kept 4 m from node 2, in merge range
    }
    street_count = {1: 2, 2: 3, 3: 2, 4: 2, 5: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3), (3, 4), (4, 1), (2, 5)):
        _add_edge(graph, coords, u, v)
    clusters = clusters_from_graph(graph, tolerance_m=5.0, min_degree=3)
    assert [(c.osmids, c.degree) for c in clusters] == [((2,), 3)]


def test_a_divided_road_arm_is_one_leg_of_a_merged_pair():
    """Fold the eastbound and westbound carriageways between two merged
    pairs into a single leg, since leg counting folds a street's travel
    directions the same way for a two-way street between two junctions.

    If the merge scored the directions apart, a mid-block median cut or
    carriageway fork would read as a three-leg intersection and earn a
    tile."""
    clusters = clusters_from_graph(
        _merged_carriageway_pair_fixture(), tolerance_m=5.0, min_degree=3
    )
    assert [c.osmids for c in clusters] == [(1, 2), (3, 4)]
    assert [c.degree for c in clusters] == [3, 3]


def test_a_link_leg_counts_only_where_the_road_would_otherwise_dead_end():
    """Count a `highway=*_link` leg only where the road would otherwise
    dead-end. Over a through road, two road legs plus a ramp stay at
    degree 2, as the fixture's node 2 shows. A lone road leg flips
    that, because there the road ends at the node and the ramps take it
    onward: split one road across two outgoing ramps and the node takes
    three legs, while bending it into a single ramp keeps two.

    Where graph simplification fused a ramp into the road it becomes,
    the merged edge lists the link value among road values, and the
    leg reads as a link at both ends. NE 40th Street's `primary_link`
    onto Eastlake Avenue NE adds nothing at Eastlake."""
    clusters = clusters_from_graph(
        _t_junction_with_link_fixture("primary_link"), tolerance_m=5.0, min_degree=1
    )
    assert {c.osmids: c.degree for c in clusters} == {(1,): 1, (2,): 2, (3,): 1}
    mixed = clusters_from_graph(
        _t_junction_with_link_fixture(["secondary", "primary_link"]),
        tolerance_m=5.0,
        min_degree=1,
    )
    assert {c.osmids: c.degree for c in mixed} == {(1,): 1, (2,): 2, (3,): 1}
    fork = clusters_from_graph(
        _road_end_with_ramps_fixture(2), tolerance_m=5.0, min_degree=3
    )
    assert [(c.osmids, c.degree) for c in fork] == [((2,), 3)]
    bend = clusters_from_graph(
        _road_end_with_ramps_fixture(1), tolerance_m=5.0, min_degree=2
    )
    assert [(c.osmids, c.degree) for c in bend] == [((2,), 2)]


def test_a_link_node_that_is_no_junction_never_merges_into_one():
    """Hold the slip lane's end, 6 m from the junction inside merge
    range, out of the merge on the dead end's terms, since two road
    legs plus a ramp leave it below the junction bar. The junction
    keeps cluster (2,) at degree 3, and the slip node lends no osmids
    and seeds no tile.

    The `junction_degree` bar decides who merges, and node 7 stands at
    two legs there. At `min_degree=1` the listing shows node 7 as its
    own degree-2 cluster beside the junction. Lower to `min_degree=2`
    and node 7 clears the bar as a junction too, so the merge folds it
    together with node 2."""
    clusters = clusters_from_graph(
        _junction_with_slip_lane_end_fixture(), tolerance_m=5.0, min_degree=3
    )
    assert [(c.osmids, c.degree) for c in clusters] == [((2,), 3)]
    listed = clusters_from_graph(
        _junction_with_slip_lane_end_fixture(),
        tolerance_m=5.0,
        min_degree=1,
        junction_degree=3,
    )
    assert {c.osmids: c.degree for c in listed if c.osmids in ((2,), (7,))} == {
        (2,): 3,
        (7,): 2,
    }
    at_two = clusters_from_graph(
        _junction_with_slip_lane_end_fixture(), tolerance_m=5.0, min_degree=2
    )
    assert [c.osmids for c in at_two if 2 in c.osmids] == [(2, 7)]


def test_a_link_within_the_merge_distance_of_a_junction_is_its_connector():
    """Treat a link as the connector inside a consolidated
    intersection when it joins a node to a junction within merge range,
    so the merge takes the node in along with its osmid.

    The close case replays Roosevelt Way NE at NE 40th Alley and
    Eastlake Avenue NE at NE Campus Parkway. Stretch the same fixture
    to a 30 m link and node 7 reverts to a slip lane's end, held out as
    before."""
    close = clusters_from_graph(
        _link_between_junctions_fixture(8.0), tolerance_m=5.0, min_degree=3
    )
    assert [(c.osmids, c.degree) for c in close] == [((2, 7), 5)]
    far = clusters_from_graph(
        _link_between_junctions_fixture(30.0), tolerance_m=5.0, min_degree=3
    )
    assert [(c.osmids, c.degree) for c in far] == [((2,), 3)]


def test_a_node_wedged_between_two_junctions_merges_across_both_plain_edges():
    clusters = clusters_from_graph(
        _wedged_link_node_fixture(), tolerance_m=5.0, min_degree=3
    )
    assert [(c.osmids, c.degree) for c in clusters] == [((1, 2, 3), 4)]


def test_a_junction_pair_across_a_median_is_one_intersection():
    """Join a carriageway pair of the same road that sits 8 m apart across a
    median. The nodes share no edge, and osmnx's rebuild cuts a
    merged buffer along connected components, as it does at Dexter
    Avenue N and Thomas Street.

    A median tie replaces the crossing segment the median has no room
    for, so the pair counts as one node and its carriageways one leg
    each way. The tie requires the same road, opposite directions, and the
    pair inside merge distance."""
    tied = clusters_from_graph(_median_pair_fixture(), tolerance_m=5.0, min_degree=3)
    assert [(c.osmids, c.degree) for c in tied] == [((2, 3), 4)]
    other_road = clusters_from_graph(
        _median_pair_fixture(same_name=False), tolerance_m=5.0, min_degree=3
    )
    assert sorted((c.osmids, c.degree) for c in other_road) == [((2,), 3), ((3,), 3)]
    wide = clusters_from_graph(
        _median_pair_fixture(gap_m=12.0), tolerance_m=5.0, min_degree=3
    )
    assert sorted((c.osmids, c.degree) for c in wide) == [((2,), 3), ((3,), 3)]


def test_contract_transitions_excludes_the_join_but_keeps_its_leg():
    """Classify the carriageway join at node 2 as a transition so it
    drops from the seeds, since its three legs otherwise read as a real
    junction and mint a spurious tile.

    With `contract_transitions` on, node 2 seeds nothing, while junction
    1 keeps the leg toward the join and reports degree 3 in either
    run."""
    graph = _carriageway_join_junction_fixture()
    without = clusters_from_graph(
        graph, tolerance_m=5.0, min_degree=3, contract_transitions=False
    )
    with_contraction = clusters_from_graph(
        graph, tolerance_m=5.0, min_degree=3, contract_transitions=True
    )
    assert (2,) in [c.osmids for c in without]
    assert (2,) not in [c.osmids for c in with_contraction]
    j_without = next(c for c in without if c.osmids == (1,))
    j_with = next(c for c in with_contraction if c.osmids == (1,))
    assert j_without.degree == j_with.degree == 3


def test_a_contracted_transition_never_merges_into_a_nearby_junction():
    """Pull the join to 6 m from junction 1, where its own three legs
    clear the junction bar, then compare who merges with whom.

    Switch contraction off and the pair fuses into one four-leg
    cluster. Switch it on and the join enters neither the merge nor the
    seed pool, leaving junction 1 alone at degree 3 with the leg toward
    the join among its three."""
    graph = _carriageway_join_junction_fixture(join_x=494.0)
    merged = clusters_from_graph(
        graph, tolerance_m=5.0, min_degree=3, contract_transitions=False
    )
    assert [(c.osmids, c.degree) for c in merged] == [((1, 2), 4)]
    contracted = clusters_from_graph(
        graph, tolerance_m=5.0, min_degree=3, contract_transitions=True
    )
    assert [(c.osmids, c.degree) for c in contracted] == [((1,), 3)]


def test_a_junction_cut_by_the_city_limit_is_a_dead_end_of_this_graph():
    """Reproduce osmnx's bookkeeping, which counts streets on the
    buffered fetch before truncating to the city polygon: a junction
    just inside the limit keeps `street_count` 3 while its surviving
    graph shows a lone neighbor.

    Within that graph the node is a dead end, so the merge keeps it out
    even at 4 m, and the surviving junction claims the leg and seeds
    alone."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (200.0, 200.0), 5: (200.0, -4.0)}
    street_count = {1: 1, 2: 3, 3: 1, 5: 3}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3), (2, 5)):
        _add_edge(graph, coords, u, v)
    clusters = clusters_from_graph(graph, tolerance_m=5.0, min_degree=3)
    assert [(c.osmids, c.degree) for c in clusters] == [((2,), 3)]


def test_the_degree_filter_is_a_stabilizer_not_hygiene():
    """Drop cul-de-sacs and minor stubs from the seeds, since they
    survive graph simplification and a walker weighs no direction
    choice at their ends. At `min_degree=3`, the filter discards exactly
    the stub's far end (99) plus the grid's four corners, and the middle
    node still counts the stub as its fifth leg."""
    graph = _grid_with_cul_de_sacs_fixture()
    listed = clusters_from_graph(graph, tolerance_m=5.0, min_degree=1)
    kept = clusters_from_graph(graph, tolerance_m=5.0, min_degree=3)
    assert {c.osmids: c.degree for c in listed} == {
        (1,): 2,
        (2,): 3,
        (3,): 2,
        (4,): 3,
        (5,): 5,
        (6,): 3,
        (7,): 2,
        (8,): 3,
        (9,): 2,
        (99,): 1,
    }
    assert sorted(c.osmids for c in kept) == [(2,), (4,), (5,), (6,), (8,)]


def test_an_unprojected_graph_is_refused():
    """Refuse an unprojected graph, since `tolerance_m` counts in
    whatever units the graph arrives with, and a geographic graph would
    take the 5 as five degrees."""
    with pytest.raises(ValueError):
        clusters_from_graph(_wgs84_fixture(), tolerance_m=5.0)


def test_a_graph_in_feet_is_refused():
    """Refuse EPSG:2927, whose US survey feet would make the 5 m
    tolerance read as 5 ft and hand back clusters that look plausible
    at the wrong distance."""
    graph = _divided_road_fixture()
    graph.graph["crs"] = "EPSG:2927"
    with pytest.raises(ValueError, match="instead of meters"):
        clusters_from_graph(graph, tolerance_m=5.0)


def test_a_preconsolidated_graph_is_refused():
    """Refuse a graph already marked up by a consolidation, since a
    second merge would fold nodes at the wrong distance."""
    with pytest.raises(PreConsolidatedGraphError):
        clusters_from_graph(_already_consolidated_fixture(), tolerance_m=5.0)


def test_a_parallel_way_to_the_same_neighbor_is_its_own_leg():
    """Credit two separately mapped ways between a node pair with two
    legs, matching osmnx's street_count, and check that the second way
    lifts node 2 to the default degree floor."""
    clusters = clusters_from_graph(
        _parallel_way_fixture(), tolerance_m=5.0, min_degree=1
    )
    by_node = {c.osmids: c.degree for c in clusters}
    assert by_node == {(1,): 1, (2,): 3, (3,): 2}
    assert [
        c.osmids for c in clusters_from_graph(_parallel_way_fixture(), tolerance_m=5.0)
    ] == [(2,)]


def test_a_loop_road_of_100_m_or_more_counts_two_legs_and_a_shorter_loop_none():
    """Score a loop way by its length. osmnx's street_count credits a
    loop twice at any length, while the library reads a loop under 100 m
    as a turning circle: its node then rates as a street end, held out
    of the merge, seeded nothing, though the junction it hangs from
    keeps the leg.

    At 100 m or more the loop is a road meeting its stem at a junction,
    and it contributes two legs there, off a stub or a through road
    alike."""
    circle = clusters_from_graph(
        _self_loop_fixture(short=True), tolerance_m=5.0, min_degree=1
    )
    assert {c.osmids: c.degree for c in circle} == {(1,): 1, (2,): 1}
    road = clusters_from_graph(_self_loop_fixture(), tolerance_m=5.0, min_degree=1)
    assert {c.osmids: c.degree for c in road} == {(1,): 1, (2,): 3}
    through_circle = clusters_from_graph(
        _self_loop_fixture(through=True, short=True), tolerance_m=5.0, min_degree=1
    )
    assert {c.osmids: c.degree for c in through_circle} == {(1,): 1, (2,): 2, (3,): 1}
    through_road = clusters_from_graph(
        _self_loop_fixture(through=True), tolerance_m=5.0, min_degree=1
    )
    assert {c.osmids: c.degree for c in through_road} == {(1,): 1, (2,): 4, (3,): 1}


def test_a_node_counts_two_legs_for_its_loops_however_many_ways_draw_them():
    """Count a loop's legs per node, the way osmnx's
    `count_streets_per_node` does. Graph simplification hands the
    directions of a loop different `osmid` values wherever the loop
    spans several OSM ways, so a per-way key would split one physical
    loop into pieces and inflate `seed_degree`.

    The fixture stacks 1, 2 and 3 such ways in turn, and node 2 remains
    at degree 3."""
    for loops in (1, 2, 3):
        clusters = clusters_from_graph(
            _self_loop_fixture(loops=loops), tolerance_m=5.0, min_degree=1
        )
        assert {c.osmids: c.degree for c in clusters} == {(1,): 1, (2,): 3}, loops


@pytest.mark.parametrize(
    ("loop_m", "degree"),
    [(99.9, 1), (100.0, 3)],
)
def test_the_loop_road_floor_is_inclusive_at_100_m(loop_m, degree):
    """Include the floor's endpoint. A loop at exactly 100 m earns its
    two legs as a loop road, while one 0.1 m short earns nothing and
    leaves the stub's end in `dead_end_nodes`, classified as a street
    end."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    _add_edge(graph, coords, 1, 2)
    graph.add_edge(2, 2, osmid=77, length=loop_m)
    graph.add_edge(2, 2, osmid=77, length=loop_m)
    clusters = clusters_from_graph(graph, tolerance_m=5.0, min_degree=1)
    assert {c.osmids: c.degree for c in clusters} == {(1,): 1, (2,): degree}
    assert (2 in dead_end_nodes(graph)) is (degree == 1)
