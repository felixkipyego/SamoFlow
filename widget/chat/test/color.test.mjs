import { test } from "node:test";
import assert from "node:assert/strict";
import { isValidCssColor } from "../src/color.js";

test("accepts 3/4/6/8-digit hex colors", () => {
  assert.equal(isValidCssColor("#fff"), true);
  assert.equal(isValidCssColor("#fff0"), true);
  assert.equal(isValidCssColor("#4f46e5"), true);
  assert.equal(isValidCssColor("#4f46e5ff"), true);
});

test("accepts rgb()/rgba()/hsl()/hsla() with numeric arguments", () => {
  assert.equal(isValidCssColor("rgb(79, 70, 229)"), true);
  assert.equal(isValidCssColor("rgba(79, 70, 229, 0.5)"), true);
  assert.equal(isValidCssColor("hsl(243, 75%, 59%)"), true);
  assert.equal(isValidCssColor("hsla(243, 75%, 59%, 0.5)"), true);
});

test("rejects CSS injection via a trailing declaration", () => {
  assert.equal(isValidCssColor("red; background: url(https://evil.example)"), false);
  assert.equal(isValidCssColor("#fff; } .evil { color"), false);
});

test("rejects a javascript: value smuggled as a color", () => {
  assert.equal(isValidCssColor("javascript:alert(1)"), false);
});

test("rejects url(), var() and calc()", () => {
  assert.equal(isValidCssColor("url(https://evil.example/x.png)"), false);
  assert.equal(isValidCssColor("var(--evil)"), false);
  assert.equal(isValidCssColor("calc(1px + 1px)"), false);
});

test("rejects named colors (deliberately out of scope, not a large table)", () => {
  assert.equal(isValidCssColor("red"), false);
  assert.equal(isValidCssColor("rebeccapurple"), false);
});

test("rejects leading/trailing whitespace padding", () => {
  assert.equal(isValidCssColor(" #fff"), false);
  assert.equal(isValidCssColor("#fff "), false);
});

test("rejects empty string and non-string values (bad input)", () => {
  assert.equal(isValidCssColor(""), false);
  assert.equal(isValidCssColor(null), false);
  assert.equal(isValidCssColor(undefined), false);
  assert.equal(isValidCssColor(42), false);
});
