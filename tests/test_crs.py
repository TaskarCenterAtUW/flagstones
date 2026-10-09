import osmnx as ox
import pytest

from flagstones.crs import (
    MAX_LOCAL_ANISOTROPY,
    MAX_LOCAL_SCALE,
    canonical_crs,
    crs_authority_string,
    crs_id_token,
    crs_of,
    local_anisotropy,
    local_scale,
    require_equal_area_crs,
    require_metric_crs,
    utm_crs_for,
)
from tests.graphs import _divided_road_fixture, _grid_fixture, _one_edge_graph


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (-122.3382, 47.6097, "EPSG:32610"),  # Seattle
        (-117.18, 46.73, "EPSG:32611"),  # Pullman
        (-78.50, -0.22, "EPSG:32717"),  # Quito, south of the equator
        (36.82, -1.29, "EPSG:32737"),  # Nairobi
        (13.40, 52.52, "EPSG:32633"),  # Berlin
        (180.0, 10.0, "EPSG:32660"),  # the antimeridian keeps zone 60
    ],
)
def test_utm_crs_for_picks_the_zone_and_hemisphere(lon, lat, expected):
    assert utm_crs_for(lon, lat) == expected


@pytest.mark.parametrize(("lon", "lat"), [(-181.0, 0.0), (0.0, 85.0), (0.0, -81.0)])
def test_utm_crs_for_refuses_points_outside_utm(lon, lat):
    with pytest.raises(ValueError, match="UTM"):
        utm_crs_for(lon, lat)


def test_a_utm_zone_is_metric_but_not_equal_area():
    require_metric_crs("EPSG:32610")
    with pytest.raises(ValueError, match="equal-area"):
        require_equal_area_crs("EPSG:32610")


def test_albers_passes_both_checks():
    require_metric_crs(5070)
    require_equal_area_crs(5070)


def test_a_geographic_crs_is_not_metric():
    with pytest.raises(ValueError, match="projected"):
        require_metric_crs(4326)


def test_a_crs_in_feet_is_not_metric():
    with pytest.raises(ValueError, match="instead of meters"):
        require_metric_crs(2926)  # NAD83(HARN) / Washington North (ftUS)


def test_crs_id_token_keeps_epsg_bare_and_prefixes_other_authorities():
    assert crs_id_token("EPSG:5070") == "5070"
    assert crs_id_token(5070) == "5070"
    assert crs_id_token("ESRI:54034") == "esri54034"


@pytest.mark.parametrize(
    ("crs", "token", "authority"),
    [
        ("EPSG:5070", "5070", "EPSG:5070"),
        ("EPSG:3035", "3035", "EPSG:3035"),
        ("EPSG:10592", "10592", "EPSG:10592"),
        ("ESRI:54034", "esri54034", "ESRI:54034"),
    ],
)
def test_each_regional_equal_area_crs_tokens_and_passes_the_equal_area_check(
    crs, token, authority
):
    """Spread the equal-area check over regional CRSs, one of them
    keyed to the ESRI authority instead of EPSG."""
    assert crs_id_token(crs) == token
    assert crs_authority_string(crs) == authority
    require_equal_area_crs(crs)


def test_crs_of_resolves_a_non_epsg_authority():
    """Project a graph into ESRI:54034 (World Cylindrical Equal Area), a
    projection with no EPSG code behind it whatsoever.

    `crs_of` answers with the authority-bearing string, matching the
    form its other returns take."""
    graph = _one_edge_graph(crs="EPSG:5070", x=-1965840.78, y=3007788.16)
    reprojected = ox.project_graph(graph, to_crs="ESRI:54034")
    assert crs_of(reprojected) == "ESRI:54034"


