import { buildGraph, setEngineCrs, moveNode, resetNode, resetAll } from "../engine/graph.js";
import { diffTiles } from "../engine/pipeline.js";
import { SLIVER_M2, layerStats, median } from "../engine/stats.js";
import { crsKey, forward, inverse, reprojectMulti } from "../engine/proj.js";
import { convexHull } from "../engine/geometry.js";
import { bufferCircle } from "../engine/cluster.js";
import { defaultEngine } from "./engine.js";

// Revealing one of these layers can trigger a compute, because
// each of them reads intersection output.
const TILE_LAYERS = ["tiles", "fallback", "seeds", "grid", "consolidation"];

const DEFAULT_LAYERS = {
  legacy: false, tiles: true, fallback: true, seeds: true, grid: false, consolidation: false, network: true, basemap: true, movedNodes: true, changedTiles: true,
};

// On open, the UI takes the defaults from the library's `TileParams()`, and
// test/defaults.test.js diffs them against test/fixtures/tile-params.json.
// degree_filter_min arrives with the bundle. The tool runs no inheritance, and
// the engine snaps seeds to corners by default, so those fields stay out.
export const DEFAULT_TILE_PARAMS = {
  toleranceM: 17,
  gridM: 24,
  contractTransitions: true,
  transitionOpts: { requireSameRoad: true, spreadMaxDeg: 60, oppositeMinDeg: 120 },
  clipBufferM: 250,
};

// The FallbackGrid() defaults from the library, checked against
// test/fixtures/fallback-grid.json.
export const DEFAULT_FALLBACK = { cellM: 250, minShare: 0.1 };

