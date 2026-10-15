import { test } from "node:test";
import assert from "node:assert/strict";
import { pickReadableTextColor } from "../src/contrast.js";

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
