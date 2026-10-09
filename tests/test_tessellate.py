import numpy as np
import pytest
import shapely
from shapely import MultiPolygon, Point
from shapely.geometry import box

from flagstones import tessellate as tessellate_module
from flagstones.consolidate import Cluster, clusters_from_graph
from flagstones.errors import SeedCollisionError
from flagstones.identity import mint_tile_native_id
from flagstones.seeds import snap_seed, tile_cell
from flagstones.tessellate import (
    TILE_PRECISION_M,
    Tile,
    _attach_detached_pieces,
    tessellate,
)
from tests.graphs import _grid_fixture


def test_tessellate_accepts_a_conformal_metric_crs():
    clusters = [
        Cluster(osmids=(1,), x=500000.0, y=5270000.0, degree=3),
        Cluster(osmids=(2,), x=500200.0, y=5270000.0, degree=3),
    ]
    clip = box(499800.0, 5269800.0, 500400.0, 5270200.0)
    tiles = tessellate(clusters, clip=clip, grid_m=24.0, crs="EPSG:32610")
    assert [t.native_id for t in tiles] == ["32610:20833:219583", "32610:20842:219583"]
    assert abs(sum(t.geometry.area for t in tiles) - clip.area) < 1e-6


def _clip():
    return box(-100.0, -100.0, 500.0, 500.0)


def _tiles(graph):
    return tessellate(
        clusters_from_graph(graph, tolerance_m=5.0),
        clip=_clip(),
        grid_m=5.0,
        crs=5070,
    )


def test_tessellate_accepts_an_equal_area_epsg():
    """Any projected CRS that measures meters satisfies the gate.
    EPSG:6933 qualifies, the call proceeds, and for this probe one
    cluster suffices.
    """
    clusters = [Cluster(osmids=(1,), x=1000.0, y=1000.0, degree=3)]
    tiles = tessellate(clusters, clip=_clip(), grid_m=5.0, crs=6933)
    assert len(tiles) == 1


def test_tessellate_refuses_an_epsg_even_with_no_clusters():
    """Empty input must not slip past the CRS contract. Ahead of the
    empty return, the CRS check runs first, so `crs=4326` raises even
    when no cluster arrives to cut.
    """
    with pytest.raises(ValueError, match="4326"):
        tessellate([], clip=_clip(), grid_m=5.0, crs=4326)


def test_two_runs_over_one_fixture_are_byte_identical():
    graph = _grid_fixture()
    first, second = _tiles(graph), _tiles(graph)
    assert [t.native_id for t in first] == [t.native_id for t in second]
    assert [t.geometry.wkb for t in first] == [t.geometry.wkb for t in second]


def test_a_sub_grid_movement_leaves_the_tessellated_polygon_byte_identical():
    """Nudge a centroid by 90 cm inside the 5 m grid, then diff the
    tessellations. The drift has to pass through the whole
    tessellation path, which a rerun of unchanged input does not
    exercise.
    """

    def run(dx: float) -> list[Tile]:
        clusters = [
            Cluster(osmids=(1,), x=0.0, y=0.0, degree=3),
            Cluster(osmids=(2,), x=1000.0 + dx, y=1000.0, degree=3),
        ]
        return tessellate(
            clusters, clip=box(-500.0, -500.0, 1500.0, 1500.0), grid_m=5.0, crs=5070
        )

    unperturbed, nudged = run(0.0), run(0.9)
    assert [t.geometry.wkb for t in unperturbed] == [t.geometry.wkb for t in nudged]
    assert [t.native_id for t in unperturbed] == [t.native_id for t in nudged]


def test_each_tiles_geometry_contains_its_own_seed():
    """`ordered=True` pairs each output polygon with the seed passed
    in. Flip the flag off and this module's aggregate checks stay
    green, because the same polygons return in a deterministically
    wrong pairing. So the test reads the direct symptom, the seed
    inside each tile's own polygon. That catches an ID and a shape
    describing different junctions. Spotting a swap takes more
    than one tile, and that sets the length floor.
    """
    tiles = _tiles(_grid_fixture())
    assert len(tiles) > 1
    assert all(t.geometry.contains(Point(t.seed_x, t.seed_y)) for t in tiles)


