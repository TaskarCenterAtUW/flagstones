"""GeoDataFrame output for a finished tiling. Needs the `osm` extra."""

from __future__ import annotations

import geopandas as gpd

from flagstones.clip import repair_reprojected
from flagstones.pipeline import TileSet

__all__ = ["tiles_to_geodataframe"]


def tiles_to_geodataframe(
    tile_set: TileSet, *, to_crs: str | None = "EPSG:4326"
) -> gpd.GeoDataFrame:
    """Return a GeoDataFrame with a row per tile, reprojected to `to_crs`.

    `to_crs` defaults to `EPSG:4326`, and passing None keeps the tiling
    CRS. Intersection tiles lead the frame in tile order, fallback tiles
    follow in cell order, rows carry a RangeIndex, and the `kind` column
    separates the groups. `area_m2` measures each geometry while it
    still sits in the tiling CRS, ahead of any reprojection.

    Reprojection can invalidate a polygon, so the export hands every
    reprojected geometry to `clip.repair_reprojected`, which raises
    `ValueError` when a repair yields no polygon. Repair can change a
    geometry, so tiles meet edge to edge exactly only in the tiling
    CRS. Pass `to_crs=None` and the join holds, where a point on a
    shared border answers `intersects` for both tiles and `within` for
    neither.
    """
    tiles = [*tile_set.tiles, *tile_set.fallback]
    rows = {
        "tile_id": [t.native_id for t in tiles],
        "kind": [t.kind for t in tiles],
        "seed_degree": [t.seed_degree for t in tiles],
        "seed_node_count": [t.seed_node_count for t in tiles],
        "seed_osmids": [list(t.seed_osmids) for t in tiles],
        "area_m2": [t.geometry.area for t in tiles],
    }
    gdf = gpd.GeoDataFrame(rows, geometry=[t.geometry for t in tiles], crs=tile_set.crs)
    if not to_crs:
        return gdf
    out = gdf.to_crs(to_crs)
    return out.set_geometry(
        [repair_reprojected(geometry) for geometry in out.geometry], crs=out.crs
    )
