import { test } from "node:test";
import assert from "node:assert/strict";
import { cpSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import puppeteer from "puppeteer-core";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const CHROMIUM = process.env.CHROMIUM ?? "/usr/bin/chromium";
// Vite starts through this Node binary and its own entry script. On Windows
// `npx` is a .cmd shim, which `spawn` cannot start without a shell.
const VITE = join(root, "node_modules/vite/bin/vite.js");
const havePullman = existsSync(join(root, "data/pullman.json"));
const REFERENCE = havePullman ? JSON.parse(readFileSync(join(root, "data/pullman.json"))).reference : null;
const REF = REFERENCE ? REFERENCE.variants.bbox.tiles.length : 0;

async function withPreview(fn) {
  // vite preview serves only dist/, and the build leaves the bundles in
  // data/ out.
  if (havePullman) {
    mkdirSync(join(root, "dist/data"), { recursive: true });
    cpSync(join(root, "data"), join(root, "dist/data"), { recursive: true });
  }
  const server = spawn(process.execPath, [VITE, "preview", "--port", "4173", "--strictPort"], { cwd: root, stdio: "ignore" });
  await new Promise((r) => setTimeout(r, 1500));
  try { return await fn("http://localhost:4173"); } finally { server.kill(); }
}

test("the app boots, computes Pullman, and reports the reference count", { skip: !existsSync(CHROMIUM) ? "no chromium" : !havePullman ? "no bundle" : false, timeout: 120000 }, async () => {
  await withPreview(async (base) => {
    const browser = await puppeteer.launch({ executablePath: CHROMIUM, args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--window-size=1600,1000"], headless: true });
    try {
      const page = await browser.newPage();
      await page.setViewport({ width: 1600, height: 1000 });
      await page.goto(`${base}/?city=pullman`, { waitUntil: "networkidle0", timeout: 60000 });
      await page.waitForFunction(() => window.tileViz?.store?.state?.results?.intersection?.tiles?.length > 0, { timeout: 60000 });
      await page.evaluate((p) => { const s = window.tileViz.store; s.setParam("toleranceM", p.consolidation_tolerance_m); s.setParam("gridM", p.seed_grid_m); s.setParam("minDegree", p.degree_filter_min); s.setParam("clipBufferM", p.clip_buffer_m); s.setParam("buildScope", "bbox"); }, REFERENCE.params);
      await page.evaluate(() => window.tileViz.store.settled());
      // The app opens at the library defaults. Walking the params back to the
      // bundle's reference lets the check compare the engine with the Python
      // count as well as the boot.
      await page.evaluate(async () => {
        const s = window.tileViz.store;
        const r = s.state.bundle.reference.params;
        for (const [name, v] of [["toleranceM", r.consolidation_tolerance_m], ["gridM", r.seed_grid_m], ["minDegree", r.degree_filter_min], ["clipBufferM", r.clip_buffer_m], ["buildScope", "bbox"]]) s.setParam(name, v);
        await s.settled();
      });
      const counts = await page.evaluate(() => ({
        intersection: window.tileViz.store.state.results.intersection.tiles.length,
        text: document.getElementById("stats").textContent,
        engine: window.tileViz.store.engine.kind,
      }));
      assert.equal(counts.engine, "worker", "the browser computes on the Worker, not in-process");
      assert.equal(counts.intersection, REF);
      assert.match(counts.text, new RegExp(String(REF)));
      await page.evaluate(() => window.tileViz.store.setParam("minDegree", 1));
      await page.waitForFunction((ref) => window.tileViz.store.state.results.intersection.tiles.length > ref, { timeout: 30000 }, REF);
      await page.evaluate(() => window.tileViz.store.setParam("minDegree", 3));
      await page.waitForFunction((ref) => window.tileViz.store.state.results.intersection.tiles.length === ref, { timeout: 30000 }, REF);
      await page.screenshot({ path: join(root, "screenshots/pullman.png") });
    } finally {
      await browser.close();
    }
  });
});
