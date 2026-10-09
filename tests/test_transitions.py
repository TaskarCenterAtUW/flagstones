import math

import networkx as nx

from flagstones.transitions import classify_transitions
from tests.graphs import _add_edge, _carriageway_join_fixture


def test_classify_transitions_detects_a_carriageway_join():
    found = classify_transitions(_carriageway_join_fixture())
    assert found == {3}


def test_classify_transitions_detects_a_one_way_fork():
    """Three one-way legs meet at node 2, all under the name WA 270.
    Traffic arrives from node 1 and leaves toward nodes 3 and 4, the
    split of one road into two carriageways, and the classifier
    reports node 2.
    """
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (400.0, 10.0), 4: (400.0, -10.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    for u, v in ((1, 2), (2, 3), (2, 4)):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        graph.add_edge(
            u,
            v,
            osmid=u * 100 + v,
            length=math.hypot(ux - vx, uy - vy),
            oneway=True,
            name="WA 270",
        )
    assert classify_transitions(graph) == {2}


def test_classify_transitions_requires_a_shared_road_name():
    """Renaming the join fixture's legs A, B, and C leaves the geometry
    exactly as it was. A side street joining a one-way avenue maps to
    identical wiring all the same, so the classifier has to lean on
    the shared name to tell the cases apart. Drop that requirement and
    node 3 qualifies again.
    """
    graph = _carriageway_join_fixture(leg_names=("A", "B", "C"))
    assert classify_transitions(graph) == set()
    assert classify_transitions(graph, require_same_road=False) == {3}


def test_classify_transitions_rejects_a_wide_angle_continuation():
    """All three legs answer to Grand Avenue, and node 3 keeps the join
    fixture's wiring. However, the continuation departs that pair at
    about 90 degrees, short of the 120-degree floor, so the
    classifier passes over node 3.
    """
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 8.0), 2: (0.0, -8.0), 3: (200.0, 0.0), 4: (200.0, 200.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    (x1, y1), (x3, y3) = coords[1], coords[3]
    graph.add_edge(
        1,
        3,
        osmid=103,
        length=math.hypot(x1 - x3, y1 - y3),
        oneway=True,
        name="Grand Avenue",
    )
    x2, y2 = coords[2]
    graph.add_edge(
        3,
        2,
        osmid=302,
        length=math.hypot(x3 - x2, y3 - y2),
        oneway=True,
        name="Grand Avenue",
    )
    x4, y4 = coords[4]
    length = math.hypot(x3 - x4, y3 - y4)
    graph.add_edge(3, 4, osmid=304, length=length, name="Grand Avenue")
    graph.add_edge(4, 3, osmid=304, length=length, name="Grand Avenue")
    assert classify_transitions(graph) == set()


def test_classify_transitions_rejects_carriageways_that_spread_too_far():
    """Legs leaving node 2 at 90 degrees describe two roads parting,
    and the default 60-degree spread ceiling refuses them. The check
    fires only on spreads above the ceiling, so widening
    `spread_max_deg` to 90 admits the same fork.
    """
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (300.0, 100.0), 4: (300.0, -100.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    for u, v in ((1, 2), (2, 3), (2, 4)):
        (ux, uy), (vx, vy) = coords[u], coords[v]
        graph.add_edge(
            u,
            v,
            osmid=u * 100 + v,
            length=math.hypot(ux - vx, uy - vy),
            oneway=True,
            name="WA 270",
        )
    assert classify_transitions(graph) == set()
    assert classify_transitions(graph, spread_max_deg=90.0) == {2}


def test_classify_transitions_ignores_an_ordinary_t_junction():
    """A ring of four two-way streets carries a cul-de-sac stub off
    a corner. The classifier admits joins (a two-way leg with a
    matched in and out) and forks (three one-way legs), and this graph
    offers it no node carrying either wiring.
    """
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {
        1: (0.0, 0.0),
        2: (200.0, 0.0),
        3: (200.0, 200.0),
        4: (0.0, 200.0),
        5: (200.0, -120.0),
    }
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    for u, v in ((1, 2), (2, 3), (3, 4), (4, 1), (2, 5)):
        _add_edge(graph, coords, u, v)
    assert classify_transitions(graph) == set()
