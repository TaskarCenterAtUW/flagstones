// The package's public engine API, imported as "flagstones-viz/engine".
// The other modules in src/engine are internal and may change without notice.

export { buildGraph, deadEnds, withoutDeadEnds, moveNode, resetNode, resetAll, setEngineCrs } from "./graph.js";
export { consolidate, ineligibleLinkNodes, medianTies, bufferCircle } from "./cluster.js";
export { classifyTransitions } from "./transitions.js";
export { tessellate } from "./voronoi.js";
export { fallbackTiles } from "./fallback.js";
export { bboxClip, computeVariant } from "./pipeline.js";
export { legacyTiles, legacySeeds } from "./legacy.js";
export { convexHull, pointInRing, multiPolygonArea } from "./geometry.js";
export { forward, inverse } from "./proj.js";
export { tileCell } from "./snap.js";
