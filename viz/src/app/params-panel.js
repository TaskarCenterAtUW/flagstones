import { LAYER_KEY } from "./layers.js";
import { collisionBound, gridOverBoundWarning } from "../engine/snap.js";
import { crsKey, crsLabel, utmZoneEpsg } from "../engine/proj.js";

function swatchSvg(s) {
  if (s.dot) return `<svg class="tp-swatch" viewBox="0 0 28 14" aria-hidden="true"><circle cx="14" cy="7" r="4" fill="${s.dot}"/></svg>`;
  if (s.stroke) return `<svg class="tp-swatch" viewBox="0 0 28 14" aria-hidden="true"><rect x="0" y="0" width="28" height="14" fill="${s.fill ?? "none"}"/><line x1="0" y1="7" x2="28" y2="7" stroke="${s.stroke}" stroke-width="${s.width ?? 1.5}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/></svg>`;
  return `<svg class="tp-swatch" viewBox="0 0 28 14" aria-hidden="true"><rect x="0" y="0" width="28" height="14" fill="${s.fill}" stroke="#C8D2CB"/></svg>`;
}

export function renderLayerKey(container, store) {
  const list = document.createElement("ul");
  list.className = "tp-key app-map-key";
  for (const entry of LAYER_KEY) {
    const li = document.createElement("li");
    li.innerHTML = `<label class="tp-key-row"><input type="checkbox" class="form-check-input" data-layer="${entry.id}"> ${swatchSvg(entry.swatch)}<span>${entry.label}</span></label>`;
    const box = li.querySelector("input");
    box.checked = store.state.layers[entry.id];
    box.addEventListener("change", () => store.setLayer(entry.id, box.checked));
    list.append(li);
  }
  container.append(list);
  store.subscribe((state) => {
    for (const box of list.querySelectorAll("input[data-layer]")) box.checked = state.layers[box.dataset.layer];
  });
}

// The clip buffers the export stored clips at: the standard buffers, plus the
// reference's own buffer when it is another whole number of meters.
export function clipBufferStops(bundle) {
  return Object.keys(bundle.clips.node_union).map(Number).sort((a, b) => a - b);
}

export function stepIndex(stops, value) {
  let best = 0;
  for (let i = 1; i < stops.length; i++) if (Math.abs(stops[i] - value) < Math.abs(stops[best] - value)) best = i;
  return best;
}

export const PARAM_CONTROLS = [
  { name: "toleranceM", kind: "range", label: "Consolidation tolerance", min: 0.5, max: 50, step: 0.5, unit: "m",
    help: "The buffer radius osmnx places around each node. Buffers that overlap merge their nodes, so nodes merge within twice this distance. Boeing's published models use 10 m, hence 5 m here. The Consolidation buffers layer shows the buffers." },
  { name: "gridM", kind: "range", label: "Seed grid", min: 1, max: 70.5, step: 0.5, unit: "m",
    help: "The cell a seed snaps to, and with it the tile id. Stay under the tolerance times root two, or clusters more than twice the tolerance apart may share a cell and an id." },
  { name: "minDegree", kind: "select", label: "Degree filter", options: [1, 2, 3, 4, 5].map((v) => ({ value: v, label: `${v} or more legs` })),
    help: "Only junctions with at least this many road legs seed a tile. 1 keeps every cluster that has a road leg, 2 follows Boeing's definition, and the library defaults to 3." },
  { name: "keepDeadEnds", kind: "switch", label: "Keep dead ends through consolidation",
    help: "On runs the library's rule. Off follows osmnx's dead_ends=False, which drops the third leg at a cul-de-sac junction." },
  { name: "contractTransitions", kind: "switch", label: "Contract carriageway transitions",
    help: "Where a road's one-way carriageways join or fork, that node neither seeds a tile nor merges. Neighboring junctions still count it as a leg." },
  { name: "clipBufferM", kind: "steps", label: "Clip buffer", stops: [], unit: "m", // filled per city
    help: "How far the clip reaches outward, so edge tiles avoid stopping at a line no pedestrian recognizes. Each stop marks a buffer the export computed a clip at." },
  // After the 0 stop, cells open at 100 m. Cell count climbs with the
  // inverse square of cell size and drags recompute time along with it.
  { name: "fallbackCellM", kind: "steps", label: "Fallback cell", unit: "m",
    stops: [0, ...Array.from({ length: 37 }, (_, k) => 100 + 25 * k)],
    help: "Square cells set against the CRS origin cover the ground intersection tiles leave open. A piece under a tenth of a cell joins the neighbor with which it shares the longest edge, and 0 keeps the cells off. The library default of 250 m matches the clip buffer." },
  { name: "buildScope", kind: "radio", label: "Build scope", options: [
      { value: "union", label: "Union of the buffered nodes, street ends left out" },
      { value: "bbox", label: "Bounding box of the seeds plus buffer" },
      { value: "boundary", label: "City boundary plus buffer" },
      { value: "city", label: "City boundary" },
      { value: "hull", label: "Convex hull of the buffered nodes" },
    ], help: "The union follows the library's rule. The exporter computes references for the bounding box and the buffered boundary as well, and the hull carries the legacy tiling's scope. Buffered scopes hold every seed, so only edge tiles move. That bare city boundary is where the graph was cut, and a junction sitting on the line can lose its tile." },
  { name: "epsg", kind: "select", label: "Computation CRS", options: [], // filled per city
    help: "Reference tiles come from the bundle's CRS. The UTM zone shows what a conformal CRS does to the cells, and the Legacy tiling layer shows cells computed in degrees." },
];

