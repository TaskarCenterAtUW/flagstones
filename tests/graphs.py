"""Hand-written graph fixtures the suite borrows, a road layout per
fixture."""

import math
from collections import Counter

import networkx as nx
from shapely import LineString


def _add_edge(graph, coords, u, v):
    ux, uy = coords[u]
    vx, vy = coords[v]
    length = math.hypot(ux - vx, uy - vy)
    # osmnx records a two-way street as an edge in each direction:
    graph.add_edge(u, v, osmid=u * 100 + v, length=length)
    graph.add_edge(v, u, osmid=u * 100 + v, length=length)


def _one_edge_graph(*, crs: str, x: float, y: float) -> nx.MultiDiGraph:
    """Join two nodes 10 units apart with a single edge.

    For a CRS-only test, this is the least graph that survives
    projection: `ox.project_graph` declines to run on edge-free
    input."""
    graph = nx.MultiDiGraph(crs=crs)
    graph.add_node(1, x=x, y=y, street_count=1)
    graph.add_node(2, x=x + 10.0, y=y, street_count=1)
    _add_edge(graph, {1: (x, y), 2: (x + 10.0, y)}, 1, 2)
    return graph


def _divided_road_fixture():
    """Cross two divided carriageways so a conceptual junction spreads
    across a 20 m square, a node at each of its corners.

    All four corners run outward at least one arm, so the merged
    intersection's degree counts real road legs rather than the square's
    inside edges."""
    graph = nx.MultiDiGraph(crs="EPSG:32610")
    coords = {
        1: (1000.0, 1010.0),
        2: (1020.0, 1010.0),
        3: (1000.0, 990.0),
        4: (1020.0, 990.0),
        10: (1000.0, 1200.0),
        11: (1020.0, 1200.0),
        12: (1000.0, 800.0),
        13: (1020.0, 800.0),
        14: (800.0, 1000.0),
        15: (1200.0, 1000.0),
    }
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=3)
    for u, v in (
        (1, 2),
        (3, 4),
        (1, 3),
        (2, 4),
        (1, 10),
        (2, 11),
        (3, 12),
        (4, 13),
        (1, 14),
        (4, 15),
    ):
        _add_edge(graph, coords, u, v)
    return graph


def _cul_de_sac_fixture():
    """Run a street A-B-C all the way to a terminal stub.

    Against the default `min_degree` (3), the filter drops
    the whole graph: all three nodes top out at two streets, at
    most."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (50.0, 0.0), 3: (100.0, 0.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=2)
    for u, v in ((1, 2), (2, 3)):
        _add_edge(graph, coords, u, v)
    return graph


def _grid_coords():
    return {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        3: (400.0, 0.0),
        4: (0.0, 200.0),
        5: (200.0, 200.0),
        6: (400.0, 200.0),
        7: (0.0, 400.0),
        8: (200.0, 400.0),
        9: (400.0, 400.0),
    }


def _grid_fixture():
    """Lay streets 200 m apart, east-west and north-south, into a 3x3
    lattice in EPSG:5070.

    Corners meet two streets, side midpoints three, the middle node
    four, so `min_degree=3` by default lets just five of nine nodes
    through. In the merge tests here, that spacing stays far past any
    5 m tolerance."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = _grid_coords()
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=2)
    edges = (
        (1, 2),
        (2, 3),
        (4, 5),
        (5, 6),
        (7, 8),
        (8, 9),
        (1, 4),
        (4, 7),
        (2, 5),
        (5, 8),
        (3, 6),
        (6, 9),
    )
    for u, v in edges:
        _add_edge(graph, coords, u, v)
    return graph


