"""CRS guards, the CRS part of tile IDs, and the picker that maps a
lon/lat point to its UTM zone."""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "MAX_LOCAL_ANISOTROPY",
    "MAX_LOCAL_SCALE",
    "canonical_crs",
    "crs_authority_string",
    "crs_id_token",
    "crs_of",
    "local_anisotropy",
    "local_scale",
    "require_equal_area_crs",
    "require_metric_crs",
    "utm_crs_for",
]


def crs_id_token(value: int | str) -> str:
    """Return the CRS part of a tile ID.

    Under the EPSG authority the function keeps the bare code, so `5070`
    and `EPSG:5070` both give `5070`. Any other authority joins its
    lowercased name to the code instead, and `ESRI:54034` gives
    `esri54034`.
    """
    authority, sep, code = str(value).partition(":")
    if not sep:
        return authority
    if authority.upper() == "EPSG":
        return code
    return f"{authority.lower()}{code}"


def crs_authority_string(value: int | str) -> str:
    """Return `value` in the `<authority>:<code>` form the `seed_crs`
    column records.

    The function reads an int as an EPSG code and hands back a string
    whose authority already appears in it.
    """
    return f"EPSG:{value}" if isinstance(value, int) else str(value)


def require_metric_crs(value: int | str) -> None:
    """Refuse a CRS whose axes are not projected meters.

    To the tiling, `grid_m`, `tolerance_m` and the Voronoi bisectors all
    mean units as the CRS itself declares them, so EPSG:4326 takes the
    grid as degrees while a feet-based CRS takes the same numbers as
    feet, which leaves every distance off by its conversion factor.
    `value` may be an EPSG code, as an int or a string, or any
    authority-bearing string `CRS.from_user_input` resolves
    (`ESRI:54034`, say). Raises `ValueError`.
    """
    from pyproj import CRS

    crs = CRS.from_user_input(value)
    if not crs.is_projected:
        raise ValueError(
            f"crs={value} ({crs.name}) is not a projected CRS, so grid_m "
            "would read in the CRS's own units instead of meters. Pass a "
            "projected CRS whose axes measure meters, such as "
            "utm_crs_for(lon, lat)."
        )
    units = {axis.unit_name for axis in crs.axis_info}
    # The unit names come straight from PROJ's axis metadata, so the
    # set below keeps PROJ's spelling of the unit.
    if units != {"metre"}:
        raise ValueError(
            f"crs={value} ({crs.name}) is projected, but its axes measure "
            f"{sorted(units)} instead of meters, so grid_m and every "
            "distance parameter would be off by that unit's conversion "
            "factor."
        )


# Equal-area methods whose PROJ name leaves out the phrase "equal
# area". PROJ still applies them to the ellipsoid, so on WGS84 their
# areas come out true too.
_EQUAL_AREA_METHODS = frozenset({"bonne", "equal earth", "sinusoidal"})


def require_equal_area_crs(value: int | str) -> None:
    """Refuse anything that is not a projected, metric and equal-area
    CRS.

    Publishers of tile areas need this check beyond `require_metric_crs`,
    because inside its own zone a UTM CRS reads `grid_m` correctly while
    its local scale distortion still skews published areas. The check
    asks pyproj for the coordinate-operation method name and consults no
    fixed list of EPSG codes. Among the methods that pass on a name
    containing "Equal Area" are Albers, Lambert azimuthal, and
    cylindrical entries such as `ESRI:54034`; the odd-named ones in
    `_EQUAL_AREA_METHODS` pass as well. Raises `ValueError`.
    """
    from pyproj import CRS

    require_metric_crs(value)
    operation = CRS.from_user_input(value).coordinate_operation
    method = operation.method_name if operation is not None else ""
    if "equal area" not in method.lower() and method.lower() not in _EQUAL_AREA_METHODS:
        raise ValueError(
            f"crs={value} is projected and metric, but its projection "
            f"method {method or 'unrecognized'!r} is not an equal-area "
            "method, so grid_m would read correctly while tile areas "
            "differ by the projection's local scale distortion. Pass an "
            "equal-area CRS."
        )