const TRANSITION_OPTS = [
  { name: "requireSameRoad", kind: "switch", label: "Require the legs to share a name or ref" },
  { name: "spreadMaxDeg", kind: "range", label: "Max angle between the carriageway legs", min: 10, max: 90, step: 5, unit: "°" },
  { name: "oppositeMinDeg", kind: "range", label: "Min angle to the continuation", min: 90, max: 180, step: 5, unit: "°" },
];

// True from a slider's first input event until its change event, or until
// it loses focus when the thumb comes back to where it started and no change
// fires.
function holdWhileEditing(input) {
  let editing = false;
  input.addEventListener("input", () => { editing = true; });
  input.addEventListener("change", () => { editing = false; });
  input.addEventListener("blur", () => { editing = false; });
  return () => editing;
}

function field(control, value, onChange) {
  const wrap = document.createElement("div");
  wrap.className = "tp-control";
  const id = `param-${control.name}`;
  if (control.kind === "range") {
    wrap.innerHTML = `<label for="${id}"><span>${control.label}</span><output for="${id}" class="tp-readout"></output></label>
      <input type="range" class="form-range" id="${id}" min="${control.min}" max="${control.max}" step="${control.step}">
      <small class="tp-help">${control.help ?? ""}</small>`;
    const input = wrap.querySelector("input"), out = wrap.querySelector("output");
    input.value = value; out.textContent = `${value} ${control.unit}`;
    // The readout follows the thumb while the recompute waits for the
    // release, because a drag fires dozens of input events and a Seattle
    // recompute takes seconds. A store notification during the drag leaves
    // the thumb where it is.
    const editing = holdWhileEditing(input);
    input.addEventListener("input", () => { out.textContent = `${input.value} ${control.unit}`; });
    input.addEventListener("change", () => onChange(Number(input.value)));
    wrap.set = (v) => { if (editing()) return; input.value = v; out.textContent = `${v} ${control.unit}`; };
  } else if (control.kind === "steps") {
    const { stops, unit } = control;
    const label = (i) => `${stops[i]} ${unit}`;
    wrap.innerHTML = `<label for="${id}"><span>${control.label}</span><output for="${id}" class="tp-readout"></output></label>
      <input type="range" class="form-range" id="${id}" min="0" max="${stops.length - 1}" step="1" list="${id}-stops">
      <datalist id="${id}-stops">${stops.map((v, i) => `<option value="${i}" label="${v} ${unit}"></option>`).join("")}</datalist>
      <small class="tp-help">${control.help ?? ""}</small>`;
    const input = wrap.querySelector("input"), out = wrap.querySelector("output");
    input.value = stepIndex(stops, value); out.textContent = label(Number(input.value));
    const editing = holdWhileEditing(input);
    input.addEventListener("input", () => { out.textContent = label(Number(input.value)); });
    input.addEventListener("change", () => onChange(stops[Number(input.value)]));
    wrap.set = (v) => { if (editing()) return; input.value = stepIndex(stops, v); out.textContent = label(Number(input.value)); };
  } else if (control.kind === "select") {
    wrap.innerHTML = `<label for="${id}"><span>${control.label}</span></label><select class="form-select" id="${id}"></select><small class="tp-help">${control.help ?? ""}</small>`;
    const select = wrap.querySelector("select");
    for (const o of control.options) { const opt = document.createElement("option"); opt.value = String(o.value); opt.textContent = o.label; select.append(opt); }
    select.value = String(value);
    select.addEventListener("change", () => onChange(typeof control.options[0].value === "number" ? Number(select.value) : select.value));
    wrap.set = (v) => { select.value = String(v); };
  } else if (control.kind === "switch") {
    wrap.innerHTML = `<div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" id="${id}"><label class="form-check-label" for="${id}">${control.label}</label></div><small class="tp-help">${control.help ?? ""}</small>`;
    const input = wrap.querySelector("input");
    input.checked = !!value;
    input.addEventListener("change", () => onChange(input.checked));
    wrap.set = (v) => { input.checked = !!v; };
  } else if (control.kind === "radio") {
    wrap.innerHTML = `<fieldset><legend>${control.label}</legend></fieldset><small class="tp-help">${control.help ?? ""}</small>`;
    const fs = wrap.querySelector("fieldset");
    for (const o of control.options) {
      const rid = `${id}-${o.value}`;
      const row = document.createElement("div");
      row.className = "form-check";
      row.innerHTML = `<input class="form-check-input" type="radio" name="${id}" id="${rid}" value="${o.value}"><label class="form-check-label" for="${rid}">${o.label}</label>`;
      const input = row.querySelector("input");
      input.checked = o.value === value;
      input.addEventListener("change", () => input.checked && onChange(o.value));
      fs.append(row);
    }
    wrap.set = (v) => { for (const i of fs.querySelectorAll("input")) i.checked = i.value === String(v); };
  }
  return wrap;
}

