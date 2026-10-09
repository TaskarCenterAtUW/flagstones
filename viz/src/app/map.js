import maplibregl from "maplibre-gl";
import { MAP_LAYERS } from "./layers.js";
import { gridFeatures } from "./grid.js";
import { circleRadiusExpression } from "./consolidation.js";
import { EMPTY, createSourceData } from "./features.js";

const SOURCES = ["legacy-tiles", "intersection-tiles", "fallback-tiles", "changed-tiles", "collision-tiles", "grid-lines", "seed-cells", "snap-links",
  "network-edges", "network-nodes", "raw-centroids", "intersection-seeds", "moved-nodes", "drag-preview", "selection", "merge-links", "merge-centroids"];

export function createMap(container, store) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const { bundle } = store.state;
  const map = new maplibregl.Map({
    container, style: "/design/app-basemap.json",
    center: bundle.city.center, zoom: 12, attributionControl: { compact: false },
    fadeDuration: reduced ? 0 : 300,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  const sourceData = createSourceData(store);
  // Remembers the last object handed to each source, so sync() skips
  // setData when that source's data matches the previous call.
  const sent = new Map();
  let basemapLayerIds = [];

  // Flips on after the load handler registers every source, which keeps sync()
  // from calling setData on a source that isn't there yet. We can't lean on
  // map.isStyleLoaded() here, because adding our layers dirties the style
  // again, so the loaded flag reads false during the sync that is meant to fill
  // the sources. The map would sit blank until some later notification arrives.
  let ready = false;
  function sync(state) {
    // Kept ahead of the ready gate so busy styling answers the
    // store from the first notification, before the map wakes.
    container.parentElement.toggleAttribute("data-busy", state.busy);
    if (!ready) return;
    for (const [id, data] of sourceData(state)) {
      if (sent.get(id) === data) continue;
      map.getSource(id).setData(data);
      sent.set(id, data);
    }
    syncGrid(state);
    map.setPaintProperty("node-buffers", "circle-radius", circleRadiusExpression(state.params.toleranceM, bundle.city.center[1]));
    for (const spec of MAP_LAYERS) {
      if (spec.toggle) map.setLayoutProperty(spec.id, "visibility", state.layers[spec.toggle] ? "visible" : "none");
    }
    for (const id of basemapLayerIds) map.setLayoutProperty(id, "visibility", state.layers.basemap ? "visible" : "none");
  }

  // moveend fires this as well, and a view move can land before the
  // load handler has registered the sources.
  function syncGrid(state) {
    if (!ready) return;
    if (!state.layers.grid || map.getZoom() < 15 || !state.results.intersection) {
      for (const id of ["grid-lines", "seed-cells", "snap-links", "raw-centroids"]) map.getSource(id).setData(EMPTY);
      return;
    }
    const b = map.getBounds();
    const f = gridFeatures(state, [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    document.querySelector('[data-layer="grid"]')?.closest("label")?.toggleAttribute("data-too-many", f.tooMany);
    map.getSource("grid-lines").setData(f.lines);
    map.getSource("seed-cells").setData(f.cells);
    map.getSource("snap-links").setData(f.links);
    map.getSource("raw-centroids").setData(f.centroids);
  }

  map.on("load", () => {
    basemapLayerIds = map.getStyle().layers.map((l) => l.id);
    for (const id of SOURCES) map.addSource(id, { type: "geojson", data: EMPTY, promoteId: id.endsWith("-tiles") ? "nativeId" : undefined });
    for (const spec of MAP_LAYERS) {
      const { toggle, ...layer } = spec;
      map.addLayer(layer);
    }
    ready = true;
    sync(store.state);
    view.fit();
    const clickable = ["network-nodes", "intersection-seeds", "intersection-fill", "legacy-fill"];
    map.on("click", (e) => {
      const hits = map.queryRenderedFeatures(e.point, { layers: clickable.filter((id) => map.getLayer(id)) });
      if (!hits.length) { store.select(null); return; }
      const hit = hits.find((h) => h.layer.id === "network-nodes") ?? hits[0];
      if (hit.layer.id === "network-nodes") store.select({ kind: "node", id: hit.properties.id });
      else if (hit.layer.id === "legacy-fill") store.select({ kind: "tile", variant: "legacy", id: hit.properties.nodeId });
      else store.select({ kind: "tile", variant: hit.properties.variant, id: hit.properties.nativeId });
    });
    for (const id of clickable) {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    }
    let dragging = null;
    let dragMoved = false;
    const preview = (lngLat) => map.getSource("drag-preview").setData(lngLat
      ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lngLat.lng, lngLat.lat] } }] }
      : EMPTY);
    const onMove = (e) => { if (dragging) { dragMoved = true; preview(e.lngLat); } };
    const finish = (e) => {
      if (!dragging) return;
      const id = dragging; dragging = null;
      const commit = dragMoved; dragMoved = false;
      map.dragPan.enable(); preview(null);
      map.off("mousemove", onMove); map.off("touchmove", onMove);
      map.off("mouseup", finish); map.off("touchend", finish);
      window.removeEventListener("mouseup", cancel);
      // A click fires mousedown and mouseup with no mousemove between them.
      // Without the `commit` guard it would move the node to the click point,
      // up to the consolidation tolerance away in a merged cluster, and mark
      // it moved, which breaks parity with the reference.
      if (e && commit) store.moveNode(id, e.lngLat.lng, e.lngLat.lat);
    };
    // The map hears a mouseup only over its canvas. A release anywhere else
    // reaches the window after any the map handled, and cancels the drag.
    const cancel = () => finish(null);
    const start = (e) => {
      const hit = map.queryRenderedFeatures(e.point, { layers: ["network-nodes"] })[0];
      if (!hit) return;
      e.preventDefault();
      dragging = hit.properties.id;
      store.select({ kind: "node", id: dragging });
      map.dragPan.disable();
      map.on("mousemove", onMove); map.on("touchmove", onMove);
      map.on("mouseup", finish); map.on("touchend", finish);
      window.addEventListener("mouseup", cancel);
    };
    map.on("mousedown", "network-nodes", start);
    map.on("touchstart", "network-nodes", start);
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") finish(null); });
  });
  map.on("moveend", () => syncGrid(store.state));
  store.subscribe(sync);

  const view = {
    map,
    fit() {
      const [w, s, e, n] = bundle.city.bounds;
      map.fitBounds([[w, s], [e, n]], { padding: 24, duration: reduced ? 0 : 600 });
    },
  };
  return view;
}
