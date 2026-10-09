Test Fixtures
=============

*toppenish.graphml* is the drivable network of Toppenish, Washington (238
nodes, 361 KB), as osmnx `graph_from_place("Toppenish, Washington, USA",
network_type="drive")` returned it at capture time (a fresh fetch differs as
OSM changes). The data is licensed ODbL, OpenStreetMap contributors.

*toppenish.golden.json* records the tiling of that graph in EPSG:5070 at
`algorithm_version` "1": 189 clusters, 189 tiles and 0 seed collisions.

Toppenish has no holed tile. From the debug records the pipeline emits, its
run takes 18 dead-end holdouts and 2 link holdouts, and it skips the
contracted transitions, median ties, reattached detached pieces and seed
collisions. The synthetic cases in *synthetic.golden.json* exercise those
branches, and `tests/branches.py` lists the case for each one.

*synthetic.golden.json* records the tiling of six hand-built graphs in
`tests/graphs.py` at a 5 m grid.

*fallback.golden.json* records `_fallback_fixture` tiled at a 12 m clip
buffer, with a 50 m fallback grid over a box wider than the clip, at
`algorithm_version` "1". It contains 23 intersection tiles and 103 fallback
tiles. The fallback branches it takes: 1 hole filled, 8 slivers merged, 3
split cells and 1 cell the intersection tiles cover whole.

`scripts/regenerate_golden.py` writes the goldens and logs their
digests. *algorithm-versions.json* maps each algorithm version to the digest
of each golden.