// In the store, panels and the map watch one graph copy, while the
// engine computes on its own, so moves and CRS changes must reach both.
export function createStore(bundle, { engine = defaultEngine(bundle) } = {}) {
  const graph = buildGraph(bundle);
  const p = bundle.params;
  const state = {
    bundle, graph,
    params: {
      toleranceM: DEFAULT_TILE_PARAMS.toleranceM, gridM: DEFAULT_TILE_PARAMS.gridM, minDegree: p.degree_filter_min,
      // The leg count that makes a node a junction for the merge. It keeps
      // the bundle's value while minDegree changes what the filter lists.
      junctionDegree: p.degree_filter_min,
      keepDeadEnds: true, contractTransitions: DEFAULT_TILE_PARAMS.contractTransitions,
      transitionOpts: { ...DEFAULT_TILE_PARAMS.transitionOpts },
      epsg: bundle.city.crs, clipBufferM: DEFAULT_TILE_PARAMS.clipBufferM, buildScope: "union",
      fallbackCellM: DEFAULT_FALLBACK.cellM, fallbackMinShare: DEFAULT_FALLBACK.minShare,
    },
    layers: { ...DEFAULT_LAYERS },
    results: { intersection: null, legacy: null },
    stats: { intersection: null, legacy: null },
    diff: { intersection: null },
    selection: null,
    warnings: [],
    busy: false,
    lastReason: "init",
    // The reason behind the request whose answer the diff came from, which
    // can lag lastReason while a compute runs.
    diffReason: null,
    timings: {},
  };
  // `state` gets `movedNodes()` too, since parity() reads the function
  // off `state`.
  const movedNodesFn = () => [...graph.nodes.values()].filter((n) => n.moved);
  state.movedNodes = movedNodesFn;
  const listeners = new Set();
  // A bad callback should not poison the rest of the loop, so notify
  // wraps every call in a try before moving on.
  const notify = () => {
    for (const fn of listeners) {
      try { fn(state); } catch (err) { console.error(err); }
    }
  };

  const bufferKey = () => String(Math.round(state.params.clipBufferM));

  // Each clip has a key marking its polygon, and under that key the
  // engine caches the prepared clip and tiles.
  function clipFor() {
    const { clips } = bundle;
    const { epsg, buildScope, clipBufferM } = state.params;
    const crs = crsKey(epsg);
    if (buildScope === "union") return { kind: "polygon", key: `union|${bufferKey()}|${crs}`, multi: reprojectMulti(clips.node_union[bufferKey()], bundle.city.crs, epsg) };
    if (buildScope === "boundary") return { kind: "polygon", key: `boundary|${bufferKey()}|${crs}`, multi: reprojectMulti(clips.boundary[bufferKey()], bundle.city.crs, epsg) };
    // The outline is graph_from_place's sever line. Dead on that
    // line, a seed may already sit beyond the clip.
    if (buildScope === "city") return { kind: "polygon", key: `city|${crs}`, multi: reprojectMulti(bundle.boundary, 4326, epsg) };
    // The hull follows the nodes, so its key carries the graph's version.
    if (buildScope === "hull") return { kind: "polygon", key: `hull|${clipBufferM}|${crs}|${graph.version}`, multi: [[legacyScopeLonLat().map(([lon, lat]) => forward(epsg, lon, lat))]] };
    return { kind: "bbox", key: `bbox|${clipBufferM}`, bufferM: clipBufferM };
  }

  // The old recipe carves output from a hull around the whole road net, stubs
  // included, where the union clip leaves street ends out. viz fattens that
  // hull by the clip buffer, projects the result to degrees, and hulls once
  // more on that side, for the recipe counts in degrees.
  function legacyScopeLonLat() {
    const { epsg, clipBufferM } = state.params;
    const hullXY = convexHull([...state.graph.nodes.values()].map((n) => [n.x, n.y]));
    const discs = hullXY.flatMap(([x, y]) => bufferCircle(x, y, clipBufferM));
    return convexHull(discs.map(([x, y]) => inverse(epsg, x, y)));
  }

  function applyTiles(result) {
    state.diff.intersection = state.results.intersection ? diffTiles(state.results.intersection.tiles, result.tiles) : { added: new Set(), removed: new Set(), changed: new Set() };
    state.results.intersection = result;
    state.stats.intersection = layerStats(result.tiles);
  }

  function applyLegacy(result) {
    state.results.legacy = result;
    const areas = result.tiles.map((t) => t.areaM2);
    state.stats.legacy = { count: result.tiles.length, medianArea: median(areas), sliverCount: areas.filter((a) => a < SLIVER_M2).length };
  }

  function needsTiles() { return TILE_LAYERS.some((id) => state.layers[id]); }

  // A newer ask replaces any older one still waiting: while the engine
  // works, the queued ask gives way and stale answers fall on the floor,
  // so even a storm of edits wastes at most one compute.
  let seq = 0, inFlight = false, queued = null;
  // The gridded fallback computes only under a visible layer. Later, flipping
  // that layer on queues a fresh run wherever an earlier run left it out.
  let intersectionHasFallback = false;
  // Tile work is skipped whenever no tile layer is visible, and legacy work too
  // while its switch stays dark. Edits made inside such a window date the
  // results on hand. Those wait until a layer reappears and demands output.
  let tilesStale = false, legacyStale = false;
  // While an unanswered legacy ask is in the air it counts as owed. A
  // replacement must resend legacy work too, since the edit behind the
  // first ask shifted nodes or the outline that legacy output hangs from.
  let legacyOwed = false;
  function fallbackExtent() {
    if (!state.layers.fallback || !bundle.clips.extent) return {};
    return { extent: reprojectMulti(bundle.clips.extent, bundle.city.crs, state.params.epsg), extentKey: `extent|${crsKey(state.params.epsg)}` };
  }
  function recompute(reason, { legacyToo = true } = {}) {
    state.busy = true; state.lastReason = reason;
    notify();
    // Clip and scope re-projection can blow up. Recovery clears the
    // busy flag, so the indicator stops spinning.
    try {
      setEngineCrs(graph, state.params.epsg);
      const wantLegacy = state.layers.legacy && (legacyToo || legacyOwed || !state.results.legacy);
      const msg = {
        type: "compute", epsg: state.params.epsg,
        intersection: needsTiles() ? { params: { ...state.params, transitionOpts: { ...state.params.transitionOpts } }, clip: { ...clipFor(), ...fallbackExtent() } } : null,
        legacy: wantLegacy ? { scopeLonLat: legacyScopeLonLat(), utmEpsg: bundle.city.utm_epsg } : null,
      };
      // Only a request that was built supersedes the one in flight, so a
      // failure above leaves that one's answer to land.
      if (!msg.intersection) tilesStale = true;
      if (legacyToo && !state.layers.legacy) legacyStale = true;
      if (msg.legacy) legacyOwed = true;
      queued = { seq: ++seq, reason, msg };
    } catch (err) {
      state.warnings = [`engine: ${err.message}`];
      console.error(err);
      state.busy = false;
      notify();
      return;
    }
    pump();
  }
  // Asking for both tilings sends two messages, tiles leading, so a
  // plodding legacy pass does not keep the tiles waiting. If a newer ask
  // appears before a half has left, that half is dropped.
  function pump() {
    if (inFlight || !queued) return;
    const { seq: mine, reason, msg } = queued;
    queued = null; inFlight = true;
    const parts = msg.intersection && msg.legacy ? [{ ...msg, legacy: null }, { ...msg, intersection: null }] : [msg];
    const send = (k) => engine.send(parts[k]).then((reply) => {
      if (mine !== seq) return undefined;
      apply(reply, parts[k], reason, { first: k === 0, last: k === parts.length - 1 });
      return k + 1 < parts.length ? send(k + 1) : undefined;
    });
    send(0).catch((err) => {
      if (mine !== seq) return;
      state.warnings = [`engine: ${err.message}`];
      console.error(err);
      state.busy = false;
      notify();
    }).finally(() => { inFlight = false; pump(); });
  }
  function apply(reply, msg, reason, { first = true, last = true } = {}) {
    state.warnings = first ? reply.warnings : [...state.warnings, ...reply.warnings];
    if (reply.intersection) {
      // The result carries the CRS and grid it was computed in, and the
      // map and the inspector draw from those while a later request runs.
      reply.intersection.epsg = msg.epsg;
      reply.intersection.gridM = msg.intersection.params.gridM;
      applyTiles(reply.intersection);
      state.diffReason = reason;
      intersectionHasFallback = !!msg.intersection.clip.extent;
      tilesStale = false;
    }
    if (msg.legacy) legacyOwed = false;
    if (reply.legacy) { applyLegacy(reply.legacy); legacyStale = false; }
    Object.assign(state.timings, reply.timings);
    state.busy = !last;
    notify();
  }
  // The engine handles messages in the order sent, so a move reaches its
  // graph before the next compute. The reply is empty.
  const tell = (msg) => { engine.send(msg).catch(() => {}); };
  // Waiters await this promise, which resolves once the pipes idle,
  // rather than trusting a sleep.
  function settled() {
    return new Promise((resolve) => {
      const check = () => (inFlight || queued ? setTimeout(check, 5) : resolve(state));
      check();
    });
  }

  const store = {
    state,
    engine,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    setParam(name, value) {
      state.params[name] = value;
      if (name === "fallbackCellM" && !state.layers.fallback) { intersectionHasFallback = false; notify(); return; }
      recompute(`param:${name}`, { legacyToo: name === "clipBufferM" || name === "epsg" });
    },
    setTransitionOpt(name, value) { state.params.transitionOpts[name] = value; recompute("param:transitions", { legacyToo: false }); },
    setLayer(id, visible) {
      state.layers[id] = visible;
      const missing = (id === "legacy" && visible && (!state.results.legacy || legacyStale))
        || (TILE_LAYERS.includes(id) && visible && (!state.results.intersection || tilesStale))
        || (id === "fallback" && visible && !intersectionHasFallback && !!bundle.clips.extent);
      if (missing) recompute(`layer:${id}`); else notify();
    },
    select(selection) { state.selection = selection; notify(); },
    // Outlines mark node edits only. A parameter change rebuilds most of
    // the map and the stats already report the counts, so an outline
    // there would repeat them. Switched-off tiles show none.
    changedTiles() {
      const d = state.diff.intersection;
      if (!/^(move|reset):/.test(state.diffReason ?? "") || !d || !state.layers.tiles) return new Set();
      return new Set([...d.added, ...d.changed]);
    },
    moveNode(id, lon, lat) { moveNode(graph, id, lon, lat); tell({ type: "move", id, lon, lat }); recompute(`move:${id}`); },
    // dx/dy apply on the engine plane the inspector displays, in the
    // active CRS. A zero-delta nudge bails before any lon/lat
    // conversion could stamp the node as travelled on float wobble.
    nudgeNode(id, dxM, dyM) {
      if (!dxM && !dyM) return;
      const n = graph.nodes.get(id);
      const [lon, lat] = inverse(state.params.epsg, n.x + dxM, n.y + dyM);
      store.moveNode(id, lon, lat);
    },
    resetNode(id) { resetNode(graph, id); tell({ type: "reset", id }); recompute(`reset:${id}`); },
    resetAll() { resetAll(graph); tell({ type: "resetAll" }); recompute("reset:all"); },
    recompute,
    settled,
    legacyScopeLonLat,
    movedNodes: movedNodesFn,
  };
  recompute("init");
  return store;
}
