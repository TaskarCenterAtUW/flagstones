import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { buildGraph, deadEnds } from "../src/engine/graph.js";
import { consolidate, ineligibleLinkNodes, medianTies } from "../src/engine/cluster.js";
import { classifyTransitions } from "../src/engine/transitions.js";
import { computeVariant } from "../src/engine/pipeline.js";
import { referenceTransitions } from "../src/app/results-panel.js";
import { legacyTiles } from "../src/engine/legacy.js";

const dataDir = join(dirname(fileURLToPath(import.meta.url)), "../data");

// The reference ran at the bundle's params, transition rules included, so
// the comparison runs at the same ones.
function paramsOf(bundle) {
  const p = bundle.reference.params;
  return {
    toleranceM: p.consolidation_tolerance_m, gridM: p.seed_grid_m, minDegree: p.degree_filter_min,
    keepDeadEnds: true, ...referenceTransitions(p), epsg: bundle.city.crs, snapCentroid: p.snap_centroid ?? true,
    fallbackCellM: bundle.reference.fallback_params?.cell_m ?? 0, fallbackMinShare: bundle.reference.fallback_params?.min_share ?? 0,
  };
}

function clipSpecFor(bundle, variant) {
  const buffer = String(bundle.reference.params.clip_buffer_m | 0);
  const extent = bundle.clips.extent;
  if (variant === "node_union") return { kind: "polygon", multi: bundle.clips.node_union[buffer], extent };
  if (variant === "boundary") return { kind: "polygon", multi: bundle.clips.boundary[buffer], extent };
  return { kind: "bbox", bufferM: bundle.reference.params.clip_buffer_m, extent };
}

// The bundles viz-export wrote, index.json aside. With none, a skipped
// entry reports why the run checked nothing.
const bundles = existsSync(dataDir)
  ? readdirSync(dataDir).filter((f) => f.endsWith(".json") && f !== "index.json").sort()
  : [];

for (const file of bundles.length ? bundles : ["none.json"]) {
  const city = file.replace(/\.json$/, "");
  const path = join(dataDir, file);
  const present = bundles.length > 0;
  test(`${city}: clusters match the library`, { skip: !present && "no bundle; run viz-export" }, () => {
    const bundle = JSON.parse(readFileSync(path, "utf8"));
    const graph = buildGraph(bundle);
    // As in the pipeline, dead ends and ramp nodes do not merge, or a
    // cul-de-sac end within the merge distance folds into its junction and
    // the junction reads as a cluster the reference lacks. The export takes
    // the reference at min_degree=1, so it omits a degree-0 cluster (a node
    // of nothing but ramps), and the comparison starts at degree 1. The
    // library drops a contracted transition whatever its degree.
    const toleranceM = bundle.reference.params.consolidation_tolerance_m;
    const { contractTransitions, transitionOpts } = paramsOf(bundle);
    const excluded = new Set(contractTransitions ? classifyTransitions(graph, transitionOpts).keys() : []);
    const unmergeable = new Set([...deadEnds(graph), ...ineligibleLinkNodes(graph, bundle.reference.params.degree_filter_min, toleranceM)]);
    const ties = medianTies(graph, toleranceM, new Set([...unmergeable, ...excluded]));
    const { clusters } = consolidate(graph, { toleranceM, excluded, unmergeable, ties });
    const ours = new Map(clusters.filter((c) => !c.excluded && c.degree >= 1).map((c) => [c.members.join(","), c]));
    const theirs = bundle.reference.clusters;
    assert.equal(ours.size, theirs.length, "cluster count");
    let centroidMismatch = 0, degreeMismatch = 0;
    for (const ref of theirs) {
      const c = ours.get([...ref.osmids].sort((a, b) => a - b).join(","));
      assert.ok(c, `missing cluster ${ref.osmids}`);
      if (c.degree !== ref.degree) degreeMismatch++;
      if (Math.hypot(c.x - ref.x, c.y - ref.y) > 0.001) centroidMismatch++;
    }
    assert.equal(degreeMismatch, 0, "degrees");
    assert.equal(centroidMismatch, 0, "centroids beyond 1 mm");
  });

  for (const variant of ["node_union", "bbox", "boundary"]) {
    test(`${city}: ${variant} tiles match the library`, { skip: !present && "no bundle; run viz-export" }, () => {
      const bundle = JSON.parse(readFileSync(path, "utf8"));
      const graph = buildGraph(bundle);
      const { tiles } = computeVariant(graph, paramsOf(bundle), clipSpecFor(bundle, variant));
      const ref = bundle.reference.variants[variant].tiles;
      const ours = new Map(tiles.map((t) => [t.nativeId, t]));
      const missing = ref.filter((r) => !ours.has(r.id)).map((r) => r.id);
      const extra = tiles.filter((t) => !ref.some((r) => r.id === t.nativeId)).map((t) => t.nativeId);
      assert.deepEqual({ missing, extra }, { missing: [], extra: [] });
      let areaMismatch = 0;
      for (const r of ref) {
        const t = ours.get(r.id);
        assert.deepEqual(t.osmids, r.osmids, `osmids at ${r.id}`);
        assert.equal(t.degree, r.degree, `degree at ${r.id}`);
        if (Math.abs(t.area - r.area_m2) / r.area_m2 > 0.001) areaMismatch++;
      }
      assert.equal(areaMismatch, 0, "areas beyond 0.1%");
      assert.equal(tiles.length, ref.length);
    });
  }

  for (const variant of ["node_union", "bbox", "boundary"]) {
    test(`${city}: ${variant} fallback cells match the library`, { skip: !present && "no bundle; run viz-export" }, () => {
      const bundle = JSON.parse(readFileSync(path, "utf8"));
      const ref = bundle.reference.variants[variant].fallback;
      assert.ok(ref, "the bundle carries no fallback reference; export it without --fallback-cell-m 0");
      // Toppenish's node-union clip leaves ground uncovered, so its
      // reference has cells and the area loop below checks something.
      if (city === "toppenish" && variant === "node_union") assert.ok(ref.length > 0, "the Toppenish node-union reference has no fallback cells");
      const { fallback } = computeVariant(buildGraph(bundle), paramsOf(bundle), clipSpecFor(bundle, variant));
      assert.deepEqual(fallback.map((t) => t.nativeId), ref.map((r) => r.id));
      let areaMismatch = 0;
      ref.forEach((r, k) => {
        if (Math.abs(fallback[k].area - r.area_m2) / r.area_m2 > 0.001) areaMismatch++;
      });
      assert.equal(areaMismatch, 0, "areas beyond 0.1%");
    });
  }

  test(`${city}: the legacy tiles match the exporter's reference`, { skip: !present && "no bundle; run viz-export" }, () => {
    const bundle = JSON.parse(readFileSync(path, "utf8"));
    const graph = buildGraph(bundle);
    const { scope, utm_epsg: utmEpsg, tiles: ref } = bundle.reference.legacy;
    const ours = new Map(legacyTiles(graph, scope, { utmEpsg }).map((t) => [`${t.seedLon},${t.seedLat}`, t]));
    assert.equal(ours.size, ref.length, "tile count");
    let areaMismatch = 0;
    for (const r of ref) {
      const t = ours.get(`${r.seed[0]},${r.seed[1]}`);
      assert.ok(t, `missing legacy tile at ${r.seed}`);
      if (Math.abs(t.areaM2 - r.area_m2) / r.area_m2 > 0.001) areaMismatch++;
    }
    assert.equal(areaMismatch, 0, "areas beyond 0.1%");
  });
}
