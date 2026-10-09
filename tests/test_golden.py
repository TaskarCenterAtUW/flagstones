import copy
import json
from collections import Counter
from pathlib import Path

import pytest
import shapely
import shapely.affinity
from shapely.geometry import box

osmnx = pytest.importorskip("osmnx")

from flagstones import FallbackGrid, TileParams, generate_tiles
from flagstones.osm import project_graph
from flagstones.pipeline import TILE_ALGORITHM_VERSION
from tests import graphs
from tests.branches import (
    BRANCHES,
    FALLBACK_BRANCHES,
    SYNTHETIC_CASES,
    count_branches,
    fallback_branches,
)
from tests.goldens import digest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDENS = ("toppenish", "synthetic", "fallback")


def load_golden(name):
    return json.loads((FIXTURES / f"{name}.golden.json").read_text())


def assert_matches(result, expected):
    """Compare every piece the golden records, cluster by cluster and
    tile by tile: osmids and degree, centroids, collision groups, the
    clip outline, and per tile the native_id, cell, seed, nodes and
    WKB shape. Coordinate checks allow 1e-6 m."""
    assert result.crs == expected["crs"]
    assert [[list(c.osmids), c.degree] for c in result.clusters] == [
        [c[0], c[3]] for c in expected["clusters"]
    ]
    assert [v for c in result.clusters for v in (c.x, c.y)] == pytest.approx(
        [v for c in expected["clusters"] for v in (c[1], c[2])], abs=1e-6
    )
    assert [[list(c.osmids) for c in g] for g in result.collisions] == expected[
        "collisions"
    ]
    assert shapely.equals_exact(
        result.clip, shapely.from_wkb(expected["clip_wkb"]), tolerance=1e-6
    )
    assert len(result.tiles) == len(expected["tiles"])
    for tile, want in zip(result.tiles, expected["tiles"], strict=True):
        assert tile.native_id == want["native_id"]
        assert tile.kind == "intersection"
        assert [tile.seed_cell_x, tile.seed_cell_y] == want["cell"]
        assert [tile.seed_x, tile.seed_y] == pytest.approx(want["seed"], abs=1e-6)
        assert list(tile.seed_osmids) == want["osmids"]
        assert tile.seed_degree == want["degree"]
        assert tile.seed_node_count == want["node_count"]
        assert shapely.equals_exact(
            tile.geometry, shapely.from_wkb(want["wkb"]), tolerance=1e-6
        )


@pytest.mark.parametrize("moved", ["centroid", "seed", "cell", "clip"])
def test_a_golden_whose_centroid_seed_cell_or_clip_moved_fails(moved):
    golden = load_golden("synthetic")
    name, (builder, _branch) = next(iter(SYNTHETIC_CASES.items()))
    result = generate_tiles(
        getattr(graphs, builder)(), TileParams.from_mapping(golden["params"])
    )
    expected = copy.deepcopy(golden["cases"][name])
    assert_matches(result, expected)
    if moved == "centroid":
        expected["clusters"][0][1] += 0.01
    elif moved == "seed":
        expected["tiles"][0]["seed"][0] += 0.01
    elif moved == "cell":
        expected["tiles"][0]["cell"][0] += 1
    else:
        clip = shapely.from_wkb(expected["clip_wkb"])
        expected["clip_wkb"] = shapely.affinity.translate(clip, xoff=0.01).wkb_hex
    with pytest.raises(AssertionError):
        assert_matches(result, expected)


def test_the_library_mints_the_golden_tiles_on_toppenish():
    golden = load_golden("toppenish")
    graph = project_graph(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), "EPSG:5070"
    )
    result = generate_tiles(graph, TileParams.from_mapping(golden["params"]))
    assert result.algorithm_version == golden["algorithm_version"]
    assert_matches(result, golden)


def test_the_goldens_are_recorded_under_the_current_algorithm_version():
    """Catch version and output drifting apart in either direction.

    Output that moved without a version bump breaks the digest against
    the ledger, while a bumped version over unchanged output repeats an
    earlier ledger entry."""
    ledger = json.loads((FIXTURES / "algorithm-versions.json").read_text())
    current = ledger[TILE_ALGORITHM_VERSION]
    for name in GOLDENS:
        golden = load_golden(name)
        assert golden["algorithm_version"] == TILE_ALGORITHM_VERSION
        assert current[name] == digest(golden)
    earlier = [
        entry for version, entry in ledger.items() if version != TILE_ALGORITHM_VERSION
    ]
    assert all(entry != current for entry in earlier)


def test_the_library_mints_the_golden_tiles_on_each_synthetic_case():
    golden = load_golden("synthetic")
    params = TileParams.from_mapping(golden["params"])
    assert set(golden["cases"]) == set(SYNTHETIC_CASES)
    for name, (builder, _branch) in SYNTHETIC_CASES.items():
        result = generate_tiles(getattr(graphs, builder)(), params)
        assert_matches(result, golden["cases"][name])


def test_each_synthetic_case_takes_its_branch():
    params = TileParams.from_mapping(load_golden("synthetic")["params"])
    for name, (builder, branch) in SYNTHETIC_CASES.items():
        with count_branches() as counts:
            generate_tiles(getattr(graphs, builder)(), params)
        assert counts[branch] > 0, f"{name} never takes {branch}"


def test_the_goldens_together_exercise_every_special_branch():
    seen = Counter()
    golden = load_golden("toppenish")
    graph = project_graph(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), "EPSG:5070"
    )
    with count_branches() as counts:
        generate_tiles(graph, TileParams.from_mapping(golden["params"]))
    seen.update(counts)
    params = TileParams.from_mapping(load_golden("synthetic")["params"])
    for builder, _branch in SYNTHETIC_CASES.values():
        with count_branches() as counts:
            generate_tiles(getattr(graphs, builder)(), params)
        seen.update(counts)
    assert [b for b in BRANCHES if not seen[b]] == []


def _fallback_result(golden):
    return generate_tiles(
        graphs._fallback_fixture(),
        TileParams.from_mapping(golden["params"]),
        fallback=FallbackGrid(**golden["grid"]),
        fallback_extent=box(*golden["extent"]),
    )


def test_the_library_reproduces_the_fallback_golden():
    golden = load_golden("fallback")
    result = _fallback_result(golden)
    assert_matches(result, golden)
    assert len(result.fallback) == len(golden["fallback"])
    for tile, want in zip(result.fallback, golden["fallback"], strict=True):
        assert (tile.native_id, tile.kind) == (want["native_id"], "fallback")
        assert [tile.seed_cell_x, tile.seed_cell_y] == want["cell"]
        assert shapely.equals_exact(
            tile.geometry, shapely.from_wkb(want["wkb"]), tolerance=1e-6
        )


def test_the_fallback_golden_takes_every_fallback_branch():
    golden = load_golden("fallback")
    counts = fallback_branches(
        _fallback_result(golden), box(*golden["extent"]), FallbackGrid(**golden["grid"])
    )
    assert [b for b in FALLBACK_BRANCHES if not counts[b]] == []
