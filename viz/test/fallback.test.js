import { test } from "node:test";
import assert from "node:assert/strict";
import { fallbackTiles, mintFallbackId } from "../src/engine/fallback.js";
import { sharedLength } from "../src/engine/geometry.js";

const box = (x0, y0, x1, y1) => [[[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]];
const tile = (geometry) => ({ nativeId: "5070:0:0", geometry });
const near = (a, b) => Math.abs(a - b) < 1e-6;
const grid = { crs: 5070, cellM: 250, minShare: 0.1 };

test("the id follows the library's mint_fallback_id", () => {
  assert.equal(mintFallbackId(1666, 21958, "EPSG:32610"), "32610:f:1666:21958");
  assert.equal(mintFallbackId(-7873, 12038, 5070), "5070:f:-7873:12038");
  assert.equal(mintFallbackId(3, 4, "ESRI:54034"), "esri54034:f:3:4");
});

test("an extent with no tiles is cut into whole cells", () => {
  const cells = fallbackTiles([], { extent: box(0, 0, 500, 250), ...grid });
  assert.deepEqual(cells.map((c) => c.nativeId), ["5070:f:0:0", "5070:f:1:0"]);
  assert.ok(cells.every((c) => near(c.area, 62500) && c.kind === "fallback"));
  assert.deepEqual([cells[0].seedX, cells[0].seedY], [125, 125]);
});

test("cells index by floor on both sides of the origin", () => {
  const cells = fallbackTiles([], { extent: box(-10, -10, 10, 10), ...grid });
  assert.deepEqual(cells.map((c) => [c.cellX, c.cellY]), [[-1, -1], [-1, 0], [0, -1], [0, 0]]);
});

test("a sliver joins the piece it shares the longest boundary with", () => {
  const cells = fallbackTiles([], { extent: box(0, 0, 500, 260), ...grid });
  assert.deepEqual(cells.map((c) => c.nativeId), ["5070:f:0:0", "5070:f:1:0"]);
  assert.ok(cells.every((c) => near(c.area, 65000) && c.geometry.length === 1));
});

test("a cell the tiles split stays one multipolygon tile", () => {
  const cells = fallbackTiles([tile(box(0, 100, 250, 150))], { extent: box(0, 0, 250, 250), ...grid });
  assert.equal(cells.length, 1);
  assert.equal(cells[0].geometry.length, 2);
  assert.ok(near(cells[0].area, 50000));
});

test("a cell the tiles cover has no fallback tile", () => {
  const cells = fallbackTiles([tile(box(250, 0, 500, 250))], { extent: box(0, 0, 500, 250), ...grid });
  assert.deepEqual(cells.map((c) => c.nativeId), ["5070:f:0:0"]);
});

test("a hole with no fallback neighbor becomes its cell's tile alone", () => {
  const walls = [[[[0, 0], [250, 0], [250, 250], [0, 250], [0, 0]], [[100, 100], [100, 110], [110, 110], [110, 100], [100, 100]]]];
  const cells = fallbackTiles([tile(walls)], { extent: box(0, 0, 250, 250), ...grid });
  assert.equal(cells.length, 1);
  assert.ok(near(cells[0].area, 100));
});

test("a sliver with no fallback neighbor joins its cell's body", () => {
  const cells = fallbackTiles([tile(box(230, 0, 240, 250))], { extent: box(0, 0, 250, 250), ...grid });
  assert.deepEqual(cells.map((c) => c.nativeId), ["5070:f:0:0"]);
  assert.equal(cells[0].geometry.length, 2);
  assert.ok(near(cells[0].area, 230 * 250 + 10 * 250));
});

test("shared length counts collinear overlap where the vertices differ", () => {
  assert.ok(near(sharedLength(box(0, 0, 10, 10), box(10, -5, 20, 20)), 10));
  assert.equal(sharedLength(box(0, 0, 10, 10), box(20, 0, 30, 10)), 0);
});

test("a bad grid is refused", () => {
  assert.throws(() => fallbackTiles([], { extent: box(0, 0, 1, 1), ...grid, cellM: 0 }), /cellM/);
  assert.throws(() => fallbackTiles([], { extent: box(0, 0, 1, 1), ...grid, minShare: 1 }), /minShare/);
});

test("fallbackTiles refuses an infinite cell size as it refuses a negative one", () => {
  assert.throws(() => fallbackTiles([], { extent: [[[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]], crs: 5070, cellM: Infinity, minShare: 0.1 }), RangeError);
});
