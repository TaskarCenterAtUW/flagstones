Viz Tool
========

The browser tool in `viz/` runs the tiling in JavaScript on a bundle
the Python library exports, with a control for every parameter and
draggable nodes. At the bundle's own parameters the browser engine
reproduces the library's clusters and tiles, and `npm run parity`
checks exactly that against the reference the bundle ships. The engine
computes on a Web Worker, so we can pan the map while it recomputes.


Exporting a Bundle
------------------

The `viz-export` command writes a bundle per place into a directory
and lists it in that directory's *index.json*, which is where the tool
reads its place list. The command needs the `osm` extra:

```
$ uv sync --extra osm
$ uv run flagstones viz-export --place "Pullman, Washington, USA" --name Pullman --out viz/data
$ uv run flagstones viz-export --graph tests/fixtures/toppenish.graphml --name Toppenish --out viz/data
```

With `--place`, osmnx geocodes the query and `fetch_seed_graph` pulls
the seed network inside that outline from Overpass. The command caches
Overpass and geocoder answers under `--cache-dir` (`viz/cache/` by
default), and git ignores that directory along with `viz/data/`. A
live fetch can give a different graph the next day, so a bundle that
must reproduce wants a saved GraphML file through `--graph` instead.
`--boundary` attaches a GeoJSON outline to that graph. Without one,
the boundary becomes the convex hull of the graph's nodes.

The remaining options:

- **`--name`:** the place label in the place list, taken from the
  place string or the graph file name by default. Exporting over a
  name already in the index replaces that bundle and keeps its slot.
  The exporter refuses a name whose file would collide with
  *index.json* (the town of Index needs a `--name` like "Index WA"),
  and one whose file name runs past 255 bytes. It also refuses, on
  every platform, a name Windows could not store as a file: one
  holding `/`, `\`, `:`, `<`, `>`, `"`, `|`, `?` or `*`, and a
  Windows device name such as `con` or `nul`.
- **`--params`:** a TOML file with a `[tiles]` table, the same table
  `flagstones generate` reads. Without it the reference runs at
  `TileParams()`. The bundle keys each clip by its buffer in whole
  meters, so a fractional `clip_buffer_m` in `--params` fails the
  export, and a `clip_buffer_m` outside the standard buffers
  adds its own clips to the bundle.
- **`--fallback-cell-m`:** the reference's fallback cell size, 250 m
  by default to match `FallbackGrid()`. A value of 0 writes no
  fallback reference, and the parity tests then fail on that bundle's
  fallback cells.

The command tiles in EPSG:5070. From Python, `export_bundle` accepts
any `crs`:

```python
import osmnx
from pathlib import Path
from flagstones.viz.export import export_bundle, write_bundle

graph = osmnx.load_graphml("tests/fixtures/toppenish.graphml")
bundle = export_bundle(graph, name="Toppenish", crs="EPSG:5070")
write_bundle(bundle, Path("viz/data"))
```


Running the Tool
----------------

After exporting at least one bundle, we can install the npm packages
and start the dev server:

```
$ cd viz
$ npm install
$ npm run dev
```

The tool opens at `http://localhost:5173/` on the first bundle in
*index.json*. The *City* menu switches between bundles, and
`?city=<slug>` opens one directly (`?city=pullman`).

The tool's own stylesheets and its basemap style live under
`viz/public/design/`, and the Vite build copies them into `dist/`
unchanged.

The remaining scripts:

- **`npm test`:** the engine suite, the parity tests and the headless
  smoke test together.
- **`npm run parity`:** the parity tests alone, over the bundles under
  `viz/data/`.
- **`npm run smoke`:** boots the built app (after `npm run build`) on
  Pullman in headless Chromium. It skips itself without
  `/usr/bin/chromium` (or `CHROMIUM`) or without a Pullman bundle.
- **`npm run build` then `npm run verify:webgl`:** walks the built
  app through its real UI on Pullman (checkboxes, sliders, selects,
  mouse drags, clicks) and checks what WebGL draws. Chromium with the
  SwiftShader software GPU is a requirement, at `/usr/bin/chromium` or
  the path in `CHROMIUM`.


Controls
--------

The tool opens at the library's default parameters, and any control
change recomputes the tiles. A slider updates its readout while we
drag and fires the recompute on release, because a Seattle recompute
takes seconds:

- **Consolidation tolerance:** osmnx's per-node buffer radius, 0.5 to
  50 m, starting at 17 m. Nodes merge where their buffers overlap, so
  within twice this distance.
- **Seed grid:** the cell a seed snaps to, which also sets the tile
  id, 1 to 70.5 m, starting at 24 m. A grid at or above the tolerance
  times sqrt(2) can put two clusters in the same cell, and the tool then
  collapses them into a single tile, draws it red and warns under the
  slider.
