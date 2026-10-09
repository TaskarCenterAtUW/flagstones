import { inverse } from "../src/engine/proj.js";
import { inProcessEngine } from "../src/app/engine.js";

// In meters (EPSG:5070 by default), this builds a CityBundle-shaped object from
// the nodes given. Edges are given once per two-way street and expanded into
// both directions the way osmnx's MultiDiGraph stores them, while a oneway edge
// appears once. `sc` carries street_count, set the way osmnx sets it.
export function bundleFromXY(nodes, edges, { epsg = 5070 } = {}) {
  const outNodes = nodes.map(({ id, x, y, sc }) => {
    const [lon, lat] = inverse(epsg, x, y);
    return { id, lon, lat, x, y, sc };
  });
  const byId = new Map(outNodes.map((n) => [n.id, n]));
  const outEdges = [];
  for (const e of edges) {
    const u = byId.get(e.u), v = byId.get(e.v);
    const geom = e.geomXY ? e.geomXY.map(([x, y]) => inverse(epsg, x, y)) : e.geom ?? [[u.lon, u.lat], [v.lon, v.lat]];
    const base = { oneway: !!e.oneway, name: e.name ?? [], ref: e.ref ?? [], highway: e.highway ?? "residential" };
    outEdges.push({ u: e.u, v: e.v, k: 0, geom, ...base });
    if (!e.oneway) outEdges.push({ u: e.v, v: e.u, k: 0, geom: [...geom].reverse(), ...base });
  }
  return { schema: "flagstones-viz/1", city: { crs: epsg, utm_epsg: 32611 }, nodes: outNodes, edges: outEdges };
}

// At a junction, two divided-road carriageways cross as a 20 m square of four
// nodes, each with an outward arm, so the consolidated intersection has degree
// 6 from its six arms. The square's four edges stay internal. Mirrors
// _divided_road_fixture in tests/graphs.py.
export function dividedRoadBundle() {
  const nodes = [
    { id: 1, x: 1000, y: 1010, sc: 3 }, { id: 2, x: 1020, y: 1010, sc: 3 },
    { id: 3, x: 1000, y: 990, sc: 3 }, { id: 4, x: 1020, y: 990, sc: 3 },
    { id: 10, x: 1000, y: 1200, sc: 1 }, { id: 20, x: 1020, y: 1200, sc: 1 },
    { id: 30, x: 1000, y: 800, sc: 1 }, { id: 40, x: 1020, y: 800, sc: 1 },
    { id: 50, x: 800, y: 1010, sc: 1 }, { id: 60, x: 1220, y: 990, sc: 1 },
  ];
  const edges = [
    { u: 1, v: 2 }, { u: 3, v: 4 }, { u: 1, v: 3 }, { u: 2, v: 4 },
    { u: 1, v: 10 }, { u: 2, v: 20 }, { u: 3, v: 30 }, { u: 4, v: 40 }, { u: 1, v: 50 }, { u: 4, v: 60 },
  ];
  return bundleFromXY(nodes, edges);
}

// Off a corner, a cul-de-sac 2-5 leaves the ring road 1-2-3-4. Node 2
// is a real three-leg junction, and node 5 remains the dead end.
export function tJunctionWithDeadEndBundle() {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 2 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 200, y: 200, sc: 2 },
      { id: 4, x: 0, y: 200, sc: 2 }, { id: 5, x: 200, y: -120, sc: 1 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 3, v: 4 }, { u: 4, v: 1 }, { u: 2, v: 5 }],
  );
}

// A 3x3 grid of two-way streets, 100 m spacing, IDs 1..9 row-major from
// the south-west corner, with a dead-end stub off the center node.
export function gridBundle() {
  const nodes = [];
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
    const id = r * 3 + c + 1;
    const sc = (r === 1 ? 1 : 0) + (c === 1 ? 1 : 0) + 2;
    nodes.push({ id, x: 100 + c * 100, y: 100 + r * 100, sc: id === 5 ? 5 : sc });
  }
  nodes.push({ id: 99, x: 200, y: 260, sc: 1 });
  const edges = [];
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
    const id = r * 3 + c + 1;
    if (c < 2) edges.push({ u: id, v: id + 1 });
    if (r < 2) edges.push({ u: id, v: id + 3 });
  }
  edges.push({ u: 5, v: 99 });
  return bundleFromXY(nodes, edges);
}

