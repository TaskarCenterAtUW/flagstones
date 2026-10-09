import { crsKey, forward, registerCrs } from "./proj.js";

function leg(adj, a, b) {
  let m = adj.get(a);
  if (!m) { m = new Map(); adj.set(a, m); }
  let l = m.get(b);
  if (!l) { l = { out: false, in: false, edges: [] }; m.set(b, l); }
  return l;
}

export function buildGraph(bundle) {
  if (bundle.city.proj4) registerCrs(bundle.city.crs, bundle.city.proj4);
  const nodes = new Map();
  for (const n of bundle.nodes) {
    nodes.set(n.id, {
      id: n.id, lon: n.lon, lat: n.lat, lon0: n.lon, lat0: n.lat,
      xe: n.x, ye: n.y, x: n.x, y: n.y, sc: n.sc, moved: false,
    });
  }
  const adj = new Map();
  for (const id of nodes.keys()) adj.set(id, new Map());
  for (const e of bundle.edges) {
    if (e.u === e.v) { leg(adj, e.u, e.u).edges.push(e); continue; }
    const ab = leg(adj, e.u, e.v);
    ab.out = true; ab.edges.push(e);
    leg(adj, e.v, e.u).in = true;
  }
  // A move or a reset bumps `version`, so a cache that keys results on
  // it notices a change of position.
  return { epsg: bundle.city.crs, exportEpsg: bundle.city.crs, nodes, edges: bundle.edges, adj, version: 0 };
}

export function legs(graph, id) {
  return graph.adj.get(id) ?? new Map();
}

export function neighbors(graph, id) {
  return [...legs(graph, id).keys()].filter((n) => n !== id);
}

// All the edges joining the two nodes, whichever direction they run. A
// leg stores only edges starting at its node, so an arriving one-way
// edge appears on the neighbor's reverse leg.
export function legEdges(graph, id, nbr) {
  const mine = legs(graph, id).get(nbr)?.edges ?? [];
  if (nbr === id) return mine;
  return [...mine, ...(legs(graph, nbr).get(id)?.edges ?? [])];
}

function project(graph, node) {
  if (crsKey(graph.epsg) === crsKey(graph.exportEpsg) && !node.moved) {
    node.x = node.xe; node.y = node.ye;
  } else {
    [node.x, node.y] = forward(graph.epsg, node.lon, node.lat);
  }
}

export function setEngineCrs(graph, epsg) {
  graph.epsg = epsg;
  for (const node of graph.nodes.values()) project(graph, node);
}

export function moveNode(graph, id, lon, lat) {
  const node = graph.nodes.get(id);
  node.lon = lon; node.lat = lat;
  node.moved = lon !== node.lon0 || lat !== node.lat0;
  project(graph, node);
  graph.version += 1;
}

export function resetNode(graph, id) {
  const node = graph.nodes.get(id);
  node.lon = node.lon0; node.lat = node.lat0; node.moved = false;
  project(graph, node);
  graph.version += 1;
}

export function resetAll(graph) {
  for (const id of graph.nodes.keys()) resetNode(graph, id);
}

// osmnx computes an edge's length as the summed great-circle distance
// across its geometry vertices, on a sphere of 6,371,009 m.
const EARTH_RADIUS_M = 6371009;
function edgeLengthM(edge) {
  const g = edge.geom ?? [];
  let s = 0;
  for (let i = 1; i < g.length; i++) {
    const [lon1, lat1] = g[i - 1].map((v) => (v * Math.PI) / 180), [lon2, lat2] = g[i].map((v) => (v * Math.PI) / 180);
    const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2;
    s += 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
  }
  return s;
}

// A loop road leaves a node and comes back to it, worth two legs once its whole
// measures 100 m, the library's _LOOP_ROAD_MIN_M. Below that boundary the loop
// is just a turning circle closing a cul-de-sac, and it marks no leg.
const LOOP_ROAD_MIN_M = 100;
export function isLoopRoad(edge) {
  return edgeLengthM(edge) >= LOOP_ROAD_MIN_M;
}

// Replicates osmnx's street_count on this graph, counting the larger of the
// directed edges to each neighbor, two more for a loop road. The export's `sc`,
// osmnx's own figure, can differ. osmnx counted streets on its
// buffered graph, not on the one it truncated to the city polygon, so a
// junction hugging the limit from inside, its other legs outside, reports a 3
// with a single neighbor here. Osmnx likewise counts any loop twice however
// long, which hands a turning-circle street a 3 as well. By either route, the
// node ends a street.
export function streetCount(graph, id) {
  let n = 0, loop = false;
  for (const [nbr, l] of legs(graph, id)) {
    if (nbr === id) { loop = loop || l.edges.some(isLoopRoad); continue; }
    n += Math.max(l.edges.length, legs(graph, nbr).get(id)?.edges.length ?? 0);
  }
  return n + (loop ? 2 : 0);
}

export function deadEnds(graph) {
  return new Set([...graph.nodes.keys()].filter((id) => streetCount(graph, id) <= 1));
}

// osmnx keeps the way tags it folds into one merged edge, so an edge part ramp
// and part road still reads as a link at either end (this is why Eastlake gains
// no extra leg from the primary_link off NE 40th Street).
export function isLink(highway) {
  return (Array.isArray(highway) ? highway : [highway]).some((v) => v != null && String(v).endsWith("_link"));
}

export function touchesLink(graph, id) {
  for (const [nbr, l] of legs(graph, id)) {
    if (l.edges.some((e) => isLink(e.highway))) return true;
    if (nbr !== id && legs(graph, nbr).get(id)?.edges.some((e) => isLink(e.highway))) return true;
  }
  return false;
}

// Dead ends drop out the same way osmnx does with dead_ends off, so the graphs
// stay easy to compare. A node whose street_count, exported as `sc`, tops out
// at 1 goes out with its edges before clustering starts. osmnx tallied the
// counts on its untrimmed fetch, so a junction at the limit keeps its node,
// here as in osmnx.
export function withoutDeadEnds(graph) {
  const dead = new Set([...graph.nodes.values()].filter((n) => n.sc <= 1).map((n) => n.id));
  const keep = new Set([...graph.nodes.keys()].filter((id) => !dead.has(id)));
  const nodes = new Map([...graph.nodes].filter(([id]) => keep.has(id)));
  const adj = new Map();
  for (const [id, m] of graph.adj) {
    if (!keep.has(id)) continue;
    adj.set(id, new Map([...m].filter(([nbr]) => keep.has(nbr))));
  }
  const edges = graph.edges.filter((e) => keep.has(e.u) && keep.has(e.v));
  return { epsg: graph.epsg, exportEpsg: graph.exportEpsg, nodes, edges, adj, version: graph.version };
}
