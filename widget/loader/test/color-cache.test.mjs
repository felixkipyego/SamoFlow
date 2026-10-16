// [ops bugfix]: the loader's own cached-color reader is the real trust
// boundary for this feature (see loader/src/color-cache.js's own
// header) -- a value sitting in localStorage could have been edited
// directly (devtools, or a same-origin XSS elsewhere), so this test
// drives the REAL `window.localStorage` code path (a tiny Map-backed
// fake, assigned to `globalThis.window` for this file only) rather than
// the private in-memory fallback, so a malicious/malformed RAW string
// is exercised exactly the way a tampered real browser value would be.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { getCachedColor } from "../src/color-cache.js";

function fakeLocalStorage() {
  const store = new Map();
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
  };
}

let storage;
const _originalWindow = globalThis.window;
beforeEach(() => {
  storage = fakeLocalStorage();
  globalThis.window = { localStorage: storage };
});
afterEach(() => {
  globalThis.window = _originalWindow;
});

function seed(siteKey, raw) {
  storage.setItem(`sf-color:${siteKey}`, raw);
}

test("returns null when nothing is cached", () => {
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("accepts a well-formed cached pair written by the real writer's own shape", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b8a6", text: "#ffffff" }));
  assert.deepEqual(getCachedColor("sk_live_abc"), { hex: "#14b8a6", text: "#ffffff" });
});

test("accepts the other valid text literal, #000000", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#ffff99", text: "#000000" }));
  assert.deepEqual(getCachedColor("sk_live_abc"), { hex: "#ffff99", text: "#000000" });
});

test("rejects malformed JSON outright", () => {
  seed("sk_live_abc", "{not json");
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a non-object JSON value (a bare string)", () => {
  seed("sk_live_abc", JSON.stringify("#14b8a6"));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a JSON null", () => {
  seed("sk_live_abc", "null");
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a missing hex field", () => {
  seed("sk_live_abc", JSON.stringify({ text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a missing text field", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b8a6" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects rgb() -- only a strict #rrggbb hex is ever accepted", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "rgb(20, 184, 166)", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects url(...) smuggled into the hex field", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "url(https://evil.example/x.png)", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects expression(...) (legacy IE CSS injection shape)", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "expression(alert(1))", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects var(...) smuggled into the hex field", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "var(--evil)", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a value with a trailing CSS-breakout payload appended to an otherwise-valid hex", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b8a6; } * { background: url(evil)", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects an overlong hex string", () => {
  seed("sk_live_abc", JSON.stringify({ hex: `#${"a".repeat(5000)}`, text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects uppercase hex digits (strict lowercase only, matching the writer's own canonical output)", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14B8A6", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects a 3-digit shorthand hex (the writer never produces one; only #rrggbb is accepted)", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects an arbitrary text value that is not exactly #ffffff or #000000", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b8a6", text: "#123456" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("rejects javascript: smuggled into the text field", () => {
  seed("sk_live_abc", JSON.stringify({ hex: "#14b8a6", text: "javascript:alert(1)" }));
  assert.equal(getCachedColor("sk_live_abc"), null);
});

test("one site key's tampered cache entry never leaks into a lookup for a different site key", () => {
  seed("sk_live_evil", JSON.stringify({ hex: "rgb(1,2,3)", text: "#ffffff" }));
  assert.equal(getCachedColor("sk_live_other"), null);
});
