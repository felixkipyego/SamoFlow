import { test } from "node:test";
import assert from "node:assert/strict";
import { validateWidgetConfig } from "../src/config-validation.js";

test("passes through valid values unchanged", () => {
  const config = validateWidgetConfig({
    title: "Acme Support",
    greeting: "Hi there!",
    colors: { primary: "#4f46e5" },
    logo_url: "https://cdn.example.com/logo.png",
    privacy_notice_url: "https://example.com/privacy",
  });
  assert.equal(config.title, "Acme Support");
  assert.equal(config.greeting, "Hi there!");
  assert.equal(config.colors.primary, "#4f46e5");
  assert.equal(config.logoUrl, "https://cdn.example.com/logo.png");
  assert.equal(config.privacyNoticeUrl, "https://example.com/privacy");
});

test("falls back to safe defaults for a completely empty config", () => {
  const config = validateWidgetConfig({});
  assert.equal(typeof config.title, "string");
  assert.equal(typeof config.greeting, "string");
  assert.match(config.colors.primary, /^#/);
  assert.equal(config.logoUrl, null);
  assert.equal(config.privacyNoticeUrl, null);
});

test("rejects a javascript: logo_url, falling back to null (never passed through)", () => {
  const config = validateWidgetConfig({ logo_url: "javascript:alert(1)" });
  assert.equal(config.logoUrl, null);
});

test("rejects a data: privacy_notice_url, falling back to null", () => {
  const config = validateWidgetConfig({ privacy_notice_url: "data:text/html,<script>alert(1)</script>" });
  assert.equal(config.privacyNoticeUrl, null);
});

test("rejects a protocol-relative logo_url", () => {
  const config = validateWidgetConfig({ logo_url: "//evil.example.com/x.png" });
  assert.equal(config.logoUrl, null);
});

test("rejects CSS injection via colors.primary, falling back to the default", () => {
  const config = validateWidgetConfig({ colors: { primary: "red; background: url(evil)" } });
  assert.equal(config.colors.primary, "#4f46e5");
});

test("rejects a non-object colors value", () => {
  const config = validateWidgetConfig({ colors: "not-an-object" });
  assert.equal(config.colors.primary, "#4f46e5");
});

test("title/greeting: a non-string value falls back rather than being rendered as-is (bad input: wrong type)", () => {
  const config = validateWidgetConfig({ title: 12345, greeting: null });
  assert.equal(typeof config.title, "string");
  assert.equal(typeof config.greeting, "string");
});

test("title/greeting: an oversized value is truncated, never thrown", () => {
  const config = validateWidgetConfig({ title: "x".repeat(10_000) });
  assert.ok(config.title.length <= 200);
});

test("never throws on a null or undefined config (bad input: None)", () => {
  assert.doesNotThrow(() => validateWidgetConfig(null));
  assert.doesNotThrow(() => validateWidgetConfig(undefined));
});

// --- PART A: verified_domains (2026-10-16/17) -------------------------------

test("verified_domains: a real, well-formed list passes through, lowercased", () => {
  const config = validateWidgetConfig({ verified_domains: ["Example.com", "docs.example.com"] });
  assert.deepEqual(config.verifiedDomains, ["example.com", "docs.example.com"]);
});

test("verified_domains: missing entirely falls back to an empty array (bad input: empty)", () => {
  const config = validateWidgetConfig({});
  assert.deepEqual(config.verifiedDomains, []);
});

test("verified_domains: null/non-array value falls back to an empty array, never throws", () => {
  assert.deepEqual(validateWidgetConfig({ verified_domains: null }).verifiedDomains, []);
  assert.deepEqual(validateWidgetConfig({ verified_domains: "example.com" }).verifiedDomains, []);
});

test("verified_domains: a non-string entry is dropped, not stringified", () => {
  const config = validateWidgetConfig({ verified_domains: ["example.com", 123, null, {}] });
  assert.deepEqual(config.verifiedDomains, ["example.com"]);
});

test("verified_domains: an entry carrying a scheme, port, path, userinfo or wildcard is dropped outright, never silently repaired", () => {
  const config = validateWidgetConfig({
    verified_domains: [
      "https://example.com",
      "example.com:8443",
      "example.com/path",
      "a@example.com",
      "*.example.com",
    ],
  });
  assert.deepEqual(config.verifiedDomains, []);
});

test("verified_domains: a trailing dot is normalized away", () => {
  const config = validateWidgetConfig({ verified_domains: ["example.com."] });
  assert.deepEqual(config.verifiedDomains, ["example.com"]);
});

test("verified_domains: an IDN entry canonicalizes to its punycode form", () => {
  const config = validateWidgetConfig({ verified_domains: ["café.example"] });
  assert.deepEqual(config.verifiedDomains, ["xn--caf-dma.example"]);
});

test("verified_domains: more than 50 entries is capped, never unbounded", () => {
  const many = Array.from({ length: 200 }, (_, i) => `d${i}.example`);
  const config = validateWidgetConfig({ verified_domains: many });
  assert.ok(config.verifiedDomains.length <= 50);
});
