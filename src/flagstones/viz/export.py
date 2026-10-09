"""Package a place into a data bundle the viz tool can open. Requires the
`osm` extra.

The library generates the reference tiles stored in each bundle, and
`viz/test/parity.test.js` checks the browser engine against them. Stored
nodes carry lon/lat read off the unprojected graph beside x/y as pyproj
projected them into the bundle CRS, so the browser reproduces the
library's tiling itself, without replaying the datum pipeline.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
import shapely
from pyproj import CRS, Transformer
from shapely import MultiPoint
from shapely.geometry.base import BaseGeometry

from flagstones import FallbackGrid
from flagstones.clip import node_union_clip, repair_reprojected
from flagstones.crs import (
    _is_geographic,
    canonical_crs,
    crs_authority_string,
    utm_crs_for,
)
from flagstones.graph import SEED_EXCLUDE_HIGHWAY, _edge_table, require_drivable_graph
from flagstones.osm import fetch_seed_graph, project_graph
from flagstones.params import TileParams
from flagstones.viz.bundle_clips import (
    BUFFERS_M,
    _from_multi,
    _multi,
    clip_polygons,
)
from flagstones.viz.bundle_network import graph_edges, graph_nodes
from flagstones.viz.reference import (
    _fallback_rows,
    _reference,
    fallback_reference,
    legacy_reference,
)

__all__ = [
    "SCHEMA",
    "crs_block",
    "export_bundle",
    "fallback_reference",
    "fetch_place",
    "legacy_reference",
    "write_bundle",
]

logger = logging.getLogger(__name__)

SCHEMA = "flagstones-viz/1"
_DEFAULT_FALLBACK = FallbackGrid()


def _to_crs(geometry: BaseGeometry, crs: str) -> BaseGeometry:
    return gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(crs).iloc[0]


# The largest gap, in meters, this exporter tolerates between a point as
# pyproj projects it and as the CRS's proj4 form projects it. Beyond that,
# the browser draws and drags nodes off the library's coordinates.
_PROJ4_TOLERANCE_M = 0.01


def crs_block(crs: str, *, check_at: tuple[float, float] | None = None) -> dict:
    """Produce the bundle's CRS entries, an authority string and the proj4 string.

    The viz engine keys a CRS by the authority string, while
    viz/src/engine/proj.js registers its proj4 definition, so export and
    render need no table of known codes. Ellipsoid alone is what
    a proj4 string remembers of a datum, and a CRS on another datum loses
    its shift to WGS84 accordingly (EPSG:27700, for instance, misses by
    89 m at Edinburgh, 125 m at London). Hand `check_at` a lon, lat pair and
    the exporter refuses any CRS whose proj4 form strays from pyproj there
    by more than `_PROJ4_TOLERANCE_M`. A CRS missing the definition, or
    the tolerance, raises `ValueError`.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        definition = CRS.from_user_input(crs).to_proj4()
    if not definition:
        raise ValueError(
            f"{crs} has no proj4 definition, so the browser cannot project it"
        )
    if check_at is not None:
        lon, lat = check_at
        exact = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        plain = Transformer.from_crs(
            "EPSG:4326", CRS.from_proj4(definition), always_xy=True
        )
        offset = math.dist(exact.transform(lon, lat), plain.transform(lon, lat))
        if not offset <= _PROJ4_TOLERANCE_M:
            raise ValueError(
                f"the proj4 form of {crs} drops the datum shift. Nodes "
                f"would end up {offset:.1f} m from the library's position "
                f"at ({lat:.2f}, {lon:.2f}). Choose a CRS whose datum is "
                "WGS84 or NAD83"
            )
    return {"crs": crs_authority_string(crs), "proj4": definition}


