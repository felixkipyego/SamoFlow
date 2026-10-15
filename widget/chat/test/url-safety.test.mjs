import { test } from "node:test";
import assert from "node:assert/strict";
import { isHttpsUrl } from "../src/url-safety.js";

test("a plain https URL is allowed", () => {
  assert.equal(isHttpsUrl("https://example.com/page"), true);
});

test("http (not https) is rejected", () => {
  assert.equal(isHttpsUrl("http://example.com"), false);
});

test("javascript: is rejected", () => {
  assert.equal(isHttpsUrl("javascript:alert(1)"), false);
});

test("mixed-case JavaScript: is rejected (case-insensitive scheme check)", () => {
  assert.equal(isHttpsUrl("JaVaScRiPt:alert(1)"), false);
});

test("a whitespace-padded scheme (tab-split javascript:) is rejected", () => {
  // The real browser-navigation trick this must survive: the WHATWG URL
  // parser itself strips embedded tab/newline/CR while parsing, so a
  // naive `startswith("javascript:")` string check would miss this --
  // using the real URL parser's own .protocol closes it structurally.
  assert.equal(isHttpsUrl("jav\tascript:alert(1)"), false);
  assert.equal(isHttpsUrl("java\nscript:alert(1)"), false);
  assert.equal(isHttpsUrl("java\rscript:alert(1)"), false);
});

test("data: is rejected", () => {
  assert.equal(isHttpsUrl("data:text/html,<script>alert(1)</script>"), false);
});

test("vbscript: is rejected", () => {
  assert.equal(isHttpsUrl("vbscript:msgbox(1)"), false);
});

test("a protocol-relative URL is rejected (no scheme, no base ever supplied)", () => {
  assert.equal(isHttpsUrl("//evil.example.com/path"), false);
});

test("a bare relative path is rejected", () => {
  assert.equal(isHttpsUrl("/some/path"), false);
});

test("an empty string is rejected", () => {
  assert.equal(isHttpsUrl(""), false);
});

test("a non-string value is rejected (bad input: wrong type)", () => {
  assert.equal(isHttpsUrl(null), false);
  assert.equal(isHttpsUrl(undefined), false);
  assert.equal(isHttpsUrl(42), false);
  assert.equal(isHttpsUrl({ toString: () => "https://example.com" }), false);
});

test("a punycode/homograph-shaped domain is still accepted as https (no domain heuristics here -- gating is the caller's exact-match allowlist, not this function)", () => {
  assert.equal(isHttpsUrl("https://xn--80ak6aa92e.com/"), true);
});
