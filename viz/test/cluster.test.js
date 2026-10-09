import { test } from "node:test";
import assert from "node:assert/strict";
import { consolidate, bufferCircle, buffersOverlap, medianTies, ineligibleLinkNodes } from "../src/engine/cluster.js";
import { buildGraph, deadEnds } from "../src/engine/graph.js";
import { ringArea } from "../src/engine/geometry.js";
import { dividedRoadBundle, tJunctionWithDeadEndBundle, gridBundle, bundleFromXY, medianPairBundle, loopXY } from "./fixtures.js";

test("the buffer is GEOS's 64-gon", () => {
  const ring = bufferCircle(0, 0, 5);
  assert.equal(ring.length, 65);
  assert.deepEqual(ring[0], [5, 0]);
  assert.ok(Math.abs(ringArea(ring) - Math.PI * 25 * (64 / (2 * Math.PI)) * Math.sin(2 * Math.PI / 64)) < 1e-9);
});

test("overlap follows the polygon, not the circle", () => {
  // Along a vertex direction the polygon reaches r exactly; along an edge
  // normal it reaches r*cos(pi/64). Two nodes 9.995 m apart along the
  // x axis overlap, while two 9.995 m apart along an edge normal do not.
  assert.equal(buffersOverlap([0, 0], [9.995, 0], 5), true);
  const psi = Math.PI / 64;
  assert.equal(buffersOverlap([0, 0], [9.995 * Math.cos(psi), 9.995 * Math.sin(psi)], 5), false);
  assert.equal(buffersOverlap([0, 0], [10.001, 0], 5), false);
});

test("a divided-road junction becomes one cluster of degree 6 at its true centroid", () => {
  const { clusters } = consolidate(buildGraph(dividedRoadBundle()), { toleranceM: 15 });
  const big = clusters.find((c) => c.members.length === 4);
  assert.deepEqual(big.members, [1, 2, 3, 4]);
  assert.equal(big.degree, 6);
  assert.ok(Math.abs(big.x - 1010) < 1e-6 && Math.abs(big.y - 1000) < 1e-6);
  assert.equal(big.split, false);
});

test("a cul-de-sac's end is a degree-1 cluster and its junction keeps three legs", () => {
  const { clusters } = consolidate(buildGraph(tJunctionWithDeadEndBundle()), { toleranceM: 5 });
  const byMember = new Map(clusters.map((c) => [c.members[0], c]));
  assert.equal(byMember.get(5).degree, 1);
  assert.equal(byMember.get(2).degree, 3);
  assert.equal(clusters.length, 5);
});

test("nodes within the merge distance but not connected stay apart", () => {
  // An overpass node 4 m above a surface junction, no edge between them.
  const bundle = bundleFromXY(
    [
      { id: 1, x: 0, y: 0, sc: 3 }, { id: 2, x: 100, y: 0, sc: 1 }, { id: 3, x: -100, y: 0, sc: 1 }, { id: 4, x: 0, y: 100, sc: 1 },
      { id: 9, x: 4, y: 0, sc: 2 }, { id: 8, x: 4, y: 100, sc: 1 }, { id: 7, x: 4, y: -100, sc: 1 },
    ],
    [{ u: 1, v: 2 }, { u: 1, v: 3 }, { u: 1, v: 4 }, { u: 9, v: 8 }, { u: 9, v: 7 }],
  );
  const { clusters } = consolidate(buildGraph(bundle), { toleranceM: 5 });
  const one = clusters.find((c) => c.members.includes(1));
  const nine = clusters.find((c) => c.members.includes(9));
  assert.notEqual(one, nine);
  assert.equal(one.split, true);
  assert.deepEqual([one.x, one.y], [0, 0]);
});

test("merging chains through overlapping buffers", () => {
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 2 }, { id: 2, x: 9, y: 0, sc: 2 }, { id: 3, x: 18, y: 0, sc: 2 }, { id: 4, x: 200, y: 0, sc: 1 }, { id: 5, x: -200, y: 0, sc: 1 }],
    [{ u: 5, v: 1 }, { u: 1, v: 2 }, { u: 2, v: 3 }, { u: 3, v: 4 }],
  );
  const { clusters, clusterOf } = consolidate(buildGraph(bundle), { toleranceM: 5 });
  assert.equal(clusterOf.get(1), clusterOf.get(3));
  const merged = clusters[clusterOf.get(1)];
  assert.deepEqual(merged.members, [1, 2, 3]);
  assert.ok(Math.abs(merged.x - 9) < 1e-9, "union of three collinear circles is symmetric about the middle one");
  assert.equal(merged.degree, 2);
});