def export_bundle(
    graph,
    *,
    name: str,
    boundary: BaseGeometry | None = None,
    params: TileParams | None = None,
    crs: str = "EPSG:5070",
    fallback: FallbackGrid | None = _DEFAULT_FALLBACK,
    exclude_highway: Sequence[str] | None = SEED_EXCLUDE_HIGHWAY,
) -> dict:
    """Build the browser bundle of `graph` in projection `crs`, tuned by `params`.

    `graph` must be an osmnx MultiDiGraph in any CRS osmnx can project.
    Pass `boundary` as a place outline in EPSG:4326 and the boundary clip
    grows by buffering it. Where it is absent the graph nodes' convex hull
    fills in. `fallback=None` drops the fallback reference from the
    output. `exclude_highway` carries the seed rule `generate_tiles`
    screens a graph against; any noncompliant edge triggers
    `NonDrivableGraphError`, so the bundle cannot advertise tiling the
    library would refuse. `ValueError` answers when no cluster reaches
    `degree_filter_min`, when `clip_buffer_m` has a fraction (the bundle
    keys clips by whole meters), when `boundary` is invalid, when the
    nodes cover no area (fewer than three, or collinear), and when `crs`
    is one the browser cannot reproduce (see `crs_block`).
    """
    params = params or TileParams()
    crs = canonical_crs(crs)
    if boundary is not None and not boundary.is_valid:
        raise ValueError(f"boundary is invalid: {shapely.is_valid_reason(boundary)}")
    if params.clip_buffer_m != int(params.clip_buffer_m):
        raise ValueError(
            f"clip_buffer_m {params.clip_buffer_m} must be a whole number, "
            "because bundle clips are keyed by whole-meter integers"
        )
    if exclude_highway is not None:
        require_drivable_graph(graph, exclude_highway=exclude_highway)
    lon_lat = (
        graph if _is_geographic(graph) else ox.project_graph(graph, to_latlong=True)
    )
    if boundary is None:
        boundary = MultiPoint(
            [(d["x"], d["y"]) for _, d in lon_lat.nodes(data=True)]
        ).convex_hull
    projected = project_graph(lon_lat, crs)
    center = boundary.centroid
    utm_epsg = int(utm_crs_for(center.x, center.y).split(":")[1])
    nodes = graph_nodes(projected, lon_lat)
    edges = graph_edges(lon_lat)
    hull = MultiPoint([(n["lon"], n["lat"]) for n in nodes]).convex_hull
    if hull.geom_type != "Polygon":
        raise ValueError(
            "the graph's nodes cover no area, fewer than three or all "
            "collinear, so the legacy tiling has no hull to scope itself to"
        )
    scope = list(hull.exterior.coords)
    boundary_projected = repair_reprojected(_to_crs(boundary, crs))
    table = _edge_table(projected)
    buffers = sorted({*BUFFERS_M, params.clip_buffer_m})
    unions = {b: node_union_clip(projected, buffer_m=b, _table=table) for b in buffers}
    clips = clip_polygons(projected, boundary_projected, buffers=buffers, unions=unions)
    # The browser tiling runs on the stored, centimeter-rounded geometry,
    # so the reference tiles that same geometry.
    key = str(int(params.clip_buffer_m))
    ref, tiled = _reference(
        projected,
        params,
        crs,
        boundary_projected,
        clips={
            "node_union": _from_multi(clips["node_union"][key]),
            "boundary": _from_multi(clips["boundary"][key]),
        },
        table=table,
    )
    ref["legacy"] = legacy_reference(nodes, edges, scope, utm_epsg)
    bundle = {
        "schema": SCHEMA,
        "city": {
            "name": name,
            **crs_block(crs, check_at=(center.x, center.y)),
            "utm_epsg": utm_epsg,
            "center": [round(center.x, 6), round(center.y, 6)],
            "bounds": [round(v, 6) for v in boundary.bounds],
            "osmnx_version": ox.__version__,
            "graph_nodes": projected.number_of_nodes(),
            "graph_edges": projected.number_of_edges(),
        },
        "params": dataclasses.asdict(params),
        "nodes": nodes,
        "edges": edges,
        "boundary": _multi(boundary, decimals=7),
        "clips": clips,
        "reference": ref,
        "boeing": None,
    }
    if fallback is not None:
        extent = _from_multi(clips["extent"])
        for variant, tiles in tiled.items():
            bundle["reference"]["variants"][variant]["fallback"] = _fallback_rows(
                tiles, extent=extent, crs=crs, grid=fallback
            )
        bundle["reference"]["fallback_params"] = dataclasses.asdict(fallback)
    return bundle


def fetch_place(query: str) -> tuple[Any, BaseGeometry]:
    """Geocode `query` and return the place's seed graph plus outline,
    both in EPSG:4326.
    """
    boundary = ox.geocode_to_gdf(query).geometry.iloc[0]
    return fetch_seed_graph(boundary), boundary


