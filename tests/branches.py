"""Recover which special branches a finished tiling run took.

The library logs branch hits inside `branch_counts` mappings on its
debug records, and `count_branches` sums those for a block. Each
case in `SYNTHETIC_CASES` names a fixture graph from `tests/graphs.py`
beside the branch it exists to fire. The fallback reports
through no log record at all, so `fallback_branches` reads its
outcomes off the geometry the run left behind instead."""

import logging
import math
from collections import Counter
from contextlib import contextmanager

import shapely
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

BRANCHES = (
    "dead_end_holdouts",
    "link_holdouts",
    "transitions_contracted",
    "median_ties",
    "detached_pieces_reattached",
    "seed_collisions",
)

# case -> (graph builder in tests/graphs.py, branch the case exists to fire)
SYNTHETIC_CASES = {
    "median_pair": ("_median_pair_fixture", "median_ties"),
    "carriageway_join": (
        "_carriageway_join_junction_fixture",
        "transitions_contracted",
    ),
    "slip_lane_end": ("_junction_with_slip_lane_end_fixture", "link_holdouts"),
    "t_dead_end": ("_t_junction_with_dead_end_fixture", "dead_end_holdouts"),
    "grade_separated_pair": ("_grade_separated_pair_fixture", "seed_collisions"),
    "two_lobes": ("_two_lobes_fixture", "detached_pieces_reattached"),
}


class _Counter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.counts: Counter = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        self.counts.update(getattr(record, "branch_counts", {}))


@contextmanager
def count_branches():
    """Hand the block a live tally of the branch stamps logged inside it.

    A DEBUG handler watches the flagstones logger for the duration, and
    each record's `branch_counts` mapping folds into the tally. When the
    block ends, the handler detaches and the logger returns to its old
    level."""
    logger = logging.getLogger("flagstones")
    handler = _Counter()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.counts
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


FALLBACK_BRANCHES = ("holes_filled", "slivers_merged", "split_cells", "cells_covered")


def fallback_branches(result, extent, grid) -> Counter:
    """Infer the fallback's branch events from a finished TileSet.

    Measured against `extent` and `grid`: where fallback land takes
    over half of an intersection hole, that hole counted as filled;
    where a fallback shape reaches past its own cell by more than 1e-6
    square meters, a sliver merged into it; where that shape comes
    apart into several parts, the cell split; and where the
    intersection tiles close over an extent cell entirely, that cell
    went covered."""
    counts: Counter = Counter()
    c = grid.cell_m
    tiles = unary_union([t.geometry for t in result.tiles])
    cells = unary_union([t.geometry for t in result.fallback])
    for polygon in shapely.get_parts(tiles):
        for ring in polygon.interiors:
            hole = Polygon(ring)
            if cells.intersection(hole).area > 0.5 * hole.area:
                counts["holes_filled"] += 1
    for t in result.fallback:
        x, y = t.seed_cell_x, t.seed_cell_y
        if (
            t.geometry.difference(box(x * c, y * c, (x + 1) * c, (y + 1) * c)).area
            > 1e-6
        ):
            counts["slivers_merged"] += 1
        if t.geometry.geom_type == "MultiPolygon":
            counts["split_cells"] += 1
    minx, miny, maxx, maxy = extent.bounds
    for i in range(math.floor(minx / c), math.floor(maxx / c) + 1):
        for j in range(math.floor(miny / c), math.floor(maxy / c) + 1):
            ground = box(i * c, j * c, (i + 1) * c, (j + 1) * c).intersection(extent)
            if ground.area > 0 and ground.difference(tiles).area < 1e-6:
                counts["cells_covered"] += 1
    return counts