def _t_junction_with_dead_end_fixture():
    """Close a ring road 1-2-3-4 and hang a cul-de-sac 2-5 off a
    corner, giving node 2 three legs while node 5 marks the dead end.

    osmnx stamps a `street_count` onto the nodes of every graph it
    builds, and its `consolidate_intersections(dead_ends=False)` reads
    that attribute and removes nothing when a graph lacks one, so these
    nodes carry matching stamps."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        3: (200.0, 200.0),
        4: (0.0, 200.0),
        5: (200.0, -120.0),
    }
    street_count = {1: 2, 2: 3, 3: 2, 4: 2, 5: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3), (3, 4), (4, 1), (2, 5)):
        _add_edge(graph, coords, u, v)
    return graph


def _grid_with_cul_de_sacs_fixture():
    """Extend `_grid_fixture` with a short dead-end stub running 60 m
    north of the middle node.

    In the wild, simplified graphs keep driveways and other minor stubs
    exactly like this one, and the degree filter exists to sort them
    back out."""
    graph = _grid_fixture()
    coords = _grid_coords()
    coords[99] = (200.0, 260.0)
    graph.add_node(99, x=200.0, y=260.0, street_count=2)
    _add_edge(graph, coords, 5, 99)
    return graph


def _wgs84_fixture():
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node(1, x=-117.18, y=46.73, street_count=3)
    graph.add_node(2, x=-117.17, y=46.73, street_count=3)
    graph.add_node(3, x=-117.18, y=46.72, street_count=3)
    _add_edge(graph, {1: (-117.18, 46.73), 2: (-117.17, 46.73)}, 1, 2)
    _add_edge(graph, {1: (-117.18, 46.73), 3: (-117.18, 46.72)}, 1, 3)
    return graph


def _already_consolidated_fixture():
    """Stamp `osmid_original` on a projected graph before
    `clusters_from_graph` ever sees it.

    The stamp is a consolidation pass's stand-in output, and a second
    pass must refuse it."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    graph.add_node(1, x=0.0, y=0.0, street_count=3, osmid_original=[10, 11])
    graph.add_node(2, x=200.0, y=0.0, street_count=3)
    _add_edge(graph, {1: (0.0, 0.0), 2: (200.0, 0.0)}, 1, 2)
    return graph


def _merged_carriageway_pair_fixture():
    """Run a divided road east, with each carriageway pair 6 m apart and
    tied by a short cross link that consolidation snaps into one node;
    the next pair waits 200 m east.

    The carriageways run one-way (1 -> 3 eastbound, 4 -> 2 westbound),
    so the merged nodes remain exactly one road arm apart. A two-way
    side street departs each carriageway node, lifting a merged pair to
    three legs: the arm plus a side street per carriageway."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 3.0),
        2: (0.0, -3.0),
        3: (200.0, 3.0),
        4: (200.0, -3.0),
        5: (-100.0, 3.0),
        6: (300.0, 3.0),
        7: (0.0, -100.0),
        8: (200.0, -100.0),
    }
    street_count = {1: 3, 2: 3, 3: 3, 4: 3, 5: 1, 6: 1, 7: 1, 8: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (3, 4), (1, 5), (3, 6), (2, 7), (4, 8)):
        _add_edge(graph, coords, u, v)
    for u, v in ((1, 3), (4, 2)):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        graph.add_edge(u, v, osmid=u * 100 + v, length=math.hypot(ux - vx, uy - vy))
    return graph


def _t_junction_with_link_fixture(link_highway):
    """Tag the extra leg 2-4 off through road 1-2-3 with whatever
    `link_highway` supplies, modelling a ramp that meets the road at
    node 2. The fixture stamps node 2 a `street_count` of 3, the
    three-way junction osmnx's count reads there."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (400.0, 0.0), 4: (200.0, 200.0)}
    street_count = {1: 1, 2: 3, 3: 1, 4: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3)):
        _add_edge(graph, coords, u, v)
    graph.add_edge(2, 4, osmid=204, length=200.0, highway=link_highway)
    graph.add_edge(4, 2, osmid=204, length=200.0, highway=link_highway)
    return graph


def _road_end_with_ramps_fixture(ramps):
    """Finish road 1-2 at node 2, then run `ramps` ramp legs
    (nodes 4, 5, ...) out of it.

    Without the ramps, node 2 is a plain dead end."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0)}
    for k in range(ramps):
        coords[4 + k] = (400.0, -100.0 + 100.0 * k)
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=1 + ramps if node == 2 else 1)
    _add_edge(graph, coords, 1, 2)
    for k in range(ramps):
        graph.add_edge(2, 4 + k, osmid=200 + k, length=200.0, highway="primary_link")
        graph.add_edge(4 + k, 2, osmid=200 + k, length=200.0, highway="primary_link")
    return graph


def _junction_with_slip_lane_end_fixture():
    """Build a three-way junction at node 2 with legs toward 1, toward
    5, and onward through node 7 to 3.

    The slip lane leaves for node 8 from node 7, set 6 m east of that
    junction. Edge 2-7 keeps the pair adjacent and inside the merge
    radius, yet two road legs plus a ramp disqualify node 7 as a
    junction in its own right."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        7: (206.0, 0.0),
        3: (400.0, 0.0),
        5: (200.0, 200.0),
        8: (206.0, -200.0),
    }
    street_count = {1: 1, 2: 3, 7: 3, 3: 1, 5: 1, 8: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 7), (7, 3), (2, 5)):
        _add_edge(graph, coords, u, v)
    graph.add_edge(7, 8, osmid=708, length=197.0, highway="primary_link")
    graph.add_edge(8, 7, osmid=708, length=197.0, highway="primary_link")
    return graph


