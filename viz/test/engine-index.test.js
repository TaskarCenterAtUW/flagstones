import { test } from "node:test";
import assert from "node:assert/strict";
import * as engine from "../src/engine/index.js";

// computeVariant runs the whole pipeline, setEngineCrs computes in another
// CRS, and resetNode undoes a moveNode.
const PUBLIC = [
  "bboxClip", "bufferCircle", "buildGraph", "classifyTransitions", "computeVariant", "consolidate",
  "convexHull", "deadEnds", "fallbackTiles", "forward", "ineligibleLinkNodes", "inverse",
  "legacySeeds", "legacyTiles", "medianTies", "moveNode", "multiPolygonArea",
  "pointInRing", "resetAll", "resetNode", "setEngineCrs", "tessellate", "tileCell", "withoutDeadEnds",
];

test("the engine entry point exports exactly the public names", () => {
  assert.deepEqual(Object.keys(engine).sort(), PUBLIC);
  for (const name of PUBLIC) assert.equal(typeof engine[name], "function", name);
});
