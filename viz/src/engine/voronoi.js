import { Delaunay } from "d3-delaunay";
import polygonClipping from "polygon-clipping";
import { tileCell, mintNativeId } from "./snap.js";
import { multiPolygonArea, multiPolygonBounds, pointInMultiPolygon, pointInPolygon, polygonArea, rectRing, ringBounds, sharedLength, snapToTileGrid } from "./geometry.js";

// Buckets the clip's boundary segments by grid cell, so a Voronoi cell can
// cheaply test whether the boundary passes through its bounding box.
function boundaryIndex(multi, cellSize) {
  const buckets = new Map();
  const key = (cx, cy) => `${cx},${cy}`;
  for (const polygon of multi) for (const ring of polygon) {
    for (let i = 0; i < ring.length - 1; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[i + 1];
      const minx = Math.min(x1, x2), maxx = Math.max(x1, x2);
      const miny = Math.min(y1, y2), maxy = Math.max(y1, y2);
      for (let cx = Math.floor(minx / cellSize); cx <= Math.floor(maxx / cellSize); cx++) {
        for (let cy = Math.floor(miny / cellSize); cy <= Math.floor(maxy / cellSize); cy++) {
          const k = key(cx, cy);
          if (!buckets.has(k)) buckets.set(k, []);
          buckets.get(k).push([minx, miny, maxx, maxy]);
        }
      }
    }
  }
  return { buckets, cellSize, key };
}

function bboxTouchesBoundary(index, [minx, miny, maxx, maxy]) {
  const { buckets, cellSize, key } = index;
  for (let cx = Math.floor(minx / cellSize); cx <= Math.floor(maxx / cellSize); cx++) {
    for (let cy = Math.floor(miny / cellSize); cy <= Math.floor(maxy / cellSize); cy++) {
      const bucket = buckets.get(key(cx, cy));
      if (!bucket) continue;
      for (const [bx1, by1, bx2, by2] of bucket) {
        if (bx1 <= maxx && bx2 >= minx && by1 <= maxy && by2 >= miny) return true;
      }
    }
  }
  return false;
}

// The clearance a cell keeps from a quadtree boundary before stepping
// across it. With cell edges kept off the piece's cut line, the points
// handed back mark crossings of the clip by the cell itself.
const TREE_MARGIN_M = 1;
const TREE_MAX_DEPTH = 14;

// A loose quadtree over the clip, filled in on demand. The tree is lazy: a node
// holds its parent's piece cut to its own rectangle, so a boundary cell tests
// local geometry instead of the full clip, which runs to thousands of vertices
// for a city. On every side, a child's rectangle grows past its quadrant by a
// quarter of its parent's size, so a box up to half the parent's size lands in
// the child whose quadrant holds its center. Even a cell straddling a center
// line descends instead of keeping a piece of whole-clip size.
export function clipTree(clip) {
  return { rect: multiPolygonBounds(clip), piece: clip, depth: 0, children: null };
}

function childRects([x0, y0, x1, y1], q) {
  const w = x1 - x0, h = y1 - y0, mx = x0 + w / 2, my = y0 + h / 2;
  const strict = [q & 1 ? mx : x0, q & 2 ? my : y0, q & 1 ? x1 : mx, q & 2 ? y1 : my];
  const loose = [strict[0] - w / 4, strict[1] - h / 4, strict[2] + w / 4, strict[3] + h / 4];
  return { strict, loose };
}

function quadrant(node, q) {
  if (!node.children) node.children = [null, null, null, null];
  if (node.children[q]) return node.children[q];
  const { strict, loose } = childRects(node.rect, q);
  const piece = node.piece.length ? polygonClipping.intersection(node.piece, [[rectRing(loose)]]) : [];
  node.children[q] = { rect: strict, piece, depth: node.depth + 1, children: null };
  return node.children[q];
}

// Descend while a child's loose rectangle contains the box at the margin, then
// hand back the piece stored at the deepest node reached. When the piece comes
// back empty, the box falls outside the clip.
export function localClip(tree, [bx0, by0, bx1, by1]) {
  const [rx0, ry0, rx1, ry1] = tree.rect;
  if (bx1 < rx0 || bx0 > rx1 || by1 < ry0 || by0 > ry1) return [];
  const cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2;
  let node = tree;
  while (node.piece.length && node.depth < TREE_MAX_DEPTH) {
    const [x0, y0, x1, y1] = node.rect;
    const q = (cx > (x0 + x1) / 2 ? 1 : 0) | (cy > (y0 + y1) / 2 ? 2 : 0);
    const { loose } = childRects(node.rect, q);
    const fits = bx0 > loose[0] + TREE_MARGIN_M && bx1 < loose[2] - TREE_MARGIN_M
      && by0 > loose[1] + TREE_MARGIN_M && by1 < loose[3] - TREE_MARGIN_M;
    if (!fits) break;
    node = quadrant(node, q);
  }
  return node.piece;
}

