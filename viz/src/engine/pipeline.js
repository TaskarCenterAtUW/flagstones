import { deadEnds, withoutDeadEnds } from "./graph.js";
import { consolidate, ineligibleLinkNodes, medianTies } from "./cluster.js";
import { fallbackTiles } from "./fallback.js";
import { classifyTransitions } from "./transitions.js";
import { clipTree, prepareClip, tessellate } from "./voronoi.js";
import { tileCell, validateParams } from "./snap.js";
import { crsKey } from "./proj.js";
import { rectRing } from "./geometry.js";

export function bboxClip(points, bufferM) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const p of points) {
    if (p.x < minx) minx = p.x; if (p.y < miny) miny = p.y;
    if (p.x > maxx) maxx = p.x; if (p.y > maxy) maxy = p.y;
  }
  return [[rectRing([minx - bufferM, miny - bufferM, maxx + bufferM, maxy + bufferM])]];
}

// The library takes its bbox clip only after collapse_seed_collisions, so any
// seeds sharing a cell have merged by then and the extent must span the
// collapsed centers. We return a mean point per used cell.
export function cellMeans(seeds, gridM) {
  const byCell = new Map();
  for (const s of seeds) {
    const k = tileCell(s.x, s.y, gridM).join(":");
    if (!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(s);
  }
  return [...byCell.values()].map((group) => ({
    x: group.reduce((sum, s) => sum + s.x, 0) / group.length,
    y: group.reduce((sum, s) => sum + s.y, 0) / group.length,
  }));
}

// `cache`, a Map the caller keeps between calls, holds each stage's last
// result under a key of everything the stage reads, so an edit recomputes
// only the stages after it. A fallback edit keeps the tiles, and a clip
// edit keeps the merge. A clip spec's `key` names its polygon and its
// `extentKey` names its extent. Stages without those keys always run. The
// graph's `version` stands for its node positions.
export function computeVariant(graph, params, clipSpec, cache = null) {
  validateParams({ toleranceM: params.toleranceM, gridM: params.gridM });
  const stage = (name, key, build) => {
    if (!cache || key == null) return build();
    const hit = cache.get(name);
    if (hit && hit.key === key) return hit.value;
    const value = build();
    cache.set(name, { key, value });
    return value;
  };
  const opts = params.transitionOpts ?? {};
  const junctionDegree = params.junctionDegree ?? params.minDegree;
  const mergeKey = [graph.version, crsKey(graph.epsg), params.toleranceM, params.keepDeadEnds, params.contractTransitions,
    opts.requireSameRoad, opts.spreadMaxDeg, opts.oppositeMinDeg, junctionDegree].join("|");
  const { transitions, excluded, unmergeable, ties, clusters, clusterOf } = stage("merge", mergeKey, () => {
    const transitions = params.contractTransitions ? classifyTransitions(graph, opts) : new Map();
    const working = params.keepDeadEnds ? graph : withoutDeadEnds(graph);
    // Dead ends stay in the graph so their junction keeps a leg for the
    // cul-de-sac. The merge leaves them alone, so one forms a degree-1 cluster
    // (degree 0 if its lone leg is a ramp) that the degree filter deletes as
    // soon as its value falls under minDegree. A ramp node that is no junction
    // on its own waits out the merge likewise, judged against `junctionDegree`
    // no matter which minDegree sits in the filter.
    const dead = deadEnds(working);
    const unmergeable = new Set([...dead, ...ineligibleLinkNodes(working, junctionDegree, params.toleranceM, dead)]);
    const excluded = new Set(transitions.keys());
    // medianTies ties junction pairs across medians with no crossing segment
    // (its comment in *cluster.js* explains the rule), and `skip` holds out
    // the nodes the merge leaves out.
    const ties = medianTies(working, params.toleranceM, new Set([...unmergeable, ...excluded]));
    return { transitions, excluded, unmergeable, ties, ...consolidate(working, { toleranceM: params.toleranceM, excluded, unmergeable, ties }) };
  });
  const seeds = clusters.filter((c) => !c.excluded && c.degree >= params.minDegree);
  const dropped = {
    deadEnds: clusters.filter((c) => !c.excluded && c.degree <= 1 && c.degree < params.minDegree).length,
    degree2: clusters.filter((c) => !c.excluded && c.degree === 2 && params.minDegree > 2).length,
    transitions: clusters.filter((c) => c.excluded).length,
  };
  const snapCentroid = params.snapCentroid ?? true;
  const tilesKey = clipSpec.key == null ? null
    : [mergeKey, params.minDegree, params.gridM, snapCentroid, crsKey(params.epsg), clipSpec.key].join("|");
  const { clip, tiles } = stage("tiles", tilesKey, () => {
    if (clipSpec.kind === "bbox") {
      const clip = bboxClip(cellMeans(seeds, params.gridM), clipSpec.bufferM);
      return { clip, tiles: seeds.length ? tessellate(seeds, { clip, gridM: params.gridM, epsg: params.epsg, snapCentroid }) : [] };
    }
    const clip = clipSpec.multi;
    const prepared = seeds.length ? stage("clip", clipSpec.key == null ? null : `${clipSpec.key}|${crsKey(params.epsg)}`, () => prepareClip(clip)) : null;
    return { clip, tiles: seeds.length ? tessellate(seeds, { clip, gridM: params.gridM, epsg: params.epsg, snapCentroid, prepared }) : [] };
  });
  const wantFallback = params.fallbackCellM > 0 && clipSpec.extent;
  const fallbackKey = tilesKey == null || clipSpec.extentKey == null ? null
    : [tilesKey, clipSpec.extentKey, params.fallbackCellM, params.fallbackMinShare].join("|");
  const { fallback, warnings } = !wantFallback ? { fallback: [], warnings: [] } : stage("fallback", fallbackKey, () => {
    try {
      const tree = stage("extent", clipSpec.extentKey == null ? null : `${clipSpec.extentKey}|${crsKey(params.epsg)}`, () => clipTree(clipSpec.extent));
      return { fallback: fallbackTiles(tiles, { extent: clipSpec.extent, crs: params.epsg, cellM: params.fallbackCellM, minShare: params.fallbackMinShare, tree }), warnings: [] };
    } catch (err) {
      console.error(err);
      return { fallback: [], warnings: [`fallback: ${err.message}`] };
    }
  });
  return { tiles, fallback, warnings: [...warnings], clusters, clusterOf, seeds, transitions, excluded, unmergeable, ties, dropped, clip };
}

function signature(tile) {
  const vertices = tile.geometry.reduce((n, p) => n + p.reduce((m, r) => m + r.length, 0), 0);
  return `${tile.area.toFixed(3)}|${vertices}|${tile.seedX}|${tile.seedY}`;
}

export function diffTiles(prev, next) {
  const before = new Map(prev.map((t) => [t.nativeId, signature(t)]));
  const after = new Map(next.map((t) => [t.nativeId, signature(t)]));
  const added = new Set(), removed = new Set(), changed = new Set();
  for (const id of after.keys()) if (!before.has(id)) added.add(id);
  for (const [id, sig] of before) {
    if (!after.has(id)) removed.add(id);
    else if (after.get(id) !== sig) changed.add(id);
  }
  return { added, removed, changed };
}
