import { forward, inverse } from "../engine/proj.js";

const MAX_LINES = 6000;

// Returns every cell border cutting across the box; an edge sits at (k + 0.5)
// * gridM, which is where tileCell puts a cell edge.
export function gridLinesInBox([minx, miny, maxx, maxy], gridM) {
  const lines = [];
  const half = gridM / 2;
  for (let k = Math.ceil((minx - half) / gridM); (k + 0.5) * gridM <= maxx; k++) {
    const x = (k + 0.5) * gridM;
    if (x < minx) continue;
    lines.push({ axis: "x", k, x0: x, y0: miny, x1: x, y1: maxy });
  }
  for (let k = Math.ceil((miny - half) / gridM); (k + 0.5) * gridM <= maxy; k++) {
    const y = (k + 0.5) * gridM;
    if (y < miny) continue;
    lines.push({ axis: "y", k, x0: minx, y0: y, x1: maxx, y1: y });
  }
  return lines;
}

function fc(features) { return { type: "FeatureCollection", features }; }

// Follows the CRS and spacing of the result currently on hand, which
// lag behind the parameter controls while a compute runs.
export function gridFeatures(state, [w, s, e, n]) {
  const res = state.results.intersection;
  const epsg = res?.epsg ?? state.params.epsg, gridM = res?.gridM ?? state.params.gridM;
  const corners = [[w, s], [e, s], [e, n], [w, n]].map(([lon, lat]) => forward(epsg, lon, lat));
  const box = [Math.min(...corners.map((c) => c[0])), Math.min(...corners.map((c) => c[1])), Math.max(...corners.map((c) => c[0])), Math.max(...corners.map((c) => c[1]))];
  const raw = gridLinesInBox(box, gridM);
  const tooMany = raw.length > MAX_LINES;
  const lines = tooMany ? [] : raw.map((l) => ({
    type: "Feature", properties: { axis: l.axis, k: l.k },
    geometry: { type: "LineString", coordinates: [inverse(epsg, l.x0, l.y0), inverse(epsg, l.x1, l.y1)] },
  }));
  const cells = [], links = [], centroids = [];
  const tiles = res?.tiles ?? [];
  const half = gridM / 2;
  for (const t of tiles) {
    if (t.seedX < box[0] - gridM || t.seedX > box[2] + gridM || t.seedY < box[1] - gridM || t.seedY > box[3] + gridM) continue;
    const ring = [[t.seedX - half, t.seedY - half], [t.seedX + half, t.seedY - half], [t.seedX + half, t.seedY + half], [t.seedX - half, t.seedY + half], [t.seedX - half, t.seedY - half]]
      .map(([x, y]) => inverse(epsg, x, y));
    cells.push({ type: "Feature", properties: { nativeId: t.nativeId }, geometry: { type: "Polygon", coordinates: [ring] } });
    links.push({ type: "Feature", properties: { nativeId: t.nativeId }, geometry: { type: "LineString", coordinates: [inverse(epsg, t.rawX, t.rawY), inverse(epsg, t.seedX, t.seedY)] } });
    centroids.push({ type: "Feature", properties: { nativeId: t.nativeId }, geometry: { type: "Point", coordinates: inverse(epsg, t.rawX, t.rawY) } });
  }
  return { lines: fc(lines), cells: fc(cells), links: fc(links), centroids: fc(centroids), tooMany };
}
