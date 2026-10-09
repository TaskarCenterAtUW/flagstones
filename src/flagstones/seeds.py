"""The seed grid: corners junctions snap to, and the merge when they collide."""

from __future__ import annotations

from collections.abc import Sequence

from flagstones.consolidate import Cluster

__all__ = ["collapse_seed_collisions", "snap_seed", "tile_cell"]


def tile_cell(x: float, y: float, *, grid_m: float) -> tuple[int, int]:
    """Return the indices of the cell that contains `(x, y)`.

    Cell keys come back as integer pairs, so a tile ID built on them
    carries none of the float noise a coordinate transform introduces.
    """
    return (round(x / grid_m), round(y / grid_m))


def snap_seed(x: float, y: float, *, grid_m: float) -> tuple[float, float]:
    """Return the grid corner nearest a point.

    The corner becomes the tile's seed while `snap_centroid` is on, the
    default; with it off, `tessellate` seeds on the raw cluster centroid.
    """
    cell_x, cell_y = tile_cell(x, y, grid_m=grid_m)
    return (cell_x * grid_m, cell_y * grid_m)


def collapse_seed_collisions(
    clusters: Sequence[Cluster], *, grid_m: float
) -> tuple[list[Cluster], list[tuple[Cluster, ...]]]:
    """Merge clusters that ended up sharing a seed cell at `grid_m`.

    The function returns survivors ordered on `(x, y)` plus the merged
    groups, sorted by their first member. The collision bound in
    `validate_params` speaks of junctions consolidation can merge. Two
    junctions never joined by an edge stay two clusters no matter how
    close they lie, so a grade separation, or a median without
    crossing or tie, can still place its junctions in the same
    cell. The merged cluster takes all member osmids, the mean
    centroid, and the largest member degree, so `seed_node_count`
    downstream reads the group's full size. Skipping this call hands
    any collision to `tessellate`, which answers with
    `SeedCollisionError` and ends the build.
    """
    groups: dict[tuple[int, int], list[Cluster]] = {}
    for cluster in clusters:
        groups.setdefault(tile_cell(cluster.x, cluster.y, grid_m=grid_m), []).append(
            cluster
        )
    collapsed: list[Cluster] = []
    collisions: list[tuple[Cluster, ...]] = []
    for members in groups.values():
        if len(members) == 1:
            collapsed.append(members[0])
            continue
        collisions.append(tuple(members))
        collapsed.append(
            Cluster(
                osmids=tuple(sorted(osmid for c in members for osmid in c.osmids)),
                x=sum(c.x for c in members) / len(members),
                y=sum(c.y for c in members) / len(members),
                degree=max(c.degree for c in members),
            )
        )
    collapsed.sort(key=lambda c: (c.x, c.y))
    return collapsed, sorted(collisions, key=lambda g: (g[0].x, g[0].y))
