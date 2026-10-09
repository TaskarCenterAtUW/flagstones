import { test } from "node:test";
import assert from "node:assert/strict";
import { LAYER_KEY, MAP_LAYERS, PAINT } from "../src/app/layers.js";

test("every toggled map layer has a key entry, and the edit layers are toggled", () => {
  const keyIds = new Set(LAYER_KEY.map((k) => k.id));
  const byId = Object.fromEntries(MAP_LAYERS.map((l) => [l.id, l]));
  assert.equal(byId["changed-line"].toggle, "changedTiles");
  assert.equal(byId["moved-nodes"].toggle, "movedNodes");
  for (const l of MAP_LAYERS) if (l.toggle) assert.ok(keyIds.has(l.toggle), `${l.id} toggles ${l.toggle}, which the key does not list`);
});

test("the consolidation overlay is one toggled key entry", () => {
  const byId = Object.fromEntries(MAP_LAYERS.map((l) => [l.id, l]));
  for (const id of ["node-buffers", "merge-links", "merge-centroids"]) assert.equal(byId[id]?.toggle, "consolidation", id);
  assert.ok(LAYER_KEY.some((k) => k.id === "consolidation"));
});

test("seed-collision tiles draw a red outline on top of the tiles, shown by default", () => {
  const byId = Object.fromEntries(MAP_LAYERS.map((l) => [l.id, l]));
  const fill = byId["collision-fill"], line = byId["collision-line"];
  assert.ok(fill && line, "fill and line layers exist");
  assert.equal(line.source, "collision-tiles", "the outline reads the flagged features");
  assert.ok(PAINT.danger, "a danger paint token exists");
  assert.equal(line.paint["line-color"], PAINT.danger);
  assert.ok(line.paint["line-width"] >= 2.5, "thicker than a normal tile outline");
  assert.equal(fill.paint["fill-color"], PAINT.danger);
  // The highlight toggles with the tiles, so it has no key entry of its own.
  assert.equal(line.toggle, "tiles");
  assert.ok(MAP_LAYERS.indexOf(fill) > MAP_LAYERS.indexOf("intersection-fill"), "fill sits above the tile fill");
  assert.ok(MAP_LAYERS.indexOf(line) > MAP_LAYERS.indexOf("intersection-line"), "line sits above the tile outline");
});

test("consolidation buffers draw only on nodes the engine will merge", () => {
  const byId = Object.fromEntries(MAP_LAYERS.map((l) => [l.id, l]));
  assert.deepEqual(byId["node-buffers"].filter, ["==", ["get", "eligible"], true]);
});
