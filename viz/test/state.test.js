import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../src/app/state.js";
import { appBundle as bundle, slowEngine } from "./fixtures.js";
import { inverse, utmZoneEpsg } from "../src/engine/proj.js";
import { inProcessEngine } from "../src/app/engine.js";

test("the store starts at the viz tool's defaults, not the bundle's published params", async () => {
  const store = createStore(bundle());
  const p = store.state.params;
  assert.equal(p.toleranceM, 17, "consolidation tolerance default");
  assert.equal(p.gridM, 24, "seed grid default");
  assert.equal(p.clipBufferM, 250, "clip buffer default");
  assert.equal(p.buildScope, "union", "the viz tool's build scope is the default");
  assert.ok(p.gridM < p.toleranceM * Math.SQRT2, "the default grid stays below the collision bound");
});

test("the store computes the enabled variants on creation", async () => {
  const store = createStore(bundle());
  await store.settled();
  assert.ok(store.state.results.intersection.tiles.length > 0);
  assert.equal(store.state.results.legacy, null, "the legacy layer starts off");
});

test("a parameter change recomputes and notifies", async () => {
  const store = createStore(bundle());
  await store.settled();
  let notified = 0;
  store.subscribe(() => notified++);
  store.setParam("minDegree", 2);
  await store.settled();
  assert.equal(store.state.results.intersection.tiles.length, 9);
  assert.ok(notified >= 1);
});

test("a grid past the bound is kept, not clamped, and the run still computes", async () => {
  const store = createStore(bundle());
  await store.settled();
  const tolBefore = store.state.params.toleranceM;
  const over = Math.floor((tolBefore * Math.SQRT2 + 1) * 2) / 2; // a valid step above the bound
  store.setParam("gridM", over);
  await store.settled();
  assert.equal(store.state.params.gridM, over, "the value is not clamped back");
  assert.ok(store.state.results.intersection, "the run still happened");
  assert.ok(!store.state.warnings.some((w) => /collision bound/.test(w)), "the over-bound warning is not a store warning");
});

test("nudging a node recomputes and records a diff", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.nudgeNode(2, 12, 0);
  await store.settled();
  assert.equal(store.movedNodes().length, 1);
  const d = store.state.diff.intersection;
  assert.ok(d.added.size + d.changed.size + d.removed.size > 0);
  store.resetAll();
  await store.settled();
  assert.equal(store.movedNodes().length, 0);
});

test("enabling the legacy layer computes it once", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.setLayer("legacy", true);
  await store.settled();
  assert.equal(store.state.results.legacy.tiles.length, 10);
});

test("a parameter change keeps the diff for the stats row but outlines nothing", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.setParam("minDegree", 2);
  await store.settled();
  const d = store.state.diff.intersection;
  assert.ok(d.added.size + d.changed.size > 0, "the stats row still has counts to show");
  assert.equal(store.changedTiles().size, 0);
});

test("a node edit outlines the tiles it changed, only while the tiles are shown", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.nudgeNode(2, 12, 0);
  await store.settled();
  const d = store.state.diff.intersection;
  const expected = new Set([...d.added, ...d.changed]);
  assert.ok(expected.size > 0);
  assert.deepEqual(store.changedTiles(), expected);
  store.setLayer("tiles", false);
  assert.equal(store.changedTiles().size, 0, "hidden tiles get no outline");
});

test("the edit layers start visible and are listed in the key", async () => {
  const store = createStore(bundle());
  assert.equal(store.state.layers.changedTiles, true);
  assert.equal(store.state.layers.movedNodes, true);
});

test("the union build scope clips to the node union the export carries", async () => {
  const b = bundle();
  const diamond = [[200, -100], [500, 200], [200, 500], [-100, 200], [200, -100]];
  b.clips.node_union[100] = [[diamond]];
  const store = createStore(b);
  await store.settled();
  // The diamond is the node-union clip at the 100 m buffer, and the UI opens
  // at the library default, so pin the buffer to 100 to read the fixture's
  // polygon.
  store.setParam("clipBufferM", 100);
  store.setParam("buildScope", "union");
  await store.settled();
  assert.deepEqual(store.state.results.intersection.clip, [[diamond]]);
  assert.equal(store.state.results.intersection.tiles.length, 5);
});

