import { test } from "node:test";
import assert from "node:assert/strict";
import { pickReadableTextColor, toHexColor } from "../src/contrast.js";

test("a dark background picks white text", () => {
  assert.equal(pickReadableTextColor("#000000"), "#ffffff");
  assert.equal(pickReadableTextColor("#1a1a1a"), "#ffffff");
});

test("a light background picks black text", () => {
  assert.equal(pickReadableTextColor("#ffffff"), "#000000");
  assert.equal(pickReadableTextColor("#f5f5f5"), "#000000");
});

test("a mid-tone brand color (the widget's own default primary) picks white", () => {
  assert.equal(pickReadableTextColor("#4f46e5"), "#ffffff");
});

test("3-digit and 4-digit hex shorthand both parse correctly", () => {
  assert.equal(pickReadableTextColor("#000"), "#ffffff");
  assert.equal(pickReadableTextColor("#fff"), "#000000");
  assert.equal(pickReadableTextColor("#000f"), "#ffffff");
});

test("rgb()/rgba() forms parse and pick correctly", () => {
  assert.equal(pickReadableTextColor("rgb(0, 0, 0)"), "#ffffff");
  assert.equal(pickReadableTextColor("rgba(255, 255, 255, 0.5)"), "#000000");
});

test("hsl()/hsla() forms parse and pick correctly", () => {
  assert.equal(pickReadableTextColor("hsl(0, 0%, 0%)"), "#ffffff"); // black
  assert.equal(pickReadableTextColor("hsl(0, 0%, 100%)"), "#000000"); // white
});

test("the chosen color always meets at least the WCAG AA 4.5:1 ratio against a representative sample of colors", () => {
  const samples = ["#4f46e5", "#e53e3e", "#38a169", "#1a202c", "#f7fafc", "#805ad5", "#d69e2e", "#2b6cb0"];
  for (const color of samples) {
    const text = pickReadableTextColor(color);
    // Re-derive the ratio independently (not just trusting the function
    // picked SOMETHING) using the same formula, inlined here so this
    // test does not just re-exercise the implementation's own internals.
    const hex = color.slice(1);
    const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
    const linearize = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const luminance = 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b);
    const textLuminance = text === "#ffffff" ? 1 : 0;
    const ratio = (Math.max(luminance, textLuminance) + 0.05) / (Math.min(luminance, textLuminance) + 0.05);
    assert.ok(ratio >= 4.5, `${color} -> ${text} only reaches ${ratio.toFixed(2)}:1`);
  }
});

test("an unparseable color never throws, falls back to a safe default", () => {
  assert.doesNotThrow(() => pickReadableTextColor("not-a-color"));
});

// [ops bugfix]: the launcher's own readability (BUG1 point 4) -- the
// SAME function the header/bubbles/Send already use, not a second copy,
// exercised with a very light and a very dark tenant color.
test("a very light tenant color (#ffff99) picks black text, readable on the launcher", () => {
  assert.equal(pickReadableTextColor("#ffff99"), "#000000");
});

test("a very dark tenant color (#111133) picks white text, readable on the launcher", () => {
  assert.equal(pickReadableTextColor("#111133"), "#ffffff");
});

// [ops bugfix]: toHexColor() -- the canonicalisation color-cache.js
// relies on, reusing this file's own RGB parsing (never a second parser).
test("toHexColor canonicalises hex/rgb/hsl inputs to a lowercase #rrggbb string", () => {
  assert.equal(toHexColor("#14b8a6"), "#14b8a6");
  assert.equal(toHexColor("#4F46E5"), "#4f46e5");
  assert.equal(toHexColor("#fff"), "#ffffff");
  assert.equal(toHexColor("rgb(20, 184, 166)"), "#14b8a6");
  assert.equal(toHexColor("hsl(0, 0%, 0%)"), "#000000");
});

test("toHexColor returns null, never throws, for an unparseable color", () => {
  assert.equal(toHexColor("not-a-color"), null);
  assert.doesNotThrow(() => toHexColor("javascript:alert(1)"));
});
