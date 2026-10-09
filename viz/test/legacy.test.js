import { test } from "node:test";
import assert from "node:assert/strict";
import { legacyTiles, legacySeeds } from "../src/engine/legacy.js";
import { buildGraph } from "../src/engine/graph.js";
import { inverse, forward } from "../src/engine/proj.js";
import { bundleFromXY, gridBundle } from "./fixtures.js";

// The fixtures live near lon -119, lat 47.5 (UTM zone 11), a sane interior
// point of EPSG:32611. At the 5070 origin (lon -120, lat 23) the Albers
// standard parallel makes the degree Voronoi coincide with the metric
// bisector and UTM 32611 sits at its zone edge, so neither the skew nor the
// area assertions could hold.
const OX = -1727016.31869, OY = 2932987.47332;

function shift(bundle) {
  return {
    ...bundle,
    nodes: bundle.nodes.map((n) => {
      const x = n.x + OX, y = n.y + OY;
      const [lon, lat] = inverse(5070, x, y);
      return { ...n, x, y, lon, lat };
    }),
  };
}

function scopeAround(bundle, marginM) {
  const xs = bundle.nodes.map((n) => n.x), ys = bundle.nodes.map((n) => n.y);
  const [minx, miny, maxx, maxy] = [Math.min(...xs) - marginM, Math.min(...ys) - marginM, Math.max(...xs) + marginM, Math.max(...ys) + marginM];
  return [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]].map(([x, y]) => inverse(5070, x, y));
}

test("every edge endpoint seeds, self-loops do not", () => {
  const bundle = shift(gridBundle());
  bundle.edges.push({ u: 1, v: 1, k: 0, oneway: false, name: [], ref: [], geom: [] });
  const g = buildGraph(bundle);
  assert.equal(legacySeeds(g).length, 10);
  const lonely = shift(bundleFromXY([{ id: 1, x: 0, y: 0, sc: 0 }, { id: 2, x: 100, y: 0, sc: 1 }, { id: 3, x: 200, y: 0, sc: 1 }], [{ u: 2, v: 3 }]));
  assert.deepEqual(legacySeeds(buildGraph(lonely)).map((s) => s.nodeId), [2, 3]);
});

test("cells are computed in degrees, so the shared boundary is not the metric bisector", () => {
  // Two seeds 100 m apart along a diagonal. The Voronoi is built in lon/lat
  // and clipped back to a box, so the shared boundary is a straight line in
  // degree space. Projected to meters its slope is -1/cos^2(lat), NOT the
  // metric perpendicular-bisector slope of -1. We read that slope off the
  // edge the clipped cells share.
  const bundle = shift(bundleFromXY([{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 70.71, y: 70.71, sc: 1 }], [{ u: 1, v: 2 }]));
  const [lon1, lat1] = inverse(5070, OX, OY);
  const [lon2, lat2] = inverse(5070, OX + 70.71, OY + 70.71);
  const cx = OX + 35.355, cy = OY + 35.355;
  const scope = [[cx - 300, cy - 300], [cx + 300, cy - 300], [cx + 300, cy + 300], [cx - 300, cy + 300], [cx - 300, cy - 300]]
    .map(([x, y]) => inverse(5070, x, y));
  const tiles = legacyTiles(buildGraph(bundle), scope, { utmEpsg: 32611 });
  assert.equal(tiles.length, 2);
  // The single segment shared by the exterior rings, in metric meters.
  const segs = (t) => {
    const r = t.geometryLonLat[0][0];
    const out = [];
    for (let i = 0; i < r.length - 1; i++) out.push([r[i].slice(), r[i + 1].slice()]);
    return out;
  };
  const near = (p, q) => Math.abs(p[0] - q[0]) < 1e-10 && Math.abs(p[1] - q[1]) < 1e-10;
  const a0 = segs(tiles[0]), b0 = segs(tiles[1]);
  const shared = a0.find((s) => b0.some((o) => (near(s[0], o[0]) && near(s[1], o[1])) || (near(s[0], o[1]) && near(s[1], o[0]))));
  assert.ok(shared, "the two cells share one boundary segment");
  const P = forward(5070, shared[0][0], shared[0][1]);
  const Q = forward(5070, shared[1][0], shared[1][1]);
  const slope = (Q[1] - P[1]) / (Q[0] - P[0]);
  const c2 = Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180)) ** 2;
  assert.ok(Math.abs(slope - (-1 / c2)) < 0.07, `degree-space slope ${slope} should be ~ -1/c^2=${-1 / c2}`);
  assert.ok(Math.abs(slope - -1) > 0.3, `slope ${slope} must not be the metric bisector's -1`);
});

test("tiles are clipped to the hull and cover it", () => {
  const bundle = shift(gridBundle());
  const scope = scopeAround(bundle, 100);
  const tiles = legacyTiles(buildGraph(bundle), scope, { utmEpsg: 32611 });
  assert.equal(tiles.length, 10);
  const total = tiles.reduce((s, t) => s + t.areaM2, 0);
  assert.ok(total > 390 * 390 && total < 410 * 410, `hull of a 400 m box, got ${total}`);
});
