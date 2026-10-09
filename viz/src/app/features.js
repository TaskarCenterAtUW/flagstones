import { inverse, reprojectMulti } from "../engine/proj.js";
import { consolidationCandidates, mergeFeatures } from "./consolidation.js";

export const EMPTY = { type: "FeatureCollection", features: [] };
const fc = (features) => ({ type: "FeatureCollection", features });

function tileFeature(tile, epsg, variant) {
  return {
    type: "Feature",
    id: tile.nativeId,
    properties: { variant, nativeId: tile.nativeId, degree: tile.degree, nodeCount: tile.nodeCount, area: tile.area, clusterIndex: tile.clusterIndex },
    geometry: { type: "MultiPolygon", coordinates: reprojectMulti(tile.geometry, epsg, 4326) },
  };
}

function seedFeature(tile, epsg, variant) {
  return {
    type: "Feature",
    properties: { variant, nativeId: tile.nativeId },
    geometry: { type: "Point", coordinates: inverse(epsg, tile.seedX, tile.seedY) },
  };
}

const point = (coordinates, properties = {}) => ({ type: "Feature", properties, geometry: { type: "Point", coordinates } });

// Caches converted features by tile ID and signature, so a node move
// re-projects only the tiles it changed instead of the whole city.
function tileCollection(cache, result, epsg, variant) {
  if (!result) return EMPTY;
  const keep = new Map();
  for (const tile of result.tiles) {
    const sig = `${epsg}|${tile.area}|${tile.seedX}|${tile.seedY}`;
    const hit = cache.get(tile.nativeId);
    const feature = hit && hit.sig === sig ? hit.feature : tileFeature(tile, epsg, variant);
    keep.set(tile.nativeId, { sig, feature });
  }
  cache.clear();
  for (const [k, v] of keep) cache.set(k, v);
  return fc([...keep.values()].map((v) => v.feature));
}

function legacyCollection(result) {
  if (!result) return EMPTY;
  return fc(result.tiles.map((t) => ({
    type: "Feature", properties: { variant: "legacy", nodeId: t.nodeId, area: t.areaM2 },
    geometry: { type: "MultiPolygon", coordinates: t.geometryLonLat },
  })));
}

function selectionFeature(state) {
  const sel = state.selection;
  if (sel?.kind === "node") {
    const n = state.graph.nodes.get(sel.id);
    return n ? point([n.lon, n.lat]) : null;
  }
  if (sel?.kind !== "tile") return null;
  const res = state.results[sel.variant];
  if (!res) return null;
  if (sel.variant === "legacy") {
    const t = res.tiles.find((x) => x.nodeId === sel.id);
    return t ? point([t.seedLon, t.seedLat]) : null;
  }
  const t = res.tiles.find((x) => x.nativeId === sel.id);
  return t ? point(inverse(res.epsg ?? state.params.epsg, t.seedX, t.seedY)) : null;
}

// The GeoJSON for each map source but the grid's, which follows the view.
// Features come from the result on hand, in the CRS it was computed in, and
// a source whose inputs have not changed since the last call gets the same
// object back, so the map skips its setData. A store notification that only
// marks the store busy then costs no re-projection.
export function createSourceData(store) {
  const tiles = new Map();
  const memo = new Map();
  const once = (id, key, build) => {
    const hit = memo.get(id);
    if (hit && hit.key.length === key.length && hit.key.every((k, i) => k === key[i])) return hit.data;
    const data = build();
    memo.set(id, { key, data });
    return data;
  };
  return function sourceData(state) {
    const res = state.results.intersection;
    const epsg = res?.epsg ?? state.params.epsg;
    const { graph } = state;
    const legacy = state.layers.legacy ? state.results.legacy : null;
    const outline = store.changedTiles();
    const outlineKey = [...outline].sort().join(" ");
    const merge = state.layers.consolidation ? once("merge", [res, graph.version], () => mergeFeatures(state)) : null;
    return new Map([
      ["intersection-tiles", once("intersection-tiles", [res], () => tileCollection(tiles, res, epsg, "intersection"))],
      ["fallback-tiles", once("fallback-tiles", [res], () => (res ? fc(res.fallback.map((t) => tileFeature(t, epsg, "fallback"))) : EMPTY))],
      ["legacy-tiles", once("legacy-tiles", [legacy], () => legacyCollection(legacy))],
      ["intersection-seeds", once("intersection-seeds", [res], () => (res ? fc(res.tiles.map((t) => seedFeature(t, epsg, "intersection"))) : EMPTY))],
      ["changed-tiles", once("changed-tiles", [res, outlineKey], () => fc(outline.size ? res.tiles.filter((t) => outline.has(t.nativeId)).map((t) => tileFeature(t, epsg, "intersection")) : []))],
      ["collision-tiles", once("collision-tiles", [res], () => fc(res ? res.tiles.filter((t) => t.seedCollision).map((t) => tileFeature(t, epsg, "intersection")) : []))],
      // Moving a node leaves each edge's geometry as the bundle supplied it.
      ["network-edges", once("network-edges", [graph.edges], () => fc(graph.edges.filter((e) => e.u !== e.v).map((e) => ({
        type: "Feature", properties: { u: e.u, v: e.v, name: e.name.join(" / "), oneway: e.oneway },
        geometry: { type: "LineString", coordinates: e.geom.map(([lon, lat]) => [lon, lat]) },
      }))))],
      ["network-nodes", once("network-nodes", [res, graph.version], () => {
        const candidates = consolidationCandidates(state);
        return fc([...graph.nodes.values()].map((n) => ({
          type: "Feature", id: n.id,
          properties: { id: n.id, sc: n.sc, moved: n.moved, eligible: candidates.has(n.id) },
          geometry: { type: "Point", coordinates: [n.lon, n.lat] },
        })));
      })],
      ["moved-nodes", once("moved-nodes", [graph.version], () => fc(store.movedNodes().map((n) => point([n.lon, n.lat], { id: n.id }))))],
      ["selection", once("selection", [state.selection, res, state.results.legacy, graph.version], () => {
        const feature = selectionFeature(state);
        return feature ? fc([feature]) : EMPTY;
      })],
      ["merge-links", merge?.links ?? EMPTY],
      ["merge-centroids", merge?.centroids ?? EMPTY],
    ]);
  };
}
