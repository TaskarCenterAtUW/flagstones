"""The whole tiling as a published call."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import shapely
from shapely import Point
from shapely.geometry.base import BaseGeometry

from flagstones.clip import node_union_clip, polygonal_part
from flagstones.consolidate import Cluster, clusters_from_graph
from flagstones.crs import crs_of, require_metric_crs
from flagstones.fallback import FallbackGrid, fallback_tiles
from flagstones.graph import SEED_EXCLUDE_HIGHWAY, _edge_table, require_drivable_graph
from flagstones.identity import inherit_ids, summarize_churn
from flagstones.params import TileParams
from flagstones.seeds import collapse_seed_collisions
from flagstones.tessellate import Tile, tessellate

__all__ = ["TILE_ALGORITHM_VERSION", "TileSet", "generate_tiles"]

logger = logging.getLogger(__name__)

# Stamped onto published tile output and versioned on its own schedule.
# The goldens decide: when a methodology change moves a fixture, this
# string moves with it and the new digests land in
# tests/fixtures/algorithm-versions.json (AGENTS.md has the procedure).
TILE_ALGORITHM_VERSION = "1"


@dataclass(frozen=True)
class TileSet:
    """Everything a tiling run produced, tiles and provenance alike.

    `clusters` holds every cluster that survived the degree filter,
    `seeds` the ones left by footprint selection followed by collision
    collapse, and `collisions` the merged groups.
    `identity.summarize_churn` supplies the `churn` counts. When the
    run built fallback tiles, `fallback_grid` records the grid behind
    them. Otherwise the field is None.
    """

    tiles: tuple[Tile, ...]
    clusters: tuple[Cluster, ...]
    seeds: tuple[Cluster, ...]
    collisions: tuple[tuple[Cluster, ...], ...]
    clip: BaseGeometry
    crs: str
    churn: Mapping[str, int]
    algorithm_version: str = TILE_ALGORITHM_VERSION
    fallback: tuple[Tile, ...] = ()
    fallback_grid: FallbackGrid | None = None


@contextmanager
def _timed(step: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    logger.info("%s took %.1f s", step, time.perf_counter() - start)


def generate_tiles(
    graph: Any,
    params: TileParams | None = None,
    *,
    footprint: BaseGeometry | None = None,
    clip_to: BaseGeometry | None = None,
    predecessor: Mapping[str, Point] | None = None,
    predecessor_edition: str | None = None,
    exclude_highway: Sequence[str] | None = SEED_EXCLUDE_HIGHWAY,
    fallback: FallbackGrid | None = None,
    fallback_extent: BaseGeometry | None = None,
) -> TileSet:
    """Produce the full tiling of a projected road graph as a `TileSet`.

    Pass `graph` as an osmnx-style MultiDiGraph whose CRS measures
    meters, and leave `params` out for `TileParams()`. In that same
    CRS, `footprint` keeps only the clusters falling inside it plus
    `clip_buffer_m`, and cuts the clip down to the discs punched at
    nodes inside that buffered region. `clip_to` intersects the
    finished clip with an outer bound, reshaping only the outer cells
    while the bound contains every seed.

    Pass `predecessor` as a map from a past edition's tile IDs to the
    seed points behind them. A new seed landing within
    `inheritance_radius_m` of one adopts that ID and stamps
    `predecessor_edition` into `Tile.inherited_from_edition`. That
    field is what `TileSet.churn` counts, so a non-empty `predecessor`
    without a `predecessor_edition` raises `ValueError`.

    `exclude_highway` states the seed rule the graph must satisfy before
    the pipeline touches it, and an edge with a `highway` value the
    list holds raises `NonDrivableGraphError`. The check turns the whole
    graph away and filters none of it out. A caller that screened the
    graph under its own rule already passes None.

    With `fallback`, the run lays grid tiles across `fallback_extent`
    (given in the graph's CRS) minus the intersection tiles, and those
    intersection tiles come back untouched. Supply one of the pair
    alone and the call raises `ValueError`. Entries in
    `TileSet.fallback` arrive sorted by cell.

    A missing, geographic, or non-meter CRS raises `ValueError`, and a
    graph already consolidated raises `PreConsolidatedGraphError`. Each
    stage reports its elapsed seconds to the `flagstones.pipeline`
    logger at INFO.
    """
    params = params or TileParams()
    if fallback is not None and fallback_extent is None:
        raise ValueError(
            "fallback needs fallback_extent, the ground the grid covers "
            "in the graph's CRS"
        )
    if fallback is None and fallback_extent is not None:
        raise ValueError("fallback_extent needs fallback, the grid that covers it")
    if predecessor and predecessor_edition is None:
        raise ValueError(
            "predecessor needs predecessor_edition, because an inherited "
            "tile records the edition and TileSet.churn counts those records"
        )
    crs = crs_of(graph)
    require_metric_crs(crs)
    if exclude_highway is not None:
        require_drivable_graph(graph, exclude_highway=exclude_highway)
    table = _edge_table(graph)
    with _timed("consolidation"):
        clusters = clusters_from_graph(
            graph,
            tolerance_m=params.consolidation_tolerance_m,
            min_degree=params.degree_filter_min,
            junction_degree=params.degree_filter_min,
            contract_transitions=params.contract_transitions,
            transition_require_same_road=params.transition_require_same_road,
            transition_spread_max_deg=params.transition_spread_max_deg,
            transition_opposite_min_deg=params.transition_opposite_min_deg,
            _table=table,
        )
    seed_area = None if footprint is None else footprint.buffer(params.clip_buffer_m)
    with _timed("seed selection"):
        candidates = clusters
        if seed_area is not None and clusters:
            shapely.prepare(seed_area)
            inside = shapely.intersects(
                seed_area, shapely.points([(c.x, c.y) for c in clusters])
            )
            candidates = [c for c, keep in zip(clusters, inside, strict=True) if keep]
        seeds, collisions = collapse_seed_collisions(
            candidates, grid_m=params.seed_grid_m
        )
        logger.debug(
            "seed collisions",
            extra={"branch_counts": {"seed_collisions": len(collisions)}},
        )
    for group in collisions:
        logger.warning(
            "seed collision: clusters %s occupy the same %s m cell and merge into one tile",
            [c.osmids for c in group],
            params.seed_grid_m,
        )
    with _timed("clip"):
        clip = node_union_clip(
            graph, buffer_m=params.clip_buffer_m, within=seed_area, _table=table
        )
        if clip_to is not None:
            clip = polygonal_part(clip.intersection(clip_to))
    with _timed("tessellation"):
        tiles = tessellate(
            seeds,
            clip=clip,
            grid_m=params.seed_grid_m,
            crs=crs,
            snap_centroid=params.snap_centroid,
        )
    previous = dict(predecessor or {})
    with _timed("id inheritance"):
        tiles = inherit_ids(
            tiles,
            previous,
            radius_m=params.inheritance_radius_m,
            predecessor_edition=predecessor_edition,
        )
    fallback_set: tuple[Tile, ...] = ()
    if fallback is not None:
        with _timed("fallback"):
            fallback_set = tuple(
                fallback_tiles(tiles, extent=fallback_extent, crs=crs, grid=fallback)
            )
    return TileSet(
        tiles=tuple(tiles),
        clusters=tuple(clusters),
        seeds=tuple(seeds),
        collisions=tuple(tuple(g) for g in collisions),
        clip=clip,
        crs=crs,
        churn=summarize_churn(tiles, previous),
        fallback=fallback_set,
        fallback_grid=fallback,
    )
