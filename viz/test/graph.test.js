import { test } from "node:test";
import assert from "node:assert/strict";
import { buildGraph, neighbors, legs, moveNode, resetNode, setEngineCrs, withoutDeadEnds, deadEnds } from "../src/engine/graph.js";
import { tJunctionWithDeadEndBundle, carriagewayJoinBundle, truncatedJunctionStubBundle, loopEndStubBundle } from "./fixtures.js";

test("adjacency de-duplicates the two directions of a two-way street", () => {
  const g = buildGraph(tJunctionWithDeadEndBundle());
  assert.deepEqual(neighbors(g, 2).sort(), [1, 3, 5]);
  const l = legs(g, 2).get(5);
  assert.equal(l.out, true);
  assert.equal(l.in, true);
});

test("a one-way leg records its direction", () => {
  const g = buildGraph(carriagewayJoinBundle());
  const l = legs(g, 3);
  assert.deepEqual([l.get(1).in, l.get(1).out], [true, false]);
  assert.deepEqual([l.get(2).in, l.get(2).out], [false, true]);
  assert.deepEqual([l.get(4).in, l.get(4).out], [true, true]);
});

test("engine coordinates come from the export at the export CRS and from proj4 elsewhere", () => {
  const g = buildGraph(tJunctionWithDeadEndBundle());
  const n = g.nodes.get(2);
  assert.equal(n.x, 200);
  setEngineCrs(g, 4326);
  assert.ok(Math.abs(n.x - n.lon) < 1e-12);
  setEngineCrs(g, 5070);
  assert.equal(n.x, 200);
});

test("moving a node reprojects it and marks it; reset restores the export coordinates", () => {
  const g = buildGraph(tJunctionWithDeadEndBundle());
  const n = g.nodes.get(2);
  moveNode(g, 2, n.lon + 0.0001, n.lat);
  assert.equal(n.moved, true);
  assert.ok(n.x > 200 && n.x < 220);
  resetNode(g, 2);
  assert.equal(n.moved, false);
  assert.equal(n.x, 200);
});

test("withoutDeadEnds drops street_count <= 1 nodes and their legs", () => {
  const g = withoutDeadEnds(buildGraph(tJunctionWithDeadEndBundle()));
  assert.equal(g.nodes.has(5), false);
  assert.deepEqual(neighbors(g, 2).sort(), [1, 3]);
});

test("deadEnds lists every node with one street or none", () => {
  // The carriageway ends 1 and 2 and the far end 4 have one street each; the
  // join has three. The ring's cul-de-sac end is the only dead end there.
  assert.deepEqual([...deadEnds(buildGraph(carriagewayJoinBundle()))].sort((a, b) => a - b), [1, 2, 4]);
  assert.deepEqual([...deadEnds(buildGraph(tJunctionWithDeadEndBundle()))], [5]);
});

test("a dead end is judged on this graph's legs, not on osmnx's street_count", () => {
  // osmnx counts streets on the graph it fetched with a buffer, before
  // truncating to the city polygon, so a junction just inside the limit whose
  // other legs lie outside arrives with street_count 3 and one neighbor.
  const g = buildGraph(truncatedJunctionStubBundle());
  assert.deepEqual([...deadEnds(g)].sort(), [1, 3, 5]);
  // A street ending in a turning circle mapped as a loop way arrives with a
  // 3 too (one street, the loop counted twice). It is a street end here,
  // while a loop road of 100 m or more makes its stem's end a junction.
  assert.deepEqual([...deadEnds(buildGraph(loopEndStubBundle()))].sort(), [1, 3, 5]);
  assert.deepEqual([...deadEnds(buildGraph(loopEndStubBundle({ loopM: 300 })))].sort(), [1, 3]);
});

test("the comparison mode drops osmnx's dead ends, read from the export's street counts", () => {
  // Node 5 is a junction osmnx counted with three streets before the graph
  // was cut at the city limit, where it has one.
  const g = withoutDeadEnds(buildGraph(truncatedJunctionStubBundle()));
  assert.ok(g.nodes.has(5));
  assert.ok(!g.nodes.has(1));
});