def _link_between_junctions_fixture(link_m):
    """Set node 7 `link_m` east of the junction at node 2 (legs to 1, 3
    and 5), riding the one-way through road 6-7-8, and tie the pair
    with a one-way `primary_link`.

    Node 7 ends up with two road legs plus that link, the arrangement
    where Roosevelt Way NE meets the connector from Eastlake Avenue NE
    at NE Campus Parkway."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        3: (200.0, 200.0),
        5: (200.0, -200.0),
        7: (200.0 + link_m, 0.0),
        6: (200.0 + link_m, 200.0),
        8: (200.0 + link_m, -200.0),
    }
    street_count = {1: 1, 2: 3, 3: 1, 5: 1, 7: 3, 6: 1, 8: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3), (2, 5)):
        _add_edge(graph, coords, u, v)
    graph.add_edge(6, 7, osmid=607, length=200.0, oneway=True)
    graph.add_edge(7, 8, osmid=708, length=200.0, oneway=True)
    graph.add_edge(2, 7, osmid=207, length=link_m, highway="primary_link", oneway=True)
    return graph


def _wedged_link_node_fixture():
    """Wedge node 2 between junction 1 and junction 3, 8 m of plain road
    to each one, inside both merge radii, with the route between the
    junctions crossing node 2 since edge 1-3 is absent.

    A `primary_link` runs from 2 out to node 8, 200 m off and near no
    junction, so the connector rule grants node 2 no excuse here.
    Southeast Eastgate Way at 148th Avenue SE in Bellevue produced this
    shape."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (192.0, 0.0),
        2: (200.0, 0.0),
        3: (208.0, 0.0),
        4: (192.0, 100.0),
        5: (92.0, 0.0),
        6: (208.0, 100.0),
        7: (308.0, 0.0),
        8: (400.0, 0.0),
    }
    street_count = {1: 3, 2: 3, 3: 3, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (2, 3), (1, 4), (1, 5), (3, 6), (3, 7)):
        _add_edge(graph, coords, u, v)
    (x2, y2), (x8, y8) = coords[2], coords[8]
    length = math.hypot(x2 - x8, y2 - y8)
    graph.add_edge(2, 8, osmid=208, length=length, highway="primary_link")
    graph.add_edge(8, 2, osmid=208, length=length, highway="primary_link")
    return graph


def _median_pair_fixture(gap_m=8.0, same_name=True):
    """Split the one-way carriageways of "Main Street" by a median
    `gap_m` wide, rejoining them at the west end (node 1) and the east
    end (node 6).

    At x=200, a side street terminates on each carriageway. The
    carriageways themselves meet only back at nodes 1 and 6. Flip
    `same_name=False` and the south carriageway takes "Other Street",
    which matters because the median tie demands the same road."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    half = gap_m / 2.0
    coords = {
        1: (0.0, 0.0),
        6: (400.0, 0.0),
        2: (200.0, half),
        3: (200.0, -half),
        4: (200.0, 200.0),
        5: (200.0, -200.0),
    }
    street_count = {1: 2, 6: 2, 2: 3, 3: 3, 4: 1, 5: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    south = "Main Street" if same_name else "Other Street"
    for u, v, name in (
        (6, 2, "Main Street"),
        (2, 1, "Main Street"),
        (1, 3, south),
        (3, 6, south),
    ):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        graph.add_edge(
            u,
            v,
            osmid=u * 100 + v,
            length=math.hypot(ux - vx, uy - vy),
            oneway=True,
            name=name,
            highway="primary",
        )
    _add_edge(graph, coords, 2, 4)
    _add_edge(graph, coords, 3, 5)
    return graph


def _carriageway_join_fixture(
    leg_names=("Grand Avenue", "Grand Avenue", "Grand Avenue"),
):
    """Give node 3 a one-way arrival from node 1, a one-way departure
    onward to node 2, and a two-way continuation out to node 4.
    `leg_names` labels those edges in order (into, out-of,
    continuation)."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 8.0), 2: (0.0, -8.0), 3: (200.0, 0.0), 4: (400.0, 0.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    into_name, out_name, continuation_name = leg_names
    (x1, y1), (x3, y3) = coords[1], coords[3]
    graph.add_edge(
        1,
        3,
        osmid=103,
        length=math.hypot(x1 - x3, y1 - y3),
        oneway=True,
        name=into_name,
    )
    x2, y2 = coords[2]
    graph.add_edge(
        3, 2, osmid=302, length=math.hypot(x3 - x2, y3 - y2), oneway=True, name=out_name
    )
    x4, y4 = coords[4]
    length = math.hypot(x3 - x4, y3 - y4)
    graph.add_edge(3, 4, osmid=304, length=length, name=continuation_name)
    graph.add_edge(4, 3, osmid=304, length=length, name=continuation_name)
    return graph


