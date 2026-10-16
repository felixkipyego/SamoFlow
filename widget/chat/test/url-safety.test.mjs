import { test } from "node:test";
import assert from "node:assert/strict";
import { canonicalizeVerifiedDomain, isHttpsUrl, urlHostname } from "../src/url-safety.js";

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

// --- PART A: urlHostname()/canonicalizeVerifiedDomain() (2026-10-16/17) ----

test("urlHostname: returns the real hostname, lowercased by the URL parser", () => {
  assert.equal(urlHostname("https://Example.com/x"), "example.com");
});

test("urlHostname: a userinfo-prefixed URL resolves to the REAL host, never the userinfo", () => {
  assert.equal(urlHostname("https://verified.example@evil.com/x"), "evil.com");
  assert.equal(urlHostname("https://verified.example%2f@evil.com/x"), "evil.com");
});

test("urlHostname: the host is never confused with a path segment that merely looks like one", () => {
  assert.equal(urlHostname("https://evil.com/verified.example"), "evil.com");
});

test("urlHostname: a subdomain is returned in full, never truncated to its parent", () => {
  assert.equal(urlHostname("https://evil.verified.example/x"), "evil.verified.example");
});

test("urlHostname: a trailing dot is stripped", () => {
  assert.equal(urlHostname("https://verified.example./x"), "verified.example");
});

test("urlHostname: an explicit port never becomes part of the hostname", () => {
  assert.equal(urlHostname("https://verified.example:8443/x"), "verified.example");
});

test("urlHostname: a backslash-separated authority still resolves honestly (WHATWG treats \\\\ like / for http(s))", () => {
  assert.equal(urlHostname("https:\\\\verified.example\\x"), "verified.example");
});

test("urlHostname: an IDN host canonicalizes to its punycode form", () => {
  assert.equal(urlHostname("https://café.example/x"), "xn--caf-dma.example");
});

test("urlHostname: null for anything that doesn't parse as a URL at all", () => {
  assert.equal(urlHostname("not a url"), null);
  assert.equal(urlHostname(""), null);
});

test("canonicalizeVerifiedDomain: a plain hostname is accepted and lowercased", () => {
  assert.equal(canonicalizeVerifiedDomain("Example.com"), "example.com");
});

test("canonicalizeVerifiedDomain: a trailing dot is stripped", () => {
  assert.equal(canonicalizeVerifiedDomain("example.com."), "example.com");
});

test("canonicalizeVerifiedDomain: an IDN entry canonicalizes to its punycode form", () => {
  assert.equal(canonicalizeVerifiedDomain("café.example"), "xn--caf-dma.example");
});

test("canonicalizeVerifiedDomain: a mixed-case IDN entry canonicalizes identically to its lowercase form", () => {
  assert.equal(canonicalizeVerifiedDomain("CAFÉ.example"), canonicalizeVerifiedDomain("café.example"));
});

test("canonicalizeVerifiedDomain: rejects anything shaped unlike the server's own normalize_domain() output -- a scheme, port, path, userinfo, wildcard or backslash is dropped outright, never silently repaired", () => {
  assert.equal(canonicalizeVerifiedDomain("https://example.com"), null);
  assert.equal(canonicalizeVerifiedDomain("example.com:8443"), null);
  assert.equal(canonicalizeVerifiedDomain("example.com/path"), null);
  assert.equal(canonicalizeVerifiedDomain("a@example.com"), null);
  assert.equal(canonicalizeVerifiedDomain("*.example.com"), null);
  assert.equal(canonicalizeVerifiedDomain("example.com\\x"), null);
  assert.equal(canonicalizeVerifiedDomain("exa mple.com"), null);
});

test("canonicalizeVerifiedDomain: an empty string, overlong string, or non-string is rejected (bad input: empty/oversized/wrong type)", () => {
  assert.equal(canonicalizeVerifiedDomain(""), null);
  assert.equal(canonicalizeVerifiedDomain("a".repeat(300)), null);
  assert.equal(canonicalizeVerifiedDomain(null), null);
  assert.equal(canonicalizeVerifiedDomain(undefined), null);
  assert.equal(canonicalizeVerifiedDomain(123), null);
});
