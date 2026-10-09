import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ringArea, polygonArea, multiPolygonArea, ringBounds, pointInRing,
  pointInPolygon, pointInMultiPolygon, multiPolygonCentroid, convexHull, closeRing, sharedLength,
} from "../src/engine/geometry.js";

const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]];

test("areas are unsigned and holes subtract", () => {
  assert.equal(ringArea(square), 100);
  assert.equal(ringArea([...square].reverse()), 100);
  assert.equal(polygonArea([square, hole]), 96);
  assert.equal(multiPolygonArea([[square], [hole]]), 104);
});

test("bounds", () => {
  assert.deepEqual(ringBounds(square), [0, 0, 10, 10]);
});

test("point in ring, polygon, multipolygon", () => {
  assert.equal(pointInRing([5, 5], square), true);
  assert.equal(pointInRing([15, 5], square), false);
  assert.equal(pointInPolygon([3, 3], [square, hole]), false);
  assert.equal(pointInPolygon([5, 5], [square, hole]), true);
  assert.equal(pointInMultiPolygon([3, 3], [[square, hole], [[[20, 20], [30, 20], [30, 30], [20, 20]]]]), false);
  assert.equal(pointInMultiPolygon([25, 21], [[square, hole], [[[20, 20], [30, 20], [30, 30], [20, 20]]]]), true);
});

test("centroid of a multipolygon is area-weighted", () => {
  const [cx, cy] = multiPolygonCentroid([[square]]);
  assert.ok(Math.abs(cx - 5) < 1e-9 && Math.abs(cy - 5) < 1e-9);
  const other = [[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]];
  const [mx, my] = multiPolygonCentroid([[square], [other]]);
  assert.ok(Math.abs(mx - 15) < 1e-9 && Math.abs(my - 5) < 1e-9);
});

test("convex hull is a closed counter-clockwise ring", () => {
  const pts = [[0, 0], [4, 0], [4, 4], [0, 4], [2, 2], [1, 3]];
  const hull = convexHull(pts);
  assert.equal(hull.length, 5);
  assert.deepEqual(hull[0], hull[4]);
  assert.equal(ringArea(hull), 16);
});

test("closeRing repeats the first point once", () => {
  assert.deepEqual(closeRing([[0, 0], [1, 0], [1, 1]]), [[0, 0], [1, 0], [1, 1], [0, 0]]);
  assert.deepEqual(closeRing(square), square);
});

test("a sliver's area far from the CRS origin keeps its digits", () => {
  // A 0.1 m by 0.17 m piece where Seattle sits in EPSG:5070.
  const [x, y] = [-1964673.19, 3020659.34];
  const ring = [[x, y], [x + 0.1, y], [x + 0.1, y + 0.17], [x, y + 0.17], [x, y]];
  assert.ok(Math.abs(ringArea(ring) - 0.017) < 1e-9, `${ringArea(ring)}`);
});

test("sharedLength counts a stretch of boundary once, even along a hairline's two edges", () => {
  const piece = [[[0, 0], [50, 0], [50, -10], [0, -10], [0, 0]]];
  const hairline = [[[0, 0], [50, 0], [50, 1e-9], [0, 1e-9], [0, 0]]];
  assert.ok(Math.abs(sharedLength([piece], [hairline]) - 50) < 1e-6);
});
