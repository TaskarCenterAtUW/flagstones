import proj4 from "proj4";

// pyproj's default 4326-to-5070 pipeline counts the NAD83/WGS84 shift as
// zero, and proj4's +datum=NAD83 does the same, so browser figures and
// exported ones agree to the millimeter. Any other CRS rides in with its
// bundle under `city.proj4`.
const CRS_DEFS = {
  "EPSG:4326": "+proj=longlat +datum=WGS84 +no_defs",
  "EPSG:5070": "+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs",
};

const CRS_LABELS = {
  "EPSG:5070": "EPSG:5070, NAD83 Conus Albers (equal-area)",
  "EPSG:4326": "EPSG:4326, degrees (the legacy tiling's CRS)",
};

// A CRS reaches this code as a numeric EPSG value or an authority
// string ("ESRI:54034").
export function crsKey(crs) {
  return typeof crs === "number" ? `EPSG:${crs}` : crs;
}

export function registerCrs(crs, definition) {
  const key = crsKey(crs);
  if (!proj4.defs(key)) proj4.defs(key, definition);
  return key;
}

function ensureDef(crs) {
  const key = crsKey(crs);
  if (proj4.defs(key)) return key;
  if (CRS_DEFS[key]) {
    proj4.defs(key, CRS_DEFS[key]);
    return key;
  }
  const utm = /^EPSG:(326|327)(\d\d)$/.exec(key);
  const zone = utm ? Number(utm[2]) : 0;
  if (zone >= 1 && zone <= 60) {
    const south = utm[1] === "327" ? " +south" : "";
    proj4.defs(key, `+proj=utm +zone=${zone}${south} +datum=WGS84 +units=m +no_defs`);
    return key;
  }
  throw new Error(`proj4 has no definition for ${key}; re-export the bundle so it records city.proj4`);
}

// Converters keyed by CRS, each mapping lon/lat into its projection.
// Asking proj4 by CRS name reparses both definitions, and a Seattle
// recompute would pay that for every node it projects. Definitions freeze
// at registration, so a converter lives as long as its CRS.
const converters = new Map();

export function converter(crs) {
  const key = ensureDef(crs);
  let c = converters.get(key);
  if (!c) {
    c = proj4("EPSG:4326", key);
    converters.set(key, c);
  }
  return c;
}

export function forward(crs, lon, lat) {
  const key = crsKey(crs);
  if (key === "EPSG:4326") return [lon, lat];
  return converter(key).forward([lon, lat]);
}

export function inverse(crs, x, y) {
  const key = crsKey(crs);
  if (key === "EPSG:4326") return [x, y];
  return converter(key).inverse([x, y]);
}

// Carries a multipolygon between CRSs, lon/lat in the middle. Equal
// keys hand back the very object passed in.
export function reprojectMulti(multi, fromCrs, toCrs) {
  if (crsKey(fromCrs) === crsKey(toCrs)) return multi;
  return multi.map((polygon) => polygon.map((ring) => ring.map(([x, y]) => {
    const [lon, lat] = inverse(fromCrs, x, y);
    return forward(toCrs, lon, lat);
  })));
}

// The WGS84 UTM zone containing a point, 326zz north of the equator and
// 327zz south of it, as the library's utm_crs_for. Refuses a point outside
// the UTM range. The antimeridian at lon 180 stays in zone 60.
export function utmZoneEpsg(lon, lat) {
  if (!(lon >= -180 && lon <= 180 && lat >= -80 && lat <= 84)) {
    throw new Error(`(${lon}, ${lat}) falls beyond the UTM range; longitude must stay within -180 to 180, latitude within -80 to 84`);
  }
  const zone = Math.min(Math.floor((lon + 180) / 6) + 1, 60);
  return (lat >= 0 ? 32600 : 32700) + zone;
}

export function crsLabel(crs) {
  const key = crsKey(crs);
  if (CRS_LABELS[key]) return CRS_LABELS[key];
  const utm = /^EPSG:32([67])(\d\d)$/.exec(key);
  if (utm) return `${key}, UTM zone ${Number(utm[2])}${utm[1] === "6" ? "N" : "S"} (conformal, not equal-area)`;
  return key;
}
