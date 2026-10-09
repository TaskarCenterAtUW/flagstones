# AGENTS.md

`flagstones` builds Voronoi tiles seeded on consolidated road
intersections, plus optional fallback grid cells over ground the tiles leave
uncovered. The package is `flagstones` under `src/`.

## Layout

- `src/flagstones/`: the library. The top level exports exactly
  `generate_tiles`, `TileParams`, `TileSet`, `Tile`, `Cluster`, `FallbackGrid`,
  the exceptions, and `__version__`.
- `tests/`: tests, shared graph builders (`tests/graphs.py`), and fixtures
  under `tests/fixtures/`. Tests live here and not beside the modules, so the
  wheel carries neither tests nor fixtures.
- `viz/`: the browser tool and its JavaScript engine. It stays out of the
  wheel and the sdist.
  `viz/test/fixtures/tile-params.json` is a dump of `TileParams()`. Refresh it
  with `uv run python -c "import dataclasses, json; from flagstones import TileParams; print(json.dumps(dataclasses.asdict(TileParams()), indent=1, sort_keys=True))" > viz/test/fixtures/tile-params.json`.
  `viz/test/fixtures/fallback-grid.json` is a dump of `FallbackGrid()`.
  Refresh it with `uv run python -c "import dataclasses, json; from flagstones import FallbackGrid; print(json.dumps(dataclasses.asdict(FallbackGrid()), indent=1, sort_keys=True))" > viz/test/fixtures/fallback-grid.json`.
- `scripts/`: maintenance scripts such as `regenerate_golden.py`.

## Commands

```
$ uv sync --extra osm --extra dev
$ uv run pytest -q
$ uv run ruff check .
$ uv run black --check .
$ uv run isort --check-only .
```

## Core Import Rule

`import flagstones` must succeed with the core dependencies only
(numpy, pandas, shapely, pyproj, networkx). Nothing that needs `osmnx` or
`geopandas` sits at the top level of any module the package imports eagerly.
The `core-import` CI job checks that neither module appears in `sys.modules`
after the import.

## Compatibility Promise

The top-level names, and the names listed in each public submodule's
`__all__` (for example `flagstones.graph`,
`flagstones.pipeline`, `flagstones.osm`), keep their
signatures and meaning across every 0.1.x release. A change to one of them is
a 0.2 release with a `CHANGELOG.md` entry. A new public name goes into its
module's `__all__`. Any name that is not top-level is imported by submodule
path, for example
`from flagstones.pipeline import TILE_ALGORITHM_VERSION`.

## Windows Rule

The library, the command line and the viz tool run on Windows. The
`locked-windows` and `viz (windows-latest)` CI legs check it.

- A bundle name passes `_slug` (Python) and `isValidSlug` (JavaScript) only
  when Windows can store it as a file, whatever platform the export runs on.
  The guards refuse the same set.
- Text written to disk names its `encoding`, and `newline="\n"` where the
  bytes are compared across platforms.
- Node scripts start other Node tools through `process.execPath`, because
  `spawn` cannot start a `.cmd` shim such as `npx` without a shell.

## Golden Rule

The golden is sensitive to numpy 1 against 2 and to the shapely version. A
golden failure on a CI leg is reported and never absorbed by a looser
tolerance. The dev environment pins shapely (2.1.2), numpy (2.5.1), pyproj
(3.7.2), pandas (3.0.5), geopandas (1.1.4) and networkx (3.6.1) in
`[tool.uv] constraint-dependencies`, and `.python-version` pins Python 3.14.8,
so local goldens compare byte for byte.

Any change that moves `tests/fixtures/*.golden.json` is a methodology change
and needs Cy. Bump `TILE_ALGORITHM_VERSION` in `pipeline.py`, run
`scripts/regenerate_golden.py`, and add its digests to
`tests/fixtures/algorithm-versions.json` under the new version. Package semver
tracks the API only.

Fallback tiles (`fallback.py`) never alter an intersection tile. A change that
moves `tests/fixtures/fallback.golden.json` follows the same version rule as
the other goldens.
