Flagstones
==========

Intersection tiles for geographic analysis.

Given a road network, Flagstones divides an area into tiles, with one tile per
road intersection. It can fetch a seed network from OpenStreetMap or use a 
provided OSMnx graph object.


Installation
------------

```
$ pip install flagstones
$ pip install "flagstones[osm]"
```

The `osm` extra adds the ability to fetch a road graph seed via the Overpass 
API.


Quick Start
-----------

If we installed the `osm` extra, we can tile our bounding polygon:

```python
from shapely.geometry import box

from flagstones.geo import tiles_to_geodataframe
from flagstones.osm import fetch_seed_graph, tiles_for_polygon

polygon = box(-117.19, 46.72, -117.15, 46.75)
graph = fetch_seed_graph(polygon)
tile_set = tiles_for_polygon(polygon, graph)
gdf = tiles_to_geodataframe(tile_set)
```


This fetches a road network graph, creates the tiles in EPSG:4326, and loads
those into a GeoPandas `GeoDataFrame` object.

By default, Flagstones uses a tiling CRS that matches the UTM zone containing
the polygon's centroid, and we can pass `crs=` to select a different one.
`tile_set.tiles` lists the intersection tiles, and `tile_set.fallback` contains
any fallback cells left over where no intersection tile exists inside the
polygon.  `tiles_to_geodataframe` produces a row per tile in EPSG:4326 with the
intersection rows first. The `kind` column describes the type of tile.

This example uses Overpass to fetch the seed graph from live OpenStreetMap 
data. When we need consistency, we might pass a graph snapshot instead
(read the next section).

If no suitable road element exists inside the polygon, `fetch_seed_graph` raises
osmnx's `InsufficientResponseError` (a `ValueError`). When every matching way
lies inside osmnx's 500 m download buffer, outside the polygon itself, osmnx
reports the case as a plain `ValueError`, and the fetch converts it to the same
exception type. A polygon with nothing to seed on still accepts `graph=None`,
which returns fallback cells alone:

```python
from flagstones.osm import InsufficientResponseError

try:
    graph = fetch_seed_graph(polygon)
except InsufficientResponseError:
    graph = None

tile_set = tiles_for_polygon(polygon, graph)
```


Tiling a Saved Graph
--------------------

When we already have a road graph, we can generate tiles without an Overpass 
dependency using data in an  osmnx-style `MultiDiGraph` on a projected CRS in
meters. The graph must not contain `highway` values that the seed rule
excludes (see [Algorithm](https://github.com/TaskarCenterAtUW/flagstones/blob/main/docs/algorithm.md)):

```python
import osmnx
from shapely import MultiPoint

from flagstones import FallbackGrid, TileParams, generate_tiles

graph = osmnx.load_graphml("tests/fixtures/toppenish.graphml")
projected = osmnx.projection.project_graph(graph, to_crs="EPSG:32610")
nodes = [(d["x"], d["y"]) for _, d in projected.nodes(data=True)]
tile_set = generate_tiles(
    projected,
    TileParams(),
    fallback=FallbackGrid(),
    fallback_extent=MultiPoint(nodes).convex_hull,
)
print(len(tile_set.tiles), len(tile_set.fallback), tile_set.algorithm_version)
```


Command Line Interface
----------------------

In addition to the Python API, we can generate tiles via the command line:

```
flagstones generate --graph path/to/place.graphml --out place.geojson
flagstones viz-export --place "Seattle, Washington, USA" --out viz/data
```


License
-------

The MIT License (MIT). Please see the
[LICENSE File](https://github.com/TaskarCenterAtUW/flagstones/blob/main/LICENSE)
for more information.

[Test Fixtures](https://github.com/TaskarCenterAtUW/flagstones/blob/main/tests/fixtures/README.md)
licensed under the ODbL license.
