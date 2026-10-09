"""Regenerate the golden fixture files with generate_tiles, or produce a
golden for a lone graphml.

    uv run python scripts/regenerate_golden.py
        replaces tests/fixtures/toppenish.golden.json, then
        tests/fixtures/synthetic.golden.json, then
        tests/fixtures/fallback.golden.json, logging each file's digest
        plus the special-branch tallies recorded during the run
    uv run python scripts/regenerate_golden.py --graph PATH --out PATH
        writes a graph's tiles as a golden document, so any before and
        after pair of tiling runs can be diffed byte for byte

A golden edit is a methodology change. Bump TILE_ALGORITHM_VERSION,
copy the digests this script prints under the fresh version inside
tests/fixtures/algorithm-versions.json, and see AGENTS.md.
"""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import osmnx as ox
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flagstones import FallbackGrid, TileParams, generate_tiles
from flagstones.osm import project_graph
from flagstones.pipeline import TILE_ALGORITHM_VERSION
from tests import graphs
from tests.branches import BRANCHES, SYNTHETIC_CASES, count_branches
from tests.goldens import digest

logger = logging.getLogger("regenerate_golden")
FIXTURES = ROOT / "tests" / "fixtures"
GRAPH_TILES = {
    "consolidation_tolerance_m": 17,
    "seed_grid_m": 24,
    "degree_filter_min": 3,
    "contract_transitions": True,
    "transition_require_same_road": True,
    "transition_spread_max_deg": 60,
    "transition_opposite_min_deg": 120,
    "inheritance_radius_m": 34.0,
    "clip_buffer_m": 250.0,
}
SYNTHETIC_TILES = {
    "consolidation_tolerance_m": 5.0,
    "seed_grid_m": 5.0,
    "degree_filter_min": 3,
    "contract_transitions": True,
    "transition_require_same_road": True,
    "transition_spread_max_deg": 60.0,
    "transition_opposite_min_deg": 120.0,
    "inheritance_radius_m": 7.0,
    "clip_buffer_m": 250.0,
}

FALLBACK_TILES = {**SYNTHETIC_TILES, "clip_buffer_m": 12.0}
FALLBACK_GRID = {"cell_m": 50.0, "min_share": 0.1}
FALLBACK_EXTENT = (-100.0, -100.0, 550.0, 300.0)


def fallback_doc() -> dict:
    """Tile `graphs._fallback_fixture` at FALLBACK_TILES and bolt on the
    FALLBACK_GRID rows that cover a box reaching past the clip."""
    result = generate_tiles(
        graphs._fallback_fixture(),
        TileParams.from_mapping(FALLBACK_TILES),
        fallback=FallbackGrid(**FALLBACK_GRID),
        fallback_extent=box(*FALLBACK_EXTENT),
    )
    doc = canonical(result)
    doc["fallback"] = [
        {
            "native_id": t.native_id,
            "cell": [t.seed_cell_x, t.seed_cell_y],
            "wkb": t.geometry.wkb_hex,
        }
        for t in result.fallback
    ]
    return {
        "algorithm_version": TILE_ALGORITHM_VERSION,
        "params": FALLBACK_TILES,
        "grid": FALLBACK_GRID,
        "extent": list(FALLBACK_EXTENT),
        **doc,
    }


def canonical(result) -> dict:
    return {
        "crs": result.crs,
        "clusters": [[list(c.osmids), c.x, c.y, c.degree] for c in result.clusters],
        "collisions": [[list(c.osmids) for c in group] for group in result.collisions],
        "clip_wkb": result.clip.wkb_hex,
        "tiles": [
            {
                "native_id": t.native_id,
                "cell": [t.seed_cell_x, t.seed_cell_y],
                "seed": [t.seed_x, t.seed_y],
                "osmids": list(t.seed_osmids),
                "degree": t.seed_degree,
                "node_count": t.seed_node_count,
                "wkb": t.geometry.wkb_hex,
            }
            for t in result.tiles
        ],
    }


def graph_doc(path: Path) -> dict:
    graph = project_graph(ox.load_graphml(path), "EPSG:5070")
    result = generate_tiles(graph, TileParams.from_mapping(GRAPH_TILES))
    return {
        "algorithm_version": TILE_ALGORITHM_VERSION,
        "params": GRAPH_TILES,
        **canonical(result),
    }


def synthetic_doc(counts: Counter) -> dict:
    params = TileParams.from_mapping(SYNTHETIC_TILES)
    cases = {}
    for name, (builder, _branch) in SYNTHETIC_CASES.items():
        with count_branches() as seen:
            cases[name] = canonical(generate_tiles(getattr(graphs, builder)(), params))
        logger.info("%s branch tally %s", name, dict(seen))
        counts.update(seen)
    return {
        "algorithm_version": TILE_ALGORITHM_VERSION,
        "params": SYNTHETIC_TILES,
        "cases": cases,
    }


def write(path: Path, doc: dict) -> None:
    # Without `newline`, text mode on Windows would write CRLF, and the
    # goldens would stop comparing byte for byte across platforms.
    path.write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if (args.graph is None) != (args.out is None):
        parser.error("--graph and --out must be given together")
    if args.graph:
        write(args.out, graph_doc(args.graph))
        return
    counts = Counter()
    with count_branches() as seen:
        doc = graph_doc(FIXTURES / "toppenish.graphml")
    logger.info("toppenish branch tally %s", dict(seen))
    counts.update(seen)
    write(FIXTURES / "toppenish.golden.json", doc)
    logger.info("toppenish %s", digest(doc))
    synthetic = synthetic_doc(counts)
    write(FIXTURES / "synthetic.golden.json", synthetic)
    logger.info("synthetic %s", digest(synthetic))
    fallback = fallback_doc()
    write(FIXTURES / "fallback.golden.json", fallback)
    logger.info("fallback %s", digest(fallback))
    missing = [b for b in BRANCHES if not counts[b]]
    logger.info("branches no golden covered: %s", missing or "none")


if __name__ == "__main__":
    main()
