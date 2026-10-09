// An end-to-end checker that runs its checks in order against shared page
// state. It drives the built app through the real UI and checks what WebGL
// draws. The run needs a Chromium that creates a WebGL context (headless
// Chromium with the SwiftShader software GPU) and the data bundles, so
// `npm run verify:webgl` starts it after a build instead of under
// `node --test`. A failed check prints FAIL, and the script exits non-zero.
import { spawn } from "node:child_process";
import { cpSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
// Vite starts through this Node binary and its own entry script. On Windows
// `npx` is a .cmd shim, which `spawn` cannot start without a shell.
const VITE = join(root, "node_modules/vite/bin/vite.js");
const shots = (name) => join(root, "screenshots", name);

mkdirSync(join(root, "screenshots"), { recursive: true });
mkdirSync(join(root, "dist/data"), { recursive: true });
cpSync(join(root, "data"), join(root, "dist/data"), { recursive: true });
const server = spawn(process.execPath, [VITE, "preview", "--port", "4173", "--strictPort"], { cwd: root, stdio: "ignore" });
await new Promise((r) => setTimeout(r, 1500));

const failures = [];
let n = 0;
function check(ok, name, detail) {
  n += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  [${String(n).padStart(2)}] ${name}${detail ? `  [${detail}]` : ""}`);
  if (!ok) failures.push(name);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const settled = () => page.evaluate(() => window.tileViz.store.settled());

const browser = await puppeteer.launch({
  executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium",
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--window-size=1600,1000"],
  headless: true,
});
const page = await browser.newPage();
await page.setViewport({ width: 1600, height: 1000 });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 300)));

// queryRenderedFeatures reads undefined as the whole viewport and a
// two-element array as pixel coordinates, so a lng/lat box must not go here.
const counts = async (layers) => {
  await sleep(500);
  return page.evaluate((L) => {
    const m = window.tileViz.view.map;
    const feats = m.queryRenderedFeatures(undefined, { layers: L });
    const out = {};
    for (const l of L) out[l] = feats.filter((f) => f.layer.id === l).length;
    return out;
  }, layers);
};
const st = (expr) => page.evaluate(new Function("return (" + expr + ");"));
const extrasText = () => st(`document.querySelector('.tp-extras').textContent`);
const boundText = () => st(`document.querySelector('#params .tp-bound').textContent`);
const inspectText = () => st(`document.getElementById('inspect').textContent`);
const tileCount = () => st(`window.tileViz.store.state.results.intersection.tiles.length`);
const movedCount = () => st(`window.tileViz.store.movedNodes().length`);
const nodeLon = (id) => st(`window.tileViz.store.state.graph.nodes.get(${JSON.stringify(id)}).lon`);
const nodeLonLat = (id) => st(`(() => { const nn = window.tileViz.store.state.graph.nodes.get(${JSON.stringify(id)}); return [nn.lon, nn.lat]; })()`);

try {
  // This page holds the basemap style request back, so a view change can
  // fire while the map still waits to load.
  const early = await browser.newPage();
  await early.setRequestInterception(true);
  let heldStyle = null;
  early.on("request", (req) => {
    if (!heldStyle && req.url().endsWith("/design/app-basemap.json")) { heldStyle = req; return; }
    req.continue();
  });
  await early.goto("http://localhost:4173/?city=pullman", { waitUntil: "domcontentloaded", timeout: 90000 });
  await early.waitForFunction(() => window.tileViz?.view?.map, { timeout: 60000 });
  const earlyThrow = await early.evaluate(() => {
    try { window.tileViz.view.map.fire("moveend"); return null; } catch (err) { return String(err); }
  });
  check(heldStyle !== null && earlyThrow === null, "a view change before the map loads does not throw", earlyThrow ?? "clean");
  await early.close();

  await page.goto("http://localhost:4173/?city=pullman", { waitUntil: "networkidle0", timeout: 90000 });
  await page.waitForFunction(() => window.tileViz?.store?.state?.results?.intersection?.tiles?.length > 0, { timeout: 60000 });
  await page.waitForFunction(() => window.tileViz?.view?.map?.getLayer("intersection-fill") != null, { timeout: 60000 });
  // The checks compare against the Python reference at its own parameters,
  // so this moves the store off the UI's opening values and onto the
  // reference's before anything runs.
  const REFP = await st(`window.tileViz.store.state.bundle.reference.params`);
  await page.evaluate((p) => { const s = window.tileViz.store; s.setParam("toleranceM", p.consolidation_tolerance_m); s.setParam("gridM", p.seed_grid_m); s.setParam("minDegree", p.degree_filter_min); s.setParam("contractTransitions", !!p.contract_transitions); s.setParam("clipBufferM", p.clip_buffer_m); s.setParam("buildScope", "bbox"); }, REFP);
  await settled(); await sleep(600);
  const REF = await st(`window.tileViz.store.state.bundle.reference.variants.bbox.tiles.length`);
  const PAR = new RegExp(`${REF} / ${REF}`);
  await settled();
  await sleep(2500);
  // The ids at the reference parameters, for the CRS round trip below.
  const ref5070 = await st(`window.tileViz.store.state.results.intersection.tiles.map((t) => t.nativeId).sort()`);

  const canvas = await st(`(() => { const c = document.querySelector('#map canvas'); return c ? { w: c.width, h: c.height, gl: !!(c.getContext('webgl2') || c.getContext('webgl')) } : null; })()`);
  check(canvas && canvas.gl && canvas.w > 0 && canvas.h > 0, "WebGL context created and canvas sized", JSON.stringify(canvas));
  check(!(await st(`document.getElementById('provenance').textContent`)).includes("map unavailable"), "provenance line does not say map unavailable");

  const c3 = await counts(["intersection-fill", "network-nodes", "intersection-seeds"]);
  check(c3["intersection-fill"] >= 500, "tile fills render (>=500 viewport tiles)", JSON.stringify(c3));
  check(c3["network-nodes"] >= 700 && c3["intersection-seeds"] === REF, "road network + seed dots render", `nodes=${c3["network-nodes"]}, seeds=${c3["intersection-seeds"]}`);
  await page.screenshot({ path: shots("pullman-webgl.png") });

  await page.click('input[data-layer="legacy"]');
  await settled(); await sleep(400);
  const legacyOn = await counts(["legacy-fill"]);
  const legacyStore = await st(`window.tileViz.store.state.results.legacy.tiles.length`);
  const legacyRef = await st(`window.tileViz.store.state.bundle.reference.legacy.tiles.length`);
  check(legacyStore === legacyRef && legacyOn["legacy-fill"] >= 100, `legacy toggle on: ${legacyRef} tiles, the exporter's count, computed and drawn`, `store=${legacyStore}, drawn=${legacyOn["legacy-fill"]}`);
  await page.click('input[data-layer="legacy"]');
  await settled(); await sleep(300);
  const legacyOff = await counts(["legacy-fill"]);
  check(legacyOff["legacy-fill"] === 0, "legacy toggle off clears the layer", `drawn=${legacyOff["legacy-fill"]}`);

  const busySync = await page.evaluate(() => {
    window.tileViz.store.setParam("toleranceM", 10);
    return document.querySelector(".tp-map-wrap").hasAttribute("data-busy");
  });
  await settled(); await sleep(400);
  check(busySync === true, "busy indicator on the very notify that starts the recompute");
  const tol10 = await tileCount();
  const c8 = await counts(["changed-line"]);
  check(tol10 !== REF && c8["changed-line"] === 0, "tolerance 10 m: recompute changes tiles, no outline on a parameter change", `tiles=${tol10}, outlines=${c8["changed-line"]}`);
  await page.evaluate((tol) => { const i = document.getElementById("param-toleranceM"); i.value = String(tol); i.dispatchEvent(new Event("input", { bubbles: true })); }, REFP.consolidation_tolerance_m);
  await sleep(400);
  const preview = await st(`({ readout: document.querySelector('output[for="param-toleranceM"]').textContent, tiles: window.tileViz.store.state.results.intersection.tiles.length, param: window.tileViz.store.state.params.toleranceM })`);
  check(preview.readout === `${REFP.consolidation_tolerance_m} m` && preview.tiles === tol10 && preview.param === 10, "slider drag previews the readout without recomputing", JSON.stringify(preview));
  await page.evaluate(() => window.tileViz.store.select(null));
  const heldThumb = await st(`({ value: document.getElementById("param-toleranceM").value, readout: document.querySelector('output[for="param-toleranceM"]').textContent })`);
  check(heldThumb.value === String(REFP.consolidation_tolerance_m) && heldThumb.readout === `${REFP.consolidation_tolerance_m} m`, "a store notification mid-drag leaves the slider where the drag put it", JSON.stringify(heldThumb));
  await page.evaluate(() => { document.getElementById("param-toleranceM").dispatchEvent(new Event("change", { bubbles: true })); });
  await settled(); await sleep(400);
  const parityAt5 = await extrasText();
  const tol5 = await tileCount();
  check(tol5 === REF && PAR.test(parityAt5), `slider release at ${REFP.consolidation_tolerance_m} m recomputes: ${REF} tiles, parity ${REF}/${REF}`, `tiles=${tol5}`);
  const clipCtl = await st(`(() => { const i = document.getElementById("param-clipBufferM"); return { type: i.type, max: i.max, value: i.value, readout: document.querySelector('output[for="param-clipBufferM"]').textContent, ticks: i.list?.options.length }; })()`);
  check(clipCtl.type === "range" && clipCtl.max === "3" && clipCtl.readout === `${REFP.clip_buffer_m} m` && clipCtl.ticks === 4, `clip buffer is a stepped slider over the four exported buffers, at ${REFP.clip_buffer_m} m`, JSON.stringify(clipCtl));
  const truncAt500 = await st(`window.tileViz.store.state.stats.intersection.truncatedCount`);
  await page.evaluate(() => { const i = document.getElementById("param-clipBufferM"); i.value = "3"; i.dispatchEvent(new Event("input", { bubbles: true })); i.dispatchEvent(new Event("change", { bubbles: true })); });
  await settled(); await sleep(400);
  const clip1000 = await st(`({ param: window.tileViz.store.state.params.clipBufferM, truncated: window.tileViz.store.state.stats.intersection.truncatedCount, readout: document.querySelector('output[for="param-clipBufferM"]').textContent })`);
  check(clip1000.param === 1000 && clip1000.readout === "1000 m" && clip1000.truncated !== truncAt500, "clip buffer slider to its last stop: 1000 m, clip truncation changes", JSON.stringify({ ...clip1000, truncAt500 }));
  await page.evaluate((m) => { const i = document.getElementById("param-clipBufferM"); i.value = String([...i.list.options].findIndex((o) => o.label === `${m} m`)); i.dispatchEvent(new Event("input", { bubbles: true })); i.dispatchEvent(new Event("change", { bubbles: true })); }, REFP.clip_buffer_m);
  await settled(); await sleep(400);
  await page.select("#param-minDegree", "1");
  await settled(); await sleep(300);
  const d1 = await tileCount();
  await page.select("#param-minDegree", "3");
  await settled(); await sleep(300);
  const d3 = await tileCount();
  check(d1 > REF && d3 === REF, "degree filter 1 then 3: count rises then returns", `1=>${d1}, 3=>${d3}`);
  const bboxScope = await st(`({ truncated: window.tileViz.store.state.stats.intersection.truncatedCount, vertices: window.tileViz.store.state.results.intersection.clip[0][0].length })`);
  await page.click("#param-buildScope-hull");
  await settled(); await sleep(400);
  const hullScope = await st(`(() => { const s = window.tileViz.store.state; const clip = s.results.intersection.clip; return { scope: s.params.buildScope, tiles: s.results.intersection.tiles.length, polygons: clip.length, rings: clip[0].length, vertices: clip[0][0].length, truncated: s.stats.intersection.truncatedCount, parity: document.querySelector('.tp-extras').textContent.includes("${REF} / ${REF}") }; })()`);
  check(hullScope.scope === "hull" && hullScope.tiles === REF && hullScope.polygons === 1 && hullScope.rings === 1 && hullScope.vertices !== bboxScope.vertices && hullScope.truncated !== bboxScope.truncated && hullScope.parity, "build scope: the legacy hull re-shapes the edge cells, same ids, parity holds", JSON.stringify({ bbox: bboxScope, hull: hullScope }));
  await page.click("#param-buildScope-bbox");
  await settled(); await sleep(400);

  // Picks a node that seeds a tile, so moving it must change tiles.
  const pickSeedNode = (exclude) => page.evaluate((ex) => {
    const m = window.tileViz.view.map;
    const s = window.tileViz.store.state;
    const feats = m.queryRenderedFeatures(undefined, { layers: ["network-nodes"] });
    const r = m.getCanvas().getBoundingClientRect();
    const seen = new Set();
    for (const tile of s.results.intersection.tiles) for (const osmid of tile.osmids) {
      if (ex && ex.includes(osmid)) continue;
      if (seen.has(osmid)) continue;
      const f = feats.find((f) => f.properties.id === osmid);
      if (!f) continue;
      seen.add(osmid);
      const p = m.project(f.geometry.coordinates);
      if (p.x < 120 || p.x > r.width - 120 || p.y < 120 || p.y > r.height - 150) continue;
      return { id: osmid, tileId: tile.nativeId, x: r.left + p.x, y: r.top + p.y };
    }
    return null;
  }, exclude);
  await page.evaluate(() => { window.tileViz.view.map.jumpTo({ zoom: 14 }); });
  await sleep(700);
  const nodeA = await pickSeedNode();
  check(!!nodeA, "found a tile-seeding node on screen for the drag", nodeA ? `${nodeA.id} in ${nodeA.tileId}` : "none");
  const lonA0 = await nodeLon(nodeA.id);
  await page.mouse.move(nodeA.x, nodeA.y);
  await page.mouse.down();
  for (const [dx, dy] of [[10, 7], [22, 4], [32, -6], [38, -10]]) await page.mouse.move(nodeA.x + dx, nodeA.y + dy);
  const dragStart = await st(`(() => { const s = window.tileViz.store.state; return { sel: s.selection }; })()`);
  check(dragStart.sel?.kind === "node" && dragStart.sel?.id === nodeA.id, "mousedown grabbed the node (selection set, drag active)", JSON.stringify(dragStart.sel));
  await page.mouse.up();
  await settled(); await sleep(500);
  const lonA1 = await nodeLon(nodeA.id);
  check(Math.abs(lonA1 - lonA0) > 1e-6, "real mouse drag moved the node", `dLon=${(lonA1 - lonA0).toExponential(2)}`);
  const movedAfterDrag = await movedCount();
  const cDrawn = await counts(["moved-nodes", "changed-line"]);
  const bText = await boundText();
  check(movedAfterDrag === 1 && cDrawn["moved-nodes"] === 1 && /Nodes moved: 1/.test(bText), "purple moved-node mark rendered, counter reads 1", `count=${movedAfterDrag}, drawn=${cDrawn["moved-nodes"]}`);
  const inspectDrag = await inspectText();
  check(cDrawn["changed-line"] >= 1 && /Moved from/.test(inspectDrag), "changed-tile outline drawn, inspector shows Moved from", `outlines=${cDrawn["changed-line"]}`);
  await page.screenshot({ path: shots("pullman-drag.png") });
  const keyIds = await st(`[...document.querySelectorAll('#layer-key input[data-layer]')].map((i) => i.dataset.layer)`);
  check(keyIds.includes("changedTiles") && keyIds.includes("movedNodes"), "layer key lists the moved-node mark and the changed-tile outline", keyIds.join(","));
  await page.click('input[data-layer="changedTiles"]'); await sleep(300);
  const cOff = await counts(["changed-line"]);
  await page.click('input[data-layer="changedTiles"]'); await sleep(300);
  const cOn = await counts(["changed-line"]);
  check(cOff["changed-line"] === 0 && cOn["changed-line"] >= 1, "changed-tile toggle hides and restores the outline", `off=${cOff["changed-line"]}, on=${cOn["changed-line"]}`);
  await page.click('input[data-layer="tiles"]'); await sleep(300);
  const cHidden = await counts(["changed-line"]);
  await page.click('input[data-layer="tiles"]'); await sleep(300);
  check(cHidden["changed-line"] === 0, "no outline while the tiles are hidden", `outlines=${cHidden["changed-line"]}`);

  const nodeB = await pickSeedNode([nodeA.id]);
  check(!!nodeB, "found a second tile-seeding node for the Escape test", nodeB ? `${nodeB.id}` : "none");
  const lonB = await nodeLon(nodeB.id);
  await page.mouse.move(nodeB.x, nodeB.y);
  await page.mouse.down();
  await page.mouse.move(nodeB.x + 20, nodeB.y - 12);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await settled(); await sleep(400);
  const lonBAfter = await nodeLon(nodeB.id);
  const movedAfterEsc = await movedCount();
  check(lonBAfter === lonB && movedAfterEsc === 1, "Escape cancels a drag in flight (B unmoved, only A remains)", `movedCount=${movedAfterEsc}`);

  // The map hears no mouseup off its canvas, so the window has to end this
  // drag, or the next click on the map would drop the node there.
  const offCanvas = await page.evaluate(() => {
    const r = window.tileViz.view.map.getCanvas().getBoundingClientRect();
    return r.left > 30 ? { x: r.left - 15, y: r.top + r.height / 2 } : { x: r.left + r.width / 2, y: r.bottom + 15 };
  });
  await page.mouse.move(nodeB.x, nodeB.y);
  await page.mouse.down();
  await page.mouse.move(nodeB.x + 20, nodeB.y - 12);
  await page.mouse.move(offCanvas.x, offCanvas.y);
  await page.mouse.up();
  await sleep(200);
  const panAfterOff = await st(`window.tileViz.view.map.dragPan.isEnabled()`);
  await page.mouse.click(nodeB.x + 60, nodeB.y + 40);
  await settled(); await sleep(300);
  const lonBOff = await nodeLon(nodeB.id);
  const movedAfterOff = await movedCount();
  check(panAfterOff && lonBOff === lonB && movedAfterOff === 1, "a drag released off the map is dropped, and the next click moves nothing", `pan=${panAfterOff}, movedCount=${movedAfterOff}`);

  const nudgeBefore = await nodeLonLat(nodeB.id);
  await page.evaluate((id) => { window.tileViz.store.select({ kind: "node", id }); }, nodeB.id);
  await sleep(300);
  await page.evaluate(() => {
    const form = document.querySelector(".tp-nudge");
    form.dx.value = "10"; form.dy.value = "10";
  });
  await page.evaluate(() => window.tileViz.store.setLayer("grid", false));
  const typed = await st(`(() => { const f = document.querySelector(".tp-nudge"); return [f.dx.value, f.dy.value]; })()`);
  check(typed[0] === "10" && typed[1] === "10", "a store notification keeps what was typed into the nudge form", JSON.stringify(typed));
  await page.evaluate(() => {
    document.querySelector(".tp-nudge").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await settled(); await sleep(400);
  const nudgeAfter = await nodeLonLat(nodeB.id);
  check(Math.abs(nudgeAfter[0] - nudgeBefore[0]) + Math.abs(nudgeAfter[1] - nudgeBefore[1]) > 1e-7, "nudge form (dx 10 m, dy 10 m) moves the selected node");
  await page.evaluate(() => { document.querySelector('#params button.btn-outline-primary').click(); });
  await settled(); await sleep(400);
  const movedAfterReset = await movedCount();
  check(movedAfterReset === 0, "Reset moved nodes clears them all", `count=${movedAfterReset}`);
  check(PAR.test(await extrasText()), `after reset the parity row is back to ${REF}/${REF}`);

  const tilePt = await page.evaluate(() => {
    const m = window.tileViz.view.map;
    const r = m.getCanvas().getBoundingClientRect();
    const tiles = m.queryRenderedFeatures(undefined, { layers: ["intersection-fill"] });
    for (const t of tiles) {
      for (const ring of t.geometry.coordinates) {
        const finite = ring.filter((c) => Number.isFinite(c[0]) && Number.isFinite(c[1]));
        if (finite.length < 4) continue;
        const ringCenter = [finite.reduce((s, c) => s + c[0], 0) / finite.length, finite.reduce((s, c) => s + c[1], 0) / finite.length];
        if (!Number.isFinite(ringCenter[0]) || !Number.isFinite(ringCenter[1])) continue;
        try {
          const p = m.project(ringCenter);
          if (p.x < 40 || p.x > r.width - 40 || p.y < 40 || p.y > r.height - 40) continue;
          const hit = m.queryRenderedFeatures([p.x, p.y], { layers: ["intersection-fill"] });
          const nodeHit = m.queryRenderedFeatures([p.x, p.y], { layers: ["network-nodes"] });
          if (hit.length && !nodeHit.length) return { x: r.left + p.x, y: r.top + p.y, id: hit[0].properties.nativeId };
        } catch {}
      }
    }
    return null;
  });
  check(!!tilePt, "found an on-screen tile center to click", tilePt ? tilePt.id : "none");
  await page.mouse.click(tilePt.x, tilePt.y);
  await sleep(400);
  const sel = await st(`(() => { const s = window.tileViz.store.state.selection; return s ? { kind: s.kind, id: s.id, variant: s.variant } : null; })()`);
  const inspectTile = await inspectText();
  check(sel?.kind === "tile" && sel.id === tilePt.id && inspectTile.includes(tilePt.id), "tile click: inspector shows that tile", JSON.stringify(sel));
  check(/Cell/.test(inspectTile) && /Degree/.test(inspectTile) && /Area/.test(inspectTile), "tile inspector carries cell, degree, area");
  await page.screenshot({ path: shots("pullman-inspect.png") });

  const nodeClick = await page.evaluate((id) => {
    const m = window.tileViz.view.map;
    const nn = window.tileViz.store.state.graph.nodes.get(id);
    const p = m.project([nn.lon, nn.lat]);
    const r = m.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, nodeA.id);
  await page.mouse.click(nodeClick.x, nodeClick.y);
  await sleep(400);
  const selNode = await st(`window.tileViz.store.state.selection.kind`);
  const inspectNode = await inspectText();
  check(selNode === "node" && /Legs/.test(inspectNode) && /Tile/.test(inspectNode), "node click: inspector shows legs and tile membership");
  const inspectEl = await page.$("#inspect");
  await inspectEl.screenshot({ path: shots("pullman-inspector-node.png") });

  await page.evaluate(() => { window.tileViz.view.map.jumpTo({ zoom: 17 }); });
  await page.click('input[data-layer="grid"]');
  await settled(); await sleep(900);
  const g1 = await counts(["grid-lines", "seed-cells", "snap-links", "raw-centroids"]);
  check(g1["grid-lines"] >= 50 && g1["seed-cells"] >= 1 && g1["snap-links"] >= 1 && g1["raw-centroids"] >= 1, "grid at z17: lines, seed cells, snap links, raw centroids", JSON.stringify(g1));
  await page.screenshot({ path: shots("pullman-grid-z17.png") });
  await page.click('input[data-layer="grid"]');
  await settled(); await sleep(400);
  const g2 = await counts(["grid-lines", "seed-cells"]);
  check(g2["grid-lines"] === 0 && g2["seed-cells"] === 0, "grid toggle off clears the overlay", JSON.stringify(g2));

  await page.click('input[data-layer="consolidation"]'); await settled(); await sleep(600);
  const cons = await page.evaluate(async () => {
    const m = window.tileViz.view.map;
    const count = async (id) => (await m.getSource(id).getData()).features.length;
    const merged = window.tileViz.store.state.results.intersection.clusters.filter((c) => c.members.length > 1);
    const nodes = (await m.getSource("network-nodes").getData()).features;
    const discs = m.queryRenderedFeatures(undefined, { layers: ["node-buffers"] });
    return {
      radius0: m.getPaintProperty("node-buffers", "circle-radius")[4],
      links: await count("merge-links"),
      expected: merged.reduce((n, c) => n + c.members.length, 0),
      centroids: await count("merge-centroids"),
      mergedCount: merged.length,
      eligible: nodes.filter((f) => f.properties.eligible).length,
      ineligible: nodes.filter((f) => !f.properties.eligible).length,
      discsDrawn: discs.length,
      discsOnIneligible: discs.filter((f) => !f.properties.eligible).length,
    };
  });
  const cCons = await counts(["node-buffers"]);
  check(cCons["node-buffers"] >= 1 && cons.links > 0 && cons.links === cons.expected && cons.centroids === cons.mergedCount, "consolidation overlay: discs drawn, one link per merged member, one centroid per merged cluster", JSON.stringify({ ...cons, drawn: cCons["node-buffers"] }));
  check(cons.ineligible > 0 && cons.eligible > 0 && cons.discsDrawn > 0 && cons.discsOnIneligible === 0, "consolidation overlay: a dead end carries the flag but draws no disc", JSON.stringify({ eligible: cons.eligible, ineligible: cons.ineligible, discsDrawn: cons.discsDrawn, discsOnIneligible: cons.discsOnIneligible }));
  await page.evaluate((tol) => window.tileViz.store.setParam("toleranceM", 2 * tol), REFP.consolidation_tolerance_m); await settled(); await sleep(400);
  const r2x = await st(`window.tileViz.view.map.getPaintProperty("node-buffers", "circle-radius")[4]`);
  check(Math.abs(r2x / cons.radius0 - 2) < 1e-9, `disc radius follows the tolerance (${2 * REFP.consolidation_tolerance_m} m is twice ${REFP.consolidation_tolerance_m} m)`, `${cons.radius0} -> ${r2x}`);
  await page.evaluate((tol) => window.tileViz.store.setParam("toleranceM", tol), REFP.consolidation_tolerance_m); await settled(); await sleep(400);
  await page.click('input[data-layer="consolidation"]'); await sleep(300);
  const cConsOff = await counts(["node-buffers", "merge-links"]);
  check(cConsOff["node-buffers"] === 0 && cConsOff["merge-links"] === 0, "consolidation toggle off clears the overlay", JSON.stringify(cConsOff));
  await page.evaluate(() => { window.tileViz.view.map.jumpTo({ zoom: 12 }); });
  await sleep(600);

  await page.select("#param-epsg", "EPSG:32611");
  await settled(); await sleep(400);
  const c2611 = await st(`(() => { const t = window.tileViz.store.state.results.intersection.tiles; return { count: t.length, all32611: t.every((x) => x.nativeId.startsWith("32611:")), sample: t[0]?.nativeId }; })()`);
  check(c2611.count > 0 && c2611.all32611, "CRS 32611: tiles re-minted under the new CRS", JSON.stringify(c2611));
  check(/params or moved nodes break the comparison/.test(await extrasText()), "CRS 32611: parity row reports the mismatch");
  const crsOptions = await st(`[...document.getElementById("param-epsg").options].map((o) => o.value)`);
  check(JSON.stringify(crsOptions) === JSON.stringify(["EPSG:5070", "EPSG:32611"]), "CRS select offers only the equal-area CRS and the UTM zone (4326 cannot tessellate in CRS units)", JSON.stringify(crsOptions));
  await page.select("#param-epsg", "EPSG:5070");
  await settled(); await sleep(400);
  const ids5070After = await st(`window.tileViz.store.state.results.intersection.tiles.map((t) => t.nativeId).sort()`);
  const parityAt5070 = await extrasText();
  const warnNow = await st(`window.tileViz.store.state.warnings.length`);
  const epsgNow = await st(`window.tileViz.store.state.params.epsg`);
  const movedNow = await movedCount();
  const missing = ids5070After.filter((x) => !ref5070.includes(x)).slice(0, 4);
  const extra = ref5070.filter((x) => !ids5070After.includes(x)).slice(0, 4);
  const restored = JSON.stringify(ids5070After) === JSON.stringify(ref5070) && PAR.test(parityAt5070) && warnNow === 0;
  check(restored, `back to 5070: byte-identical ids, parity ${REF}/${REF}, no warnings`, `count=${ids5070After.length}, epsg=${epsgNow}, moved=${movedNow}, warnings=${warnNow}, parity=${PAR.test(parityAt5070)}, missing=${missing.join("|")}, extra=${extra.join("|")}`);

  // getStyle() reports visibility under layout, or on the layer itself.
  const hiddenCount = () => st(`window.tileViz.view.map.getStyle().layers.filter((l) => ((l.layout && (l.layout.visibility || "visible")) || l.visibility || "visible") === "none").length`);
  const hiddenBefore = await hiddenCount();
  await page.click('input[data-layer="basemap"]');
  await sleep(400);
  const hiddenAfter = await hiddenCount();
  check(hiddenAfter > hiddenBefore, "basemap toggle hides the style's basemap layers", `${hiddenBefore} -> ${hiddenAfter} hidden`);
  await page.click('input[data-layer="basemap"]');
  await sleep(300);
  await page.click('input[data-layer="network"]');
  await sleep(300);
  const netVis = await st(`window.tileViz.view.map.getLayoutProperty("network-edges", "visibility")`);
  check(netVis === "none", "network toggle hides the roads", `visibility=${netVis}`);
  await page.click('input[data-layer="network"]');
  await sleep(300);

  check(pageErrors.length === 0, "no uncaught page errors during the whole pass", pageErrors.slice(0, 2).join(" | ") || "clean");
  const shotBuf = await page.screenshot({ path: shots("pullman.png") });
  check(shotBuf.length > 60000, "full screenshot shows the map (over 60 kB)", `${(shotBuf.length / 1024).toFixed(0)} kB`);
  const cities = await st(`[...document.getElementById("city").options].map((o) => o.value)`);
  const indexed = JSON.parse(readFileSync(join(root, "data/index.json"), "utf8")).bundles.map((b) => b.slug);
  check(JSON.stringify(cities) === JSON.stringify(indexed), "city switcher lists every bundle in data/index.json", JSON.stringify(cities));
  const shotNames = ["pullman-webgl.png", "pullman-drag.png", "pullman-inspect.png", "pullman-inspector-node.png", "pullman-grid-z17.png", "pullman.png"];
  const shotReport = shotNames.map((name) => `${name}:${(statSync(shots(name)).size / 1024).toFixed(0)}kB`).join(" ");
  check(shotNames.every((name) => statSync(shots(name)).size > 10000), "6 screenshots all written", shotReport);
} finally {
  await browser.close();
  server.kill();
}

console.log(`\n${n - failures.length}/${n} PASS${failures.length ? `; FAILED: ${failures.join("; ")}` : ""}`);
process.exit(failures.length ? 1 : 0);
