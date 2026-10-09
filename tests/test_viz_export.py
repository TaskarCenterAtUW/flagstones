import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

osmnx = pytest.importorskip("osmnx")

from shapely import MultiPolygon, Point, Polygon

from flagstones import Cluster, TileParams, generate_tiles
from flagstones.graph import dead_end_nodes
from flagstones.osm import project_graph
from flagstones.seeds import collapse_seed_collisions
from flagstones.viz.export import crs_block, export_bundle, write_bundle

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def graph():
    return osmnx.load_graphml(FIXTURES / "toppenish.graphml")


@pytest.fixture(scope="module")
def bundle(graph):
    return export_bundle(graph, name="Toppenish")


def test_bundle_carries_the_documented_sections(bundle):
    assert bundle["schema"] == "flagstones-viz/1"
    assert set(bundle) >= {
        "city",
        "params",
        "nodes",
        "edges",
        "boundary",
        "clips",
        "reference",
        "boeing",
    }
    assert bundle["city"]["crs"] == "EPSG:5070"
    assert bundle["city"]["proj4"].startswith("+proj=aea")
    assert bundle["city"]["utm_epsg"] == 32610
    assert bundle["boeing"] is None


def test_nodes_carry_pyproj_coordinates_and_street_count(graph, bundle):
    projected = project_graph(graph, "EPSG:5070")
    node_id, data = next(iter(projected.nodes(data=True)))
    node = next(n for n in bundle["nodes"] if n["id"] == node_id)
    assert node["x"] == data["x"]
    assert node["y"] == data["y"]
    assert node["sc"] == data["street_count"]
    assert node["lon"] == round(graph.nodes[node_id]["x"], 7)


def test_edges_are_directed_with_list_valued_names(graph, bundle):
    edges = bundle["edges"]
    assert len(edges) == graph.number_of_edges()
    assert all(
        isinstance(e["name"], list) and all(isinstance(n, str) for n in e["name"])
        for e in edges
    )
    assert any(e["name"] for e in edges)
    assert all(len(e["geom"]) >= 2 for e in edges)
    assert all(isinstance(e["oneway"], bool) for e in edges)


def test_clips_exist_for_every_buffer_and_rule(bundle):
    for rule in ("node_union", "boundary"):
        assert set(bundle["clips"][rule]) == {"100", "250", "500", "1000"}
        for multi in bundle["clips"][rule].values():
            assert multi and all(
                len(ring) >= 4 for polygon in multi for ring in polygon
            )


def test_the_reference_lists_every_cluster_and_each_variant(bundle):
    ref = bundle["reference"]
    degrees = {c["degree"] for c in ref["clusters"]}
    assert 1 in degrees and max(degrees) >= 3
    tiles = ref["variants"]["bbox"]["tiles"]
    assert tiles and all(t["degree"] >= 3 for t in tiles)
    assert all(t["id"].startswith("5070:") for t in tiles)
    assert all(t["area_m2"] > 0 for t in tiles)
    assert set(ref["variants"]) == {"node_union", "bbox", "boundary"}


def test_the_bbox_variant_covers_the_seeds_box_grown_by_the_clip_buffer(bundle):
    ref = bundle["reference"]
    params = ref["params"]
    clusters = [
        Cluster(osmids=tuple(c["osmids"]), x=c["x"], y=c["y"], degree=c["degree"])
        for c in ref["clusters"]
        if c["degree"] >= params["degree_filter_min"]
    ]
    seeds, _ = collapse_seed_collisions(clusters, grid_m=params["seed_grid_m"])
    buffer_m = params["clip_buffer_m"]
    xs, ys = [s.x for s in seeds], [s.y for s in seeds]
    width = max(xs) - min(xs) + 2 * buffer_m
    height = max(ys) - min(ys) + 2 * buffer_m
    tiles = ref["variants"]["bbox"]["tiles"]
    assert sum(t["area_m2"] for t in tiles) == pytest.approx(width * height, rel=1e-9)


def test_bundle_is_json_serializable(bundle):
    json.dumps(bundle)


