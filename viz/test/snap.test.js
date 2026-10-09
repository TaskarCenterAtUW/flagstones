import { test } from "node:test";
import assert from "node:assert/strict";
import { roundHalfEven, tileCell, snapSeed, mintNativeId, collisionBound, validateParams, gridOverBoundWarning, crsToken } from "../src/engine/snap.js";

test("round half to even matches Python's round()", () => {
  assert.equal(roundHalfEven(0.5), 0);
  assert.equal(roundHalfEven(1.5), 2);
  assert.equal(roundHalfEven(2.5), 2);
  assert.equal(roundHalfEven(-0.5), -0);
  assert.equal(roundHalfEven(-1.5), -2);
  assert.equal(roundHalfEven(2.4999), 2);
  assert.equal(roundHalfEven(2.5001), 3);
});

test("a sub-grid move leaves the cell and the snapped seed unchanged", () => {
  assert.deepEqual(tileCell(1002.4, 998.0, 5), tileCell(1000.1, 1001.9, 5));
  assert.deepEqual(snapSeed(1002.4, 998.0, 5), [1000, 1000]);
});

test("the cell changes across a grid line", () => {
  assert.notDeepEqual(tileCell(1002.4, 1000, 5), tileCell(1002.6, 1000, 5));
});

test("the id is the readable natural key and survives negative eastings", () => {
  assert.equal(mintNativeId(200, 200, 5070), "5070:200:200");
  assert.equal(mintNativeId(-393598, 601822, 5070), "5070:-393598:601822");
});

test("the collision bound is tolerance times root two, and the grid may exceed it", () => {
  assert.ok(Math.abs(collisionBound(5) - 7.0710678) < 1e-6);
  // The engine runs an over-bound grid, the UI warns, and the map highlights
  // any seed collision. Only a value that is not positive is an engine error.
  validateParams({ toleranceM: 5, gridM: 5 });
  validateParams({ toleranceM: 5, gridM: 7.0710678118654755 });
  validateParams({ toleranceM: 5, gridM: 8 });
  assert.throws(() => validateParams({ toleranceM: 0, gridM: 5 }), RangeError);
  assert.throws(() => validateParams({ toleranceM: 5, gridM: 0 }), RangeError);
});

test("gridOverBoundWarning is silent below the bound and names it above", () => {
  assert.equal(gridOverBoundWarning(5, 5), null, "below the bound: no warning");
  const w = gridOverBoundWarning(5, 8);
  assert.match(w, /collision bound/);
  assert.match(w, /7\.07/);
});

test("the id token follows the library's crs_id_token", () => {
  assert.equal(mintNativeId(1, 2, 5070), "5070:1:2");
  assert.equal(mintNativeId(1, 2, "EPSG:5070"), "5070:1:2");
  assert.equal(mintNativeId(1, 2, "ESRI:54034"), "esri54034:1:2");
});

test("crsToken splits at the first colon only, as the library's crs_id_token does", () => {
  assert.equal(crsToken("urn:ogc:def:crs:EPSG::5070"), "urnogc:def:crs:EPSG::5070");
  assert.equal(crsToken("ESRI:54034"), "esri54034");
  assert.equal(crsToken(5070), "5070");
});
