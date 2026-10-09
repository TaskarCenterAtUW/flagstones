from pathlib import Path

import pytest

osmnx = pytest.importorskip("osmnx")

import geopandas as gpd
import shapely
from shapely.geometry import box

from flagstones import FallbackGrid, Tile, TileSet, generate_tiles
from flagstones.geo import tiles_to_geodataframe
from flagstones.osm import project_graph

FIXTURES = Path(__file__).parent / "fixtures"


def test_tiles_to_geodataframe_reprojects_and_keeps_a_range_index():
    graph = project_graph(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), "EPSG:5070"
    )
    tile_set = generate_tiles(graph)
    gdf = tiles_to_geodataframe(tile_set)
    assert gdf.crs.to_epsg() == 4326
    assert list(gdf.columns) == [
        "tile_id",
        "kind",
        "seed_degree",
        "seed_node_count",
        "seed_osmids",
        "area_m2",
        "geometry",
    ]
    assert list(gdf.index) == list(range(len(tile_set.tiles)))
    assert set(gdf["kind"]) == {"intersection"}
    assert gdf["area_m2"].iloc[0] == pytest.approx(tile_set.tiles[0].geometry.area)


def test_fallback_tiles_follow_the_intersection_tiles():
    graph = project_graph(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), "EPSG:5070"
    )
    xs = [d["x"] for _, d in graph.nodes(data=True)]
    ys = [d["y"] for _, d in graph.nodes(data=True)]
    extent = box(min(xs), min(ys), max(xs), max(ys))
    tile_set = generate_tiles(graph, fallback=FallbackGrid(), fallback_extent=extent)
    gdf = tiles_to_geodataframe(tile_set)
    count = len(tile_set.tiles)
    assert list(gdf["tile_id"]) == [
        t.native_id for t in (*tile_set.tiles, *tile_set.fallback)
    ]
    assert list(gdf["kind"]) == ["intersection"] * count + ["fallback"] * len(
        tile_set.fallback
    )
    assert list(gdf.index) == list(range(len(gdf)))


def test_an_empty_tile_set_gives_an_empty_frame_in_the_target_crs():
    empty = TileSet(
        tiles=(),
        clusters=(),
        seeds=(),
        collisions=(),
        clip=None,
        crs="EPSG:5070",
        churn={},
    )
    gdf = tiles_to_geodataframe(empty, to_crs="EPSG:32610")
    assert gdf.empty and gdf.crs.to_epsg() == 32610


# Fallback cell 32611:f:1955:20946, lifted from the tiled extent of a
# hand-vetted pedestrian network. Subtracting the tiles left a vertex
# 3e-11 m short of an edge of its own cell, which reads valid inside
# EPSG:32611 yet self-intersects after projection into EPSG:4326.
PINCHED_CELL = shapely.from_wkt(
    "POLYGON ((488950.42345695634 5236250, 488950.55460116745 5236320, "
    "488950.84827411344 5236476.751914875, 488947.8474348803 5236478.550548692, "
    "488935.4286845336 5236499.269981905, 488934.88726621453 5236500, "
    "488920.81389479863 5236518.975744691, 488904.3379767546 5236537.154118947, "
    "488886.15960249887 5236553.63003699, 488866.45383971283 5236568.244826726, "
    "488845.41046566446 5236580.857739736, 488823.2321395492 5236591.347306778, "
    "488800.1324507716 5236599.612507583, 488776.33386196196 5236605.57374375, "
    "488752.06556654035 5236609.173605317, 488750 5236609.275080094, "
    "488750 5236750, 488951.36020132154 5236750, 488950.891829139 5236500, "
    "488950.42345695634 5236250))"
)


def _fallback_set(geometry):
    tile = Tile(
        native_id="32611:f:1955:20946",
        geometry=geometry,
        seed_crs="EPSG:32611",
        seed_cell_x=1955,
        seed_cell_y=20946,
        seed_x=488875.0,
        seed_y=5236625.0,
        seed_node_count=0,
        seed_degree=0,
        seed_osmids=(),
        kind="fallback",
    )
    return TileSet(
        tiles=(),
        clusters=(),
        seeds=(),
        collisions=(),
        clip=None,
        crs="EPSG:32611",
        churn={},
        fallback=(tile,),
    )


def test_a_tile_that_reprojection_pinches_comes_out_valid():
    assert PINCHED_CELL.is_valid
    raw = gpd.GeoSeries([PINCHED_CELL], crs="EPSG:32611").to_crs("EPSG:4326")
    assert not raw.is_valid.iloc[0]
    gdf = tiles_to_geodataframe(_fallback_set(PINCHED_CELL))
    assert gdf.is_valid.all()
    back = gdf.to_crs("EPSG:32611").geometry.iloc[0]
    assert back.area == pytest.approx(PINCHED_CELL.area, rel=1e-6)
    assert gdf["area_m2"].iloc[0] == PINCHED_CELL.area


def test_valid_reprojected_tiles_pass_through_unchanged():
    graph = project_graph(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), "EPSG:5070"
    )
    tile_set = generate_tiles(graph)
    gdf = tiles_to_geodataframe(tile_set)
    plain = gpd.GeoSeries(
        [t.geometry for t in tile_set.tiles], crs=tile_set.crs
    ).to_crs("EPSG:4326")
    assert plain.is_valid.all()
    assert list(shapely.to_wkb(gdf.geometry.values)) == list(
        shapely.to_wkb(plain.values)
    )