def test_the_node_union_leaves_street_ends_out(graph, bundle):
    """A dead end's own disc would push a round lobe of the clip into a
    neighboring junction's tile, so the union is of the nodes that are not
    street ends: a street end between 100 m and 250 m from every other node
    lies outside the 100 m clip and inside the 250 m one."""
    projected = project_graph(graph, "EPSG:5070")
    ends = set(dead_end_nodes(projected))
    kept = [
        Point(d["x"], d["y"]) for n, d in projected.nodes(data=True) if n not in ends
    ]
    candidates = []
    for node in ends:
        point = Point(projected.nodes[node]["x"], projected.nodes[node]["y"])
        if 105 < min(point.distance(k) for k in kept) < 245:
            candidates.append(point)
    assert candidates

    def clip(buffer):
        return MultiPolygon(
            [
                Polygon(rings[0], rings[1:])
                for rings in bundle["clips"]["node_union"][buffer]
            ]
        )

    assert not any(clip("100").contains(p) for p in candidates)
    assert all(clip("250").contains(p) for p in candidates)


def test_the_bundle_reference_matches_generate_tiles(tmp_path):
    graph = osmnx.load_graphml(FIXTURES / "toppenish.graphml")
    bundle = export_bundle(graph, name="Toppenish")
    expected = generate_tiles(project_graph(graph, "EPSG:5070"))
    ids = [t["id"] for t in bundle["reference"]["variants"]["node_union"]["tiles"]]
    assert ids == [t.native_id for t in expected.tiles]


def test_write_bundle_lists_each_bundle_once_in_the_index(tmp_path):
    graph = osmnx.load_graphml(FIXTURES / "toppenish.graphml")
    bundle = export_bundle(graph, name="Toppenish")
    write_bundle(bundle, tmp_path)
    write_bundle(bundle, tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert index == {"bundles": [{"slug": "toppenish", "name": "Toppenish"}]}


@pytest.mark.parametrize(
    "corrupt",
    ["not json {{{", '{"nope": 1}', "[1, 2]", '{"bundles": "nope"}', ""],
)
def test_write_bundle_starts_fresh_from_a_corrupt_index(tmp_path, caplog, corrupt):
    """An unparsable or wrongly-shaped index must not fail the next write
    or lose the bundle it is about to list."""
    bundle = {"city": {"name": "Toppenish"}}
    write_bundle(bundle, tmp_path)
    (tmp_path / "index.json").write_text(corrupt)
    with caplog.at_level(logging.WARNING):
        write_bundle(bundle, tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert index == {"bundles": [{"slug": "toppenish", "name": "Toppenish"}]}
    assert "index.json" in caplog.text


def test_write_bundle_keeps_a_well_shaped_index(tmp_path, caplog):
    bundle = {"city": {"name": "Toppenish"}}
    other = {"city": {"name": "Spokane"}}
    write_bundle(bundle, tmp_path)
    write_bundle(other, tmp_path)
    with caplog.at_level(logging.WARNING):
        write_bundle(bundle, tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert index == {
        "bundles": [
            {"slug": "toppenish", "name": "Toppenish"},
            {"slug": "spokane", "name": "Spokane"},
        ]
    }
    assert caplog.text == ""


@pytest.mark.parametrize(
    "name",
    [
        "../../evil",
        "/abs/evil",
        "..",
        ".",
        "a/b",
        "a\\b",
        "evil\0name",
        "",
        "C:evil",
        "ads:stream",
        "line\nbreak",
        "tab\there",
    ],
)
def test_write_bundle_refuses_a_name_that_leaves_the_output_dir(tmp_path, name):
    with pytest.raises(ValueError, match="invalid bundle name") as excinfo:
        write_bundle({"city": {"name": name}}, tmp_path)
    if name:
        assert repr(name) in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    ['say "hi"', "what?", "a<b", "a>b", "a|b", "a*b"],
)
def test_write_bundle_refuses_a_character_windows_bans(tmp_path, name):
    """Windows refuses these characters in a file name, so a bundle
    saved under one elsewhere could not be copied to a Windows machine,
    and an export there would fail only at the final write."""
    with pytest.raises(ValueError, match="invalid bundle name") as excinfo:
        write_bundle({"city": {"name": name}}, tmp_path)
    assert repr(name) in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    ["CON", "nul", "Aux", "prn", "COM1", "lpt9", "com0", "com¹", "CONIN$", "nul.v2"],
)
def test_write_bundle_refuses_a_windows_device_name(tmp_path, name):
    """Windows maps these stems to devices whatever extension follows,
    so `nul.json` and `nul.v2.json` would both open the null device."""
    with pytest.raises(ValueError, match="invalid bundle name"):
        write_bundle({"city": {"name": name}}, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Concord", "concord"),
        ("Aux Cayes", "aux-cayes"),
        ("Nulato", "nulato"),
        ("com10", "com10"),
        ("St. Con", "st.-con"),
    ],
)
def test_a_name_that_only_contains_a_device_name_keeps_its_slug(name, slug):
    from flagstones.viz.export import _slug

    assert _slug(name) == slug


