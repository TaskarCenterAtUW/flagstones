import polygonClipping from "polygon-clipping";
import { neighbors, legs, legEdges, isLink, deadEnds, touchesLink, isLoopRoad } from "./graph.js";
import { roadNames, legBearingDeg, angleDiff } from "./transitions.js";
import { multiPolygonCentroid } from "./geometry.js";

// GEOS approximates a buffered point as a 64-gon (quad_segs=16). Corner
// k lies at k * 2*pi/64 from (x + r, y).
const CIRCLE_SEGMENTS = 64;
const SEGMENT_ANGLE = (2 * Math.PI) / CIRCLE_SEGMENTS;

export function bufferCircle(x, y, r) {
  const ring = [];
  for (let k = 0; k < CIRCLE_SEGMENTS; k++) {
    const a = k * SEGMENT_ANGLE;
    ring.push([x + r * Math.cos(a), y + r * Math.sin(a)]);
  }
  ring.push(ring[0]);
  return ring;
}

// How far the 64-gon's rim reaches from its center along direction theta.
function polygonRadius(r, theta) {
  const t = ((theta % SEGMENT_ANGLE) + SEGMENT_ANGLE) % SEGMENT_ANGLE;
  const psi = t - SEGMENT_ANGLE / 2;
  return (r * Math.cos(SEGMENT_ANGLE / 2)) / Math.cos(psi);
}

// Two equal 64-gons overlap once their centers come closer than the sum of
// their boundary distances along the line joining those centers. Because
// a half turn maps the 64-gon onto itself, a single polygonRadius call
// computes the reach at both centers.
export function buffersOverlap([ax, ay], [bx, by], r) {
  const dx = bx - ax, dy = by - ay;
  const d = Math.hypot(dx, dy);
  if (d === 0) return true;
  return d < 2 * polygonRadius(r, Math.atan2(dy, dx));
}

function mean(points) {
  let sx = 0, sy = 0;
  for (const [x, y] of points) { sx += x; sy += y; }
  return [sx / points.length, sy / points.length];
}

const cellKey = (cx, cy) => `${cx},${cy}`;

function cellBuckets(graph, ids, cell) {
  const buckets = new Map();
  for (const id of ids) {
    const n = graph.nodes.get(id);
    const k = cellKey(Math.floor(n.x / cell), Math.floor(n.y / cell));
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(id);
  }
  return buckets;
}

function components(graph, members, ties = new Map()) {
  const set = new Set(members);
  const seen = new Set();
  const out = [];
  for (const start of members) {
    if (seen.has(start)) continue;
    const comp = [];
    const stack = [start];
    seen.add(start);
    while (stack.length) {
      const id = stack.pop();
      comp.push(id);
      for (const nbr of [...neighbors(graph, id), ...(ties.get(id) ?? [])]) {
        if (set.has(nbr) && !seen.has(nbr)) { seen.add(nbr); stack.push(nbr); }
      }
    }
    out.push(comp);
  }
  return out;
}

