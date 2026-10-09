import { test } from "node:test";
import assert from "node:assert/strict";
import { PARAM_CONTROLS, clipBufferStops, stepIndex } from "../src/app/params-panel.js";
import { appBundle } from "./fixtures.js";
import { collisionBound } from "../src/engine/snap.js";

test("every control names a store parameter and a kind", () => {
  for (const c of PARAM_CONTROLS) {
    assert.ok(["range", "steps", "select", "switch", "radio"].includes(c.kind), c.name);
    assert.ok(c.label && c.help, c.name);
  }
  assert.deepEqual(PARAM_CONTROLS.map((c) => c.name), [
    "toleranceM", "gridM", "minDegree", "keepDeadEnds", "contractTransitions", "clipBufferM", "fallbackCellM", "buildScope", "epsg",
  ]);
});

test("the clip buffer is a stepped slider over the exported buffers", () => {
  const c = PARAM_CONTROLS.find((c) => c.name === "clipBufferM");
  assert.equal(c.kind, "steps");
  assert.equal(c.unit, "m");
});

test("the clip buffer stops include a reference's own non-standard buffer", () => {
  const b = appBundle();
  b.clips.node_union["300"] = b.clips.node_union["250"];
  b.clips.boundary["300"] = b.clips.boundary["250"];
  assert.deepEqual(clipBufferStops(b), [100, 250, 300, 500, 1000]);
});

test("the seed grid slider reaches the collision bound of the tolerance slider", () => {
  const tol = PARAM_CONTROLS.find((c) => c.name === "toleranceM");
  const grid = PARAM_CONTROLS.find((c) => c.name === "gridM");
  const bound = collisionBound(tol.max);
  const representable = Math.floor(bound / grid.step) * grid.step;
  assert.ok(grid.max < bound, `grid max ${grid.max} must stay below the collision bound ${bound}`);
  assert.ok(bound - grid.max <= grid.step, `grid max ${grid.max} must reach within one step of the bound ${bound}, not below representable ${representable}`);
});

test("the fallback cell slider offers off, then 100 m to 1000 m in 25 m steps", () => {
  const c = PARAM_CONTROLS.find((c) => c.name === "fallbackCellM");
  assert.equal(c.kind, "steps");
  assert.equal(c.unit, "m");
  assert.deepEqual(c.stops.slice(0, 3), [0, 100, 125]);
  assert.equal(c.stops.at(-1), 1000);
  assert.ok(c.stops.includes(250), "the library's default is a stop");
});

test("stepIndex picks the nearest stop", () => {
  const stops = [100, 250, 500, 1000];
  assert.equal(stepIndex(stops, 500), 2);
  assert.equal(stepIndex(stops, 400), 2);
  assert.equal(stepIndex(stops, 120), 0);
  assert.equal(stepIndex(stops, 5000), 3);
});

test("the build scope offers five rules, labeled without asides", () => {
  const c = PARAM_CONTROLS.find((c) => c.name === "buildScope");
  assert.deepEqual(c.options.map((o) => o.value).sort(), ["bbox", "boundary", "city", "hull", "union"]);
  for (const o of c.options) assert.ok(!/\(/.test(o.label), o.label);
});
