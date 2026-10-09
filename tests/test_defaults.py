"""Pin the defaults every tiling step declares.

The table pairs each defaulted argument of a tiling step with the
`TileParams` field whose value it must equal, and the second test
rejects a defaulted argument the table skips. `NOT_TILE_PARAMS` exempts
the defaulted arguments that mirror no field. The viz fixtures below
must also match their dataclass dumps key for key."""

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from flagstones import FallbackGrid
from flagstones.consolidate import clusters_from_graph
from flagstones.params import TileParams
from flagstones.tessellate import tessellate
from flagstones.transitions import classify_transitions

STEP_DEFAULTS = [
    (clusters_from_graph, "min_degree", "degree_filter_min"),
    (clusters_from_graph, "contract_transitions", "contract_transitions"),
    (
        clusters_from_graph,
        "transition_require_same_road",
        "transition_require_same_road",
    ),
    (clusters_from_graph, "transition_spread_max_deg", "transition_spread_max_deg"),
    (clusters_from_graph, "transition_opposite_min_deg", "transition_opposite_min_deg"),
    (classify_transitions, "require_same_road", "transition_require_same_road"),
    (classify_transitions, "spread_max_deg", "transition_spread_max_deg"),
    (classify_transitions, "opposite_min_deg", "transition_opposite_min_deg"),
    (tessellate, "snap_centroid", "snap_centroid"),
]
NOT_TILE_PARAMS = {"junction_degree", "_table"}


@pytest.mark.parametrize(("function", "argument", "field"), STEP_DEFAULTS)
def test_each_step_default_is_the_tile_params_default(function, argument, field):
    assert inspect.signature(function).parameters[argument].default == getattr(
        TileParams(), field
    )


@pytest.mark.parametrize(
    "function", [clusters_from_graph, classify_transitions, tessellate]
)
def test_no_step_default_sits_outside_the_table(function):
    listed = {a for f, a, _ in STEP_DEFAULTS if f is function}
    defaulted = {
        name
        for name, p in inspect.signature(function).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    assert defaulted - NOT_TILE_PARAMS == listed


VIZ_DEFAULTS = (
    Path(__file__).parents[1] / "viz" / "test" / "fixtures" / "tile-params.json"
)


@pytest.mark.skipif(not VIZ_DEFAULTS.exists(), reason="viz/ is not in an sdist")
def test_the_viz_defaults_fixture_is_tile_params():
    assert json.loads(VIZ_DEFAULTS.read_text()) == dataclasses.asdict(TileParams())


VIZ_FALLBACK = (
    Path(__file__).parents[1] / "viz" / "test" / "fixtures" / "fallback-grid.json"
)


@pytest.mark.skipif(not VIZ_FALLBACK.exists(), reason="viz/ is not in an sdist")
def test_the_viz_fallback_fixture_is_fallback_grid():
    assert json.loads(VIZ_FALLBACK.read_text()) == dataclasses.asdict(FallbackGrid())
