// Literal values of the design tokens' hues. A MapLibre style has no way
// to read CSS variables, so this list duplicates them. Dash patterns
// separate the tessellations without color.
export const PAINT = {
  ink: "#24312B", inkSoft: "#4A574F", amber: "#755916", slate: "#4C5B66",
  mode: "#177299", action: "#4B2E83", line: "#C8D2CB", warnBg: "#F6EED8", paper: "#F5F7F4",
  danger: "#8C2F26",
};

export const LAYER_KEY = [
  { id: "legacy", label: "Legacy tiling", swatch: { stroke: PAINT.slate, dash: "3 3" } },
  { id: "tiles", label: "Intersection tiles", swatch: { stroke: PAINT.amber } },
  { id: "fallback", label: "Fallback grid cells", swatch: { stroke: PAINT.inkSoft, dash: "1 2" } },
  { id: "seeds", label: "Intersection seeds", swatch: { dot: PAINT.amber } },
  { id: "grid", label: "Snapping grid (zoom 15+)", swatch: { stroke: PAINT.line, fill: PAINT.warnBg } },
  { id: "consolidation", label: "Consolidation buffers (zoom 15+)", swatch: { stroke: PAINT.mode, fill: "rgba(23,114,153,0.15)" } },
  { id: "movedNodes", label: "Moved nodes", swatch: { dot: PAINT.action } },
  { id: "changedTiles", label: "Tiles changed by the last edit", swatch: { stroke: PAINT.action, width: 3 } },
  { id: "network", label: "Drivable road network", swatch: { stroke: PAINT.mode, dash: "7 2", width: 2 } },
  { id: "basemap", label: "Basemap", swatch: { fill: PAINT.paper } },
];

// In draw order.
export const MAP_LAYERS = [
  { id: "legacy-fill", type: "fill", source: "legacy-tiles", paint: { "fill-color": PAINT.slate, "fill-opacity": 0.08 }, toggle: "legacy" },
  { id: "legacy-line", type: "line", source: "legacy-tiles", paint: { "line-color": PAINT.slate, "line-width": 1.5, "line-dasharray": [2, 2] }, toggle: "legacy" },
  { id: "intersection-fill", type: "fill", source: "intersection-tiles", paint: { "fill-color": PAINT.amber, "fill-opacity": 0.1 }, toggle: "tiles" },
  { id: "intersection-line", type: "line", source: "intersection-tiles", paint: { "line-color": PAINT.amber, "line-width": 1.5 }, toggle: "tiles" },
  { id: "fallback-fill", type: "fill", source: "fallback-tiles", paint: { "fill-color": PAINT.inkSoft, "fill-opacity": 0.06 }, toggle: "fallback" },
  { id: "fallback-line", type: "line", source: "fallback-tiles", paint: { "line-color": PAINT.inkSoft, "line-width": 1, "line-dasharray": [1, 2] }, toggle: "fallback" },
  { id: "changed-line", type: "line", source: "changed-tiles", paint: { "line-color": PAINT.action, "line-width": 3 }, toggle: "changedTiles" },
  // A tile that collapsed clusters sharing a grid cell. The layer
  // paints over every tile layer and shares the tiles toggle,
  // so the highlight cannot vanish while tiles show.
  { id: "collision-fill", type: "fill", source: "collision-tiles", paint: { "fill-color": PAINT.danger, "fill-opacity": 0.18 }, toggle: "tiles" },
  { id: "collision-line", type: "line", source: "collision-tiles", paint: { "line-color": PAINT.danger, "line-width": 3 }, toggle: "tiles" },
  { id: "grid-lines", type: "line", source: "grid-lines", minzoom: 16, paint: { "line-color": PAINT.line, "line-width": 0.6 }, toggle: "grid" },
  { id: "seed-cells", type: "fill", source: "seed-cells", minzoom: 15, paint: { "fill-color": PAINT.warnBg, "fill-opacity": 0.55, "fill-outline-color": PAINT.amber }, toggle: "grid" },
  { id: "snap-links", type: "line", source: "snap-links", minzoom: 15, paint: { "line-color": PAINT.amber, "line-width": 1 }, toggle: "grid" },
  // circleRadiusExpression supplies the radius, from tolerance, set in
  // map.js; features.js marks `eligible` through
  // consolidationCandidates. A zero-radius disc still paints its
  // stroke, so the filter drops nodes the engine won't merge.
  { id: "node-buffers", type: "circle", source: "network-nodes", minzoom: 15, filter: ["==", ["get", "eligible"], true], paint: { "circle-radius": 0, "circle-color": PAINT.mode, "circle-opacity": 0.12, "circle-stroke-color": PAINT.mode, "circle-stroke-width": 1, "circle-stroke-opacity": 0.5, "circle-pitch-alignment": "map" }, toggle: "consolidation" },
  { id: "merge-links", type: "line", source: "merge-links", minzoom: 15, paint: { "line-color": PAINT.mode, "line-width": 1.5 }, toggle: "consolidation" },
  { id: "merge-centroids", type: "circle", source: "merge-centroids", minzoom: 15, paint: { "circle-radius": 3.5, "circle-color": PAINT.mode, "circle-stroke-color": PAINT.paper, "circle-stroke-width": 1 }, toggle: "consolidation" },
  { id: "network-edges", type: "line", source: "network-edges", paint: { "line-color": PAINT.mode, "line-width": 2, "line-dasharray": [7, 2] }, toggle: "network" },
  { id: "network-nodes", type: "circle", source: "network-nodes", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 1.5, 17, 4], "circle-color": PAINT.inkSoft, "circle-stroke-color": PAINT.paper, "circle-stroke-width": 0.5 }, toggle: "network" },
  { id: "raw-centroids", type: "circle", source: "raw-centroids", minzoom: 15, paint: { "circle-radius": 4, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": PAINT.amber, "circle-stroke-width": 1.5 }, toggle: "grid" },
  { id: "intersection-seeds", type: "circle", source: "intersection-seeds", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 2, 17, 5], "circle-color": PAINT.amber, "circle-stroke-color": PAINT.paper, "circle-stroke-width": 1 }, toggle: "seeds" },
  { id: "moved-nodes", type: "circle", source: "moved-nodes", paint: { "circle-radius": 7, "circle-color": PAINT.action, "circle-stroke-color": PAINT.paper, "circle-stroke-width": 2 }, toggle: "movedNodes" },
  { id: "drag-preview", type: "circle", source: "drag-preview", paint: { "circle-radius": 8, "circle-color": PAINT.action, "circle-opacity": 0.6 }, toggle: null },
  { id: "selection", type: "circle", source: "selection", paint: { "circle-radius": 11, "circle-color": "rgba(75,46,131,0.18)", "circle-stroke-color": PAINT.action, "circle-stroke-width": 2 }, toggle: null },
];
