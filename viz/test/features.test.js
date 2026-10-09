import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../src/app/state.js";
import { createSourceData } from "../src/app/features.js";
import { utmZoneEpsg } from "../src/engine/proj.js";
import { appBundle, slowEngine } from "./fixtures.js";

test("a notification with nothing new hands every source the data it had", async () => {
  const store = createStore(appBundle());
  await store.settled();
  const sourceData = createSourceData(store);
  const before = sourceData(store.state);
  store.select(null);
  const after = sourceData(store.state);
  for (const [id, data] of before) assert.equal(after.get(id), data, id);
});

test("while a CRS switch computes, the map keeps the tiles where they were computed", async () => {
  const b = appBundle();
  const store = createStore(b, { engine: slowEngine(b) });
  await store.settled();
  const sourceData = createSourceData(store);
  const before = sourceData(store.state);
  const n = store.state.graph.nodes.get(2);
  store.setParam("epsg", `EPSG:${utmZoneEpsg(n.lon, n.lat)}`);
  assert.equal(store.state.busy, true);
  const during = sourceData(store.state);
  for (const id of ["intersection-tiles", "intersection-seeds", "fallback-tiles"]) assert.equal(during.get(id), before.get(id), id);
  await store.settled();
});

test("a selected legacy tile is marked at its seed", async () => {
  const store = createStore(appBundle());
  store.setLayer("legacy", true);
  await store.settled();
  const t = store.state.results.legacy.tiles[0];
  store.select({ kind: "tile", variant: "legacy", id: t.nodeId });
  const { features } = createSourceData(store)(store.state).get("selection");
  assert.equal(features.length, 1);
  assert.deepEqual(features[0].geometry.coordinates, [t.seedLon, t.seedLat]);
});

test("a node move rebuilds the node features and marks the node", async () => {
  const store = createStore(appBundle());
  await store.settled();
  const sourceData = createSourceData(store);
  const before = sourceData(store.state);
  const n = store.state.graph.nodes.get(2);
  store.moveNode(2, n.lon + 1e-5, n.lat);
  const after = sourceData(store.state);
  assert.notEqual(after.get("network-nodes"), before.get("network-nodes"));
  assert.equal(after.get("network-nodes").features.find((f) => f.id === 2).properties.moved, true);
  assert.deepEqual(after.get("moved-nodes").features.map((f) => f.properties.id), [2]);
  assert.equal(after.get("network-edges"), before.get("network-edges"), "edges keep the bundle's geometry");
  await store.settled();
});