// `excluded` collects the contracted carriageway transitions and
// `unmergeable` the dead ends kept back for leg counting plus the ramp
// nodes, and both groups skip the merge as singletons. Only `excluded`
// marks its cluster, because a transition seeds no tile at all, whereas
// the degree filter instead deletes a held-out dead end.
export function consolidate(graph, { toleranceM, excluded = new Set(), unmergeable = new Set(), ties = new Map() }) {
  const ids = [...graph.nodes.keys()];
  const parent = new Map(ids.map((id) => [id, id]));
  const find = (a) => {
    while (parent.get(a) !== a) { parent.set(a, parent.get(parent.get(a))); a = parent.get(a); }
    return a;
  };
  const union = (a, b) => { a = find(a); b = find(b); if (a !== b) parent.set(a, b); };
  const doNotMerge = (id) => excluded.has(id) || unmergeable.has(id);

  // To limit candidate pairs to nearby nodes, the sweep consults a grid
  // hash whose bucket side is 2 * toleranceM, so a center within merging
  // distance is always in its own bucket or one beside it.
  const cell = 2 * toleranceM;
  const buckets = cellBuckets(graph, ids.filter((id) => !doNotMerge(id)), cell);
  for (const id of ids) {
    if (doNotMerge(id)) continue;
    const n = graph.nodes.get(id);
    const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const bucket = buckets.get(cellKey(cx + dx, cy + dy));
      if (!bucket) continue;
      for (const other of bucket) {
        if (other <= id) continue;
        const m = graph.nodes.get(other);
        if (buffersOverlap([n.x, n.y], [m.x, m.y], toleranceM)) union(id, other);
      }
    }
  }

  const groups = new Map();
  for (const id of ids) {
    const root = find(id);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(id);
  }

  const clusters = [];
  for (const members of groups.values()) {
    if (members.length === 1) {
      const n = graph.nodes.get(members[0]);
      clusters.push({ members, x: n.x, y: n.y, excluded: excluded.has(members[0]), split: false });
      continue;
    }
    const comps = components(graph, members, ties);
    if (comps.length === 1) {
      const circles = members.map((id) => { const n = graph.nodes.get(id); return [bufferCircle(n.x, n.y, toleranceM)]; });
      const merged = polygonClipping.union(...circles);
      const [x, y] = multiPolygonCentroid(merged);
      clusters.push({ members: [...members].sort((a, b) => a - b), x, y, excluded: false, split: false });
    } else {
      for (const comp of comps) {
        // The library and osmnx place a component at the centroid of the
        // union of its points, which counts coincident nodes once.
        const points = new Map(comp.map((id) => { const n = graph.nodes.get(id); return [`${n.x},${n.y}`, [n.x, n.y]]; }));
        const [x, y] = mean([...points.values()]);
        clusters.push({ members: [...comp].sort((a, b) => a - b), x, y, excluded: false, split: true });
      }
    }
  }

  clusters.sort((a, b) => a.x - b.x || a.y - b.y);
  const clusterOf = new Map();
  clusters.forEach((c, i) => { c.index = i; for (const id of c.members) clusterOf.set(id, i); });
  for (const c of clusters) c.degree = legCount(graph, c.members, (nbr) => clusterOf.get(nbr), c.index);
  return { clusters, clusterOf };
}

// On the rebuilt graph, the count reproduces osmnx's street_count, treating
// `members` as one junction. Between `members` and a neighboring junction,
// whichever direction counted more edges wins, so a two-way street yields one
// leg, two separately mapped ways yield two, and both carriageways of a divided
// road make one. A loop road out of a member and back adds two (see
// isLoopRoad). Roads and `*_link` edges tally apart, and a link counts only
// when the junction has a single road leg, so a ramp-ended road still takes its
// ramps as legs while a ramp onto a through road contributes nothing.
// `clusterOf(node)` maps a neighbor to its junction, and an edge reaching a
// member of `own` adds nothing. The adjacency stores edges under their source
// alone, so incoming ones appear in the neighbor's entry.
function legCount(graph, members, clusterOf, own) {
  const count = (l, links) => (l ? l.edges.filter((e) => isLink(e.highway) === links).length : 0);
  const tally = [0, 0];
  for (const links of [false, true]) {
    const out = new Map(), inn = new Map();
    let loop = false;
    for (const id of members) {
      for (const [nbr, l] of legs(graph, id)) {
        if (nbr === id) { if (l.edges.some((e) => isLink(e.highway) === links && isLoopRoad(e))) loop = true; continue; }
        const j = clusterOf(nbr);
        if (j === undefined || j === own) continue;
        out.set(j, (out.get(j) ?? 0) + count(l, links));
        inn.set(j, (inn.get(j) ?? 0) + count(legs(graph, nbr).get(id), links));
      }
    }
    let legsOfKind = 0;
    for (const j of new Set([...out.keys(), ...inn.keys()])) legsOfKind += Math.max(out.get(j) ?? 0, inn.get(j) ?? 0);
    tally[links ? 1 : 0] = legsOfKind + (loop ? 2 : 0);
  }
  const [roads, links] = tally;
  return roads + (roads === 1 ? links : 0);
}