test("the hull build scope is the hull of every node's buffered disc, whatever the union clip", async () => {
  // The legacy tiling clips to the convex hull of the whole network, so the
  // hull follows all of the graph's nodes, dead ends included, and not the
  // union clip, which leaves street ends out. The fixture's diamond union
  // must not shape it: buffered by the clip buffer, the hull reaches past the
  // outermost node on every side.
  const b = bundle();
  b.clips.node_union[100] = [[[[200, -100], [500, 200], [200, 500], [-100, 200], [200, -100]]]];
  const store = createStore(b);
  await store.settled();
  store.setParam("clipBufferM", 100);
  store.setParam("buildScope", "hull");
  await store.settled();
  const clip = store.state.results.intersection.clip;
  assert.equal(clip.length, 1, "one polygon");
  assert.equal(clip[0].length, 1, "one ring, no holes");
  const ring = clip[0][0];
  const xs = b.nodes.map((n) => n.x), ys = b.nodes.map((n) => n.y);
  const reach = [[Math.min(...ring.map((p) => p[0])), Math.min(...xs) - 100], [Math.max(...ring.map((p) => p[0])), Math.max(...xs) + 100],
    [Math.min(...ring.map((p) => p[1])), Math.min(...ys) - 100], [Math.max(...ring.map((p) => p[1])), Math.max(...ys) + 100]];
  for (const [got, want] of reach) assert.ok(Math.abs(got - want) < 1e-3, `hull reaches ${want}, got ${got}`);
  assert.equal(store.state.results.intersection.tiles.length, 5, "the clip rule re-shapes edge cells, it does not drop tiles");
});

test("the city build scope clips to the unbuffered boundary the export carries", async () => {
  // The boundary is lon/lat in the bundle; the clip must come back in the
  // computation CRS. A polygon slightly inside the fixture's 0..400 square
  // shows the seeds on the edge are what this scope can drop.
  const b = bundle();
  const ring5070 = [[20, 20], [380, 20], [380, 380], [20, 380], [20, 20]];
  b.boundary = [[ring5070.map(([x, y]) => inverse(5070, x, y))]];
  const store = createStore(b);
  await store.settled();
  const before = store.state.results.intersection.tiles.length;
  store.setParam("buildScope", "city");
  await store.settled();
  const ring = store.state.results.intersection.clip[0][0];
  assert.equal(ring.length, 5);
  for (let i = 0; i < 5; i++) assert.ok(Math.abs(ring[i][0] - ring5070[i][0]) < 1e-6 && Math.abs(ring[i][1] - ring5070[i][1]) < 1e-6, `vertex ${i}`);
  assert.equal(store.state.results.intersection.tiles.length, before, "seeds inside the boundary keep their tiles");
});

test("the store computes through the engine it is given", async () => {
  const engine = slowEngine(bundle());
  const store = createStore(bundle(), { engine });
  assert.equal(store.engine, engine);
  await store.settled();
  assert.equal(engine.computes, 1);
  assert.ok(store.state.results.intersection.tiles.length > 0);
  assert.ok(store.state.timings.intersection >= 0, "the compute time is reported from the engine");
});

test("requests made while one is in flight coalesce into the newest, and its answer is the one applied", async () => {
  const engine = slowEngine(bundle());
  const store = createStore(bundle(), { engine });
  await store.settled();
  assert.equal(engine.computes, 1);
  store.setParam("minDegree", 1);
  store.setParam("minDegree", 2);
  store.setParam("minDegree", 3);
  assert.equal(store.state.busy, true);
  await store.settled();
  assert.equal(engine.computes, 3, "three changes: one was in flight, the other two collapsed into one request");
  assert.equal(store.state.params.minDegree, 3);
  assert.equal(store.state.results.intersection.tiles.length, 5, "the answer is for the last parameters");
  assert.equal(store.state.busy, false);
});

test("a compute that fails leaves a warning and the last good result, and the store is not stuck busy", async () => {
  const store = createStore(bundle(), { engine: inProcessEngine(bundle()) });
  await store.settled();
  const good = store.state.results.intersection;
  store.setParam("gridM", 0);
  await store.settled();
  assert.equal(store.state.results.intersection, good);
  assert.match(store.state.warnings[0], /^tiles: /);
  assert.equal(store.state.busy, false);
});

function bundleWithExtent() {
  const b = bundle();
  b.clips.extent = [[[[-300, -300], [700, -300], [700, 700], [-300, 700], [-300, -300]]]];
  return b;
}

test("the fallback cells are computed only while their layer is on", async () => {
  const store = createStore(bundleWithExtent());
  await store.settled();
  assert.ok(store.state.results.intersection.fallback.length > 0, "the layer starts on");
  store.setLayer("fallback", false);
  store.setParam("minDegree", 2);
  await store.settled();
  assert.deepEqual(store.state.results.intersection.fallback, [], "no fallback while the layer is off");
  store.setLayer("fallback", true);
  await store.settled();
  assert.ok(store.state.results.intersection.fallback.length > 0, "turning the layer on computes the cells");
});