- **Degree filter:** the legs a cluster needs to seed a tile, 1 to 5,
  starting at the bundle's value.
- **Keep dead ends through consolidation:** on follows the library.
  Off reproduces osmnx's `dead_ends=False`, which strips a cul-de-sac
  junction of its third leg.
- **Contract carriageway transitions:** a node where a road's
  one-way carriageways join or fork seeds no tile and merges with
  nothing, though its neighbors still count it as a leg. The
  *Transition rules* panel below it sets the same-road check and
  the angle limits, by default a carriageway pair at most 60
  degrees apart and a continuation at least 120 degrees from them.
- **Clip buffer:** a stepped slider over the buffers the export
  computed its clips at: 100, 250, 500 and 1000 m, plus the
  reference's own buffer whenever `--params` sets another whole
  number of meters.
- **Fallback cell:** the side of the fallback grid's squares, 100 to
  1000 m in 25 m steps, starting at the library's 250 m. The cells
  fill the part of the city boundary the intersection tiles leave
  open, and the 0 stop disables them. The sliver threshold matches
  the library's tenth of a cell.
- **Build scope:** the node union with street ends left out (the
  library's rule), the seeds' bounding box grown by the clip buffer,
  the buffered boundary, the bare boundary, or the buffered hull. The
  legacy tiling clips to the hull of every node in the graph, dead
  ends included, and the buffered hull is that hull grown by the clip
  buffer. The buffered scopes contain every seed and change only the
  edge tiles, while the bare boundary traces the cut line of the
  graph and can drop a junction sitting on it.
- **Computation CRS:** the bundle's CRS or the UTM zone of its
  center, which shows what a conformal CRS does to the cells.

The line under the controls gives the collision bound at the current
tolerance and the moved-node count, and *Reset moved nodes* returns
them all.


Layers
------

The layer key toggles each layer, and the legacy tiling, the snapping
grid and the consolidation buffers start off:

- **Intersection tiles:** the tiles the engine computes. A tile
  collapsed from clusters sharing a grid cell draws in red.
- **Fallback grid cells:** the fallback cells, drawn dotted.
- **Intersection seeds:** a dot on each tile's snapped seed.
- **Legacy tiling:** a Voronoi diagram over the network's edge
  endpoints, computed in degrees and clipped to the buffered hull,
  drawn dashed. The parity reference clips to the unbuffered hull
  instead (see *The Parity Check*).
- **Snapping grid:** from zoom 15, each seed's grid cell plus a link
  from the cluster's raw centroid to the seed it snapped to. Grid
  lines join from zoom 16, and past 6,000 lines in view the key asks
  us to zoom in.
- **Consolidation buffers:** from zoom 15, a disc of the tolerance
  radius on every node the engine attempts to merge, plus a line from
  each member of a merged cluster to its centroid.
- **Moved nodes:** a mark on each dragged or nudged node.
- **Tiles changed by the last edit:** an outline on the tiles a node
  edit added or changed. A parameter change draws none, because it
  changes most of the map.
- **Drivable road network:** the bundle's edges and nodes.
- **Basemap:** the style in *viz/public/design/app-basemap.json*.

While the fallback layer is off the engine skips the fallback cells,
so switching the layer on triggers a recompute, and any cell size set
in the meantime waits for it. A fallback-grid error surfaces as a
`fallback:` warning and leaves the intersection tiles standing.


The Inspector
-------------

Clicking a tile or a node opens it in the inspector. A tile shows its
cell, seed, raw centroid and snap distance, degree, OSM ids, road
names, area, and whether the clip truncated it. A node shows its
street count, coordinates, legs with their direction and road names,
any carriageway transition, and its tile. Dragging a node, or nudging
it in meters through the inspector's *dx* and *dy* fields, recomputes
the tiles it touches, and Escape cancels a drag in flight. *Reset
node* returns a single node.


Results Panel
-------------

The statistics table reports tile count, merged seeds, degree shares,
median area, slivers under 100 m2, clip truncation, seed collisions,
snap distances, the clusters the filter removed, contracted
transitions, ties across a median, and compute time. With the *Legacy
tiling* layer on, a second column and a tile ratio compare the
tilings. *Last change* counts the tiles the last recompute added,
changed and removed.

At the bundle's parameters with no node moved, the parity row reports
how many reference tile ids the browser reproduced. Tile ids derive
from the seeds, and the hull contains every seed, so the row compares
against the bounding-box reference. The bare boundary does the same
and reports fewer matches wherever it drops a seed on its edge. Away
from those parameters, the row says they differ.


The Parity Check
----------------

The parity tests in *viz/test/parity.test.js* run over the bundles
under `viz/data/` and check the browser engine against each bundle's
reference, at the reference's own parameters:

- **Clusters:** the same clusters in sorted osmid membership, degree
  for degree, centroids within 1 mm. The reference lists clusters
  from one leg up and leaves contracted transitions out.
- **Tiles:** for each of the node-union, bounding-box and
  buffered-boundary clips, the same tile ids, the same osmids and
  degree at each id, and areas within 0.1%. A seed cell two clusters
  share collapses into one tile on both sides.
- **Fallback cells:** for the same clips, the same fallback ids
  in the same order, areas within 0.1%. The Toppenish node-union clip
  must yield at least one cell so the area check has something to
  compare.
- **Legacy tiles:** the same seeds and areas within 0.1%.

In CI, the `viz` job exports the Toppenish fixture twice, once at
`TileParams()` and once at a 100 m clip buffer, and runs `npm test`,
so a pull request that moves the Python tiles away from the
JavaScript ones fails there. The 100 m clip has the narrowest necks
and the most severed pieces, the place the engines' clipping is most
likely to disagree. A bundle at any other buffer takes a `--params`
file:

```
$ printf '[tiles]\nclip_buffer_m = 100.0\n' > clip-100.toml
$ uv run flagstones viz-export --graph tests/fixtures/toppenish.graphml --name "Toppenish 100" --params clip-100.toml --out viz/data
```

A failing parity test means the engines drifted, and the fix belongs
in `viz/src/engine/` with a regression test under `viz/test/`. The
reference does not move to meet the engine.

The browser tiles the clips the bundle stores, whose geometry is
snapped to the centimeter, and the exporter computes the reference
tiles over those same stored clips. It then runs `fallback_tiles` on
those tiles and the stored extent for the fallback reference. The same
clip feeds both engines, so the comparison tests the ports and
leaves the rounding of the clips out.

`reference.legacy` records the legacy tiling that
*viz/src/engine/legacy.js* reproduces. `legacy_reference` computes it
from the bundle's own `nodes` and `edges` lists, so both engines read
identical coordinates. It seeds a Voronoi diagram in degrees on the
de-duplicated endpoints of the edges that are not self-loops, and
clips the cells to the unbuffered hull: the convex hull of the
exported node lon/lats with no clip buffer
(`reference.legacy.scope`). Areas come from the UTM zone
`reference.legacy.utm_epsg`. Because the diagram works in degrees, a
degree of longitude shorter than a degree of latitude leaves its cell
boundaries askew of the intersection tiles.

The parity tests pass on the places below, exported at `TileParams()` in
EPSG:5070 with the 250 m fallback grid, and again with
`clip_buffer_m` at 100 m. The clip buffer leaves the clusters and
tile ids alone:

| Place | Nodes | Clusters | Tiles, each clip | Legacy tiles |
| --- | --- | --- | --- | --- |
| Toppenish (fixture) | 238 | 223 | 189 | 238 |
| Pullman (Overpass) | 831 | 767 | 532 | 831 |
| Seattle (Overpass) | 19,130 | 13,831 | 11,077 | 19,122 |

The Pullman and Seattle bundles came from Overpass under osmnx 2.1.1,
and a fresh export may give other counts.

The buffered boundary contains the whole extent, so its clip leaves
no fallback cells, while the node-union and bounding-box clips leave
ground open:

| Bundle | Node-union cells | Bounding-box cells | Boundary cells |
| --- | --- | --- | --- |
| Toppenish (fixture) | 12 | 0 | 0 |
| Pullman (Overpass) | 290 | 84 | 0 |
| Seattle (Overpass) | 2,935 | 823 | 0 |
| Toppenish at 100 m | 43 | 5 | 0 |
| Pullman at 100 m | 508 | 106 | 0 |
| Seattle at 100 m | 5,313 | 886 | 0 |

The largest relative area difference between the engines came to
2.3e-7, on a 7.4 m2 fallback piece in Seattle at 100 m, and the
largest absolute one to 6e-6 m2, on a 25,097 m2 tile there.


The Bundle
----------

A bundle records its schema as `flagstones-viz/1`. The sections:

- **`city`:** the name, `crs`, `proj4`, `utm_epsg`, the center and
  bounds in lon/lat, the osmnx version, and the graph's node and edge
  counts.
- **`params`:** `TileParams` as a dict, the parameters the reference
  ran at.
- **`nodes`:** id, lon/lat from the unprojected graph (rounded to 7
  decimals), x/y from the graph as pyproj projected it into
  `city.crs`, and the street count.
- **`edges`:** each directed edge with its key, `oneway`, name and ref
  lists, `highway`, and lon/lat geometry.
- **`boundary`:** the place outline in lon/lat as MultiPolygon rings.
- **`clips`:** the node-union and buffered-boundary clips keyed by
  their buffer as whole meters, plus `extent`, the unbuffered
  boundary the fallback grid fills, all in `city.crs` as MultiPolygon
  rings snapped to the centimeter. The snap treats each clip as one
  shape, so a ring remains valid at a narrow neck where rounding one
  coordinate at a time would make it cross itself. The keys are the
  standard buffers 100, 250, 500 and 1000 and, when
  `params.clip_buffer_m` is another whole number, that buffer too, so
  the reference's own clip is always stored.
- **`reference`:** the library's clusters and tiles, the fallback
  cells of each clip under `variants.<clip>.fallback` (rows of `id`,
  `cell` and `area_m2` in cell order), the grid they ran at as
  `fallback_params` (`cell_m` and `min_share`), and the legacy tiling
  as `legacy`.
- **`boeing`:** always `null`.

The nodes pyproj projected keep their projected coordinates, so the
browser reproduces the library's tiles without reproducing pyproj's
datum pipeline. The browser projects a moved node, or the whole graph
when the computation CRS changes, with proj4.

`city.crs` is an authority string (`EPSG:5070`, `ESRI:54034`), and
`city.proj4` is pyproj's proj4 form of it. `buildGraph` registers
that definition with proj4 before any coordinate is read, so the
exporter needs no registry of known CRS codes and the browser
projects whatever CRS a bundle gives it. A CRS with no proj4 form
fails at export, and so does one whose proj4 form drops a datum
shift. A proj4 string identifies a datum only by its ellipsoid, so a
CRS on another datum loses that datum's shift to WGS84 (89 m at
Edinburgh and 125 m at London in EPSG:27700), and the exporter
refuses such a CRS as soon as its proj4 projection deviates more than
a centimeter from pyproj's at the place. The browser mints tile ids on
the library's CRS token: the bare code for EPSG (`5070:-67114:117397`)
and the lowercase authority plus code for any other authority
(`esri54034:...`).


