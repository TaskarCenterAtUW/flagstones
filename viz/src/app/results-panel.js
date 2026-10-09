import { fmtInt, fmtM2, fmtPct, fmtM } from "./format.js";
import { crsKey, inverse } from "../engine/proj.js";
import { legEdges, legs, neighbors } from "../engine/graph.js";
import { classifyNode, roadNames } from "../engine/transitions.js";

const VARIANTS = [["intersection", "Intersection"], ["legacy", "Legacy"]];

function enabled(state) {
  return VARIANTS.filter(([v]) => state.results[v] && (v !== "legacy" || state.layers.legacy));
}

// The map files a tie under both nodes, so halving the count turns
// those entries back into pairs.
function tiePairs(ties) {
  let n = 0;
  for (const partners of (ties ?? new Map()).values()) n += partners.length;
  return n / 2;
}

export function statRows(state) {
  const s = state.stats, r = state.results;
  const row = (label, fn) => ({ label, values: VARIANTS.map(([v]) => (s[v] ? fn(v, s[v], r[v]) ?? "" : "")) });
  return [
    row("Tiles", (v, st) => fmtInt(st.count)),
    row("Merged seeds", (v, st) => v === "legacy" ? "n/a" : `${fmtInt(st.mergedCount)} (${fmtPct(st.mergedShare)})`),
    row("Degree 3 / 4 / 5+", (v, st) => v === "legacy" ? "n/a" : `${fmtPct(st.degreeShare.d3)} / ${fmtPct(st.degreeShare.d4)} / ${fmtPct(st.degreeShare.d5plus)}`),
    row("Median area (IQR)", (v, st) => v === "legacy" ? fmtM2(st.medianArea) : `${fmtM2(st.medianArea)} (${fmtInt(st.q25Area)} to ${fmtInt(st.q75Area)})`),
    row("Slivers under 100 m²", (v, st) => fmtInt(st.sliverCount)),
    row("Truncated by the clip", (v, st) => v === "legacy" ? "" : fmtInt(st.truncatedCount)),
    row("Seed-collision tiles", (v, st) => v === "legacy" ? "" : fmtInt(st.collisionCount)),
    row("Snap distance median / max", (v, st) => v === "legacy" ? "" : `${fmtM(st.snapMedian)} / ${fmtM(st.snapMax)}`),
    row("Clusters", (v, st, res) => v === "legacy" ? "" : fmtInt(res.clusters.length)),
    row("Dead-end clusters removed", (v, st, res) => v === "legacy" ? "" : fmtInt(res.dropped.deadEnds)),
    row("Degree-2 clusters removed", (v, st, res) => v === "legacy" ? "" : fmtInt(res.dropped.degree2)),
    row("Transitions contracted", (v, st, res) => v === "legacy" ? "" : fmtInt(res.dropped.transitions)),
    row("Ties across a median", (v, st, res) => v === "legacy" ? "" : fmtInt(tiePairs(res.ties))),
    row("Compute time", (v) => state.timings[v] != null ? `${state.timings[v].toFixed(0)} ms` : ""),
  ];
}

// When a reference's params list no transition rules, that run kept
// contraction off.
export function referenceTransitions(r) {
  return {
    contractTransitions: !!r.contract_transitions,
    transitionOpts: {
      requireSameRoad: r.transition_require_same_road ?? true,
      spreadMaxDeg: r.transition_spread_max_deg ?? 60,
      oppositeMinDeg: r.transition_opposite_min_deg ?? 120,
    },
  };
}

function atReferenceParams(state) {
  const p = state.params, r = state.bundle.reference?.params;
  if (!r) return false;
  const ref = referenceTransitions(r);
  const sameTransitions = p.contractTransitions === ref.contractTransitions
    && (!p.contractTransitions || ["requireSameRoad", "spreadMaxDeg", "oppositeMinDeg"].every((k) => p.transitionOpts[k] === ref.transitionOpts[k]));
  return p.toleranceM === r.consolidation_tolerance_m && p.gridM === r.seed_grid_m && p.minDegree === r.degree_filter_min
    && p.keepDeadEnds && sameTransitions && crsKey(p.epsg) === crsKey(state.bundle.city.crs) && p.clipBufferM === r.clip_buffer_m
    && (p.snapCentroid ?? true) === (r.snap_centroid ?? true) && state.movedNodes().length === 0;
}

