import { test } from "node:test";
import assert from "node:assert/strict";
import proj4 from "proj4";
import { converter, forward, inverse, registerCrs, utmZoneEpsg, crsLabel } from "../src/engine/proj.js";

// pyproj, EPSG:4326 -> EPSG:5070, always_xy=True. The pipeline pyproj picks
// is "Inverse of NAD83 to WGS 84 (1) + Conus Albers", a null datum shift.
const CASES = [
  { name: "seattle", lon: -122.3321, lat: 47.6062, x: -1967991.1690072708, y: 3009111.1392167644 },
  { name: "pullman", lon: -117.1817, lat: 46.7298, x: -1611685.1644850008, y: 2818137.028223794 },
];

for (const c of CASES) {
  test(`forward to 5070 matches pyproj within 1 mm (${c.name})`, () => {
    const [x, y] = forward(5070, c.lon, c.lat);
    assert.ok(Math.abs(x - c.x) < 0.001, `${x} vs ${c.x}`);
    assert.ok(Math.abs(y - c.y) < 0.001, `${y} vs ${c.y}`);
  });
  test(`inverse round-trips (${c.name})`, () => {
    const [lon, lat] = inverse(5070, c.x, c.y);
    assert.ok(Math.abs(lon - c.lon) < 1e-8);
    assert.ok(Math.abs(lat - c.lat) < 1e-8);
  });
}

test("4326 is the identity", () => {
  assert.deepEqual(forward(4326, -122.3, 47.6), [-122.3, 47.6]);
});

// The library's utm_crs_for cases, same points and zones.
const UTM_CASES = [
  { name: "seattle", lon: -122.3382, lat: 47.6097, epsg: 32610 },
  { name: "pullman", lon: -117.18, lat: 46.73, epsg: 32611 },
  { name: "quito", lon: -78.5, lat: -0.22, epsg: 32717 },
  { name: "nairobi", lon: 36.82, lat: -1.29, epsg: 32737 },
  { name: "berlin", lon: 13.4, lat: 52.52, epsg: 32633 },
  { name: "antimeridian", lon: 180.0, lat: 10.0, epsg: 32660 },
];

for (const c of UTM_CASES) {
  test(`UTM zone and hemisphere (${c.name})`, () => {
    assert.equal(utmZoneEpsg(c.lon, c.lat), c.epsg);
  });
}

test("UTM refuses points outside the UTM range", () => {
  for (const [lon, lat] of [[-181.0, 0.0], [0.0, 85.0], [0.0, -81.0]]) {
    assert.throws(() => utmZoneEpsg(lon, lat), { message: /UTM range/ }, `(${lon}, ${lat})`);
  }
});

test("a bundle's proj4 definition registers a CRS the table does not know", () => {
  registerCrs("ESRI:54034", "+proj=cea +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs");
  const [x, y] = forward("ESRI:54034", 10, 20);
  const [lon, lat] = inverse("ESRI:54034", x, y);
  assert.ok(Math.abs(lon - 10) < 1e-9 && Math.abs(lat - 20) < 1e-9);
});

test("a CRS gets one converter, which projects as proj4 does by name", () => {
  assert.equal(converter(5070), converter("EPSG:5070"));
  const direct = proj4("EPSG:4326", "EPSG:5070", [-117.18, 46.73]);
  assert.deepEqual(forward(5070, -117.18, 46.73), direct);
  assert.deepEqual(inverse(5070, ...direct), proj4("EPSG:5070", "EPSG:4326", direct));
});

test("crsLabel names southern UTM zones too", () => {
  assert.equal(crsLabel("EPSG:32737"), "EPSG:32737, UTM zone 37S (conformal, not equal-area)");
});
