import networkx as nx
import pytest

from flagstones.errors import NonDrivableGraphError
from flagstones.graph import (
    SEED_EXCLUDE_ACCESS,
    SEED_EXCLUDE_HIGHWAY,
    dead_end_nodes,
    require_drivable_graph,
)
from tests.graphs import _grid_fixture, _road_and_path_fixture, _self_loop_fixture

EXCLUDED_HIGHWAY = SEED_EXCLUDE_HIGHWAY[:-2]


def test_the_seed_rule_is_the_drivable_rule_plus_service_and_track():
    assert SEED_EXCLUDE_HIGHWAY[-2:] == ("service", "track")
    assert len(set(SEED_EXCLUDE_HIGHWAY)) == len(SEED_EXCLUDE_HIGHWAY) == 14
    assert SEED_EXCLUDE_ACCESS == ("no", "private")


def test_dead_end_nodes_are_judged_on_the_graph():
    """The count walks this graph's edge table instead of trusting the
    `street_count` a fetch stamped on each node. A loop way measuring
    100 m or more adds two to its host's count, a shorter loop adds
    nothing, and at the stub's end loop length alone flips node 2's
    verdict.
    """
    assert dead_end_nodes(_self_loop_fixture(short=True)) == [1, 2]
    assert dead_end_nodes(_self_loop_fixture()) == [1]
    assert dead_end_nodes(_self_loop_fixture(through=True)) == [1, 3]


def test_a_graph_with_a_footway_edge_is_refused():
    with pytest.raises(NonDrivableGraphError, match="footway"):
        require_drivable_graph(
            _road_and_path_fixture(), exclude_highway=EXCLUDED_HIGHWAY
        )


def test_a_drivable_graph_passes():
    require_drivable_graph(
        _road_and_path_fixture("residential"), exclude_highway=EXCLUDED_HIGHWAY
    )


def test_a_list_valued_highway_is_read_through():
    """For ways merged into one edge, osmnx stores the tags as a list.
    An excluded value buried among legal road values on the same edge
    must veto the graph all the same.
    """
    graph = _road_and_path_fixture(["residential", "cycleway"])
    with pytest.raises(NonDrivableGraphError, match="cycleway"):
        require_drivable_graph(graph, exclude_highway=EXCLUDED_HIGHWAY)


def test_a_set_valued_highway_cites_the_least_tag_deterministically():
    """Python varies a `set`'s member order from process to process, so
    the error quotes the lowest excluded tag instead of whichever hit
    comes first. Raising twice and comparing the messages pins that rule.
    """
    graph = nx.MultiGraph()
    graph.add_edge(1, 2, highway={"track", "service"})
    with pytest.raises(NonDrivableGraphError) as first:
        require_drivable_graph(graph, exclude_highway=SEED_EXCLUDE_HIGHWAY)
    with pytest.raises(NonDrivableGraphError) as second:
        require_drivable_graph(graph, exclude_highway=SEED_EXCLUDE_HIGHWAY)
    assert str(first.value) == str(second.value)
    assert "highway='service'" in str(first.value)
    assert "track" not in str(first.value).split("sample:")[1]


def test_an_edge_with_no_highway_tag_is_no_evidence():
    """Hand-built fixtures such as `_grid_fixture` have no `highway`
    tags at all. The veto reads tags that exist, so tag-less graphs
    stay usable as input throughout this module.
    """
    require_drivable_graph(_grid_fixture(), exclude_highway=EXCLUDED_HIGHWAY)


def test_a_missing_name_read_as_nan_names_no_road():
    """Tables read an absent tag back as NaN, and `_road_names` strips
    those values. Left in, the unnamed edges would all land on one road
    spelled "nan".
    """
    from flagstones.graph import _road_names

    assert _road_names({"name": float("nan"), "ref": [None, "SR 22"]}) == {"SR 22"}
