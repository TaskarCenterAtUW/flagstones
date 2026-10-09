import pytest
from shapely.geometry import Point, box
from shapely.ops import unary_union

from flagstones import (
    FallbackGrid,
    NonDrivableGraphError,
    TileParams,
    generate_tiles,
)
from tests.graphs import _grid_fixture


def _params(**overrides):
    return TileParams(consolidation_tolerance_m=5.0, seed_grid_m=5.0, **overrides)


def test_generate_tiles_runs_the_published_pipeline_on_a_grid():
    result = generate_tiles(_grid_fixture(), _params())
    assert result.crs == "EPSG:5070"
    assert len(result.tiles) == len(result.seeds) > 0
    assert result.churn == {"inherited": 0, "minted": len(result.tiles), "unclaimed": 0}
    union = result.tiles[0].geometry.union(result.tiles[-1].geometry)
    assert result.clip.buffer(1e-6).covers(union)


def test_a_footprint_limits_the_seeds_and_the_clip():
    graph = _grid_fixture()
    full = generate_tiles(graph, _params())
    first = full.seeds[0]
    footprint = Point(first.x, first.y).buffer(1.0)
    small = generate_tiles(graph, _params(clip_buffer_m=1.0), footprint=footprint)
    assert [s.osmids for s in small.seeds] == [first.osmids]


def test_clip_to_trims_outer_cells_and_keeps_the_tile_set():
    graph = _grid_fixture()
    full = generate_tiles(graph, _params())
    xs = [s.x for s in full.seeds]
    ys = [s.y for s in full.seeds]
    hull = box(min(xs), min(ys), max(xs), max(ys))
    trimmed = generate_tiles(graph, _params(), clip_to=hull)
    assert [t.native_id for t in trimmed.tiles] == [t.native_id for t in full.tiles]
    assert all(hull.buffer(1e-6).covers(t.geometry) for t in trimmed.tiles)


def test_a_predecessor_seed_within_the_radius_passes_its_id_on():
    graph = _grid_fixture()
    first = generate_tiles(graph, _params())
    tile = first.tiles[0]
    previous = {"5070:0:0": Point(tile.seed_x + 1.0, tile.seed_y)}
    second = generate_tiles(
        graph, _params(), predecessor=previous, predecessor_edition="v1"
    )
    assert second.tiles[0].native_id == "5070:0:0"
    assert second.churn["inherited"] == 1


def test_a_predecessor_without_its_edition_is_refused():
    """`TileSet.churn` reads `inherited_from_edition`, and only the
    edition stamp writes that field. A reused ID with no stamp would
    book as newly minted, so a populated `predecessor` without
    `predecessor_edition` must fail the call. An empty `predecessor`
    makes no claim, passes the guard, and reports zero inherited.
    """
    graph = _grid_fixture()
    tile = generate_tiles(graph, _params()).tiles[0]
    previous = {"5070:0:0": Point(tile.seed_x, tile.seed_y)}
    with pytest.raises(ValueError, match="predecessor_edition"):
        generate_tiles(graph, _params(), predecessor=previous)
    assert generate_tiles(graph, _params(), predecessor={}).churn["inherited"] == 0


def test_a_graph_in_feet_is_refused_before_any_work():
    graph = _grid_fixture()
    graph.graph["crs"] = "EPSG:2927"
    with pytest.raises(ValueError, match="instead of meters"):
        generate_tiles(graph, _params())


def test_a_graph_with_a_seed_excluded_edge_is_refused_unless_the_check_is_skipped():
    graph = _grid_fixture()
    u, v, k = next(iter(graph.edges(keys=True)))
    graph.edges[u, v, k]["highway"] = "service"
    with pytest.raises(NonDrivableGraphError):
        generate_tiles(graph, _params())
    assert generate_tiles(graph, _params(), exclude_highway=None).tiles