test("an excluded node is a singleton nobody merges with, and still counts as a leg", () => {
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 3 }, { id: 2, x: 6, y: 0, sc: 2 }, { id: 3, x: 0, y: 100, sc: 1 }, { id: 4, x: 0, y: -100, sc: 1 }, { id: 5, x: 100, y: 0, sc: 1 }],
    [{ u: 1, v: 3 }, { u: 1, v: 4 }, { u: 1, v: 2 }, { u: 2, v: 5 }],
  );
  const { clusters } = consolidate(buildGraph(bundle), { toleranceM: 5, excluded: new Set([2]) });
  const two = clusters.find((c) => c.members.includes(2));
  assert.deepEqual(two.members, [2]);
  assert.equal(two.excluded, true);
  assert.equal(clusters.find((c) => c.members.includes(1)).degree, 3);
});

test("output is sorted by x then y", () => {
  const { clusters } = consolidate(buildGraph(gridBundle()), { toleranceM: 5 });
  for (let i = 1; i < clusters.length; i++) {
    const a = clusters[i - 1], b = clusters[i];
    assert.ok(a.x < b.x || (a.x === b.x && a.y <= b.y));
    assert.equal(clusters[i].index, i);
  }
});

test("a parallel way to the same neighbor is its own leg", () => {
  // Along the line 1-2-3, with a second way also joining 2 and 3, node 2 has
  // three street legs and two neighbors, as osmnx's street_count reads it.
  const bundle = bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 400, y: 0, sc: 2 }],
    [{ u: 1, v: 2, name: ["Grand"] }, { u: 2, v: 3, name: ["Grand"] }, { u: 2, v: 3, name: ["Harms"] }],
  );
  const { clusters } = consolidate(buildGraph(bundle), { toleranceM: 5 });
  const degree = new Map(clusters.map((c) => [c.members[0], c.degree]));
  assert.deepEqual([degree.get(1), degree.get(2), degree.get(3)], [1, 3, 2]);
});

test("a loop road of 100 m or more counts two legs; a shorter loop is a turning circle", () => {
  // Wherever it sits, osmnx's street_count counts a loop twice. A loop
  // under 100 m forms a turning circle that makes no junction. A longer
  // one reads as a loop road that meets its stem at a junction and counts
  // two legs there, as does a loop that leaves a through road.
  const degree = (bundle) => new Map(consolidate(buildGraph(bundle), { toleranceM: 5 }).clusters.map((c) => [c.members[0], c.degree]));
  const stub = (loopM) => bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }],
    [{ u: 1, v: 2 }, { u: 2, v: 2, geomXY: loopXY(200, 0, loopM) }],
  );
  assert.deepEqual([degree(stub(40)).get(1), degree(stub(40)).get(2)], [1, 1]);
  assert.deepEqual([degree(stub(300)).get(1), degree(stub(300)).get(2)], [1, 3]);
  const through = (loopM) => bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 4 }, { id: 3, x: 400, y: 0, sc: 1 }],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 2, geomXY: loopXY(200, 0, loopM) }],
  );
  assert.equal(degree(through(40)).get(2), 2);
  assert.equal(degree(through(300)).get(2), 4);
});

