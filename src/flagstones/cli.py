"""The `flagstones` command line, run with the `osm` extra installed."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from flagstones.errors import NonDrivableGraphError, PreConsolidatedGraphError
from flagstones.params import TileParams

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

# For a graph or parameter the library refuses, the CLI prints a stderr
# line. `AnisotropicCrsError` and `CrsScaleError` subclass `ValueError`, so
# the `ValueError` entry covers them.
_REFUSALS = (ValueError, NonDrivableGraphError, PreConsolidatedGraphError)

_NON_PARAM_KEYS = {"seed_exclude_highway", "fallback_cell_m", "fallback_min_share"}


def _load_params(path: str | None) -> TileParams | None:
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError:
        raise SystemExit(f"no such file: {path}") from None
    except OSError as error:
        raise SystemExit(f"cannot read {path}: {error}") from None
    except ValueError as error:
        # `tomllib.TOMLDecodeError` subclasses `ValueError`, so it arrives here.
        raise SystemExit(f"invalid TOML in {path}: {error}") from None
    if "tiles" not in document:
        raise SystemExit(f"{path} has no [tiles] table")
    table = document["tiles"]
    try:
        return TileParams.from_mapping(table, ignore=_NON_PARAM_KEYS)
    except (ValueError, TypeError) as error:
        # A string parked in a numeric slot trips `math.isfinite`, which
        # raises `TypeError`. Either way the tool prints a line and quits.
        raise SystemExit(str(error)) from None


_GRAPHML_READ_BYTES = 65536


def _check_graphml(path: str) -> None:
    """Refuse, via `ValueError`, any GraphML declaring a DOCTYPE or ENTITY.

    An entity expansion can reach gigabytes while the parser reads,
    which is why the scan hunts just those XML keywords through the raw
    text before osmnx sees the file. Each read hands its final bytes to the
    next, because a keyword can straddle the boundary between reads.

    Raw bytes are enough for this scan. Expat will sniff the encoding for
    itself and expand entities whatever the scan saw, and a UTF-16 or
    UTF-32 stream dots NUL bytes between the ASCII keywords, hiding them.
    While a UTF-16/32 encoding of ASCII packs a NUL beside every
    character, valid UTF-8 or ASCII XML has none at all, so a single NUL
    byte condemns the file without further argument.
    """
    carry = b""
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_GRAPHML_READ_BYTES), b""):
            if b"\x00" in chunk:
                raise ValueError(
                    f"{path} holds NUL bytes, so this is not UTF-8 or "
                    "ASCII XML; it may disguise entity declarations in a "
                    "wider encoding"
                )
            window = carry + chunk.upper()
            if b"<!DOCTYPE" in window or b"<!ENTITY" in window:
                raise ValueError(
                    f"{path} declares an XML DOCTYPE or ENTITY. Expansion "
                    "of what it defines can exhaust memory, and the scan "
                    "refuses the file before any parser reaches it"
                )
            carry = window[-8:]


def _load_graph(path: str):
    """Run `path` through the entity scan, then hand it to osmnx's loader.

    Anywhere on this path, in the scan or in the loader, a refusal becomes
    exactly one stderr line before exit.
    """
    import osmnx

    try:
        _check_graphml(path)
        graph = osmnx.load_graphml(path)
    except FileNotFoundError:
        raise SystemExit(f"no such file: {path}") from None
    except OSError as error:
        raise SystemExit(f"cannot read {path}: {error}") from None
    except (ValueError, SyntaxError) as error:
        # `_check_graphml` raises `ValueError`, a truncated or misencoded file
        # dies inside `xml.etree.ParseError` (descends from `SyntaxError`), and
        # neither case should reach the user as a traceback.
        raise SystemExit(str(error)) from None
    if graph.number_of_nodes() == 0:
        raise SystemExit(f"{path} contains no nodes")
    return graph


def _generate(args: argparse.Namespace) -> None:
    from pyproj.exceptions import CRSError
    from shapely import MultiPoint

    from flagstones.crs import require_metric_crs, utm_crs_for
    from flagstones.fallback import FallbackGrid
    from flagstones.geo import tiles_to_geodataframe
    from flagstones.osm import _lon_lat_center, project_graph
    from flagstones.pipeline import generate_tiles

    out = Path(args.out)
    if not out.parent.is_dir():
        raise SystemExit(f"no such directory: {out.parent}")
    params = _load_params(args.params)
    graph = _load_graph(args.graph)
    try:
        crs = args.crs or utm_crs_for(*_lon_lat_center(graph))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    try:
        # When the CRS is geographic or counts in feet, say so before the
        # anisotropy check misreads it and before any consolidation work.
        require_metric_crs(crs)
        projected = project_graph(graph, crs)
    except CRSError:
        raise SystemExit(f"invalid CRS: {crs}") from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    fallback = (
        FallbackGrid(cell_m=args.fallback_cell_m) if args.fallback_cell_m > 0 else None
    )
    extent = None
    if fallback is not None:
        points = [(d["x"], d["y"]) for _, d in projected.nodes(data=True)]
        extent = MultiPoint(points).convex_hull
    try:
        tile_set = generate_tiles(
            projected, params, fallback=fallback, fallback_extent=extent
        )
    except _REFUSALS as error:
        raise SystemExit(str(error)) from None
    gdf = tiles_to_geodataframe(tile_set)
    suffix = out.suffix.lower()
    if suffix == ".parquet":
        gdf.to_parquet(out)
    elif suffix == ".geojson":
        gdf["seed_osmids"] = gdf["seed_osmids"].map(
            lambda ids: ",".join(str(i) for i in ids)
        )
        gdf.to_file(out, driver="GeoJSON")


def _viz_export(args: argparse.Namespace) -> None:
    import geopandas as gpd
    import osmnx

    from flagstones.fallback import FallbackGrid
    from flagstones.viz.export import (
        _slug,
        export_bundle,
        fetch_place,
        write_bundle,
    )

    out = Path(args.out)
    # Settle the effective bundle name now, whether explicit or derived, so
    # an unsafe one dies before any graph load, Overpass fetch, or tiling.
    # The default fills only an omitted `--name`, so `--name ""` still makes
    # it to `_slug`'s refusal.
    if args.name is not None:
        name = args.name
    elif args.place is not None:
        name = args.place.split(",")[0].strip()
    else:
        name = Path(args.graph).stem
    try:
        _slug(name)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    params = _load_params(args.params)
    if args.place is not None:
        osmnx.settings.cache_folder = args.cache_dir
        osmnx.settings.use_cache = True
        try:
            graph, boundary = fetch_place(args.place)
        except ValueError as error:
            raise SystemExit(str(error)) from None
    else:
        graph = _load_graph(args.graph)
        boundary = None
        if args.boundary is not None:
            if not Path(args.boundary).is_file():
                raise SystemExit(f"no such file: {args.boundary}")
            boundary = gpd.read_file(args.boundary).to_crs(4326).union_all()
    fallback = (
        FallbackGrid(cell_m=args.fallback_cell_m) if args.fallback_cell_m > 0 else None
    )
    try:
        bundle = export_bundle(
            graph, name=name, boundary=boundary, params=params, fallback=fallback
        )
        written = write_bundle(bundle, out)
    except _REFUSALS as error:
        raise SystemExit(str(error)) from None
    # A piped stdout on Windows takes the ANSI code page, which cannot encode
    # a bundle name from most alphabets. The bundle is already on disk, so
    # the path prints with escapes in place of what the stream cannot hold.
    encoding = sys.stdout.encoding or "utf-8"
    print(str(written).encode(encoding, "backslashreplace").decode(encoding))


def _check_cell_size(parser: argparse.ArgumentParser, cell_m: float) -> None:
    # `nan < 0` compares false, so a bare range test lets NaN through, and
    # the `cell_m > 0` check downstream would then switch the grid off with
    # no complaint.
    if not math.isfinite(cell_m) or cell_m < 0:
        parser.error("--fallback-cell-m wants a finite, non-negative count of meters")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="flagstones")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="tile a saved road network")
    gen.add_argument(
        "--graph", required=True, help="GraphML file holding the seed road network"
    )
    gen.add_argument("--params", help="TOML file with a [tiles] table of parameters")
    gen.add_argument(
        "--crs",
        help="CRS to tile in (default: the UTM zone of the graph's center)",
    )
    gen.add_argument(
        "--fallback-cell-m",
        type=float,
        default=250.0,
        help="side length in meters of the fallback grid cells; 0 turns the grid off",
    )
    gen.add_argument(
        "--out", required=True, help="output path ending .geojson or .parquet"
    )
    gen.set_defaults(func=_generate)
    viz = commands.add_parser(
        "viz-export", help="write a data bundle for the browser tool"
    )
    source = viz.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--place", help="place name to geocode; its network comes from Overpass"
    )
    source.add_argument("--graph", help="GraphML file holding the seed road network")
    viz.add_argument(
        "--boundary",
        help=(
            "GeoJSON outline of the place, paired with --graph "
            "(default: convex hull of the graph nodes)"
        ),
    )
    viz.add_argument(
        "--name", help="bundle name (default: the place, or the graph file's stem)"
    )
    viz.add_argument("--params", help="TOML file with a [tiles] table of parameters")
    viz.add_argument(
        "--out", default="viz/data", help="directory the bundle and its index land in"
    )
    viz.add_argument(
        "--fallback-cell-m",
        type=float,
        default=250.0,
        help=(
            "side length in meters of the fallback grid cells; "
            "0 skips the fallback reference"
        ),
    )
    viz.add_argument(
        "--cache-dir",
        default="viz/cache",
        help="osmnx cache directory for Overpass and geocoding replies (with --place)",
    )
    viz.set_defaults(func=_viz_export)
    args = parser.parse_args(argv)
    _check_cell_size(parser, args.fallback_cell_m)
    if args.command == "generate":
        if Path(args.out).suffix.lower() not in (".geojson", ".parquet"):
            parser.error("--out must end in .geojson or .parquet")
    elif args.boundary is not None and args.graph is None:
        parser.error("--boundary needs --graph")
    args.func(args)


if __name__ == "__main__":
    main()