def test_crs_of_refuses_a_projected_crs_with_no_authority():
    """Feed `crs_of` a bare PROJ definition: projected and metric, yet
    stripped of any naming authority.

    Nothing remains to record in the tile ID or in `seed_crs`, so the
    call must refuse."""
    graph = _one_edge_graph(crs="EPSG:5070", x=0.0, y=0.0)
    graph.graph["crs"] = (
        "+proj=laea +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
    with pytest.raises(ValueError, match="no authority"):
        crs_of(graph)


def test_local_anisotropy_reads_tissot_semi_axes():
    """Read Tissot's semi-axes through `local_anisotropy`. Across CONUS,
    Albers keeps its indicatrix within 3% of conformal, 2.8% at Key
    West and 1.4% at Seattle, a margin `MAX_LOCAL_ANISOTROPY` admits
    at 1.03.

    A UTM zone reads exactly 1.00, conformal, while at Seattle's
    latitude the cylindrical default reaches 1.65, error of the size a
    Voronoi built on degrees would make."""
    assert local_anisotropy("EPSG:5070", -122.33, 47.6) == pytest.approx(
        1.0136, abs=1e-3
    )
    assert local_anisotropy("EPSG:5070", -81.8, 24.55) == pytest.approx(
        1.0276, abs=1e-3
    )
    assert local_anisotropy("EPSG:32610", -122.33, 47.6) == pytest.approx(1.0, abs=1e-4)
    assert local_anisotropy("EPSG:6933", -122.33, 47.6) == pytest.approx(
        1.646, abs=1e-2
    )
    assert MAX_LOCAL_ANISOTROPY == 1.03


def test_local_scale_reads_the_square_root_of_the_areal_scale():
    """Read the square root of the areal scale. An equal-area projection
    returns 1.0 by construction, a UTM central meridian comes back at
    0.9996, and Web Mercator reproduces the secant of the latitude."""
    assert local_scale("EPSG:5070", -96.0, 37.0) == pytest.approx(1.0, abs=1e-9)
    assert local_scale("EPSG:32610", -123.0, 46.0) == pytest.approx(0.9996, abs=1e-6)
    assert local_scale("EPSG:3857", -120.31, 46.38) == pytest.approx(1.4495, abs=1e-3)
    assert MAX_LOCAL_SCALE == 1.01


def test_albers_reads_true_scale_across_conus():
    """At every grid point from lon -125 to -70 by 5 and lat 24 to 48 by
    3, Albers returns `local_scale` 1.0, the equal-area promise over the
    whole CONUS sweep."""
    for lon in range(-125, -65, 5):
        for lat in range(24, 51, 3):
            assert local_scale("EPSG:5070", lon, lat) == pytest.approx(1.0, abs=1e-9)


def test_the_gate_accepts_the_esri_authority():
    require_equal_area_crs("ESRI:54034")


@pytest.mark.parametrize("crs", ["EPSG:8857", "ESRI:54008", "ESRI:54024"])
def test_the_gate_accepts_equal_area_methods_named_otherwise(crs):
    """Accept Bonne, Sinusoidal and Equal Earth, all equal-area on the
    ellipsoid even where the method name omits those words."""
    require_equal_area_crs(crs)


@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:4326", "ESRI:54026"])
def test_the_gate_still_refuses_crs_without_equal_area_properties(crs):
    with pytest.raises(ValueError):
        require_equal_area_crs(crs)


def test_crs_of_returns_the_graphs_real_code():
    """Read fixtures in different CRSs back under their own codes, so
    an implementation that hardcoded an answer fails."""
    assert crs_of(_grid_fixture()) == "EPSG:5070"
    assert crs_of(_divided_road_fixture()) == "EPSG:32610"


@pytest.mark.parametrize(
    ("crs", "reason"),
    [
        # Degrees. Fed to grid_m, 5.0 would span five degrees of ground,
        # and area_m2 would mean no area at all.
        pytest.param(4326, "not a projected CRS", id="geographic"),
        # In US survey feet over NY Long Island, the CRS still projects
        # and the geographic check waves it through. What skews grid_m
        # and area_m2 is the feet-to-meters factor alone.
        pytest.param(2263, "instead of meters", id="feet"),
        # UTM zone 11N, projected, metric, yet conformal still: area_m2
        # would inherit the local scale warp UTM imposes.
        pytest.param(32611, "not an equal-area method", id="conformal"),
    ],
)
def test_require_equal_area_crs_refuses_a_crs_that_is_not_metric_equal_area(
    crs, reason
):
    """Trip each CRS on a different check and match the message to it.

    Remove a check and its own row fails there, instead of falling
    through to the next surviving check."""
    with pytest.raises(ValueError, match=reason):
        require_equal_area_crs(crs)


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("EPSG:5070", "EPSG:5070"),
        ("epsg:32610", "EPSG:32610"),
        (32610, "EPSG:32610"),
        ("ESRI:102039", "EPSG:5070"),
        ("urn:ogc:def:crs:EPSG::32610", "EPSG:32610"),
        ("ESRI:54034", "ESRI:54034"),
    ],
)
def test_canonical_crs_names_a_crs_the_way_crs_of_does(value, label):
    """Mint the same canonical label however a caller spells a CRS, since tile
    IDs bury the CRS inside them. Alias, lowercase authority, plain int
    and bare URN must all resolve onto the same token."""
    assert canonical_crs(value) == label


def test_canonical_crs_refuses_what_crs_of_refuses():
    with pytest.raises(ValueError, match="geographic"):
        canonical_crs("EPSG:4326")
    with pytest.raises(ValueError, match="no authority"):
        canonical_crs("+proj=laea +lat_0=0 +lon_0=0 +datum=WGS84 +units=m")
