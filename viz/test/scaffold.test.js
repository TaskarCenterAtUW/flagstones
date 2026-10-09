import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("the four design assets are present", () => {
  for (const name of [
    "app.css",
    "app-map.css",
    "app-bootstrap.css",
    "app-basemap.json",
  ]) {
    assert.ok(existsSync(join(root, "public/design", name)), `${name} missing`);
  }
});

test("the basemap style is a version 8 style on OpenFreeMap tiles", () => {
  const style = JSON.parse(readFileSync(join(root, "public/design/app-basemap.json"), "utf8"));
  assert.equal(style.version, 8);
  assert.match(JSON.stringify(style.sources), /openfreemap/);
});

test("index.html loads the design CSS in the documented order", () => {
  const html = readFileSync(join(root, "index.html"), "utf8");
  const order = ["app-bootstrap.css", "app.css", "app-map.css", "viz.css"]
    .map((name) => html.indexOf(name));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.ok(order.every((i) => i >= 0));
});
