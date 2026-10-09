import { test } from "node:test";
import assert from "node:assert/strict";
import { circleRadiusExpression, mergeFeatures, consolidationCandidates } from "../src/app/consolidation.js";
import { createStore } from "../src/app/state.js";
import { appBundle as bundle, carriagewayJoinBundle, junctionWithSlipLaneEndBundle, slowEngine } from "./fixtures.js";
import { utmZoneEpsg } from "../src/engine/proj.js";

test("circleRadiusExpression draws a meter radius that scales with zoom at the city's latitude", () => {
  const expr = circleRadiusExpression(5, 46.7);
  assert.equal(expr[0], "interpolate");
  assert.deepEqual(expr[1], ["exponential", 2]);
  assert.deepEqual(expr[2], ["zoom"]);
  // MapLibre's 512 px tiles: 78,271.517 m per pixel at zoom 0 on the equator, so
  // 5 m at zoom 15 and 46.7 deg north is 3.052 px.
  const pxAt = (z) => expr[4] * 2 ** z;
  assert.ok(Math.abs(pxAt(15) - 3.052) < 0.005, `${pxAt(15)}`);
  assert.ok(Math.abs(expr[6] / expr[4] - 2 ** 24) < 1e-6, "the two stops are 24 doublings apart");
});

test("mergeFeatures links every member of a merged cluster to the cluster centroid", async () => {
  const store = createStore(bundle());
  await store.settled();
  assert.equal(mergeFeatures(store.state).links.features.length, 0, "nothing merges at 5 m on a 100 m grid");
  store.setParam("toleranceM", 60);
  await store.settled();
  const merged = store.state.results.intersection.clusters.filter((c) => c.members.length > 1);
  assert.ok(merged.length >= 1, "a 60 m buffer merges 100 m neighbors");
  const f = mergeFeatures(store.state);
  assert.equal(f.links.features.length, merged.reduce((n, c) => n + c.members.length, 0));
  assert.equal(f.centroids.features.length, merged.length);
  assert.equal(f.links.features[0].geometry.type, "LineString");
});

test("a dead end is never a consolidation candidate; the junction it hangs off is", async () => {
  const store = createStore(bundle());
  await store.settled();
  const candidates = consolidationCandidates(store.state);
  assert.equal(candidates.has(99), false, "the stub's end");
  assert.equal(candidates.has(5), true, "the junction it hangs off");
  assert.equal(candidates.size, 9, "every grid node, and nothing else");
});

test("a carriageway transition is a candidate only while contraction is off", async () => {
  const store = createStore(bundle(carriagewayJoinBundle()));
  await store.settled();
  assert.equal(store.state.params.contractTransitions, true, "the app opens with contraction on");
  assert.equal(consolidationCandidates(store.state).has(3), false, "on: the join is contracted, not merged");
  store.setParam("contractTransitions", false);
  await store.settled();
  assert.equal(consolidationCandidates(store.state).has(3), true, "off: the join merges like any junction");
  store.setParam("contractTransitions", true);
  await store.settled();
  assert.equal(consolidationCandidates(store.state).has(3), false, "on again: contracted");
});

test("a link node that is no junction draws no buffer disc", async () => {
  const store = createStore(bundle(junctionWithSlipLaneEndBundle()));
  await store.settled();
  const candidates = consolidationCandidates(store.state);
  assert.equal(candidates.has(7), false, "the slip lane's end");
  assert.equal(candidates.has(2), true, "the junction beside it");
});

test("while a CRS switch computes, the merge overlay places cluster centers in the CRS they were computed in", async () => {
  const b = bundle();
  const store = createStore(b, { engine: slowEngine(b) });
  store.setParam("toleranceM", 60);
  await store.settled();
  const before = mergeFeatures(store.state).centroids.features[0].geometry.coordinates;
  const n = store.state.graph.nodes.get(2);
  store.setParam("epsg", `EPSG:${utmZoneEpsg(n.lon, n.lat)}`);
  assert.equal(store.state.busy, true);
  const during = mergeFeatures(store.state).centroids.features[0].geometry.coordinates;
  assert.ok(Math.abs(during[0] - before[0]) < 1e-9 && Math.abs(during[1] - before[1]) < 1e-9);
  await store.settled();
});