@pytest.mark.parametrize("name", ["index", "Index", "INDEX"])
def test_write_bundle_refuses_the_name_of_the_place_list(tmp_path, name):
    """The slug `index` would write the bundle over index.json, losing the
    bundle and every place already in the list."""
    (tmp_path / "index.json").write_text('{"bundles": [{"slug": "a", "name": "A"}]}')
    with pytest.raises(ValueError, match="invalid bundle name"):
        write_bundle({"city": {"name": name}}, tmp_path)
    assert (
        json.loads((tmp_path / "index.json").read_text())["bundles"][0]["slug"] == "a"
    )


def test_write_bundle_exports_a_unicode_name(tmp_path, bundle):
    name = "Спокан-Ünïcödé ✓"
    named = {**bundle, "city": {**bundle["city"], "name": name}}
    out = write_bundle(named, tmp_path)
    slug = name.lower().replace(" ", "-")
    assert out == tmp_path / f"{slug}.json"
    assert out.exists()
    index = json.loads((tmp_path / "index.json").read_text())
    assert index == {"bundles": [{"slug": slug, "name": name}]}


@pytest.mark.parametrize("crs", ["EPSG:5070", "EPSG:32610", "ESRI:54034"])
def test_the_bundle_carries_a_proj4_definition_for_its_crs(crs):
    block = crs_block(crs)
    assert block["crs"] == crs
    assert block["proj4"].startswith("+proj=")


def test_the_bundle_carries_the_legacy_reference():
    bundle = export_bundle(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), name="Toppenish"
    )
    legacy = bundle["reference"]["legacy"]
    assert legacy["utm_epsg"] == 32610
    assert len(legacy["tiles"]) > 0


def _viz_export(*args):
    return subprocess.run(
        [sys.executable, "-m", "flagstones.cli", "viz-export", *args],
        capture_output=True,
        check=False,
    )


