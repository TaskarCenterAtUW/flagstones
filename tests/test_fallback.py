import math

import pytest
from shapely.geometry import Point, Polygon, box

from flagstones import FallbackGrid, Tile
from flagstones.fallback import fallback_tiles, mint_fallback_id


def _tile(geometry):
    return Tile(
        native_id="5070:0:0",
        geometry=geometry,
        seed_crs="EPSG:5070",
        seed_cell_x=0,
        seed_cell_y=0,
        seed_x=0.0,
        seed_y=0.0,
        seed_node_count=1,
        seed_degree=3,
        seed_osmids=(1,),
    )


def test_the_defaults_are_a_250_m_cell_and_a_tenth_of_a_cell():
    assert FallbackGrid() == FallbackGrid(cell_m=250.0, min_share=0.1)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"cell_m": 0.0}, "cell_m"),
        ({"cell_m": -1.0}, "cell_m"),
        ({"min_share": 1.0}, "min_share"),
        ({"min_share": -0.1}, "min_share"),
    ],
)
def test_a_bad_grid_is_refused(kwargs, field):
    with pytest.raises(ValueError, match=field):
        FallbackGrid(**kwargs)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["cell_m", "min_share"])
def test_a_non_finite_grid_parameter_is_refused(field, value):
    with pytest.raises(ValueError, match=f"{field}.*finite"):
        FallbackGrid(**{field: value})


@pytest.mark.parametrize(
    ("x", "y", "crs", "expected"),
    [
        (1666, 21958, "EPSG:32610", "32610:f:1666:21958"),
        (-7873, 12038, 5070, "5070:f:-7873:12038"),
        (3, 4, "ESRI:54034", "esri54034:f:3:4"),
    ],
)
def test_the_fallback_id_names_the_crs_and_the_cell(x, y, crs, expected):
    assert mint_fallback_id(x, y, crs=crs) == expected


def test_an_extent_with_no_tiles_is_cut_into_whole_cells():
    cells = fallback_tiles([], extent=box(0, 0, 500, 250), crs="EPSG:5070")
    assert [t.native_id for t in cells] == ["5070:f:0:0", "5070:f:1:0"]
    assert [t.geometry.area for t in cells] == [62500.0, 62500.0]
    first = cells[0]
    assert (first.kind, first.seed_crs, first.seed_cell_x, first.seed_cell_y) == (
        "fallback",
        "EPSG:5070",
        0,
        0,
    )
    assert (first.seed_x, first.seed_y) == (125.0, 125.0)
    assert (first.seed_node_count, first.seed_degree, first.seed_osmids) == (0, 0, ())


def test_cells_index_by_floor_on_both_sides_of_the_origin():
    cells = fallback_tiles([], extent=box(-10, -10, 10, 10), crs="EPSG:5070")
    assert [(t.seed_cell_x, t.seed_cell_y) for t in cells] == [
        (-1, -1),
        (-1, 0),
        (0, -1),
        (0, 0),
    ]


def test_a_sliver_joins_the_piece_it_shares_the_longest_boundary_with():
    """Anything under a tenth of a cell drops into sliver territory, so
    the 10 m band atop each cell needs a host. The band shares a full
    250 m boundary with the cell beneath it and only a 10 m edge seam
    with its fellow band half, and a sliver never hosts another, so
    both halves settle downward."""
    cells = fallback_tiles([], extent=box(0, 0, 500, 260), crs="EPSG:5070")
    assert [t.native_id for t in cells] == ["5070:f:0:0", "5070:f:1:0"]
    assert [t.geometry.area for t in cells] == pytest.approx([65000.0, 65000.0])
    assert all(t.geometry.geom_type == "Polygon" for t in cells)


def test_a_cell_the_tiles_split_stays_one_multipolygon_tile():
    band = _tile(box(0, 100, 250, 150))
    (cell,) = fallback_tiles([band], extent=box(0, 0, 250, 250), crs="EPSG:5070")
    assert cell.native_id == "5070:f:0:0"
    assert cell.geometry.geom_type == "MultiPolygon"
    assert cell.geometry.area == pytest.approx(50000.0)


def test_a_cell_the_tiles_cover_has_no_fallback_tile():
    cover = _tile(box(250, 0, 500, 250))
    cells = fallback_tiles([cover], extent=box(0, 0, 500, 250), crs="EPSG:5070")
    assert [t.native_id for t in cells] == ["5070:f:0:0"]


def test_a_hole_with_no_fallback_neighbor_becomes_its_cells_tile_alone():
    walls = _tile(box(0, 0, 250, 250).difference(box(100, 100, 110, 110)))
    (cell,) = fallback_tiles([walls], extent=box(0, 0, 250, 250), crs="EPSG:5070")
    assert cell.native_id == "5070:f:0:0"
    assert cell.geometry.area == pytest.approx(100.0)


def test_a_sliver_with_no_fallback_neighbor_joins_its_cells_body():
    band = _tile(box(230, 0, 240, 250))
    (cell,) = fallback_tiles([band], extent=box(0, 0, 250, 250), crs="EPSG:5070")
    assert cell.native_id == "5070:f:0:0"
    assert cell.geometry.geom_type == "MultiPolygon"
    assert cell.geometry.area == pytest.approx(230 * 250 + 10 * 250)


def test_tiles_and_fallback_tiles_partition_the_extent():
    tile = _tile(Point(300, 120).buffer(90))
    extent = box(0, 0, 750, 500)
    cells = fallback_tiles([tile], extent=extent, crs="EPSG:5070")
    assert sum(c.geometry.area for c in cells) + tile.geometry.area == pytest.approx(
        extent.area
    )
    assert all(c.geometry.intersection(tile.geometry).area < 1e-6 for c in cells)


def test_an_empty_extent_gives_no_fallback_tiles():
    assert fallback_tiles([], extent=Polygon(), crs="EPSG:5070") == []


def test_a_geographic_crs_is_refused():
    with pytest.raises(ValueError, match="projected"):
        fallback_tiles([], extent=box(0, 0, 1, 1), crs="EPSG:4326")
