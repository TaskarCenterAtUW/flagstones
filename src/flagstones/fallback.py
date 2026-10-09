"""Fallback tiles: grid squares laid over whatever the intersection
tiles miss.

Intersection tiles spread `clip_buffer_m` past the junctions that
seed them and stop there, so a caller needing an area covered end to end
(census places under a city, a dataset's bounding box) takes the
remainder from this grid. Cell (i, j) spans [i * cell_m,
(i + 1) * cell_m) x [j * cell_m, (j + 1) * cell_m), fixed to the CRS
origin. Each cell keeps what survives of the extent inside it once the
intersection tiles come off, which fills the holes in the node-union
clip too. This module never edits a Voronoi tile.

Anything left below `min_share` of a full cell is a sliver. Each sliver
moves to whichever fallback piece shares its longest boundary, in
whichever cell, and inherits that piece's ID, the same rule
`_attach_detached_pieces` uses on cut-off Voronoi pieces. A sliver that
borders no fallback piece falls back to its own cell, where a lone
sliver becomes the cell's entire tile. A cell's disconnected leftovers
stay one tile, in MultiPolygon form.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import shapely
from shapely import STRtree, box, union_all
from shapely.geometry.base import BaseGeometry

from flagstones.clip import polygonal_part
from flagstones.crs import (
    crs_authority_string,
    crs_id_token,
    require_metric_crs,
)
from flagstones.params import _require_finite
from flagstones.tessellate import Tile

__all__ = ["NOISE_AREA_M2", "FallbackGrid", "fallback_tiles", "mint_fallback_id"]

#: Square meters. A piece below this size is overlay noise, such as the
#: hairline left where a tile edge runs along the extent boundary, so it
#: drops out before the sliver rule reads it and mints no tile.
NOISE_AREA_M2 = 0.01

Cell = tuple[int, int]


@dataclass(frozen=True)
class FallbackGrid:
    """Cell size in meters for the grid, plus the sliver threshold.

    Anything below `min_share` of a full cell's area counts as a
    sliver. The default `cell_m` equals `TileParams().clip_buffer_m`.
    Validation rejects a non-finite value, a `cell_m` at or below zero,
    and a `min_share` outside [0, 1), so `ValueError` means the grid
    never exists in a bad state.
    """

    cell_m: float = 250.0
    min_share: float = 0.1

    def __post_init__(self) -> None:
        _require_finite("cell_m", self.cell_m)
        if not self.cell_m > 0:
            raise ValueError(f"cell_m has to be above zero, got {self.cell_m}")
        _require_finite("min_share", self.min_share)
        if not 0 <= self.min_share < 1:
            raise ValueError(
                f"min_share has to fall in [0, 1), got {self.min_share}",
            )


def mint_fallback_id(cell_x: int, cell_y: int, *, crs: int | str) -> str:
    """Mint the fallback tile ID `{crs token}:f:{cell_x}:{cell_y}`, as in
    `32610:f:1666:21958`.

    The `f` marks the form off from intersection tile IDs, which run
    three parts. An ID pins a cell, and the ID says nothing about size,
    so grids of different `cell_m` reuse an ID across different
    squares. A run operates with a single grid, recorded as
    `TileSet.fallback_grid`.
    """
    return f"{crs_id_token(crs)}:f:{cell_x}:{cell_y}"


def _cells(parts: Sequence[BaseGeometry], cell_m: float) -> list[Cell]:
    """Return, sorted, the cells touching the bounding box of each part
    of `extent`.

    A part contributes only its own box, so scattered parts (a state's
    census places, say) skip the empty cells between settlements instead
    of covering the entire state.
    """
    cells: set[Cell] = set()
    for part in parts:
        minx, miny, maxx, maxy = part.bounds
        for i in range(math.floor(minx / cell_m), math.floor(maxx / cell_m) + 1):
            for j in range(math.floor(miny / cell_m), math.floor(maxy / cell_m) + 1):
                cells.add((i, j))
    return sorted(cells)


def _pieces(
    tiles: Sequence[Tile], extent: BaseGeometry, cell_m: float
) -> Iterator[tuple[Cell, BaseGeometry]]:
    """Yield the polygons each cell keeps after subtracting `tiles` from
    `extent`, with anything under `NOISE_AREA_M2` already dropped."""
    geometries = [t.geometry for t in tiles]
    tile_tree = STRtree(geometries)
    parts = list(shapely.get_parts(extent))
    part_tree = STRtree(parts)
    for i, j in _cells(parts, cell_m):
        square = box(i * cell_m, j * cell_m, (i + 1) * cell_m, (j + 1) * cell_m)
        inside = part_tree.query(square, predicate="intersects")
        if len(inside) == 0:
            continue
        ground = polygonal_part(
            square.intersection(union_all([parts[k] for k in inside]))
        )
        hits = tile_tree.query(square, predicate="intersects")
        if len(hits):
            ground = polygonal_part(
                ground.difference(union_all([geometries[k] for k in hits]))
            )
        for piece in shapely.get_parts(ground):
            if piece.area >= NOISE_AREA_M2:
                yield (i, j), piece


def _attach_slivers(
    owned: dict[Cell, BaseGeometry], slivers: list[tuple[Cell, BaseGeometry]]
) -> list[tuple[Cell, BaseGeometry]]:
    """Attach each sliver to the fallback piece sharing the most boundary
    with it.

    A tie falls to the lower cell, and the function updates `owned` in
    place. A sliver bordering only other slivers waits for a further
    pass, once its neighbors have joined pieces. Whatever no fallback
    piece took comes back in the return value.
    """
    pending = slivers
    while pending and owned:
        cells = sorted(owned)
        tree = STRtree([owned[c] for c in cells])
        moves: list[tuple[Cell, BaseGeometry]] = []
        waiting: list[tuple[Cell, BaseGeometry]] = []
        for cell, piece in pending:
            best: tuple[float, Cell] | None = None
            for k in tree.query(piece, predicate="intersects"):
                other = cells[int(k)]
                shared = piece.boundary.intersection(owned[other].boundary).length
                if shared <= 0.0:
                    continue
                key = (-shared, other)
                if best is None or key < best:
                    best = key
            if best is None:
                waiting.append((cell, piece))
            else:
                moves.append((best[1], piece))
        if not moves:
            break
        # Every move was chosen against the tile set as it stood when
        # the pass began, so visit order within a pass cannot matter.
        for other, piece in moves:
            owned[other] = union_all([owned[other], piece])
        pending = waiting
    return pending


def fallback_tiles(
    tiles: Sequence[Tile],
    *,
    extent: BaseGeometry,
    crs: str,
    grid: FallbackGrid | None = None,
) -> list[Tile]:
    """Return fallback tiles over `extent` minus `tiles`, sorted by cell.

    `extent` names the ground to cover, `tiles` the intersection tiles,
    and both are given in `crs`, whose axes must be projected meters or
    `require_metric_crs` raises `ValueError`. Neither input gets
    modified. Each output tile reports kind "fallback", lists its cell
    in `seed_cell_x` and `seed_cell_y`, centers `seed_x` and `seed_y` on
    that cell, and sets `seed_node_count` to 0.
    """
    grid = grid or FallbackGrid()
    require_metric_crs(crs)
    extent = polygonal_part(extent)
    if extent.is_empty:
        return []
    cell_m = grid.cell_m
    sliver_below = grid.min_share * cell_m * cell_m
    bodies: dict[Cell, list[BaseGeometry]] = {}
    slivers: list[tuple[Cell, BaseGeometry]] = []
    for cell, piece in _pieces(tiles, extent, cell_m):
        if piece.area >= sliver_below:
            bodies.setdefault(cell, []).append(piece)
        else:
            slivers.append((cell, piece))
    owned = {cell: union_all(parts) for cell, parts in bodies.items()}
    for cell, piece in _attach_slivers(owned, slivers):
        owned[cell] = union_all([owned[cell], piece]) if cell in owned else piece
    seed_crs = crs_authority_string(crs)
    return [
        Tile(
            native_id=mint_fallback_id(i, j, crs=crs),
            geometry=owned[(i, j)],
            seed_crs=seed_crs,
            seed_cell_x=i,
            seed_cell_y=j,
            seed_x=(i + 0.5) * cell_m,
            seed_y=(j + 0.5) * cell_m,
            seed_node_count=0,
            seed_degree=0,
            seed_osmids=(),
            kind="fallback",
        )
        for i, j in sorted(owned)
    ]