def utm_crs_for(lon: float, lat: float) -> str:
    """Return the WGS84 UTM zone covering a lon/lat point, as an EPSG
    code.

    Zones north of the equator use `EPSG:326zz` and zones south use
    `EPSG:327zz`. The arithmetic applies no Norway or Svalbard
    exceptions. UTM is conformal, so a meter parameter means the same
    length in every direction at the point, though the zone is not
    equal-area, and a caller publishing `area_m2` would do better with
    an equal-area CRS. Raises `ValueError` outside longitude -180 to 180
    or latitude -80 to 84.
    """
    if not (-180.0 <= lon <= 180.0 and -80.0 <= lat <= 84.0):
        raise ValueError(
            f"({lon}, {lat}) sits past the UTM limits, longitude -180 to "
            "180 and latitude -80 to 84"
        )
    zone = min(int((lon + 180.0) // 6.0) + 1, 60)
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def _is_geographic(graph: Any) -> bool:
    """Return whether the graph sits on a geographic CRS.

    A graph that names no CRS reads False here, while `crs_of` refuses
    the same graph with a `ValueError`.
    """
    from pyproj import CRS

    raw = graph.graph.get("crs")
    return raw is not None and CRS.from_user_input(raw).is_geographic


def canonical_crs(value: Any) -> str:
    """Express a CRS as the `AUTHORITY:CODE` string that goes into tile
    IDs.

    Any spelling of a CRS resolves to the same string. `5070`,
    `epsg:5070`, its URN, and aliases pyproj maps to it such as
    `ESRI:102039` all resolve to `EPSG:5070`, and a CRS with no EPSG
    identity resolves under its own authority, such as `ESRI:54034`.
    A geographic CRS raises `ValueError`, because it would turn every
    meter parameter into degrees, and so does a projected CRS that
    exposes no authority code, whose tile ID would then record nothing.
    """
    from pyproj import CRS

    crs = CRS.from_user_input(value)
    if crs.is_geographic:
        raise ValueError(
            f"crs {crs.to_string()} is geographic, so the tiling would "
            "read its meter parameters as degrees. Project the graph "
            "before consolidating"
        )
    epsg = crs.to_epsg()
    if epsg is not None:
        return f"EPSG:{epsg}"
    authority = crs.to_authority()
    if authority is None:
        raise ValueError(
            f"crs {crs.to_string()} exposes no authority code for a tile ID"
        )
    return f"{authority[0]}:{authority[1]}"


def crs_of(graph: Any) -> str:
    """Return the graph's CRS as the `AUTHORITY:CODE` string that
    `canonical_crs` produces.

    `tessellate` and `mint_tile_native_id` embed the result. The graph's
    CRS may arrive spelled any way `canonical_crs` accepts. Raises
    `ValueError` for a graph that names no CRS, and for any CRS
    `canonical_crs` refuses.
    """
    raw = graph.graph.get("crs")
    if raw is None:
        raise ValueError("graph has no crs, so project it before tiling")
    return canonical_crs(raw)


# tolerance_m and grid_m mean meters to the Voronoi work, and it treats
# every bisector as a metric perpendicular, so the CRS must run near
# conformal where the tiles build. The ratio of the Tissot semi-axes
# measures the departure at a point, 1.00 for a conformal projection.
# Albers over CONUS reaches 1.028 at Key West, so this ceiling admits
# that CRS across its whole area while refusing a cylindrical equal-area
# projection except near its standard parallel. For Seattle, EPSG:6933
# reports 1.65, and on the equator it reads 1.33, an error like tiling
# in degrees.
MAX_LOCAL_ANISOTROPY = 1.03


def local_anisotropy(crs: Any, lon: float, lat: float) -> float:
    """For `crs` at a lon/lat point, divide the Tissot semi-major axis by
    the semi-minor and return the answer.

    A locally conformal projection gives 1.0, and the ratio grows with
    the stretch of one direction against the other. PROJ reads NaN at a
    point where the projection is undefined.
    """
    from pyproj import Proj

    factors = Proj(crs).get_factors(lon, lat)
    return factors.tissot_semimajor / factors.tissot_semiminor


# The tiling takes every distance parameter at the CRS's own meter
# value, so the CRS has to sit near true scale wherever tiles build.
# The scale at a point is the Tissot areal scale under a square root,
# and equal-area projections return 1.0 by construction. On a UTM
# central meridian the reading is 0.9996 at all latitudes, and 6 degrees
# away the worst case reaches 1.0051 on the equator (1.0024 at 45
# degrees north), which a centered graph clears easily. The reading
# crosses 1.01 about 11.7 degrees off the meridian on the 45th parallel,
# or 8.2 off it at the equator, so where tiling happens a whole zone
# fits under the limit plus margin. Both Washington state plane zones
# remain under 1.0010 across the state, while a zone read at the far end
# of a taller state fails (Texas north, 1.013 at Brownsville). At 46
# degrees north Web Mercator reports 1.44, and 60 degrees off its
# central meridian a UTM zone reads 1.26 at latitude 45.
MAX_LOCAL_SCALE = 1.01


def local_scale(crs: Any, lon: float, lat: float) -> float:
    """Return `crs`'s local linear scale at a lon/lat point, its Tissot
    areal scale under a square root.

    A reading of 1.0 means a meter in the CRS matches a meter on the
    ground averaged over directions, and an equal-area projection reads
    1.0 everywhere.
    """
    from pyproj import Proj

    factors = Proj(crs).get_factors(lon, lat)
    return math.sqrt(factors.areal_scale)
