import "maplibre-gl/dist/maplibre-gl.css";
import { createStore } from "./state.js";
import { createMap } from "./map.js";
import { renderLayerKey, renderParams } from "./params-panel.js";
import { renderResults } from "./results-panel.js";
import { bundlePath, isValidSlug } from "./slugs.js";

async function loadIndex() {
  const res = await fetch("/data/index.json");
  if (!res.ok) throw new Error("data/index.json is missing; run flagstones viz-export.");
  return (await res.json()).bundles;
}

async function loadBundle(slug) {
  // Names for bundles arrive from ?city and from index.json; each
  // turns into the request path. So a hostile slug gets rejected before any
  // fetch, and it looks just like a bundle that is simply missing.
  if (!isValidSlug(slug)) throw new Error(`no bundle for ${slug}; run flagstones viz-export.`);
  const res = await fetch(bundlePath(slug));
  if (!res.ok) throw new Error(`no bundle for ${slug}; run flagstones viz-export.`);
  return res.json();
}

async function boot(slug) {
  const provenance = document.getElementById("provenance");
  provenance.textContent = `loading ${slug}`;
  const bundle = await loadBundle(slug);
  const store = createStore(bundle);
  provenance.textContent = `${bundle.city.name} · osmnx ${bundle.city.osmnx_version} · ${bundle.city.graph_nodes} nodes · ${bundle.city.crs}`;
  // Boot paints panels before the map, so a browser with no WebGL
  // context still hands tests its store, its stats and its inspector.
  renderLayerKey(document.getElementById("layer-key"), store);
  renderParams(document.getElementById("params"), store);
  renderResults(document.getElementById("results"), store);
  let view = null;
  try {
    document.getElementById("map").replaceChildren();
    view = createMap(document.getElementById("map"), store);
  } catch (err) {
    console.error("map init failed; this browser may lack WebGL:", err);
    provenance.textContent += " · map unavailable";
  }
  window.tileViz = { store, view };
  return store;
}

async function start() {
  const bundles = await loadIndex();
  if (!bundles.length) throw new Error("data/index.json lists no bundles; run flagstones viz-export.");
  const select = document.getElementById("city");
  for (const b of bundles) {
    const opt = document.createElement("option");
    opt.value = b.slug; opt.textContent = b.name;
    select.append(opt);
  }
  const initial = new URLSearchParams(location.search).get("city") ?? bundles[0].slug;
  select.value = initial;
  select.addEventListener("change", () => {
    const url = new URL(location.href);
    url.searchParams.set("city", select.value);
    location.assign(url);
  });
  return boot(initial);
}

start().catch((err) => {
  document.getElementById("provenance").textContent = err.message;
  console.error(err);
});
