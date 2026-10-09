import { test } from "node:test";
import assert from "node:assert/strict";
import { tessellate, clipTree, localClip, attachDetachedPieces } from "../src/engine/voronoi.js";
import polygonClipping from "polygon-clipping";
import { multiPolygonArea, multiPolygonBounds, pointInMultiPolygon } from "../src/engine/geometry.js";

const CLIP = [[[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]];
const seed = (x, y, members = [1], degree = 4) => ({ x, y, members, degree, index: 0 });

test("tiles partition the clip region and each contains its seed", () => {
  const tiles = tessellate([seed(20, 20), seed(80, 20), seed(50, 80)], { clip: CLIP, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 3);
  const total = tiles.reduce((s, t) => s + t.area, 0);
  assert.ok(Math.abs(total - 10000) < 1e-6);
  for (const t of tiles) assert.ok(pointInMultiPolygon([t.seedX, t.seedY], t.geometry));
});

test("with snapCentroid off the cells bisect the raw centroids under the same ids", () => {
  const seeds = [seed(21, 22), seed(78, 19)];
  const snapped = tessellate(seeds, { clip: CLIP, gridM: 5, epsg: 5070 });
  const raw = tessellate(seeds, { clip: CLIP, gridM: 5, epsg: 5070, snapCentroid: false });
  assert.deepEqual(raw.map((t) => t.nativeId), snapped.map((t) => t.nativeId));
  assert.deepEqual(raw.map((t) => [t.seedX, t.seedY]), [[21, 22], [78, 19]]);
  assert.deepEqual(snapped.map((t) => [t.seedX, t.seedY]), [[20, 20], [80, 20]]);
  assert.ok(Math.abs(raw[0].area - snapped[0].area) > 1, "the bisector follows the raw centroids");
});

test("a non-convex clip truncates only the cells that cross it", () => {
  const L = [[[[0, 0], [100, 0], [100, 40], [40, 40], [40, 100], [0, 100], [0, 0]]]];
  const tiles = tessellate([seed(20, 20), seed(80, 20), seed(20, 80), seed(80, 80)], { clip: L, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 3, "the cell around (80,80) lies wholly outside the L");
  const total = tiles.reduce((s, t) => s + t.area, 0);
  assert.ok(Math.abs(total - multiPolygonArea(L)) < 1e-6);
});

test("two seeds in one cell collapse to a single flagged tile", () => {
  const seeds = [seed(20, 20, [1]), seed(21, 22, [2])];
  const tiles = tessellate(seeds, { clip: CLIP, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 1);
  assert.ok(tiles[0].seedCollision, "the collapsed tile is flagged");
  assert.deepEqual([...tiles[0].osmids].sort(), [1, 2]);
  assert.equal(tiles[0].cellX, 4);
  assert.equal(tiles[0].cellY, 4);
});

test("no seed collision when the seeds keep their own cells", () => {
  const tiles = tessellate([seed(20, 20, [1]), seed(80, 20, [2])], { clip: CLIP, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 2);
  for (const t of tiles) assert.ok(!t.seedCollision);
});

test("output is sorted by cell and ids are the cells", () => {
  const tiles = tessellate([seed(80, 20), seed(20, 80), seed(20, 20)], { clip: CLIP, gridM: 5, epsg: 5070 });
  assert.deepEqual(tiles.map((t) => t.nativeId), ["5070:4:4", "5070:4:16", "5070:16:4"]);
});

test("two runs are identical and a sub-grid move changes nothing", () => {
  const a = tessellate([seed(20, 20), seed(80, 20)], { clip: CLIP, gridM: 5, epsg: 5070 });
  const b = tessellate([seed(21.2, 18.9), seed(80, 20)], { clip: CLIP, gridM: 5, epsg: 5070 });
  assert.deepEqual(a.map((t) => [t.nativeId, t.area, t.geometry]), b.map((t) => [t.nativeId, t.area, t.geometry]));
});

test("no seeds, no tiles", () => {
  assert.deepEqual(tessellate([], { clip: CLIP, gridM: 5, epsg: 5070 }), []);
});

// A 10 km square whose right edge zigzags every 5 m: a boundary of about
// 4,000 vertices, the shape of a buffered node union at city scale.
function jaggedClip(sizeM = 10000, stepM = 5, amplitudeM = 20) {
  const ring = [[0, 0], [sizeM, 0]];
  for (let y = stepM, k = 0; y < sizeM; y += stepM, k++) ring.push([sizeM + (k % 2 ? amplitudeM : -amplitudeM), y]);
  ring.push([sizeM, sizeM], [0, sizeM], [0, 0]);
  return [[ring]];
}

// A 5 km circle drawn with 20,000 vertices: a long boundary with no stacked
// segments, so polygon-clipping can finish an intersection against all of
// it (a zigzag this fine overflows its recursion), which is what the timing
// guard compares against.
function roundClip(vertices = 20000, r = 5000) {
  const ring = [];
  for (let k = 0; k < vertices; k++) {
    const a = (2 * Math.PI * k) / vertices;
    ring.push([5000 + r * Math.cos(a), 5000 + r * Math.sin(a)]);
  }
  ring.push(ring[0]);
  return [[ring]];
}

test("localClip hands a boundary cell a small piece of the clip that clips identically", () => {
  const clip = jaggedClip();
  const tree = clipTree(clip);
  const cell = [[9900, 7000], [10050, 7000], [10050, 7150], [9900, 7150], [9900, 7000]];
  const piece = localClip(tree, [9900, 7000, 10050, 7150]);
  const pieceVertices = piece.reduce((n, poly) => n + poly.reduce((m, r) => m + r.length, 0), 0);
  assert.ok(pieceVertices > 0 && pieceVertices < 200, `piece has ${pieceVertices} vertices, the clip has ${clip[0][0].length}`);
  const local = multiPolygonArea(polygonClipping.intersection([[cell]], piece));
  const full = multiPolygonArea(polygonClipping.intersection([[cell]], clip));
  assert.ok(Math.abs(local - full) < 1e-6, `${local} vs ${full}`);
  assert.deepEqual(localClip(tree, [12000, 5000, 12100, 5100]), [], "a box outside the clip gets nothing");
});

test("tessellating against a long boundary costs the clip near each cell, not the whole clip", () => {
  // Intersecting each boundary cell against the whole clip overruns the
  // guard below several times over. Against the clip near the cell, the
  // run fits well inside it.
  const clip = roundClip();
  const seeds = [];
  for (let x = 100, i = 0; x < 10000; x += 180) for (let y = 100; y < 10000; y += 180, i++) seeds.push(seed(x, y, [i + 1]));
  const t0 = performance.now();
  const tiles = tessellate(seeds, { clip, gridM: 5, epsg: 5070 });
  const ms = performance.now() - t0;
  assert.ok(tiles.length > 0 && tiles.length < seeds.length, "the corners of the lattice fall outside the circle");
  assert.ok(Math.abs(tiles.reduce((s, t) => s + t.area, 0) - multiPolygonArea(clip)) < 1e-3, "the tiles still partition the clip");
  assert.ok(ms < 4000, `${ms.toFixed(0)} ms for ${seeds.length} seeds against a ${clip[0][0].length}-vertex clip`);
});

test("cells inside a hole tile nothing, and cells across its edge are cut by it", () => {
  const withHole = [[[[0, 0], [300, 0], [300, 300], [0, 300], [0, 0]], [[100, 100], [200, 100], [200, 200], [100, 200], [100, 100]]]];
  const seeds = [];
  for (let x = 12.5, i = 0; x < 300; x += 25) for (let y = 12.5; y < 300; y += 25, i++) seeds.push(seed(x, y, [i + 1]));
  const tiles = tessellate(seeds, { clip: withHole, gridM: 5, epsg: 5070 });
  assert.ok(Math.abs(tiles.reduce((s, t) => s + t.area, 0) - 80000) < 1e-6, "the hole is not tiled");
  assert.ok(tiles.every((t) => !(t.rawX > 100 && t.rawX < 200 && t.rawY > 100 && t.rawY < 200)), "no seed inside the hole keeps a tile");
  assert.ok(tiles.some((t) => t.truncated), "cells across the hole's edge are truncated");
});

// Across a 50 m gap, a clip can have two lobes, the way a buffered node
// union has lobes wherever the network thins out.
const LOBES = [[[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]], [[[150, 0], [250, 0], [250, 100], [150, 100], [150, 0]]]];

test("a piece of a cell cut off across a gap in the clip goes to the tile beside it", () => {
  // Since the bisector of the seeds runs at x = 165, the left seed's
  // cell reaches 15 m into the right lobe. That strip belongs to the
  // right lobe's tile.
  const tiles = tessellate([seed(90, 50, [1]), seed(240, 50, [2])], { clip: LOBES, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 2);
  for (const t of tiles) assert.equal(t.geometry.length, 1, `${t.nativeId} is one polygon`);
  const left = tiles.find((t) => t.osmids[0] === 1), right = tiles.find((t) => t.osmids[0] === 2);
  assert.ok(Math.abs(left.area - 10000) < 1e-6, `left ${left.area}`);
  assert.ok(Math.abs(right.area - 10000) < 1e-6, `right ${right.area}`);
  assert.ok(pointInMultiPolygon([left.seedX, left.seedY], left.geometry));
});

test("a cut-off piece with no tile beside it is dropped", () => {
  // Only the left lobe has a seed. Its cell reaches over the right lobe too,
  // and with no tile there to take the piece, the tile is the left lobe alone.
  const tiles = tessellate([seed(90, 50, [1])], { clip: LOBES, gridM: 5, epsg: 5070 });
  assert.equal(tiles.length, 1);
  assert.equal(tiles[0].geometry.length, 1);
  assert.ok(Math.abs(tiles[0].area - 10000) < 1e-6, `${tiles[0].area}`);
});

// Hand-drawn tiles for the attach rules, which read only the ID, the
// geometry, and the seed. The library tests' _piece_tile, same coordinates.
const pieceTile = (nativeId, seed, boxes) => {
  const geometry = boxes.map(([x0, y0, x1, y1]) => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]);
  return { nativeId, seedX: seed[0], seedY: seed[1], geometry, area: multiPolygonArea(geometry) };
};

const boundsBy = (tiles) => Object.fromEntries(tiles.map((t) =>
  [t.nativeId, multiPolygonBounds(t.geometry).map((v) => Math.round(v * 1e6) / 1e6)]));

test("a tile that gives and takes in one pass sheds what it gave", () => {
  // Three tiles in a row: the middle tile passes a piece cut off from its
  // seed to the right-hand tile and takes the left-hand tile's cut-off
  // piece in the same pass. A union rebuilds the receiver's polygon objects,
  // so a tile that received before donating would no longer find what it
  // gave to shed, and the piece would ride on both tiles.
  const tiles = [
    pieceTile("a", [0.5, 0.5], [[0, 0, 1, 1], [4, 0, 5, 1]]),
    pieceTile("b", [3.5, 0.5], [[3, 0, 4, 1], [5, 0, 6, 1]]),
    pieceTile("c", [6.5, 0.5], [[6, 0, 7, 1]]),
  ];
  const t0 = performance.now();
  attachDetachedPieces(tiles);
  assert.ok(performance.now() - t0 < 1000, "the loop terminates");
  for (const t of tiles) assert.equal(t.geometry.length, 1, `${t.nativeId} is one polygon`);
  for (const t of tiles) assert.ok(pointInMultiPolygon([t.seedX, t.seedY], t.geometry), `${t.nativeId} holds its seed`);
  const total = tiles.reduce((s, t) => s + t.area, 0);
  const combined = multiPolygonArea(polygonClipping.union(...tiles.map((t) => t.geometry)));
  assert.ok(Math.abs(total - combined) < 1e-9, `${total} vs ${combined}, no ground duplicated`);
  assert.deepEqual(boundsBy(tiles), { a: [0, 0, 1, 1], b: [3, 0, 5, 1], c: [5, 0, 7, 1] });
});

test("a detached piece between two equal neighbors goes to the lower id", () => {
  const tiles = [
    pieceTile("x", [0.5, 0.5], [[0, 0, 1, 1], [5, 0, 6, 2]]),
    pieceTile("c", [6.5, 1], [[6, 0, 7, 2]]),
    pieceTile("b", [4.5, 1], [[4, 0, 5, 2]]),
  ];
  attachDetachedPieces(tiles);
  assert.deepEqual(boundsBy(tiles), { x: [0, 0, 1, 1], b: [4, 0, 6, 2], c: [6, 0, 7, 2] });
});

test("a piece that touches only another detached piece waits a pass", () => {
  const tiles = [
    pieceTile("a", [0.5, 0.5], [[0, 0, 1, 1], [5, 0, 6, 1]]),
    pieceTile("b", [10.5, 0.5], [[10, 0, 11, 1], [6, 0, 7, 1]]),
    pieceTile("c", [7.5, 0.5], [[7, 0, 8, 1]]),
  ];
  attachDetachedPieces(tiles);
  assert.deepEqual(boundsBy(tiles), { a: [0, 0, 1, 1], b: [10, 0, 11, 1], c: [5, 0, 8, 1] });
});

test("a union that stays in two parts ends the attach loop", () => {
  // Here, polygon-clipping merges every adjacent pair, so the precision case of
  // a receiver that keeps the piece a separate part needs the union swapped for
  // one that never merges. A piece that lands on no tile must not pass back and
  // forth between two neighbors without end. The loop stops once a pass leaves
  // as many detached pieces as the one before, and the stranded piece drops
  // like any piece no tile takes.
  const realUnion = polygonClipping.union;
  polygonClipping.union = (...inputs) => inputs.flat();
  try {
    const tiles = [
      pieceTile("x", [0.5, 0.5], [[0, 0, 1, 1], [5, 0, 6, 2]]),
      pieceTile("c", [6.5, 1], [[6, 0, 7, 2]]),
      pieceTile("b", [4.5, 1], [[4, 0, 5, 2]]),
    ];
    const t0 = performance.now();
    attachDetachedPieces(tiles);
    assert.ok(performance.now() - t0 < 1000, "the loop terminates");
    assert.deepEqual(boundsBy(tiles), { x: [0, 0, 1, 1], b: [4, 0, 5, 2], c: [6, 0, 7, 2] });
  } finally {
    polygonClipping.union = realUnion;
  }
});

test("a piece finds its receiver across a crossing an ulp apart", () => {
  // As on Seattle's 100 m node union, the sides of the crossing come
  // from separate clip operations.
  const x = -1972961.374879449, xNext = -1972961.3748794491;
  assert.notEqual(x, xNext);
  const tiles = [
    { nativeId: "a", seedX: x - 50, seedY: 5, geometry: [[[[x - 51, 0], [x - 49, 0], [x - 49, 10], [x - 51, 10], [x - 51, 0]]], [[[x - 10, 0], [x, 0], [x, 10], [x - 10, 10], [x - 10, 0]]]] },
    { nativeId: "b", seedX: xNext + 5, seedY: 5, geometry: [[[[xNext, 0], [xNext + 10, 0], [xNext + 10, 10], [xNext, 10], [xNext, 0]]]] },
  ];
  attachDetachedPieces(tiles);
  assert.equal(tiles[0].geometry.length, 1);
  assert.ok(Math.abs(tiles[1].area - 200) < 1e-6, `b holds ${tiles[1].area} m²`);
});

test("a piece still finds its receiver after a union dropped the vertex they shared", () => {
  // b takes the piece beside it first, and polygon-clipping's union drops
  // the collinear vertex at (1, 1). The second piece touched b only at that
  // point, so it waits a pass and then shares the edge y = 1 with b. GEOS
  // computes the overlap whatever the vertices, and so must the engine.
  const tiles = [
    pieceTile("a", [10.5, 10.5], [[10, 10, 11, 11], [1, 0, 2, 1], [1, 1, 2, 2]]),
    pieceTile("b", [0.5, 0.5], [[0, 0, 1, 1]]),
  ];
  attachDetachedPieces(tiles);
  assert.equal(tiles[1].area, 3);
});

test("tile coordinates lie on the micrometer grid", () => {
  const tiles = tessellate([seed(20.3, 21.7), seed(80.1, 19.9), seed(50.6, 80.2)], { clip: CLIP, gridM: 5, epsg: 5070, snapCentroid: false });
  for (const t of tiles) {
    for (const ring of t.geometry.flat()) for (const [vx, vy] of ring) {
      assert.equal(Math.round(vx * 1e6) / 1e6, vx);
      assert.equal(Math.round(vy * 1e6) / 1e6, vy);
    }
  }
});