Engine Notes
------------

A clipped cell can fall into pieces where the clip has a gap, a
concavity or a hole. The far piece lies nearer to its seed in the
plane yet has no road to it, so it joins the neighboring tile whose
seed-holding polygon it shares its longest boundary with. The engine
drops a piece with no tile beside it (a lobe of the clip containing
no intersection), so every tile ends as one polygon around its seed.

The tessellation cuts the clip down to each boundary cell's
neighborhood with a lazily built loose quadtree. A cell away from the
boundary takes its inside-or-outside answer from a neighbor instead of
point-testing the clip's rings.

A shoelace centroid taken at absolute EPSG:5070 coordinates
multiplies terms around 5e12 and recovers the area from their
cancellation, which would place merged-cluster centroids meters from
the GEOS ones. *viz/src/engine/geometry.js* takes the cross products
about the ring's first vertex and adds the offset back, which brings
the worst case to about 1e-9 m. Ring areas come about the first
vertex too: at absolute coordinates they fall on multiples of about
5e-4 m2, as much as a small sliver's whole parity tolerance.

Both engines snap each clipped cell, and each tile a piece joins, to
the library's micrometer tile grid. Two neighboring cells are clipped
separately, and polygon-clipping can place the same clip crossing an
ulp apart. On the grid the copies coincide. The
engine computes the boundary a piece shares with a tile as collinear
overlap, the way GEOS does, so a vertex a union dropped on one side
cannot hide the edge.


