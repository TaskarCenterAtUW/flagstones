import json
import subprocess
import sys
from pathlib import Path

import pytest

osmnx = pytest.importorskip("osmnx")
FIXTURES = Path(__file__).parent / "fixtures"


def _generate(tmp_path, *extra):
    out = tmp_path / "tiles.geojson"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "generate",
            "--graph",
            str(FIXTURES / "toppenish.graphml"),
            "--crs",
            "EPSG:5070",
            "--out",
            str(out),
            *extra,
        ],
        check=True,
    )
    return json.loads(out.read_text())["features"]


def test_generate_writes_geojson_in_wgs84(tmp_path):
    features = _generate(tmp_path, "--fallback-cell-m", "0")
    golden = json.loads((FIXTURES / "toppenish.golden.json").read_text())
    assert len(features) == len(golden["tiles"])
    assert features[0]["properties"]["tile_id"] == golden["tiles"][0]["native_id"]
    assert {f["properties"]["kind"] for f in features} == {"intersection"}


def test_generate_writes_parquet_with_the_same_tiles_as_geojson(tmp_path):
    out = tmp_path / "tiles.parquet"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "generate",
            "--graph",
            str(FIXTURES / "toppenish.graphml"),
            "--crs",
            "EPSG:5070",
            "--out",
            str(out),
            "--fallback-cell-m",
            "0",
        ],
        check=True,
    )
    import geopandas

    frame = geopandas.read_parquet(out)
    features = _generate(tmp_path, "--fallback-cell-m", "0")
    assert len(frame) == len(features)


def test_generate_fills_the_graph_hull_with_fallback_cells_by_default(tmp_path):
    features = _generate(tmp_path)
    golden = json.loads((FIXTURES / "toppenish.golden.json").read_text())
    kinds = [f["properties"]["kind"] for f in features]
    assert kinds.count("intersection") == len(golden["tiles"])
    assert kinds.count("fallback") > 0
    assert all(
        f["properties"]["tile_id"].startswith("5070:f:")
        for f in features
        if f["properties"]["kind"] == "fallback"
    )


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "flagstones.cli", "generate", *args],
        capture_output=True,
        check=False,
    )


def test_a_bad_out_suffix_or_cell_size_is_refused_before_loading(tmp_path):
    graph = str(FIXTURES / "does-not-exist.graphml")
    assert _run("--graph", graph, "--out", str(tmp_path / "t.csv")).returncode == 2
    for cell_m in ("-1", "nan", "inf"):
        done = _run(
            "--graph",
            graph,
            "--out",
            str(tmp_path / "t.geojson"),
            "--fallback-cell-m",
            cell_m,
        )
        assert done.returncode == 2 and b"--fallback-cell-m" in done.stderr


def test_params_without_a_tiles_table_is_an_error(tmp_path):
    params = tmp_path / "p.toml"
    params.write_text("[other]\nx = 1\n")
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--params",
        str(params),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    assert done.returncode != 0 and b"no [tiles] table" in done.stderr


def test_params_with_an_unknown_key_is_a_clean_error(tmp_path):
    params = tmp_path / "p.toml"
    params.write_text("[tiles]\nseed_grid_m = 24\nseed_exclude_highwas = []\n")
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--params",
        str(params),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    assert done.returncode != 0
    assert b"unrecognized tile parameter" in done.stderr
    assert b"Traceback" not in done.stderr


_HOSTILE_GRAPHML = (
    '<?xml version="1.0"?>\n<!DOCTYPE graphml [\n<!ENTITY lol "lol">\n]>\n<graphml/>\n'
)


def test_a_graphml_with_a_doctype_or_entity_is_refused(tmp_path):
    from flagstones import cli

    path = tmp_path / "hostile.graphml"
    path.write_text(_HOSTILE_GRAPHML)
    with pytest.raises(ValueError, match="DOCTYPE or ENTITY"):
        cli._check_graphml(str(path))


def test_the_graphml_check_spans_a_chunk_boundary(tmp_path):
    """The keywords can straddle the read boundary in a large file, so the
    scan carries the tail of each chunk into the next."""
    from flagstones import cli

    head = b"<graphml>"
    # Chunk 1 holds the first 4 bytes of the 9-byte marker; the remaining
    # 5 fall into chunk 2 and are found through the carry.
    pad = b" " * (65536 - len(head) - 4)
    path = tmp_path / "split.graphml"
    path.write_bytes(head + pad + b"<!DOCTYPE graphml [<!ENTITY x 'y'>]>")
    with pytest.raises(ValueError, match="DOCTYPE or ENTITY"):
        cli._check_graphml(str(path))


def test_a_clean_graphml_passes_the_check(tmp_path):
    from flagstones import cli

    cli._check_graphml(str(FIXTURES / "toppenish.graphml"))