def test_snap_centroid_false_ids_by_cell_but_seeds_at_the_raw_centroid():
    """Here the switch picks only the point the Voronoi bisectors
    watch. IDs still mint from the snapped cell, since `tile_cell`
    ignores `snap_centroid`, while `seed_x`/`seed_y` keep the raw
    centroid.
    """
    clusters = [
        Cluster(osmids=(1,), x=1000.9, y=2000.9, degree=3),
        Cluster(osmids=(2,), x=1080.0, y=2000.0, degree=3),
    ]
    tiles = tessellate(
        clusters,
        clip=box(500.0, 1500.0, 1500.0, 2500.0),
        grid_m=5.0,
        crs=5070,
        snap_centroid=False,
    )
    by_osmids = {t.seed_osmids: t for t in tiles}
    tile = by_osmids[(1,)]
    assert (tile.seed_x, tile.seed_y) == (1000.9, 2000.9)
    assert tile.native_id == mint_tile_native_id(
        *tile_cell(1000.9, 2000.9, grid_m=5.0), crs=5070
    )
    # The snapped corner is a different place, so its absence shows the
    # recorded seed stayed at the raw centroid, off the grid.
    assert (tile.seed_x, tile.seed_y) != snap_seed(1000.9, 2000.9, grid_m=5.0)


# A clip of two lobes separated by 50 m. A buffered node union grows
# lobes like these wherever the network thins out.
LOBES = MultiPolygon([box(0.0, 0.0, 100.0, 100.0), box(150.0, 0.0, 250.0, 100.0)])


def test_a_piece_cut_off_across_a_gap_goes_to_the_tile_beside_it():
    """Place the seeds at (90, 50) and (240, 50). The bisector then
    falls on x = 165, and the west cell crosses the gap to claim a
    15 m strip inside the east lobe, which the east tile meets at its
    border and adopts. Afterwards both rows store a single Polygon,
    the seeds stay inside their own shapes, and the per-tile areas
    come out at 10,000.
    """
    clusters = [
        Cluster(osmids=(1,), x=90.0, y=50.0, degree=3),
        Cluster(osmids=(2,), x=240.0, y=50.0, degree=3),
    ]
    tiles = tessellate(clusters, clip=LOBES, grid_m=5.0, crs=5070)
    assert [t.geometry.geom_type for t in tiles] == ["Polygon", "Polygon"]
    by_seed = {t.seed_osmids: t for t in tiles}
    assert by_seed[(1,)].geometry.area == pytest.approx(10000.0)
    assert by_seed[(2,)].geometry.area == pytest.approx(10000.0)
    assert by_seed[(1,)].geometry.contains(Point(90.0, 50.0))


def test_a_cut_off_piece_with_no_tile_beside_it_is_dropped():
    """A single seed's cell claims both lobes, so the whole east
    lobe lands severed from the main body, with no second tile
    around to take it, so the part drops and only the west lobe
    remains.
    """
    tiles = tessellate(
        [Cluster(osmids=(1,), x=90.0, y=50.0, degree=3)],
        clip=LOBES,
        grid_m=5.0,
        crs=5070,
    )
    assert len(tiles) == 1
    assert tiles[0].geometry.geom_type == "Polygon"
    assert tiles[0].geometry.area == pytest.approx(10000.0)


