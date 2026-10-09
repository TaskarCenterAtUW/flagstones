import polygonClipping from "polygon-clipping";
import { multiPolygonArea, multiPolygonBounds, polygonArea, rectRing, ringBounds, sharedLength } from "./geometry.js";
import { crsToken } from "./snap.js";
import { clipTree, localClip } from "./voronoi.js";

// Mirrors flagstones/fallback.py, and test/parity.test.js runs the engines
// against each other. Square cells anchored at the CRS origin fill the extent
// the intersection tiles leave open. A piece under minShare of a full cell
// joins the fallback piece it shares the longest boundary with and takes that
// piece's id. A piece that borders no fallback piece joins its own cell's tile,
// alone when the cell holds nothing else, and a cell's disconnected pieces
// merge into one tile. The fallback never alters the intersection tiles.

// NOISE_AREA_M2 in the library. A piece below it is overlay noise, so the
// stage mints nothing for it.
const NOISE_AREA_M2 = 0.01;

export function mintFallbackId(cx, cy, crs) {
  return `${crsToken(crs)}:f:${cx}:${cy}`;
}

function overlaps(a, b) {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

function cellsOf(extent, c) {
  const keys = new Set();
  for (const polygon of extent) {
    const [x0, y0, x1, y1] = ringBounds(polygon[0]);
    for (let i = Math.floor(x0 / c); i <= Math.floor(x1 / c); i++) {
      for (let j = Math.floor(y0 / c); j <= Math.floor(y1 / c); j++) keys.add(`${i},${j}`);
    }
  }
  return [...keys].map((k) => k.split(",").map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

const cellBefore = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);

// The indexes of the boxes that might touch each cell, in box order. The
// range reaches one cell further each way, so a box that only touches a
// cell's edge is still listed, and the caller's overlaps() test decides.
function boxesByCell(boxes, c) {
  const near = new Map();
  boxes.forEach(([x0, y0, x1, y1], k) => {
    for (let i = Math.floor(x0 / c) - 1; i <= Math.floor(x1 / c) + 1; i++) {
      for (let j = Math.floor(y0 / c) - 1; j <= Math.floor(y1 / c) + 1; j++) {
        const key = `${i},${j}`;
        if (!near.has(key)) near.set(key, []);
        near.get(key).push(k);
      }
    }
  });
  return near;
}

function addTo(owned, cell, polygon) {
  const key = `${cell[0]},${cell[1]}`;
  const body = owned.get(key);
  if (body) body.geometry = polygonClipping.union(body.geometry, [polygon]);
  else owned.set(key, { cell, geometry: [polygon] });
}

// A caller that built the extent's clipTree may hand it in as `tree`.
export function fallbackTiles(tiles, { extent, crs, cellM, minShare, tree = null }) {
  if (!(Number.isFinite(cellM) && cellM > 0)) throw new RangeError(`cellM must be a finite positive value, got ${cellM}`);
  if (!(minShare >= 0 && minShare < 1)) throw new RangeError(`minShare must stay from 0 up to, yet below, 1, got ${minShare}`);
  if (!extent || !extent.length) return [];
  tree ??= clipTree(extent);
  const boxes = tiles.map((t) => multiPolygonBounds(t.geometry));
  const near = boxesByCell(boxes, cellM);
  const sliverBelow = minShare * cellM * cellM;
  const owned = new Map();
  let slivers = [];
  for (const [i, j] of cellsOf(extent, cellM)) {
    const bounds = [i * cellM, j * cellM, (i + 1) * cellM, (j + 1) * cellM];
    const local = localClip(tree, bounds);
    if (!local.length) continue;
    let ground = polygonClipping.intersection([[rectRing(bounds)]], local);
    if (!ground.length) continue;
    const hits = (near.get(`${i},${j}`) ?? []).filter((k) => overlaps(boxes[k], bounds)).map((k) => tiles[k].geometry);
    if (hits.length) ground = polygonClipping.difference(ground, ...hits);
    for (const polygon of ground) {
      const area = polygonArea(polygon);
      if (area < NOISE_AREA_M2) continue;
      if (area < sliverBelow) slivers.push({ cell: [i, j], polygon });
      else addTo(owned, [i, j], polygon);
    }
  }
  while (slivers.length && owned.size) {
    const receivers = [...owned.values()].map((o) => ({ o, bounds: multiPolygonBounds(o.geometry) }));
    const nearReceivers = boxesByCell(receivers.map((r) => r.bounds), cellM);
    const moves = [], waiting = [];
    for (const s of slivers) {
      const bounds = ringBounds(s.polygon[0]);
      // Candidate arrival order decides nothing. The rule below chooses
      // by shared boundary length, and its tie-break by the lower cell.
      const candidates = new Set();
      for (let i = Math.floor(bounds[0] / cellM); i <= Math.floor(bounds[2] / cellM); i++) {
        for (let j = Math.floor(bounds[1] / cellM); j <= Math.floor(bounds[3] / cellM); j++) {
          for (const k of nearReceivers.get(`${i},${j}`) ?? []) candidates.add(k);
        }
      }
      let best = null;
      for (const k of candidates) {
        const { o, bounds: ob } = receivers[k];
        if (!overlaps(ob, bounds)) continue;
        const shared = sharedLength([s.polygon], o.geometry);
        if (shared <= 0) continue;
        if (!best || shared > best.shared || (shared === best.shared && cellBefore(o.cell, best.o.cell))) best = { o, shared };
      }
      if (best) moves.push({ o: best.o, polygon: s.polygon });
      else waiting.push(s);
    }
    if (!moves.length) break;
    // All choices of a pass commit against the same tile set, as they
    // do in the library.
    for (const { o, polygon } of moves) o.geometry = polygonClipping.union(o.geometry, [polygon]);
    slivers = waiting;
  }
  for (const s of slivers) addTo(owned, s.cell, s.polygon);
  return [...owned.values()]
    .sort((a, b) => a.cell[0] - b.cell[0] || a.cell[1] - b.cell[1])
    .map(({ cell: [i, j], geometry }) => ({
      nativeId: mintFallbackId(i, j, crs), kind: "fallback",
      cellX: i, cellY: j, seedX: (i + 0.5) * cellM, seedY: (j + 0.5) * cellM,
      geometry, area: multiPolygonArea(geometry), nodeCount: 0, degree: 0, osmids: [],
    }));
}
