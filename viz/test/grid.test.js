import { test } from "node:test";
import assert from "node:assert/strict";
import { gridLinesInBox, gridFeatures } from "../src/app/grid.js";
import { createStore } from "../src/app/state.js";
import { appBundle, slowEngine } from "./fixtures.js";
import { forward } from "../src/engine/proj.js";

test("cell boundaries fall on odd multiples of half the grid", () => {
  const lines = gridLinesInBox([0, 0, 20, 10], 5);
  const xs = lines.filter((l) => l.axis === "x").map((l) => l.x0);
  const ys = lines.filter((l) => l.axis === "y").map((l) => l.y0);
  assert.deepEqual(xs, [2.5, 7.5, 12.5, 17.5]);
  assert.deepEqual(ys, [2.5, 7.5]);
  for (const l of lines) assert.equal(l.axis === "x" ? l.x1 : l.y1, l.axis === "x" ? l.x0 : l.y0);
});

test("a cell index maps to the square between its boundaries", () => {
  const lines = gridLinesInBox([-10, -10, 10, 10], 5);
  const xs = lines.filter((l) => l.axis === "x").map((l) => l.x0);
  assert.ok(xs.includes(-2.5) && xs.includes(2.5), "cell 0 spans -2.5 to 2.5");
});

test("while a grid edit computes, the grid overlay draws the grid the tiles on hand were seeded on", async () => {
  const b = appBundle();
  const store = createStore(b, { engine: slowEngine(b) });
  await store.settled();
  store.setParam("gridM", 20);
  assert.equal(store.state.busy, true);
  const { cells } = gridFeatures(store.state, [-96.01, 22.99, -95.99, 23.01]);
  const [a, c] = cells.features[0].geometry.coordinates[0];
  const [x0] = forward(b.city.crs, ...a), [x1] = forward(b.city.crs, ...c);
  assert.ok(Math.abs(x1 - x0 - 24) < 1e-6, `a ${x1 - x0} m cell`);
  await store.settled();
});
