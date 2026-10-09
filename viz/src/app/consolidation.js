import { inverse } from "../engine/proj.js";
import { deadEnds } from "../engine/graph.js";

// How much ground a pixel spans at the equator before any zoom-in, counting
// MapLibre's 512-pixel tiles. Each zoom step cuts that span in half.
const EQUATOR_M_PER_PX_Z0 = 40075016.686 / 512;

// A circle-radius expression for a radius in meters at any zoom, at the
// city's latitude. Exponential base-2 interpolation doubles the pixel
// radius per zoom level as the meters per pixel halve, so the disc needs no
// geometry of its own and draws from the node source.
export function circleRadiusExpression(meters, latDeg) {
  const px0 = meters / (EQUATOR_M_PER_PX_Z0 * Math.cos((latDeg * Math.PI) / 180));
  return ["interpolate", ["exponential", 2], ["zoom"], 0, px0, 24, px0 * 2 ** 24];
}

// The nodes the engine tries to merge, which are the ones that get a buffer
// disc. Dead ends and ramp nodes that are no junction by themselves sit out
// the merge (the result's `unmergeable`), and so do carriageway transitions
// while contraction is on (`excluded`, empty while it is off). Reading both
// sets keeps the discs in step with the merge links.
export function consolidationCandidates(state) {
  const dead = deadEnds(state.graph);
  const held = state.results.intersection?.unmergeable ?? new Set();
  const excluded = state.results.intersection?.excluded ?? new Set();
  const out = new Set();
  for (const id of state.graph.nodes.keys()) if (!dead.has(id) && !held.has(id) && !excluded.has(id)) out.add(id);
  return out;
}

const fc = (features) => ({ type: "FeatureCollection", features });

// A single-node cluster draws nothing, so the picture keeps just
// the joins this tolerance made.
export function mergeFeatures(state) {
  const res = state.results.intersection;
  const links = [], centroids = [];
  if (res) {
    const { graph } = state, epsg = res.epsg ?? state.params.epsg;
    res.clusters.forEach((c, i) => {
      if (c.members.length < 2) return;
      const center = inverse(epsg, c.x, c.y);
      centroids.push({ type: "Feature", properties: { cluster: i, members: c.members.length }, geometry: { type: "Point", coordinates: center } });
      for (const id of c.members) {
        const n = graph.nodes.get(id);
        links.push({ type: "Feature", properties: { cluster: i, node: id }, geometry: { type: "LineString", coordinates: [[n.lon, n.lat], center] } });
      }
    });
  }
  return { links: fc(links), centroids: fc(centroids) };
}