// The export includes a Python run for the node union, the seeds' bounding box
// and the buffered boundary. Tile IDs come from the seeds. The hull contains
// all of them, so it compares against the bbox run. So does the bare
// boundary, which reports fewer matches where it drops a seed on its edge.
const REFERENCE_BY_SCOPE = { union: "node_union", bbox: "bbox", hull: "bbox", city: "bbox", boundary: "boundary" };

export function parity(state) {
  if (!atReferenceParams(state) || !state.results.intersection) return null;
  const reference = REFERENCE_BY_SCOPE[state.params.buildScope];
  const ref = state.bundle.reference.variants?.[reference];
  if (!ref) return null;
  const ours = new Set(state.results.intersection.tiles.map((t) => t.nativeId));
  return { matched: ref.tiles.filter((t) => ours.has(t.id)).length, total: ref.tiles.length, reference };
}

export function describeTile(state, variant, id) {
  if (variant === "legacy") {
    const t = state.results.legacy?.tiles.find((x) => x.nodeId === id);
    return t ? { variant, nodeId: t.nodeId, seedLonLat: [t.seedLon, t.seedLat], area: t.areaM2 } : null;
  }
  const res = state.results[variant];
  const t = res?.tiles.find((x) => x.nativeId === id);
  if (!t) return null;
  const names = new Set();
  for (const osmid of t.osmids) {
    for (const nbr of legs(state.graph, osmid).keys()) for (const n of roadNames(legEdges(state.graph, osmid, nbr))) names.add(n);
  }
  // Each result's seed sits in the CRS its computation used, which trails
  // the current parameter until a CRS switch finishes.
  const epsg = res.epsg ?? state.params.epsg;
  return {
    variant, nativeId: t.nativeId, cell: [t.cellX, t.cellY], seed: [t.seedX, t.seedY], seedLonLat: inverse(epsg, t.seedX, t.seedY),
    raw: [t.rawX, t.rawY], snapDistance: Math.hypot(t.seedX - t.rawX, t.seedY - t.rawY), nodeCount: t.nodeCount, degree: t.degree,
    osmids: t.osmids, roads: [...names].sort(), area: t.area, truncated: t.truncated,
  };
}

export function describeNode(state, id) {
  const g = state.graph, n = g.nodes.get(id);
  if (!n) return null;
  const legRows = neighbors(g, id).map((m) => {
    const l = legs(g, id).get(m);
    return { to: m, direction: l.out && l.in ? "two-way" : l.out ? "one-way out" : "one-way in", roads: [...roadNames(legEdges(g, id, m))].sort() };
  });
  const transition = classifyNode(g, id, state.params.transitionOpts);
  const res = state.results.intersection;
  return {
    id, lonLat: [n.lon, n.lat], engineXY: [n.x, n.y], streetCount: n.sc, moved: n.moved,
    movedFrom: n.moved ? [n.lon0, n.lat0] : null, legs: legRows, transition, tile: res ? nodeTile(res, id) : null,
  };
}

// The tile seeded by a node, plus the reason when there is none. A snap
// cell joins its clusters into a single tile, so the search runs by the
// node's osmid.
function nodeTile(res, id) {
  const t = res.tiles.find((x) => x.osmids.includes(id));
  if (t) return t.nativeId;
  const ci = res.clusterOf.get(id);
  const c = ci == null ? null : res.clusters[ci];
  if (!c) return "none";
  if (c.excluded) return `cluster ${ci} (contracted transition)`;
  if (!res.seeds.includes(c)) return `cluster ${ci} (filtered, degree ${c.degree})`;
  return `cluster ${ci} (no tile in this clip)`;
}

function kv(pairs) {
  const dl = document.createElement("dl");
  dl.className = "tp-kv";
  for (const [k, v] of pairs) {
    if (v == null || v === "") continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  }
  return dl;
}