def test_a_hostile_graphml_is_refused_before_osmnx_parses(tmp_path, monkeypatch):
    from flagstones import cli

    path = tmp_path / "hostile.graphml"
    path.write_text(_HOSTILE_GRAPHML)

    def never(*args, **kwargs):
        raise AssertionError("osmnx.load_graphml must not be reached")

    monkeypatch.setattr(osmnx, "load_graphml", never)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["generate", "--graph", str(path), "--out", str(tmp_path / "t.geojson")]
        )
    assert "DOCTYPE or ENTITY" in str(excinfo.value)


# Two entity levels, expansion far below 4 KB; the point is the
# encoding, not the size.
_UTF16_ENTITY_GRAPHML = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE g [<!ENTITY a "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx">'
    '<!ENTITY b "&a;&a;">]>\n'
    '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
    '<graph id="G" edgedefault="directed"><node id="1"/></graph>\n'
    "</graphml>\n"
).encode("utf-16-le")


def test_a_utf16_graphml_is_refused_on_its_nul_bytes(tmp_path, monkeypatch):
    """The keyword scan reads raw bytes, so a UTF-16 stream hides the
    ASCII keywords between NUL bytes while expat still parses and expands."""
    from flagstones import cli

    path = tmp_path / "utf16.graphml"
    path.write_bytes(_UTF16_ENTITY_GRAPHML)

    def never(*args, **kwargs):
        raise AssertionError("osmnx.load_graphml must not be reached")

    monkeypatch.setattr(osmnx, "load_graphml", never)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["generate", "--graph", str(path), "--out", str(tmp_path / "t.geojson")]
        )
    assert "NUL" in str(excinfo.value)


def test_a_graphml_with_a_stray_nul_byte_is_refused(tmp_path):
    from flagstones import cli

    path = tmp_path / "nul.graphml"
    path.write_bytes(b'<graphml>\x00<graph id="G"/>\n')
    with pytest.raises(ValueError, match="NUL"):
        cli._check_graphml(str(path))


def _assert_one_line_error(done, phrase):
    lines = done.stderr.decode().strip().splitlines()
    assert done.returncode == 1, done.stderr
    assert len(lines) == 1, done.stderr
    assert "Traceback" not in done.stderr.decode()
    assert phrase in lines[0]


def test_a_missing_params_file_is_a_one_line_error(tmp_path):
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--params",
        str(tmp_path / "absent.toml"),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, "no such file:")


def test_a_missing_graph_file_is_a_one_line_error(tmp_path):
    done = _run(
        "--graph",
        str(tmp_path / "absent.graphml"),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, "no such file:")


def test_a_missing_graph_file_is_a_one_line_error_for_viz_export(tmp_path):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "viz-export",
            "--graph",
            str(tmp_path / "absent.graphml"),
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
    )
    _assert_one_line_error(done, "no such file:")


def test_an_invalid_crs_is_a_one_line_error(tmp_path):
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--crs",
        "EPSG:999999",
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, "invalid CRS: EPSG:999999")


@pytest.mark.parametrize(
    ("crs", "phrase"),
    [
        ("EPSG:4326", "not a projected CRS"),
        ("EPSG:2285", "instead of meters"),
        ("EPSG:3857", "scale"),
    ],
)
def test_a_crs_the_tiling_cannot_use_is_a_one_line_error(tmp_path, crs, phrase):
    """A geographic or foot-unit --crs is refused before projection, so the
    message names the CRS's real fault and no consolidation runs first."""
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--crs",
        crs,
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, phrase)


def test_a_graph_outside_the_seed_rule_is_a_one_line_error(tmp_path):
    graph = osmnx.load_graphml(FIXTURES / "toppenish.graphml")
    u, v, k = next(iter(graph.edges(keys=True)))
    graph.edges[u, v, k]["highway"] = "footway"
    path = tmp_path / "footway.graphml"
    osmnx.save_graphml(graph, path)
    done = _run("--graph", str(path), "--out", str(tmp_path / "t.geojson"))
    _assert_one_line_error(done, "seed rule excludes")


def test_a_string_param_value_is_a_one_line_error(tmp_path):
    params = tmp_path / "p.toml"
    params.write_text('[tiles]\nclip_buffer_m = "abc"\n')
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--params",
        str(params),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, "must be real number, not str")