# ext4 and APFS cap a single path component at this many bytes. NTFS caps
# it at 255 UTF-16 code units, which a name within this byte count never
# exceeds.
_FILE_NAME_MAX_BYTES = 255

# Windows refuses these characters in a file name, as it does the path
# separators, the colon, and anything under `0x20`.
_WINDOWS_BANNED_CHARACTERS = '<>"|?*'

# Windows maps these stems to devices whatever extension follows, so
# `nul.json` opens the null device and no file.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{port}{n}" for port in ("com", "lpt") for n in "0123456789¹²³"}
)


def _slug(name: str) -> str:
    """Produce the bundle's file stem and turn away names selecting unsafe files.

    `write_bundle` appends this stem to the output directory, so
    `../../evil` or `/abs/evil` would drag the written file outside it.
    Windows sees a colon as the drive separator, and one doubles as the
    handle for NTFS alternate data streams. `index`, kept as a stem,
    would clobber the `index.json` place list. The check therefore
    rejects empty results, the reserved `.`, `..`, and `index`, embedded
    `/`, `\\` or `:`, and characters under `0x20`. The browser's URL
    parser throws tab, LF, and CR away whenever it resolves a fetch path,
    Windows bans those characters from file names outright, and a bundle
    saved under either fault could not be fetched back.

    A bundle directory has to work on Windows too, wherever the export
    ran. The check rejects `<`, `>`, `"`, `|`, `?` and `*`, which Windows
    bans from file names, and a stem whose part before the first dot is
    a Windows device name (`con`, `prn`, `aux`, `nul`, `com1`, `lpt1`
    and their kin), which Windows opens as the device whatever extension
    follows. Since the viz lists places beyond English-speaking ones,
    names in any alphabet pass. A stem long enough to push `<slug>.json`
    past 255 UTF-8 bytes, the usual cap, fails the check here too.
    Without that guard, an export would spend an entire run before the
    filesystem rejected the file.
    """
    slug = name.lower().replace(" ", "-")
    if (
        not slug
        or slug in (".", "..", "index")
        or slug.partition(".")[0] in _WINDOWS_DEVICE_NAMES
        or any(ch in slug for ch in "/\\:" + _WINDOWS_BANNED_CHARACTERS)
        or any(ord(ch) < 0x20 for ch in slug)
    ):
        raise ValueError(
            f"invalid bundle name {name!r}: a slug may not be empty, '.', "
            "'..', 'index' or a Windows device name such as 'con' or 'nul', "
            'and may not contain any of / \\ : < > " | ? * or control '
            "characters"
        )
    if len(f"{slug}.json".encode()) > _FILE_NAME_MAX_BYTES:
        raise ValueError(
            f"invalid bundle name {name[:40]!r}...: the file name would "
            f"exceed {_FILE_NAME_MAX_BYTES} bytes"
        )
    return slug


def _index_entries(index_path: Path) -> list[dict]:
    """Read out the listed bundles, treating a damaged index file as empty.

    A save that is about to list a good bundle should not die because
    `index.json` was torn mid-write or hand-mangled. When the JSON will
    not parse, or its shape takes no append, the writer begins the index
    anew and the logger says why.
    """
    if not index_path.exists():
        return []
    try:
        document = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        logger.warning(f"index {index_path} unreadable ({error}); starting it fresh")
        return []
    entries = document.get("bundles") if isinstance(document, dict) else None
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or "slug" not in entry for entry in entries
    ):
        logger.warning(f"index {index_path} has no usable bundles list; rebuilding")
        return []
    return entries


def write_bundle(bundle: dict, out_dir: Path) -> Path:
    """Write `bundle` to `out_dir/<slug>.json` and return that path.

    `out_dir/index.json`, the place list the viz reads, gains an entry
    too. When the slug is already listed, its entry updates in place and
    does not move. Before a byte reaches disk, a name that `_slug`
    rejects raises `ValueError`.
    """
    slug = _slug(bundle["city"]["name"])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = bundle["city"]["name"]
    out = out_dir / f"{slug}.json"
    out.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    logger.info(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    index_path = out_dir / "index.json"
    entries = _index_entries(index_path)
    entry = {"slug": slug, "name": name}
    for i, existing in enumerate(entries):
        if existing["slug"] == slug:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    index_path.write_text(
        json.dumps({"bundles": entries}, indent=1) + "\n", encoding="utf-8"
    )
    return out