test("a link leg counts only where the road would otherwise dead-end", () => {
  // A through road 1-2-3 and a ramp 2-4: osmnx's street_count reads node 2 as
  // three-way, but a ramp joining a through road adds nothing, so the
  // junction reads degree 2, and the ramp's far end, with no road at all,
  // reads degree 0.
  const tee = (highway) => bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 3 }, { id: 3, x: 400, y: 0, sc: 1 }, { id: 4, x: 200, y: 200, sc: 1 }],
    [{ u: 1, v: 2 }, { u: 2, v: 3 }, { u: 2, v: 4, highway }],
  );
  const degree = (bundle) => new Map(consolidate(buildGraph(bundle), { toleranceM: 5 }).clusters.map((c) => [c.members[0], c.degree]));
  assert.deepEqual([...degree(tee("primary_link")).entries()].sort(), [[1, 1], [2, 2], [3, 1], [4, 0]]);
  assert.equal(degree(tee(["secondary", "primary_link"])).get(2), 2, "simplification joined a ramp to the road it becomes: a ramp at both ends");
  // Road 1-2 ends at node 2 in ramps. With one road leg, the ramps mark
  // where the road goes on, so two ramps make a three-leg junction and
  // one ramp makes a two-leg bend.
  const roadEnd = (ramps) => bundleFromXY(
    [{ id: 1, x: 0, y: 0, sc: 1 }, { id: 2, x: 200, y: 0, sc: 1 + ramps }, ...Array.from({ length: ramps }, (_, k) => ({ id: 4 + k, x: 400, y: -100 + 100 * k, sc: 1 }))],
    [{ u: 1, v: 2 }, ...Array.from({ length: ramps }, (_, k) => ({ u: 2, v: 4 + k, highway: "primary_link" }))],
  );
  assert.equal(degree(roadEnd(2)).get(2), 3);
  assert.equal(degree(roadEnd(1)).get(2), 2);
});

test("a junction pair across a median with no crossing segment is one cluster", () => {
  // Node 2 on the westbound carriageway and node 3 on the eastbound one are
  // 8 m apart, inside the merge distance, but osmnx's rebuild splits a merged
  // buffer into its connected components and no edge connects them. A median
  // tie stands in for the segment the median has no room for: one cluster,
  // whose carriageways count as one leg each way.
  const g = buildGraph(medianPairBundle());
  const ties = medianTies(g, 5, new Set());
  assert.deepEqual([...ties], [[2, [3]], [3, [2]]]);
  const { clusters } = consolidate(g, { toleranceM: 5, ties });
  const pair = clusters.find((c) => c.members.length === 2);
  assert.deepEqual(pair.members, [2, 3]);
  assert.equal(pair.degree, 4);
  assert.equal(pair.split, false);
});

test("the tie asks for one road, opposite directions, the merge distance, and two nodes in the merge", () => {
  const other = buildGraph(medianPairBundle({ sameName: false }));
  assert.equal(medianTies(other, 5, new Set()).size, 0);
  const apart = consolidate(other, { toleranceM: 5 }).clusters.filter((c) => c.split);
  assert.deepEqual(apart.map((c) => c.members), [[3], [2]], "split into two, as osmnx leaves them");
  assert.equal(medianTies(buildGraph(medianPairBundle({ gapM: 12 })), 5, new Set()).size, 0);
  assert.equal(medianTies(buildGraph(medianPairBundle()), 5, new Set([3])).size, 0, "a held-out node is never tied");
});

test("a split component's centroid counts coincident nodes once, as the library and osmnx do", () => {
  // Nodes 1 and 2 coincide, 4 is 4 m east of them, and junction 3, 8 m
  // east, has no edge to them, so the merged buffer splits in two.
  const nodes = [
    { id: 1, x: 1000, y: 1000, sc: 3 }, { id: 2, x: 1000, y: 1000, sc: 3 }, { id: 4, x: 1004, y: 1000, sc: 3 }, { id: 3, x: 1008, y: 1000, sc: 3 },
    { id: 10, x: 1000, y: 1200, sc: 1 }, { id: 11, x: 800, y: 1000, sc: 1 }, { id: 12, x: 1004, y: 800, sc: 1 },
    { id: 20, x: 1008, y: 1200, sc: 1 }, { id: 21, x: 1208, y: 1000, sc: 1 }, { id: 22, x: 1008, y: 800, sc: 1 },
  ];
  const edges = [{ u: 1, v: 2 }, { u: 2, v: 4 }, { u: 1, v: 10 }, { u: 2, v: 11 }, { u: 4, v: 12 }, { u: 3, v: 20 }, { u: 3, v: 21 }, { u: 3, v: 22 }];
  const g = buildGraph(bundleFromXY(nodes, edges));
  const unmergeable = new Set([...deadEnds(g), ...ineligibleLinkNodes(g, 3, 5)]);
  const { clusters } = consolidate(g, { toleranceM: 5, unmergeable, ties: medianTies(g, 5, unmergeable) });
  const c = clusters.find((k) => k.members.join(",") === "1,2,4");
  assert.equal(c.x, 1002);
});
