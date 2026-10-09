from shapely import Point

from flagstones.identity import (
    inherit_ids,
    mint_tile_native_id,
    summarize_churn,
)
from flagstones.seeds import tile_cell
from flagstones.tessellate import Tile


def _tile(*, seed, epsg=5070, grid_m=5.0):
    """Construct a stand-in `Tile` for the tests below; none of them
    depends on the geometry's shape.

    `native_id` comes fresh off `tile_cell` and `mint_tile_native_id`,
    the pair `tessellate` itself uses at first mint, which keeps real
    adoption (some predecessor's key taken over) clearly apart from a
    coincidence."""
    cell = tile_cell(seed.x, seed.y, grid_m=grid_m)
    return Tile(
        native_id=mint_tile_native_id(*cell, crs=epsg),
        geometry=seed.buffer(1.0),
        seed_crs=f"EPSG:{epsg}",
        seed_cell_x=cell[0],
        seed_cell_y=cell[1],
        seed_x=seed.x,
        seed_y=seed.y,
        seed_node_count=1,
        seed_degree=3,
        seed_osmids=(1,),
    )


def test_id_is_the_readable_natural_key():
    cell = tile_cell(1000.0, 2000.0, grid_m=5.0)
    assert mint_tile_native_id(*cell, crs=5070) == "5070:200:400"
    # With the EPSG code inside the key, two regions mint no shared ID.
    assert mint_tile_native_id(*cell, crs=6933) == "6933:200:400"


def test_id_carries_a_non_epsg_authority_token():
    """Key a non-EPSG authority as lowercase text fused onto its code.

    ESRI:54034 takes `esri54034`, so the same number under different authorities
    produces no collision."""
    cell = tile_cell(1000.0, 2000.0, grid_m=5.0)
    assert mint_tile_native_id(*cell, crs="ESRI:54034") == "esri54034:200:400"


def test_an_epsg_crs_string_mints_the_token_it_always_minted():
    """Mint the same key from the text `EPSG:5070` as from the int 5070,
    so published tile IDs hold steady across calls that spell the CRS
    differently."""
    cell = tile_cell(1000.0, 2000.0, grid_m=5.0)
    assert mint_tile_native_id(*cell, crs="EPSG:5070") == "5070:200:400"
    assert mint_tile_native_id(*cell, crs="EPSG:5070") == mint_tile_native_id(
        *cell, crs=5070
    )


def test_id_is_inherited_across_a_grid_line_crossing():
    """Inherit across a grid-line crossing, where the new cell would
    otherwise mint a brand-new key, and only a predecessor sitting
    within the inheritance radius keeps the tile's old ID."""
    previous = {"abc123": Point(1000.0, 2000.0)}
    tiles = [_tile(seed=Point(1005.0, 2000.0))]  # a single cell east
    assert inherit_ids(tiles, previous, radius_m=7.1)[0].native_id == "abc123"


def test_a_wholly_new_junction_mints_a_fresh_id():
    previous = {"abc123": Point(1000.0, 2000.0)}
    tiles = [_tile(seed=Point(4000.0, 9000.0))]
    assert inherit_ids(tiles, previous, radius_m=7.1)[0].native_id != "abc123"


def test_inheritance_does_not_depend_on_iteration_order():
    """On a distance tie between two predecessors, the lexicographically
    smaller ID wins, so the winner cannot depend on how `previous`
    iterates."""
    previous = {"bbb": Point(1003.0, 2000.0), "aaa": Point(1003.0, 2000.0)}
    tiles = [_tile(seed=Point(1000.0, 2000.0))]
    assert inherit_ids(tiles, previous, radius_m=7.1)[0].native_id == "aaa"
    assert (
        inherit_ids(tiles, dict(reversed(previous.items())), radius_m=7.1)[0].native_id
        == "aaa"
    )


def test_one_predecessor_is_claimed_at_most_once():
    """Claim the one predecessor for the nearer of two new seeds and
    force the loser to mint fresh, since otherwise the pair would
    publish a shared ID."""
    previous = {"abc123": Point(1000.0, 2000.0)}
    tiles = [_tile(seed=Point(1001.0, 2000.0)), _tile(seed=Point(1005.0, 2000.0))]
    result = inherit_ids(tiles, previous, radius_m=7.1)
    assert result[0].native_id == "abc123"
    assert result[1].native_id != "abc123"