export function renderParams(container, store) {
  const { bundle } = store.state;
  const utm = bundle.city.utm_epsg ?? utmZoneEpsg(...bundle.city.center);
  const clipStops = clipBufferStops(bundle);
  const controls = PARAM_CONTROLS.map((c) => c.name === "epsg"
    ? { ...c, options: [bundle.city.crs, utm].map((v) => ({ value: crsKey(v), label: crsLabel(v) })) }
    : c.name === "clipBufferM" ? { ...c, stops: clipStops } : c);
  const fields = new Map();
  const form = document.createElement("form");
  form.className = "tp-params";
  form.addEventListener("submit", (e) => e.preventDefault());
  for (const c of controls) {
    const onChange = (v) => store.setParam(c.name, v);
    const f = field(c, store.state.params[c.name], onChange);
    fields.set(c.name, f);
    form.append(f);
    if (c.name === "contractTransitions") {
      const details = document.createElement("details");
      details.className = "tp-details";
      details.innerHTML = "<summary>Transition rules</summary>";
      for (const t of TRANSITION_OPTS) {
        const onOpt = (v) => store.setTransitionOpt(t.name, v);
        details.append(field(t, store.state.params.transitionOpts[t.name], onOpt));
      }
      form.append(details);
    }
  }
  const bound = document.createElement("p");
  bound.className = "tp-help tp-bound";
  form.append(bound);
  // Read from the live params instead of a recompute's warnings, so it shows
  // as soon as the grid crosses the bound.
  const gridWarn = document.createElement("p");
  gridWarn.className = "tp-help tp-grid-warn";
  gridWarn.setAttribute("role", "status");
  gridWarn.hidden = true;
  fields.get("gridM").after(gridWarn);
  const reset = document.createElement("button");
  reset.type = "button"; reset.className = "btn btn-outline-primary"; reset.textContent = "Reset moved nodes";
  reset.addEventListener("click", () => store.resetAll());
  form.append(reset);
  const alerts = document.createElement("div");
  alerts.setAttribute("role", "status");
  form.append(alerts);
  container.append(form);

  const refresh = (state) => {
    for (const [name, f] of fields) f.set(state.params[name]);
    bound.textContent = `At this tolerance the collision bound is ${collisionBound(state.params.toleranceM).toFixed(2)} m. Nodes moved: ${store.movedNodes().length}.`;
    const over = gridOverBoundWarning(state.params.toleranceM, state.params.gridM);
    const lines = over ? [over] : [];
    const colliding = state.results.intersection?.tiles.filter((t) => t.seedCollision).length ?? 0;
    if (colliding) lines.push(`${colliding} tile${colliding === 1 ? "" : "s"} took clusters sharing a grid cell, drawn red on the map.`);
    gridWarn.textContent = lines.join("\n");
    gridWarn.hidden = lines.length === 0;
    gridWarn.classList.toggle("tp-grid-warn--colliding", colliding > 0);
    alerts.replaceChildren(...state.warnings.map((w) => { const d = document.createElement("div"); d.className = "alert alert-warning"; d.textContent = w; return d; }));
  };
  refresh(store.state);
  store.subscribe(refresh);
}
