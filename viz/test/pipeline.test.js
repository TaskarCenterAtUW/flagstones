import { test } from "node:test";
import assert from "node:assert/strict";
import { computeVariant, diffTiles, bboxClip, cellMeans } from "../src/engine/pipeline.js";
import { layerStats } from "../src/engine/stats.js";
import { buildGraph, moveNode } from "../src/engine/graph.js";
import { gridBundle, tJunctionWithDeadEndBundle, carriagewayJoinBundle, dividedRoadBundle, culDeSacCloseStubBundle, junctionWithSlipLaneEndBundle, linkBetweenJunctionsBundle, wedgedLinkNodeBundle, medianPairBundle, truncatedJunctionStubBundle, loopEndStubBundle } from "./fixtures.js";

const PARAMS = { toleranceM: 5, gridM: 5, minDegree: 3, keepDeadEnds: true, contractTransitions: false, transitionOpts: {}, epsg: 5070 };
const BBOX = { kind: "bbox", bufferM: 50 };

test("the grid yields one tile per three-or-more-leg junction, and the stub is dropped", () => {
  const r = computeVariant(buildGraph(gridBundle()), PARAMS, BBOX);
  assert.equal(r.tiles.length, 5, "center plus four edge-midpoint junctions; corners are degree 2");
  // The stub is a dead end. It does not merge, and the filter drops its
  // degree-1 cluster, so it tiles nothing.
  assert.equal(r.dropped.deadEnds, 1);
  assert.equal(r.dropped.degree2, 4);
  assert.equal(r.tiles.some((t) => t.osmids.includes(99)), false, "the stub tiles nothing");
});

test("keepDeadEnds=false strips the junction's cul-de-sac leg, as osmnx's dead_ends=False does", () => {
  const g = buildGraph(tJunctionWithDeadEndBundle());
  assert.equal(computeVariant(g, PARAMS, BBOX).tiles.length, 1);
  assert.equal(computeVariant(g, { ...PARAMS, keepDeadEnds: false }, BBOX).tiles.length, 0);
});

test("a close cul-de-sac stub is not consolidated into its junction", () => {
  // The stub sits 4 m from node 2, inside the 10 m merge distance. As a
  // street end it remains its own filtered cluster. Folding it into the
  // junction would make one degree-2 cluster that the filter drops,
  // leaving the junction without a tile.
  const g = buildGraph(culDeSacCloseStubBundle());
  const r = computeVariant(g, PARAMS, BBOX);
  const junction = r.tiles.find((t) => t.osmids.includes(2));
  assert.ok(junction, "the three-leg junction tiles");
  assert.deepEqual(junction.osmids, [2], "the stub is not a member of the junction");
  assert.equal(junction.nodeCount, 1);
  assert.equal(junction.degree, 3, "the cul-de-sac leg still counts");
  const stub = r.clusters.find((c) => c.members.includes(5));
  assert.deepEqual(stub.members, [5], "the stub stays its own cluster");
  assert.equal(stub.degree, 1, "the stub is a degree-1 cluster the filter removes");
  assert.equal(r.tiles.some((t) => t.osmids.includes(5)), false, "the stub tiles nothing");
});

test("a link node that is no junction never merges into one", () => {
  // The slip lane's end is 6 m from the junction, inside the merge
  // distance, but two road legs and a ramp make it no intersection, so
  // the merge holds it out like a dead end.
  const r = computeVariant(buildGraph(junctionWithSlipLaneEndBundle()), PARAMS, BBOX);
  assert.ok(r.unmergeable.has(7), "the slip node is unmergeable");
  const junction = r.tiles.find((t) => t.osmids.includes(2));
  assert.deepEqual(junction.osmids, [2]);
  assert.equal(junction.degree, 3);
  assert.equal(r.tiles.some((t) => t.osmids.includes(7)), false, "the slip node tiles nothing");
  // junctionDegree decides what a junction is, whatever the filter lists.
  // At minDegree 1 the filter lists the slip node on its own, and the
  // merge still holds it out.
  const listed = computeVariant(buildGraph(junctionWithSlipLaneEndBundle()), { ...PARAMS, minDegree: 1, junctionDegree: 3 }, BBOX);
  assert.ok(listed.unmergeable.has(7));
  assert.deepEqual(listed.clusters.find((c) => c.members.includes(2)).members, [2]);
});

