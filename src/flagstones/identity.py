"""The natural key stamped on a tile, and how an ID survives into the
next edition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
import shapely
from shapely import Point, STRtree

from flagstones.crs import crs_id_token

if TYPE_CHECKING:
    from flagstones.tessellate import Tile

__all__ = ["inherit_ids", "mint_tile_native_id", "summarize_churn"]


def mint_tile_native_id(cell_x: int, cell_y: int, *, crs: int | str) -> str:
    """Return the ID a cell mints, shaped `{crs token}:{cell_x}:{cell_y}`.

    This natural key reads by eye, answers range queries through its
    cell parts, and sorts rows into spatially local groups. `crs` takes
    an EPSG code, whether numeric or spelled as text, or an
    authority-bearing string such as `ESRI:54034`. EPSG contributes the
    bare code, other authorities contribute `{authority}{code}`
    lowercased, so the same number under different authorities cannot
    produce the same token.
    """
    return f"{crs_id_token(crs)}:{cell_x}:{cell_y}"


def inherit_ids(
    tiles: Sequence[Tile],
    previous: dict[str, Point],
    *,
    radius_m: float,
    predecessor_edition: str | None = None,
) -> list[Tile]:
    """Give each tile the ID of a predecessor near enough to replace.

    When a predecessor's seed falls no farther than `radius_m` from a
    tile's own seed, the tile adopts that ID, catching seeds that
    crossed a grid line between editions. Candidates sort by distance,
    then predecessor ID, then tile position, and each side claims the
    match once. Two new tiles beside the same old seed therefore cannot both
    inherit and publish a duplicate ID, and ties resolve identically no
    matter how `previous` iterates. A bulk STRtree query finds the
    candidate pairs under GEOS's inclusive `dwithin` test, because
    asking the same question pair by pair does not scale to a statewide
    edition.

    `predecessor_edition` says which release `previous` came from, and
    inheriting tiles stamp it into `inherited_from_edition`. A build
    publishing the field has to supply it.

    In editions past the first, an inherited ID need not match the cell
    around the tile's own seed, so a separately minted tile can hand
    out an ID a neighbor already carries by inheritance. It takes two
    seeds within `radius_m` of each other to set this up, and callers
    publishing IDs need a uniqueness check of their own to catch the pair.
    """
    if not tiles or not previous:
        return list(tiles)

    predecessor_ids = list(previous.keys())
    predecessor_points = [previous[pid] for pid in predecessor_ids]
    tree = STRtree(predecessor_points)

    seeds = shapely.points([(tile.seed_x, tile.seed_y) for tile in tiles])
    tile_index, tree_index = tree.query(seeds, predicate="dwithin", distance=radius_m)
    distances = shapely.distance(
        seeds[tile_index], np.asarray(predecessor_points, dtype=object)[tree_index]
    )
    candidates = [
        (float(distance), predecessor_ids[j], int(i))
        for distance, i, j in zip(distances, tile_index, tree_index, strict=True)
    ]
    candidates.sort()

    claimed_tiles: set[int] = set()
    claimed_predecessors: set[str] = set()
    inherited: dict[int, str] = {}
    for _distance, predecessor_id, index in candidates:
        if index in claimed_tiles or predecessor_id in claimed_predecessors:
            continue
        inherited[index] = predecessor_id
        claimed_tiles.add(index)
        claimed_predecessors.add(predecessor_id)

    return [
        (
            replace(
                tile,
                native_id=inherited[index],
                inherited_from_edition=predecessor_edition,
            )
            if index in inherited
            else tile
        )
        for index, tile in enumerate(tiles)
    ]


def summarize_churn(
    tiles: Sequence[Tile], previous: dict[str, Point]
) -> dict[str, int]:
    """Count inherited, minted, and unclaimed tiles at an edition boundary.

    Pass `tiles` exactly as `inherit_ids` left them, and `previous` as
    it received. The count reads `inherited_from_edition` alone.
    Membership among `previous` keys misreports, since past the first
    edition a minted ID can coincide with an unrelated predecessor's
    through cell alignment, and the test would score it as an
    inheritance.
    """
    inherited = sum(1 for tile in tiles if tile.inherited_from_edition is not None)
    claimed_ids = {
        tile.native_id for tile in tiles if tile.inherited_from_edition is not None
    }
    return {
        "inherited": inherited,
        "minted": len(tiles) - inherited,
        "unclaimed": len(previous) - len(claimed_ids & previous.keys()),
    }
