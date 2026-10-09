"""The clip shapes stored in a viz bundle and the ring encoding that wraps
them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shapely import set_precision
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

from flagstones.clip import node_union_clip, polygonal_part
from flagstones.consolidate import Cluster

BUFFERS_M = (100.0, 250.0, 500.0, 1000.0)


def _ring(coords, decimals: int) -> list[list[float]]:
    return [[round(x, decimals), round(y, decimals)] for x, y in coords]


def _multi(geometry: BaseGeometry, *, decimals: int) -> list:
    """Write each polygon of `geometry` as a MultiPolygon ring set,
    `[[exterior, *holes]]`, and descend into nested collections too.

    `set_precision` rounds the whole geometry in one step, which keeps the
    saved rings valid. Snapping coordinates one at a time instead can
    pinch a narrow neck until the ring crosses itself, or make parts
    overlap.
    """
    polygonal = set_precision(polygonal_part(geometry), 10.0**-decimals)
    polygons = list(getattr(polygonal, "geoms", [polygonal]))
    return [
        [_ring(p.exterior.coords, decimals)]
        + [_ring(i.coords, decimals) for i in p.interiors]
        for p in polygons
        if not p.is_empty
    ]


def clip_polygons(
    projected,
    boundary: BaseGeometry,
    buffers: Sequence[float] = BUFFERS_M,
    *,
    unions: Mapping[float, BaseGeometry] | None = None,
) -> dict:
    """Return the node-union and boundary clips the viz gets to offer, at
    every buffer.

    `boundary` arrives in the graph's projection. Entries in `buffers`
    must count whole meters, because the keys that store the clips derive
    from them. A caller holding a ready node-union clip for some buffer
    can pass it in `unions`.
    """
    keys = sorted(set(buffers))
    unions = unions or {}
    return {
        "node_union": {
            str(int(b)): _multi(
                unions[b] if b in unions else node_union_clip(projected, buffer_m=b),
                decimals=2,
            )
            for b in keys
        },
        "boundary": {str(int(b)): _multi(boundary.buffer(b), decimals=2) for b in keys},
        "extent": _multi(boundary, decimals=2),
    }


def _from_multi(rings: list) -> BaseGeometry:
    """Rebuild the geometry the browser assembles out of a bundle's rings."""
    return MultiPolygon([Polygon(polygon[0], polygon[1:]) for polygon in rings])


def _seed_bbox(seeds: Sequence[Cluster], buffer_m: float) -> Polygon:
    xs = [c.x for c in seeds]
    ys = [c.y for c in seeds]
    return box(
        min(xs) - buffer_m,
        min(ys) - buffer_m,
        max(xs) + buffer_m,
        max(ys) + buffer_m,
    )
