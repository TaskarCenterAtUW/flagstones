"""Build scope: the clip unioned over node discs, and the piece index cells
are cut against."""

from __future__ import annotations

from typing import Any

import numpy as np
import shapely
from shapely import MultiPolygon, Point, STRtree
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from flagstones.buffers import _BUFFER_QUAD_SEGMENTS
from flagstones.graph import _edge_table, _EdgeTable

__all__ = ["node_union_clip", "polygonal_part", "repair_reprojected"]


def node_union_clip(
    graph: Any,
    *,
    buffer_m: float,
    within: BaseGeometry | None = None,
    _table: _EdgeTable | None = None,
) -> BaseGeometry:
    """Union `buffer_m` discs built on the graph's nodes into one shape.

    Coordinate values read in the graph's projected CRS, and a graph
    with no nodes returns an empty polygon. The union answers how far a
    tile may reach past the roads seeding it, `buffer_m` and nothing
    farther; a bounding box, or the jurisdiction outline buffered
    outward, instead awards land no road touches to the network's
    fringe junctions. Dead-end nodes stay out of the disc set, because a
    street end's disc bulges the clip's border into the neighboring
    junction's tile. Pass `within` to drop discs whose nodes fall
    outside it, so a graph extracted with a margin shapes nothing beyond
    the footprint to be tiled. `_table` accepts the caller's prebuilt
    edge table.
    """
    table = _edge_table(graph) if _table is None else _table
    streets, _ = table.counts
    kept = streets > 1
    x, y = table.x[kept], table.y[kept]
    if within is not None and len(x):
        shapely.prepare(within)
        chosen = shapely.intersects(within, shapely.points(x, y))
        x, y = x[chosen], y[chosen]
    return _union_of_discs(x, y, buffer_m)


# Dissolving the whole disc set of a statewide graph in one call exhausts
# memory. Union associates, so the discs dissolve inside square blocks
# first and the block results afterwards. Every intermediate then holds
# one block's vertices, and a block this many radii wide keeps most discs
# inside it, leaving the join little to do.
_UNION_BLOCK_RADII = 40.0


def _union_of_discs(x: np.ndarray, y: np.ndarray, radius: float) -> BaseGeometry:
    if len(x) == 0:
        return Point().buffer(0)
    cell = _UNION_BLOCK_RADII * radius
    cx = np.floor((x - x.min()) / cell).astype(np.int64)
    cy = np.floor((y - y.min()) / cell).astype(np.int64)
    block = cx * (int(cy.max()) + 1) + cy
    order = np.argsort(block, kind="stable")
    ordered = block[order]
    starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]])
    bounds = np.r_[starts, len(order)]
    pieces = [
        shapely.union_all(
            shapely.buffer(
                shapely.points(x[members], y[members]),
                radius,
                quad_segs=_BUFFER_QUAD_SEGMENTS,
            )
        )
        for start, stop in zip(bounds[:-1], bounds[1:], strict=True)
        for members in (order[start:stop],)
    ]
    return shapely.union_all(pieces)


# A cell-by-clip intersection spends every vertex the clip holds, and a
# statewide union holds millions. Cut into pieces of at most this vertex
# count, the clip costs a cell only the vertices near it.
_CLIP_PIECE_MAX_VERTICES = 2000

_CLIP_TREE_MAX_DEPTH = 20


class _ClipIndex:
    """Break the clip into rectangular pieces so a cell meets only those nearby.

    Every polygon splits at its bounds' midlines, level by level, until a
    piece holds at most `_CLIP_PIECE_MAX_VERTICES` coordinates or the
    depth ceiling stops it. The pieces cover the clip and no two share
    area. `intersection` hands back what lies inside, and a cell whole
    within a piece comes back untouched, matching what the full clip
    would have returned for it.
    """

    def __init__(self, clip: BaseGeometry) -> None:
        from shapely import get_num_coordinates
        from shapely.prepared import prep

        parts = list(clip.geoms) if hasattr(clip, "geoms") else [clip]
        pieces: list[BaseGeometry] = []
        stack = [(part, 0) for part in parts if not part.is_empty]
        while stack:
            piece, depth = stack.pop()
            if (
                get_num_coordinates(piece) <= _CLIP_PIECE_MAX_VERTICES
                or depth >= _CLIP_TREE_MAX_DEPTH
            ):
                pieces.append(piece)
                continue
            minx, miny, maxx, maxy = piece.bounds
            mx, my = (minx + maxx) / 2.0, (miny + maxy) / 2.0
            for rect in (
                box(minx, miny, mx, my),
                box(mx, miny, maxx, my),
                box(minx, my, mx, maxy),
                box(mx, my, maxx, maxy),
            ):
                part = piece.intersection(rect)
                if not part.is_empty and part.area > 0.0:
                    stack.append((part, depth + 1))
        self.pieces = pieces
        self.tree = STRtree(pieces) if pieces else None
        self.prepared = [prep(piece) for piece in pieces]

    def intersection(self, region: BaseGeometry) -> BaseGeometry:
        if self.tree is None:
            return Point().buffer(0)
        found = [int(i) for i in self.tree.query(region)]
        if not found:
            return Point().buffer(0)
        if len(found) == 1 and self.prepared[found[0]].contains(region):
            return region
        local = unary_union([self.pieces[i] for i in found])
        return polygonal_part(region.intersection(local))


def polygonal_part(geometry: BaseGeometry) -> BaseGeometry:
    """Return the polygonal part of `geometry`, leaving out lines and points.

    Where a cell merely touches the clip, GEOS adds lines and points. A
    bisector flush against the clip boundary, for one, comes out as a
    GeometryCollection holding the cell's polygon plus that stray edge.
    A tile stores a single polygonal shape, so the strays have to come
    off. When `geometry` supplies no polygon, an empty polygon returns.
    """
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    polygons = [
        polygon
        for member in shapely.get_parts(geometry)
        for polygon in shapely.get_parts(member)
        if polygon.geom_type == "Polygon" and not polygon.is_empty
    ]
    if not polygons:
        return Point().buffer(0)
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def repair_reprojected(geometry: BaseGeometry) -> BaseGeometry:
    """Return a reprojected `geometry`, repaired if it turned invalid.

    A valid geometry returns as the very same object. An invalid one
    returns whatever polygons `shapely.make_valid` produces, and one of
    them may be a hairline sliver left at the self-crossing it removed.
    The call raises `ValueError` when nothing polygonal survives, since
    a tile of zero area has nothing to publish.

    Fallback cells may keep a vertex within a nanometer of one of their
    own edges, and the cell stays valid at that. Reprojection displaces
    the vertex and its edge by unequal amounts, so they can trade
    sides. Consumers such as an osmnx polygon query refuse a shape in
    that state. A caller's bounds polygon is just as exposed. Apply the
    fix to reprojected output and to incoming bounds, and skip it while
    a tile still sits in its tiling CRS, so the tile keeps the exact
    area the tessellation gave it.
    """
    if geometry.is_valid:
        return geometry
    repaired = polygonal_part(shapely.make_valid(geometry))
    if repaired.is_empty:
        raise ValueError(f"repair of an invalid {geometry.geom_type} left no polygon")
    return repaired
