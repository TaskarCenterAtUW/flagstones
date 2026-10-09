// Python's round() rounds half to even on the exact binary value, so this
// must too, or a centroid on a half-grid line mints a different ID here than
// in the library.
export function roundHalfEven(v) {
  const f = Math.floor(v);
  const d = v - f;
  let r;
  if (d < 0.5) r = f;
  else if (d > 0.5) r = f + 1;
  else r = f % 2 === 0 ? f : f + 1;
  return r === 0 && v < 0 ? -0 : r;
}

export function tileCell(x, y, gridM) {
  return [roundHalfEven(x / gridM), roundHalfEven(y / gridM)];
}

export function snapSeed(x, y, gridM) {
  const [cx, cy] = tileCell(x, y, gridM);
  return [cx * gridM, cy * gridM];
}

// Follows crs_id_token in the library: EPSG gives up its bare code, and
// any other authority arrives spelled `{authority}{code}` in lowercase.
export function crsToken(crs) {
  const text = String(typeof crs === "number" ? `EPSG:${crs}` : crs);
  const at = text.indexOf(":");
  if (at < 0) return text;
  const authority = text.slice(0, at), code = text.slice(at + 1);
  return authority.toUpperCase() === "EPSG" ? code : `${authority.toLowerCase()}${code}`;
}

export function mintNativeId(cx, cy, crs) {
  return `${crsToken(crs)}:${cx}:${cy}`;
}

export function collisionBound(toleranceM) {
  return toleranceM * Math.SQRT2;
}

export function validateParams({ toleranceM, gridM }) {
  if (!(toleranceM > 0) || !(gridM > 0)) throw new RangeError("tolerance and grid must be positive");
}

export function gridOverBoundWarning(toleranceM, gridM) {
  const bound = collisionBound(toleranceM);
  if (gridM < bound) return null;
  return `Seed grid ${gridM} m reaches the collision bound for tolerance ${toleranceM} m ` +
    `(${bound.toFixed(2)} m). Clusters over ${2 * toleranceM} m apart may snap into the same cell ` +
    `and share a tile id. The map colors shared cells red.`;
}
