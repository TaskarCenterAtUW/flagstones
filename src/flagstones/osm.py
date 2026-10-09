"""The osmnx seam: a seed graph from Overpass, a guarded projection, and
tiles for a polygon.

Everything here needs the `osm` extra. The fetch adds no retries and
catches no network faults, so a fault reaches the caller as osmnx or
`requests` raised it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import geopandas as gpd
import networkx as nx
import shapely
from osmnx import graph_from_polygon, projection
from osmnx._errors import InsufficientResponseError
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from flagstones.clip import repair_reprojected
from flagstones.crs import (
    MAX_LOCAL_ANISOTROPY,
    MAX_LOCAL_SCALE,
    _is_geographic,
    canonical_crs,
    crs_of,
    local_anisotropy,
    local_scale,
    utm_crs_for,
)
from flagstones.errors import AnisotropicCrsError, CrsScaleError
from flagstones.fallback import FallbackGrid, fallback_tiles
from flagstones.graph import SEED_EXCLUDE_ACCESS, SEED_EXCLUDE_HIGHWAY
from flagstones.identity import summarize_churn
from flagstones.params import TileParams
from flagstones.pipeline import TileSet, generate_tiles

__all__ = [
    "SEED_CUSTOM_FILTER",
    "InsufficientResponseError",
    "fetch_seed_graph",
    "project_graph",
    "tiles_for_polygon",
]


def _alternation(values: Iterable[str]) -> str:
    return "^(" + "|".join(values) + ")$"


# The preset network_type="drive" would seed fewer tiles, because it
# excludes more than the seed rule does.
SEED_CUSTOM_FILTER = (
    f'["highway"]["highway"!~"{_alternation(SEED_EXCLUDE_HIGHWAY)}"]'
    f'["access"!~"{_alternation(SEED_EXCLUDE_ACCESS)}"]'
)


_NO_NODES_IN_POLYGON = "Found no graph nodes within the requested polygon"


def fetch_seed_graph(polygon: BaseGeometry) -> nx.MultiDiGraph:
    """Fetch the seed network within `polygon` (EPSG:4326) from Overpass.

    The query sends `SEED_CUSTOM_FILTER`. A polygon with no matching way
    raises `InsufficientResponseError`, a `ValueError` subclass. osmnx
    reports one such case as a plain `ValueError` instead, and this
    function recognizes and converts it: the polygon's matching ways all
    lie in osmnx's download buffer, outside the polygon itself. Other
    `ValueError`s pass through untouched. Overpass serves live data that
    can change by the next day, so a run can reproduce its tiles only by
    working from a pinned snapshot.
    """
    try:
        return graph_from_polygon(
            polygon,
            custom_filter=SEED_CUSTOM_FILTER,
            simplify=True,
            retain_all=True,
        )
    except InsufficientResponseError:
        raise
    except ValueError as err:
        if _NO_NODES_IN_POLYGON in str(err):
            raise InsufficientResponseError(str(err)) from err
        raise


def _lon_lat_center(graph: Any) -> tuple[float, float]:
    xs = [d["x"] for _, d in graph.nodes(data=True)]
    ys = [d["y"] for _, d in graph.nodes(data=True)]
    x, y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if _is_geographic(graph):
        return x, y
    point = gpd.GeoSeries.from_xy([x], [y], crs=crs_of(graph)).to_crs(4326)
    return float(point.x.iloc[0]), float(point.y.iloc[0])


def _require_faithful_crs(crs: str, lon: float, lat: float) -> None:
    """Reject a CRS that skews shapes or reads lengths wrong at a
    lon/lat point.

    The shape test goes first. PROJ reads NaN at a point where `crs` is
    undefined, and NaN answers every comparison with False, so only a check
    that looks for the NaN explicitly refuses an undefined CRS for what
    it is. Raises `AnisotropicCrsError` or `CrsScaleError`.
    """
    ratio = local_anisotropy(crs, lon, lat)
    if math.isnan(ratio):
        raise AnisotropicCrsError(
            f"{crs} is not defined at ({lat:.2f}, {lon:.2f}), so it cannot "
            "project tiles built there"
        )
    if ratio > MAX_LOCAL_ANISOTROPY:
        raise AnisotropicCrsError(
            f"{crs} stretches one axis {ratio:.2f} times the other at "
            f"({lat:.2f}, {lon:.2f}), above the {MAX_LOCAL_ANISOTROPY} "
            "ceiling"
        )
    scale = local_scale(crs, lon, lat)
    if not 1.0 / MAX_LOCAL_SCALE <= scale <= MAX_LOCAL_SCALE:
        raise CrsScaleError(
            f"{crs} reads a meter at ({lat:.2f}, {lon:.2f}) as "
            f"{scale:.3f} of its true length, beyond the "
            f"{MAX_LOCAL_SCALE} limit, so every meter parameter would "
            "stretch by that factor. Pass a CRS whose scale runs true "
            "there, such as the point's UTM zone or an equal-area CRS"
        )


def project_graph(
    graph: Any, crs: str, *, check_at: tuple[float, float] | None = None
) -> Any:
    """Return `graph` reprojected into `crs`.

    `check_at` picks the lon/lat pair the guards read, and omitted means
    the center of the graph's extent. The guards raise
    `AnisotropicCrsError` for a `crs` that cannot show that point at all
    or whose local anisotropy there tops `MAX_LOCAL_ANISOTROPY`, and
    `CrsScaleError` once the local scale drifts past `MAX_LOCAL_SCALE`.
    """
    lon, lat = check_at or _lon_lat_center(graph)
    _require_faithful_crs(crs, lon, lat)
    return projection.project_graph(graph, to_crs=crs)


_DEFAULT_FALLBACK = FallbackGrid()


def tiles_for_polygon(
    polygon: BaseGeometry,
    graph: Any | None,
    *,
    params: TileParams | None = None,
    crs: str | None = None,
    fallback: FallbackGrid | None = _DEFAULT_FALLBACK,
) -> TileSet:
    """Lay tiles across `polygon`, given `graph` and `polygon` in
    EPSG:4326.

    Supply `graph` from `fetch_seed_graph(polygon)` or a pinned
    snapshot. The tiling runs in `crs`, and omitted means a call to
    `utm_crs_for` for the centroid's UTM zone; a centroid beyond
    latitude -80 or 84 fails that call with `ValueError`. Far enough
    from conformal, or from true scale at the centroid, and the call
    raises `AnisotropicCrsError` or `CrsScaleError`. The intersection
    tiles cut at the node union within the polygon while the fallback
    cells take what is left, so together they cover the polygon with
    no gaps and no overlap. With `fallback=None`, ground farther from
    every non-dead-end node than `clip_buffer_m` belongs to no tile.

    When the polygon has no road, pass `graph=None`. That is the polygon
    which `fetch_seed_graph` refused with `InsufficientResponseError`.
    Fallback cells alone cover the area then, just as for an edgeless
    graph. An invalid polygon raises `ValueError`, and a highway the
    seed rule excludes anywhere in the graph raises
    `NonDrivableGraphError`.
    """
    if not polygon.is_valid:
        raise ValueError(f"invalid polygon: {shapely.is_valid_reason(polygon)}")
    center = polygon.centroid
    crs = crs or utm_crs_for(center.x, center.y)
    _require_faithful_crs(crs, center.x, center.y)
    # Reprojection moves vertices and edges by different amounts and can
    # bring one across another the polygon kept apart.
    bound = repair_reprojected(gpd.GeoSeries([polygon], crs=4326).to_crs(crs).iloc[0])
    if graph is None or graph.number_of_edges() == 0:
        # osmnx refuses to project a zero-edge graph, which seeds no
        # tile anyway. fetch_seed_graph returns one when the polygon's
        # only junction has every street leaving the boundary.
        # canonical_crs returns the string generate_tiles records for the
        # projected CRS, so cells and tiles share an ID space.
        label = canonical_crs(crs)
        cells = (
            ()
            if fallback is None
            else fallback_tiles((), extent=bound, crs=label, grid=fallback)
        )
        return TileSet(
            tiles=(),
            clusters=(),
            seeds=(),
            collisions=(),
            clip=Polygon(),
            crs=label,
            churn=summarize_churn((), {}),
            fallback=tuple(cells),
            fallback_grid=fallback,
        )
    projected = project_graph(graph, crs, check_at=(center.x, center.y))
    return generate_tiles(
        projected,
        params,
        clip_to=bound,
        fallback=fallback,
        fallback_extent=None if fallback is None else bound,
    )