def _carriageway_join_junction_fixture(join_x=300.0):
    """Anchor junction 1 with two-way arms to the carriageway join at
    node 2 and to nodes 5 and 6. The one-way pair 3 and 4 brings traffic
    into the join and takes it back out.

    At the default `join_x`, the join measures 200 m from every
    neighbor, past any tolerance the suite tests, so the join and the
    junction remain apart whether or not contraction runs."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (500.0, 0.0),
        2: (join_x, 0.0),
        3: (100.0, 8.0),
        4: (100.0, -8.0),
        5: (650.0, 100.0),
        6: (650.0, -100.0),
    }
    street_count = {1: 3, 2: 3, 3: 1, 4: 1, 5: 1, 6: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])

    def _oneway(u, v, name):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        graph.add_edge(
            u,
            v,
            osmid=u * 100 + v,
            length=math.hypot(ux - vx, uy - vy),
            oneway=True,
            name=name,
        )

    def _twoway(u, v, name):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        length = math.hypot(ux - vx, uy - vy)
        graph.add_edge(u, v, osmid=u * 100 + v, length=length, name=name)
        graph.add_edge(v, u, osmid=u * 100 + v, length=length, name=name)

    _oneway(3, 2, "Grand Avenue")
    _oneway(2, 4, "Grand Avenue")
    _twoway(1, 2, "Grand Avenue")
    _twoway(1, 5, "Oak Street")
    _twoway(1, 6, "Pine Street")
    return graph


def _parallel_way_fixture():
    """Map a second, separate way between nodes 2 and 3 of road 1-2-3: a
    frontage road or old alignment that peels away after node 2 and
    rejoins at node 3.

    Across just two neighbors, node 2 therefore meets three street
    legs, and the stamped `street_count` matches what osmnx would
    record."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (400.0, 0.0)}
    street_count = {1: 1, 2: 3, 3: 2}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    _add_edge(graph, coords, 1, 2)
    _add_edge(graph, coords, 2, 3)
    # this way keeps its own bow and osmid, so it stays distinct from
    # the edge already mapped between 2 and 3.
    bow = LineString([(200.0, 0.0), (300.0, 40.0), (400.0, 0.0)])
    graph.add_edge(2, 3, osmid=99, length=bow.length, geometry=bow)
    graph.add_edge(3, 2, osmid=99, length=bow.length, geometry=bow.reverse())
    return graph


def _self_loop_fixture(through=False, short=False, loops=1):
    """End stub 1-2 in a loop way that departs node 2 and returns:
    224 m around, or 56 m when `short`.

    By osmnx's counting, node 2 carries 3 streets there (one street
    plus the loop counted twice), and the fixture stamps that reading.
    With `through`,
    road 1-2 runs on to node 3 so the loop hangs off a through road
    instead of a stub. Give `loops` a count and it draws that many loop
    ways off node 2, each separate with its own osmid."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0)}
    if through:
        coords[3] = (400.0, 0.0)
    for node, (x, y) in coords.items():
        graph.add_node(
            node, x=x, y=y, street_count={1: 1, 2: 4 if through else 3, 3: 1}[node]
        )
    _add_edge(graph, coords, 1, 2)
    if through:
        _add_edge(graph, coords, 2, 3)
    reach = 15.0 if short else 60.0
    for n in range(loops):
        # distinct osmids per way, bulging alternately east and west
        # of node 2.
        side = 1.0 if n % 2 == 0 else -1.0
        loop = LineString(
            [
                (200.0, 0.0),
                (200.0 + reach * side, reach * 2 / 3),
                (200.0 + reach * side, -reach * 2 / 3),
                (200.0, 0.0),
            ]
        )
        osmid = 77 + n
        graph.add_edge(2, 2, osmid=osmid, length=loop.length, geometry=loop)
        graph.add_edge(2, 2, osmid=osmid, length=loop.length, geometry=loop.reverse())
    return graph


def _road_and_path_fixture(path_highway="footway"):
    """Lead two residential legs off node 1 and a third tagged with the
    caller's `path_highway` value, which decides whether that leg reads
    as a road or a footpath."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (0.0, 200.0), 4: (-200.0, 0.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=3 if node == 1 else 1)
    _add_edge(graph, coords, 1, 2)
    _add_edge(graph, coords, 1, 4)
    _add_edge(graph, coords, 1, 3)
    for _, _, data in graph.edges(data=True):
        data["highway"] = "residential"
    for key in graph[1][3]:
        graph.edges[1, 3, key]["highway"] = path_highway
    return graph


