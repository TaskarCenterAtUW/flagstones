import { buildGraph, setEngineCrs, moveNode, resetNode, resetAll } from "./graph.js";
import { computeVariant } from "./pipeline.js";
import { legacyTiles } from "./legacy.js";

// The session takes plain messages, so the same code runs behind a Worker in
// the browser and in-process under Node. A failed computation returns a
// warning and no result, so the caller keeps its last good one.
export function createSession(bundle) {
  const graph = buildGraph(bundle);
  // Stage results from computeVariant, surviving from one compute to the next.
  const cache = new Map();

  function compute(msg) {
    setEngineCrs(graph, msg.epsg);
    const reply = { intersection: null, legacy: null, warnings: [], timings: {} };
    if (msg.intersection) {
      const t0 = performance.now();
      try {
        reply.intersection = computeVariant(graph, msg.intersection.params, msg.intersection.clip, cache);
        reply.warnings.push(...reply.intersection.warnings);
      } catch (err) {
        reply.warnings.push(`tiles: ${err.message}`);
        console.error(err);
      }
      reply.timings.intersection = performance.now() - t0;
    }
    if (msg.legacy) {
      const t0 = performance.now();
      try {
        reply.legacy = { tiles: legacyTiles(graph, msg.legacy.scopeLonLat, { utmEpsg: msg.legacy.utmEpsg }) };
      } catch (err) {
        reply.warnings.push(`legacy: ${err.message}`);
        console.error(err);
      }
      reply.timings.legacy = performance.now() - t0;
    }
    return reply;
  }

  return {
    handle(msg) {
      switch (msg.type) {
        case "move": moveNode(graph, msg.id, msg.lon, msg.lat); return null;
        case "reset": resetNode(graph, msg.id); return null;
        case "resetAll": resetAll(graph); return null;
        case "compute": return compute(msg);
        default: throw new Error(`unknown engine message: ${msg.type}`);
      }
    },
  };
}