test("a fallback cell change waits for the layer while it is off", async () => {
  const store = createStore(bundleWithExtent());
  await store.settled();
  store.setLayer("fallback", false);
  const before = store.state.results.intersection;
  store.setParam("fallbackCellM", 500);
  await store.settled();
  assert.equal(store.state.results.intersection, before, "no recompute");
  store.setLayer("fallback", true);
  await store.settled();
  assert.ok(store.state.results.intersection.fallback.every((t) => Math.abs(t.seedX % 500) === 250), "cells at the new size");
});

test("a bundle without an extent switches CRS without the fallback", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.setParam("epsg", "EPSG:32614");
  await store.settled();
  assert.equal(store.state.busy, false);
  assert.deepEqual(store.state.warnings, []);
  assert.deepEqual(store.state.results.intersection.fallback, []);
});

test("a request that fails to build clears the busy flag and warns", async () => {
  const b = bundle();
  b.boundary = null;
  const store = createStore(b);
  await store.settled();
  store.setParam("buildScope", "city");
  await store.settled();
  assert.equal(store.state.busy, false);
  assert.match(store.state.warnings[0], /^engine: /);
});

const TILE_LAYERS = ["tiles", "fallback", "seeds", "grid", "consolidation"];

test("a compute asks for the tiles only while a tile layer is on", async () => {
  for (const id of TILE_LAYERS) {
    const sent = [];
    const inner = inProcessEngine(bundle());
    const engine = { send(msg) { if (msg.type === "compute") sent.push(msg); return inner.send(msg); } };
    const store = createStore(bundle(), { engine });
    await store.settled();
    for (const other of TILE_LAYERS) store.setLayer(other, false);
    store.setParam("minDegree", 2);
    await store.settled();
    assert.equal(sent.at(-1).intersection, null, "no tile layer on");
    store.setLayer(id, true);
    store.setParam("minDegree", 3);
    await store.settled();
    assert.notEqual(sent.at(-1).intersection, null, id);
  }
});

test("showing a tile layer after an edit made while all were hidden recomputes", async () => {
  const store = createStore(bundle());
  await store.settled();
  for (const id of TILE_LAYERS) store.setLayer(id, false);
  store.setParam("minDegree", 2);
  await store.settled();
  const fresh = createStore(bundle());
  fresh.setParam("minDegree", 2);
  await fresh.settled();
  store.setLayer("tiles", true);
  await store.settled();
  assert.equal(store.state.results.intersection.tiles.length, fresh.state.results.intersection.tiles.length);
});

test("showing the legacy layer after an edit made while it was hidden recomputes it", async () => {
  const sent = [];
  const inner = inProcessEngine(bundle());
  const engine = { send(msg) { if (msg.type === "compute") sent.push(msg); return inner.send(msg); } };
  const store = createStore(bundle(), { engine });
  await store.settled();
  store.setLayer("legacy", true);
  await store.settled();
  store.setLayer("legacy", false);
  store.setParam("clipBufferM", 500);
  await store.settled();
  const asked = () => sent.filter((m) => m.legacy).length;
  const before = asked();
  store.setLayer("legacy", true);
  await store.settled();
  assert.equal(asked(), before + 1);
});

test("turning on a tile layer computes the tiles when none are on hand", async () => {
  for (const id of TILE_LAYERS) {
    let computes = 0;
    const engine = {
      send(msg) {
        if (msg.type !== "compute") return Promise.resolve(null);
        computes++;
        return Promise.resolve({ intersection: null, legacy: null, warnings: [], timings: {} });
      },
    };
    const store = createStore(bundle(), { engine });
    await store.settled();
    assert.equal(computes, 1);
    store.setLayer("network", false);
    await store.settled();
    assert.equal(computes, 1, "the network is not a tile layer");
    store.setLayer(id, true);
    await store.settled();
    assert.equal(computes, 2, id);
  }
});

test("a nudge of nothing leaves the node unmoved", async () => {
  const store = createStore(bundle());
  await store.settled();
  store.nudgeNode(2, 0, 0);
  await store.settled();
  assert.equal(store.movedNodes().length, 0);
});