def test_inherit_ids_claims_a_predecessor_at_exactly_the_radius():
    """Claim at `distance <= radius_m`, inclusive at the endpoint.

    Passing that computed distance straight through as `radius_m` puts
    the boundary itself on the table, and an off-by-one comparison
    trips on it."""
    previous_seed = Point(1000.0, 2000.0)
    tile = _tile(seed=Point(1010.0, 2003.0))
    exact_radius = Point(tile.seed_x, tile.seed_y).distance(previous_seed)
    result = inherit_ids([tile], {"abc123": previous_seed}, radius_m=exact_radius)
    assert result[0].native_id == "abc123"


def test_an_inherited_tile_keeps_its_own_geometry_and_seed():
    """Swap only `native_id` during inheritance, plus
    `inherited_from_edition` where the caller passes one. Any edit that
    rewrote the geometry or seed position along the way would silently
    change what the adopted ID points at."""
    previous = {"abc123": Point(1000.0, 2000.0)}
    original = _tile(seed=Point(1005.0, 2000.0))
    inherited = inherit_ids([original], previous, radius_m=7.1)[0]
    assert inherited.geometry == original.geometry
    assert (inherited.seed_x, inherited.seed_y) == (original.seed_x, original.seed_y)
    assert inherited.seed_cell_x == original.seed_cell_x
    assert inherited.seed_cell_y == original.seed_cell_y


def test_the_default_radius_inherits_across_one_cell_diagonal():
    """A snapped seed that crosses both grid lines moves a diagonal
    cell, a stride of `seed_grid_m` * sqrt(2), and
    `inheritance_radius_m`'s default has to reach across it."""
    from flagstones import TileParams

    params = TileParams()
    grid = params.seed_grid_m
    previous = {"5070:0:0": Point(0.0, 0.0)}
    moved = _tile(seed=Point(grid, grid), grid_m=grid)
    inherited = inherit_ids(
        [moved],
        previous,
        radius_m=params.inheritance_radius_m,
        predecessor_edition="v1",
    )
    assert inherited[0].native_id == "5070:0:0"


def test_churn_summary_counts_all_three_outcomes():
    """Hand `inherit_ids` a `predecessor_edition` on purpose.

    Without that argument, a tile that inherited still reports
    `inherited_from_edition` as None, and `summarize_churn` counts off
    nothing else."""
    previous = {
        "5070:200:400": Point(1000.0, 2000.0),
        "5070:900:400": Point(4500.0, 2000.0),
    }
    tiles = [
        _tile(seed=Point(1001.0, 2000.0)),  # inherits
        _tile(seed=Point(8000.0, 9000.0)),  # fresh
    ]
    inherited_tiles = inherit_ids(
        tiles, previous, radius_m=7.1, predecessor_edition="v1"
    )
    churn = summarize_churn(inherited_tiles, previous)
    assert churn == {"inherited": 1, "minted": 1, "unclaimed": 1}


def test_churn_summary_reports_zero_unclaimed_when_every_predecessor_matches():
    """The case above always strands a predecessor, so
    any summary hardcoding unclaimed=1 would keep passing it. With each
    predecessor matched, the unclaimed=0 report is this test's job
    alone."""
    previous = {"5070:200:400": Point(1000.0, 2000.0)}
    tiles = [_tile(seed=Point(1001.0, 2000.0))]
    inherited_tiles = inherit_ids(
        tiles, previous, radius_m=7.1, predecessor_edition="v1"
    )
    churn = summarize_churn(inherited_tiles, previous)
    assert churn == {"inherited": 1, "minted": 0, "unclaimed": 0}


def test_churn_summary_does_not_count_a_coincidental_id_match_as_inherited():
    """Refuse to count a coincidental key match, which grid alignment
    can manufacture when a predecessor's seed has wandered off the cell
    named in its own ID. Membership among `previous` keys would score
    such a tile as inherited. `summarize_churn` instead trusts
    `inherited_from_edition`, a stamp `inherit_ids` never puts on a
    fresh mint.
    """
    previous = {"5070:200:400": Point(1500.0, 2000.0)}  # seed left its own cell
    tiles = [_tile(seed=Point(1000.0, 2000.0))]  # fresh mint lands on the same key
    assert tiles[0].native_id == "5070:200:400"

    # the seeds sit 500 m apart, far past any real inheritance radius:
    inherited_tiles = inherit_ids(
        tiles, previous, radius_m=7.1, predecessor_edition="v1"
    )
    assert inherited_tiles[0].inherited_from_edition is None

    churn = summarize_churn(inherited_tiles, previous)
    assert churn == {"inherited": 0, "minted": 1, "unclaimed": 1}
