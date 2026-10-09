import math

import networkx as nx
import numpy as np
import pytest
from shapely import Point, Polygon
from shapely.geometry import box

from flagstones.clip import _ClipIndex, node_union_clip, repair_reprojected
from flagstones.graph import dead_end_nodes
from tests.graphs import _add_edge, _grid_fixture


def test_node_union_clip_is_the_buffered_junctions_with_street_ends_left_out():
    """Keep street-end discs out of the union, because a disc on a
    street end bulges a round lobe of the clip into the next junction's
    tile.

    The fixture builds a tee at the origin with a stub ending 100 m
    east. Union reach therefore stops 250 m past the tee, and x=340,
    which a disc around the stub end would claim, stays outside."""
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    coords = {1: (0.0, 0.0), 2: (0.0, 300.0), 3: (0.0, -300.0), 4: (100.0, 0.0)}
    for node, (x, y) in coords.items():
        graph.add_node(node, x=x, y=y)
    for u, v in ((1, 2), (1, 3), (1, 4)):
        _add_edge(graph, coords, u, v)
    assert dead_end_nodes(graph) == [2, 3, 4]
    clip = node_union_clip(graph, buffer_m=250.0)
    assert clip.covers(Point(249.0, 0.0))
    assert not clip.covers(Point(251.0, 0.0))
    assert not clip.covers(Point(340.0, 0.0))
    assert clip.area == pytest.approx(Point(0, 0).buffer(250.0).area)


def test_node_union_clip_within_keeps_only_the_nodes_inside():
    graph = _grid_fixture()
    xs = [d["x"] for _, d in graph.nodes(data=True)]
    ys = [d["y"] for _, d in graph.nodes(data=True)]
    left_half = box(min(xs) - 1.0, min(ys) - 1.0, (min(xs) + max(xs)) / 2, max(ys) + 1)
    whole = node_union_clip(graph, buffer_m=50.0)
    part = node_union_clip(graph, buffer_m=50.0, within=left_half)
    assert part.area < whole.area
    assert whole.covers(part)
    right_edge = Point(max(xs) + 49.0, ys[0])
    assert whole.covers(right_edge) and not part.covers(right_edge)


def test_the_disc_union_dissolves_across_its_blocks():
    """Check that `_union_of_discs`, which dissolves discs inside square
    blocks before unioning the block results, matches a single-pass
    union.

    Two rows of 134 points at 15 m steps span 1.995 km. At a 10 m
    radius the discs weld across several blocks into one Polygon,
    within 1e-6 of a direct `union_all`."""
    import shapely

    from flagstones.clip import _union_of_discs

    xs = [15.0 * i for i in range(134)]
    x = np.array(xs + xs)
    y = np.array([0.0] * len(xs) + [12.0] * len(xs))
    blocked = _union_of_discs(x, y, 10.0)
    direct = shapely.union_all(shapely.buffer(shapely.points(x, y), 10.0, quad_segs=16))
    assert blocked.geom_type == "Polygon"
    assert blocked.symmetric_difference(direct).area < 1e-6


def test_node_union_clip_of_no_nodes_is_empty():
    graph = nx.MultiDiGraph(crs="EPSG:5070")
    assert node_union_clip(graph, buffer_m=250.0).is_empty


def test_the_clip_index_matches_the_whole_clip_piece_by_piece():
    """A clip over a piece's vertex allowance arrives split into
    pieces, and intersecting a region through its nearby pieces must
    answer exactly what an overlay against the whole clip answers.

    The 600 Voronoi regions here exercise both interesting answers:
    regions sealed inside a single piece return whole, and regions cut
    by a piece seam or the outer border match the direct overlay either
    way."""
    from shapely import MultiPoint, voronoi_polygons
    from shapely.ops import unary_union

    rng_points = [
        Point(
            2000.0 * math.cos(i * 0.37) + 1.3 * i, 2000.0 * math.sin(i * 0.53) + 0.7 * i
        )
        for i in range(600)
    ]
    clip = unary_union(rng_points).buffer(180.0)
    index = _ClipIndex(clip)
    assert len(index.pieces) > 1
    assert unary_union(index.pieces).area == pytest.approx(clip.area, rel=1e-9)
    seeds = MultiPoint([Point(p.x * 0.9 + 40.0, p.y * 0.9 - 30.0) for p in rng_points])
    regions = voronoi_polygons(seeds, extend_to=box(*clip.bounds), ordered=True)
    inside_seen = boundary_seen = 0
    for region in regions.geoms:
        direct = region.intersection(clip)
        indexed = index.intersection(region)
        assert indexed.area == pytest.approx(direct.area, abs=1e-6)
        assert indexed.symmetric_difference(direct).area < 1e-6
        if direct.equals(region):
            inside_seen += 1
        elif not direct.is_empty:
            boundary_seen += 1
    assert inside_seen > 0 and boundary_seen > 0


def test_repair_reprojected_returns_a_valid_geometry_itself():
    square = box(0.0, 0.0, 10.0, 10.0)
    assert repair_reprojected(square) is square


def test_repair_reprojected_keeps_both_lobes_of_a_bow_tie():
    bow_tie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    repaired = repair_reprojected(bow_tie)
    assert repaired.is_valid
    assert repaired.geom_type == "MultiPolygon"
    assert repaired.area == pytest.approx(50.0)


def test_repair_reprojected_refuses_a_ring_that_collapses_to_a_line():
    collapsed = Polygon([(0, 0), (1, 1), (2, 2), (0, 0)])
    assert not collapsed.is_valid
    with pytest.raises(ValueError, match="no polygon"):
        repair_reprojected(collapsed)