// Bounds, index and quadtree derive from the clip alone. Across a seed-only
// change, keeping them skips the rebuild, while the quadtree grows on use.
export function prepareClip(clip) {
  const bounds = multiPolygonBounds(clip);
  return { bounds, index: boundaryIndex(clip, Math.max(50, (bounds[2] - bounds[0]) / 200)), tree: clipTree(clip) };
}

export function tessellate(seeds, { clip, gridM, epsg, snapCentroid = true, prepared = null }) {
  if (!seeds.length) return [];
  // Two seeds falling in the same snap cell (the grid reached past the collision
  // bound) become a single site, so a duplicate point never reaches Voronoi,
  // and the merged tile keeps the seedCollision flag.
  const byCell = new Map();
  for (const s of seeds) {
    const [cx, cy] = tileCell(s.x, s.y, gridM);
    const k = `${cx}:${cy}`;
    if (!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(s);
  }
  const sites = [...byCell.values()].map((group) => {
    const [cx, cy] = tileCell(group[0].x, group[0].y, gridM);
    const collision = group.length > 1;
    const rawX = average(group.map((s) => s.x)), rawY = average(group.map((s) => s.y));
    return {
      cellX: cx, cellY: cy,
      // The library's snap_centroid switch. When the switch is set it bisects
      // between the grid corners, and when cleared it bisects between the raw
      // centroids (a collapsed cell's mean), under the same IDs either way.
      seedX: snapCentroid ? cx * gridM : rawX, seedY: snapCentroid ? cy * gridM : rawY,
      rawX, rawY,
      // Sorted, matching what collapse_seed_collisions lists for a
      // collapsed pair's osmids.
      members: group.flatMap((s) => s.members).sort((a, b) => a - b),
      degree: Math.max(...group.map((s) => s.degree)),
      index: group[0].index,
      seedCollision: collision,
    };
  });
  const { bounds, index, tree } = prepared ?? prepareClip(clip);
  const voronoi = Delaunay.from(sites.map((s) => [s.seedX, s.seedY])).voronoi(bounds);
  const cells = sites.map((_, i) => voronoi.cellPolygon(i));
  const boxes = cells.map((cell) => cell && ringBounds(cell));
  const touches = boxes.map((box) => !!box && bboxTouchesBoundary(index, box));

  // See no boundary through a cell and it sits clean, wholly in or wholly out,
  // like the clean neighbors beside it, so one point check answers for a whole
  // connected run. Traversal follows the clipped diagram, whose adjacencies
  // skip edges the bounds cut away.
  const inside = new Array(sites.length).fill(null);
  for (let i = 0; i < sites.length; i++) {
    if (!cells[i] || touches[i] || inside[i] !== null) continue;
    const verdict = pointInMultiPolygon([sites[i].seedX, sites[i].seedY], clip);
    inside[i] = verdict;
    const stack = [i];
    while (stack.length) {
      const j = stack.pop();
      for (const k of voronoi.neighbors(j)) {
        if (cells[k] && !touches[k] && inside[k] === null) { inside[k] = verdict; stack.push(k); }
      }
    }
  }

  const tiles = [];
  for (let i = 0; i < sites.length; i++) {
    const cell = cells[i];
    if (!cell) continue;
    const site = sites[i];
    let geometry, truncated;
    if (!touches[i]) {
      if (!inside[i]) continue;
      geometry = snapToTileGrid([[cell]]); truncated = false;
    } else {
      const piece = localClip(tree, boxes[i]);
      if (!piece.length) continue;
      geometry = snapToTileGrid(polygonClipping.intersection([[cell]], piece));
      if (!geometry.length) continue;
      truncated = true;
    }
    const area = multiPolygonArea(geometry);
    if (area === 0) continue;
    tiles.push({
      nativeId: mintNativeId(site.cellX, site.cellY, epsg),
      cellX: site.cellX, cellY: site.cellY,
      seedX: site.seedX, seedY: site.seedY,
      rawX: site.rawX, rawY: site.rawY,
      geometry, area,
      nodeCount: site.members.length, degree: site.degree, osmids: site.members,
      truncated, clusterIndex: site.index,
      seedCollision: site.seedCollision,
    });
  }
  attachDetachedPieces(tiles);
  tiles.sort((a, b) => a.cellX - b.cellX || a.cellY - b.cellY);
  return tiles;
}

function average(values) { return values.reduce((a, b) => a + b, 0) / values.length; }

// Which polygon of a split tile contains its seed. The polygon covering
// the point takes it, a seed sitting on a shared boundary hands the
// choice to the largest of the polygons it touches. Outside every polygon,
// a seed picks the largest overall.
function tileBody(tile) {
  if (tile.geometry.length === 1) return tile.geometry[0];
  const seed = [tile.seedX, tile.seedY];
  const holders = tile.geometry.filter((poly) => pointInPolygon(seed, poly) || onBoundary(seed, poly));
  return (holders.length ? holders : tile.geometry).reduce((a, b) => (polygonArea(b) > polygonArea(a) ? b : a));
}

function onBoundary([px, py], polygon) {
  for (const ring of polygon) {
    for (let i = 0; i < ring.length - 1; i++) {
      const [ax, ay] = ring[i], [bx, by] = ring[i + 1];
      const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
      if (Math.hypot(px - ax - t * dx, py - ay - t * dy) < 1e-9) return true;
    }
  }
  return false;
}

// A shard lying off its tile's seed-holding polygon migrates to the neighboring
// tile whose seed-holding polygon shares the longest boundary with it, and the
// smaller `nativeId` decides the standoff. Gaps, concavities or holes in the
// clip can shatter a cell, and a distant shard belongs to the intersection
// next door, though its own seed looks nearer on the plane. A shard ringed only
// by other shards waits for the next round. A clip lobe with no intersection at
// all loses its shard, and each tile ends holding exactly one shape. This
// mirrors the library's _attach_detached_pieces pass for pass, and both
// arrive at identical receivers.
export function attachDetachedPieces(tiles) {
  let remaining = Infinity;
  for (;;) {
    const bodies = tiles.map(tileBody);
    const detached = [];
    tiles.forEach((tile, i) => {
      if (tile.geometry.length < 2) return;
      for (const piece of tile.geometry) if (piece !== bodies[i]) detached.push({ i, piece, area: polygonArea(piece) });
    });
    // Moves land on the receiving body, so a round should leave fewer detached
    // pieces than the one before. A union that precision leaves in two parts
    // breaks the promise, so a round that fails to shrink the set ends the loop
    // and its stranded pieces drop the way pieces no tile accepts drop.
    if (!detached.length || detached.length >= remaining) break;
    remaining = detached.length;
    const boxes = bodies.map((body) => ringBounds(body[0]));
    detached.sort((p, q) => (tiles[p.i].nativeId < tiles[q.i].nativeId ? -1 : tiles[p.i].nativeId > tiles[q.i].nativeId ? 1 : q.area - p.area));
    const moves = [];
    for (const { i, piece } of detached) {
      const [px0, py0, px1, py1] = ringBounds(piece[0]);
      let best = null;
      for (let j = 0; j < tiles.length; j++) {
        if (j === i) continue;
        const [bx0, by0, bx1, by1] = boxes[j];
        if (bx0 > px1 || bx1 < px0 || by0 > py1 || by1 < py0) continue;
        const shared = sharedLength([piece], [bodies[j]]);
        if (shared <= 0) continue;
        if (!best || shared > best.shared || (shared === best.shared && tiles[j].nativeId < tiles[best.j].nativeId)) best = { j, shared };
      }
      if (best) moves.push({ i, piece, j: best.j });
    }
    if (!moves.length) break;
    // Stripping every donor precedes delivery. Because unioning rebuilds
    // the receiver's polygons, a tile that gives and takes the same round
    // could no longer locate its own donated piece, and the piece would
    // end up on both tiles.
    const geometries = tiles.map((tile) => tile.geometry);
    for (const { i, piece } of moves) geometries[i] = geometries[i].filter((poly) => poly !== piece);
    for (const { piece, j } of moves) geometries[j] = snapToTileGrid(polygonClipping.union(geometries[j], [piece]));
    const touched = new Set();
    for (const { i, j } of moves) { touched.add(i); touched.add(j); }
    for (const k of touched) {
      tiles[k].geometry = geometries[k];
      tiles[k].area = multiPolygonArea(geometries[k]);
    }
  }
  for (const tile of tiles) {
    if (tile.geometry.length < 2) continue;
    tile.geometry = [tileBody(tile)];
    tile.area = multiPolygonArea(tile.geometry);
  }
}