export function renderResults(container, store) {
  const stats = document.getElementById("stats");
  const inspect = document.getElementById("inspect");
  const table = document.createElement("table");
  table.className = "table table-sm tp-stats";
  const extras = document.createElement("div");
  extras.className = "tp-extras";
  stats.append(table, extras);
  const body = document.createElement("div");
  inspect.append(body);

  function renderStats(state) {
    const cols = enabled(state);
    table.innerHTML = `<thead><tr><th scope="col">Figure</th>${cols.map(([, l]) => `<th scope="col">${l}</th>`).join("")}</tr></thead>`;
    const tbody = document.createElement("tbody");
    for (const row of statRows(state)) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<th scope="row">${row.label}</th>` + cols.map(([v]) => `<td>${row.values[VARIANTS.findIndex(([x]) => x === v)]}</td>`).join("");
      tbody.append(tr);
    }
    table.append(tbody);

    const lines = [];
    const intersection = state.stats.intersection, legacy = state.stats.legacy;
    if (intersection && legacy && state.layers.legacy) lines.push(["Intersection / legacy tile ratio", (intersection.count / legacy.count).toFixed(3)]);
    const par = parity(state);
    lines.push(["Python reference parity", par ? `${fmtInt(par.matched)} / ${fmtInt(par.total)} ids (${par.reference})` : "params or moved nodes break the comparison"]);
    const d = state.diff.intersection;
    if (d) lines.push([`Last change (${state.diffReason ?? state.lastReason})`, `${d.added.size} added, ${d.changed.size} changed, ${d.removed.size} removed`]);
    extras.replaceChildren(kv(lines));
  }

  // The nudge form belongs to the node on display. Re-rendering that same
  // node keeps the form in place, so a store notification cannot wipe a
  // half-typed value.
  let nudge = null;

  function renderInspect(state) {
    const sel = state.selection;
    document.getElementById("inspect-empty").hidden = !!sel;
    if (sel?.kind !== "node") nudge = null;
    if (!sel) { body.replaceChildren(); return; }
    if (sel.kind === "tile") {
      const d = describeTile(state, sel.variant, sel.id);
      if (!d) { body.replaceChildren(kv([["Tile", "the last recompute removed this tile"]])); return; }
      body.replaceChildren(kv([
        ["Tile", d.nativeId ?? `Legacy seed ${d.nodeId}`], ["Variant", d.variant], ["Cell", d.cell?.join(", ")],
        ["Seed", d.seed ? `${d.seed[0].toFixed(2)}, ${d.seed[1].toFixed(2)}` : null], ["Seed lon/lat", d.seedLonLat.map((x) => x.toFixed(6)).join(", ")],
        ["Raw centroid", d.raw ? `${d.raw[0].toFixed(2)}, ${d.raw[1].toFixed(2)} (snapped ${fmtM(d.snapDistance)})` : null],
        ["Nodes", d.nodeCount], ["Degree", d.degree], ["OSM ids", d.osmids?.join(", ")], ["Roads", d.roads?.join("; ")],
        ["Area", fmtM2(d.area)], ["Truncated by clip", d.truncated == null ? null : d.truncated ? "yes" : "no"],
      ]));
      return;
    }
    const d = describeNode(state, sel.id);
    const list = kv([
      ["Node", d.id], ["Street count", d.streetCount], ["Lon/lat", d.lonLat.map((x) => x.toFixed(6)).join(", ")],
      ["Engine x/y", `${d.engineXY[0].toFixed(2)}, ${d.engineXY[1].toFixed(2)}`],
      ["Legs", d.legs.map((l) => `${l.to} ${l.direction}${l.roads.length ? ` (${l.roads.join(", ")})` : ""}`).join("; ")],
      ["Transition", d.transition ? `${d.transition.kind} on ${d.transition.roads.join(", ")}; spread ${d.transition.spread.toFixed(0)}°, continuation ${d.transition.opposite.toFixed(0)}°` : "no"],
      ["Tile", d.tile],
      ["Moved from", d.movedFrom ? d.movedFrom.map((x) => x.toFixed(6)).join(", ") : null],
    ]);
    if (nudge?.id === d.id && nudge.form.parentNode === body) {
      body.firstChild.replaceWith(list);
      return;
    }
    const form = document.createElement("form");
    form.className = "tp-nudge";
    form.innerHTML = `<label class="tp-field"><span>dx (m)</span><input class="form-control" name="dx" type="number" step="0.5" value="0"></label>
      <label class="tp-field"><span>dy (m)</span><input class="form-control" name="dy" type="number" step="0.5" value="0"></label>
      <button class="btn btn-primary" type="submit">Move</button>
      <button class="btn btn-outline-primary" type="button" data-reset>Reset node</button>`;
    form.addEventListener("submit", (e) => { e.preventDefault(); store.nudgeNode(d.id, Number(form.dx.value), Number(form.dy.value)); });
    form.querySelector("[data-reset]").addEventListener("click", () => store.resetNode(d.id));
    nudge = { id: d.id, form };
    body.replaceChildren(list, form);
  }

  const refresh = (state) => { renderStats(state); renderInspect(state); };
  refresh(store.state);
  store.subscribe(refresh);
}
