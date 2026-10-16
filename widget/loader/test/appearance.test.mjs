// [SECURITY] Step 7-follow-up, Part C: adversarial tests for
// appearance.js's own strict validation -- the real trust boundary for
// this feature (network-delivered data, read back only as a CSSOM
// `setProperty()` value). Mirrors the retired color-cache.test.mjs's
// own discipline exactly.
import { test } from "node:test";
import assert from "node:assert/strict";
import { fetchAppearance } from "../src/appearance.js";

function fakeFetch(body, { ok = true, status = 200 } = {}) {
  return async () => ({
    ok,
    status,
    json: async () => body,
  });
}

function throwingFetch() {
  return async () => {
    throw new TypeError("network error");
  };
}

test("accepts a well-formed response", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b8a6", primary_text: "#ffffff", position: "left" }),
  });
  assert.deepEqual(result, { primary: "#14b8a6", primaryText: "#ffffff", position: "left" });
});

test("accepts the other valid text literal and position", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#ffff99", primary_text: "#000000", position: "right" }),
  });
  assert.deepEqual(result, { primary: "#ffff99", primaryText: "#000000", position: "right" });
});

test("returns null on a network failure", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: throwingFetch(),
  });
  assert.equal(result, null);
});

test("returns null on a non-2xx status", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ detail: "not found" }, { ok: false, status: 404 }),
  });
  assert.equal(result, null);
});

test("returns null when .json() itself throws (malformed body)", async () => {
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => {
      throw new SyntaxError("bad json");
    },
  });
  assert.equal(await fetchAppearance("https://api.example.com", "sk_live_x", { fetchImpl }), null);
});

test("rejects a non-object response", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch("#14b8a6"),
  });
  assert.equal(result, null);
});

test("rejects rgb() -- only a strict #rrggbb hex is ever accepted for primary", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "rgb(20, 184, 166)", primary_text: "#ffffff", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects url(...) smuggled into primary", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({
      primary: "url(https://evil.example/x.png)",
      primary_text: "#ffffff",
      position: "right",
    }),
  });
  assert.equal(result, null);
});

test("rejects var(...) smuggled into primary", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "var(--evil)", primary_text: "#ffffff", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects an overlong primary string", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({
      primary: `#${"a".repeat(5000)}`,
      primary_text: "#ffffff",
      position: "right",
    }),
  });
  assert.equal(result, null);
});

test("rejects uppercase hex digits (strict lowercase only)", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14B8A6", primary_text: "#ffffff", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects a 3-digit shorthand hex", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b", primary_text: "#ffffff", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects an arbitrary primary_text value", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b8a6", primary_text: "#123456", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects javascript: smuggled into primary_text", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b8a6", primary_text: "javascript:alert(1)", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects an arbitrary position value", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b8a6", primary_text: "#ffffff", position: "center" }),
  });
  assert.equal(result, null);
});

test("rejects a response missing the primary field", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary_text: "#ffffff", position: "right" }),
  });
  assert.equal(result, null);
});

test("rejects a response missing the position field", async () => {
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: fakeFetch({ primary: "#14b8a6", primary_text: "#ffffff" }),
  });
  assert.equal(result, null);
});

test("times out and returns null rather than hanging forever", async () => {
  // A real fetch() rejects when its own AbortSignal fires -- this fake
  // mirrors exactly that contract (never settling on its own, but
  // listening for the signal), so the test proves fetchAppearance()'s
  // own timeout wiring actually aborts the real request, not just that
  // SOME unrelated promise eventually resolves.
  const hangingFetch = (url, { signal } = {}) =>
    new Promise((_, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  const result = await fetchAppearance("https://api.example.com", "sk_live_x", {
    fetchImpl: hangingFetch,
    timeoutMs: 20,
  });
  assert.equal(result, null);
});