def _grade_separated_pair_fixture():
    """Park a three-leg junction 2 m from another, sharing no edge, an
    overpass junction above a surface one.

    The merge parts them into two clusters, while a 5 m seed grid drops
    both centroids into cell (20, 20) and hands the pipeline a seed
    collision to resolve."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (100.0, 100.0),
        11: (100.0, 300.0),
        12: (-100.0, 100.0),
        13: (100.0, -100.0),
        2: (102.0, 100.0),
        21: (302.0, 100.0),
        22: (302.0, 300.0),
        23: (302.0, -100.0),
    }
    street_count = {1: 3, 2: 3, 11: 1, 12: 1, 13: 1, 21: 1, 22: 1, 23: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 11), (1, 12), (1, 13), (2, 21), (2, 22), (2, 23)):
        _add_edge(graph, coords, u, v)
    return graph


def _two_lobes_fixture():
    """Set two road networks far enough apart that untiled ground gaps
    their clip lobes. A junction at the origin sits on a loop road, and
    its far nodes at x=300 drag the west lobe out to x=550. The east
    network crowds a junction at x=1000 behind three dead-end arms, so
    its lobe starts at x=750. The Voronoi bisector of the
    seeds falls at x=500, inside the west lobe, which first
    leaves the east seed owning a slab of western ground that touches
    no part of its own tile; the reattach step moves that slab onto the
    west tile."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (-200.0, 0.0),
        3: (0.0, 200.0),
        4: (300.0, 0.0),
        5: (300.0, 200.0),
        6: (1000.0, 0.0),
        7: (1200.0, 0.0),
        8: (1000.0, 200.0),
        9: (1000.0, -200.0),
    }
    street_count = {1: 4, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 1, 8: 1, 9: 1}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=street_count[node])
    for u, v in ((1, 2), (1, 3), (1, 4), (4, 5), (5, 1), (6, 7), (6, 8), (6, 9)):
        _add_edge(graph, coords, u, v)
    return graph


def _fallback_fixture():
    """Fire every fallback branch at a 50 m cell with a 12 m clip
    buffer, one graph doing all of it at once.

    Cell (0, 0) fills whole from a 5x5 street lattice at 12.5 m spacing.
    East of the lattice a chain of two-leg nodes runs along y=25, and
    the thin band around that chain slices cells (1, 0) through (3, 0)
    in two. The square ring road between (200, 14) and (400, 214) wraps
    a hole, while its band strands strips under a tenth of a
    cell along y=0 and y=200. The ring also spurs east to a dead end at
    (450, 101.5). Since all node pairs sit at least the lattice's 12.5 m
    apart, a 5 m tolerance merges nothing here."""
    step = 12.5
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords: dict[int, tuple[float, float]] = {}
    edges: list[tuple[int, int]] = []
    for a in range(5):
        for b in range(5):
            node = 1 + a * 5 + b
            coords[node] = (a * step, b * step)
            if a < 4:
                edges.append((node, node + 5))
            if b < 4:
                edges.append((node, node + 1))
    previous = 1 + 4 * 5 + 2  # the chain starts at lattice node (50, 25)
    for k in range(1, 12):
        coords[100 + k] = (50 + k * step, 25.0)
        edges.append((previous, 100 + k))
        previous = 100 + k
    corners = ((200.0, 14.0), (400.0, 14.0), (400.0, 214.0), (200.0, 214.0))
    ring = []
    for side in range(4):
        (x0, y0), (x1, y1) = corners[side], corners[(side + 1) % 4]
        for k in range(16):
            node = 200 + side * 16 + k
            coords[node] = (x0 + (x1 - x0) * k / 16, y0 + (y1 - y0) * k / 16)
            ring.append(node)
    edges += [(ring[k], ring[(k + 1) % len(ring)]) for k in range(len(ring))]
    edges.append((previous, 263))  # joins chain end (187.5, 25) to ring (200, 26.5)
    previous = 223  # the spur starts off the ring at (400, 101.5)
    for k in range(1, 5):
        coords[300 + k] = (400 + k * step, 101.5)
        edges.append((previous, 300 + k))
        previous = 300 + k
    legs = Counter(node for edge in edges for node in edge)
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y, street_count=legs[node])
    for u, v in edges:
        _add_edge(graph, coords, u, v)
    return graph
