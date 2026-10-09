import { Delaunay } from "d3-delaunay";
import polygonClipping from "polygon-clipping";
import { convexHull, pointInRing, ringBounds, multiPolygonArea } from "./geometry.js";
import { reprojectMulti } from "./proj.js";

// The edge endpoints outside the self-loops, a seed per distinct coordinate.
// Whichever node shows first at a coordinate hands the seed its id.
export function legacySeeds(graph) {
  const seen = new Map();
  for (const e of graph.edges) {
    if (e.u === e.v) continue;
    for (const id of [e.u, e.v]) {
      const n = graph.nodes.get(id);
      const k = `${n.lon},${n.lat}`;
      if (!seen.has(k)) seen.set(k, { nodeId: id, lon: n.lon, lat: n.lat });
    }
  }
  return [...seen.values()];
}

// The legacy tiling. Voronoi works on the seeds in degrees, cells get cut
// by a convex hull over `scopeLonLat`, and the areas are measured in UTM
// zone `utmEpsg`.
export function legacyTiles(graph, scopeLonLat, { utmEpsg }) {
  const seeds = legacySeeds(graph);
  if (!seeds.length) return [];
  const hull = convexHull(scopeLonLat);
  const voronoi = Delaunay.from(seeds.map((s) => [s.lon, s.lat])).voronoi(ringBounds(hull));
  const tiles = [];
  for (let i = 0; i < seeds.length; i++) {
    const cell = voronoi.cellPolygon(i);
    if (!cell) continue;
    let geometry;
    if (cell.every((pt) => pointInRing(pt, hull))) {
      geometry = [[cell]];
    } else {
      geometry = polygonClipping.intersection([[cell]], [[hull]]);
      if (!geometry.length) continue;
    }
    tiles.push({
      nodeId: seeds[i].nodeId, seedLon: seeds[i].lon, seedLat: seeds[i].lat,
      geometryLonLat: geometry,
      areaM2: multiPolygonArea(reprojectMulti(geometry, 4326, utmEpsg)),
    });
  }
  return tiles;
}
