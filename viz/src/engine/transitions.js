import { legEdges, legs, neighbors } from "./graph.js";
import { forward } from "./proj.js";

export function roadNames(edges) {
  const out = new Set();
  for (const e of edges) {
    for (const key of ["name", "ref"]) {
      const v = e[key];
      if (v == null) continue;
      if (Array.isArray(v)) for (const s of v) out.add(String(s));
      else out.add(String(v));
    }
  }
  return out;
}

// The bearing measures out to the first vertex 5 m or farther down the leg, so
// a tiny first segment bends nothing. The library's `_bearing_away` works off
// its projected graph, so keeping to the same CRS pins both classifiers to the same
// truth at the spread limit. Bearings from scaled lon/lat put some nodes on
// the other side of it.
export function legBearingDeg(graph, edge, fromId) {
  const node = graph.nodes.get(fromId);
  const coords = edge.u === fromId ? edge.geom : [...edge.geom].reverse();
  let target = [node.x, node.y];
  for (let i = 1; i < coords.length; i++) {
    target = forward(graph.epsg, coords[i][0], coords[i][1]);
    if (Math.hypot(target[0] - node.x, target[1] - node.y) >= 5) break;
  }
  return ((Math.atan2(target[1] - node.y, target[0] - node.x) * 180) / Math.PI + 360) % 360;
}

export function angleDiff(a, b) {
  const d = Math.abs(a - b) % 360;
  return Math.min(d, 360 - d);
}

function legInfo(graph, id, nbrId) {
  const mine = legs(graph, id).get(nbrId);
  const edges = legEdges(graph, id, nbrId);
  const out = !!mine?.out, inn = !!mine?.in;
  const edge = edges[0];
  return { nbrId, out, in: inn, twoWay: out && inn, names: roadNames(edges), bearing: legBearingDeg(graph, edge, id) };
}

// A node's transition in the shape classifyTransitions returns it,
// or null when the node offers none.
export function classifyNode(graph, id, { requireSameRoad = true, spreadMaxDeg = 60, oppositeMinDeg = 120 } = {}) {
  const nbrs = neighbors(graph, id);
  if (nbrs.length !== 3) return null;
  const info = nbrs.map((n) => legInfo(graph, id, n));
  const twoWay = info.filter((l) => l.twoWay);
  const outs = info.filter((l) => l.out && !l.in);
  const ins = info.filter((l) => l.in && !l.out);
  let kind, pair, single;
  if (twoWay.length === 1 && outs.length === 1 && ins.length === 1) {
    kind = "join"; pair = [ins[0], outs[0]]; single = twoWay[0];
  } else if (twoWay.length === 0 && outs.length === 2 && ins.length === 1) {
    kind = "fork"; pair = outs; single = ins[0];
  } else if (twoWay.length === 0 && ins.length === 2 && outs.length === 1) {
    kind = "fork"; pair = ins; single = outs[0];
  } else {
    return null;
  }
  let common = new Set(info[0].names);
  for (const l of info.slice(1)) common = new Set([...common].filter((n) => l.names.has(n)));
  if (requireSameRoad && common.size === 0) return null;
  const spread = angleDiff(pair[0].bearing, pair[1].bearing);
  const opposite = Math.min(angleDiff(single.bearing, pair[0].bearing), angleDiff(single.bearing, pair[1].bearing));
  if (spread > spreadMaxDeg || opposite < oppositeMinDeg) return null;
  return { kind, roads: [...common].sort(), spread, opposite };
}

export function classifyTransitions(graph, opts = {}) {
  const found = new Map();
  for (const id of graph.nodes.keys()) {
    const transition = classifyNode(graph, id, opts);
    if (transition) found.set(id, transition);
  }
  return found;
}