def test_a_truncated_graphml_is_a_one_line_error(tmp_path):
    graph = tmp_path / "truncated.graphml"
    text = (FIXTURES / "toppenish.graphml").read_text()
    graph.write_text(text[: len(text) // 2])
    done = _run("--graph", str(graph), "--out", str(tmp_path / "t.geojson"))
    _assert_one_line_error(done, "no element found")


def test_malformed_toml_is_a_one_line_error(tmp_path):
    params = tmp_path / "p.toml"
    params.write_text("[tiles\nclip_buffer_m = 4\n")
    done = _run(
        "--graph",
        str(FIXTURES / "toppenish.graphml"),
        "--params",
        str(params),
        "--out",
        str(tmp_path / "t.geojson"),
    )
    _assert_one_line_error(done, "invalid TOML in")


def test_a_directory_as_graph_is_a_one_line_error(tmp_path):
    done = _run("--graph", str(tmp_path), "--out", str(tmp_path / "t.geojson"))
    _assert_one_line_error(done, "cannot read")


def test_viz_export_refuses_an_empty_name_before_writing(tmp_path):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "viz-export",
            "--graph",
            str(FIXTURES / "toppenish.graphml"),
            "--name",
            "",
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
    )
    _assert_one_line_error(done, "invalid bundle name")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["a:b", "", "x" * 300])
def test_viz_export_refuses_a_bad_explicit_name_before_loading(
    tmp_path, monkeypatch, name
):
    from flagstones import cli
    from flagstones.viz import export as viz_export

    def never(*args, **kwargs):
        raise AssertionError("export_bundle must not be reached")

    monkeypatch.setattr(viz_export, "export_bundle", never)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "viz-export",
                "--graph",
                str(FIXTURES / "toppenish.graphml"),
                "--name",
                name,
                "--out",
                str(tmp_path),
            ]
        )
    message = str(excinfo.value)
    assert "invalid bundle name" in message and "\n" not in message
    assert list(tmp_path.iterdir()) == []


def test_out_suffix_is_case_insensitive(tmp_path):
    out = tmp_path / "TILES.GEOJSON"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "flagstones.cli",
            "generate",
            "--graph",
            str(FIXTURES / "toppenish.graphml"),
            "--crs",
            "EPSG:5070",
            "--out",
            str(out),
            "--fallback-cell-m",
            "0",
        ],
        check=True,
    )
    assert out.exists()


def test_an_out_path_in_a_missing_directory_is_refused_before_loading(tmp_path):
    graph = str(FIXTURES / "does-not-exist.graphml")
    done = _run("--graph", graph, "--out", str(tmp_path / "missing" / "t.geojson"))
    assert done.returncode == 1
    assert done.stderr.decode().strip() == f"no such directory: {tmp_path / 'missing'}"


def test_a_graphml_with_no_nodes_is_a_one_line_error(tmp_path):
    empty = tmp_path / "empty.graphml"
    empty.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
        '<key id="d0" for="graph" attr.name="crs" attr.type="string"/>'
        '<graph edgedefault="directed"><data key="d0">epsg:4326</data></graph>'
        "</graphml>\n"
    )
    done = _run("--graph", str(empty), "--out", str(tmp_path / "t.geojson"))
    assert done.returncode == 1
    assert done.stderr.decode().strip() == f"{empty} contains no nodes"


def test_a_missing_boundary_file_is_a_one_line_error(tmp_path):
    from flagstones import cli

    missing = tmp_path / "missing.geojson"
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "viz-export",
                "--graph",
                str(FIXTURES / "toppenish.graphml"),
                "--boundary",
                str(missing),
                "--out",
                str(tmp_path / "data"),
            ]
        )
    assert str(excinfo.value) == f"no such file: {missing}"


def test_a_graph_beyond_the_utm_range_is_a_one_line_error(tmp_path):
    import networkx as nx

    graph = nx.MultiDiGraph(crs="epsg:4326")
    for node, (x, y) in enumerate([(0.0, 85.0), (0.01, 85.0), (0.0, 85.01)]):
        graph.add_node(node, x=x, y=y, street_count=1)
    graph.add_edge(0, 1, highway="residential", length=800.0, oneway=False)
    graph.add_edge(1, 0, highway="residential", length=800.0, oneway=False)
    path = tmp_path / "polar.graphml"
    osmnx.save_graphml(graph, path)
    done = _run("--graph", str(path), "--out", str(tmp_path / "t.geojson"))
    assert done.returncode == 1
    lines = done.stderr.decode().strip().splitlines()
    assert len(lines) == 1 and "UTM" in lines[0]


def test_a_place_the_geocoder_cannot_find_is_a_one_line_error(tmp_path, monkeypatch):
    from flagstones import cli
    from flagstones.viz import export as viz_export

    def unknown(query):
        raise ValueError(f"Nominatim could not geocode query {query!r}")

    monkeypatch.setattr(viz_export, "fetch_place", unknown)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "viz-export",
                "--place",
                "Nowhere, Nowhere",
                "--out",
                str(tmp_path / "data"),
                "--cache-dir",
                str(tmp_path / "cache"),
            ]
        )
    assert str(excinfo.value) == "Nominatim could not geocode query 'Nowhere, Nowhere'"