test("a link within the merge distance of a junction is its connector, not a ramp", () => {
  // Node 7 has two road legs and the link, but the link reaches a junction 8 m
  // away, so the two merge into one intersection of five legs. At 30 m the link
  // reads as a slip lane and the merge holds node 7 out as before.
  const close = computeVariant(buildGraph(linkBetweenJunctionsBundle(8)), PARAMS, BBOX);
  assert.equal(close.unmergeable.has(7), false);
  assert.deepEqual(close.tiles.map((t) => [t.osmids, t.degree]), [[[2, 7], 5]]);
  const far = computeVariant(buildGraph(linkBetweenJunctionsBundle(30)), PARAMS, BBOX);
  assert.equal(far.unmergeable.has(7), true);
  assert.deepEqual(far.tiles.map((t) => [t.osmids, t.degree]), [[[2], 3]]);
});

test("a node wedged between two junctions merges across both plain edges", () => {
  // Node 7's link reaches no junction, but all its ordinary edges do,
  // within the merge distance. Node 7 sits inside a consolidated
  // intersection, so it merges with both junctions, and the link adds
  // no leg to a junction of six road legs.
  const r = computeVariant(buildGraph(wedgedLinkNodeBundle()), PARAMS, BBOX);
  assert.equal(r.unmergeable.has(7), false, "not held out");
  assert.deepEqual(r.tiles.map((t) => [t.osmids, t.degree]), [[[2, 7, 9], 6]]);
});

test("contraction removes the join's tile and reports it", () => {
  const g = buildGraph(carriagewayJoinBundle());
  assert.equal(computeVariant(g, PARAMS, BBOX).tiles.length, 1);
  const r = computeVariant(g, { ...PARAMS, contractTransitions: true }, BBOX);
  assert.equal(r.tiles.length, 0);
  assert.equal(r.dropped.transitions, 1);
});

test("bbox clip is the seed extent plus the buffer", () => {
  const multi = bboxClip([{ x: 10, y: 20 }, { x: 110, y: 70 }], 500);
  assert.deepEqual(multi, [[[[-490, -480], [610, -480], [610, 570], [-490, 570], [-490, -480]]]]);
});

test("moving a node changes only the tiles it touches", () => {
  const g = buildGraph(gridBundle());
  const before = computeVariant(g, PARAMS, { kind: "polygon", multi: bboxClip([{ x: 100, y: 100 }, { x: 300, y: 300 }], 50) }).tiles;
  const n = g.nodes.get(2);
  moveNode(g, 2, n.lon + 0.00012, n.lat);
  const after = computeVariant(g, PARAMS, { kind: "polygon", multi: bboxClip([{ x: 100, y: 100 }, { x: 300, y: 300 }], 50) }).tiles;
  const d = diffTiles(before, after);
  assert.equal(d.removed.size, 1);
  assert.equal(d.added.size, 1);
  assert.ok(d.changed.size >= 1 && d.changed.size < before.length);
});

test("an over-bound grid collapses shared cells into flagged tiles, it does not throw", () => {
  // At grid 150 m the center junction and both of its neighbors' junctions
  // snap into the same cell. The run must complete and flag the collapsed tile.
  const r = computeVariant(buildGraph(gridBundle()), { ...PARAMS, gridM: 150 }, BBOX);
  const flagged = r.tiles.filter((t) => t.seedCollision);
  assert.equal(flagged.length, 1, "one collapsed tile");
  assert.deepEqual([...flagged[0].osmids].sort(), [2, 4, 5]);
  assert.equal(r.tiles.length, 3, "three cells survive");
  assert.equal(layerStats(r.tiles).collisionCount, 1);
});

test("no seed collision when the grid stays below the bound", () => {
  const r = computeVariant(buildGraph(gridBundle()), { ...PARAMS, toleranceM: 15, gridM: 17 }, BBOX);
  assert.ok(r.tiles.every((t) => !t.seedCollision));
  assert.equal(layerStats(r.tiles).collisionCount, 0);
});

