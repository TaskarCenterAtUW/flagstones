import pytest
from shapely.geometry import box

from flagstones.consolidate import Cluster
from flagstones.errors import SeedCollisionError
from flagstones.identity import mint_tile_native_id
from flagstones.seeds import collapse_seed_collisions, snap_seed, tile_cell
from flagstones.tessellate import tessellate


def test_snap_absorbs_a_small_move():
    """After a consolidation rerun, a centroid has moved by 0.9 m on
    each axis. The snap maps both spots to the same grid corner, so a
    tile's seed, and with it its ID, survives the rebuild unchanged.
    """
    assert snap_seed(1000.0, 2000.0, grid_m=5.0) == snap_seed(
        1000.9, 2000.9, grid_m=5.0
    )


def test_snapped_seed_and_id_move_together():
    """Once a seed steps across a grid line, the mint must change in
    the same move. A tile that changed cell now counts as its own
    tile. Reconciling such crossings across builds falls to the
    inheritance step.
    """
    assert snap_seed(1002.0, 2000.0, grid_m=5.0) != snap_seed(
        1004.0, 2000.0, grid_m=5.0
    )
    assert mint_tile_native_id(
        *tile_cell(1002.0, 2000.0, grid_m=5.0), crs=5070
    ) != mint_tile_native_id(*tile_cell(1004.0, 2000.0, grid_m=5.0), crs=5070)


def test_id_does_not_depend_on_float_noise():
    """Coordinates reaching the same cell along different float routes can
    disagree deep in the mantissa. `tile_cell` hands the mint only an
    integer index pair, so the ID strings match anyway.
    """
    assert mint_tile_native_id(
        *tile_cell(1000.0000001, 2000.0, grid_m=5.0), crs=5070
    ) == mint_tile_native_id(*tile_cell(999.9999999, 2000.0, grid_m=5.0), crs=5070)


def test_collapse_seed_collisions_folds_a_shared_cell_into_one_cluster():
    """With a raised roadway between them, the junctions below
    share no edge, so the merge keeps them apart however close their
    centroids sit. Three meters apart still drops both into the same cell
    of the seeding grid at 24 m. Collapse welds them into one site
    listing every member osmid, averaging the centroids, and taking the
    larger degree. A cluster sitting alone in its cell passes through
    untouched.
    """
    a = Cluster(osmids=(1,), x=100.0, y=100.0, degree=3)
    b = Cluster(osmids=(2, 5), x=103.0, y=100.0, degree=4)
    c = Cluster(osmids=(3,), x=400.0, y=400.0, degree=3)
    assert tile_cell(a.x, a.y, grid_m=24.0) == tile_cell(b.x, b.y, grid_m=24.0)
    collapsed, collisions = collapse_seed_collisions([c, b, a], grid_m=24.0)
    assert collisions == [(b, a)] or collisions == [(a, b)]
    assert [cl.osmids for cl in collapsed] == [(1, 2, 5), (3,)]
    merged = collapsed[0]
    assert merged.x == pytest.approx(101.5) and merged.y == pytest.approx(100.0)
    assert merged.degree == 4
    # tessellate refuses the uncollapsed pair and tiles the collapsed rows.
    with pytest.raises(SeedCollisionError):
        tessellate([a, b, c], clip=box(0, 0, 500, 500), grid_m=24.0, crs=5070)
    tiles = tessellate(collapsed, clip=box(0, 0, 500, 500), grid_m=24.0, crs=5070)
    assert [t.seed_osmids for t in tiles] == [(1, 2, 5), (3,)]
    assert tiles[0].seed_node_count == 3


def test_collapse_seed_collisions_leaves_distinct_cells_alone():
    clusters = [Cluster(osmids=(i,), x=100.0 * i, y=0.0, degree=3) for i in range(1, 5)]
    collapsed, collisions = collapse_seed_collisions(clusters, grid_m=24.0)
    assert collapsed == clusters and collisions == []
