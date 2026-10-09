import dataclasses
import math

import pytest

from flagstones.params import TileParams, validate_params


def test_the_defaults_are_the_published_design():
    assert dataclasses.asdict(TileParams()) == {
        "consolidation_tolerance_m": 17.0,
        "seed_grid_m": 24.0,
        "degree_filter_min": 3,
        "contract_transitions": True,
        "transition_require_same_road": True,
        "transition_spread_max_deg": 60.0,
        "transition_opposite_min_deg": 120.0,
        "clip_buffer_m": 250.0,
        "inheritance_radius_m": 34.0,
        "snap_centroid": True,
    }


def test_a_grid_over_the_collision_bound_is_refused():
    with pytest.raises(ValueError, match="collision bound"):
        TileParams(consolidation_tolerance_m=17.0, seed_grid_m=25.0)


def test_grid_must_stay_below_the_collision_bound():
    """A cell may not be able to seat two distinct clusters. osmnx reads
    the tolerance as a node's buffer radius, so merging spans twice
    it. Within a cell, points lie at most `seed_grid_m` * sqrt(2)
    apart. At or above tolerance * sqrt(2), two distinct clusters could
    share a cell out of merge reach and mint one ID for two tiles.
    """
    with pytest.raises(ValueError, match="collision bound"):
        validate_params(consolidation_tolerance_m=5.0, seed_grid_m=10.0)
    validate_params(consolidation_tolerance_m=5.0, seed_grid_m=5.0)


def test_validate_params_rejects_the_bound_value_itself():
    """The comparison fires on >=, so a grid reaching exactly
    tolerance * sqrt(2) draws the refusal.
    """
    bound = 5.0 * math.sqrt(2)
    with pytest.raises(ValueError, match="collision bound"):
        validate_params(consolidation_tolerance_m=5.0, seed_grid_m=bound)


def test_validate_params_rejects_a_non_positive_value():
    with pytest.raises(ValueError, match="positive"):
        validate_params(consolidation_tolerance_m=0.0, seed_grid_m=5.0)
    with pytest.raises(ValueError, match="positive"):
        validate_params(consolidation_tolerance_m=5.0, seed_grid_m=-1.0)


@pytest.mark.parametrize("field", ["degree_filter_min", "clip_buffer_m"])
def test_non_positive_counts_and_distances_are_refused(field):
    with pytest.raises(ValueError, match=field):
        TileParams(**{field: 0})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_degree_filter_min_is_refused(value):
    """NaN and -inf refuse fast at the range test, because `x >= 1`
    answers false for both. Infinity passes that test and falls to the
    finiteness check. A hand-written TOML file can contain any of these
    spellings as plain values, so the refusal quotes the field on the
    way out.
    """
    with pytest.raises(ValueError, match="degree_filter_min"):
        TileParams(degree_filter_min=value)


def test_an_infinite_degree_filter_min_gets_the_finite_refusal():
    # NaN and -inf exit at the range check, so infinity alone reaches
    # the finiteness message. The CLI shows this sentence to users,
    # so the match pins it whole.
    with pytest.raises(ValueError, match="degree_filter_min must be finite, got inf"):
        TileParams(degree_filter_min=math.inf)


@pytest.mark.parametrize(
    "field",
    [
        "consolidation_tolerance_m",
        "seed_grid_m",
        "clip_buffer_m",
        "inheritance_radius_m",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_distance_is_refused(field, value):
    """Infinity sails past every minimum, and a comparison to NaN
    always fails, so range rules alone miss both. Distance fields
    need `math.isfinite` alongside their ranges.
    """
    with pytest.raises(ValueError, match=f"{field}.*finite"):
        TileParams(**{field: value})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_transition_angle_is_refused(value):
    with pytest.raises(ValueError, match="finite"):
        TileParams(transition_spread_max_deg=value)
    with pytest.raises(ValueError, match="finite"):
        TileParams(transition_opposite_min_deg=value)


@pytest.mark.parametrize(
    "table",
    [
        {"snap_centroid": "false"},
        {"contract_transitions": "no"},
        {"transition_require_same_road": None},
        {"degree_filter_min": True},
        {"degree_filter_min": 2.5},
    ],
)
def test_a_mistyped_switch_or_degree_is_refused(table):
    """TOML can hand over "false" as a string, which Python reads as
    truthy. Coercing that would build with a switch on while its
    recorded parameter claims off, and the run would stop matching
    its own inputs. Types pass strict, or construction refuses.
    """
    with pytest.raises(ValueError, match=next(iter(table))):
        TileParams.from_mapping(table)


def test_a_negative_inheritance_radius_is_refused():
    with pytest.raises(ValueError, match="inheritance_radius_m"):
        TileParams(inheritance_radius_m=-1.0)


def test_from_mapping_reads_a_tiles_table_with_extra_keys():
    table = {
        "consolidation_tolerance_m": 17,
        "seed_grid_m": 24,
        "degree_filter_min": 3,
        "seed_exclude_highway": ["service", "track"],
        "contract_transitions": True,
        "transition_require_same_road": True,
        "transition_spread_max_deg": 60,
        "transition_opposite_min_deg": 120,
        "inheritance_radius_m": 34.0,
        "clip_buffer_m": 250.0,
    }
    assert (
        TileParams.from_mapping(table, ignore={"seed_exclude_highway"}) == TileParams()
    )


def test_from_mapping_refuses_an_unknown_key():
    with pytest.raises(ValueError, match="seed_exclude_highway"):
        TileParams.from_mapping({"seed_exclude_highway": []})


@pytest.mark.parametrize(
    "field", ["transition_spread_max_deg", "transition_opposite_min_deg"]
)
@pytest.mark.parametrize("value", [-400.0, -1.0, 180.5, 400.0])
def test_a_transition_angle_outside_a_half_turn_is_refused(field, value):
    with pytest.raises(ValueError, match=field):
        TileParams(**{field: value})


@pytest.mark.parametrize(
    "field", ["transition_spread_max_deg", "transition_opposite_min_deg"]
)
@pytest.mark.parametrize("value", [0.0, 180.0])
def test_a_transition_angle_at_either_end_of_a_half_turn_is_accepted(field, value):
    assert getattr(TileParams(**{field: value}), field) == value


@pytest.mark.parametrize("field", ["degree_filter_min", "clip_buffer_m"])
def test_an_integer_past_the_float_range_is_refused_by_name(field):
    with pytest.raises(ValueError, match=field):
        TileParams(**{field: 10**400})