// For the app's store, `b` arrives dressed as an exported bundle, with
// reference params of 5 / 5 / 3 / 100, square clips at every exported
// buffer, and a reference with no clusters or tiles.
export function appBundle(b = gridBundle()) {
  b.params = { consolidation_tolerance_m: 5, seed_grid_m: 5, degree_filter_min: 3, inheritance_radius_m: 7.1, clip_buffer_m: 100 };
  b.city = { ...b.city, name: "Grid", query: "Grid", center: [0, 0], bounds: [0, 0, 0, 0] };
  const square = (m) => [[[[100 - m, 100 - m], [300 + m, 100 - m], [300 + m, 300 + m], [100 - m, 300 + m], [100 - m, 100 - m]]]];
  b.clips = { node_union: {}, boundary: {} };
  for (const m of [100, 250, 500, 1000]) { b.clips.node_union[m] = square(m); b.clips.boundary[m] = square(m); }
  b.boundary = []; b.boeing = null;
  b.reference = { params: b.params, clusters: [], variants: {} };
  return b;
}

// A dual carriageway ("Grand Avenue") whose one-way carriageways merge
// at node 3 into a two-way continuation. Within the join, three legs
// meet, all the same road. Nodes 1 and 2 sit at the carriageway ends
// (dead ends here), and node 4 closes the two-way stretch.
export function carriagewayJoinBundle() {
  const name = ["Grand Avenue"];
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 8, sc: 1 }, { id: 2, x: 0, y: -8, sc: 1 },
      { id: 3, x: 200, y: 0, sc: 3 }, { id: 4, x: 400, y: 0, sc: 1 },
    ],
    [
      { u: 1, v: 3, oneway: true, name }, // northbound carriageway flows into the join
      { u: 3, v: 2, oneway: true, name }, // southbound carriageway leaves it
      { u: 3, v: 4, name },               // two-way continuation
    ],
  );
}

// Off node 2 of the four-node ring 1-2-3-4 runs a cul-de-sac 2-5, its
// dead end 4 m from node 2 and inside the 10 m merge distance. Node 2
// makes a three-leg junction (two ring legs plus the cul-de-sac). If the
// stub folded into the junction, the junction would read degree 2 and
// the filter would drop its tile, so node 5 keeps its own filtered
// cluster. Node 2 tiles at degree 3, and node 5 tiles nothing.
export function culDeSacCloseStubBundle() {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 2 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 200, y: 200, sc: 2 },
      { id: 4, x: 0, y: 200, sc: 2 }, { id: 5, x: 200, y: -4, sc: 1 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 3, v: 4 }, { u: 4, v: 1 }, { u: 2, v: 5 }],
  );
}

// A three-way junction sits at node 2, roads to 1, 5, and on to 3, with
// a slip lane leaving that road 6 m past the junction at node 7. Two
// road legs and a ramp make node 7 no intersection, and it sits inside
// the 10 m merge distance of node 2, adjacent to it.
export function junctionWithSlipLaneEndBundle() {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 7, x: 206, y: 0, sc: 3 },
      { id: 3, x: 400, y: 0, sc: 1 }, { id: 5, x: 200, y: 200, sc: 1 }, { id: 8, x: 206, y: -200, sc: 1 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 7 }, { u: 7, v: 3 }, { u: 2, v: 5 }, { u: 7, v: 8, highway: "primary_link" }],
  );
}

// A node wedged between two junctions. Node 7 sits 8 m east of junction 2
// (roads to 1, 3, 5) and 8 m west of junction 9 (roads to 10, 11, 12), on
// ordinary road edges to both, and has a primary_link south to a dead end that
// reaches no junction. In Bellevue, Southeast Eastgate Way at 148th Avenue SE
// has this shape, and the library merges it across both plain edges.
export function wedgedLinkNodeBundle() {
  return bundleFromXY(
    [
      { id: 1, x: -200, y: 0, sc: 1 }, { id: 2, x: 0, y: 0, sc: 3 }, { id: 3, x: 0, y: 200, sc: 1 }, { id: 5, x: 0, y: -200, sc: 1 },
      { id: 7, x: 8, y: 0, sc: 3 }, { id: 8, x: 8, y: -300, sc: 1 },
      { id: 9, x: 16, y: 0, sc: 3 }, { id: 10, x: 216, y: 0, sc: 1 }, { id: 11, x: 16, y: 200, sc: 1 }, { id: 12, x: 16, y: -200, sc: 1 },
    ],
    [
      { u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 5 }, { u: 2, v: 7 }, { u: 7, v: 9 }, { u: 9, v: 10 }, { u: 9, v: 11 }, { u: 9, v: 12 },
      { u: 7, v: 8, highway: "primary_link" },
    ],
  );
}

