"""The tilings a viz bundle includes for the browser engine to match."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping, Sequence

import shapely.ops
from pyproj import Transformer
from shapely import MultiPoint, voronoi_polygons
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from flagstones import FallbackGrid
from flagstones.clip import node_union_clip
from flagstones.consolidate import Cluster, clusters_from_graph
from flagstones.fallback import fallback_tiles
from flagstones.params import TileParams
from flagstones.seeds import collapse_seed_collisions
from flagstones.tessellate import Tile, tessellate
from flagstones.viz.bundle_clips import _seed_bbox

logger = logging.getLogger(__name__)

_DEFAULT_PARAMS = TileParams()


def fallback_reference(
    seeds: Sequence[Cluster],
    clips: Mapping[str, BaseGeometry],
    *,
    extent: BaseGeometry,
    crs: str,
    grid_m: float,
    grid: FallbackGrid,
    snap_centroid: bool = _DEFAULT_PARAMS.snap_centroid,
) -> dict[str, list[dict]]:
    """For each reference variant, cover the part of `extent` its tiles missed.

    The function tessellates `seeds` again against the entry `clips` holds
    for the variant, that being the very clip the bundle ships, and the
    ground those tiles miss becomes fallback rows.
    viz/src/engine/fallback.js subtracts tiles built over these same
    stored clips, so the engines remove identical footprints.
    """
    out = {}
    for name, clip in clips.items():
        tiles = tessellate(
            seeds, clip=clip, grid_m=grid_m, crs=crs, snap_centroid=snap_centroid
        )
        out[name] = _fallback_rows(tiles, extent=extent, crs=crs, grid=grid)
    return out


def _fallback_rows(
    tiles: Sequence[Tile], *, extent: BaseGeometry, crs: str, grid: FallbackGrid
) -> list[dict]:
    """Turn the fallback coverage of `extent` left outside `tiles` into rows.

    Each row is an ID, its grid cell, and its area in square meters.
    """
    return [
        {
            "id": t.native_id,
            "cell": [t.seed_cell_x, t.seed_cell_y],
            "area_m2": t.geometry.area,
        }
        for t in fallback_tiles(tiles, extent=extent, crs=crs, grid=grid)
    ]


def _tile_row(tile) -> dict:
    return {
        "id": tile.native_id,
        "cell": [tile.seed_cell_x, tile.seed_cell_y],
        "seed": [tile.seed_x, tile.seed_y],
        "osmids": list(tile.seed_osmids),
        "degree": tile.seed_degree,
        "node_count": tile.seed_node_count,
        "area_m2": tile.geometry.area,
    }


def reference(projected, params: TileParams, crs: str, boundary: BaseGeometry) -> dict:
    """Build the library's own clustering and tiling as `params` defines them.

    Per clip the viz can rebuild, the result ships a tile set. Give
    `boundary` in the projection the graph uses, and `ValueError` fires
    when all clusters fall short of `degree_filter_min`.
    """
    return _reference(projected, params, crs, boundary)[0]


def _reference(
    projected,
    params: TileParams,
    crs: str,
    boundary: BaseGeometry,
    *,
    clips: Mapping[str, BaseGeometry] | None = None,
    table=None,
) -> tuple[dict, dict[str, list[Tile]]]:
    """Produce `reference`'s dict plus the tile lists behind every variant.

    `table` takes a prebuilt edge table when the caller has one. Through
    `clips` a caller supplies ready-made clips in place of the ones built
    here, while the exporter passes along the bundle's centimeter clips
    the browser reads.
    """
    tol = params.consolidation_tolerance_m
    grid = params.seed_grid_m
    min_degree = params.degree_filter_min
    buffer_m = params.clip_buffer_m

    # Cluster from degree 1 up first and apply the degree filter afterward,
    # so the viz can rewalk the entire consolidation instead of watching
    # only seeds that survived the filter.
    every_cluster = clusters_from_graph(
        projected,
        tolerance_m=tol,
        min_degree=1,
        junction_degree=min_degree,
        contract_transitions=params.contract_transitions,
        transition_require_same_road=params.transition_require_same_road,
        transition_spread_max_deg=params.transition_spread_max_deg,
        transition_opposite_min_deg=params.transition_opposite_min_deg,
        _table=table,
    )
    seeds = [c for c in every_cluster if c.degree >= min_degree]
    # Two unconnected junctions sharing a seed cell collapse to one site
    # here just as the viz side folds them, and a single tile answers on
    # both sides of the comparison for a collision.
    seeds, collisions = collapse_seed_collisions(seeds, grid_m=grid)
    if collisions:
        logger.info(f"collapsed {len(collisions)} seed collisions at grid {grid}")
    if not seeds:
        raise ValueError(
            f"the graph has no junction of degree {min_degree} or above, so "
            "nothing seeds a tile and the reference cannot be exported"
        )
    clips = dict(clips or {})
    if "node_union" not in clips:
        clips["node_union"] = node_union_clip(
            projected, buffer_m=buffer_m, _table=table
        )
    clips.setdefault("bbox", _seed_bbox(seeds, buffer_m))
    if "boundary" not in clips:
        clips["boundary"] = boundary.buffer(buffer_m)
    variant_clips = {name: clips[name] for name in ("node_union", "bbox", "boundary")}
    variants = {}
    tiled = {}
    for name, clip in variant_clips.items():
        tiled[name] = tessellate(
            seeds, clip=clip, grid_m=grid, crs=crs, snap_centroid=params.snap_centroid
        )
        variants[name] = {"clip": name, "tiles": [_tile_row(t) for t in tiled[name]]}
    ref = {
        "params": dataclasses.asdict(params),
        "clusters": [
            {"osmids": list(c.osmids), "x": c.x, "y": c.y, "degree": c.degree}
            for c in every_cluster
        ],
        "variants": variants,
    }
    return ref, tiled


def legacy_reference(
    nodes: list[dict], edges: list[dict], scope: list[list[float]], utm_epsg: int
) -> dict:
    """Rebuild the legacy tiling exactly as viz/src/engine/legacy.js replays it.

    Deduplicated edge endpoints (self-loops excluded) seed a Voronoi diagram
    in degree units, clipped to the convex hull around `scope`, and
    measured areas come from UTM zone `utm_epsg`. The caller draws inputs
    from the bundle's own node and edge rows, so both engines consume
    exactly the same coordinates.
    """
    lon_lat = {n["id"]: (n["lon"], n["lat"]) for n in nodes}
    seeds: dict[tuple[float, float], None] = {}
    for e in edges:
        if e["u"] == e["v"]:
            continue
        for node in (e["u"], e["v"]):
            seeds.setdefault(lon_lat[node], None)
    points = list(seeds)
    hull = MultiPoint(scope).convex_hull
    cells = voronoi_polygons(
        MultiPoint(points), extend_to=box(*hull.bounds), ordered=True
    )
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True)
    tiles = []
    for point, cell in zip(points, cells.geoms, strict=True):
        piece = cell.intersection(hull)
        if piece.is_empty or piece.area == 0.0:
            continue
        tiles.append(
            {
                "seed": list(point),
                "area_m2": shapely.ops.transform(to_utm.transform, piece).area,
            }
        )
    return {"scope": [list(p) for p in scope], "utm_epsg": utm_epsg, "tiles": tiles}
