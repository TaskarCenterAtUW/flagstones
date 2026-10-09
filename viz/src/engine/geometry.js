// Ring: [[x, y], ...] closed. Polygon: [exterior, ...holes]. MultiPolygon:
// Polygon[]. polygon-clipping takes the same shapes, unconverted.

export function closeRing(ring) {
  const [fx, fy] = ring[0];
  const [lx, ly] = ring[ring.length - 1];
  return fx === lx && fy === ly ? ring : [...ring, [fx, fy]];
}

// Products anchor on the ring's own leading corner rather than the origin.
// Absolute EPSG:5070 coordinates push the cross terms to about 5e12, and
// after cancellation the surviving area lands as a multiple near 5e-4 m2,
// about the grain of a sliver's parity tolerance. GEOS likewise measures
// from a vertex.
function signedArea(ring) {
  if (ring.length < 3) return 0;
  const [x0, y0] = ring[0];
  let s = 0;
  for (let i = 1; i < ring.length - 1; i++) {
    const x1 = ring[i][0] - x0, y1 = ring[i][1] - y0;
    const x2 = ring[i + 1][0] - x0, y2 = ring[i + 1][1] - y0;
    s += x1 * y2 - x2 * y1;
  }
  return s / 2;
}

export function ringArea(ring) {
  return Math.abs(signedArea(ring));
}

export function polygonArea(polygon) {
  let a = ringArea(polygon[0]);
  for (let i = 1; i < polygon.length; i++) a -= ringArea(polygon[i]);
  return a;
}

export function multiPolygonArea(multi) {
  return multi.reduce((sum, p) => sum + polygonArea(p), 0);
}

export function ringBounds(ring) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const [x, y] of ring) {
    if (x < minx) minx = x;
    if (y < miny) miny = y;
    if (x > maxx) maxx = x;
    if (y > maxy) maxy = y;
  }
  return [minx, miny, maxx, maxy];
}

// Traces [minx, miny, maxx, maxy] counter-clockwise, closing the loop
// back on the corner it began.
export function rectRing([minx, miny, maxx, maxy]) {
  return [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]];
}

function segmentsOf(multi) {
  const out = [];
  for (const polygon of multi) for (const ring of polygon) {
    for (let k = 0; k < ring.length - 1; k++) out.push([ring[k][0], ring[k][1], ring[k + 1][0], ring[k + 1][1]]);
  }
  return out;
}

// Total length of boundary the two multipolygons share. Because each side may
// split an edge at vertices of its own, shared stretches need not line up end
// to end, so the measure sums collinear overlaps. A sliver's edge on a fallback
// grid line may end before its neighbor's does, while a union may drop a vertex
// from either side while the other keeps it. The library equivalent is
// piece.boundary.intersection(body.boundary).length.
export function sharedLength(a, b, toleranceM = 1e-6) {
  const others = segmentsOf(b);
  let total = 0;
  for (const [x1, y1, x2, y2] of segmentsOf(a)) {
    const len = Math.hypot(x2 - x1, y2 - y1);
    if (len === 0) continue;
    const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
    const spans = [];
    for (const [x3, y3, x4, y4] of others) {
      if (Math.abs((x3 - x1) * uy - (y3 - y1) * ux) > toleranceM) continue;
      if (Math.abs((x4 - x1) * uy - (y4 - y1) * ux) > toleranceM) continue;
      const t3 = (x3 - x1) * ux + (y3 - y1) * uy, t4 = (x4 - x1) * ux + (y4 - y1) * uy;
      const lo = Math.max(0, Math.min(t3, t4)), hi = Math.min(len, Math.max(t3, t4));
      if (hi > lo) spans.push([lo, hi]);
    }
    // A hairline puts both of its edges inside a segment's tolerance
    // band, so overlapping spans fuse before tallying, the way a boundary
    // intersection adds them once.
    spans.sort((p, q) => p[0] - q[0]);
    let end = -Infinity;
    for (const [lo, hi] of spans) {
      if (hi <= end) continue;
      total += hi - Math.max(lo, end);
      end = hi;
    }
  }
  return total;
}

// Tile coordinates snap to a spacing of TILE_PRECISION_M, in CRS meters, the
// value the library declares. Two adjacent cells, clipped as separate
// operations, can leave their shared crossing a ulp apart, and a shared grid
// reunites the pair. GEOS scales by the spacing's inverse and rounds half up,
// and Math.round does the same, so both engines land on the same grid point.
export const TILE_PRECISION_M = 1e-6;
const TILE_GRID_SCALE = 1e6;

// Rebuilds `multi` with its corners pinned to grid multiples. A corner
// rounding onto its predecessor disappears. Snapped rings need at least four
// points and positive area, else they drop. Only the outer ring's loss
// deletes the whole polygon, and a dropped hole removes its cutout alone.
export function snapToTileGrid(multi) {
  const out = [];
  for (const polygon of multi) {
    const rings = [];
    for (const ring of polygon) {
      const snapped = [];
      for (const [x, y] of ring) {
        const p = [Math.round(x * TILE_GRID_SCALE) / TILE_GRID_SCALE, Math.round(y * TILE_GRID_SCALE) / TILE_GRID_SCALE];
        const last = snapped[snapped.length - 1];
        if (!last || last[0] !== p[0] || last[1] !== p[1]) snapped.push(p);
      }
      if (snapped.length >= 4 && ringArea(snapped) > 0) rings.push(snapped);
      else if (!rings.length) break;
    }
    if (rings.length) out.push(rings);
  }
  return out;
}

export function multiPolygonBounds(multi) {
  let b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const polygon of multi) {
    const r = ringBounds(polygon[0]);
    b = [Math.min(b[0], r[0]), Math.min(b[1], r[1]), Math.max(b[2], r[2]), Math.max(b[3], r[3])];
  }
  return b;
}

export function pointInRing([px, py], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

export function pointInPolygon(pt, polygon) {
  if (!pointInRing(pt, polygon[0])) return false;
  for (let i = 1; i < polygon.length; i++) if (pointInRing(pt, polygon[i])) return false;
  return true;
}

export function pointInMultiPolygon(pt, multi) {
  return multi.some((polygon) => pointInPolygon(pt, polygon));
}

function ringCentroidWeighted(ring) {
  // Weights use signed area, so hole rings subtract their share. Because
  // raw city coordinates put the x*y terms around 5e12, products anchor
  // off the ring's own starting corner; origin-anchored cancellation drift
  // would push merged-cluster centroids meters from GEOS.
  const [ox, oy] = ring[0];
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const x1 = ring[i][0] - ox, y1 = ring[i][1] - oy;
    const x2 = ring[i + 1][0] - ox, y2 = ring[i + 1][1] - oy;
    const cross = x1 * y2 - x2 * y1;
    a += cross;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  const sa = a / 2;
  return [cx / 6 + ox * sa, cy / 6 + oy * sa, sa];
}

export function multiPolygonCentroid(multi) {
  let sx = 0, sy = 0, sa = 0;
  for (const polygon of multi) {
    polygon.forEach((ring, i) => {
      let [wx, wy, a] = ringCentroidWeighted(ring);
      const sign = (i === 0 ? 1 : -1) * Math.sign(a || 1);
      sx += wx * sign; sy += wy * sign; sa += Math.abs(a) * (i === 0 ? 1 : -1);
    });
  }
  return [sx / sa, sy / sa];
}

export function convexHull(points) {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return closeRing(pts);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop(); lower.pop();
  return closeRing([...lower, ...upper]);
}