// A junction at node 2 (roads to 1, 3, 5) and, `linkM` east of it along a
// primary_link, node 7 on a one-way through road 6-7-8 that crosses there.
// It has two road legs and the link, the way Roosevelt Way NE meets the
// connector from Eastlake Avenue NE at NE Campus Parkway.
export function linkBetweenJunctionsBundle(linkM) {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 200, y: 200, sc: 1 },
      { id: 5, x: 200, y: -200, sc: 1 }, { id: 7, x: 200 + linkM, y: 0, sc: 3 },
      { id: 6, x: 200 + linkM, y: 200, sc: 1 }, { id: 8, x: 200 + linkM, y: -200, sc: 1 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 5 }, { u: 6, v: 7, oneway: true }, { u: 7, v: 8, oneway: true }, { u: 2, v: 7, oneway: true, highway: "primary_link" }],
  );
}

// A two-way side street meeting a one-way avenue at a T. Same in/out/two-way
// shape as a join, different roads, so it must NOT classify as a transition.
export function oneWayAvenueTeeBundle() {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 },
      { id: 3, x: 400, y: 0, sc: 1 }, { id: 4, x: 200, y: 150, sc: 1 },
    ],
    [
      { u: 1, v: 2, oneway: true, name: ["3rd Avenue"] },
      { u: 2, v: 3, oneway: true, name: ["3rd Avenue"] },
      { u: 2, v: 4, name: ["Pine Street"] },
    ],
  );
}

// A road divided by a median, "Main Street" as two one-way carriageways
// `gapM` apart that rejoin at nodes 1 (west) and 6 (east), a side street
// ending on each carriageway at x=200, and no segment through the median.
export function medianPairBundle({ gapM = 8, sameName = true } = {}) {
  const h = gapM / 2;
  const north = ["Main Street"], south = [sameName ? "Main Street" : "Other Street"];
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 2 }, { id: 6, x: 400, y: 0, sc: 2 },
      { id: 2, x: 200, y: h, sc: 3 }, { id: 3, x: 200, y: -h, sc: 3 },
      { id: 4, x: 200, y: 200, sc: 1 }, { id: 5, x: 200, y: -200, sc: 1 },
    ],
    [
      { u: 6, v: 2, oneway: true, name: north, highway: "primary" }, { u: 2, v: 1, oneway: true, name: north, highway: "primary" },
      { u: 1, v: 3, oneway: true, name: south, highway: "primary" }, { u: 3, v: 6, oneway: true, name: south, highway: "primary" },
      { u: 2, v: 4, name: ["North Street"] }, { u: 3, v: 5, name: ["South Street"] },
    ],
  );
}

// A square loop way of perimeter `loopM` leaving (x, y) and returning to it.
export function loopXY(x, y, loopM) {
  const s = loopM / 4;
  return [[x, y], [x + s, y], [x + s, y - s], [x, y - s], [x, y]];
}

// A T junction at node 2 whose third street ends 60 m on at node 5 in a
// loop way of perimeter `loopM`, with osmnx's street_count of 3 there.
// A turning circle at 40 m, a loop road at 300 m.
export function loopEndStubBundle({ loopM = 40 } = {}) {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 200, y: 200, sc: 1 },
      { id: 5, x: 200, y: -60, sc: 3 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 5 }, { u: 5, v: 5, geomXY: loopXY(200, -60, loopM) }],
  );
}

// A T junction at node 2 with a 4 m stub to node 5 that osmnx truncated at
// the city limit. The stub end keeps the street_count of the junction it
// was in the buffered graph (3), while this graph gives it one leg.
export function truncatedJunctionStubBundle() {
  return bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 200, y: 200, sc: 1 },
      { id: 5, x: 200, y: -4, sc: 3 },
    ],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 5 }],
  );
}

// An engine that answers after a delay and counts the computes it was asked
// for, so a test can see what the store sends while a request is in flight.
export function slowEngine(b, delayMs = 30) {
  const inner = inProcessEngine(b);
  const engine = { kind: "slow", computes: 0, send(msg) {
    if (msg.type === "compute") engine.computes++;
    return new Promise((r) => setTimeout(r, delayMs)).then(() => inner.send(msg));
  } };
  return engine;
}
