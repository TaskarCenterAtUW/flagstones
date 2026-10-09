"""The error classes a tiling can raise."""

__all__ = [
    "AnisotropicCrsError",
    "CrsScaleError",
    "NonDrivableGraphError",
    "PreConsolidatedGraphError",
    "SeedCollisionError",
]


class SeedCollisionError(Exception):
    """Two junction clusters occupy the same seed cell.

    `tessellate` checks its seeds for duplicates before handing them to
    GEOS. GEOS answers the same duplicate with "Multiple input
    coordinates in cell", text that omits the cell and the clusters
    behind it.
    """


class PreConsolidatedGraphError(Exception):
    """The input graph carries intersections consolidated upstream.

    `consolidate.clusters_from_graph` trips on `osmid_original`, the
    marker a consolidated node wears. A second merge would then merge
    the first pass's centroids and pair nodes farther apart than
    `consolidation_tolerance_m` states.
    """


class NonDrivableGraphError(Exception):
    """An edge in the graph violates the seed rule.

    A seed should mark a drivable road junction. When footpaths or
    cycleways stay in the input, tiles form at their crossings, and the
    released tuning accounts for none of that geometry.
    """


class AnisotropicCrsError(ValueError):
    """The CRS stretches one axis more than the other where tiles are built.

    It is raised when the `crs.local_anisotropy` ratio passes
    `crs.MAX_LOCAL_ANISOTROPY` at the spot under check, or when the
    projection is undefined at that spot and PROJ answers NaN.
    `osm.tiles_for_polygon` and `osm.project_graph` refuse a CRS this
    way.
    """


class CrsScaleError(ValueError):
    """The CRS distorts every meter it measures where the tiles sit.

    Local scale strays from 1 farther than `crs.MAX_LOCAL_SCALE` at the
    tiles' location, so meter-valued parameters read stretched or
    shrunk units.
    """
