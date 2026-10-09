function quantile(sorted, q) {
  if (!sorted.length) return 0;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos), hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

// Tiles below this square-meter area count as slivers.
export const SLIVER_M2 = 100;

export function median(values) {
  const s = [...values].sort((a, b) => a - b);
  return quantile(s, 0.5);
}

export function layerStats(tiles) {
  const count = tiles.length;
  const areas = tiles.map((t) => t.area).sort((a, b) => a - b);
  const merged = tiles.filter((t) => t.nodeCount > 1).length;
  const deg = (pred) => tiles.filter((t) => pred(t.degree)).length / (count || 1);
  const snaps = tiles.map((t) => Math.hypot(t.seedX - t.rawX, t.seedY - t.rawY)).sort((a, b) => a - b);
  return {
    count,
    mergedCount: merged,
    mergedShare: count ? merged / count : 0,
    degreeShare: { d3: deg((d) => d === 3), d4: deg((d) => d === 4), d5plus: deg((d) => d >= 5) },
    medianArea: quantile(areas, 0.5),
    q25Area: quantile(areas, 0.25),
    q75Area: quantile(areas, 0.75),
    sliverCount: areas.filter((a) => a < SLIVER_M2).length,
    snapMedian: quantile(snaps, 0.5),
    snapMax: snaps.length ? snaps[snaps.length - 1] : 0,
    truncatedCount: tiles.filter((t) => t.truncated).length,
    collisionCount: tiles.filter((t) => t.seedCollision).length,
  };
}
