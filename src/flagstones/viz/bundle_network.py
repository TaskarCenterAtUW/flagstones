"""Node and edge rows written into a viz bundle."""

from __future__ import annotations

from typing import Any

from flagstones.graph import _one_way, _tag_values
from flagstones.viz.bundle_clips import _ring


def _listify(value: Any) -> list[str]:
    return [str(v) for v in _tag_values(value)]


def _highway(value: Any) -> str | list[str] | None:
    """The tag passes through in osmnx's stored shape, a lone value or a
    list.

    Simplification produces a list where merged ways disagree, and the leg
    rule has to read both forms.
    """
    values = _listify(value)
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def graph_nodes(projected, lon_lat) -> list[dict]:
    rows = []
    for node_id, d in projected.nodes(data=True):
        ll = lon_lat.nodes[node_id]
        rows.append(
            {
                "id": int(node_id),
                "lon": round(float(ll["x"]), 7),
                "lat": round(float(ll["y"]), 7),
                "x": float(d["x"]),
                "y": float(d["y"]),
                "sc": int(d.get("street_count") or 0),
            }
        )
    rows.sort(key=lambda r: r["id"])
    return rows


def graph_edges(lon_lat) -> list[dict]:
    rows = []
    for u, v, k, d in lon_lat.edges(keys=True, data=True):
        geom = d.get("geometry")
        if geom is None:
            coords = [
                (lon_lat.nodes[u]["x"], lon_lat.nodes[u]["y"]),
                (lon_lat.nodes[v]["x"], lon_lat.nodes[v]["y"]),
            ]
        else:
            coords = list(geom.coords)
        oneway = _one_way(d)
        rows.append(
            {
                "u": int(u),
                "v": int(v),
                "k": int(k),
                "oneway": oneway,
                "name": _listify(d.get("name")),
                "ref": _listify(d.get("ref")),
                "highway": _highway(d.get("highway")),
                "geom": _ring(coords, 7),
            }
        )
    rows.sort(key=lambda r: (r["u"], r["v"], r["k"]))
    return rows