// Dead ends aside, the merge skips ramp nodes that fail the junction test on
// their own, be it a slip lane's end sitting mid-block, a bend dropping a road
// into a single ramp, or a knot of ramps only. Left in, one drags a real
// junction's centroid off true and joins that junction's osmids.
// `junctionDegree` fixes what counts as a junction, no matter which minDegree
// the filter lists. It merges anyway in the library's cases. Where a
// link reaches a junction within the merge distance, the node marks a connector
// tucked inside a consolidated intersection, like Roosevelt Way NE at NE 40th
// Alley and Eastlake Avenue NE at NE Campus Parkway. When every ordinary edge
// reaches a junction inside the merge distance, the node sits pinched between
// them, as at the Bellevue crossing of Southeast Eastgate Way and 148th Avenue
// SE, where two short road edges leave for junctions on either side while a
// `primary_link` finds nothing in range. A through road passing near a
// junction on its way to an unrelated dead end fails that test, and its
// node stays held out.
export function ineligibleLinkNodes(graph, junctionDegree, toleranceM, dead = deadEnds(graph)) {
  const junction = (id) => legCount(graph, [id], (nbr) => nbr, id) >= junctionDegree;
  const connectsToAJunction = (id) => {
    const n = graph.nodes.get(id);
    const nearJunction = (nbr) => {
      const m = graph.nodes.get(nbr);
      return junction(nbr) && buffersOverlap([n.x, n.y], [m.x, m.y], toleranceM);
    };
    const plain = [];
    for (const nbr of legs(graph, id).keys()) {
      if (nbr === id) continue;
      const edges = legEdges(graph, id, nbr);
      if (edges.some((e) => isLink(e.highway)) && nearJunction(nbr)) return true;
      if (edges.some((e) => !isLink(e.highway))) plain.push(nbr);
    }
    return plain.length > 0 && plain.every(nearJunction);
  };
  return new Set([...graph.nodes.keys()].filter((id) => !dead.has(id) && touchesLink(graph, id) && !junction(id) && !connectsToAJunction(id)));
}

// Builds [name, travel bearing] pairs from the node's one-way non-link
// legs, in either direction of travel, a pair per name or ref.
function carriageways(graph, id) {
  const found = [];
  for (const [nbr, l] of legs(graph, id)) {
    if (nbr === id) continue;
    for (const e of l.edges) {
      if (!e.oneway || isLink(e.highway)) continue;
      const bearing = legBearingDeg(graph, e, id);
      for (const name of roadNames([e])) found.push([name, bearing]);
    }
    for (const e of legs(graph, nbr).get(id)?.edges ?? []) {
      if (!e.oneway || isLink(e.highway)) continue;
      const bearing = (legBearingDeg(graph, e, id) + 180) % 360;
      for (const name of roadNames([e])) found.push([name, bearing]);
    }
  }
  return found;
}

const OPPOSITE_MIN_DEG = 150;

// A divided road puts an intersection's junctions on opposite carriageways,
// with no segment crossing the median between them. osmnx unions their buffers
// and splits the union into connected components, so the median leaves the
// sides in separate clusters, as at Dexter Avenue N and Thomas Street. A
// median tie stands in for the crossing segment the median has no room for.
// Two nodes earn a tie when one-way non-link legs at each end share a name or
// ref, their bearings differ by `OPPOSITE_MIN_DEG` or more, they share no
// joining edge, and the buffers overlap. The tie joins the split only, and leg
// counting skips it. Nodes in `skip` never tie. The map is keyed by tied node
// and lists its partners.
export function medianTies(graph, toleranceM, skip) {
  const byNode = new Map();
  for (const id of graph.nodes.keys()) {
    if (skip.has(id)) continue;
    const found = carriageways(graph, id);
    if (found.length) byNode.set(id, found);
  }
  const cell = 2 * toleranceM;
  const buckets = cellBuckets(graph, byNode.keys(), cell);
  const opposite = (mine, theirs) => mine.some(([name, b]) => theirs.some(([other, c]) => other === name && angleDiff(b, c) >= OPPOSITE_MIN_DEG));
  const ties = new Map();
  const tie = (a, b) => { if (!ties.has(a)) ties.set(a, []); ties.get(a).push(b); };
  for (const [a, mine] of byNode) {
    const n = graph.nodes.get(a);
    const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const b of buckets.get(cellKey(cx + dx, cy + dy)) ?? []) {
        if (b <= a || legs(graph, a).has(b)) continue;
        const m = graph.nodes.get(b);
        if (!buffersOverlap([n.x, n.y], [m.x, m.y], toleranceM)) continue;
        if (!opposite(mine, byNode.get(b))) continue;
        tie(a, b); tie(b, a);
      }
    }
  }
  return ties;
}
