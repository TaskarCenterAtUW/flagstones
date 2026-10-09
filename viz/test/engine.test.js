import { test } from "node:test";
import assert from "node:assert/strict";
import { createSession } from "../src/engine/session.js";
import { inProcessEngine, workerEngine } from "../src/app/engine.js";
import { buildGraph } from "../src/engine/graph.js";
import { computeVariant } from "../src/engine/pipeline.js";
import { appBundle, gridBundle } from "./fixtures.js";

const PARAMS = { toleranceM: 5, gridM: 5, minDegree: 3, keepDeadEnds: true, contractTransitions: false, transitionOpts: {}, epsg: 5070 };
const CLIP = { kind: "bbox", bufferM: 50 };

test("the session computes the same tiles as the pipeline does on the same graph", () => {
  const session = createSession(gridBundle());
  const reply = session.handle({ type: "compute", epsg: 5070, intersection: { params: PARAMS, clip: CLIP }, legacy: null });
  const direct = computeVariant(buildGraph(gridBundle()), PARAMS, CLIP);
  assert.deepEqual(reply.intersection.tiles.map((t) => t.nativeId), direct.tiles.map((t) => t.nativeId));
  assert.deepEqual(reply.warnings, []);
  assert.ok(reply.timings.intersection >= 0, "the compute time comes back with the result");
  assert.equal(reply.legacy, null, "nothing asked for, nothing computed");
});

test("a move message reaches the session's graph before the next compute", () => {
  const session = createSession(gridBundle());
  const before = session.handle({ type: "compute", epsg: 5070, intersection: { params: PARAMS, clip: CLIP }, legacy: null }).intersection.tiles.map((t) => t.nativeId);
  const n = buildGraph(gridBundle()).nodes.get(2);
  session.handle({ type: "move", id: 2, lon: n.lon + 0.00012, lat: n.lat });
  const after = session.handle({ type: "compute", epsg: 5070, intersection: { params: PARAMS, clip: CLIP }, legacy: null }).intersection.tiles.map((t) => t.nativeId);
  assert.notDeepEqual(after, before);
  session.handle({ type: "reset", id: 2 });
  const reset = session.handle({ type: "compute", epsg: 5070, intersection: { params: PARAMS, clip: CLIP }, legacy: null }).intersection.tiles.map((t) => t.nativeId);
  assert.deepEqual(reset, before);
});

test("a compute that throws comes back as a warning, so the caller keeps its last result", () => {
  const session = createSession(gridBundle());
  const reply = session.handle({ type: "compute", epsg: 5070, intersection: { params: { ...PARAMS, gridM: 0 }, clip: CLIP }, legacy: null });
  assert.equal(reply.intersection, null);
  assert.match(reply.warnings[0], /^tiles: /);
});

test("the in-process engine answers in order, one message after the other", async () => {
  const engine = inProcessEngine(gridBundle());
  assert.equal(engine.kind, "in-process");
  const n = buildGraph(gridBundle()).nodes.get(2);
  const moved = engine.send({ type: "move", id: 2, lon: n.lon + 0.00012, lat: n.lat });
  const computed = engine.send({ type: "compute", epsg: 5070, intersection: { params: PARAMS, clip: CLIP }, legacy: null });
  await moved;
  const reply = await computed;
  const unmoved = computeVariant(buildGraph(gridBundle()), PARAMS, CLIP).tiles.map((t) => t.nativeId);
  assert.notDeepEqual(reply.intersection.tiles.map((t) => t.nativeId), unmoved, "the move was applied before the compute");
});

test("the worker gets only the parts of the bundle its session reads", async () => {
  const posted = [];
  const realWorker = globalThis.Worker;
  globalThis.Worker = class {
    postMessage(data) {
      posted.push(data);
      queueMicrotask(() => this.onmessage({ data: { id: data.id, ok: true, result: null } }));
    }
  };
  try {
    const b = appBundle();
    await workerEngine(b).send({ type: "resetAll" });
    assert.deepEqual(Object.keys(posted[0].msg.bundle).sort(), ["city", "edges", "nodes"]);
    assert.equal(posted[0].msg.bundle.nodes, b.nodes);
  } finally {
    globalThis.Worker = realWorker;
  }
});

const square = (lo, hi) => [[[[lo, lo], [hi, lo], [hi, hi], [lo, hi], [lo, lo]]]];
const keyed = (key, lo, hi) => ({ kind: "polygon", key, multi: square(lo, hi), extent: square(-400, 800), extentKey: "extent" });
const STAGED = { ...PARAMS, fallbackCellM: 250, fallbackMinShare: 0.1 };
const compute = (session, params, clip) => session.handle({ type: "compute", epsg: 5070, intersection: { params, clip }, legacy: null }).intersection;

test("a fallback edit keeps the tiles, and a clip edit keeps the merge", () => {
  const session = createSession(gridBundle());
  const first = compute(session, STAGED, keyed("a", 0, 400));
  const cells = compute(session, { ...STAGED, fallbackCellM: 300 }, keyed("a", 0, 400));
  assert.equal(cells.tiles, first.tiles);
  assert.notEqual(cells.fallback, first.fallback);
  const clipped = compute(session, STAGED, keyed("b", 50, 350));
  assert.equal(clipped.clusters, first.clusters);
  assert.notEqual(clipped.tiles, first.tiles);
});

test("the stages a cache keeps give the tiles a fresh session computes", () => {
  const session = createSession(gridBundle());
  compute(session, STAGED, keyed("a", 0, 400));
  compute(session, { ...STAGED, minDegree: 2 }, keyed("b", 50, 350));
  const again = compute(session, STAGED, keyed("a", 0, 400));
  const fresh = compute(createSession(gridBundle()), STAGED, keyed("a", 0, 400));
  const shape = (r) => [r.tiles.map((t) => [t.nativeId, t.area]), r.fallback.map((t) => [t.nativeId, t.area])];
  assert.deepEqual(shape(again), shape(fresh));
});

test("a node move recomputes every stage", () => {
  const session = createSession(gridBundle());
  const before = compute(session, STAGED, keyed("a", 0, 400));
  const n = buildGraph(gridBundle()).nodes.get(2);
  session.handle({ type: "move", id: 2, lon: n.lon + 0.00012, lat: n.lat });
  const after = compute(session, STAGED, keyed("a", 0, 400));
  assert.notEqual(after.clusters, before.clusters);
  const fresh = createSession(gridBundle());
  fresh.handle({ type: "move", id: 2, lon: n.lon + 0.00012, lat: n.lat });
  assert.deepEqual(after.tiles.map((t) => [t.nativeId, t.area]), compute(fresh, STAGED, keyed("a", 0, 400)).tiles.map((t) => [t.nativeId, t.area]));
});

test("a degree edit reuses the prepared clip", () => {
  const graph = buildGraph(gridBundle());
  const cache = new Map();
  computeVariant(graph, STAGED, keyed("a", 0, 400), cache);
  const prepared = cache.get("clip").value;
  computeVariant(graph, { ...STAGED, minDegree: 2 }, keyed("a", 0, 400), cache);
  assert.equal(cache.get("clip").value, prepared);
});

test("a clip without a key is never served from the cache", () => {
  const session = createSession(gridBundle());
  const clip = { kind: "polygon", multi: square(0, 400) };
  const first = compute(session, PARAMS, clip);
  assert.notEqual(compute(session, PARAMS, clip).tiles, first.tiles);
});
