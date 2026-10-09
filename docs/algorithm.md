Algorithm
=========

`generate_tiles` turns a projected road graph into Voronoi tiles
through the steps below, executed in order. The code under
`src/flagstones/` settles any question this page leaves open, and the
[Viz Tool](viz.md) reproduces the same pipeline in a browser.


Pipeline
--------

`generate_tiles` runs these steps:

- **Check.** The CRS must be projected with meter axes, and the edges
  must omit the seed rule's highway values.
- **Consolidate.** Nodes merge into clusters, and clusters with too
  few legs drop out.
- **Seed.** Each surviving cluster snaps to the seed grid, and
  clusters sharing a cell collapse into one.
- **Clip.** The union of a disc around each junction node bounds the
  tiles.
- **Tessellate.** A Voronoi diagram of the seeds, cut to the clip,
  yields the tiles.
- **Inherit.** A seed near a predecessor's seed inherits that tile's
  ID.
- **Fallback.** An optional grid of square cells fills whatever ground
  the tiles leave.

The finished `TileSet` gives back the tiles, the clusters and seeds,
the collisions, the clip, the CRS, the churn counts, the
`algorithm_version`, and the fallback tiles with their grid.


Seed Rule
---------

Tiles seed on road junctions, so the graph must present the drivable
network with further exclusions. `SEED_EXCLUDE_HIGHWAY` in
`flagstones.graph` lists the highway values the rule drops: `footway`,
`path`, `pedestrian`, `steps`, `cycleway`, `bridleway`, `corridor`,
`proposed`, `construction`, `abandoned`, `platform`, `raceway`,
`service` and `track`. `SEED_EXCLUDE_ACCESS` does the same for the
access values `no` and `private`.

`generate_tiles` raises `NonDrivableGraphError` when a graph still
shows an edge with a highway value from the first list. The check
refuses the whole graph and filters nothing. Where a custom rule has
already filtered the graph, we can skip the check with
`exclude_highway=None`.

`SEED_CUSTOM_FILTER` in `flagstones.osm` expresses the same rule as an
Overpass filter, and `fetch_seed_graph` hands it to osmnx. Osmnx's own
`network_type="drive"` filter drops `motor_vehicle=no`,
`service=alley`, `service=driveway`, `service=parking`, rest areas and
services, among other things, so a `drive` graph seeds fewer tiles
than one built from `SEED_CUSTOM_FILTER`.


Consolidation
-------------

Consolidation follows
[`osmnx.simplification.consolidate_intersections`][consolidate] with
`rebuild_graph=True`, though the computation works over an edge table
instead of osmnx's rebuilt graph. A node's buffer dissolves into every
buffer it overlaps, and the dissolved shape splits into the connected
components that the graph's own edges form among its nodes. Each
component becomes a `Cluster`, placed at the dissolved buffer's
centroid where the buffer came through whole and at the mean of its node
points where it split. `consolidation_tolerance_m` sets the per-node
buffer radius, so nodes merge within twice that distance.

[consolidate]: https://osmnx.readthedocs.io/en/stable/user-reference.html#osmnx.simplification.consolidate_intersections

### Dead Ends

A dead end shows one street or none at its node, counted on the
graph's own edges. Dead ends cross consolidation without merging and
leave at the degree filter. The order matters, because the junction a
cul-de-sac hangs off keeps it as one of its legs. A loop way under
100 m around forms a turning circle at a cul-de-sac's end and adds no
leg.

### Loop Roads

From 100 m around, a loop way that leaves a node and returns to it
counts as a loop road. It adds two legs at that node, however many
ways draw it.

### Legs and the Degree Filter

A cluster seeds a tile once it reaches `degree_filter_min` legs (3 by
default). On the consolidated graph an undirected incident edge counts
as one leg, so two separately mapped ways between the same junctions
make two legs, while a divided road's paired one-way carriageways make
one. Counting distinct neighbors instead would read a frontage road
that rejoins its arterial as a two-leg node.

### Links

A `*_link` edge counts as a leg only at a node with exactly one road
leg. A road that ends by becoming ramps then still meets those ramps at
a junction this way, while a ramp that joins a through road adds
nothing.

### Median Ties

Some divided roads join a side street to each carriageway and leave no
crossing segment through the median. Two nodes count as connected for
the component split when they sit on one-way, non-link edges that
share a name or ref, run at least 150 degrees apart, have no edge
between them, and have overlapping buffers. The tie stands in for the
missing crossing, so one intersection drawn as two nodes becomes one
cluster. The leg count ignores the tie.

### Transitions

Where the two one-way carriageways of a road join or fork, the node
presents three legs without becoming a junction. With
`contract_transitions` on, the pipeline contracts such a node when
all of the following are true:

