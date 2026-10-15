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