test("a nudge moves the node along the axes of the computation CRS the inspector shows", async () => {
  const store = createStore(bundle());
  await store.settled();
  const n = store.state.graph.nodes.get(2);
  // The UTM zone at the node, whose axes turn against the bundle's Albers.
  store.setParam("epsg", `EPSG:${utmZoneEpsg(n.lon, n.lat)}`);
  await store.settled();
  const [x0, y0] = [n.x, n.y];
  store.nudgeNode(2, 10, 0);
  await store.settled();
  assert.ok(Math.abs(n.x - x0 - 10) < 1e-6 && Math.abs(n.y - y0) < 1e-6, `moved by ${n.x - x0}, ${n.y - y0}`);
});

test("while a move computes, the outline still shows the last move's changes, not a parameter edit's", async () => {
  const b = bundle();
  const store = createStore(b, { engine: slowEngine(b) });
  await store.settled();
  store.setParam("gridM", 20);
  await store.settled();
  const d = store.state.diff.intersection;
  assert.ok(d.added.size + d.changed.size > 0, "the parameter edit changed tiles");
  store.nudgeNode(2, 3, 0);
  assert.equal(store.state.busy, true);
  assert.equal(store.changedTiles().size, 0, "the diff on hand came from a parameter edit");
  await store.settled();
});

test("a listener that throws once does not stop later computes", async () => {
  const store = createStore(bundle());
  await store.settled();
  let thrown = false;
  store.subscribe((state) => { if (!state.busy && !thrown) { thrown = true; throw new Error("render failed"); } });
  const swallow = () => {};
  const logged = [];
  const { error } = console;
  process.on("unhandledRejection", swallow);
  console.error = (err) => logged.push(err);
  try {
    store.setParam("minDegree", 2);
    await new Promise((r) => setTimeout(r, 50));
    store.setParam("minDegree", 3);
    await store.settled();
  } finally {
    process.off("unhandledRejection", swallow);
    console.error = error;
  }
  assert.equal(store.state.results.intersection.tiles.length, 5);
  assert.equal(store.state.busy, false);
  assert.deepEqual(logged.map((e) => e.message), ["render failed"], "the store reports the listener's error");
});

test("a request that fails to build does not drop the answer to the one in flight", async () => {
  const b = bundle();
  // With the city boundary gone, a request for the city scope throws while
  // it builds its clip.
  b.boundary = undefined;
  const store = createStore(b, { engine: slowEngine(b) });
  await store.settled();
  store.setParam("minDegree", 2);
  const { error } = console;
  console.error = () => {};
  try {
    store.setParam("buildScope", "city");
  } finally {
    console.error = error;
  }
  await store.settled();
  assert.equal(store.state.params.minDegree, 2);
  assert.equal(store.state.results.intersection.tiles.length, 9, "the minDegree 2 answer was applied");
});

test("the legacy column's median interpolates like the intersection column's", async () => {
  const b = bundle();
  const store = createStore(b);
  await store.settled();
  store.setLayer("legacy", true);
  await store.settled();
  const areas = store.state.results.legacy.tiles.map((t) => t.areaM2).sort((p, q) => p - q);
  assert.equal(areas.length % 2, 0, "this fixture has an even tile count");
  const mid = areas.length / 2;
  assert.equal(store.state.stats.legacy.medianArea, (areas[mid - 1] + areas[mid]) / 2);
});

test("a legacy tiling asked for by a superseded request still lands", async () => {
  const b = bundle();
  const store = createStore(b, { engine: slowEngine(b) });
  store.setLayer("legacy", true);
  await store.settled();
  // The clip buffer moves the legacy hull. The degree edit right after it
  // supersedes that request before its reply lands.
  store.setParam("clipBufferM", 500);
  store.setParam("minDegree", 2);
  await store.settled();
  const fresh = createStore(bundle());
  fresh.setLayer("legacy", true);
  fresh.setParam("clipBufferM", 500);
  await fresh.settled();
  assert.deepEqual(store.state.results.legacy.tiles.map((t) => t.areaM2), fresh.state.results.legacy.tiles.map((t) => t.areaM2));
});

test("the tiles land without waiting for the legacy tiling", async () => {
  const b = bundle();
  const inner = inProcessEngine(b);
  // A legacy tiling that takes far longer than the tiles.
  const engine = { send: (msg) => new Promise((r) => setTimeout(r, msg.legacy ? 300 : 10)).then(() => inner.send(msg)) };
  const store = createStore(b, { engine });
  store.setLayer("legacy", true);
  await store.settled();
  store.setParam("clipBufferM", 500);
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(store.state.diffReason, "param:clipBufferM", "the tiles for the new buffer are on hand");
  assert.equal(store.state.busy, true, "the legacy tiling is still running");
  await store.settled();
  assert.equal(store.state.busy, false);
});