- exactly three neighbors, in one of two shapes: one two-way leg with
  one leg in and one out (a join), or one-way legs only with two of
  them running in one direction (a fork)
- the three legs share a name or ref (`transition_require_same_road`)
- the paired legs lie within `transition_spread_max_deg` of each other
- the continuation leaves at least `transition_opposite_min_deg` from
  both

Once contracted, the node seeds no tile and merges with no neighbor,
while the junction on its far side still counts the leg to it.


Seed Grid
---------

A cluster's centroid snaps to the nearest multiple of `seed_grid_m`,
and the cell indices `(round(x / grid), round(y / grid))` form the ID.
With `snap_centroid=False`, `tessellate` seeds from the unsnapped
centroid instead. The ID does not change, though the polygon loses its
byte-identical reproduction.

Two points in the same cell lie at most `seed_grid_m * sqrt(2)` apart, and
consolidation merges nodes within `2 * consolidation_tolerance_m`, so
`validate_params` refuses a grid at or above
`consolidation_tolerance_m * sqrt(2)`. The bound holds for connected
nodes only. Two junctions with no edge between them, a grade
separation or a median with neither crossing nor tie, can still share
a cell, so the pipeline calls `collapse_seed_collisions`, which merges
any group whose centroids share a cell into one cluster. The merged
cluster adopts the members' osmids, the mean of their centroids and
the largest degree, and the pipeline logs a warning listing the group.
`tessellate` itself raises `SeedCollisionError` over a collision it
receives, so a caller that wants the build to halt can skip the
collapse.


Clip
----

`node_union_clip` unions a disc of radius `clip_buffer_m` around each
node, and street ends contribute nothing. A tile therefore reaches
`clip_buffer_m` past the network that seeds it and no further. A
street end's disc would push a round lobe into the neighboring
junction's tile, which is why the union excludes street ends.

Any clip that contains the seeds gives the same tiles and IDs and
differs only in the shape of the outer cells, which is exactly what
`clip_to` changes. `footprint` limits the seeds to clusters inside its
own shape grown by `clip_buffer_m`, and limits the clip to the discs
of the nodes inside that grown shape.


Tessellation
------------

Where the clip has a gap or a concavity, a clipped Voronoi cell can
fall into pieces. A piece severed from the one holding its seed goes
to the neighboring tile it shares its longest boundary with. Ties fall
to the lower ID. When no tile takes a piece, the tessellation drops
it, and the tiles then partition the clip less the dropped pieces.

Each clipped cell, and each tile a piece joins, snaps to a micrometer
grid (`TILE_PRECISION_M`, 1e-6 m). Neighboring cells meet the clip in
separate operations, which can place the same clip crossing an ulp
apart. On the grid the copies coincide, so the
shared boundary between them comes out at its full length.

### Tile Geometry

`Tile.geometry` comes back as a Polygon or a MultiPolygon in `seed_crs`.
A Polygon may have interior rings where the clip leaves a hole that no
junction disc reaches. Reattaching pieces returns single Polygons, but
the contract allows a MultiPolygon, so callers must handle both shapes.
The library leaves holes in intersection tiles open because filling one
would move published geometry and its area. A fallback tile then takes
that ground as a tile of its own.

### Tile Kind

`Tile.kind` marks the tile family: `"intersection"` for a tile
seeded on a junction and `"fallback"` for a fallback grid cell. A
fallback tile records its cell in `seed_cell_x` and `seed_cell_y` and
its cell center in `seed_x` and `seed_y`, and it reports 0 for
`seed_node_count`.


IDs and Inheritance
-------------------

An intersection tile ID reads `{crs token}:{cell_x}:{cell_y}`, with
`5070:-82014:125400` as an example. The token is the bare code for
EPSG and `{authority}{code}` lowercased for any other authority
(`esri54034`), so a code that exists under two authorities cannot
collide. The ID contains integers and the CRS token only: no hash, no
OSM id, no float.

A `predecessor` maps tile IDs to their seed points. A new seed within
`inheritance_radius_m` of a predecessor's seed keeps that tile's ID.
Matching proceeds nearest-first and claims each predecessor at most once,
and a tie in distance resolves on the predecessor ID. From the second
edition on, an inherited ID can fall outside the cell of its own seed,
so a consumer reads the seed's position from `seed_cell_x` and
`seed_cell_y` instead of parsing the ID. `TileSet.churn` counts the
tiles inherited, the tiles minted fresh and the predecessors left
unclaimed.


Fallback Grid
-------------

Intersection tiles reach `clip_buffer_m` past the junctions that seed
them and no further. Passing `fallback=FallbackGrid(...)` together
with a `fallback_extent` (or calling `tiles_for_polygon`, which
supplies the polygon) lays square cells over the rest of the extent
and leaves the intersection tiles unchanged. The grid follows these
rules:

