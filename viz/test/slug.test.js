import { test } from "node:test";
import assert from "node:assert/strict";
import { isValidSlug, bundlePath } from "../src/app/slugs.js";

test("plain bundle names are valid", () => {
  for (const slug of ["seattle", "toppenish.wa", "pullman-2", "Спокан-ünïcödé-✓"]) {
    assert.ok(isValidSlug(slug), slug);
  }
});

test("path traversal and empty or absent slugs are invalid", () => {
  for (const slug of ["", ".", "..", "../secret", "..\\..\\secret", "a/b", "a\\b", "a\0b", "/etc/passwd"]) {
    assert.ok(!isValidSlug(slug), JSON.stringify(slug));
  }
  for (const slug of [null, undefined, 42, {}]) assert.ok(!isValidSlug(slug), String(slug));
});

test("index is invalid in any case, matching the Python slug guard", () => {
  // /data/index.json is the place list, so a bundle named index would be
  // fetched as the list and written over it.
  for (const slug of ["index", "Index", "INDEX"]) {
    assert.ok(!isValidSlug(slug), slug);
  }
  assert.ok(isValidSlug("index-wa"));
});

test("colons are invalid, matching the Python slug guard", () => {
  // A drive-relative or alternate-data-stream name is refused on the
  // producing side; the JS guard rejects the same input set.
  for (const slug of ["a:b", "C:x", "data:nugget", "vol:stream.json", ":"]) {
    assert.ok(!isValidSlug(slug), JSON.stringify(slug));
  }
});

test("control characters are invalid, matching the Python slug guard", () => {
  // The browser URL parser silently strips tab, LF and CR from request
  // paths, so a slug carrying one resolves to a different file than the
  // exporter wrote; Windows refuses such filenames outright. Any code
  // point below 0x20 is refused.
  for (const slug of ["a\nb", "a\tb", "a\x00b", "\n", "\x1f", "a\x01b"]) {
    assert.ok(!isValidSlug(slug), JSON.stringify(slug));
  }
});

test("characters Windows bans from file names are invalid, matching the Python slug guard", () => {
  // The exporter refuses these on every platform, so a bundle directory
  // can be copied to or served from Windows. No exported file carries one.
  for (const slug of ['say-"hi"', "what?", "a<b", "a>b", "a|b", "a*b"]) {
    assert.ok(!isValidSlug(slug), JSON.stringify(slug));
  }
});

test("Windows device names are invalid in any case and with any extension", () => {
  // Windows opens nul.json and nul.v2.json as the null device, so the stem
  // before the first dot decides.
  for (const slug of ["con", "CON", "prn", "Aux", "nul", "com1", "LPT9", "com0", "com¹", "conin$", "CONOUT$", "nul.v2"]) {
    assert.ok(!isValidSlug(slug), JSON.stringify(slug));
  }
  for (const slug of ["concord", "aux-cayes", "nulato", "com10", "st.-con"]) {
    assert.ok(isValidSlug(slug), slug);
  }
});

test("bundlePath builds /data/<slug>.json for plain and unicode slugs", () => {
  assert.equal(bundlePath("seattle"), "/data/seattle.json");
  assert.equal(bundlePath("toppenish.wa"), "/data/toppenish.wa.json");
  assert.equal(bundlePath("nütnë-ville"), "/data/nütnë-ville.json");
  assert.equal(bundlePath("Спокан-ünïcödé-✓"), "/data/Спокан-ünïcödé-✓.json");
});

test("bundlePath encodes the characters that reinterpret the URL", () => {
  // A fragment or percent escape in a slug must reach the server as
  // literal name bytes. URL syntax there would point at some other file.
  // Percent is encoded first so an escape already in the name is not
  // double-processed. The guard refuses '?', so no query can arrive here.
  assert.equal(bundlePath("a#b"), "/data/a%23b.json");
  assert.equal(bundlePath("%"), "/data/%25.json");
  assert.equal(bundlePath("a%23b"), "/data/a%2523b.json");
  assert.equal(bundlePath("x#z%w"), "/data/x%23z%25w.json");
});