test("layer statistics", () => {
  const r = computeVariant(buildGraph(dividedRoadBundle()), { ...PARAMS, toleranceM: 15, gridM: 7 }, BBOX);
  const s = layerStats(r.tiles);
  assert.equal(s.count, 1);
  assert.equal(s.mergedCount, 1);
  assert.equal(s.mergedShare, 1);
  assert.equal(s.degreeShare.d5plus, 1);
  assert.equal(s.sliverCount, 0);
  assert.ok(s.snapMax <= 7 * Math.SQRT1_2 + 1e-9);
});

test("a junction pair across a median tiles once", () => {
  const r = computeVariant(buildGraph(medianPairBundle()), PARAMS, BBOX);
  assert.deepEqual(r.tiles.map((t) => [t.osmids, t.degree]), [[[2, 3], 4]]);
  assert.deepEqual([...r.ties], [[2, [3]], [3, [2]]], "the pipeline reports the ties it merged across");
});

test("a junction cut by the city limit is a dead end of this graph", () => {
  // Its stub end sits 4 m from the junction and would merge into it on the
  // strength of a street_count the truncation left behind. The merge
  // holds it out like any dead end, the junction keeps the leg, and the
  // stub tiles nothing.
  const r = computeVariant(buildGraph(truncatedJunctionStubBundle()), PARAMS, BBOX);
  assert.ok(r.unmergeable.has(5));
  assert.deepEqual(r.tiles.map((t) => [t.osmids, t.degree]), [[[2], 3]]);
});

test("a street ending in a turning circle is a street end, not a junction", () => {
  // The street ends in a loop way: one street and the loop, which
  // street_count reads as three. The junction it hangs off keeps the leg;
  // the loop end draws no disc, merges with nothing, and tiles nothing.
  const r = computeVariant(buildGraph(loopEndStubBundle()), PARAMS, BBOX);
  assert.ok(r.unmergeable.has(5));
  assert.deepEqual(r.tiles.map((t) => [t.osmids, t.degree]), [[[2], 3]]);
  // A loop road of 100 m or more meets its stem at a junction.
  const road = computeVariant(buildGraph(loopEndStubBundle({ loopM: 300 })), PARAMS, BBOX);
  assert.equal(road.unmergeable.has(5), false);
  assert.deepEqual(road.tiles.map((t) => [t.osmids, t.degree]).sort((a, b) => a[0][0] - b[0][0]), [[[2], 3], [[5], 3]]);
});

test("the fallback cells fill the extent the tiles leave open", () => {
  const extent = [[[[0, 0], [400, 0], [400, 400], [0, 400], [0, 0]]]];
  const r = computeVariant(buildGraph(gridBundle()), { ...PARAMS, fallbackCellM: 250, fallbackMinShare: 0.1 }, { ...BBOX, extent });
  assert.ok(r.fallback.length > 0);
  assert.deepEqual(r.warnings, []);
});

test("a fallback that throws keeps the intersection tiles and reports a warning", () => {
  const extent = [[[[0, 0], [400, 0], [400, 400], [0, 400], [0, 0]]]];
  const r = computeVariant(buildGraph(gridBundle()), { ...PARAMS, fallbackCellM: 250, fallbackMinShare: 1 }, { ...BBOX, extent });
  assert.equal(r.tiles.length, 5);
  assert.deepEqual(r.fallback, []);
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0], /^fallback: minShare/);
});

test("the bbox clip spans one mean point per seed cell, as the library's collapsed seeds do", () => {
  const means = cellMeans([{ x: -10, y: 0 }, { x: 8, y: 2 }, { x: 500, y: 0 }], 24);
  assert.deepEqual(bboxClip(means, 250)[0][0][0], [-251, -250]);
  assert.deepEqual(bboxClip(means, 250)[0][0][2], [750, 251]);
});

test("dead-end clusters count as removed only below the degree filter", () => {
  const params = { toleranceM: 5, gridM: 5, minDegree: 1, keepDeadEnds: true, contractTransitions: false, transitionOpts: {}, epsg: 5070 };
  const r = computeVariant(buildGraph(gridBundle()), params, { kind: "bbox", bufferM: 50 });
  assert.ok(r.seeds.some((c) => c.degree === 1), "degree-1 clusters are seeds at minDegree 1");
  assert.equal(r.dropped.deadEnds, 0);
});