- **Cell rule.** Cell `(i, j)` spans `[i * cell_m, (i + 1) * cell_m)`
  by `[j * cell_m, (j + 1) * cell_m)` in the CRS, anchored at the CRS
  origin. The cell's tile takes its part of the extent less the
  intersection tiles, which fills holes inside the clip too.
- **Noise floor.** A piece under `NOISE_AREA_M2` (0.01 m2), such as
  the hairline where a tile edge runs along the extent's edge, drops
  out.
- **Sliver rule.** A piece under `min_share` of a full cell joins the
  fallback piece it shares the longest boundary with, in any cell, and
  takes that piece's ID. A sliver that borders no fallback piece joins
  its own cell's tile instead, and makes up that tile alone when the
  cell is otherwise empty. A cell whose remaining pieces are
  disconnected is still one tile, a MultiPolygon.
- **ID.** `{crs token}:f:{i}:{j}`, for example `32610:f:1666:21958`.
  The `f` part keeps it apart from the three-part intersection IDs.
  The ID omits the cell size, so grids with different `cell_m`
  mint the same ID for different squares, and a consumer records the
  grid once per run from `TileSet.fallback_grid`.

`FallbackGrid` defaults to `cell_m=250.0`, matching `clip_buffer_m`,
and `min_share=0.1`.


Parameters
----------

`TileParams` gathers the tiling parameters, and its defaults state the
published design.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `consolidation_tolerance_m` | 17.0 | Per-node buffer radius, so nodes merge within 34 m. Boeing's published 10 m merge reads `5.0` here |
| `seed_grid_m` | 24.0 | Seed grid cell. It must remain under `consolidation_tolerance_m * sqrt(2)`, which is 24.04 m at 17.0 |
| `degree_filter_min` | 3 | Legs a cluster needs before it seeds a tile |
| `contract_transitions` | true | Contract carriageway joins and forks |
| `transition_require_same_road` | true | The three legs must share a name or ref |
| `transition_spread_max_deg` | 60.0 | The largest angle allowed between the paired legs, 0 to 180 |
| `transition_opposite_min_deg` | 120.0 | The smallest angle demanded of the continuation from both paired legs, 0 to 180 |
| `clip_buffer_m` | 250.0 | Radius of the junction discs that make the clip |
| `inheritance_radius_m` | 34.0 | Just past a cell diagonal at 24 m (33.94 m), so a seed crossing both grid lines keeps its ID |
| `snap_centroid` | true | Seed from the snapped centroid |

`TileParams.from_mapping` reads a `[tiles]`-style table and refuses a
key outside the fields unless the caller names it in `ignore`, so a
misspelt parameter cannot fall back to its default unnoticed.


CRS
---

The graph's CRS must be projected with meter axes, and `crs_of` returns
it as `AUTH:CODE`. The ID embeds the token, so all tiles from a run
share an ID space.

`project_graph` and `tiles_for_polygon` inspect the CRS at the center
of the build, once for shape and once for scale.
`AnisotropicCrsError` fires when the CRS's local anisotropy (the ratio
of the Tissot semi-axes) exceeds `MAX_LOCAL_ANISOTROPY` (1.03),
because such a projection bends the perpendicular bisectors that bound
a Voronoi cell, and when the CRS is undefined at the point.
`CrsScaleError` fires when the CRS's local scale (the square root of
the Tissot areal scale) falls outside 1 / `MAX_LOCAL_SCALE` to
`MAX_LOCAL_SCALE` (1.01), because all the distance parameters read in
the CRS's own units. Web Mercator reads 1.44 at 46 degrees north, and
a point 45 degrees from a UTM zone's central meridian reads 1.15 at 45
degrees north, so both fail the scale check. A UTM zone near its own
area of validity passes, and an equal-area CRS passes scale by
construction while answering to the anisotropy limit alone.

A caller that publishes areas can ask for more than the scale check:
`require_equal_area_crs` closes the gap, because a UTM zone reads
distances correctly inside its zone and areas off by its local scale.

`geo.tiles_to_geodataframe` repairs a tile that reprojection makes
self-intersecting through `clip.repair_reprojected`, so its output in
the target CRS remains valid (a repaired tile may come back as a
MultiPolygon with a hairline part). The tile in the tiling CRS and its
`area_m2` do not change.

Tiles minted in different CRSs, or from different seed graphs, share no
ID space and will not join. Translating between them falls to the
caller.


Algorithm Version
-----------------

`TILE_ALGORITHM_VERSION` in `flagstones.pipeline` versions the tile
output separately from the package. A change that moves
`tests/fixtures/*.golden.json` changes the methodology and bumps it,
whatever the package version does alongside. *algorithm-versions.json*
under `tests/fixtures/` records the digests of each version's goldens.
Package semver tracks the API only.
