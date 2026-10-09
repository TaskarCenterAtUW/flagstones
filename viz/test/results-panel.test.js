import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../src/app/state.js";
import { statRows, parity, describeNode, describeTile } from "../src/app/results-panel.js";
import { appBundle, bundleFromXY, carriagewayJoinBundle, slowEngine } from "./fixtures.js";
import { utmZoneEpsg } from "../src/engine/proj.js";

function bundle() {
  const b = appBundle();
  const tiles = [{ id: "5070:40:40", osmids: [5] }, { id: "5070:0:0", osmids: [999] }];
  b.reference.variants = { node_union: { tiles }, bbox: { tiles }, boundary: { tiles: tiles.slice(0, 1) } };
  return b;
}

test("stat rows carry one value per variant", async () => {
  const store = createStore(bundle());
  await store.settled();
  const rows = statRows(store.state);
  const tiles = rows.find((r) => r.label === "Tiles");
  assert.deepEqual(tiles.values, ["5", ""], "one intersection column, legacy empty until enabled");
});

// Parity needs the live params to match the fixture's reference, which
// carries no transition rules and so ran without contraction. The store
// opens at the library defaults, so the tests walk the params back first.
function toReferenceParams(store) {
  return Promise.resolve()
    .then(() => store.setParam("toleranceM", 5))
    .then(() => store.setParam("gridM", 5))
    .then(() => store.setParam("contractTransitions", false))
    .then(() => store.setParam("clipBufferM", 100))
    .then(() => store.setParam("buildScope", "bbox"))
    .then(() => store.settled());
}

test("the parity row reads null off the default params", async () => {
  const store = createStore(bundle());
  await store.settled();
  await toReferenceParams(store);
  assert.deepEqual(parity(store.state), { matched: 1, total: 2, reference: "bbox" });
  store.setParam("toleranceM", 17);
  await store.settled();
  assert.equal(parity(store.state), null, "the default params drift off the reference");
});

test("a reference exported at a non-standard clip buffer reaches parity at that buffer", async () => {
  const b = bundle();
  const square = (m) => [[[[100 - m, 100 - m], [300 + m, 100 - m], [300 + m, 300 + m], [100 - m, 300 + m], [100 - m, 100 - m]]]];
  b.clips.node_union["300"] = square(300);
  b.clips.boundary["300"] = square(300);
  b.reference.params = { ...b.reference.params, clip_buffer_m: 300 };
  const store = createStore(b);
  await store.settled();
  await toReferenceParams(store);
  store.setParam("clipBufferM", 300);
  await store.settled();
  assert.equal(store.state.warnings.length, 0, store.state.warnings.join("; "));
  assert.deepEqual(parity(store.state), { matched: 1, total: 2, reference: "bbox" });
});

test("describeNode and describeTile read the current results", async () => {
  const store = createStore(bundle());
  await store.settled();
  // Node 5 snaps to cell 8, 8 under the default grid of 24 m.
  const node = describeNode(store.state, 5);
  assert.equal(node.streetCount, 5);
  assert.equal(node.legs.length, 5);
  assert.equal(node.tile, "5070:8:8");
  const tile = describeTile(store.state, "intersection", "5070:8:8");
  assert.deepEqual(tile.osmids, [5]);
  assert.equal(tile.degree, 5);
});

test("parity picks the Python reference by build scope", async () => {
  const store = createStore(bundle());
  await store.settled();
  await toReferenceParams(store);
  store.setParam("buildScope", "hull");
  await store.settled();
  assert.deepEqual(parity(store.state), { matched: 1, total: 2, reference: "bbox" }, "ids do not depend on the clip, so the hull reads against the bbox run");
  store.setParam("buildScope", "union");
  await store.settled();
  assert.deepEqual(parity(store.state), { matched: 1, total: 2, reference: "node_union" });
  store.setParam("buildScope", "boundary");
  await store.settled();
  assert.deepEqual(parity(store.state), { matched: 1, total: 1, reference: "boundary" });
  store.setParam("buildScope", "city");
  await store.settled();
  assert.deepEqual(parity(store.state), { matched: 1, total: 2, reference: "bbox" }, "no Python run clips to the bare boundary; ids read against the bbox run");
});

test("the parity row reads null for a reference that seeded at the raw centroid", async () => {
  // The engine snaps unless told otherwise, so a reference exported with
  // snap_centroid off is a different tiling, whatever the IDs say.
  const b = bundle();
  b.reference.params = { ...b.reference.params, snap_centroid: false };
  const store = createStore(b);
  await store.settled();
  await toReferenceParams(store);
  assert.equal(parity(store.state), null);
});

test("while a CRS switch computes, the inspector places tiles in the CRS they were computed in", async () => {
  const b = appBundle();
  const store = createStore(b, { engine: slowEngine(b) });
  await store.settled();
  const id = store.state.results.intersection.tiles[0].nativeId;
  const before = describeTile(store.state, "intersection", id).seedLonLat;
  const n = store.state.graph.nodes.get(2);
  store.setParam("epsg", `EPSG:${utmZoneEpsg(n.lon, n.lat)}`);
  assert.equal(store.state.busy, true);
  const during = describeTile(store.state, "intersection", id).seedLonLat;
  assert.ok(Math.abs(during[0] - before[0]) < 1e-9 && Math.abs(during[1] - before[1]) < 1e-9);
  await store.settled();
});

test("the inspector names a road that only arrives at a node", async () => {
  const store = createStore(appBundle(carriagewayJoinBundle()));
  await store.settled();
  const leg = describeNode(store.state, 3).legs.find((l) => l.to === 1);
  assert.equal(leg.direction, "one-way in");
  assert.deepEqual(leg.roads, ["Grand Avenue"]);
});

test("the inspector finds a node's tile when its cluster collapsed into another's", async () => {
  // Two T junctions 10 m apart with no edge between them share a 24 m cell.
  const nodes = [
    { id: 1, x: 190, y: 192, sc: 3 }, { id: 11, x: 190, y: 400, sc: 1 }, { id: 12, x: 0, y: 192, sc: 1 }, { id: 13, x: 190, y: 0, sc: 1 },
    { id: 2, x: 200, y: 192, sc: 3 }, { id: 21, x: 200, y: 400, sc: 1 }, { id: 22, x: 400, y: 192, sc: 1 }, { id: 23, x: 200, y: 0, sc: 1 },
  ];
  const edges = [{ u: 1, v: 11 }, { u: 1, v: 12 }, { u: 1, v: 13 }, { u: 2, v: 21 }, { u: 2, v: 22 }, { u: 2, v: 23 }];
  const store = createStore(appBundle(bundleFromXY(nodes, edges)));
  await store.settled();
  const tile = store.state.results.intersection.tiles.find((t) => t.osmids.includes(2));
  assert.ok(tile && tile.osmids.includes(1), "the two clusters collapsed into one tile");
  assert.equal(describeNode(store.state, 1).tile, tile.nativeId);
  assert.equal(describeNode(store.state, 2).tile, tile.nativeId);
});