def test_every_tile_param_reaches_the_step_that_reads_it(monkeypatch):
    from flagstones import pipeline

    seen = {}

    def spy(name, real):
        def wrapper(*args, **kwargs):
            seen[name] = {k: v for k, v in kwargs.items() if k != "_table"}
            return real(*args, **kwargs)

        return wrapper

    for name in (
        "clusters_from_graph",
        "collapse_seed_collisions",
        "node_union_clip",
        "tessellate",
        "inherit_ids",
    ):
        monkeypatch.setattr(pipeline, name, spy(name, getattr(pipeline, name)))
    params = TileParams(
        consolidation_tolerance_m=5.0,
        seed_grid_m=5.0,
        degree_filter_min=2,
        contract_transitions=False,
        transition_require_same_road=False,
        transition_spread_max_deg=50.0,
        transition_opposite_min_deg=130.0,
        clip_buffer_m=100.0,
        inheritance_radius_m=4.0,
        snap_centroid=False,
    )
    generate_tiles(_grid_fixture(), params)
    assert seen["clusters_from_graph"] == {
        "tolerance_m": 5.0,
        "min_degree": 2,
        "junction_degree": 2,
        "contract_transitions": False,
        "transition_require_same_road": False,
        "transition_spread_max_deg": 50.0,
        "transition_opposite_min_deg": 130.0,
    }
    assert seen["collapse_seed_collisions"] == {"grid_m": 5.0}
    assert seen["node_union_clip"] == {"buffer_m": 100.0, "within": None}
    assert {k: seen["tessellate"][k] for k in ("grid_m", "crs", "snap_centroid")} == {
        "grid_m": 5.0,
        "crs": "EPSG:5070",
        "snap_centroid": False,
    }
    assert seen["inherit_ids"] == {"radius_m": 4.0, "predecessor_edition": None}


def test_fallback_without_an_extent_is_refused():
    with pytest.raises(ValueError, match="fallback_extent"):
        generate_tiles(_grid_fixture(), _params(), fallback=FallbackGrid())


def test_an_extent_without_a_fallback_grid_is_refused():
    with pytest.raises(ValueError, match="fallback_extent needs fallback"):
        generate_tiles(_grid_fixture(), _params(), fallback_extent=box(0, 0, 1, 1))


def test_the_fallback_fills_what_the_tiles_leave_of_the_extent():
    extent = box(-600, -600, 1000, 1000)
    result = generate_tiles(
        _grid_fixture(),
        _params(clip_buffer_m=60.0),
        fallback=FallbackGrid(),
        fallback_extent=extent,
    )
    assert result.fallback
    assert result.fallback_grid == FallbackGrid()
    assert {t.kind for t in result.tiles} == {"intersection"}
    assert {t.kind for t in result.fallback} == {"fallback"}
    pieces = [t.geometry for t in (*result.tiles, *result.fallback)]
    assert unary_union(pieces).area == pytest.approx(extent.area)
    assert sum(p.area for p in pieces) == pytest.approx(extent.area)


def test_a_clip_to_that_misses_the_network_leaves_the_fallback_alone():
    extent = box(1e6, 1e6, 1e6 + 200, 1e6 + 200)
    result = generate_tiles(
        _grid_fixture(),
        _params(),
        clip_to=extent,
        fallback=FallbackGrid(cell_m=50.0),
        fallback_extent=extent,
    )
    assert result.seeds and result.tiles == ()
    assert result.clip.is_empty
    assert sum(t.geometry.area for t in result.fallback) == pytest.approx(extent.area)


def test_the_fallback_leaves_the_intersection_tiles_as_they_were():
    graph = _grid_fixture()
    plain = generate_tiles(graph, _params())
    filled = generate_tiles(
        graph,
        _params(),
        fallback=FallbackGrid(),
        fallback_extent=box(-600, -600, 1000, 1000),
    )
    assert (plain.fallback, plain.fallback_grid) == ((), None)
    assert [(t.native_id, t.geometry.wkb) for t in filled.tiles] == [
        (t.native_id, t.geometry.wkb) for t in plain.tiles
    ]
