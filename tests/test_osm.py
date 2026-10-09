from pathlib import Path

import pytest

osmnx = pytest.importorskip("osmnx")

import geopandas as gpd
import networkx as nx
from shapely.geometry import box
from shapely.ops import unary_union

from flagstones import AnisotropicCrsError, CrsScaleError
from flagstones.osm import (
    SEED_CUSTOM_FILTER,
    InsufficientResponseError,
    _require_faithful_crs,
    project_graph,
    tiles_for_polygon,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _toppenish():
    return osmnx.load_graphml(FIXTURES / "toppenish.graphml")


def _extent(graph):
    xs = [d["x"] for _, d in graph.nodes(data=True)]
    ys = [d["y"] for _, d in graph.nodes(data=True)]
    return box(min(xs), min(ys), max(xs), max(ys))


def _projected_polygon(polygon, crs):
    """The input polygon reprojected into the tiling CRS. The coverage
    tests below use this shape as ground truth, because a rectangle
    pinned to the projected nodes comes up short. Meridian convergence
    turns the lon/lat box about 2 degrees in UTM, so its corners, and
    the tiles in them, reach past such a rectangle.
    """
    return gpd.GeoSeries([polygon], crs=4326).to_crs(crs).iloc[0]


def test_tiles_for_polygon_tiles_in_the_utm_zone_without_a_network_call():
    graph = _toppenish()
    result = tiles_for_polygon(_extent(graph), graph)
    assert result.crs == "EPSG:32610"
    assert len(result.tiles) > 0


def test_tiles_for_polygon_keeps_tiles_inside_the_polygon():
    graph = _toppenish()
    polygon = _extent(graph)
    result = tiles_for_polygon(polygon, graph)
    bound = _projected_polygon(polygon, result.crs).buffer(1e-6)
    assert all(bound.covers(t.geometry) for t in (*result.tiles, *result.fallback))


def test_tiles_and_fallback_tiles_cover_the_polygon_without_overlap():
    graph = _toppenish()
    polygon = _extent(graph)
    result = tiles_for_polygon(polygon, graph)
    bound = _projected_polygon(polygon, result.crs)
    pieces = [t.geometry for t in (*result.tiles, *result.fallback)]
    assert result.fallback
    assert unary_union(pieces).area == pytest.approx(bound.area, rel=1e-6)
    assert sum(p.area for p in pieces) == pytest.approx(bound.area, rel=1e-6)


def test_fallback_none_leaves_only_intersection_tiles():
    graph = _toppenish()
    result = tiles_for_polygon(_extent(graph), graph, fallback=None)
    assert result.tiles and result.fallback == ()


def _lone_node_graph():
    """A junction inside the polygon with every street leg reaching
    outside it, the leftover once osmnx truncates the fetched graph
    to the polygon. Then osmnx keeps no edges, and the result is a
    graph of nodes alone.
    """
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node(1, x=-120.31, y=46.38, street_count=4)
    return graph


@pytest.mark.parametrize(
    "graph", [None, nx.MultiDiGraph(crs="EPSG:4326"), _lone_node_graph()]
)
def test_a_polygon_with_no_road_is_tiled_by_fallback_cells_alone(graph):
    polygon = _extent(_toppenish())
    result = tiles_for_polygon(polygon, graph)
    assert result.crs == "EPSG:32610"
    assert result.tiles == () and result.clusters == ()
    assert {t.kind for t in result.fallback} == {"fallback"}
    bound = _projected_polygon(polygon, result.crs)
    assert sum(t.geometry.area for t in result.fallback) == pytest.approx(
        bound.area, rel=1e-6
    )


def test_project_graph_refuses_a_crs_that_stretches_the_build():
    with pytest.raises(AnisotropicCrsError):
        project_graph(_toppenish(), "EPSG:6933")


def test_project_graph_refuses_a_crs_undefined_where_the_graph_lies():
    """A south-polar orthographic projection cannot show Washington.
    PROJ answers NaN for the anisotropy there, and NaN fails every
    comparison, so the anisotropy gate would wave a broken CRS
    straight through. Before comparing the reading, the guard checks
    it for NaN.
    """
    with pytest.raises(AnisotropicCrsError, match="not defined"):
        project_graph(_toppenish(), "ESRI:102037")


def test_the_seed_filter_is_the_seed_rule_in_overpass_form():
    assert SEED_CUSTOM_FILTER == (
        '["highway"]["highway"!~"^(footway|path|pedestrian|steps|cycleway|bridleway|'
        'corridor|proposed|construction|abandoned|platform|raceway|service|track)$"]'
        '["access"!~"^(no|private)$"]'
    )


def test_insufficient_response_error_is_osmnx_own_class():
    from osmnx._errors import InsufficientResponseError as theirs

    assert InsufficientResponseError is theirs
    assert issubclass(InsufficientResponseError, ValueError)


def test_fetch_seed_graph_asks_overpass_for_the_seed_rule(monkeypatch):
    from flagstones import osm

    seen = {}

    def fake(polygon, **kwargs):
        seen.update(kwargs)
        return "graph"

    monkeypatch.setattr(osm, "graph_from_polygon", fake)
    assert osm.fetch_seed_graph(box(0, 0, 1, 1)) == "graph"
    assert seen == {
        "custom_filter": SEED_CUSTOM_FILTER,
        "simplify": True,
        "retain_all": True,
    }


def _buffer_only_response():
    # The only mapped way lies 0.0026 degrees east of the requested box,
    # about 200 m past its edge and inside osmnx's 500 m download buffer.
    return {
        "elements": [
            {"type": "node", "id": 1, "lat": 46.372, "lon": -120.3074},
            {"type": "node", "id": 2, "lat": 46.378, "lon": -120.3074},
            {
                "type": "way",
                "id": 10,
                "nodes": [1, 2],
                "tags": {"highway": "residential"},
            },
        ]
    }


def test_a_way_only_in_the_download_buffer_is_an_insufficient_response(monkeypatch):
    import osmnx._overpass as overpass

    from flagstones import osm

    monkeypatch.setattr(
        overpass,
        "_download_overpass_network",
        lambda *a, **k: iter([_buffer_only_response()]),
    )
    with pytest.raises(InsufficientResponseError):
        osm.fetch_seed_graph(box(-120.32, 46.37, -120.31, 46.38))


def test_another_value_error_passes_through_fetch_seed_graph(monkeypatch):
    from flagstones import osm

    def boom(polygon, **kwargs):
        raise ValueError("something else")

    monkeypatch.setattr(osm, "graph_from_polygon", boom)
    with pytest.raises(ValueError, match="something else") as caught:
        osm.fetch_seed_graph(box(0, 0, 1, 1))
    assert not isinstance(caught.value, InsufficientResponseError)


def test_a_stretched_crs_fails_with_or_without_roads():
    with pytest.raises(AnisotropicCrsError):
        tiles_for_polygon(_extent(_toppenish()), None, crs="EPSG:6933")


@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:32631"])
def test_project_graph_refuses_a_crs_whose_scale_is_wrong_there(crs):
    """Across Toppenish, Web Mercator reads a meter 1.45 times too
    long, and UTM zone 31N, whose central meridian lies about 123
    degrees of longitude away, reads 1.22 times. Both projections are
    conformal, so only the scale gate refuses them. Tiling in either
    would stretch every meter-denominated parameter silently.
    """
    with pytest.raises(CrsScaleError, match="scale"):
        project_graph(_toppenish(), crs)


@pytest.mark.parametrize(
    ("crs", "lon", "lat"),
    [
        # UTM 10N probes: its central meridian, the east rim at the
        # equator, a point a degree beyond the east rim, and a point a
        # degree beyond the west rim at latitude 60.
        ("EPSG:32610", -123.0, 46.0),
        ("EPSG:32610", -120.0, 0.0),
        ("EPSG:32610", -119.0, 0.5),
        ("EPSG:32610", -127.0, 60.0),
        # Washington's meter-based state plane zones, north and south.
        ("EPSG:32148", -120.5, 48.0),
        ("EPSG:32149", -120.5, 46.0),
        # Equal-area CRSs a consumer might tile in, probed at points
        # inside the area each one serves.
        ("EPSG:5070", -96.0, 37.0),
        ("EPSG:5070", -122.3, 47.6),
        ("EPSG:5070", -81.8, 24.55),
        ("EPSG:3035", 10.0, 52.0),
        ("EPSG:10592", 20.0, 5.0),
        ("EPSG:6933", -100.0, 30.0),
        ("ESRI:54034", 36.8, -1.3),
    ],
)
def test_the_crs_guard_passes_a_crs_true_to_shape_and_scale_there(crs, lon, lat):
    _require_faithful_crs(crs, lon, lat)


def test_the_crs_guard_refuses_a_crs_undefined_at_the_point():
    with pytest.raises(AnisotropicCrsError, match="not defined"):
        _require_faithful_crs("ESRI:102037", -120.31, 46.38)


@pytest.mark.parametrize("crs", ["ESRI:102039", "epsg:32610", 32610])
def test_a_polygon_with_no_road_names_its_crs_as_the_graph_branch_does(crs):
    """The branch that skips the network runs `canonical_crs` over
    `crs`, same as the projected path. Feed it an ESRI alias, a
    lowercase EPSG string, or a bare integer, and cells mint from the same
    ID space either way.
    """
    polygon = _extent(_toppenish())
    without = tiles_for_polygon(polygon, None, crs=crs)
    with_roads = tiles_for_polygon(polygon, _toppenish(), crs=crs)
    assert without.crs == with_roads.crs
    assert {t.native_id.split(":f:")[0] for t in without.fallback} == {
        t.native_id.split(":f:")[0] for t in with_roads.fallback
    }
    assert {t.seed_crs for t in without.fallback} == {with_roads.crs}


def test_an_invalid_polygon_is_refused_by_name():
    from shapely import Polygon

    bow = Polygon(
        [(-120.33, 46.37), (-120.29, 46.39), (-120.29, 46.37), (-120.33, 46.39)]
    )
    with pytest.raises(ValueError, match="invalid"):
        tiles_for_polygon(bow, _toppenish())


def test_a_polygon_reprojection_makes_invalid_is_repaired():
    """`tiles_for_polygon` must repair polygons that a reprojection
    breaks. This shell runs along latitude 46.35 for 0.3 degrees, and
    the transform trades that straight segment for a chord, so a hole
    vertex 0.2 m above the segment ends up outside the outline.
    """
    from shapely import Polygon

    shell = [(-120.45, 46.35), (-120.15, 46.35), (-120.15, 46.42), (-120.45, 46.42)]
    hole = [(-120.301, 46.36), (-120.300, 46.35 + 2e-6), (-120.299, 46.36)]
    polygon = Polygon(shell, [hole])
    assert polygon.is_valid
    result = tiles_for_polygon(polygon, _toppenish())
    assert result.tiles and result.fallback