def test_every_tile_is_one_polygon_when_a_bisector_runs_along_the_clip():
    """Set the seeds at (90, 50) and (210, 50), and their bisector
    falls on x = 150, landing right on the east lobe's west outline.
    Such line-only contact makes GEOS return the intersection as a
    polygon plus a stray line, and the cleanup rules must still leave
    one Polygon per stored row.
    """
    tiles = tessellate(
        [
            Cluster(osmids=(1,), x=90.0, y=50.0, degree=3),
            Cluster(osmids=(2,), x=210.0, y=50.0, degree=3),
        ],
        clip=LOBES,
        grid_m=5.0,
        crs=5070,
    )
    assert [t.geometry.geom_type for t in tiles] == ["Polygon", "Polygon"]
    assert sum(t.geometry.area for t in tiles) == pytest.approx(LOBES.area)


def test_tiles_partition_their_clip_region():
    """Cells of a Voronoi diagram fill the plane, so the tile areas
    add up to the whole clip within float slack.
    """
    tiles = _tiles(_grid_fixture())
    assert sum(t.geometry.area for t in tiles) == pytest.approx(_clip().area, rel=1e-6)


def test_seed_collision_names_the_colliding_clusters_and_cell():
    """Two clusters in the same cell must halt the build, and the raised
    error must cite both osmid tuples plus their cell. If the check
    skipped them, the failure would surface as an unreadable GEOS
    fault, or as a single quiet tile in place of two sites.
    """
    clusters = [
        Cluster(osmids=(1,), x=1000.0, y=2000.0, degree=3),
        Cluster(osmids=(2,), x=1001.0, y=2000.0, degree=3),
    ]
    with pytest.raises(SeedCollisionError) as excinfo:
        tessellate(clusters, clip=_clip(), grid_m=5.0, crs=5070)
    message = str(excinfo.value)
    assert "(1,)" in message and "(2,)" in message
    assert "5070:200:400" in message


def test_a_cluster_entirely_outside_the_clip_contributes_no_tile():
    """A cluster whose cell finds no ground inside the clip drops out,
    which keeps zero-area tiles away from this module's other checks.
    The near seed inside the clip is there because with one seed alone
    the cell stretches across the whole `extend_to` envelope, and a
    one-seed run could not leave a cell wholly outside the clip.
    """
    clusters = [
        Cluster(osmids=(1,), x=200.0, y=200.0, degree=3),
        Cluster(osmids=(2,), x=10_050.0, y=10_050.0, degree=3),
    ]
    clip = box(0.0, 0.0, 500.0, 500.0)
    tiles = tessellate(clusters, clip=clip, grid_m=5.0, crs=5070)
    assert [t.seed_osmids for t in tiles] == [(1,)]


def test_an_empty_clip_yields_no_tiles():
    """`box` can only choke on the NaN bounds an empty geometry
    reports, so the guard steps in before any Voronoi envelope forms.
    """
    clusters = [Cluster(osmids=(1,), x=200.0, y=200.0, degree=3)]
    assert tessellate(clusters, clip=Point().buffer(0), grid_m=5.0, crs=5070) == []


def test_tessellate_sorts_its_output_by_cell():
    """Output order follows `(seed_cell_x, seed_cell_y)`. Handing over
    already-sorted clusters would prove nothing, since
    `clusters_from_graph` sorts ascending upstream. So the argument
    list here arrives with descending cells, and if the output comes
    back ascending, `tessellate` itself put it in that order.
    """
    clusters = [
        Cluster(osmids=(2,), x=1000.0, y=1000.0, degree=3),
        Cluster(osmids=(1,), x=0.0, y=0.0, degree=3),
    ]
    clip = box(-500.0, -500.0, 1500.0, 1500.0)
    tiles = tessellate(clusters, clip=clip, grid_m=5.0, crs=5070)
    assert [t.seed_osmids for t in tiles] == [(1,), (2,)]
    cells = [(t.seed_cell_x, t.seed_cell_y) for t in tiles]
    assert cells == sorted(cells)


