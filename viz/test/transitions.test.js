import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyTransitions, roadNames } from "../src/engine/transitions.js";
import { buildGraph } from "../src/engine/graph.js";
import { carriagewayJoinBundle, oneWayAvenueTeeBundle, tJunctionWithDeadEndBundle, bundleFromXY } from "./fixtures.js";

test("a same-road join classifies", () => {
  const found = classifyTransitions(buildGraph(carriagewayJoinBundle()));
  assert.deepEqual([...found.keys()], [3]);
  const info = found.get(3);
  assert.equal(info.kind, "join");
  assert.deepEqual(info.roads, ["Grand Avenue"]);
  assert.ok(info.spread < 10, `carriageways nearly parallel, got ${info.spread}`);
  assert.ok(info.opposite > 170, `continuation opposite, got ${info.opposite}`);
});

test("a two-way side street on a one-way avenue is not a transition", () => {
  assert.equal(classifyTransitions(buildGraph(oneWayAvenueTeeBundle())).size, 0);
});

test("a same-road pattern still fails without the bearing shape", () => {
  // Same names as the join fixture but the continuation leaves at a right angle.
  const name = ["Grand Avenue"];
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 8, sc: 1 }, { id: 2, x: 0, y: -8, sc: 1 }, { id: 3, x: 200, y: 0, sc: 3 }, { id: 4, x: 200, y: 200, sc: 1 }],
    [{ u: 1, v: 3, oneway: true, name }, { u: 3, v: 2, oneway: true, name }, { u: 3, v: 4, name }],
  );
  assert.equal(classifyTransitions(buildGraph(bundle)).size, 0);
});

test("requireSameRoad=false accepts the tee on shape alone", () => {
  const found = classifyTransitions(buildGraph(oneWayAvenueTeeBundle()), { requireSameRoad: false });
  assert.equal(found.size, 0, "the tee's continuation is at 90 degrees, so the bearing test still rejects it");
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 8, sc: 1 }, { id: 2, x: 0, y: -8, sc: 1 }, { id: 3, x: 200, y: 0, sc: 3 }, { id: 4, x: 400, y: 0, sc: 1 }],
    [{ u: 1, v: 3, oneway: true, name: ["A"] }, { u: 3, v: 2, oneway: true, name: ["B"] }, { u: 3, v: 4, name: ["C"] }],
  );
  assert.equal(classifyTransitions(buildGraph(bundle)).size, 0);
  assert.equal(classifyTransitions(buildGraph(bundle), { requireSameRoad: false }).size, 1);
});

test("a one-way fork classifies", () => {
  const name = ["WA 270"];
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 400, y: 10, sc: 1 }, { id: 4, x: 400, y: -10, sc: 1 }],
    [{ u: 1, v: 2, oneway: true, name }, { u: 2, v: 3, oneway: true, name }, { u: 2, v: 4, oneway: true, name }],
  );
  const found = classifyTransitions(buildGraph(bundle));
  assert.equal(found.get(2)?.kind, "fork");
});

test("an ordinary T-junction is never a transition", () => {
  assert.equal(classifyTransitions(buildGraph(tJunctionWithDeadEndBundle())).size, 0);
});

test("road names come from name and ref, strings or lists", () => {
  const names = roadNames([{ name: "A Street", ref: ["WA 27", "US 195"] }, { name: ["A Street", "B Street"], ref: null }]);
  assert.deepEqual([...names].sort(), ["A Street", "B Street", "US 195", "WA 27"]);
});