def test_viz_export_writes_a_bundle_and_the_index(tmp_path):
    done = _viz_export(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--name",
        "Toppenish",
        "--out",
        str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    bundle = json.loads((tmp_path / "toppenish.json").read_text())
    assert bundle["city"]["name"] == "Toppenish"
    index = json.loads((tmp_path / "index.json").read_text())
    assert index == {"bundles": [{"slug": "toppenish", "name": "Toppenish"}]}


def test_viz_export_prints_a_path_its_stdout_cannot_encode(tmp_path):
    """A piped stdout on Windows takes the ANSI code page, which holds
    one alphabet. The bundle is on disk by the time its path prints, so
    a name outside that alphabet must not fail the run."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "viz-export",
            "--graph",
            str(FIXTURES / "toppenish.graphml"),
            "--name",
            "Спокан",
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "спокан.json").exists()
    assert done.stdout.strip().endswith(b".json")


def test_viz_export_takes_one_source(tmp_path):
    graph = str(FIXTURES / "toppenish.graphml")
    assert _viz_export("--out", str(tmp_path)).returncode == 2
    both = _viz_export("--place", "Toppenish", "--graph", graph, "--out", str(tmp_path))
    assert both.returncode == 2
    boundary = _viz_export("--place", "Toppenish", "--boundary", "b.geojson")
    assert boundary.returncode == 2


def test_viz_export_caches_under_viz_cache_by_default(tmp_path, monkeypatch):
    from flagstones import cli, viz

    class Stop(Exception):
        pass

    def stop(place):
        raise Stop

    monkeypatch.setattr(viz.export, "fetch_place", stop)
    monkeypatch.setattr(osmnx.settings, "cache_folder", "unset")
    with pytest.raises(Stop):
        cli.main(["viz-export", "--place", "Toppenish", "--out", str(tmp_path)])
    assert osmnx.settings.cache_folder == "viz/cache"
    with pytest.raises(Stop):
        cli.main(
            [
                "viz-export",
                "--place",
                "Toppenish",
                "--out",
                str(tmp_path),
                "--cache-dir",
                str(tmp_path / "c"),
            ]
        )
    assert osmnx.settings.cache_folder == str(tmp_path / "c")


def test_the_reference_carries_the_library_fallback_cells():
    bundle = export_bundle(
        osmnx.load_graphml(FIXTURES / "toppenish.graphml"), name="Toppenish"
    )
    reference = bundle["reference"]
    assert reference["fallback_params"] == {"cell_m": 250.0, "min_share": 0.1}
    assert bundle["clips"]["extent"]
    rows = reference["variants"]["node_union"]["fallback"]
    # Toppenish's hull reaches more than 250 m past its outer junctions, so
    # the node-union variant leaves ground for fallback cells; the bbox and
    # boundary clips cover the hull and leave none.
    assert rows and all(row["id"].startswith("5070:f:") for row in rows)
    assert [row["cell"] for row in rows] == sorted(row["cell"] for row in rows)
    assert reference["variants"]["bbox"]["fallback"] == []
    assert reference["variants"]["boundary"]["fallback"] == []


def test_a_reference_clip_buffer_outside_the_standard_ones_is_stored(graph):
    """The fallback reference and the viz read the clip at the reference's
    own buffer, so a buffer outside BUFFERS_M adds its clips."""
    bundle = export_bundle(graph, name="T", params=TileParams(clip_buffer_m=300.0))
    assert sorted(bundle["clips"]["node_union"], key=int) == [
        "100",
        "250",
        "300",
        "500",
        "1000",
    ]
    assert bundle["reference"]["variants"]["node_union"]["fallback"]


def test_a_fractional_clip_buffer_is_refused(graph):
    with pytest.raises(ValueError, match="whole number"):
        export_bundle(graph, name="T", params=TileParams(clip_buffer_m=250.5))


def test_a_graph_outside_the_seed_rule_is_refused():
    from flagstones import NonDrivableGraphError

    footway = osmnx.load_graphml(FIXTURES / "toppenish.graphml")
    u, v, k = next(iter(footway.edges(keys=True)))
    footway.edges[u, v, k]["highway"] = "footway"
    with pytest.raises(NonDrivableGraphError):
        export_bundle(footway, name="T")


def test_a_graph_with_no_seed_is_refused_by_name(graph):
    with pytest.raises(ValueError, match="no junction of degree 99"):
        export_bundle(graph, name="Toppenish", params=TileParams(degree_filter_min=99))


def test_fallback_off_leaves_the_reference_without_fallback_cells():
    graph = osmnx.load_graphml(FIXTURES / "toppenish.graphml")
    bundle = export_bundle(graph, name="Toppenish", fallback=None)
    assert "fallback_params" not in bundle["reference"]
    assert all("fallback" not in v for v in bundle["reference"]["variants"].values())


def test_viz_export_with_a_zero_cell_writes_no_fallback_reference(tmp_path):
    done = _viz_export(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--out",
        str(tmp_path),
        "--fallback-cell-m",
        "0",
    )
    assert done.returncode == 0, done.stderr
    bundle = json.loads((tmp_path / "toppenish.json").read_text())
    assert "fallback_params" not in bundle["reference"]


def test_viz_export_refuses_a_negative_cell_before_loading(tmp_path):
    done = _viz_export(
        "--graph",
        str(FIXTURES / "does-not-exist.graphml"),
        "--out",
        str(tmp_path),
        "--fallback-cell-m",
        "-1",
    )
    assert done.returncode == 2
    assert b"--fallback-cell-m wants a finite" in done.stderr


def test_a_graph_whose_nodes_enclose_no_area_is_refused_by_name():
    """Two nodes and one road: the legacy scope hull is a line."""
    import networkx as nx

    g = nx.MultiDiGraph(crs="EPSG:4326")
    g.add_node(1, x=-120.310, y=46.38, street_count=1)
    g.add_node(2, x=-120.300, y=46.38, street_count=1)
    for u, v in ((1, 2), (2, 1)):
        g.add_edge(u, v, 0, length=767.0, oneway=False, highway="residential")
    with pytest.raises(ValueError, match="no area"):
        export_bundle(g, name="T")


def test_a_crs_whose_proj4_form_drops_its_datum_shift_is_refused():
    """proj4 has no datum shift for a CRS it names only by ellipsoid, so the
    browser would place points about 125 m from pyproj's at London in
    EPSG:27700."""
    with pytest.raises(ValueError, match="datum"):
        crs_block("EPSG:27700", check_at=(-0.12, 51.5))
    assert crs_block("EPSG:5070", check_at=(-120.31, 46.38))["crs"] == "EPSG:5070"


def test_bundle_rings_keep_polygons_nested_in_a_collection():
    from shapely import GeometryCollection, LineString, box

    from flagstones.viz.bundle_clips import _multi

    nested = GeometryCollection(
        [LineString([(0, 0), (1, 1)]), MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])]
    )
    assert len(_multi(nested, decimals=2)) == 2


def test_bundle_edges_read_names_and_oneway_as_the_library_does():
    import networkx as nx

    from flagstones.viz.bundle_network import graph_edges

    g = nx.MultiDiGraph(crs="EPSG:4326")
    g.add_node(1, x=-120.31, y=46.38)
    g.add_node(2, x=-120.30, y=46.38)
    g.add_edge(1, 2, 0, name=[None, "A"], ref=float("nan"), oneway=(True, False))
    (edge,) = graph_edges(g)
    assert edge["name"] == ["A"] and edge["ref"] == [] and edge["oneway"] is True


def test_a_stored_clip_stays_valid_where_rounding_closes_a_neck():
    """A bridge 8 mm wide between two squares rounds to a line at the
    centimeter. Rounding each coordinate alone would store a ring that
    crosses itself, which GEOS and polygon-clipping read differently."""
    from flagstones.viz.bundle_clips import _from_multi, _multi

    neck = Polygon(
        [
            (0, 0),
            (1, 0),
            (1, 0.496),
            (2, 0.496),
            (2, 0),
            (3, 0),
            (3, 1),
            (2, 1),
            (2, 0.504),
            (1, 0.504),
            (1, 1),
            (0, 1),
        ]
    )
    stored = _from_multi(_multi(neck, decimals=2))
    assert stored.is_valid
    assert abs(stored.area - 2.0) < 1e-9


def test_the_reference_tiles_the_clips_the_bundle_stores(graph, bundle):
    """The browser tiles the stored, centimeter clips, so the reference
    tiles those too, and the parity tests compare the engines on the same
    clip."""
    from flagstones.tessellate import tessellate
    from flagstones.viz.bundle_clips import _from_multi

    for variant in ("node_union", "boundary"):
        clip = _from_multi(bundle["clips"][variant]["250"])
        rows = bundle["reference"]["variants"][variant]["tiles"]
        clusters = [
            Cluster(
                osmids=tuple(r["osmids"]),
                x=r["seed"][0],
                y=r["seed"][1],
                degree=r["degree"],
            )
            for r in rows
        ]
        tiles = tessellate(clusters, clip=clip, grid_m=24.0, crs="EPSG:5070")
        assert [t.native_id for t in tiles] == [r["id"] for r in rows]
        assert [t.geometry.area for t in tiles] == [r["area_m2"] for r in rows]


def test_an_invalid_boundary_is_refused_by_name(graph):
    bow_tie = Polygon(
        [(-120.33, 46.36), (-120.29, 46.40), (-120.29, 46.36), (-120.33, 46.40)]
    )
    with pytest.raises(ValueError, match="boundary is invalid: Self-intersection"):
        export_bundle(graph, name="Toppenish", boundary=bow_tie)