def _piece_tile(native_id, geometry, seed):
    """A `Tile` built with hand-drawn geometry for the
    `_attach_detached_pieces` rules. Only `native_id`, the stored
    geometry, and the seed point matter to that code, and the rest of
    the record is filler.
    """
    return Tile(
        native_id=native_id,
        geometry=geometry,
        seed_crs="EPSG:5070",
        seed_cell_x=0,
        seed_cell_y=0,
        seed_x=seed[0],
        seed_y=seed[1],
        seed_node_count=1,
        seed_degree=3,
        seed_osmids=(1,),
    )


def test_a_tile_that_gives_and_takes_in_one_pass_sheds_what_it_gave():
    """In the same pass the middle tile donates a piece and receives
    another. Donations are recognized by geometry equality alone, and
    `union` rewrites the receiver's rings around whatever lands.
    Deliver before stripping and a donated piece would merge into its
    receiver while the donor still lists it, so an unstripped donor
    would hand the same piece out again in later passes. So the loop
    strips every donor first and delivers second.
    """
    from shapely.ops import unary_union

    # Tiles a, b, and c sit in a row, and the middle keeps the stray
    # shaped for the right-hand border while the left-hand tile sends
    # its own stray to the middle, so giving and receiving land in the same
    # pass.
    left = MultiPolygon([box(0.0, 0.0, 1.0, 1.0), box(4.0, 0.0, 5.0, 1.0)])
    middle = MultiPolygon([box(3.0, 0.0, 4.0, 1.0), box(5.0, 0.0, 6.0, 1.0)])
    right = box(6.0, 0.0, 7.0, 1.0)
    tiles = [
        _piece_tile("a", left, (0.5, 0.5)),
        _piece_tile("b", middle, (3.5, 0.5)),
        _piece_tile("c", right, (6.5, 0.5)),
    ]

    settled = _attach_detached_pieces(tiles)

    # A donor that still listed its gift would spoil the area sum below.
    assert all(t.geometry.geom_type == "Polygon" for t in settled)
    for tile in settled:
        assert tile.geometry.covers(Point(tile.seed_x, tile.seed_y))
    total = sum(t.geometry.area for t in settled)
    assert total == pytest.approx(unary_union([t.geometry for t in settled]).area)
    assert [t.geometry.bounds for t in settled] == [
        (0.0, 0.0, 1.0, 1.0),
        (3.0, 0.0, 5.0, 1.0),
        (5.0, 0.0, 7.0, 1.0),
    ]


def test_a_detached_piece_between_two_equal_neighbors_goes_to_the_lower_id():
    """The loose piece shares 2 m of edge with each candidate body, so
    shared-edge length cannot pick a winner. In a tie, the lower
    native_id wins, and the input lists c ahead of b, so arrival order
    takes no part.
    """
    tiles = [
        _piece_tile(
            "x",
            MultiPolygon([box(0.0, 0.0, 1.0, 1.0), box(5.0, 0.0, 6.0, 2.0)]),
            (0.5, 0.5),
        ),
        _piece_tile("c", box(6.0, 0.0, 7.0, 2.0), (6.5, 1.0)),
        _piece_tile("b", box(4.0, 0.0, 5.0, 2.0), (4.5, 1.0)),
    ]
    settled = {t.native_id: t.geometry.bounds for t in _attach_detached_pieces(tiles)}
    assert settled == {
        "x": (0.0, 0.0, 1.0, 1.0),
        "c": (6.0, 0.0, 7.0, 2.0),
        "b": (4.0, 0.0, 6.0, 2.0),
    }