Timings
-------

The Python library and the browser engine take comparable time on
Pullman. The figures come from a single machine (AMD Ryzen 9 3900X,
24 threads, 128 GB), on the bundles above at the default parameters
with the 250 m node-union clip:

| Run | Pullman | Seattle |
| --- | --- | --- |
| Python `generate_tiles`, median of 5 | 293 ms | |
| JS engine on the Worker, headless Chromium, median of 5 recomputes | 177 ms | 3.3 s |
| JS `computeVariant` under Node 24.18.1, median of 5 | 279 ms | 3.7 s |
| JS `fallbackTiles` on the node-union tiles under Node 24.18.1, median of 5 | 147 ms | 1.4 s |

The map's redraw runs on the main thread and goes untimed, so a large
place can pause the map briefly while it redraws. The `fallbackTiles`
row times the fallback grid alone over already-computed tiles, so a
recompute with the fallback layer on costs the `computeVariant` time
plus that figure.

We can reproduce the Python figure from the repository root:

```
$ uv run python -c "import statistics, time, osmnx as ox; ox.settings.cache_folder = 'viz/cache'; from flagstones import generate_tiles; from flagstones.osm import fetch_seed_graph, project_graph; g = project_graph(fetch_seed_graph(ox.geocode_to_gdf('Pullman, Washington, USA').geometry.iloc[0]), 'EPSG:5070'); runs = []; [runs.append((lambda s: (generate_tiles(g), time.perf_counter() - s)[1])(time.perf_counter())) for _ in range(5)]; print(f'generate_tiles on Pullman: median {statistics.median(runs) * 1000:.0f} ms over 5 runs')"
generate_tiles on Pullman: median 293 ms over 5 runs
```
