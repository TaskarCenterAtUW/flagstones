"""Voronoi cells for the cluster seeds, cut to the build geometry, with any
fragment the clip severs passed to the tile that owns the ground around it."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from shapely import (
    MultiPoint,
    MultiPolygon,
    Point,
    STRtree,
    set_precision,
    union,
    voronoi_polygons,
)
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from flagstones.clip import _ClipIndex
from flagstones.consolidate import Cluster
from flagstones.crs import crs_authority_string, require_metric_crs
from flagstones.errors import SeedCollisionError
from flagstones.identity import mint_tile_native_id
from flagstones.params import TileParams
from flagstones.seeds import snap_seed, tile_cell

_DEFAULTS = TileParams()

logger = logging.getLogger(__name__)

__all__ = ["TILE_PRECISION_M", "Tile", "tessellate"]

# In CRS meters, this grid sets how stored tile coordinates round. Cells
# cross the clip one at a time, so two copies of a shared border can end
# up an ulp apart, and the border the cells share then measures
# nothing. Both copies round to identical values once they share this
# grid. The browser engine snaps to the same grid and rounds half up to
# match GEOS.
TILE_PRECISION_M = 1e-6


@dataclass(frozen=True)
class Tile:
    """A published tile: its identity, its clipped cell, and what seeded it.

    `geometry` gives the clipped cell as drawn in `seed_crs`. Reattachment
    leaves one Polygon per tile, but the contract admits a MultiPolygon,
    so consumers must handle either. A ring inside the tile outlines a
    hole in the clip that no junction disc reaches. Widening the tile to
    fill it would move published geometry and area, so the library leaves
    the hole open; a fallback tile covers that ground instead.

    `kind` reads "intersection" for a junction-seeded cell and "fallback"
    for a grid cell produced by `flagstones.fallback`. In a fallback tile,
    `seed_cell_x` and `seed_cell_y` record the grid cell, `seed_x` and
    `seed_y` its center, and its seed-node list stays empty.

    `inherited_from_edition` remains None unless `inherit_ids` has replaced
    `native_id` with the ID of an older edition.
    """

    native_id: str
    geometry: BaseGeometry
    seed_crs: str
    seed_cell_x: int
    seed_cell_y: int
    seed_x: float
    seed_y: float
    seed_node_count: int
    seed_degree: int
    seed_osmids: tuple[int, ...]
    inherited_from_edition: str | None = None
    kind: str = "intersection"


def _attach_detached_pieces(tiles: list[Tile]) -> list[Tile]:
    """Give the severed parts of a MultiPolygon tile to bordering tiles.

    Gaps, concavities, and holes in the clip split a cell into separate
    parts. A distant part lies closer to its own seed on the plane, but no
    road connects it to that seed. The junction that owns the ground around
    it sits in a neighbor's tile, so a part joins the seed-holding body with
    which it shares the most boundary, and ties go to the smaller native ID.

    With no seed-holding neighbor, say a clip lobe enclosing no junction,
    parts fall out of the result. Parts touching nothing but other
    loose parts wait for a later round. What comes back is one seed-holding
    polygon per tile, a partition of the clip minus whatever was dropped.
    """

    def body(tile: Tile) -> BaseGeometry:
        geometry = tile.geometry
        if geometry.geom_type != "MultiPolygon":
            return geometry
        seed = Point(tile.seed_x, tile.seed_y)
        holders = [p for p in geometry.geoms if p.covers(seed)]
        return max(holders or geometry.geoms, key=lambda p: p.area)

    def without(geometry: BaseGeometry, piece: BaseGeometry) -> BaseGeometry:
        parts = [p for p in geometry.geoms if p != piece]
        return parts[0] if len(parts) == 1 else MultiPolygon(parts)

    reattached = 0
    remaining = math.inf
    while True:
        bodies = [body(t) for t in tiles]
        detached = [
            (i, piece)
            for i, (tile, own) in enumerate(zip(tiles, bodies, strict=True))
            if tile.geometry.geom_type == "MultiPolygon"
            for piece in tile.geometry.geoms
            if piece != own
        ]
        # Each move merges a part into a body, so a pass shrinks the
        # detached count strictly. Precision can split a union straight back
        # into two parts and break that promise, which would shuttle a part
        # forever, so the loop also quits once a pass stops shrinking the
        # count. Anything left stranded then drops like an unowned part.
        if not detached or len(detached) >= remaining:
            break
        remaining = len(detached)
        tree = STRtree(bodies)
        moves = []
        for i, piece in sorted(
            detached, key=lambda d: (tiles[d[0]].native_id, -d[1].area)
        ):
            best = None
            for j in tree.query(piece, predicate="intersects"):
                j = int(j)
                if j == i:
                    continue
                shared = piece.boundary.intersection(bodies[j].boundary).length
                if shared <= 0.0:
                    continue
                key = (-shared, tiles[j].native_id)
                if best is None or key < best[0]:
                    best = (key, j)
            if best is not None:
                moves.append((i, piece, best[1]))
        if not moves:
            break
        reattached += len(moves)
        geometries = [t.geometry for t in tiles]
        # Strip all donations away before `union` delivers any. A tile that
        # gives and receives in the same round would otherwise still include
        # the piece it donated (`union` rebuilds the receiver's rings, while
        # `without` finds a part by equality), and it would hand that piece
        # out again in the next round.
        for i, piece, _ in moves:
            geometries[i] = without(geometries[i], piece)
        for _, piece, j in moves:
            geometries[j] = union(geometries[j], piece, grid_size=TILE_PRECISION_M)
        tiles = [replace(t, geometry=g) for t, g in zip(tiles, geometries, strict=True)]
    logger.debug(
        "detached pieces",
        extra={"branch_counts": {"detached_pieces_reattached": reattached}},
    )
    return [
        replace(t, geometry=body(t)) if t.geometry.geom_type == "MultiPolygon" else t
        for t in tiles
    ]


def tessellate(
    clusters: Sequence[Cluster],
    *,
    clip: BaseGeometry,
    grid_m: float,
    crs: int | str,
    snap_centroid: bool = _DEFAULTS.snap_centroid,
) -> list[Tile]:
    """Voronoi-tessellate `clusters` and cut the diagram down to `clip`.

    Exactly as supplied, `clip` applies, and widening the build scope
    before the call belongs to the caller. Accept `crs` either as an EPSG
    code or in authority-string form (like `ESRI:54034`). Before anything
    else happens, even when `clusters` is empty, `require_metric_crs`
    enforces the meter rule. A publisher of tile areas layers
    `require_equal_area_crs` on top. When two clusters share a cell,
    `SeedCollisionError` fires. Running `seeds.collapse_seed_collisions`
    beforehand merges such a pair.

    A tile's ID derives from the cell around each cluster's raw centroid,
    which is also the cell the collision check keys on, so `snap_centroid`
    has no effect on IDs. It moves only the point the bisectors watch. By
    default that point snaps to the grid corner, and a polygon stays
    byte-identical while its seed drifts less than the grid. `False` keeps
    the raw centroid, which pulls tile borders after the network instead of
    the grid.

    Tile coordinates land on the `TILE_PRECISION_M` grid. When a cell
    shares no area with `clip`, its seed contributes nothing, and an empty
    `clip` leaves the list empty as well. `(seed_cell_x, seed_cell_y)`
    orders the return.
    """
    require_metric_crs(crs)
    if not clusters:
        return []

    cells = [tile_cell(c.x, c.y, grid_m=grid_m) for c in clusters]

    seen: dict[tuple[int, int], Cluster] = {}
    for cluster, cell in zip(clusters, cells, strict=True):
        previous = seen.get(cell)
        if previous is not None:
            raise SeedCollisionError(
                f"clusters {previous.osmids} and {cluster.osmids} both occupy "
                f"cell {mint_tile_native_id(*cell, crs=crs)} at grid_m "
                f"{grid_m}; shrink the seed grid or raise the consolidation "
                "tolerance"
            )
        seen[cell] = cluster

    # An empty geometry reports NaN bounds, which `box` refuses outright.
    if clip.is_empty:
        return []

    seeds = (
        [snap_seed(c.x, c.y, grid_m=grid_m) for c in clusters]
        if snap_centroid
        else [(c.x, c.y) for c in clusters]
    )
    # `ordered=True` (shapely 2.1) hands the cells back in seed order.
    # GEOS orders them however it likes without the flag, and a tile would
    # then adopt another seed's ID unnoticed. `extend_to` matters only
    # through its envelope, so GEOS gets `clip`'s bounding box instead of
    # a clip that can run to millions of vertices.
    regions = voronoi_polygons(
        MultiPoint([Point(x, y) for x, y in seeds]),
        extend_to=box(*clip.bounds),
        ordered=True,
    )
    geoms = list(regions.geoms)
    if len(geoms) != len(clusters):
        raise RuntimeError(
            f"voronoi_polygons returned {len(geoms)} cells for "
            f"{len(clusters)} seeds, so the cells and seeds no longer match up"
        )

    clip_index = _ClipIndex(clip)
    tiles: list[Tile] = []
    for cluster, cell, seed, region in zip(clusters, cells, seeds, geoms, strict=True):
        geometry = set_precision(clip_index.intersection(region), TILE_PRECISION_M)
        # A zero-area entry would break the partition the tiles form, so an
        # empty intersection drops the seed.
        if geometry.is_empty or geometry.area == 0.0:
            continue
        tiles.append(
            Tile(
                native_id=mint_tile_native_id(*cell, crs=crs),
                geometry=geometry,
                seed_crs=crs_authority_string(crs),
                seed_cell_x=cell[0],
                seed_cell_y=cell[1],
                seed_x=seed[0],
                seed_y=seed[1],
                seed_node_count=len(cluster.osmids),
                seed_degree=cluster.degree,
                seed_osmids=tuple(sorted(cluster.osmids)),
            )
        )

    tiles = _attach_detached_pieces(tiles)
    # Sorting by the cell pair rather than by IDs keeps neighbors adjacent
    # in the file, so Parquet can drop row groups with the min/max
    # statistics recorded for those columns.
    tiles.sort(key=lambda t: (t.seed_cell_x, t.seed_cell_y))
    return tiles