def test_a_piece_that_touches_only_another_detached_piece_waits_a_pass():
    """In pass one, the only thing a's stray piece borders is another
    stray piece, b's, and a detached piece cannot receive anything, so
    a waits until b's piece joins c. Only then does the grown c pick
    up a's stray and close the chain.
    """
    tiles = [
        _piece_tile(
            "a",
            MultiPolygon([box(0.0, 0.0, 1.0, 1.0), box(5.0, 0.0, 6.0, 1.0)]),
            (0.5, 0.5),
        ),
        _piece_tile(
            "b",
            MultiPolygon([box(10.0, 0.0, 11.0, 1.0), box(6.0, 0.0, 7.0, 1.0)]),
            (10.5, 0.5),
        ),
        _piece_tile("c", box(7.0, 0.0, 8.0, 1.0), (7.5, 0.5)),
    ]
    settled = {t.native_id: t.geometry for t in _attach_detached_pieces(tiles)}
    assert {k: g.geom_type for k, g in settled.items()} == dict.fromkeys(
        "abc", "Polygon"
    )
    assert settled["a"].bounds == (0.0, 0.0, 1.0, 1.0)
    assert settled["b"].bounds == (10.0, 0.0, 11.0, 1.0)
    assert settled["c"].bounds == (5.0, 0.0, 8.0, 1.0)


def test_a_union_that_stays_in_two_parts_ends_the_attach_loop(monkeypatch):
    """A stubbed `union` hands back a MultiPolygon no matter what,
    reproducing what precision can do to a union on its own. Delivery
    then leaves the piece unjoined, and the following pass would flip
    it to the other neighbor, round and round. Passes track the
    detached count, and the loop quits once a pass stops shrinking it,
    so the run drops a piece nobody can absorb, just as it drops an
    undelivered one.
    """

    def _unmerged(a, b, grid_size=None):
        return MultiPolygon([part for g in (a, b) for part in shapely.get_parts(g)])

    monkeypatch.setattr(tessellate_module, "union", _unmerged)
    tiles = [
        _piece_tile(
            "x",
            MultiPolygon([box(0.0, 0.0, 1.0, 1.0), box(5.0, 0.0, 6.0, 2.0)]),
            (0.5, 0.5),
        ),
        _piece_tile("c", box(6.0, 0.0, 7.0, 2.0), (6.5, 1.0)),
        _piece_tile("b", box(4.0, 0.0, 5.0, 2.0), (4.5, 1.0)),
    ]
    settled = {t.native_id: t.geometry.bounds for t in _attach_detached_pieces(tiles)}
    assert settled == {
        "x": (0.0, 0.0, 1.0, 1.0),
        "c": (6.0, 0.0, 7.0, 2.0),
        "b": (4.0, 0.0, 5.0, 2.0),
    }


def _on_tile_grid(geometry):
    """Decides whether all vertices already sit on the
    `TILE_PRECISION_M` coordinate grid. Half-up rounding is the rule
    GEOS and the browser engine share, so scaling by the whole inverse
    of the spacing and rounding must return every value unchanged.
    """
    scale = round(1.0 / TILE_PRECISION_M)
    coords = shapely.get_coordinates(geometry)
    return bool((np.floor(coords * scale + 0.5) / scale == coords).all())


def test_tile_coordinates_lie_on_the_tile_grid():
    clusters = [
        Cluster(osmids=(1,), x=20.3, y=21.7, degree=3),
        Cluster(osmids=(2,), x=80.1, y=19.9, degree=3),
        Cluster(osmids=(3,), x=50.6, y=80.2, degree=3),
    ]
    clip = Point(50.0, 50.0).buffer(60.0)
    tiles = tessellate(clusters, clip=clip, grid_m=5.0, crs=5070, snap_centroid=False)
    assert len(tiles) == 3
    assert all(_on_tile_grid(t.geometry) for t in tiles)


def test_a_reattached_tile_stays_on_the_tile_grid():
    tiles = [
        _piece_tile(
            "a",
            MultiPolygon([box(10.0, 10.0, 11.0, 11.0), box(1.0, 0.0, 2.0, 1.0 / 3.0)]),
            (10.5, 10.5),
        ),
        _piece_tile("b", box(0.0, 0.0, 1.0, 1.0 / 3.0), (0.5, 0.1)),
    ]
    settled = {t.native_id: t for t in _attach_detached_pieces(tiles)}
    assert settled["b"].geometry.bounds[2] == 2.0
    assert _on_tile_grid(settled["b"].geometry)
